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
    GitHubRepoBranchSummary,
    GitHubRepoImportCandidate,
    GitHubRepoImportCandidateResponse,
    GitHubRepoListResponse,
    GitHubRepoMetadataResponse,
    GitHubRepoSummary,
    JobResponse,
    PaperPreviewResponse,
    PaperResponse,
    ProjectItemPageResponse,
    ProjectItemSummary,
    ProjectListResponse,
    ProjectRepoSyncResponse,
    ProjectResponse,
    ProjectSessionPageResponse,
    PythonFileResponse,
    ResultSetResponse,
    RunPythonFileResponse,
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


def _project(
    project_id: str = "proj_1",
    *,
    name: str = "General",
    description: str = "",
    is_default: bool = False,
    session_count: int = 1,
) -> ProjectResponse:
    return ProjectResponse(
        project_id=project_id,
        name=name,
        description=description,
        is_default=is_default,
        session_count=session_count,
        created_at=NOW,
        updated_at=NOW,
    )


def _project_item(
    item_id: str = "thread_1",
    *,
    item_kind: str = "thread",
    project_id: str = "proj_1",
    title: str = "Open Thread",
    thread_id: str | None = "thread_1",
    file_id: str | None = None,
    filename: str = "",
    file_kind: str = "python",
    repo_path: str = "",
) -> ProjectItemSummary:
    return ProjectItemSummary(
        item_id=item_id,
        item_kind=item_kind,
        project_id=project_id,
        title=title,
        created_at=NOW,
        updated_at=NOW,
        thread_id=thread_id,
        file_id=file_id,
        last_message_preview="Recent message" if item_kind == "thread" else "",
        filename=filename,
        file_kind=file_kind,  # type: ignore[arg-type]
        repo_path=repo_path,
    )


