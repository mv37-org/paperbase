from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

import pytest
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

from paperbase import api as api_module
from paperbase import auth as auth_module
from paperbase.api import _chat_redirect_base, _is_safe_return_to, _parse_tags, _sse_event, app
from paperbase.auth import (
    clear_browser_session_cookie,
    require_bearer_token,
    set_browser_session_cookie,
    set_no_store_headers,
)
from paperbase.config import Settings, get_settings
from paperbase.models import (
    BrowserSessionResponse,
    BrowserUser,
    ChatMessageResponse,
    ChatThreadListResponse,
    ChatThreadResponse,
    ChatThreadSummary,
    FileListResponse,
    FileObject,
    JobResponse,
    PaperResponse,
    ResultSetResponse,
    SearchPaperResult,
    UploadListResponse,
)
from paperbase.security import csrf_token_for_session
from paperbase.service import get_service


NOW = datetime(2026, 4, 21, tzinfo=timezone.utc)


def _job(job_type: str, *, payload: dict[str, object] | None = None) -> JobResponse:
    return JobResponse(
        job_id=f"job_{job_type}",
        job_type=job_type,
        status="pending",
        payload=payload or {},
        created_at=NOW,
        updated_at=NOW,
    )


def _paper(arxiv_id: str = "2401.12345") -> PaperResponse:
    return PaperResponse(
        paper_id=f"arxiv:{arxiv_id}",
        arxiv_id=arxiv_id,
        title="Benchmark Paper",
        authors="Ada Lovelace",
        abstract="Grounded benchmark paper.",
        tldr="Benchmark tldr",
        summary_short="Short summary",
        summary_eli5="Simple summary",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2026-04-01",
        updated="2026-04-02",
        pdf_url=f"https://example.test/{arxiv_id}.pdf",
        html_url=f"https://example.test/{arxiv_id}",
        citation_count=12,
        influential_citation_count=3,
        index_status="indexed",
    )


