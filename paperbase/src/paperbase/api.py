"""FastAPI application for the hosted paperbase service."""

from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from authlib.integrations.starlette_client import OAuth
from fastapi.encoders import jsonable_encoder
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel as _BaseModel, Field as _Field
from starlette.middleware.sessions import SessionMiddleware

from .auth import (
    clear_browser_session_cookie,
    require_bearer_token,
    require_browser_admin_user,
    require_browser_user,
    require_browser_write_admin_user,
    require_browser_write_user,
    set_browser_session_cookie,
    set_no_store_headers,
)
from .config import get_settings
from .logging_utils import build_log_config
from .models import (
    AdminJobPapersResponse,
    AdminJobsOverviewResponse,
    BrowserSessionResponse,
    ChatThreadListResponse,
    ChatThreadResponse,
    CodeContextResponse,
    CodeFileListResponse,
    CodeReadResponse,
    CodeSearchResponse,
    CreateProjectRequest,
    CreateChatThreadRequest,
    FileContentResponse,
    FileListResponse,
    ForgotPasswordRequest,
    GitHubRepoImportCandidateResponse,
    GitHubRepoListResponse,
    GitHubRepoMetadataResponse,
    GrepRequest,
    GrepResponse,
    IntegrationProbeResponse,
    JobResponse,
    LoginRequest,
    ManifestResponse,
    MoveChatThreadProjectRequest,
    PaperAssetResponse,
    PaperPreviewResponse,
    PaperResponse,
    ProjectItemPageResponse,
    ProjectRepoSyncRequest,
    ProjectRepoSyncResponse,
    ProjectListResponse,
    ProjectResponse,
    ProjectSessionPageResponse,
    PythonFileResponse,
    RunPythonFileRequest,
    ResetPasswordRequest,
    ResultSetResponse,
    RunPythonFileResponse,
    SaveOpenAIKeyRequest,
    SaveModalCredentialsRequest,
    SearchClickRequest,
    SearchPaperResult,
    SearchRequest,
    SearchResponse,
    SearchSectionRequest,
    SearchSectionResponse,
    SendChatMessageRequest,
    SendChatMessageResponse,
    SignupRequest,
    TableResponse,
    UpdateAppearanceRequest,
    UpdatePythonFileRequest,
    UpdateNotificationsRequest,
    UpdateProjectRequest,
    FigureResponse,
    UploadListResponse,
    UploadedDocumentResponse,
)
from .service import PaperbaseService, get_service
from .security import csrf_token_for_session

app = FastAPI(
    title="paperbase",
    version="0.1.0",
    description="Hosted backend for indexing and serving scientific paper artifacts",
)
_app_settings = get_settings()
_PACKAGE_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _PACKAGE_DIR / "static"


def _env_flag(name: str) -> bool:
    value = os.getenv(name, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _allowed_browser_origins() -> list[str]:
    origins: list[str] = []
    for value in (_app_settings.browser_origin, _app_settings.chat_public_url):
        if value and value not in origins:
            origins.append(value)
    return origins


app.add_middleware(
    SessionMiddleware,
    secret_key=_app_settings.app_secret,
    same_site="lax",
    https_only=_app_settings.browser_session_secure,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_browser_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=_STATIC_DIR, check_dir=False), name="static")


def _resolve_bind_address() -> tuple[str, int]:
    host = os.getenv("PAPERBASE_API_HOST", "0.0.0.0")
    port = int(os.getenv("PAPERBASE_API_PORT") or os.getenv("PORT") or "8080")
    return host, port


def _parse_tags(raw_tags: str) -> list[str]:
    values: list[str] = []
    for item in raw_tags.replace("\n", ",").split(","):
        cleaned = item.strip()
        if cleaned:
            values.append(cleaned)
    return values


def _browser_session_token(request: Request) -> str | None:
    settings = get_settings()
    return request.cookies.get(settings.browser_session_cookie_name)


def _forwarded_header_value(value: str | None) -> str | None:
    if not value:
        return None
    first = value.split(",", 1)[0].strip().strip('"')
    return first or None


def _forwarded_header_param(value: str | None, key: str) -> str | None:
    if not value:
        return None
    first = value.split(",", 1)[0]
    for item in first.split(";"):
        name, sep, raw_value = item.partition("=")
        if sep and name.strip().lower() == key:
            parsed = raw_value.strip().strip('"')
            return parsed or None
    return None


def _public_base_url(request: Request) -> str:
    forwarded = request.headers.get("forwarded")
    proto = _forwarded_header_param(forwarded, "proto") or _forwarded_header_value(
        request.headers.get("x-forwarded-proto")
    )
    host = (
        _forwarded_header_param(forwarded, "host")
        or _forwarded_header_value(request.headers.get("x-forwarded-host"))
        or request.headers.get("host")
        or urlsplit(str(request.base_url)).netloc
    )
    if not proto or not host:
        return str(request.base_url).rstrip("/")
    port = _forwarded_header_value(request.headers.get("x-forwarded-port"))
    if port and ":" not in host and port not in {"80", "443"}:
        host = f"{host}:{port}"
    return f"{proto.lower()}://{host}".rstrip("/")


def _is_safe_return_to(value: str | None) -> bool:
    if not value:
        return False
    parts = urlsplit(value)
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def _chat_redirect_base(request: Request, settings, *, return_to: str | None = None) -> str:
    if _is_safe_return_to(return_to):
        return return_to.rstrip("/")
    for session_key in ("browser_google_return_to", "browser_github_return_to"):
        session_return_to = request.session.pop(session_key, None)
        if _is_safe_return_to(session_return_to):
            return session_return_to.rstrip("/")
    referer = request.headers.get("referer")
    if _is_safe_return_to(referer):
        parts = urlsplit(referer)
        return f"{parts.scheme}://{parts.netloc}"
    return settings.chat_public_url.rstrip("/")


def _sse_event(event_name: str, payload: object) -> str:
    return f"event: {event_name}\ndata: {json.dumps(jsonable_encoder(payload), ensure_ascii=False)}\n\n"


