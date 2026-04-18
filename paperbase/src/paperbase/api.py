"""FastAPI application for the hosted paperbase service."""

from __future__ import annotations

import os

from fastapi import Depends, FastAPI, Query

from .auth import require_bearer_token
from .models import (
    FileContentResponse,
    FileListResponse,
    GrepRequest,
    GrepResponse,
    JobResponse,
    ManifestResponse,
    PaperResponse,
    ResultSetResponse,
    SearchPaperResult,
    SearchRequest,
    SearchResponse,
    TableResponse,
    FigureResponse,
)
from .service import PaperbaseService, get_service

app = FastAPI(
    title="paperbase",
    version="0.1.0",
    description="Hosted backend for indexing and serving scientific paper artifacts",
)


def _resolve_bind_address() -> tuple[str, int]:
    host = os.getenv("PAPERBASE_API_HOST", "0.0.0.0")
    port = int(os.getenv("PAPERBASE_API_PORT") or os.getenv("PORT") or "8080")
    return host, port


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/index/papers/arxiv/{arxiv_id}", response_model=JobResponse, dependencies=[Depends(require_bearer_token)])
def index_paper(
    arxiv_id: str,
    force: bool = Query(default=False),
    service: PaperbaseService = Depends(get_service),
) -> JobResponse:
    return service.enqueue_index_job(arxiv_id, force=force)


@app.post("/v1/index/sync/latest", response_model=JobResponse, dependencies=[Depends(require_bearer_token)])
def sync_latest(service: PaperbaseService = Depends(get_service)) -> JobResponse:
    return service.enqueue_latest_sync_job()


@app.get("/v1/papers/{paper_id}", response_model=PaperResponse, dependencies=[Depends(require_bearer_token)])
def get_paper(paper_id: str, service: PaperbaseService = Depends(get_service)) -> PaperResponse:
    return service.get_paper(paper_id)


@app.get("/v1/papers/{paper_id}/manifest", response_model=ManifestResponse, dependencies=[Depends(require_bearer_token)])
def get_manifest(paper_id: str, service: PaperbaseService = Depends(get_service)) -> ManifestResponse:
    return service.get_manifest(paper_id)


@app.get("/v1/papers/{paper_id}/files", response_model=FileListResponse, dependencies=[Depends(require_bearer_token)])
def list_files(paper_id: str, service: PaperbaseService = Depends(get_service)) -> FileListResponse:
    return service.list_files(paper_id)


@app.get("/v1/papers/{paper_id}/files/{path:path}", response_model=FileContentResponse, dependencies=[Depends(require_bearer_token)])
def read_file(paper_id: str, path: str, service: PaperbaseService = Depends(get_service)) -> FileContentResponse:
    return service.read_file(paper_id, path)


@app.get("/v1/papers/{paper_id}/figures/{figure_id}", response_model=FigureResponse, dependencies=[Depends(require_bearer_token)])
def get_figure(paper_id: str, figure_id: str, service: PaperbaseService = Depends(get_service)) -> FigureResponse:
    return service.get_figure(paper_id, figure_id)


@app.get("/v1/papers/{paper_id}/tables/{table_id}", response_model=TableResponse, dependencies=[Depends(require_bearer_token)])
def get_table(paper_id: str, table_id: str, service: PaperbaseService = Depends(get_service)) -> TableResponse:
    return service.get_table(paper_id, table_id)


@app.post("/v1/search", response_model=SearchResponse, dependencies=[Depends(require_bearer_token)])
def search(request: SearchRequest, service: PaperbaseService = Depends(get_service)) -> SearchResponse:
    return service.search(request)


@app.get("/v1/results/{result_set_id}", response_model=ResultSetResponse, dependencies=[Depends(require_bearer_token)])
def get_results(result_set_id: str, service: PaperbaseService = Depends(get_service)) -> ResultSetResponse:
    return service.get_results(result_set_id)


@app.post("/v1/results/{result_set_id}/grep", response_model=GrepResponse, dependencies=[Depends(require_bearer_token)])
def grep_results(
    result_set_id: str,
    request: GrepRequest,
    service: PaperbaseService = Depends(get_service),
) -> GrepResponse:
    return service.grep_results(result_set_id, request.pattern, limit=request.limit)


@app.get("/v1/papers/{paper_id}/related", response_model=list[SearchPaperResult], dependencies=[Depends(require_bearer_token)])
def related(paper_id: str, service: PaperbaseService = Depends(get_service)) -> list[SearchPaperResult]:
    return service.get_related_papers(paper_id)


@app.get("/v1/jobs/{job_id}", response_model=JobResponse, dependencies=[Depends(require_bearer_token)])
def get_job(job_id: str, service: PaperbaseService = Depends(get_service)) -> JobResponse:
    return service.get_job(job_id)


def main() -> None:
    import uvicorn

    host, port = _resolve_bind_address()
    uvicorn.run("paperbase.api:app", host=host, port=port, reload=False)