class StubApiService:
    def __init__(self) -> None:
        self.browser_user = BrowserUser(
            user_id="user_1",
            email="user@example.com",
            display_name="User Example",
        )
        self.calls: list[tuple[str, object]] = []

    def enqueue_latest_sync_job(self):
        self.calls.append(("sync_latest", None))
        return _job("sync_latest")

    def enqueue_summarize_top_cited_job(self, *, limit: int):
        self.calls.append(("summarize_top_cited", limit))
        return _job("summarize_top_cited", payload={"limit": limit})

    def enqueue_reindex_embeddings_job(self, *, limit: int, include_chunks: bool):
        self.calls.append(("reindex_missing_embeddings", (limit, include_chunks)))
        return _job(
            "reindex_missing_embeddings",
            payload={"limit": limit, "include_chunks": include_chunks},
        )

    def expand_query(self, query: str, *, variants: int = 4) -> list[str]:
        self.calls.append(("expand_query", (query, variants)))
        return [query, f"{query} variant"]

    def suggest_papers(self, q: str, *, limit: int = 10) -> list[dict[str, object]]:
        self.calls.append(("suggest_papers", (q, limit)))
        paper = _paper()
        return [
            {
                "paper_id": paper.paper_id,
                "arxiv_id": paper.arxiv_id,
                "title": paper.title,
                "authors": paper.authors,
                "summary_short": paper.summary_short,
                "citation_count": paper.citation_count,
                "published": paper.published,
                "score": 0.9,
                "matched_field": "title",
                "highlight_snippet": paper.title,
            }
        ]

    def get_stats(self) -> dict[str, object]:
        self.calls.append(("stats", None))
        return {"paper_count": 1, "result_set_count": 2}

    def get_paper(self, paper_id: str) -> PaperResponse:
        self.calls.append(("get_paper", paper_id))
        return _paper("2401.54321")

    def list_files(self, paper_id: str) -> FileListResponse:
        self.calls.append(("list_files", paper_id))
        return FileListResponse(
            paper_id=paper_id,
            files=[FileObject(path="text/full.md", size=10, content_type="text/markdown")],
        )

    def get_results(self, result_set_id: str) -> ResultSetResponse:
        self.calls.append(("get_results", result_set_id))
        paper = SearchPaperResult(**_paper().model_dump(), score=1.0, source="local")
        return ResultSetResponse(
            result_set_id=result_set_id,
            query="grounded query",
            created_at=NOW,
            expires_at=NOW,
            papers=[paper],
        )

    def get_related_papers(self, paper_id: str) -> list[SearchPaperResult]:
        self.calls.append(("get_related_papers", paper_id))
        return [SearchPaperResult(**_paper().model_dump(), score=0.5, source="semantic-scholar")]

    def get_paper_references(self, paper_id: str, *, refresh: bool = False) -> list[dict[str, object]]:
        self.calls.append(("get_paper_references", (paper_id, refresh)))
        return [{"paper_id": paper_id, "reference_type": "citation"}]

    def get_job(self, job_id: str) -> JobResponse:
        self.calls.append(("get_job", job_id))
        return _job("index_paper", payload={"job_id": job_id})

    def get_current_browser_user(self, raw_token: str | None):
        self.calls.append(("get_current_browser_user", raw_token))
        if raw_token == "browser-session-token":
            return self.browser_user
        return None

    def delete_user_anthropic_key(self, user: BrowserUser) -> BrowserSessionResponse:
        self.calls.append(("delete_user_anthropic_key", user.user_id))
        return BrowserSessionResponse(
            authenticated=True,
            user=user,
            remaining_free_responses=2,
            has_anthropic_api_key=False,
        )

    def list_uploaded_documents(self, user: BrowserUser) -> UploadListResponse:
        self.calls.append(("list_uploaded_documents", user.user_id))
        return UploadListResponse(documents=[], available_tags=["benchmark"])

    def list_chat_threads(self, user: BrowserUser) -> ChatThreadListResponse:
        self.calls.append(("list_chat_threads", user.user_id))
        return ChatThreadListResponse(
            threads=[
                ChatThreadSummary(
                    thread_id="thread_1",
                    title="Open Thread",
                    created_at=NOW,
                    updated_at=NOW,
                    last_message_preview="Recent message",
                )
            ]
        )

    def get_chat_thread(self, user: BrowserUser, thread_id: str) -> ChatThreadResponse:
        self.calls.append(("get_chat_thread", (user.user_id, thread_id)))
        return ChatThreadResponse(
            thread_id=thread_id,
            title="Open Thread",
            created_at=NOW,
            updated_at=NOW,
            messages=[
                ChatMessageResponse(
                    message_id="msg_1",
                    role="assistant",
                    content_markdown="Grounded answer",
                    created_at=NOW,
                )
            ],
        )

    def upsert_google_browser_user(
        self,
        *,
        google_subject: str,
        email: str,
        display_name: str,
    ) -> tuple[BrowserSessionResponse, str]:
        self.calls.append(("upsert_google_browser_user", (google_subject, email, display_name)))
        return (
            BrowserSessionResponse(
                authenticated=True,
                user=BrowserUser(user_id="user_google", email=email, display_name=display_name),
                remaining_free_responses=3,
                has_anthropic_api_key=False,
            ),
            "google-browser-token",
        )


def _make_request(
    *,
    headers: dict[str, str] | None = None,
    session: dict[str, object] | None = None,
) -> Request:
    header_items = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": header_items,
        "query_string": b"",
        "scheme": "http",
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    request = Request(scope)
    request.scope["session"] = dict(session or {})
    return request


@pytest.fixture
def api_client(monkeypatch):
    settings = Settings(
        api_key="test-bearer-token",
        browser_origin="https://chat.example",
        chat_public_url="https://paperbase.example",
        app_secret="paperbase-test-secret",
        browser_session_cookie_name="paperbase_session",
    )
    service = StubApiService()

    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(api_module, "get_settings", lambda: settings)
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings)

    with TestClient(app) as client:
        yield client, service, settings

    app.dependency_overrides.clear()