def _suggestion_response(service: PaperbaseService, q: str, *, limit: int) -> dict[str, object]:
    suggestions = service.suggest_papers(q, limit=limit)
    fallback = False
    if not suggestions:
        suggestions = service.suggest_fallback_papers(limit=min(limit, 6))
        fallback = True
    return {"suggestions": suggestions, "fallback": fallback}


def _retry_job_or_404(service: PaperbaseService, job_id: str) -> JobResponse:
    result = service.retry_dead_job(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} is not in a retryable state")
    return result


def _enqueue_harvest_range_request(
    request: "HarvestRangeRequest",
    service: PaperbaseService,
) -> JobResponse:
    from .errors import PermanentError

    categories = request.categories or list(get_settings().arxiv_categories)
    try:
        return service.enqueue_harvest_range_job(
            categories=categories,
            start_date=request.start_date,
            end_date=request.end_date,
            limit_per_window=request.limit_per_window,
            chunk_months=request.chunk_months,
            source=request.source,
        )
    except PermanentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _enqueue_harvest_top_cited_request(
    request: "HarvestTopCitedRequest",
    service: PaperbaseService,
) -> JobResponse:
    from .errors import PermanentError

    try:
        return service.enqueue_harvest_top_cited_job(
            min_citations=request.min_citations,
            max_papers=request.max_papers,
            chunk_size=request.chunk_size,
        )
    except PermanentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _enqueue_harvest_venue_request(
    request: "HarvestVenueRequest",
    service: PaperbaseService,
) -> JobResponse:
    from .errors import PermanentError

    try:
        return service.enqueue_harvest_venue_job(
            venue=request.venue,
            year=request.year,
            max_papers=request.max_papers,
            chunk_size=request.chunk_size,
        )
    except PermanentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@lru_cache(maxsize=1)