def _python_file(
    file_id: str = "file_1",
    *,
    project_id: str = "proj_1",
    filename: str = "something.py",
    content: str = "",
    file_kind: str = "python",
    repo_path: str = "src/something.py",
) -> PythonFileResponse:
    return PythonFileResponse(
        file_id=file_id,
        project_id=project_id,
        filename=filename,
        file_kind=file_kind,  # type: ignore[arg-type]
        repo_path=repo_path,
        content=content,
        created_at=NOW,
        updated_at=NOW,
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

    def enqueue_reindex_embeddings_job(self, *, limit: int, include_chunks: bool, rewrite_existing: bool):
        self.calls.append(("reindex_embeddings", (limit, include_chunks, rewrite_existing)))
        return _job(
            "reindex_embeddings" if rewrite_existing else "reindex_missing_embeddings",
            payload={
                "limit": limit,
                "include_chunks": include_chunks,
                "rewrite_existing": rewrite_existing,
            },
        )

    def enqueue_enrich_missing_metadata_job(self, *, limit: int, min_age_seconds: int):
        self.calls.append(("enqueue_enrich_missing_metadata_job", (limit, min_age_seconds)))
        return _job(
            "enrich_missing_metadata",
            payload={"limit": limit, "min_age_seconds": min_age_seconds},
        )

    def expand_query(self, query: str, *, variants: int = 4) -> list[str]:
        self.calls.append(("expand_query", (query, variants)))
        return [query, f"{query} variant"]

    def suggest_papers(self, q: str, *, limit: int = 10) -> list[dict[str, object]]:
        self.calls.append(("suggest_papers", (q, limit)))
        if q == "nomatch":
            return []
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

    def suggest_fallback_papers(self, *, limit: int = 6) -> list[dict[str, object]]:
        self.calls.append(("suggest_fallback_papers", limit))
        paper = _paper("2409.99999")
        return [
            {
                "paper_id": paper.paper_id,
                "arxiv_id": paper.arxiv_id,
                "title": paper.title,
                "authors": paper.authors,
                "summary_short": paper.summary_short,
                "citation_count": paper.citation_count,
                "published": paper.published,
                "score": 0.0,
                "matched_field": "fallback",
                "highlight_snippet": paper.summary_short,
            }
        ]

    def get_stats(self) -> dict[str, object]:
        self.calls.append(("stats", None))
        return {"paper_count": 1, "result_set_count": 2}

    def get_paper(self, paper_id: str) -> PaperResponse:
        self.calls.append(("get_paper", paper_id))
        return _paper("2401.54321")

    def get_paper_preview(self, paper_id: str) -> PaperPreviewResponse:
        self.calls.append(("get_paper_preview", paper_id))
        if paper_id == "missing":
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Preview not found")
        return PaperPreviewResponse(
            paper_id=paper_id,
            image_url=f"https://signed.example/{paper_id}/preview?sig=abc",
            content_type="image/webp",
        )

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

    def list_jobs_by_status(self, status: str, *, limit: int = 50) -> list[JobResponse]:
        self.calls.append(("list_jobs_by_status", (status, limit)))
        return [_job("index_paper", payload={"job_id": f"dead_{status}"})]

    def enqueue_harvest_range_job(self, **kwargs):
        self.calls.append(("enqueue_harvest_range_job", kwargs))
        return _job("harvest_range", payload=kwargs)

    def enqueue_harvest_top_cited_job(self, **kwargs):
        self.calls.append(("enqueue_harvest_top_cited_job", kwargs))
        return _job("harvest_top_cited", payload=kwargs)

    def enqueue_harvest_venue_job(self, **kwargs):
        self.calls.append(("enqueue_harvest_venue_job", kwargs))
        return _job("harvest_venue", payload=kwargs)

    def enqueue_harvest_author_job(self, **kwargs):
        self.calls.append(("enqueue_harvest_author_job", kwargs))
        return _job("harvest_author", payload=kwargs)

    def retry_dead_job(self, job_id: str):
        self.calls.append(("retry_dead_job", job_id))
        if job_id == "job_nonexistent":
            return None
        return _job("index_paper", payload={"job_id": job_id, "retried": True})

    def record_search_click(self, query_id: str, paper_id: str) -> bool:
        self.calls.append(("record_search_click", (query_id, paper_id)))
        return query_id == "qry_known"

    # Paper-code retrieval stubs (#5 PR 2). Every method returns ``None``
    # for ``paper_unknown`` so the route-level 404 path is covered.
    def list_paper_code_files(self, paper_id: str):
        from paperbase.models import CodeEligibility, CodeFileListResponse, CodeFileSummary

        self.calls.append(("list_paper_code_files", paper_id))
        if paper_id == "paper_unknown":
            return None
        return CodeFileListResponse(
            paper_id=paper_id,
            eligibility=CodeEligibility(eligible=True, status="indexed"),
            resolved_sha="abc123",
            files=[CodeFileSummary(repo_path="README.md", size_bytes=10, indexed=True)],
        )

    def read_paper_code_file(self, paper_id: str, repo_path: str):
        from paperbase.models import CodeEligibility, CodeReadResponse

        self.calls.append(("read_paper_code_file", (paper_id, repo_path)))
        if paper_id == "paper_unknown":
            return None
        return CodeReadResponse(
            paper_id=paper_id,
            eligibility=CodeEligibility(eligible=True, status="indexed"),
            repo_path=repo_path,
            content="# stub\n",
            size_bytes=7,
        )

    def search_paper_code(self, paper_id: str, query: str, *, limit: int = 10):
        from paperbase.models import CodeEligibility, CodeSearchHit, CodeSearchResponse

        self.calls.append(("search_paper_code", (paper_id, query, limit)))
        if paper_id == "paper_unknown":
            return None
        return CodeSearchResponse(
            paper_id=paper_id,
            eligibility=CodeEligibility(eligible=True, status="indexed"),
            query=query,
            hits=[
                CodeSearchHit(
                    repo_path="README.md", chunk_kind="code_readme", preview="demo", score=0.9
                )
            ],
        )

    def get_paper_code_context(self, paper_id: str, query: str, *, limit: int = 5):
        from paperbase.models import CodeContextResponse, CodeEligibility

        self.calls.append(("get_paper_code_context", (paper_id, query, limit)))
        if paper_id == "paper_unknown":
            return None
        return CodeContextResponse(
            paper_id=paper_id,
            eligibility=CodeEligibility(eligible=True, status="indexed"),
            query=query,
            context="### README.md (code_readme)\ndemo",
        )

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
            has_anthropic_api_key=False,
        )

    def save_user_modal_credentials(self, user: BrowserUser, request) -> BrowserSessionResponse:
        self.calls.append(("save_user_modal_credentials", (user.user_id, request.token_id, request.token_secret)))
        return BrowserSessionResponse(
            authenticated=True,
            user=user,
            has_anthropic_api_key=False,
        )

    def update_user_notification_preferences(self, user: BrowserUser, request) -> BrowserSessionResponse:
        self.calls.append(("update_user_notification_preferences", (user.user_id, request.weekly_papers_review_email)))
        return BrowserSessionResponse(
            authenticated=True,
            user=user,
            has_anthropic_api_key=False,
        )

    def update_user_appearance(self, user: BrowserUser, request) -> BrowserSessionResponse:
        self.calls.append(("update_user_appearance", (user.user_id, request.theme)))
        return BrowserSessionResponse(
            authenticated=True,
            user=user,
            has_anthropic_api_key=False,
        )

    def save_github_connection(
        self,
        user: BrowserUser,
        *,
        github_user_id: str,
        github_login: str,
        access_token: str,
        scope: str,
    ) -> BrowserSessionResponse:
        self.calls.append(
            (
                "save_github_connection",
                (user.user_id, github_user_id, github_login, access_token, scope),
            )
        )
        return BrowserSessionResponse(
            authenticated=True,
            user=user,
            has_anthropic_api_key=False,
        )

    def delete_github_connection(self, user: BrowserUser) -> BrowserSessionResponse:
        self.calls.append(("delete_github_connection", user.user_id))
        return BrowserSessionResponse(
            authenticated=True,
            user=user,
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
                    project_id="proj_1",
                    title="Open Thread",
                    created_at=NOW,
                    updated_at=NOW,
                    last_message_preview="Recent message",
                )
            ]
        )

    def list_projects(self, user: BrowserUser) -> ProjectListResponse:
        self.calls.append(("list_projects", user.user_id))
        return ProjectListResponse(
            projects=[
                _project("proj_1", name="General", is_default=True, session_count=1),
                _project("proj_2", name="Agents", description="Study agent systems", session_count=1),
            ]
        )

    def get_github_repo_metadata(self, user: BrowserUser, *, owner: str, repo: str) -> GitHubRepoMetadataResponse:
        self.calls.append(("get_github_repo_metadata", (user.user_id, owner, repo)))
        return GitHubRepoMetadataResponse(
            owner=owner,
            name=repo,
            html_url=f"https://github.com/{owner}/{repo}",
            default_branch="main",
            branches=[
                GitHubRepoBranchSummary(name="main", commit_sha="abc123", is_default=True),
                GitHubRepoBranchSummary(name="dev", commit_sha="def456", is_default=False),
            ],
        )

    def list_github_repositories(self, user: BrowserUser) -> GitHubRepoListResponse:
        self.calls.append(("list_github_repositories", user.user_id))
        return GitHubRepoListResponse(
            repos=[
                GitHubRepoSummary(
                    owner="octocat",
                    name="existing-repo",
                    full_name="octocat/existing-repo",
                    html_url="https://github.com/octocat/existing-repo",
                    default_branch="main",
                    private=True,
                )
            ]
        )

    def list_github_repo_import_candidates(
        self,
        user: BrowserUser,
        *,
        owner: str,
        repo: str,
        branch: str,
    ) -> GitHubRepoImportCandidateResponse:
        self.calls.append(("list_github_repo_import_candidates", (user.user_id, owner, repo, branch)))
        return GitHubRepoImportCandidateResponse(
            owner=owner,
            name=repo,
            base_branch=branch,
            candidates=[
                GitHubRepoImportCandidate(path="src/analysis.py", file_kind="python", size=18),
                GitHubRepoImportCandidate(path="notebooks/demo.ipynb", file_kind="notebook", size=128),
            ],
        )

    def create_project(self, user: BrowserUser, request) -> ProjectResponse:
        self.calls.append(
            (
                "create_project",
                (
                    user.user_id,
                    request.name,
                    request.description,
                    request.github_repo.mode if request.github_repo else None,
                    request.github_repo.name if request.github_repo else None,
                ),
            )
        )
        return _project(
            "proj_3",
            name=request.name,
            description=request.description,
            session_count=0,
        )

    def update_project(self, user: BrowserUser, project_id: str, request) -> ProjectResponse:
        self.calls.append(("update_project", (user.user_id, project_id, request.name, request.description)))
        return _project(
            project_id,
            name=request.name,
            description=request.description,
            is_default=(project_id == "proj_1"),
            session_count=1,
        )

    def delete_project(self, user: BrowserUser, project_id: str) -> None:
        self.calls.append(("delete_project", (user.user_id, project_id)))

    def list_project_chat_threads(
        self,
        user: BrowserUser,
        project_id: str,
        *,
        limit: int = 5,
        offset: int = 0,
    ) -> ProjectSessionPageResponse:
        self.calls.append(("list_project_chat_threads", (user.user_id, project_id, limit, offset)))
        return ProjectSessionPageResponse(
            project_id=project_id,
            threads=[
                ChatThreadSummary(
                    thread_id="thread_1",
                    project_id=project_id,
                    title="Open Thread",
                    created_at=NOW,
                    updated_at=NOW,
                    last_message_preview="Recent message",
                )
            ],
            offset=offset,
            limit=limit,
            total_count=1,
            has_more=False,
        )

    def list_project_items(
        self,
        user: BrowserUser,
        project_id: str,
        *,
        limit: int = 25,
        offset: int = 0,
        item_kind: str | None = None,
    ) -> ProjectItemPageResponse:
        self.calls.append(("list_project_items", (user.user_id, project_id, limit, offset, item_kind)))
        items = [
            _project_item(
                "thread_1",
                item_kind="thread",
                project_id=project_id,
                title="Open Thread",
                thread_id="thread_1",
            ),
            _project_item(
                "file_1",
                item_kind="python_file",
                project_id=project_id,
                title="something.py",
                thread_id=None,
                file_id="file_1",
                filename="something.py",
            ),
        ]
        if item_kind:
            items = [item for item in items if item.item_kind == item_kind]
        return ProjectItemPageResponse(
            project_id=project_id,
            items=items,
            offset=offset,
            limit=limit,
            total_count=len(items),
            has_more=False,
        )

    def create_python_file(self, user: BrowserUser, project_id: str) -> PythonFileResponse:
        self.calls.append(("create_python_file", (user.user_id, project_id)))
        return _python_file("file_1", project_id=project_id, filename="something.py")

    def import_project_notebook_upload(
        self,
        user: BrowserUser,
        project_id: str,
        *,
        filename: str,
        data: bytes,
    ) -> PythonFileResponse:
        self.calls.append(("import_project_notebook_upload", (user.user_id, project_id, filename, data)))
        return _python_file(
            "file_2",
            project_id=project_id,
            filename=filename,
            content="# %%\nprint('notebook')\n",
            file_kind="notebook",
            repo_path=f"notebooks/{filename}",
        )

    def get_python_file(self, user: BrowserUser, file_id: str) -> PythonFileResponse:
        self.calls.append(("get_python_file", (user.user_id, file_id)))
        return _python_file(file_id, filename="something.py", content="print('hello')")

    def update_python_file(
        self,
        user: BrowserUser,
        file_id: str,
        *,
        filename: str | None,
        content: str | None,
    ) -> PythonFileResponse:
        self.calls.append(("update_python_file", (user.user_id, file_id, filename, content)))
        return _python_file(
            file_id,
            filename=filename or "something.py",
            content=content or "",
        )

    def delete_python_file(self, user: BrowserUser, file_id: str) -> dict[str, object]:
        self.calls.append(("delete_python_file", (user.user_id, file_id)))
        return {
            "file_id": file_id,
            "project_id": "proj_1",
            "filename": "something.py",
            "deleted": True,
        }

    def run_python_file(self, user: BrowserUser, file_id: str, *, mode: str = "full") -> RunPythonFileResponse:
        self.calls.append(("run_python_file", (user.user_id, file_id, mode)))
        return RunPythonFileResponse(
            file_id=file_id,
            status="completed",
            stdout="runner output",
            stderr="",
            exit_code=0,
            started_at=NOW,
            finished_at=NOW,
        )

    def get_project_repo_sync(self, user: BrowserUser, project_id: str) -> ProjectRepoSyncResponse:
        self.calls.append(("get_project_repo_sync", (user.user_id, project_id)))
        return ProjectRepoSyncResponse(project_id=project_id, repo_exists=False)

    def sync_project_repo(self, user: BrowserUser, project_id: str, request) -> ProjectRepoSyncResponse:
        self.calls.append(("sync_project_repo", (user.user_id, project_id, request.commit_message)))
        return ProjectRepoSyncResponse(
            project_id=project_id,
            repo_exists=True,
            github_repo_name="paperbase-general-123abc",
            github_repo_url="https://github.com/octocat/paperbase-general-123abc",
            default_branch="main",
            last_sync_commit_sha="abc123",
            last_sync_commit_url="https://github.com/octocat/paperbase-general-123abc/commit/abc123",
            last_synced_at=NOW,
        )

    def create_chat_thread(self, user: BrowserUser, request) -> ChatThreadResponse:
        self.calls.append(("create_chat_thread", (user.user_id, request.title, request.project_id)))
        return ChatThreadResponse(
            thread_id="thread_2",
            project_id=request.project_id or "proj_1",
            project_name="Agents" if request.project_id == "proj_2" else "General",
            project_instructions="Study agent systems" if request.project_id == "proj_2" else "",
            title=request.title or "New chat",
            created_at=NOW,
            updated_at=NOW,
            messages=[],
        )

    def move_chat_thread_to_project(self, user: BrowserUser, thread_id: str, request) -> ChatThreadResponse:
        self.calls.append(("move_chat_thread_to_project", (user.user_id, thread_id, request.project_id)))
        return ChatThreadResponse(
            thread_id=thread_id,
            project_id=request.project_id,
            project_name="Agents" if request.project_id == "proj_2" else "General",
            project_instructions="Study agent systems" if request.project_id == "proj_2" else "",
            title="Open Thread",
            created_at=NOW,
            updated_at=NOW,
            messages=[],
        )

    def get_chat_thread(self, user: BrowserUser, thread_id: str) -> ChatThreadResponse:
        self.calls.append(("get_chat_thread", (user.user_id, thread_id)))
        return ChatThreadResponse(
            thread_id=thread_id,
            project_id="proj_1",
            project_name="General",
            project_instructions="",
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

    def delete_chat_thread(self, user: BrowserUser, thread_id: str) -> None:
        self.calls.append(("delete_chat_thread", (user.user_id, thread_id)))

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
                has_anthropic_api_key=False,
            ),
            "google-browser-token",
        )


