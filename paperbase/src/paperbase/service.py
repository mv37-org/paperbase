"""Application service that orchestrates indexing, search, and artifact retrieval."""

from __future__ import annotations

import json
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
    TableResponse,
)
from .repository import PaperRepository, build_repository
from .storage import ObjectStore, StoredObject, build_object_store
from . import sources
from .utils import canonical_paper_id, new_id, utcnow


class OptionalEmbeddingProvider:
    def __init__(self, settings: Settings) -> None:
        self._model_name = settings.embedding_model
        self._model = None

    def encode(self, text: str) -> list[float] | None:
        if not self._model_name:
            return None
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except Exception:
                return None
            self._model = SentenceTransformer(self._model_name)
        vector = self._model.encode([text], normalize_embeddings=True)[0]
        return [float(value) for value in vector.tolist()]


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

    def init(self) -> None:
        self.repository.init()

    def enqueue_index_job(self, arxiv_id: str, *, force: bool = False) -> JobResponse:
        return self.repository.create_job("index_paper", {"arxiv_id": arxiv_id, "force": force})

    def enqueue_latest_sync_job(self) -> JobResponse:
        return self.repository.create_job(
            "sync_latest",
            {
                "days": self.settings.latest_sync_days,
                "categories": self.settings.arxiv_categories,
                "limit_per_category": self.settings.latest_sync_limit_per_category,
            },
        )

    def process_next_job(self) -> JobResponse | None:
        job = self.repository.claim_pending_job()
        if job is None:
            return None
        try:
            if job.job_type == "index_paper":
                self.index_arxiv_paper(job.payload["arxiv_id"], force=bool(job.payload.get("force")))
            elif job.job_type == "sync_latest":
                self.sync_latest_papers(
                    days=int(job.payload.get("days", self.settings.latest_sync_days)),
                    categories=list(job.payload.get("categories", self.settings.arxiv_categories)),
                    limit_per_category=int(job.payload.get("limit_per_category", self.settings.latest_sync_limit_per_category)),
                )
            else:
                raise ValueError(f"Unsupported job type: {job.job_type}")
            self.repository.mark_job_completed(job.job_id)
        except Exception as exc:  # pragma: no cover - surfaced via job state and API
            self.repository.mark_job_failed(job.job_id, str(exc))
        return self.repository.get_job(job.job_id)

    def sync_latest_papers(self, *, days: int, categories: list[str], limit_per_category: int) -> list[PaperResponse]:
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
        return indexed

    def index_arxiv_paper(self, arxiv_id: str, *, paper: PaperMetadata | None = None, force: bool = False) -> PaperResponse:
        existing = self.repository.get_paper_by_arxiv_id(arxiv_id)
        if existing and existing.index_status == "indexed" and not force:
            return existing

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
        search_document = extraction.text_markdown
        embedding = self.embeddings.encode(f"{metadata.title}\n\n{metadata.abstract}") if self.embeddings else None
        stored = self.repository.upsert_paper(metadata, search_document=search_document, embedding=embedding)

        prefix = f"papers/{stored.paper_id}"
        self.object_store.put_text(f"{prefix}/meta.json", json.dumps(metadata.model_dump(mode='json'), indent=2), content_type="application/json")
        self.object_store.put_text(f"{prefix}/text/full.md", extraction.text_markdown, content_type="text/markdown")

        text_chunks: list[dict[str, str]] = [
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

        for artifact in extraction.raw_artifacts:
            self.object_store.put_bytes(f"{prefix}/{artifact.path}", artifact.data, content_type=artifact.content_type)

        manifest = build_manifest(stored.paper_id, metadata, extraction)
        self.object_store.put_text(
            f"{prefix}/manifest.json",
            json.dumps(manifest, indent=2),
            content_type="application/json",
        )

        self.repository.replace_text_chunks(stored.paper_id, text_chunks)
        return stored

    def search(self, request: SearchRequest) -> SearchResponse:
        local_results = self.repository.search_local(
            request.query,
            limit=request.limit * 2,
            query_embedding=self.embeddings.encode(request.query) if self.embeddings else None,
        )
        merged: dict[str, SearchPaperResult] = {result.paper_id: result for result in local_results}

        if request.include_live_sources:
            arxiv_results = sources.search_arxiv(request.query, max_results=request.limit, category=request.category)
            semantic_results = sources.search_semantic_scholar(request.query, limit=request.limit, year=request.year)
            live_papers = arxiv_results + semantic_results
            sources.enrich_with_semantic_scholar(live_papers)
            for index, paper in enumerate(live_papers):
                stored = self.repository.upsert_paper(paper, search_document=paper.abstract)
                score = 1.0 / (index + 1)
                result = SearchPaperResult(**stored.model_dump(), score=score, source="live")
                current = merged.get(stored.paper_id)
                if current is None or (current.score or 0) < score:
                    merged[stored.paper_id] = result

        ordered = sorted(
            merged.values(),
            key=lambda paper: ((paper.score or 0), paper.citation_count, paper.published),
            reverse=True,
        )[: request.limit]
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
        return paper

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
        target = self.settings.gcs_bucket if self.settings.storage_backend == "gcs" else self.settings.storage_backend
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