def get_google_oauth_client():
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        return None
    oauth = OAuth()
    oauth.register(
        name="google",
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth.create_client("google")


@lru_cache(maxsize=1)
def get_github_oauth_client():
    settings = get_settings()
    if not settings.github_client_id or not settings.github_client_secret:
        return None
    oauth = OAuth()
    oauth.register(
        name="github",
        access_token_url="https://github.com/login/oauth/access_token",
        authorize_url="https://github.com/login/oauth/authorize",
        api_base_url="https://api.github.com/",
        client_id=settings.github_client_id,
        client_secret=settings.github_client_secret,
        client_kwargs={"scope": "repo read:user user:email"},
    )
    return oauth.create_client("github")


def _browser_session_response_with_csrf(
    session_response: BrowserSessionResponse,
    *,
    request: Request,
    response: Response,
) -> BrowserSessionResponse:
    raw_token = _browser_session_token(request)
    if session_response.authenticated and raw_token:
        session_response.csrf_token = csrf_token_for_session(raw_token, get_settings())
    set_no_store_headers(response)
    return session_response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    if not _STATIC_DIR.exists():
        return HTMLResponse("Chat frontend not built. Run `cd chat && vp build`.", status_code=503)
    html = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
    api_base_url = _public_base_url(request)
    html = html.replace("__PAPERBASE_BASE_URL__", api_base_url)
    response = HTMLResponse(html)
    set_no_store_headers(response)
    return response


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


class HarvestRangeRequest(_BaseModel):
    # When omitted/empty, the API defaults to the configured AI/ML arxiv
    # categories (per the product scope — see issue #33 comment). Callers
    # can still pass an explicit subset to harvest a narrower slice.
    categories: list[str] = _Field(default_factory=list)
    start_date: str = _Field(min_length=10, max_length=10)
    end_date: str = _Field(min_length=10, max_length=10)
    # Per-(category, window) cap — NOT a per-category total across the
    # whole date range. A 2-year range with ``chunk_months=3`` is 8
    # windows, so ``limit_per_window=1000`` means each category can
    # ingest up to ``8 * 1000 = 8000`` papers. The arg is named after
    # the chunk so operators don't read it as a category-wide budget.
    limit_per_window: int | None = _Field(default=None, ge=1, le=10_000_000)
    chunk_months: int = _Field(default=3, ge=1, le=24)
    source: str = "arxiv"


@app.post("/v1/index/harvest", response_model=JobResponse, dependencies=[Depends(require_bearer_token)])
def harvest_range(
    request: HarvestRangeRequest,
    service: PaperbaseService = Depends(get_service),
) -> JobResponse:
    """Queue a ``harvest_range`` planner job. The planner splits
    ``[start_date, end_date]`` into ``chunk_months``-wide windows per
    category and enqueues one ``harvest_chunk`` per window. Chunks run at
    lower priority than interactive work so a large backfill does not
    starve on-demand indexing."""
    return _enqueue_harvest_range_request(request, service)


class HarvestTopCitedRequest(_BaseModel):
    # Citation floor on Semantic Scholar's bulk search. From measurement:
    # ≥100 cs cites yields ~106k arxiv-linked papers; ≥25 yields ~470k.
    min_citations: int = _Field(default=100, ge=0, le=1_000_000)
    # Stop after this many candidates; ``None`` means "until S2 is exhausted".
    max_papers: int | None = _Field(default=None, ge=1, le=10_000_000)
    # arxiv_ids per harvest_chunk; smaller chunks let interactive work
    # cut in faster, larger chunks cut planner overhead. 50 is a balance.
    chunk_size: int = _Field(default=50, ge=1, le=500)


@app.post(
    "/v1/index/harvest-top-cited",
    response_model=JobResponse,
    dependencies=[Depends(require_bearer_token)],
)
def harvest_top_cited(
    request: HarvestTopCitedRequest,
    service: PaperbaseService = Depends(get_service),
) -> JobResponse:
    """Queue a ``harvest_top_cited`` planner job. The planner streams
    Semantic Scholar's bulk search ordered by citationCount desc,
    filters to arxiv-linked papers, and enqueues ``harvest_chunk`` jobs
    (``source='s2_top_cited'``) in fixed-size arxiv-id batches. Chunks
    run at the same low priority as ``harvest_range`` so an on-demand
    ``index_paper`` always cuts in front."""
    return _enqueue_harvest_top_cited_request(request, service)


class HarvestVenueRequest(_BaseModel):
    # Conference venue name; S2 accepts both short ("ICLR") and long
    # ("International Conference on Learning Representations") forms.
    venue: str = _Field(..., min_length=1, max_length=200)
    # 4-digit conference year.
    year: int = _Field(..., ge=1900, le=2100)
    # Stop after this many candidates; ``None`` means "until S2 exhausts".
    max_papers: int | None = _Field(default=None, ge=1, le=10_000_000)
    # arxiv_ids per harvest_chunk; smaller = finer interactive priority.
    chunk_size: int = _Field(default=50, ge=1, le=500)


@app.post(
    "/v1/index/harvest-venue",
    response_model=JobResponse,
    dependencies=[Depends(require_bearer_token)],
)
def harvest_venue(
    request: HarvestVenueRequest,
    service: PaperbaseService = Depends(get_service),
) -> JobResponse:
    """Queue a ``harvest_venue`` planner job (#39 PR B). Pages
    Semantic Scholar's bulk search filtered by venue + year, enqueues
    ``harvest_chunk`` jobs (``source='s2_venue'``); the chunk worker
    indexes each paper and stamps the ``venue`` columns."""
    return _enqueue_harvest_venue_request(request, service)


@app.post("/v1/index/summarize-top-cited", response_model=JobResponse, dependencies=[Depends(require_bearer_token)])
def summarize_top_cited(
    limit: int = Query(default=500_000, ge=1, le=5_000_000),
    service: PaperbaseService = Depends(get_service),
) -> JobResponse:
    return service.enqueue_summarize_top_cited_job(limit=limit)


@app.post("/v1/index/reindex-embeddings", response_model=JobResponse, dependencies=[Depends(require_bearer_token)])
def reindex_embeddings(
    limit: int = Query(default=100_000, ge=1, le=5_000_000),
    include_chunks: bool = Query(default=True),
    rewrite_existing: bool = Query(default=True),
    service: PaperbaseService = Depends(get_service),
) -> JobResponse:
    return service.enqueue_reindex_embeddings_job(
        limit=limit,
        include_chunks=include_chunks,
        rewrite_existing=rewrite_existing,
    )


@app.post(
    "/v1/index/enrich-missing-metadata",
    response_model=JobResponse,
    dependencies=[Depends(require_bearer_token)],
)
def enrich_missing_metadata(
    limit: int = Query(default=50_000, ge=1, le=5_000_000),
    min_age_seconds: int = Query(default=3600, ge=0, le=30 * 24 * 3600),
    service: PaperbaseService = Depends(get_service),
) -> JobResponse:
    """Queue an ``enrich_missing_metadata`` job (#38). The worker picks
    up the job and re-runs Semantic Scholar enrichment for any indexed
    paper that inline enrichment left unfilled (usually due to transient
    429s). Asynchronous so a backfill at corpus scale doesn't tie up
    the serving container."""
    return service.enqueue_enrich_missing_metadata_job(
        limit=limit,
        min_age_seconds=min_age_seconds,
    )


@app.post("/v1/query/expand", dependencies=[Depends(require_bearer_token)])
def expand_query(
    query: str = Query(..., min_length=1),
    variants: int = Query(default=4, ge=1, le=8),
    service: PaperbaseService = Depends(get_service),
) -> dict[str, list[str]]:
    return {"queries": service.expand_query(query, variants=variants)}


@app.get("/v1/papers/suggest", dependencies=[Depends(require_bearer_token)])
def suggest_papers(
    q: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(default=10, ge=1, le=50),
    service: PaperbaseService = Depends(get_service),
) -> dict[str, object]:
    return _suggestion_response(service, q, limit=limit)


@app.get("/v1/stats", dependencies=[Depends(require_bearer_token)])
def stats(service: PaperbaseService = Depends(get_service)) -> dict:
    return service.get_stats()


@app.get("/v1/papers/{paper_id}", response_model=PaperResponse, dependencies=[Depends(require_bearer_token)])
def get_paper(paper_id: str, service: PaperbaseService = Depends(get_service)) -> PaperResponse:
    return service.get_paper(paper_id)


@app.get(
    "/v1/papers/{paper_id}/manifest", response_model=ManifestResponse, dependencies=[Depends(require_bearer_token)]
)
def get_manifest(paper_id: str, service: PaperbaseService = Depends(get_service)) -> ManifestResponse:
    return service.get_manifest(paper_id)


@app.get("/v1/papers/{paper_id}/files", response_model=FileListResponse, dependencies=[Depends(require_bearer_token)])
def list_files(paper_id: str, service: PaperbaseService = Depends(get_service)) -> FileListResponse:
    return service.list_files(paper_id)


@app.get(
    "/v1/papers/{paper_id}/files/{path:path}",
    response_model=FileContentResponse,
    dependencies=[Depends(require_bearer_token)],
)
def read_file(paper_id: str, path: str, service: PaperbaseService = Depends(get_service)) -> FileContentResponse:
    return service.read_file(paper_id, path)


@app.get(
    "/v1/papers/{paper_id}/figures/{figure_id}",
    response_model=FigureResponse,
    dependencies=[Depends(require_bearer_token)],
)
def get_figure(paper_id: str, figure_id: str, service: PaperbaseService = Depends(get_service)) -> FigureResponse:
    return service.get_figure(paper_id, figure_id)


@app.get(
    "/v1/papers/{paper_id}/tables/{table_id}",
    response_model=TableResponse,
    dependencies=[Depends(require_bearer_token)],
)
def get_table(paper_id: str, table_id: str, service: PaperbaseService = Depends(get_service)) -> TableResponse:
    return service.get_table(paper_id, table_id)


# --------------------------------------------------------------------------- #
# Paper-code retrieval (#5 PR 2). All four endpoints are paper-scoped and
# read-only; the service layer gates on ``code_is_python`` and surfaces a
# structured ``eligibility`` object rather than silently searching.
# --------------------------------------------------------------------------- #


@app.get(
    "/v1/papers/{paper_id}/code/files",
    response_model=CodeFileListResponse,
    dependencies=[Depends(require_bearer_token)],
)
def list_code_files(paper_id: str, service: PaperbaseService = Depends(get_service)) -> CodeFileListResponse:
    response = service.list_paper_code_files(paper_id)
    if response is None:
        raise HTTPException(status_code=404, detail=f"Paper {paper_id} not found")
    return response


@app.get(
    "/v1/papers/{paper_id}/code/files/{repo_path:path}",
    response_model=CodeReadResponse,
    dependencies=[Depends(require_bearer_token)],
)
def read_code_file(
    paper_id: str, repo_path: str, service: PaperbaseService = Depends(get_service)
) -> CodeReadResponse:
    response = service.read_paper_code_file(paper_id, repo_path)
    if response is None:
        raise HTTPException(status_code=404, detail=f"Paper {paper_id} not found")
    return response


@app.get(
    "/v1/papers/{paper_id}/code/search",
    response_model=CodeSearchResponse,
    dependencies=[Depends(require_bearer_token)],
)
def search_code(
    paper_id: str,
    q: str = "",
    limit: int = 10,
    service: PaperbaseService = Depends(get_service),
) -> CodeSearchResponse:
    response = service.search_paper_code(paper_id, q, limit=limit)
    if response is None:
        raise HTTPException(status_code=404, detail=f"Paper {paper_id} not found")
    return response


@app.get(
    "/v1/papers/{paper_id}/code/context",
    response_model=CodeContextResponse,
    dependencies=[Depends(require_bearer_token)],
)
def code_context(
    paper_id: str,
    q: str = "",
    limit: int = 5,
    service: PaperbaseService = Depends(get_service),
) -> CodeContextResponse:
    response = service.get_paper_code_context(paper_id, q, limit=limit)
    if response is None:
        raise HTTPException(status_code=404, detail=f"Paper {paper_id} not found")
    return response


@app.post("/v1/search", response_model=SearchResponse, dependencies=[Depends(require_bearer_token)])
def search(request: SearchRequest, service: PaperbaseService = Depends(get_service)) -> SearchResponse:
    return service.search(request)


@app.post("/v1/search/sections", response_model=SearchSectionResponse, dependencies=[Depends(require_bearer_token)])
def search_sections(
    request: SearchSectionRequest, service: PaperbaseService = Depends(get_service)
) -> SearchSectionResponse:
    return service.search_sections(request)


@app.post(
    "/v1/search/{query_id}/click",
    status_code=204,
    dependencies=[Depends(require_bearer_token)],
)
def record_search_click(
    query_id: str,
    request: SearchClickRequest,
    service: PaperbaseService = Depends(get_service),
) -> None:
    """Attach a click event to a previously-logged search. Idempotent: the
    last click wins. 404 if the query_id is unknown (e.g. the search was
    made with log_query=False, or the row has already been aged out)."""
    if not service.record_search_click(query_id, request.paper_id):
        raise HTTPException(status_code=404, detail="Unknown query_id")
    return None


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


@app.get(
    "/v1/papers/{paper_id}/related",
    response_model=list[SearchPaperResult],
    dependencies=[Depends(require_bearer_token)],
)
def related(paper_id: str, service: PaperbaseService = Depends(get_service)) -> list[SearchPaperResult]:
    return service.get_related_papers(paper_id)


@app.get("/v1/papers/{paper_id}/references", dependencies=[Depends(require_bearer_token)])
def references(
    paper_id: str,
    refresh: bool = Query(default=False),
    service: PaperbaseService = Depends(get_service),
) -> list[dict]:
    return service.get_paper_references(paper_id, refresh=refresh)


@app.get("/v1/browser/papers/suggest")
def browser_suggest_papers(
    q: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(default=10, ge=1, le=50),
    service: PaperbaseService = Depends(get_service),
) -> dict[str, object]:
    return _suggestion_response(service, q, limit=limit)


@app.get("/v1/browser/papers/{paper_id}", response_model=PaperResponse)
def browser_get_paper(
    paper_id: str,
    user=Depends(require_browser_user),
    service: PaperbaseService = Depends(get_service),
) -> PaperResponse:
    return service.get_paper(paper_id)


@app.get("/v1/browser/papers/{paper_id}/files/{path:path}", response_model=FileContentResponse)
def browser_read_paper_file(
    paper_id: str,
    path: str,
    user=Depends(require_browser_user),
    service: PaperbaseService = Depends(get_service),
) -> FileContentResponse:
    return service.read_file(paper_id, path)


@app.get("/v1/browser/papers/{paper_id}/figures/{figure_id}", response_model=FigureResponse)
def browser_get_paper_figure(
    paper_id: str,
    figure_id: str,
    user=Depends(require_browser_user),
    service: PaperbaseService = Depends(get_service),
) -> FigureResponse:
    return service.get_figure(paper_id, figure_id)


@app.get("/v1/browser/papers/{paper_id}/preview", response_model=PaperPreviewResponse)
def browser_get_paper_preview(
    paper_id: str,
    response: Response,
    user=Depends(require_browser_user),
    service: PaperbaseService = Depends(get_service),
) -> PaperPreviewResponse:
    preview = service.get_paper_preview(paper_id)
    set_no_store_headers(response)
    return preview


@app.get("/v1/browser/papers/{paper_id}/preview/content")
def browser_get_paper_preview_content(
    paper_id: str,
    user=Depends(require_browser_user),
    service: PaperbaseService = Depends(get_service),
) -> Response:
    payload, content_type = service.get_paper_preview_content(paper_id)
    response = Response(content=payload, media_type=content_type)
    set_no_store_headers(response)
    return response


@app.get("/v1/browser/papers/{paper_id}/tables/{table_id}", response_model=TableResponse)
def browser_get_paper_table(
    paper_id: str,
    table_id: str,
    user=Depends(require_browser_user),
    service: PaperbaseService = Depends(get_service),
) -> TableResponse:
    return service.get_table(paper_id, table_id)


@app.get("/v1/browser/admin/jobs/overview", response_model=AdminJobsOverviewResponse)
def browser_admin_jobs_overview(
    limit: int = Query(default=50, ge=1, le=200),
    user=Depends(require_browser_admin_user),
    service: PaperbaseService = Depends(get_service),
) -> AdminJobsOverviewResponse:
    return service.get_admin_jobs_overview(limit=limit)


@app.get("/v1/browser/admin/jobs", response_model=list[JobResponse])
def browser_admin_list_jobs(
    status: str = Query(
        default="all",
        description="Job status to filter on (all|dead|failed|pending|running|completed).",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    user=Depends(require_browser_admin_user),
    service: PaperbaseService = Depends(get_service),
) -> list[JobResponse]:
    return service.list_jobs_by_status(status, limit=limit)


@app.get("/v1/browser/admin/jobs/{root_job_id}/papers", response_model=AdminJobPapersResponse)
def browser_admin_job_papers(
    root_job_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    user=Depends(require_browser_admin_user),
    service: PaperbaseService = Depends(get_service),
) -> AdminJobPapersResponse:
    return service.get_admin_job_papers(root_job_id, offset=offset, limit=limit)


@app.post("/v1/browser/admin/jobs/{job_id}/retry", response_model=JobResponse)
def browser_admin_retry_job(
    job_id: str,
    user=Depends(require_browser_write_admin_user),
    service: PaperbaseService = Depends(get_service),
) -> JobResponse:
    return _retry_job_or_404(service, job_id)


@app.post("/v1/browser/admin/index/papers/arxiv/{arxiv_id}", response_model=JobResponse)
def browser_admin_index_paper(
    arxiv_id: str,
    force: bool = Query(default=False),
    user=Depends(require_browser_write_admin_user),
    service: PaperbaseService = Depends(get_service),
) -> JobResponse:
    return service.enqueue_index_job(arxiv_id, force=force)


@app.post("/v1/browser/admin/index/harvest", response_model=JobResponse)
def browser_admin_harvest_range(
    request: HarvestRangeRequest,
    user=Depends(require_browser_write_admin_user),
    service: PaperbaseService = Depends(get_service),
) -> JobResponse:
    return _enqueue_harvest_range_request(request, service)


@app.post("/v1/browser/admin/index/harvest-top-cited", response_model=JobResponse)
def browser_admin_harvest_top_cited(
    request: HarvestTopCitedRequest,
    user=Depends(require_browser_write_admin_user),
    service: PaperbaseService = Depends(get_service),
) -> JobResponse:
    return _enqueue_harvest_top_cited_request(request, service)


@app.post("/v1/browser/admin/index/harvest-venue", response_model=JobResponse)
def browser_admin_harvest_venue(
    request: HarvestVenueRequest,
    user=Depends(require_browser_write_admin_user),
    service: PaperbaseService = Depends(get_service),
) -> JobResponse:
    return _enqueue_harvest_venue_request(request, service)


@app.post("/v1/browser/admin/index/enrich-missing-metadata", response_model=JobResponse)
def browser_admin_enrich_missing_metadata(
    limit: int = Query(default=50_000, ge=1, le=5_000_000),
    min_age_seconds: int = Query(default=3600, ge=0, le=30 * 24 * 3600),
    user=Depends(require_browser_write_admin_user),
    service: PaperbaseService = Depends(get_service),
) -> JobResponse:
    return service.enqueue_enrich_missing_metadata_job(
        limit=limit,
        min_age_seconds=min_age_seconds,
    )


@app.get("/v1/browser/admin/papers", response_model=list[PaperResponse])
def browser_admin_list_papers(
    q: str = Query(default="", max_length=200),
    index_status: str = Query(default="all"),
    limit: int = Query(default=50, ge=1, le=200),
    user=Depends(require_browser_admin_user),
    service: PaperbaseService = Depends(get_service),
) -> list[PaperResponse]:
    return service.list_papers(query=q or None, index_status=index_status, limit=limit)


@app.get("/v1/browser/admin/papers/{paper_id}/files", response_model=FileListResponse)
def browser_admin_list_paper_files(
    paper_id: str,
    user=Depends(require_browser_admin_user),
    service: PaperbaseService = Depends(get_service),
) -> FileListResponse:
    return service.list_files(paper_id)


@app.get("/v1/browser/admin/papers/{paper_id}/files/{path:path}", response_model=FileContentResponse)
def browser_admin_read_paper_file(
    paper_id: str,
    path: str,
    user=Depends(require_browser_admin_user),
    service: PaperbaseService = Depends(get_service),
) -> FileContentResponse:
    return service.read_file(paper_id, path)


@app.get("/v1/browser/admin/papers/{paper_id}/assets/{path:path}", response_model=PaperAssetResponse)
def browser_admin_get_paper_asset(
    paper_id: str,
    path: str,
    response: Response,
    user=Depends(require_browser_admin_user),
    service: PaperbaseService = Depends(get_service),
) -> PaperAssetResponse:
    asset = service.get_paper_asset(paper_id, path)
    set_no_store_headers(response)
    return asset


@app.get("/v1/browser/admin/papers/{paper_id}/asset-content/{path:path}")
def browser_admin_get_paper_asset_content(
    paper_id: str,
    path: str,
    user=Depends(require_browser_admin_user),
    service: PaperbaseService = Depends(get_service),
) -> Response:
    payload, content_type = service.get_paper_asset_content(paper_id, path)
    response = Response(content=payload, media_type=content_type)
    set_no_store_headers(response)
    return response


@app.get("/v1/jobs/{job_id}", response_model=JobResponse, dependencies=[Depends(require_bearer_token)])
def get_job(job_id: str, service: PaperbaseService = Depends(get_service)) -> JobResponse:
    return service.get_job(job_id)


@app.get(
    "/v1/jobs",
    response_model=list[JobResponse],
    dependencies=[Depends(require_bearer_token)],
)
def list_jobs(
    status: str = Query(
        default="dead",
        description="Job status to filter on (all|dead|failed|pending|running|completed).",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    service: PaperbaseService = Depends(get_service),
) -> list[JobResponse]:
    return service.list_jobs_by_status(status, limit=limit)


@app.post(
    "/v1/jobs/{job_id}/retry",
    response_model=JobResponse,
    dependencies=[Depends(require_bearer_token)],
)
def retry_job(
    job_id: str,
    service: PaperbaseService = Depends(get_service),
) -> JobResponse:
    """Promote a dead (or legacy-failed) job back to pending with its
    attempt counter reset. Returns 404 if the id isn't in a dead-letter
    state."""
    return _retry_job_or_404(service, job_id)


@app.post(
    "/v1/debug/integrations", response_model=IntegrationProbeResponse, dependencies=[Depends(require_bearer_token)]
)
def probe_integrations(
    response: Response, service: PaperbaseService = Depends(get_service)
) -> IntegrationProbeResponse:
    result = service.probe_integrations()
    if not result.ok:
        response.status_code = 503
    return result


@app.get("/v1/browser/session", response_model=BrowserSessionResponse)
def browser_session(
    request: Request, response: Response, service: PaperbaseService = Depends(get_service)
) -> BrowserSessionResponse:
    raw_token = _browser_session_token(request)
    session = service.get_browser_session(raw_token)
    if session.authenticated and raw_token:
        session.csrf_token = csrf_token_for_session(raw_token, get_settings())
    set_no_store_headers(response)
    return session


@app.post("/v1/browser/auth/signup", response_model=BrowserSessionResponse)
def browser_signup(
    request: SignupRequest,
    response: Response,
    service: PaperbaseService = Depends(get_service),
) -> BrowserSessionResponse:
    session_response, raw_token = service.register_browser_user(request)
    set_browser_session_cookie(response, raw_token, get_settings())
    session_response.csrf_token = csrf_token_for_session(raw_token, get_settings())
    set_no_store_headers(response)
    return session_response


@app.post("/v1/browser/auth/login", response_model=BrowserSessionResponse)
def browser_login(
    request: LoginRequest,
    response: Response,
    service: PaperbaseService = Depends(get_service),
) -> BrowserSessionResponse:
    session_response, raw_token = service.authenticate_browser_user(request)
    set_browser_session_cookie(response, raw_token, get_settings())
    session_response.csrf_token = csrf_token_for_session(raw_token, get_settings())
    set_no_store_headers(response)
    return session_response


@app.post("/v1/browser/auth/logout")
def browser_logout(
    request: Request,
    response: Response,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> dict[str, str]:
    service.logout_browser_session(_browser_session_token(request))
    clear_browser_session_cookie(response, get_settings())
    set_no_store_headers(response)
    return {"status": "ok"}


@app.post("/v1/browser/auth/forgot-password")
def browser_forgot_password(
    request: ForgotPasswordRequest,
    service: PaperbaseService = Depends(get_service),
) -> dict[str, str]:
    return service.request_password_reset(request)


@app.post("/v1/browser/auth/reset-password")
def browser_reset_password(
    request: ResetPasswordRequest,
    service: PaperbaseService = Depends(get_service),
) -> dict[str, str]:
    return service.reset_password(request)


@app.get("/v1/browser/auth/google/start")
async def browser_google_start(request: Request, return_to: str | None = Query(default=None)):
    client = get_google_oauth_client()
    settings = get_settings()
    redirect_base = _chat_redirect_base(request, settings, return_to=return_to)
    if _is_safe_return_to(return_to):
        request.session["browser_google_return_to"] = return_to
    if client is None:
        return RedirectResponse(f"{redirect_base}/?auth_error=google_not_configured", status_code=302)
    redirect_uri = settings.google_oauth_redirect_url or str(request.url_for("browser_google_callback"))
    return await client.authorize_redirect(request, redirect_uri)


@app.get("/v1/browser/auth/google/callback", name="browser_google_callback")
async def browser_google_callback(
    request: Request,
    service: PaperbaseService = Depends(get_service),
):
    settings = get_settings()
    client = get_google_oauth_client()
    redirect_base = _chat_redirect_base(request, settings)
    if client is None:
        return RedirectResponse(f"{redirect_base}/?auth_error=google_not_configured", status_code=302)
    try:
        token = await client.authorize_access_token(request)
        userinfo = token.get("userinfo") or await client.parse_id_token(request, token)
        if not userinfo:
            raise RuntimeError("Google did not return user info")
        email = userinfo.get("email")
        subject = userinfo.get("sub")
        name = userinfo.get("name") or userinfo.get("given_name") or ""
        if not email or not subject:
            raise RuntimeError("Google response was missing email or subject")
        session_response, raw_token = service.upsert_google_browser_user(
            google_subject=subject,
            email=email,
            display_name=name,
        )
        response = RedirectResponse(f"{redirect_base}/?auth=google", status_code=302)
        set_browser_session_cookie(response, raw_token, settings)
        return response
    except Exception:
        return RedirectResponse(f"{redirect_base}/?auth_error=google_callback_failed", status_code=302)


@app.get("/v1/browser/auth/github/start")
async def browser_github_start(
    request: Request,
    return_to: str | None = Query(default=None),
    user=Depends(require_browser_user),
):
    del user
    client = get_github_oauth_client()
    settings = get_settings()
    redirect_base = _chat_redirect_base(request, settings, return_to=return_to)
    if _is_safe_return_to(return_to):
        request.session["browser_github_return_to"] = return_to
    if client is None:
        return RedirectResponse(
            f"{redirect_base}/?auth_error=github_not_configured&settings=version-control",
            status_code=302,
        )
    redirect_uri = settings.github_oauth_redirect_url or str(request.url_for("browser_github_callback"))
    return await client.authorize_redirect(request, redirect_uri, scope="read:user repo")


@app.get("/v1/browser/auth/github/callback", name="browser_github_callback")
async def browser_github_callback(
    request: Request,
    service: PaperbaseService = Depends(get_service),
):
    settings = get_settings()
    client = get_github_oauth_client()
    redirect_base = _chat_redirect_base(request, settings)
    raw_token = _browser_session_token(request)
    user = service.get_current_browser_user(raw_token)
    if client is None:
        return RedirectResponse(
            f"{redirect_base}/?auth_error=github_not_configured&settings=version-control",
            status_code=302,
        )
    if user is None:
        return RedirectResponse(
            f"{redirect_base}/?auth_error=github_auth_required&settings=version-control",
            status_code=302,
        )
    try:
        token = await client.authorize_access_token(request)
        access_token = str(token.get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError("GitHub did not return an access token")
        userinfo_response = await client.get("user", token=token)
        userinfo = userinfo_response.json()
        github_user_id = str(userinfo.get("id") or "").strip()
        github_login = str(userinfo.get("login") or "").strip()
        if not github_user_id or not github_login:
            raise RuntimeError("GitHub response was missing user id or login")
        service.save_github_connection(
            user,
            github_user_id=github_user_id,
            github_login=github_login,
            access_token=access_token,
            scope=str(token.get("scope") or ""),
        )
        return RedirectResponse(
            f"{redirect_base}/?auth=github_connected&settings=version-control",
            status_code=302,
        )
    except Exception:
        return RedirectResponse(
            f"{redirect_base}/?auth_error=github_callback_failed&settings=version-control",
            status_code=302,
        )


@app.put("/v1/browser/settings/openai-key", response_model=BrowserSessionResponse)
@app.put("/v1/browser/settings/anthropic-key", response_model=BrowserSessionResponse, include_in_schema=False)
def browser_save_openai_key(
    request: SaveOpenAIKeyRequest,
    http_request: Request,
    response: Response,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> BrowserSessionResponse:
    if hasattr(service, "save_user_openai_key"):
        session_response = service.save_user_openai_key(user, request)
    else:
        session_response = service.save_user_anthropic_key(user, request)
    return _browser_session_response_with_csrf(
        session_response,
        request=http_request,
        response=response,
    )


@app.delete("/v1/browser/settings/openai-key", response_model=BrowserSessionResponse)
@app.delete("/v1/browser/settings/anthropic-key", response_model=BrowserSessionResponse, include_in_schema=False)
def browser_delete_openai_key(
    request: Request,
    response: Response,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> BrowserSessionResponse:
    if hasattr(service, "delete_user_openai_key"):
        session_response = service.delete_user_openai_key(user)
    else:
        session_response = service.delete_user_anthropic_key(user)
    return _browser_session_response_with_csrf(
        session_response,
        request=request,
        response=response,
    )


@app.put("/v1/browser/settings/modal-credentials", response_model=BrowserSessionResponse)
def browser_save_modal_credentials(
    request: SaveModalCredentialsRequest,
    http_request: Request,
    response: Response,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> BrowserSessionResponse:
    session_response = service.save_user_modal_credentials(user, request)
    return _browser_session_response_with_csrf(
        session_response,
        request=http_request,
        response=response,
    )


@app.delete("/v1/browser/settings/modal-credentials", response_model=BrowserSessionResponse)
def browser_delete_modal_credentials(
    request: Request,
    response: Response,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> BrowserSessionResponse:
    session_response = service.delete_user_modal_credentials(user)
    return _browser_session_response_with_csrf(
        session_response,
        request=request,
        response=response,
    )


@app.put("/v1/browser/settings/notifications", response_model=BrowserSessionResponse)
def browser_update_notifications(
    request: UpdateNotificationsRequest,
    http_request: Request,
    response: Response,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> BrowserSessionResponse:
    session_response = service.update_user_notification_preferences(user, request)
    return _browser_session_response_with_csrf(
        session_response,
        request=http_request,
        response=response,
    )


@app.put("/v1/browser/settings/appearance", response_model=BrowserSessionResponse)
def browser_update_appearance(
    request: UpdateAppearanceRequest,
    http_request: Request,
    response: Response,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> BrowserSessionResponse:
    session_response = service.update_user_appearance(user, request)
    return _browser_session_response_with_csrf(
        session_response,
        request=http_request,
        response=response,
    )


@app.delete("/v1/browser/settings/github", response_model=BrowserSessionResponse)
def browser_delete_github_connection(
    request: Request,
    response: Response,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> BrowserSessionResponse:
    session_response = service.delete_github_connection(user)
    return _browser_session_response_with_csrf(
        session_response,
        request=request,
        response=response,
    )


@app.get("/v1/browser/uploads", response_model=UploadListResponse)
def browser_list_uploads(
    user=Depends(require_browser_user),
    service: PaperbaseService = Depends(get_service),
) -> UploadListResponse:
    return service.list_uploaded_documents(user)


@app.post("/v1/browser/uploads", response_model=UploadedDocumentResponse)
async def browser_upload_document(
    file: UploadFile = File(...),
    tags: str = Form(default=""),
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> UploadedDocumentResponse:
    data = await file.read()
    return service.upload_document(
        user=user,
        filename=file.filename or "upload.bin",
        content_type=file.content_type or "application/octet-stream",
        data=data,
        tags=_parse_tags(tags),
    )


@app.get("/v1/browser/chats", response_model=ChatThreadListResponse)
def browser_list_chats(
    user=Depends(require_browser_user),
    service: PaperbaseService = Depends(get_service),
) -> ChatThreadListResponse:
    return service.list_chat_threads(user)


@app.get("/v1/browser/projects", response_model=ProjectListResponse)
def browser_list_projects(
    user=Depends(require_browser_user),
    service: PaperbaseService = Depends(get_service),
) -> ProjectListResponse:
    return service.list_projects(user)


@app.get("/v1/browser/github/repositories", response_model=GitHubRepoListResponse)
def browser_list_github_repositories(
    user=Depends(require_browser_user),
    service: PaperbaseService = Depends(get_service),
) -> GitHubRepoListResponse:
    return service.list_github_repositories(user)


@app.get("/v1/browser/github/repositories/{owner}/{repo}", response_model=GitHubRepoMetadataResponse)
def browser_get_github_repo_metadata(
    owner: str,
    repo: str,
    user=Depends(require_browser_user),
    service: PaperbaseService = Depends(get_service),
) -> GitHubRepoMetadataResponse:
    return service.get_github_repo_metadata(user, owner=owner, repo=repo)


@app.get(
    "/v1/browser/github/repositories/{owner}/{repo}/import-candidates",
    response_model=GitHubRepoImportCandidateResponse,
)
def browser_list_github_repo_import_candidates(
    owner: str,
    repo: str,
    branch: str = Query(..., min_length=1),
    user=Depends(require_browser_user),
    service: PaperbaseService = Depends(get_service),
) -> GitHubRepoImportCandidateResponse:
    return service.list_github_repo_import_candidates(user, owner=owner, repo=repo, branch=branch)


@app.post("/v1/browser/projects", response_model=ProjectResponse)
def browser_create_project(
    request: CreateProjectRequest,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> ProjectResponse:
    return service.create_project(user, request)


@app.patch("/v1/browser/projects/{project_id}", response_model=ProjectResponse)
def browser_update_project(
    project_id: str,
    request: UpdateProjectRequest,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> ProjectResponse:
    return service.update_project(user, project_id, request)


@app.delete("/v1/browser/projects/{project_id}", response_model=dict[str, str])
def browser_delete_project(
    project_id: str,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> dict[str, str]:
    service.delete_project(user, project_id)
    return {"status": "deleted"}


@app.get("/v1/browser/projects/{project_id}/sessions", response_model=ProjectSessionPageResponse)
def browser_list_project_sessions(
    project_id: str,
    limit: int = Query(5, ge=1, le=25),
    offset: int = Query(0, ge=0),
    user=Depends(require_browser_user),
    service: PaperbaseService = Depends(get_service),
) -> ProjectSessionPageResponse:
    return service.list_project_chat_threads(user, project_id, limit=limit, offset=offset)


@app.get("/v1/browser/projects/{project_id}/items", response_model=ProjectItemPageResponse)
def browser_list_project_items(
    project_id: str,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    item_kind: str | None = Query(None, pattern="^(thread|python_file)$"),
    user=Depends(require_browser_user),
    service: PaperbaseService = Depends(get_service),
) -> ProjectItemPageResponse:
    return service.list_project_items(user, project_id, limit=limit, offset=offset, item_kind=item_kind)


@app.post("/v1/browser/projects/{project_id}/notebooks", response_model=PythonFileResponse)
async def browser_import_project_notebook(
    project_id: str,
    file: UploadFile = File(...),
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> PythonFileResponse:
    data = await file.read()
    return service.import_project_notebook_upload(
        user,
        project_id,
        filename=file.filename or "notebook.ipynb",
        data=data,
    )


@app.post("/v1/browser/projects/{project_id}/python-files", response_model=PythonFileResponse)
def browser_create_python_file(
    project_id: str,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> PythonFileResponse:
    return service.create_python_file(user, project_id)


@app.get("/v1/browser/python-files/{file_id}", response_model=PythonFileResponse)
def browser_get_python_file(
    file_id: str,
    user=Depends(require_browser_user),
    service: PaperbaseService = Depends(get_service),
) -> PythonFileResponse:
    return service.get_python_file(user, file_id)


@app.patch("/v1/browser/python-files/{file_id}", response_model=PythonFileResponse)
def browser_update_python_file(
    file_id: str,
    request: UpdatePythonFileRequest,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> PythonFileResponse:
    return service.update_python_file(
        user,
        file_id,
        filename=request.filename,
        content=request.content,
    )


@app.delete("/v1/browser/python-files/{file_id}", response_model=dict[str, object])
def browser_delete_python_file(
    file_id: str,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> dict[str, object]:
    return service.delete_python_file(user, file_id)


@app.post("/v1/browser/python-files/{file_id}/run", response_model=RunPythonFileResponse)
def browser_run_python_file(
    file_id: str,
    request: RunPythonFileRequest | None = None,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> RunPythonFileResponse:
    return service.run_python_file(user, file_id, mode=(request.mode if request is not None else "full"))


@app.get("/v1/browser/projects/{project_id}/repo-sync", response_model=ProjectRepoSyncResponse)
def browser_get_project_repo_sync(
    project_id: str,
    user=Depends(require_browser_user),
    service: PaperbaseService = Depends(get_service),
) -> ProjectRepoSyncResponse:
    return service.get_project_repo_sync(user, project_id)


@app.post("/v1/browser/projects/{project_id}/repo-sync", response_model=ProjectRepoSyncResponse)
def browser_sync_project_repo(
    project_id: str,
    request: ProjectRepoSyncRequest,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> ProjectRepoSyncResponse:
    return service.sync_project_repo(user, project_id, request)


@app.post("/v1/browser/chats", response_model=ChatThreadResponse)
def browser_create_chat(
    request: CreateChatThreadRequest,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> ChatThreadResponse:
    return service.create_chat_thread(user, request)


@app.get("/v1/browser/chats/{thread_id}", response_model=ChatThreadResponse)
def browser_get_chat(
    thread_id: str,
    user=Depends(require_browser_user),
    service: PaperbaseService = Depends(get_service),
) -> ChatThreadResponse:
    return service.get_chat_thread(user, thread_id)


@app.patch("/v1/browser/chats/{thread_id}/project", response_model=ChatThreadResponse)
def browser_move_chat_to_project(
    thread_id: str,
    request: MoveChatThreadProjectRequest,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> ChatThreadResponse:
    return service.move_chat_thread_to_project(user, thread_id, request)


@app.delete("/v1/browser/chats/{thread_id}", response_model=dict[str, str])
def browser_delete_chat(
    thread_id: str,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> dict[str, str]:
    service.delete_chat_thread(user, thread_id)
    return {"status": "deleted"}


@app.post("/v1/browser/chats/{thread_id}/messages", response_model=SendChatMessageResponse)
def browser_send_chat_message(
    thread_id: str,
    request: SendChatMessageRequest,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> SendChatMessageResponse:
    return service.send_chat_message(user, thread_id, request)


@app.post("/v1/browser/chats/{thread_id}/messages/stream")
def browser_stream_chat_message(
    thread_id: str,
    request: SendChatMessageRequest,
    user=Depends(require_browser_write_user),
    service: PaperbaseService = Depends(get_service),
) -> StreamingResponse:
    def event_stream():
        for event_name, payload in service.stream_chat_message(user, thread_id, request):
            yield _sse_event(event_name, payload)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def main() -> None:
    import uvicorn

    settings = get_settings()
    host, port = _resolve_bind_address()
    reload_enabled = _env_flag("PAPERBASE_API_RELOAD")
    uvicorn.run(
        "paperbase.api:app",
        host=host,
        port=port,
        reload=reload_enabled,
        log_config=build_log_config(component="api", level=settings.log_level, log_format=settings.log_format),
        log_level=settings.log_level.lower(),
    )
