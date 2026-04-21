"""Application service that orchestrates indexing, search, and artifact retrieval."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from functools import lru_cache
from typing import Any

from fastapi import HTTPException

from .config import Settings, get_settings
from .extractors import build_manifest, figure_index_payload, index_paper_artifacts, table_csv_bytes, table_index_payload, table_json_bytes
from .models import (
    FileContentResponse,
    FileListResponse,
    FileObject,
    FigureResponse,
    GrepResponse,
    IntegrationProbeComponent,
    IntegrationProbeResponse,
    JobResponse,
    ManifestResponse,
    PaperMetadata,
    PaperResponse,
    ResultSetResponse,
    SearchPaperResult,
    SearchRequest,
    SearchResponse,
    SearchSectionRequest,
    SearchSectionResponse,
    TableResponse,
)
from .repository import PaperRepository, build_repository
from .summaries import build_paper_summaries
from .storage import ObjectStore, StoredObject, build_object_store
from . import sources
from .utils import canonical_paper_id, new_id, utcnow


logger = logging.getLogger(__name__)


_MODEL_CACHE: dict[str, Any] = {}
_RERANKER_CACHE: dict[str, Any] = {}

_RECENCY_SIGNAL_RE = re.compile(
    r"\b(recent(?:ly)?|latest|newest|new|this\s+year|202[4-9]|203[0-9])\b",
    re.IGNORECASE,
)


def _query_signals_recency(query: str) -> bool:
    return bool(_RECENCY_SIGNAL_RE.search(query or ""))


_APPENDIX_TITLE_RE = re.compile(r"\b(appendix|supplement|acknowledg|references?|bibliography)\b", re.IGNORECASE)


def _section_depth_for(section: Any) -> int:
    """Infer section depth heuristically.

    Appendices / references / acknowledgements → 3 so they rank below main
    body. Sections introduced via an explicit depth attribute would override
    this when the extractor starts supplying one.
    """
    title = getattr(section, "title", "") or ""
    if _APPENDIX_TITLE_RE.search(title):
        return 3
    return 1


def _stride_chunks(text: str, *, max_chars: int, overlap: int) -> list[str]:
    """Split text into overlapping windows.

    Returns the text unchanged when it already fits, so short sections don't
    turn into two-copy chunks. Boundary tries to snap to paragraph breaks to
    avoid cutting mid-sentence when possible.
    """
    if not text:
        return [""]
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]
    if overlap < 0:
        overlap = 0
    if overlap >= max_chars:
        overlap = max_chars // 4
    step = max(1, max_chars - overlap)
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            # Prefer a paragraph break within the last 20% of the window.
            window_start = max(start + int(max_chars * 0.8), start)
            break_at = text.rfind("\n\n", window_start, end)
            if break_at == -1:
                break_at = text.rfind(". ", window_start, end)
                if break_at != -1:
                    break_at += 1  # include the period
            if break_at != -1 and break_at > start + overlap:
                end = break_at
        chunks.append(text[start:end].strip())
        if end >= n:
            break
        start = max(end - overlap, start + step)
    return [chunk for chunk in chunks if chunk]


def _table_to_indexable_text(table: Any) -> str:
    """Render a TableArtifact as Markdown-table text so its cell values are
    visible to lexical + vector retrieval. Caps rows so one giant table can't
    dominate the chunk pool.
    """
    caption = (getattr(table, "caption", None) or "").strip()
    rows = list(getattr(table, "rows", []) or [])
    parts: list[str] = []
    if caption:
        parts.append(f"Caption: {caption}")
    if rows:
        # Keep the header + first 40 rows; 40 × ~80 chars ≈ 3 KB.
        header, *body = rows
        parts.append("| " + " | ".join(str(c) for c in header) + " |")
        parts.append("|" + "|".join(["---"] * len(header)) + "|")
        for row in body[:40]:
            cells = [str(c) for c in row]
            if len(cells) < len(header):
                cells += [""] * (len(header) - len(cells))
            parts.append("| " + " | ".join(cells[: len(header)]) + " |")
        if len(body) > 40:
            parts.append(f"(+{len(body) - 40} more rows)")
    return "\n".join(parts).strip()


class _TransformersCLSEmbedder:
    """Wrap a raw HuggingFace AutoModel with CLS pooling + L2 normalization.

    Used for SPECTER2-family models that don't ship a sentence-transformers
    ``modules.json`` (they're published as adapter-format checkpoints). The
    SPECTER2 authors document the intended usage as:
        1. tokenize ``title + SEP + abstract`` up to 512 tokens,
        2. run through the base encoder,
        3. take the ``[CLS]`` token,
        4. L2-normalize.
    That is exactly what this class does.
    """

    def __init__(self, model_name: str) -> None:
        from transformers import AutoModel, AutoTokenizer
        import torch

        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(model_name)
        self._model = AutoModel.from_pretrained(model_name).eval()
        # Pick the best available device once, cache it on self; keeps encode
        # batches off the default CPU path when we have MPS or CUDA.
        if torch.cuda.is_available():
            self._device = "cuda"
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            self._device = "mps"
        else:
            self._device = "cpu"
        self._model.to(self._device)

    def encode(
        self,
        texts: list[str],
        *,
        normalize_embeddings: bool = True,
        convert_to_numpy: bool = True,
        show_progress_bar: bool = False,
    ):
        """Single-sequence tokenization path. Used for chunks (section text,
        figure captions, etc.) where there is no natural ``(title, body)``
        pair to feed."""
        return self._run([(text, None) for text in texts],
                         normalize_embeddings=normalize_embeddings,
                         convert_to_numpy=convert_to_numpy)

    def encode_pairs(
        self,
        pairs: list[tuple[str, str | None]],
        *,
        normalize_embeddings: bool = True,
        convert_to_numpy: bool = True,
    ):
        """Pair-tokenization path. Each ``(title, body)`` pair is fed to the
        tokenizer with ``text=title`` and ``text_pair=body``, which produces
        the ``title [SEP] body`` input shape SPECTER2 was trained on.

        ``body`` may be None / empty — in that case the call degrades
        gracefully to the single-sequence path for that one example.
        """
        return self._run(pairs,
                         normalize_embeddings=normalize_embeddings,
                         convert_to_numpy=convert_to_numpy)

    def _run(
        self,
        pairs: list[tuple[str, str | None]],
        *,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
    ):
        torch = self._torch
        # The HF tokenizer accepts parallel lists for batched pair inputs;
        # None entries must be filtered out of text_pair so we don't force
        # an empty SEP-suffix on chunks that have no natural pair.
        texts = [pair[0] for pair in pairs]
        text_pairs = [pair[1] for pair in pairs]
        # If no example has a pair, skip the text_pair argument entirely so
        # the tokenizer uses its single-sequence fast path.
        if any(pair is not None and pair != "" for pair in text_pairs):
            normalized_pairs = [pair if pair else "" for pair in text_pairs]
            inputs = self._tokenizer(
                text=texts,
                text_pair=normalized_pairs,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
        else:
            inputs = self._tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
        inputs = inputs.to(self._device)
        with torch.inference_mode():
            hidden = self._model(**inputs).last_hidden_state
        pooled = hidden[:, 0, :]
        if normalize_embeddings:
            pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
        if convert_to_numpy:
            return pooled.detach().cpu().numpy()
        return pooled


# HuggingFace model names that do not ship a sentence-transformers bundle and
# must be loaded via raw transformers + CLS pooling. Match on substring so that
# both ``allenai/specter2_base`` and future Allen AI SciRepEval variants
# (``scirepeval_*``) pick up the same path.
_TRANSFORMERS_CLS_MODEL_TOKENS: tuple[str, ...] = (
    "specter2",
    "scirepeval",
)


def _needs_raw_transformers_path(model_name: str) -> bool:
    lowered = model_name.lower()
    return any(token in lowered for token in _TRANSFORMERS_CLS_MODEL_TOKENS)


class OptionalEmbeddingProvider:
    # Module-level flags so we log each distinct disabled-state exactly once,
    # rather than once per query.
    _logged_unconfigured: bool = False
    _logged_import_failure: bool = False
    _logged_load_failure: bool = False

    def __init__(self, settings: Settings) -> None:
        self._model_name = settings.embedding_model
        self._expected_dim = settings.embedding_dim

    def _model(self):
        if not self._model_name:
            if not OptionalEmbeddingProvider._logged_unconfigured:
                logger.info(
                    "Embeddings disabled: no PAPERBASE_EMBEDDING_MODEL set",
                    extra={"event": "embed.unconfigured"},
                )
                OptionalEmbeddingProvider._logged_unconfigured = True
            return None
        cached = _MODEL_CACHE.get(self._model_name)
        if cached is not None:
            return cached

        # SPECTER2 and the SciRepEval family are published as adapter models
        # (no sentence-transformers modules.json); load them via raw
        # transformers with CLS pooling. Everything else goes through the
        # sentence-transformers auto-loader, which handles its own pooling.
        if _needs_raw_transformers_path(self._model_name):
            try:
                model = _TransformersCLSEmbedder(self._model_name)
            except ImportError as exc:
                if not OptionalEmbeddingProvider._logged_import_failure:
                    logger.warning(
                        "Embeddings disabled: transformers import failed",
                        extra={"event": "embed.import_failed", "error": str(exc)},
                    )
                    OptionalEmbeddingProvider._logged_import_failure = True
                return None
            except Exception as exc:
                if not OptionalEmbeddingProvider._logged_load_failure:
                    logger.warning(
                        "Embeddings disabled: model failed to load (transformers path)",
                        extra={
                            "event": "embed.load_failed",
                            "model": self._model_name,
                            "error": str(exc),
                        },
                    )
                    OptionalEmbeddingProvider._logged_load_failure = True
                return None
            _MODEL_CACHE[self._model_name] = model
            return model

        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:
            if not OptionalEmbeddingProvider._logged_import_failure:
                logger.warning(
                    "Embeddings disabled: sentence-transformers import failed",
                    extra={"event": "embed.import_failed", "error": str(exc)},
                )
                OptionalEmbeddingProvider._logged_import_failure = True
            return None
        try:
            model = SentenceTransformer(self._model_name)
        except Exception as exc:
            if not OptionalEmbeddingProvider._logged_load_failure:
                logger.warning(
                    "Embeddings disabled: model failed to load",
                    extra={
                        "event": "embed.load_failed",
                        "model": self._model_name,
                        "error": str(exc),
                    },
                )
                OptionalEmbeddingProvider._logged_load_failure = True
            return None
        _MODEL_CACHE[self._model_name] = model
        return model

    def encode(self, text: str) -> list[float] | None:
        batch = self.encode_batch([text])
        if batch is None or not batch:
            return None
        return batch[0]

    def encode_batch(self, texts: list[str]) -> list[list[float]] | None:
        if not texts:
            return []
        model = self._model()
        if model is None:
            return None
        try:
            vectors = model.encode(
                texts,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            # This one we log every time — query-level failures are actionable,
            # and we don't want to hide a flapping model behind a once-flag.
            logger.warning("Embedding encode failed", extra={"event": "embed.encode_failed", "error": str(exc)})
            return None
        return [[float(v) for v in vector.tolist()] for vector in vectors]

    def encode_paper(self, title: str, body: str) -> list[float] | None:
        """Paper-level embedding with backend-correct input shape.

        SPECTER2-family models expect ``title + [SEP] + abstract`` as a pair
        input to the tokenizer; concatenated strings land in a subtly
        different embedding space. For sentence-transformers-compatible
        models we just join with a newline, which is their trained input.
        """
        batch = self.encode_paper_batch([(title, body)])
        if batch is None or not batch:
            return None
        return batch[0]

    def encode_paper_batch(self, pairs: list[tuple[str, str]]) -> list[list[float]] | None:
        if not pairs:
            return []
        model = self._model()
        if model is None:
            return None
        try:
            if self._model_name and _needs_raw_transformers_path(self._model_name):
                # Route through the pair tokenizer path so SPECTER2 sees the
                # title [SEP] body shape it was trained on.
                vectors = model.encode_pairs(
                    [(title or "", body or "") for title, body in pairs],
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                )
            else:
                # sentence-transformers models take a single string; match
                # what BGE / MPNet / all-MiniLM were trained on.
                joined = [
                    "\n\n".join(part for part in (title, body) if part)
                    for title, body in pairs
                ]
                vectors = model.encode(
                    joined,
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )
        except Exception as exc:
            logger.warning(
                "Paper embedding encode failed",
                extra={"event": "embed.encode_failed", "error": str(exc)},
            )
            return None
        return [
            [float(v) for v in (vector.tolist() if hasattr(vector, "tolist") else vector)]
            for vector in vectors
        ]


class OptionalReranker:
    def __init__(self, settings: Settings) -> None:
        self._model_name = settings.rerank_model
        self._top_k = settings.rerank_top_k

    def enabled(self) -> bool:
        return bool(self._model_name)

    @property
    def top_k(self) -> int:
        return self._top_k

    def _model(self):
        if not self._model_name:
            return None
        cached = _RERANKER_CACHE.get(self._model_name)
        if cached is not None:
            return cached
        try:
            from sentence_transformers import CrossEncoder
        except Exception as exc:
            logger.warning(
                "CrossEncoder unavailable, reranker disabled",
                extra={"event": "rerank.unavailable", "error": str(exc)},
            )
            return None
        try:
            model = CrossEncoder(self._model_name)
        except Exception as exc:
            logger.warning(
                "Failed to load reranker, reranker disabled",
                extra={"event": "rerank.load_failed", "model": self._model_name, "error": str(exc)},
            )
            return None
        _RERANKER_CACHE[self._model_name] = model
        return model

    def score(self, query: str, docs: list[str]) -> list[float] | None:
        if not docs:
            return []
        model = self._model()
        if model is None:
            return None
        try:
            scores = model.predict([(query, doc) for doc in docs], show_progress_bar=False)
        except Exception as exc:
            logger.warning("Reranker predict failed", extra={"event": "rerank.predict_failed", "error": str(exc)})
            return None
        return [float(s) for s in scores]


class PaperbaseService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: PaperRepository,
        object_store: ObjectStore,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.object_store = object_store
        self.embeddings = OptionalEmbeddingProvider(settings)
        self.reranker = OptionalReranker(settings)

    def init(self) -> None:
        self.repository.init()

    def enqueue_index_job(self, arxiv_id: str, *, force: bool = False) -> JobResponse:
        job = self.repository.create_job("index_paper", {"arxiv_id": arxiv_id, "force": force})
        logger.info(
            "Created index job",
            extra={
                "event": "jobs.index.created",
                "job_id": job.job_id,
                "arxiv_id": arxiv_id,
                "force": force,
            },
        )
        return job

    def enqueue_reindex_embeddings_job(self, *, limit: int = 100_000, include_chunks: bool = True) -> JobResponse:
        job = self.repository.create_job(
            "reindex_missing_embeddings",
            {"limit": limit, "include_chunks": include_chunks},
        )
        logger.info(
            "Created reindex_missing_embeddings job",
            extra={
                "event": "jobs.reindex.created",
                "job_id": job.job_id,
                "limit": limit,
                "include_chunks": include_chunks,
            },
        )
        return job

    def reindex_missing_embeddings(self, *, limit: int, include_chunks: bool = True) -> dict[str, int]:
        if self.embeddings is None:
            logger.warning(
                "reindex_missing_embeddings invoked without an embedding provider",
                extra={"event": "reindex.noop"},
            )
            return {"papers_updated": 0, "chunks_updated": 0}

        papers_updated = 0
        chunks_updated = 0
        page = max(64, min(limit, 512))
        remaining = limit
        # Keyset cursor over the same ordering used by the repo method. Advances
        # after every fetched page so a paper that failed to encode this round
        # does not keep the frontier stuck (failed rows still match the
        # backfill predicate).
        after: PaperResponse | None = None

        while remaining > 0:
            batch = self.repository.list_papers_needing_embedding_backfill(
                limit=min(page, remaining),
                include_chunks=include_chunks,
                after=after,
            )
            if not batch:
                break

            # Paper-level embeddings in one batched encode. Use the same
            # (title, body) pair contract as index_arxiv_paper so the
            # reindex path produces vectors in the same space SPECTER2 was
            # trained on — summary_short is intentionally excluded.
            pairs = [
                (paper.title or "", paper.tldr or paper.abstract or "")
                for paper in batch
            ]
            vectors = self.embeddings.encode_paper_batch(pairs)
            if vectors is not None and len(vectors) == len(batch):
                for paper, vector in zip(batch, vectors):
                    self.repository.update_paper_embedding(paper.paper_id, vector)
                    papers_updated += 1

            if include_chunks:
                for paper in batch:
                    missing = self.repository.list_chunks_missing_embedding(paper.paper_id)
                    if not missing:
                        continue
                    cap = self.settings.chunk_max_chars + self.settings.chunk_overlap_chars
                    chunk_inputs = [(chunk.get("content") or "")[:cap] for chunk in missing]
                    chunk_vectors = self.embeddings.encode_batch(chunk_inputs)
                    if chunk_vectors is None or len(chunk_vectors) != len(missing):
                        continue
                    for chunk, vector in zip(missing, chunk_vectors):
                        self.repository.update_chunk_embedding(chunk["chunk_id"], vector)
                        chunks_updated += 1

            after = batch[-1]
            remaining -= len(batch)
            logger.info(
                "reindex_missing_embeddings progress",
                extra={
                    "event": "reindex.progress",
                    "papers_updated": papers_updated,
                    "chunks_updated": chunks_updated,
                },
            )

        logger.info(
            "Completed reindex_missing_embeddings",
            extra={
                "event": "reindex.completed",
                "papers_updated": papers_updated,
                "chunks_updated": chunks_updated,
            },
        )
        return {"papers_updated": papers_updated, "chunks_updated": chunks_updated}

    def enqueue_summarize_top_cited_job(self, *, limit: int = 500_000) -> JobResponse:
        job = self.repository.create_job("summarize_top_cited", {"limit": limit})
        logger.info(
            "Created summarize_top_cited job",
            extra={
                "event": "jobs.summarize_top_cited.created",
                "job_id": job.job_id,
                "limit": limit,
            },
        )
        return job

    def summarize_top_cited(self, *, limit: int) -> dict[str, int]:
        from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait

        from .summaries import generate_summary_on_demand

        if self.settings.summary_provider != "vertex":
            logger.warning(
                "summarize_top_cited invoked but summary_provider is not 'vertex'; nothing to do",
                extra={"event": "summarize_top_cited.noop", "provider": self.settings.summary_provider},
            )
            return {"processed": 0, "updated": 0, "failed": 0}

        concurrency = max(1, self.settings.featured_tier_concurrency)
        # Sliding window of in-flight futures so we never buffer all of `limit`
        # papers and their futures in memory at once. Page size matches the
        # window so each DB read matches what the pool is about to process.
        window = max(concurrency * 4, 64)

        logger.info(
            "Starting summarize_top_cited",
            extra={
                "event": "summarize_top_cited.start",
                "limit": limit,
                "concurrency": concurrency,
                "in_flight_window": window,
            },
        )

        def _summarize_one(paper: PaperResponse) -> tuple[str, tuple[str, str] | None]:
            try:
                return paper.paper_id, generate_summary_on_demand(paper, self.settings)
            except Exception as exc:
                logger.warning(
                    "featured-tier paper summary failed",
                    extra={
                        "event": "summarize_top_cited.paper_failed",
                        "paper_id": paper.paper_id,
                        "error": str(exc),
                    },
                )
                return paper.paper_id, None

        processed = 0
        updated = 0
        failed = 0
        in_flight: set = set()
        remaining = limit
        # Keyset cursor over the same ordering list_papers_needing_summary
        # uses. Advances past every fetched paper regardless of whether its
        # summarization succeeded, so the loop cannot stall on a failed
        # frontier (rows that failed to summarize still match the predicate).
        after: PaperResponse | None = None

        def _drain(wait_all: bool) -> None:
            nonlocal processed, updated, failed
            if not in_flight:
                return
            mode = None if wait_all else FIRST_COMPLETED
            done, still_in_flight = wait(in_flight, return_when=mode) if mode else (set(in_flight), set())
            for future in done:
                paper_id, result = future.result()
                processed += 1
                if result is None:
                    failed += 1
                else:
                    short, eli5 = result
                    if self.repository.update_summaries(paper_id, summary_short=short, summary_eli5=eli5) is not None:
                        updated += 1
                if processed % 500 == 0:
                    logger.info(
                        "summarize_top_cited progress",
                        extra={
                            "event": "summarize_top_cited.progress",
                            "processed": processed,
                            "updated": updated,
                            "failed": failed,
                        },
                    )
            in_flight.difference_update(done)

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            while remaining > 0:
                page_size = min(window, remaining)
                batch = self.repository.list_papers_needing_summary(limit=page_size, after=after)
                if not batch:
                    break
                for paper in batch:
                    if len(in_flight) >= window:
                        _drain(wait_all=False)
                    in_flight.add(pool.submit(_summarize_one, paper))
                # Advance the keyset cursor even if some submissions failed;
                # failed papers still match the predicate, so without advancing
                # the next fetch would return the same frontier forever.
                after = batch[-1]
                remaining -= len(batch)
            _drain(wait_all=True)

        logger.info(
            "Completed summarize_top_cited",
            extra={
                "event": "summarize_top_cited.completed",
                "processed": processed,
                "updated": updated,
                "failed": failed,
            },
        )
        return {"processed": processed, "updated": updated, "failed": failed}

    def enqueue_latest_sync_job(self) -> JobResponse:
        job = self.repository.create_job(
            "sync_latest",
            {
                "days": self.settings.latest_sync_days,
                "categories": self.settings.arxiv_categories,
                "limit_per_category": self.settings.latest_sync_limit_per_category,
            },
        )
        logger.info(
            "Created latest sync job",
            extra={
                "event": "jobs.sync_latest.created",
                "job_id": job.job_id,
                "sync_days": self.settings.latest_sync_days,
                "category_count": len(self.settings.arxiv_categories),
                "limit_per_category": self.settings.latest_sync_limit_per_category,
            },
        )
        return job

    def process_next_job(self) -> JobResponse | None:
        job = self.repository.claim_pending_job()
        if job is None:
            return None
        logger.info(
            "Claimed job",
            extra={
                "event": "jobs.claimed",
                "job_id": job.job_id,
                "job_type": job.job_type,
            },
        )
        try:
            if job.job_type == "index_paper":
                self.index_arxiv_paper(job.payload["arxiv_id"], force=bool(job.payload.get("force")))
            elif job.job_type == "sync_latest":
                self.sync_latest_papers(
                    days=int(job.payload.get("days", self.settings.latest_sync_days)),
                    categories=list(job.payload.get("categories", self.settings.arxiv_categories)),
                    limit_per_category=int(job.payload.get("limit_per_category", self.settings.latest_sync_limit_per_category)),
                )
            elif job.job_type == "summarize_top_cited":
                self.summarize_top_cited(limit=int(job.payload.get("limit", 500_000)))
            elif job.job_type == "reindex_missing_embeddings":
                self.reindex_missing_embeddings(
                    limit=int(job.payload.get("limit", 100_000)),
                    include_chunks=bool(job.payload.get("include_chunks", True)),
                )
            else:
                raise ValueError(f"Unsupported job type: {job.job_type}")
            self.repository.mark_job_completed(job.job_id)
            logger.info(
                "Completed job",
                extra={
                    "event": "jobs.completed",
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                },
            )
        except Exception as exc:  # pragma: no cover - surfaced via job state and API
            self.repository.mark_job_failed(job.job_id, str(exc))
            logger.exception(
                "Job failed",
                extra={
                    "event": "jobs.failed",
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                },
            )
        return self.repository.get_job(job.job_id)

    def sync_latest_papers(self, *, days: int, categories: list[str], limit_per_category: int) -> list[PaperResponse]:
        logger.info(
            "Starting latest sync",
            extra={
                "event": "sync_latest.start",
                "sync_days": days,
                "category_count": len(categories),
                "limit_per_category": limit_per_category,
            },
        )
        indexed: list[PaperResponse] = []
        seen: set[str] = set()
        for category in categories:
            papers = sources.fetch_recent(category, days=days, max_results=limit_per_category)
            sources.enrich_with_semantic_scholar(papers)
            for paper in papers:
                if paper.arxiv_id in seen:
                    continue
                seen.add(paper.arxiv_id)
                indexed.append(self.index_arxiv_paper(paper.arxiv_id, paper=paper, force=False))
        logger.info(
            "Completed latest sync",
            extra={
                "event": "sync_latest.completed",
                "indexed_count": len(indexed),
                "unique_paper_count": len(seen),
            },
        )
        return indexed

    def index_arxiv_paper(self, arxiv_id: str, *, paper: PaperMetadata | None = None, force: bool = False) -> PaperResponse:
        existing = self.repository.get_paper_by_arxiv_id(arxiv_id)
        if existing and existing.index_status == "indexed" and not force:
            logger.info(
                "Skipped already indexed paper",
                extra={
                    "event": "index.skipped",
                    "arxiv_id": arxiv_id,
                    "paper_id": existing.paper_id,
                },
            )
            return existing

        logger.info(
            "Starting paper indexing",
            extra={
                "event": "index.start",
                "arxiv_id": arxiv_id,
                "force": force,
            },
        )
        metadata = paper or sources.get_paper_details(arxiv_id)
        if metadata is None:
            raise HTTPException(status_code=404, detail=f"Paper {arxiv_id} not found on arXiv")
        sources.enrich_with_semantic_scholar([metadata])
        metadata.index_status = "indexing"
        stored = self.repository.upsert_paper(metadata, search_document=metadata.abstract)
        extraction = index_paper_artifacts(metadata)

        metadata.source_type = extraction.source_type
        metadata.index_status = "indexed"
        metadata.indexed_at = utcnow()
        metadata.summary_short, metadata.summary_eli5 = build_paper_summaries(metadata, extraction, self.settings)

        search_document_parts = [metadata.title, metadata.tldr, metadata.summary_short, extraction.text_markdown]
        search_document = "\n\n".join(part for part in search_document_parts if part)

        # Paper-level embedding uses the (title, body) pair contract that
        # SPECTER2 is trained on. summary_short is intentionally *not* fed
        # into this vector — it is heuristic text with a noisy signal, and
        # SPECTER2's training distribution never saw it. It stays in
        # search_document for lexical scoring and lives on chunks.
        paper_title = metadata.title or ""
        paper_body = metadata.tldr or metadata.abstract or ""
        paper_embedding = (
            self.embeddings.encode_paper(paper_title, paper_body)
            if (paper_title or paper_body)
            else None
        )

        stored = self.repository.upsert_paper(metadata, search_document=search_document, embedding=paper_embedding)

        prefix = f"papers/{stored.paper_id}"
        self.object_store.delete_prefix(prefix)
        self.object_store.put_text(f"{prefix}/meta.json", json.dumps(metadata.model_dump(mode='json'), indent=2), content_type="application/json")
        self.object_store.put_text(f"{prefix}/text/full.md", extraction.text_markdown, content_type="text/markdown")

        text_chunks: list[dict[str, Any]] = [
            {
                "path": "text/full.md",
                "chunk_kind": "full_text",
                "content": extraction.text_markdown,
                "chunk_index": 0,
                "section_depth": 0,
                "section_order": 0,
            }
        ]

        for section_order, section in enumerate(extraction.sections):
            section_path = f"{prefix}/sections/{section.slug}.md"
            self.object_store.put_text(section_path, section.markdown, content_type="text/markdown")
            section_depth = _section_depth_for(section)
            for chunk_index, sub_content in enumerate(
                _stride_chunks(
                    section.markdown,
                    max_chars=self.settings.chunk_max_chars,
                    overlap=self.settings.chunk_overlap_chars,
                )
            ):
                text_chunks.append(
                    {
                        "path": f"sections/{section.slug}.md",
                        "chunk_kind": "section",
                        "content": sub_content,
                        "chunk_index": chunk_index,
                        "section_depth": section_depth,
                        "section_order": section_order,
                    }
                )

        if extraction.figures:
            self.object_store.put_text(
                f"{prefix}/figures/index.json",
                json.dumps(figure_index_payload(extraction.figures), indent=2),
                content_type="application/json",
            )
            for figure_order, figure in enumerate(extraction.figures):
                caption_path = f"{prefix}/figures/{figure.figure_id}/caption.md"
                self.object_store.put_text(caption_path, figure.caption, content_type="text/markdown")
                text_chunks.append(
                    {
                        "path": f"figures/{figure.figure_id}/caption.md",
                        "chunk_kind": "figure_caption",
                        "content": figure.caption,
                        "chunk_index": 0,
                        "section_depth": 1,
                        "section_order": figure_order,
                    }
                )
                if figure.image_bytes is not None:
                    image_path = f"{prefix}/figures/{figure.figure_id}/image.{figure.file_extension}"
                    self.object_store.put_bytes(image_path, figure.image_bytes, content_type=figure.content_type)

        if extraction.tables:
            self.object_store.put_text(
                f"{prefix}/tables/index.json",
                json.dumps(table_index_payload(extraction.tables), indent=2),
                content_type="application/json",
            )
            for table_order, table in enumerate(extraction.tables):
                json_path = f"{prefix}/tables/{table.table_id}.json"
                csv_path = f"{prefix}/tables/{table.table_id}.csv"
                self.object_store.put_bytes(json_path, table_json_bytes(table), content_type="application/json")
                self.object_store.put_bytes(csv_path, table_csv_bytes(table), content_type="text/csv")
                table_text = _table_to_indexable_text(table)
                if table_text:
                    text_chunks.append(
                        {
                            "path": f"tables/{table.table_id}.json",
                            "chunk_kind": "table",
                            "content": table_text,
                            "chunk_index": 0,
                            "section_depth": 1,
                            "section_order": table_order,
                        }
                    )

        # Truncate per-kind before embedding — full_text stays large so that
        # vector search can still recover it as a coarse fallback, but
        # sections/tables stay focused.
        chunk_texts: list[str] = []
        for chunk in text_chunks:
            cap = 4000 if chunk["chunk_kind"] == "full_text" else self.settings.chunk_max_chars + self.settings.chunk_overlap_chars
            chunk_texts.append((chunk["content"] or "")[:cap])
        chunk_embeddings = self.embeddings.encode_batch(chunk_texts)
        if chunk_embeddings is not None and len(chunk_embeddings) == len(text_chunks):
            for chunk, vector in zip(text_chunks, chunk_embeddings):
                chunk["embedding"] = vector

        for artifact in extraction.raw_artifacts:
            self.object_store.put_bytes(f"{prefix}/{artifact.path}", artifact.data, content_type=artifact.content_type)

        manifest = build_manifest(stored.paper_id, metadata, extraction)
        self.object_store.put_text(
            f"{prefix}/manifest.json",
            json.dumps(manifest, indent=2),
            content_type="application/json",
        )

        self.repository.replace_text_chunks(stored.paper_id, text_chunks)

        if self.settings.fetch_references_at_index:
            try:
                refs = sources.fetch_references(metadata.arxiv_id, metadata.semantic_scholar_id)
                if refs:
                    self.repository.replace_paper_refs(stored.paper_id, refs)
            except Exception as exc:
                logger.warning(
                    "Failed to fetch references",
                    extra={"event": "index.refs_failed", "arxiv_id": arxiv_id, "error": str(exc)},
                )

        logger.info(
            "Completed paper indexing",
            extra={
                "event": "index.completed",
                "arxiv_id": arxiv_id,
                "paper_id": stored.paper_id,
                "section_count": len(extraction.sections),
                "figure_count": len(extraction.figures),
                "table_count": len(extraction.tables),
                "raw_artifact_count": len(extraction.raw_artifacts),
            },
        )
        return stored

    def search(self, request: SearchRequest) -> SearchResponse:
        k = self.settings.rrf_k
        pool_size = max(request.limit * 4, 50)
        query_embedding = self.embeddings.encode(request.query)

        local_results = self.repository.search_local(
            request.query,
            limit=pool_size,
            query_embedding=query_embedding,
            rrf_k=k,
            citation_boost_scale=self.settings.citation_boost_scale,
            citation_boost_max=self.settings.citation_boost_max,
        )

        arxiv_results: list[PaperMetadata] = []
        s2_results: list[PaperMetadata] = []
        if request.include_live_sources:
            try:
                arxiv_results = sources.search_arxiv(request.query, max_results=request.limit, category=request.category)
            except Exception as exc:
                logger.warning("arxiv live search failed", extra={"event": "search.arxiv_failed", "error": str(exc)})
            try:
                s2_results = sources.search_semantic_scholar(request.query, limit=request.limit, year=request.year)
            except Exception as exc:
                logger.warning("s2 live search failed", extra={"event": "search.s2_failed", "error": str(exc)})
            live_papers = arxiv_results + s2_results
            if live_papers:
                sources.enrich_with_semantic_scholar(live_papers)

        candidates: dict[str, SearchPaperResult] = {}
        rrf: dict[str, float] = {}

        for rank, result in enumerate(local_results):
            pid = result.paper_id
            candidates[pid] = result
            rrf[pid] = rrf.get(pid, 0.0) + 1.0 / (k + rank)

        live_weight = (
            self.settings.live_source_weight_recent
            if _query_signals_recency(request.query)
            else self.settings.live_source_weight
        )

        for rank, paper in enumerate(arxiv_results):
            stored = self.repository.upsert_paper_shallow(paper)
            pid = stored.paper_id
            candidates.setdefault(pid, SearchPaperResult(**stored.model_dump(), source="live-arxiv"))
            rrf[pid] = rrf.get(pid, 0.0) + live_weight / (k + rank)

        for rank, paper in enumerate(s2_results):
            stored = self.repository.upsert_paper_shallow(paper)
            pid = stored.paper_id
            candidates.setdefault(pid, SearchPaperResult(**stored.model_dump(), source="live-s2", tldr=paper.tldr or None))
            rrf[pid] = rrf.get(pid, 0.0) + live_weight / (k + rank)

        from math import log10

        for pid, paper in candidates.items():
            citation_boost = min(
                self.settings.citation_boost_scale * log10(1 + (paper.citation_count or 0)),
                self.settings.citation_boost_max,
            )
            rrf[pid] = rrf.get(pid, 0.0) + citation_boost

        fused = sorted(rrf.items(), key=lambda item: item[1], reverse=True)
        fused_papers = [candidates[pid] for pid, _ in fused if pid in candidates]

        if self.reranker.enabled():
            rerank_head = fused_papers[: self.reranker.top_k]
            rerank_tail = fused_papers[self.reranker.top_k :]
            best_chunks = self.repository.best_chunk_for_papers(
                request.query,
                [p.paper_id for p in rerank_head],
                query_embedding=query_embedding,
            )
            doc_texts = [
                " ".join(
                    part
                    for part in (
                        p.title,
                        best_chunks.get(p.paper_id) or p.tldr or p.summary_short or p.abstract,
                    )
                    if part
                )[:2000]
                for p in rerank_head
            ]
            rerank_scores = self.reranker.score(request.query, doc_texts)
            if rerank_scores is not None and len(rerank_scores) == len(rerank_head):
                head_ranked = sorted(
                    zip(rerank_head, rerank_scores), key=lambda pair: pair[1], reverse=True
                )
                for paper, score in head_ranked:
                    paper.score = float(score)
                fused_papers = [paper for paper, _ in head_ranked] + rerank_tail
            else:
                for paper in rerank_head:
                    paper.score = rrf[paper.paper_id]
        else:
            for paper in fused_papers:
                paper.score = rrf[paper.paper_id]

        ordered = fused_papers[: request.limit]
        result_set = self.repository.save_result_set(
            query=request.query,
            papers=ordered,
            ttl_days=self.settings.result_ttl_days,
        )
        return SearchResponse(
            result_set_id=result_set.result_set_id,
            query=request.query,
            papers=ordered,
            expires_at=result_set.expires_at,
        )

    def get_results(self, result_set_id: str) -> ResultSetResponse:
        result_set = self.repository.get_result_set(result_set_id)
        if result_set is None:
            raise HTTPException(status_code=404, detail=f"Result set {result_set_id} not found")
        return result_set

    def search_sections(self, request: SearchSectionRequest) -> SearchSectionResponse:
        if request.result_set_id:
            self.get_results(request.result_set_id)
        query_embedding = self.embeddings.encode(request.query)
        sections = self.repository.search_sections(
            request.query,
            limit=request.limit,
            result_set_id=request.result_set_id,
            paper_ids=request.paper_ids,
            query_embedding=query_embedding,
            rrf_k=self.settings.rrf_k,
        )
        return SearchSectionResponse(query=request.query, sections=sections)

    def grep_results(self, result_set_id: str, pattern: str, *, limit: int) -> GrepResponse:
        parent = self.get_results(result_set_id)
        matches = self.repository.grep_result_set(result_set_id, pattern, limit=limit)
        narrowed_papers: list[SearchPaperResult] = []
        paper_lookup = {paper.paper_id: paper for paper in parent.papers}
        for match in matches:
            if match.paper_id in paper_lookup:
                narrowed_papers.append(paper_lookup[match.paper_id])
        saved = self.repository.save_result_set(
            query=parent.query,
            papers=narrowed_papers,
            parent_result_set_id=result_set_id,
            pattern=pattern,
            ttl_days=self.settings.result_ttl_days,
        )
        return GrepResponse(
            result_set_id=saved.result_set_id,
            parent_result_set_id=result_set_id,
            pattern=pattern,
            papers=matches,
            expires_at=saved.expires_at,
        )

    def get_paper(self, paper_id: str) -> PaperResponse:
        paper = self.repository.get_paper(paper_id)
        if paper is None:
            raise HTTPException(status_code=404, detail=f"Paper {paper_id} not found")
        if self._should_lazy_summarize(paper):
            upgraded = self._lazy_summarize(paper)
            if upgraded is not None:
                return upgraded
        return paper

    def _should_lazy_summarize(self, paper: PaperResponse) -> bool:
        if not self.settings.lazy_summary_on_read:
            return False
        if self.settings.summary_provider != "vertex":
            return False
        return not paper.summary_eli5

    def _lazy_summarize(self, paper: PaperResponse) -> PaperResponse | None:
        from .summaries import generate_summary_on_demand

        result = generate_summary_on_demand(paper, self.settings)
        if result is None:
            return None
        short, eli5 = result
        updated = self.repository.update_summaries(paper.paper_id, summary_short=short, summary_eli5=eli5)
        logger.info(
            "Lazy summary generated",
            extra={"event": "summary.lazy", "paper_id": paper.paper_id},
        )
        return updated or paper

    def get_manifest(self, paper_id: str) -> ManifestResponse:
        path = f"papers/{paper_id}/manifest.json"
        if not self.object_store.exists(path):
            raise HTTPException(status_code=404, detail=f"Manifest for {paper_id} not found")
        return ManifestResponse(paper_id=paper_id, manifest=json.loads(self.object_store.get_text(path)))

    def list_files(self, paper_id: str) -> FileListResponse:
        prefix = f"papers/{paper_id}/"
        objects = self.object_store.list_prefix(prefix)
        files = [
            FileObject(path=item.path[len(prefix):], size=item.size, content_type=item.content_type)
            for item in objects
        ]
        return FileListResponse(paper_id=paper_id, files=files)

    def read_file(self, paper_id: str, path: str) -> FileContentResponse:
        full_path = f"papers/{paper_id}/{path}"
        if not self.object_store.exists(full_path):
            raise HTTPException(status_code=404, detail=f"File {path} not found for {paper_id}")
        return FileContentResponse(paper_id=paper_id, path=path, content=self.object_store.get_text(full_path))

    def get_figure(self, paper_id: str, figure_id: str) -> FigureResponse:
        caption_path = f"papers/{paper_id}/figures/{figure_id}/caption.md"
        if not self.object_store.exists(caption_path):
            raise HTTPException(status_code=404, detail=f"Figure {figure_id} not found for {paper_id}")
        image_url = None
        content_type = None
        for extension in ("png", "jpg", "jpeg", "svg", "gif", "webp", "bin"):
            image_path = f"papers/{paper_id}/figures/{figure_id}/image.{extension}"
            if self.object_store.exists(image_path):
                image_url = self.object_store.presign_get(image_path, expires_in=self.settings.presign_ttl_seconds)
                content_type = "image/" + ("jpeg" if extension == "jpg" else extension)
                break
        return FigureResponse(
            paper_id=paper_id,
            figure_id=figure_id,
            caption=self.object_store.get_text(caption_path),
            image_url=image_url,
            content_type=content_type,
        )

    def get_table(self, paper_id: str, table_id: str) -> TableResponse:
        json_path = f"papers/{paper_id}/tables/{table_id}.json"
        csv_path = f"papers/{paper_id}/tables/{table_id}.csv"
        if not self.object_store.exists(json_path):
            raise HTTPException(status_code=404, detail=f"Table {table_id} not found for {paper_id}")
        rows_payload = json.loads(self.object_store.get_text(json_path))
        if rows_payload and isinstance(rows_payload[0], dict):
            rows = [list(rows_payload[0].keys())] + [list(record.values()) for record in rows_payload]
        else:
            rows = []
        table_index_path = f"papers/{paper_id}/tables/index.json"
        caption = None
        if self.object_store.exists(table_index_path):
            index_payload = json.loads(self.object_store.get_text(table_index_path))
            for item in index_payload:
                if item.get("table_id") == table_id:
                    caption = item.get("caption")
                    break
        return TableResponse(
            paper_id=paper_id,
            table_id=table_id,
            caption=caption,
            rows=rows,
            csv_url=self.object_store.presign_get(csv_path, expires_in=self.settings.presign_ttl_seconds),
        )

    def get_paper_references(self, paper_id: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        paper = self.get_paper(paper_id)
        refs = self.repository.get_paper_refs(paper_id)
        if refs and not refresh:
            return refs
        fetched = sources.fetch_references(paper.arxiv_id, paper.semantic_scholar_id)
        if fetched:
            self.repository.replace_paper_refs(paper_id, fetched)
            return self.repository.get_paper_refs(paper_id)
        return refs

    def get_related_papers(self, paper_id: str) -> list[SearchPaperResult]:
        paper = self.get_paper(paper_id)
        related = sources.get_related_papers(paper.arxiv_id, paper.semantic_scholar_id)
        papers: list[SearchPaperResult] = []
        for item in related:
            stored = self.repository.upsert_paper_shallow(item)
            papers.append(SearchPaperResult(**stored.model_dump(), source="related"))
        return papers

    def get_job(self, job_id: str) -> JobResponse:
        job = self.repository.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        return job

    def cleanup_expired_results(self) -> None:
        self.repository.cleanup_expired_results()

    def suggest_papers(self, q: str, *, limit: int = 10) -> list[dict[str, Any]]:
        return self.repository.suggest_papers(q, limit=limit)

    def get_stats(self) -> dict[str, Any]:
        return self.repository.stats()

    def expand_query(self, query: str, *, variants: int | None = None) -> list[str]:
        from .summaries import expand_query_with_gemini

        want = variants if variants is not None else self.settings.query_expansion_variants
        expanded = expand_query_with_gemini(query, self.settings, variants=want)
        if not expanded:
            return [query]
        return expanded

    def probe_integrations(self) -> IntegrationProbeResponse:
        checked_at = utcnow()
        database_backend = "memory" if self.settings.database_url.startswith("memory://") else "sqlalchemy"
        database_target = "memory://" if database_backend == "memory" else self.settings.database_url.split(":", 1)[0].split("+", 1)[0]
        try:
            database = self.repository.probe()
        except Exception as exc:
            database = IntegrationProbeComponent(
                ok=False,
                backend=database_backend,
                detail=f"Database probe failed: {exc}",
                target=database_target,
                metadata={"error_type": exc.__class__.__name__},
            )
        storage = self._probe_object_store(checked_at)
        return IntegrationProbeResponse(
            ok=database.ok and storage.ok,
            checked_at=checked_at,
            database=database,
            storage=storage,
        )

    def _probe_object_store(self, checked_at) -> IntegrationProbeComponent:
        probe_id = new_id("probe")
        probe_path = f"system/probes/{probe_id}.json"
        payload = json.dumps(
            {"probe_id": probe_id, "checked_at": checked_at.isoformat()},
            sort_keys=True,
        )
        if self.settings.storage_backend == "gcs":
            target = self.settings.gcs_bucket
        elif self.settings.storage_backend == "local":
            target = self.settings.local_storage_root
        else:
            target = self.settings.storage_backend
        metadata: dict[str, object] = {"path": probe_path}

        try:
            self.object_store.put_text(probe_path, payload, content_type="application/json")
            if not self.object_store.exists(probe_path):
                raise RuntimeError("probe object was not visible after write")
            roundtrip = self.object_store.get_text(probe_path)
            if roundtrip != payload:
                raise RuntimeError("probe object contents changed during roundtrip")
            metadata["bytes"] = len(payload.encode("utf-8"))
        except Exception as exc:
            metadata["error_type"] = exc.__class__.__name__
            return IntegrationProbeComponent(
                ok=False,
                backend=self.settings.storage_backend,
                detail=f"Object store write/read probe failed: {exc}",
                target=target,
                metadata=metadata,
            )

        try:
            signed_url = self.object_store.presign_get(
                probe_path,
                expires_in=min(self.settings.presign_ttl_seconds, 300),
            )
        except Exception as exc:
            metadata["error_type"] = exc.__class__.__name__
            return IntegrationProbeComponent(
                ok=False,
                backend=self.settings.storage_backend,
                detail=f"Object store write/read succeeded but signed URL generation failed: {exc}",
                target=target,
                metadata=metadata,
            )

        metadata["signed_url_generated"] = signed_url is not None
        return IntegrationProbeComponent(
            ok=True,
            backend=self.settings.storage_backend,
            detail="Object store write/read probe succeeded",
            target=target,
            metadata=metadata,
        )


@lru_cache(maxsize=1)
def get_service() -> PaperbaseService:
    settings = get_settings()
    repository = build_repository(settings)
    object_store = build_object_store(settings)
    service = PaperbaseService(settings=settings, repository=repository, object_store=object_store)
    service.init()
    return service
