"""Application service that orchestrates indexing, search, and artifact retrieval."""

from __future__ import annotations

import json
import logging
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


class OptionalEmbeddingProvider:
    def __init__(self, settings: Settings) -> None:
        self._model_name = settings.embedding_model
        self._expected_dim = settings.embedding_dim

    def _model(self):
        if not self._model_name:
            return None
        cached = _MODEL_CACHE.get(self._model_name)
        if cached is not None:
            return cached
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:
            logger.warning(
                "sentence-transformers unavailable, embeddings disabled",
                extra={"event": "embed.unavailable", "error": str(exc)},
            )
            return None
        try:
            model = SentenceTransformer(self._model_name)
        except Exception as exc:
            logger.warning(
                "Failed to load embedding model, embeddings disabled",
                extra={"event": "embed.load_failed", "model": self._model_name, "error": str(exc)},
            )
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
            vectors = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        except Exception as exc:
            logger.warning("Embedding encode failed", extra={"event": "embed.encode_failed", "error": str(exc)})
            return None
        return [[float(v) for v in vector.tolist()] for vector in vectors]


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
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from .summaries import generate_summary_on_demand

        if self.settings.summary_provider != "vertex":
            logger.warning(
                "summarize_top_cited invoked but summary_provider is not 'vertex'; nothing to do",
                extra={"event": "summarize_top_cited.noop", "provider": self.settings.summary_provider},
            )
            return {"processed": 0, "updated": 0, "failed": 0}

        papers = self.repository.list_papers_needing_summary(limit=limit)
        logger.info(
            "Starting summarize_top_cited",
            extra={
                "event": "summarize_top_cited.start",
                "candidate_count": len(papers),
                "concurrency": self.settings.featured_tier_concurrency,
            },
        )

        updated = 0
        failed = 0
        concurrency = max(1, self.settings.featured_tier_concurrency)

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

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(_summarize_one, paper) for paper in papers]
            for index, future in enumerate(as_completed(futures), start=1):
                paper_id, result = future.result()
                if result is None:
                    failed += 1
                    continue
                short, eli5 = result
                if self.repository.update_summaries(paper_id, summary_short=short, summary_eli5=eli5) is not None:
                    updated += 1
                if index % 500 == 0:
                    logger.info(
                        "summarize_top_cited progress",
                        extra={
                            "event": "summarize_top_cited.progress",
                            "processed": index,
                            "updated": updated,
                            "failed": failed,
                        },
                    )

        logger.info(
            "Completed summarize_top_cited",
            extra={
                "event": "summarize_top_cited.completed",
                "processed": len(papers),
                "updated": updated,
                "failed": failed,
            },
        )
        return {"processed": len(papers), "updated": updated, "failed": failed}

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

        paper_embedding_input = "\n".join(
            part
            for part in (
                metadata.title,
                metadata.tldr or metadata.abstract,
                metadata.summary_short,
            )
            if part
        )
        paper_embedding = self.embeddings.encode(paper_embedding_input) if paper_embedding_input else None

        stored = self.repository.upsert_paper(metadata, search_document=search_document, embedding=paper_embedding)

        prefix = f"papers/{stored.paper_id}"
        self.object_store.delete_prefix(prefix)
        self.object_store.put_text(f"{prefix}/meta.json", json.dumps(metadata.model_dump(mode='json'), indent=2), content_type="application/json")
        self.object_store.put_text(f"{prefix}/text/full.md", extraction.text_markdown, content_type="text/markdown")

        text_chunks: list[dict[str, Any]] = [
            {"path": "text/full.md", "chunk_kind": "full_text", "content": extraction.text_markdown}
        ]

        for section in extraction.sections:
            section_path = f"{prefix}/sections/{section.slug}.md"
            self.object_store.put_text(section_path, section.markdown, content_type="text/markdown")
            text_chunks.append(
                {
                    "path": f"sections/{section.slug}.md",
                    "chunk_kind": "section",
                    "content": section.markdown,
                }
            )

        if extraction.figures:
            self.object_store.put_text(
                f"{prefix}/figures/index.json",
                json.dumps(figure_index_payload(extraction.figures), indent=2),
                content_type="application/json",
            )
            for figure in extraction.figures:
                caption_path = f"{prefix}/figures/{figure.figure_id}/caption.md"
                self.object_store.put_text(caption_path, figure.caption, content_type="text/markdown")
                text_chunks.append(
                    {
                        "path": f"figures/{figure.figure_id}/caption.md",
                        "chunk_kind": "figure_caption",
                        "content": figure.caption,
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
            for table in extraction.tables:
                json_path = f"{prefix}/tables/{table.table_id}.json"
                csv_path = f"{prefix}/tables/{table.table_id}.csv"
                self.object_store.put_bytes(json_path, table_json_bytes(table), content_type="application/json")
                self.object_store.put_bytes(csv_path, table_csv_bytes(table), content_type="text/csv")
                if table.caption:
                    text_chunks.append(
                        {
                            "path": f"tables/{table.table_id}.json",
                            "chunk_kind": "table",
                            "content": table.caption,
                        }
                    )

        chunk_texts = [(chunk["content"] or "")[:2000] for chunk in text_chunks]
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

        for rank, paper in enumerate(arxiv_results):
            stored = self.repository.upsert_paper(paper, search_document=paper.abstract)
            pid = stored.paper_id
            candidates.setdefault(pid, SearchPaperResult(**stored.model_dump(), source="live-arxiv"))
            rrf[pid] = rrf.get(pid, 0.0) + 0.8 / (k + rank)

        for rank, paper in enumerate(s2_results):
            stored = self.repository.upsert_paper(
                paper,
                search_document=paper.tldr or paper.abstract,
            )
            pid = stored.paper_id
            candidates.setdefault(pid, SearchPaperResult(**stored.model_dump(), source="live-s2", tldr=paper.tldr or None))
            rrf[pid] = rrf.get(pid, 0.0) + 0.8 / (k + rank)

        from math import log10

        for pid, paper in candidates.items():
            citation_boost = 0.05 * log10(1 + (paper.citation_count or 0))
            rrf[pid] = rrf.get(pid, 0.0) + citation_boost

        fused = sorted(rrf.items(), key=lambda item: item[1], reverse=True)
        fused_papers = [candidates[pid] for pid, _ in fused if pid in candidates]

        if self.reranker.enabled():
            rerank_head = fused_papers[: self.reranker.top_k]
            rerank_tail = fused_papers[self.reranker.top_k :]
            doc_texts = [
                " ".join(part for part in (p.title, p.tldr or p.summary_short or p.abstract) if part)[:2000]
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

    def get_related_papers(self, paper_id: str) -> list[SearchPaperResult]:
        paper = self.get_paper(paper_id)
        related = sources.get_related_papers(paper.arxiv_id, paper.semantic_scholar_id)
        papers: list[SearchPaperResult] = []
        for item in related:
            stored = self.repository.upsert_paper(item, search_document=item.abstract)
            papers.append(SearchPaperResult(**stored.model_dump(), source="related"))
        return papers

    def get_job(self, job_id: str) -> JobResponse:
        job = self.repository.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        return job

    def cleanup_expired_results(self) -> None:
        self.repository.cleanup_expired_results()

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