def _make_request(
    *,
    headers: dict[str, str] | None = None,
    session: dict[str, object] | None = None,
) -> Request:
    header_items = [(key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in (headers or {}).items()]
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
    assert any('paperbase_session=""' in value for value in set_cookie_headers)
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"


def test_bearer_routes_require_valid_authentication(api_client):
    client, _, _ = api_client

    missing = client.get("/v1/stats")
    invalid = client.get("/v1/stats", headers={"Authorization": "Bearer wrong"})

    assert missing.status_code == 401
    assert invalid.status_code == 403


def test_search_click_endpoint_delegates_to_service_and_404s_on_unknown(api_client):
    """POST /v1/search/{query_id}/click returns 204 on success, 404 when
    the service reports the query_id is unknown."""
    client, service, settings = api_client
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    ok = client.post(
        "/v1/search/qry_known/click",
        headers=headers,
        json={"paper_id": "arxiv:2401.12345"},
    )
    unknown = client.post(
        "/v1/search/qry_unknown/click",
        headers=headers,
        json={"paper_id": "arxiv:2401.12345"},
    )

    assert ok.status_code == 204
    assert unknown.status_code == 404
    assert ("record_search_click", ("qry_known", "arxiv:2401.12345")) in service.calls


def test_search_click_endpoint_requires_bearer_auth(api_client):
    """The click endpoint is authenticated just like /v1/search itself."""
    client, _, _ = api_client
    response = client.post(
        "/v1/search/qry_known/click",
        json={"paper_id": "arxiv:2401.12345"},
    )
    assert response.status_code == 401


def test_bearer_routes_delegate_to_service(api_client):
    client, service, settings = api_client
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    sync = client.post("/v1/index/sync/latest", headers=headers)
    summarize = client.post("/v1/index/summarize-top-cited?limit=42", headers=headers)
    reindex = client.post(
        "/v1/index/reindex-embeddings?limit=11&include_chunks=false&rewrite_existing=false",
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
    assert reindex.json()["payload"] == {
        "limit": 11,
        "include_chunks": False,
        "rewrite_existing": False,
    }
    assert expanded.json() == {"queries": ["grounding", "grounding variant"]}
    assert suggested.json()["fallback"] is False
    assert suggested.json()["suggestions"][0]["matched_field"] == "title"
    assert stats.json()["paper_count"] == 1
    assert paper.json()["paper_id"] == "arxiv:2401.54321"
    assert files.json()["files"][0]["path"] == "text/full.md"
    assert results.json()["result_set_id"] == "rs_1"
    assert related.json()[0]["source"] == "semantic-scholar"
    assert references.json()[0]["reference_type"] == "citation"
    assert job.json()["payload"]["job_id"] == "job_123"
    assert ("sync_latest", None) in service.calls
    assert ("reindex_embeddings", (11, False, False)) in service.calls
    assert ("get_job", "job_123") in service.calls


def test_browser_suggest_route_is_public(api_client):
    client, service, _ = api_client

    suggested = client.get("/v1/browser/papers/suggest?q=ground&limit=5")

    assert suggested.status_code == 200
    assert suggested.json()["fallback"] is False
    assert suggested.json()["suggestions"][0]["matched_field"] == "title"
    assert ("suggest_papers", ("ground", 5)) in service.calls


def test_browser_suggest_route_returns_fallback_suggestions_when_no_matches(api_client):
    client, service, _ = api_client

    suggested = client.get("/v1/browser/papers/suggest?q=nomatch&limit=6")

    assert suggested.status_code == 200
    assert suggested.json()["fallback"] is True
    assert suggested.json()["suggestions"][0]["matched_field"] == "fallback"
    assert ("suggest_papers", ("nomatch", 6)) in service.calls
    assert ("suggest_fallback_papers", 6) in service.calls


def test_browser_get_routes_accept_session_cookie_without_csrf(api_client):
    client, service, settings = api_client
    client.cookies.set(settings.browser_session_cookie_name, "browser-session-token")

    uploads = client.get("/v1/browser/uploads")
    chats = client.get("/v1/browser/chats")
    thread = client.get("/v1/browser/chats/thread_1")
    projects = client.get("/v1/browser/projects")
    project_sessions = client.get("/v1/browser/projects/proj_1/sessions?limit=5&offset=0")
    repo_list = client.get("/v1/browser/github/repositories")
    repo_metadata = client.get("/v1/browser/github/repositories/octocat/existing-repo")
    import_candidates = client.get(
        "/v1/browser/github/repositories/octocat/existing-repo/import-candidates?branch=dev"
    )

    assert uploads.status_code == 200
    assert uploads.json()["available_tags"] == ["benchmark"]
    assert chats.json()["threads"][0]["thread_id"] == "thread_1"
    assert thread.json()["messages"][0]["content_markdown"] == "Grounded answer"
    assert projects.json()["projects"][0]["name"] == "General"
    assert project_sessions.json()["threads"][0]["project_id"] == "proj_1"
    assert repo_list.json()["repos"][0]["full_name"] == "octocat/existing-repo"
    assert repo_list.json()["repos"][0]["private"] is True
    assert repo_metadata.json()["branches"][0]["name"] == "main"
    assert repo_metadata.json()["branches"][0]["commit_sha"] == "abc123"
    assert import_candidates.json()["candidates"][1]["file_kind"] == "notebook"
    assert ("list_uploaded_documents", "user_1") in service.calls
    assert ("list_projects", "user_1") in service.calls
    assert ("list_project_chat_threads", ("user_1", "proj_1", 5, 0)) in service.calls
    assert ("list_github_repositories", "user_1") in service.calls
    assert ("get_github_repo_metadata", ("user_1", "octocat", "existing-repo")) in service.calls
    assert (
        "list_github_repo_import_candidates",
        ("user_1", "octocat", "existing-repo", "dev"),
    ) in service.calls
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


def test_browser_settings_routes_delegate_with_valid_csrf(api_client):
    client, service, settings = api_client
    client.cookies.set(settings.browser_session_cookie_name, "browser-session-token")
    csrf = csrf_token_for_session("browser-session-token", settings)
    headers = {
        "Origin": settings.browser_origin,
        "X-Paperbase-CSRF": csrf,
    }

    modal_credentials = client.put(
        "/v1/browser/settings/modal-credentials",
        json={"token_id": "modal-token-id-123", "token_secret": "modal-token-secret-456"},
        headers=headers,
    )
    notifications = client.put(
        "/v1/browser/settings/notifications",
        json={"weekly_papers_review_email": True},
        headers=headers,
    )
    appearance = client.put(
        "/v1/browser/settings/appearance",
        json={"theme": "dark"},
        headers=headers,
    )
    github = client.delete("/v1/browser/settings/github", headers=headers)

    assert modal_credentials.status_code == 200
    assert notifications.status_code == 200
    assert appearance.status_code == 200
    assert github.status_code == 200
    assert modal_credentials.json()["csrf_token"] == csrf
    assert notifications.json()["csrf_token"] == csrf
    assert appearance.json()["csrf_token"] == csrf
    assert github.json()["csrf_token"] == csrf
    assert (
        "save_user_modal_credentials",
        ("user_1", "modal-token-id-123", "modal-token-secret-456"),
    ) in service.calls
    assert ("update_user_notification_preferences", ("user_1", True)) in service.calls
    assert ("update_user_appearance", ("user_1", "dark")) in service.calls
    assert ("delete_github_connection", "user_1") in service.calls


def test_browser_project_and_chat_write_routes_delegate_with_valid_csrf(api_client):
    client, service, settings = api_client
    client.cookies.set(settings.browser_session_cookie_name, "browser-session-token")
    csrf = csrf_token_for_session("browser-session-token", settings)
    headers = {
        "Origin": settings.browser_origin,
        "X-Paperbase-CSRF": csrf,
    }

    created_project = client.post(
        "/v1/browser/projects",
        json={
            "name": "Agents",
            "description": "Study agent systems",
            "github_repo": {
                "mode": "create",
                "name": "agent-lab",
                "base_branch": "main",
                "import_paths": [],
            },
        },
        headers=headers,
    )
    updated_project = client.patch(
        "/v1/browser/projects/proj_2",
        json={"name": "Agents v2", "description": "Updated instructions"},
        headers=headers,
    )
    created_chat = client.post(
        "/v1/browser/chats",
        json={"title": "Project chat", "project_id": "proj_2"},
        headers=headers,
    )
    moved_chat = client.patch(
        "/v1/browser/chats/thread_2/project",
        json={"project_id": "proj_2"},
        headers=headers,
    )
    created_python_file = client.post(
        "/v1/browser/projects/proj_2/python-files",
        headers=headers,
    )
    imported_notebook = client.post(
        "/v1/browser/projects/proj_2/notebooks",
        files={"file": ("demo.ipynb", b'{"nbformat":4}', "application/x-ipynb+json")},
        headers=headers,
    )
    updated_python_file = client.patch(
        "/v1/browser/python-files/file_1",
        json={"filename": "analysis.py", "content": "print('hello')"},
        headers=headers,
    )
    deleted_python_file = client.delete(
        "/v1/browser/python-files/file_1",
        headers=headers,
    )
    ran_python_file = client.post(
        "/v1/browser/python-files/file_1/run",
        json={"mode": "smoke"},
        headers=headers,
    )
    synced_project_repo = client.post(
        "/v1/browser/projects/proj_2/repo-sync",
        json={"commit_message": "Save analysis.py"},
        headers=headers,
    )
    deleted_chat = client.delete("/v1/browser/chats/thread_1", headers=headers)
    deleted_project = client.delete("/v1/browser/projects/proj_2", headers=headers)

    assert created_project.status_code == 200
    assert created_project.json()["project_id"] == "proj_3"
    assert updated_project.status_code == 200
    assert updated_project.json()["name"] == "Agents v2"
    assert created_chat.status_code == 200
    assert created_chat.json()["project_id"] == "proj_2"
    assert moved_chat.status_code == 200
    assert moved_chat.json()["project_id"] == "proj_2"
    assert created_python_file.status_code == 200
    assert created_python_file.json()["filename"] == "something.py"
    assert imported_notebook.status_code == 200
    assert imported_notebook.json()["file_kind"] == "notebook"
    assert updated_python_file.status_code == 200
    assert updated_python_file.json()["filename"] == "analysis.py"
    assert deleted_python_file.status_code == 200
    assert deleted_python_file.json()["deleted"] is True
    assert ran_python_file.status_code == 200
    assert ran_python_file.json()["stdout"] == "runner output"
    assert synced_project_repo.status_code == 200
    assert synced_project_repo.json()["github_repo_name"] == "paperbase-general-123abc"
    assert deleted_chat.json() == {"status": "deleted"}
    assert deleted_project.json() == {"status": "deleted"}
    assert ("create_project", ("user_1", "Agents", "Study agent systems", "create", "agent-lab")) in service.calls
    assert ("update_project", ("user_1", "proj_2", "Agents v2", "Updated instructions")) in service.calls
    assert ("create_chat_thread", ("user_1", "Project chat", "proj_2")) in service.calls
    assert ("move_chat_thread_to_project", ("user_1", "thread_2", "proj_2")) in service.calls
    assert ("create_python_file", ("user_1", "proj_2")) in service.calls
    assert (
        "import_project_notebook_upload",
        ("user_1", "proj_2", "demo.ipynb", b'{"nbformat":4}'),
    ) in service.calls
    assert ("update_python_file", ("user_1", "file_1", "analysis.py", "print('hello')")) in service.calls
    assert ("delete_python_file", ("user_1", "file_1")) in service.calls
    assert ("run_python_file", ("user_1", "file_1", "smoke")) in service.calls
    assert ("sync_project_repo", ("user_1", "proj_2", "Save analysis.py")) in service.calls
    assert ("delete_chat_thread", ("user_1", "thread_1")) in service.calls
    assert ("delete_project", ("user_1", "proj_2")) in service.calls


def test_browser_write_routes_allow_same_origin_api_host_with_valid_csrf(api_client):
    client, service, settings = api_client
    client.cookies.set(settings.browser_session_cookie_name, "browser-session-token")
    csrf = csrf_token_for_session("browser-session-token", settings)

    deleted_chat = client.delete(
        "/v1/browser/chats/thread_1",
        headers={
          "Origin": "http://testserver",
          "X-Paperbase-CSRF": csrf,
        },
    )

    assert deleted_chat.status_code == 200
    assert deleted_chat.json() == {"status": "deleted"}
    assert ("delete_chat_thread", ("user_1", "thread_1")) in service.calls


def test_browser_project_item_routes_delegate_to_service(api_client):
    client, service, settings = api_client
    client.cookies.set(settings.browser_session_cookie_name, "browser-session-token")

    items = client.get("/v1/browser/projects/proj_1/items?limit=25&offset=0&item_kind=python_file")
    python_file = client.get("/v1/browser/python-files/file_1")
    repo_sync = client.get("/v1/browser/projects/proj_1/repo-sync")

    assert items.status_code == 200
    assert items.json()["items"][0]["item_kind"] == "python_file"
    assert python_file.status_code == 200
    assert python_file.json()["content"] == "print('hello')"
    assert repo_sync.status_code == 200
    assert repo_sync.json()["repo_exists"] is False
    assert ("list_project_items", ("user_1", "proj_1", 25, 0, "python_file")) in service.calls
    assert ("get_python_file", ("user_1", "file_1")) in service.calls
    assert ("get_project_repo_sync", ("user_1", "proj_1")) in service.calls


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


def test_github_callback_success_connects_account_and_redirects_to_settings(api_client, monkeypatch):
    client, service, settings = api_client
    client.cookies.set(settings.browser_session_cookie_name, "browser-session-token")

    class FakeGitHubResponse:
        def json(self):
            return {"id": 42, "login": "octocat"}

    class FakeGitHubClient:
        async def authorize_access_token(self, request):
            return {"access_token": "github-access-token", "scope": "repo,read:user"}

        async def get(self, path, token):
            assert path == "user"
            assert token["access_token"] == "github-access-token"
            return FakeGitHubResponse()

    monkeypatch.setattr(api_module, "get_github_oauth_client", lambda: FakeGitHubClient())

    response = client.get("/v1/browser/auth/github/callback", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://paperbase.example/?auth=github_connected&settings=version-control"
    assert (
        "save_github_connection",
        ("user_1", "42", "octocat", "github-access-token", "repo,read:user"),
    ) in service.calls


def test_github_callback_failure_redirects_with_error(api_client, monkeypatch):
    client, _, settings = api_client
    client.cookies.set(settings.browser_session_cookie_name, "browser-session-token")

    class FailingGitHubClient:
        async def authorize_access_token(self, request):
            raise RuntimeError("no token")

    monkeypatch.setattr(api_module, "get_github_oauth_client", lambda: FailingGitHubClient())

    response = client.get("/v1/browser/auth/github/callback", follow_redirects=False)

    assert response.status_code == 302
    assert (
        response.headers["location"]
        == f"{settings.chat_public_url}/?auth_error=github_callback_failed&settings=version-control"
    )


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


def test_list_jobs_endpoint_delegates_to_service(api_client):
    """GET /v1/jobs?status=dead routes through the bearer-auth gate and
    forwards status + limit to the service, so an operator can triage
    dead-letters without running the CLI."""
    client, service, settings = api_client
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    response = client.get("/v1/jobs?status=dead&limit=5", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["payload"]["job_id"] == "dead_dead"
    assert ("list_jobs_by_status", ("dead", 5)) in service.calls


def test_list_jobs_endpoint_requires_bearer_token(api_client):
    """No auth → 401 / invalid auth → 403. The dead-letter view must not
    be readable anonymously."""
    client, _, _ = api_client

    missing = client.get("/v1/jobs?status=dead")
    invalid = client.get("/v1/jobs?status=dead", headers={"Authorization": "Bearer wrong"})

    assert missing.status_code == 401
    assert invalid.status_code == 403


def test_retry_job_endpoint_promotes_dead_job(api_client):
    """POST /v1/jobs/{id}/retry delegates to service.retry_dead_job; a
    dead id returns the requeued JobResponse. This is the admin unblock
    for exhausted jobs."""
    client, service, settings = api_client
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    response = client.post("/v1/jobs/job_dead_1/retry", headers=headers)

    assert response.status_code == 200
    assert response.json()["payload"]["retried"] is True
    assert ("retry_dead_job", "job_dead_1") in service.calls


def test_retry_job_endpoint_returns_404_for_unknown_job(api_client):
    """An id that isn't dead (already completed, never existed, etc.)
    returns 404 so the caller doesn't silently assume success."""
    client, service, settings = api_client
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    response = client.post("/v1/jobs/job_nonexistent/retry", headers=headers)

    assert response.status_code == 404
    assert ("retry_dead_job", "job_nonexistent") in service.calls


def test_browser_preview_route_returns_signed_url_and_no_store_cache(api_client):
    client, service, settings = api_client
    client.cookies.set(settings.browser_session_cookie_name, "browser-session-token")

    response = client.get("/v1/browser/papers/arxiv:2401.54321/preview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["paper_id"] == "arxiv:2401.54321"
    assert payload["content_type"] == "image/webp"
    assert payload["image_url"].startswith("https://signed.example/")
    assert response.headers["Cache-Control"] == "no-store"
    assert ("get_paper_preview", "arxiv:2401.54321") in service.calls


def test_browser_preview_route_requires_browser_session(api_client):
    client, _, _ = api_client

    response = client.get("/v1/browser/papers/arxiv:2401.54321/preview")

    assert response.status_code == 401


def test_browser_preview_route_returns_404_when_preview_missing(api_client):
    client, _, settings = api_client
    client.cookies.set(settings.browser_session_cookie_name, "browser-session-token")

    response = client.get("/v1/browser/papers/missing/preview")

    assert response.status_code == 404


def test_harvest_endpoint_delegates_to_service(api_client):
    """POST /v1/index/harvest queues a planner job via the service. The
    endpoint is bearer-auth'd and forwards the full payload shape."""
    client, service, settings = api_client
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    response = client.post(
        "/v1/index/harvest",
        headers=headers,
        json={
            "categories": ["cs.LG", "cs.CL"],
            "start_date": "2020-01-01",
            "end_date": "2020-06-30",
            "chunk_months": 3,
            "limit_per_window": 1000,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["job_type"] == "harvest_range"
    enqueue_calls = [c for c in service.calls if c[0] == "enqueue_harvest_range_job"]
    assert len(enqueue_calls) == 1
    assert enqueue_calls[0][1]["categories"] == ["cs.LG", "cs.CL"]
    assert enqueue_calls[0][1]["start_date"] == "2020-01-01"
    assert enqueue_calls[0][1]["end_date"] == "2020-06-30"
    assert enqueue_calls[0][1]["chunk_months"] == 3
    assert enqueue_calls[0][1]["limit_per_window"] == 1000


def test_harvest_endpoint_defaults_to_configured_ai_categories(api_client):
    """When the caller omits ``categories`` the API fills in the configured
    AI/ML arxiv list. The product scope is AI research — per issue #33, we
    don't want an ergonomics slip to accidentally default to "everything on
    arxiv"."""
    client, service, settings = api_client
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    response = client.post(
        "/v1/index/harvest",
        headers=headers,
        json={"start_date": "2020-01-01", "end_date": "2020-03-31"},
    )
    assert response.status_code == 200
    enqueue_calls = [c for c in service.calls if c[0] == "enqueue_harvest_range_job"]
    assert len(enqueue_calls) == 1
    used = enqueue_calls[0][1]["categories"]
    assert used == list(settings.arxiv_categories)
    assert "cs.LG" in used
    assert "cs.AI" in used


def test_harvest_endpoint_requires_bearer_token(api_client):
    """Bulk harvest is admin-only. No auth → 401 / invalid auth → 403."""
    client, _, _ = api_client

    body = {
        "categories": ["cs.LG"],
        "start_date": "2020-01-01",
        "end_date": "2020-03-31",
    }
    missing = client.post("/v1/index/harvest", json=body)
    invalid = client.post("/v1/index/harvest", headers={"Authorization": "Bearer wrong"}, json=body)

    assert missing.status_code == 401
    assert invalid.status_code == 403


def test_harvest_top_cited_endpoint_delegates_to_service(api_client):
    """``POST /v1/index/harvest-top-cited`` forwards request fields to
    ``service.enqueue_harvest_top_cited_job`` and returns the planner job."""
    client, service, settings = api_client
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    response = client.post(
        "/v1/index/harvest-top-cited",
        headers=headers,
        json={
            "min_citations": 100,
            "max_papers": 5000,
            "chunk_size": 25,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["job_type"] == "harvest_top_cited"
    enqueue_calls = [c for c in service.calls if c[0] == "enqueue_harvest_top_cited_job"]
    assert len(enqueue_calls) == 1
    kw = enqueue_calls[0][1]
    assert kw["min_citations"] == 100
    assert kw["max_papers"] == 5000
    assert kw["chunk_size"] == 25
    # The unenforced ``categories`` knob was dropped per PR #50 review —
    # if the endpoint silently re-introduces it the kwargs assertion above
    # would have to grow, which is enough of a signal.
    assert "categories" not in kw


def test_harvest_top_cited_endpoint_requires_bearer_token(api_client):
    """Bulk harvest is admin-only. No auth → 401 / invalid auth → 403."""
    client, _, _ = api_client

    body = {"min_citations": 100}
    missing = client.post("/v1/index/harvest-top-cited", json=body)
    invalid = client.post(
        "/v1/index/harvest-top-cited",
        headers={"Authorization": "Bearer wrong"},
        json=body,
    )

    assert missing.status_code == 401
    assert invalid.status_code == 403


def test_enrich_missing_metadata_endpoint_delegates_to_service(api_client):
    """``POST /v1/index/enrich-missing-metadata`` queues an
    ``enrich_missing_metadata`` job via
    ``service.enqueue_enrich_missing_metadata_job`` (#38). Asynchronous so
    a corpus-scale backfill doesn't tie up the serving container."""
    client, service, settings = api_client
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    response = client.post(
        "/v1/index/enrich-missing-metadata?limit=2000&min_age_seconds=600",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["job_type"] == "enrich_missing_metadata"
    enqueue_calls = [c for c in service.calls if c[0] == "enqueue_enrich_missing_metadata_job"]
    assert len(enqueue_calls) == 1
    assert enqueue_calls[0][1] == (2000, 600)


def test_enrich_missing_metadata_endpoint_uses_defaults(api_client):
    """No query params → server defaults (limit=50_000, min_age_seconds=3600).
    Default min-age guards against racing inline enrichment on freshly
    ingested papers."""
    client, service, settings = api_client
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    response = client.post("/v1/index/enrich-missing-metadata", headers=headers)
    assert response.status_code == 200
    enqueue_calls = [c for c in service.calls if c[0] == "enqueue_enrich_missing_metadata_job"]
    assert enqueue_calls[-1][1] == (50_000, 3600)


def test_enrich_missing_metadata_endpoint_requires_bearer_token(api_client):
    """Backfill is admin-only. No auth → 401 / invalid auth → 403."""
    client, _, _ = api_client

    missing = client.post("/v1/index/enrich-missing-metadata")
    invalid = client.post(
        "/v1/index/enrich-missing-metadata",
        headers={"Authorization": "Bearer wrong"},
    )

    assert missing.status_code == 401
    assert invalid.status_code == 403


def test_harvest_venue_endpoint_delegates_to_service(api_client):
    """``POST /v1/index/harvest-venue`` forwards request fields to
    ``service.enqueue_harvest_venue_job`` and returns the planner job."""
    client, service, settings = api_client
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    response = client.post(
        "/v1/index/harvest-venue",
        headers=headers,
        json={
            "venue": "ICLR",
            "year": 2024,
            "max_papers": 1000,
            "chunk_size": 50,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["job_type"] == "harvest_venue"
    enqueue_calls = [c for c in service.calls if c[0] == "enqueue_harvest_venue_job"]
    assert len(enqueue_calls) == 1
    kw = enqueue_calls[0][1]
    assert kw["venue"] == "ICLR"
    assert kw["year"] == 2024
    assert kw["max_papers"] == 1000
    assert kw["chunk_size"] == 50


def test_harvest_venue_endpoint_rejects_missing_venue(api_client):
    """Missing or empty ``venue`` is a 422 — Pydantic enforces min_length."""
    client, _, settings = api_client
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    missing_venue = client.post(
        "/v1/index/harvest-venue",
        headers=headers,
        json={"year": 2024},
    )
    assert missing_venue.status_code == 422

    bad_year = client.post(
        "/v1/index/harvest-venue",
        headers=headers,
        json={"venue": "ICLR", "year": 1800},
    )
    assert bad_year.status_code == 422


def test_harvest_venue_endpoint_requires_bearer_token(api_client):
    """Bulk harvest is admin-only. No auth → 401 / invalid auth → 403."""
    client, _, _ = api_client

    body = {"venue": "ICLR", "year": 2024}
    missing = client.post("/v1/index/harvest-venue", json=body)
    invalid = client.post(
        "/v1/index/harvest-venue",
        headers={"Authorization": "Bearer wrong"},
        json=body,
    )

    assert missing.status_code == 401
    assert invalid.status_code == 403


def test_harvest_author_endpoint_delegates_to_service(api_client):
    """``POST /v1/index/harvest-author`` forwards request fields to
    ``service.enqueue_harvest_author_job`` and returns the planner job."""
    client, service, settings = api_client
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    response = client.post(
        "/v1/index/harvest-author",
        headers=headers,
        json={
            "min_paper_count": 2,
            "min_cite_mass": 10_000,
            "max_authors": 50,
            "max_papers_per_author": 100,
            "chunk_size": 25,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["job_type"] == "harvest_author"
    enqueue_calls = [c for c in service.calls if c[0] == "enqueue_harvest_author_job"]
    assert len(enqueue_calls) == 1
    kw = enqueue_calls[0][1]
    assert kw["min_paper_count"] == 2
    assert kw["min_cite_mass"] == 10_000
    assert kw["max_authors"] == 50
    assert kw["max_papers_per_author"] == 100
    assert kw["chunk_size"] == 25


def test_harvest_author_endpoint_uses_defaults_when_omitted(api_client):
    """Defaults match the #39 spec — min_cite_mass=5000."""
    client, service, settings = api_client
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    response = client.post("/v1/index/harvest-author", headers=headers, json={})
    assert response.status_code == 200
    enqueue_calls = [c for c in service.calls if c[0] == "enqueue_harvest_author_job"]
    assert len(enqueue_calls) == 1
    kw = enqueue_calls[0][1]
    assert kw["min_cite_mass"] == 5_000
    assert kw["max_authors"] == 100
    assert kw["chunk_size"] == 50


def test_harvest_author_endpoint_requires_bearer_token(api_client):
    """Bulk harvest is admin-only. No auth → 401 / invalid auth → 403."""
    client, _, _ = api_client

    missing = client.post("/v1/index/harvest-author", json={})
    invalid = client.post(
        "/v1/index/harvest-author",
        headers={"Authorization": "Bearer wrong"},
        json={},
    )
    assert missing.status_code == 401
    assert invalid.status_code == 403


# --------------------------------------------------------------------------- #
# Paper-code retrieval endpoints (#5 PR 2)
# --------------------------------------------------------------------------- #


def test_paper_code_endpoints_delegate_to_service(api_client):
    client, service, settings = api_client
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    response = client.get("/v1/papers/paper_xyz/code/files", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["paper_id"] == "paper_xyz"
    assert payload["eligibility"]["eligible"] is True
    assert payload["files"][0]["repo_path"] == "README.md"
    assert ("list_paper_code_files", "paper_xyz") in service.calls

    response = client.get("/v1/papers/paper_xyz/code/files/src/mod.py", headers=headers)
    assert response.status_code == 200
    assert response.json()["repo_path"] == "src/mod.py"
    assert ("read_paper_code_file", ("paper_xyz", "src/mod.py")) in service.calls

    response = client.get(
        "/v1/papers/paper_xyz/code/search",
        params={"q": "transformer", "limit": 3},
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "transformer"
    assert payload["hits"][0]["chunk_kind"] == "code_readme"
    assert ("search_paper_code", ("paper_xyz", "transformer", 3)) in service.calls

    response = client.get(
        "/v1/papers/paper_xyz/code/context",
        params={"q": "transformer"},
        headers=headers,
    )
    assert response.status_code == 200
    assert "README.md" in response.json()["context"]
    assert ("get_paper_code_context", ("paper_xyz", "transformer", 5)) in service.calls


def test_paper_code_endpoints_404_for_unknown_paper(api_client):
    client, _, settings = api_client
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    for path in [
        "/v1/papers/paper_unknown/code/files",
        "/v1/papers/paper_unknown/code/files/README.md",
        "/v1/papers/paper_unknown/code/search?q=x",
        "/v1/papers/paper_unknown/code/context?q=x",
    ]:
        response = client.get(path, headers=headers)
        assert response.status_code == 404, f"{path} should 404 for unknown paper"


def test_paper_code_endpoints_require_bearer_auth(api_client):
    client, _, _ = api_client

    for path in [
        "/v1/papers/paper_xyz/code/files",
        "/v1/papers/paper_xyz/code/files/README.md",
        "/v1/papers/paper_xyz/code/search?q=x",
        "/v1/papers/paper_xyz/code/context?q=x",
    ]:
        response = client.get(path)
        assert response.status_code == 401, f"{path} must require bearer token"