def test_parse_tags_normalizes_commas_and_newlines():
    assert _parse_tags("agents, retrieval\nbenchmark , grounding") == [
        "agents",
        "retrieval",
        "benchmark",
        "grounding",
    ]


def test_is_safe_return_to_requires_absolute_http_urls():
    assert _is_safe_return_to("https://chat.example/app") is True
    assert _is_safe_return_to("http://chat.example/app") is True
    assert _is_safe_return_to("/relative/path") is False
    assert _is_safe_return_to("javascript:alert(1)") is False
    assert _is_safe_return_to(None) is False


def test_chat_redirect_base_prefers_explicit_then_session_then_referer_then_default():
    settings = Settings(chat_public_url="https://paperbase.example")
    request = _make_request(
        headers={"referer": "https://ref.example/thread/123"},
        session={"browser_google_return_to": "https://session.example/app"},
    )

    assert (
        _chat_redirect_base(request, settings, return_to="https://explicit.example/app")
        == "https://explicit.example/app"
    )
    assert _chat_redirect_base(request, settings) == "https://session.example/app"
    assert (
        _chat_redirect_base(
            _make_request(headers={"referer": "https://ref.example/thread/123"}),
            settings,
        )
        == "https://ref.example"
    )
    assert _chat_redirect_base(_make_request(), settings) == "https://paperbase.example"


def test_sse_event_wraps_json_payload():
    event = _sse_event("delta", {"text": "Grounded answer", "count": 2})

    assert event == 'event: delta\ndata: {"text": "Grounded answer", "count": 2}\n\n'


def test_bearer_auth_requires_valid_token():
    settings = Settings(api_key="secret-token")

    with pytest.raises(Exception) as missing_exc:
        require_bearer_token(credentials=None, settings=settings)
    assert missing_exc.value.status_code == 401

    with pytest.raises(Exception) as invalid_exc:
        require_bearer_token(
            credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong-token"),
            settings=settings,
        )
    assert invalid_exc.value.status_code == 403

    assert (
        require_bearer_token(
            credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="secret-token"),
            settings=settings,
        )
        == "secret-token"
    )


def test_browser_cookie_helpers_set_and_clear_expected_headers():
    settings = Settings(
        browser_session_cookie_name="paperbase_session",
        browser_session_ttl_seconds=3600,
        browser_session_secure=True,
    )
    response = Response()

    set_browser_session_cookie(response, "raw-token", settings)
    set_no_store_headers(response)
    clear_browser_session_cookie(response, settings)

    set_cookie_headers = response.headers.getlist("set-cookie")
    assert any("paperbase_session=raw-token" in value for value in set_cookie_headers)
    assert any("paperbase_session=\"\"" in value for value in set_cookie_headers)
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"


def test_bearer_routes_require_valid_authentication(api_client):
    client, _, _ = api_client

    missing = client.get("/v1/stats")
    invalid = client.get("/v1/stats", headers={"Authorization": "Bearer wrong"})

    assert missing.status_code == 401
    assert invalid.status_code == 403


def test_bearer_routes_delegate_to_service(api_client):
    client, service, settings = api_client
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    sync = client.post("/v1/index/sync/latest", headers=headers)
    summarize = client.post("/v1/index/summarize-top-cited?limit=42", headers=headers)
    reindex = client.post(
        "/v1/index/reindex-embeddings?limit=11&include_chunks=false",
        headers=headers,
    )
    expanded = client.post("/v1/query/expand?query=grounding&variants=2", headers=headers)
    suggested = client.get("/v1/papers/suggest?q=ground&limit=5", headers=headers)
    stats = client.get("/v1/stats", headers=headers)
    paper = client.get("/v1/papers/arxiv:2401.54321", headers=headers)
    files = client.get("/v1/papers/arxiv:2401.54321/files", headers=headers)
    results = client.get("/v1/results/rs_1", headers=headers)
    related = client.get("/v1/papers/arxiv:2401.54321/related", headers=headers)
    references = client.get("/v1/papers/arxiv:2401.54321/references?refresh=true", headers=headers)
    job = client.get("/v1/jobs/job_123", headers=headers)

    assert sync.status_code == 200
    assert summarize.json()["payload"]["limit"] == 42
    assert reindex.json()["payload"] == {"limit": 11, "include_chunks": False}
    assert expanded.json() == {"queries": ["grounding", "grounding variant"]}
    assert suggested.json()["suggestions"][0]["matched_field"] == "title"
    assert stats.json()["paper_count"] == 1
    assert paper.json()["paper_id"] == "arxiv:2401.54321"
    assert files.json()["files"][0]["path"] == "text/full.md"
    assert results.json()["result_set_id"] == "rs_1"
    assert related.json()[0]["source"] == "semantic-scholar"
    assert references.json()[0]["reference_type"] == "citation"
    assert job.json()["payload"]["job_id"] == "job_123"
    assert ("sync_latest", None) in service.calls
    assert ("get_job", "job_123") in service.calls


def test_browser_get_routes_accept_session_cookie_without_csrf(api_client):
    client, service, settings = api_client
    client.cookies.set(settings.browser_session_cookie_name, "browser-session-token")

    uploads = client.get("/v1/browser/uploads")
    chats = client.get("/v1/browser/chats")
    thread = client.get("/v1/browser/chats/thread_1")

    assert uploads.status_code == 200
    assert uploads.json()["available_tags"] == ["benchmark"]
    assert chats.json()["threads"][0]["thread_id"] == "thread_1"
    assert thread.json()["messages"][0]["content_markdown"] == "Grounded answer"
    assert ("list_uploaded_documents", "user_1") in service.calls
    assert ("get_chat_thread", ("user_1", "thread_1")) in service.calls


def test_browser_delete_anthropic_key_requires_valid_csrf(api_client):
    client, service, settings = api_client
    client.cookies.set(settings.browser_session_cookie_name, "browser-session-token")
    csrf = csrf_token_for_session("browser-session-token", settings)

    response = client.delete(
        "/v1/browser/settings/anthropic-key",
        headers={
            "Origin": settings.browser_origin,
            "X-Paperbase-CSRF": csrf,
        },
    )

    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert response.json()["has_anthropic_api_key"] is False
    assert response.json()["csrf_token"] == csrf
    assert ("delete_user_anthropic_key", "user_1") in service.calls


def test_google_callback_success_sets_cookie_and_redirects_to_saved_return_to(api_client, monkeypatch):
    client, service, settings = api_client

    class FakeGoogleClient:
        async def authorize_redirect(self, request, redirect_uri):
            return RedirectResponse(
                f"https://accounts.google.com/o/oauth2/auth?redirect_uri={quote(redirect_uri, safe='')}",
                status_code=302,
            )

        async def authorize_access_token(self, request):
            return {
                "userinfo": {
                    "email": "google@example.com",
                    "sub": "google-user-1",
                    "name": "Google User",
                }
            }

        async def parse_id_token(self, request, token):
            return token.get("userinfo")

    monkeypatch.setattr(api_module, "get_google_oauth_client", lambda: FakeGoogleClient())

    start = client.get(
        "/v1/browser/auth/google/start",
        params={"return_to": "https://chat.example/app"},
        follow_redirects=False,
    )
    callback = client.get("/v1/browser/auth/google/callback", follow_redirects=False)

    assert start.status_code == 302
    assert callback.status_code == 302
    assert callback.headers["location"] == "https://chat.example/app/?auth=google"
    assert "google-browser-token" in callback.headers["set-cookie"]
    assert (
        "upsert_google_browser_user",
        ("google-user-1", "google@example.com", "Google User"),
    ) in service.calls


def test_google_callback_failure_redirects_with_error(api_client, monkeypatch):
    client, _, settings = api_client

    class FailingGoogleClient:
        async def authorize_access_token(self, request):
            raise RuntimeError("no token")

        async def parse_id_token(self, request, token):
            return None

    monkeypatch.setattr(api_module, "get_google_oauth_client", lambda: FailingGoogleClient())

    response = client.get("/v1/browser/auth/google/callback", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == f"{settings.chat_public_url}/?auth_error=google_callback_failed"
