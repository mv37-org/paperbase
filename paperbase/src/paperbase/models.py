"""Pydantic models exposed by the paperbase API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, model_validator


class PaperMetadata(BaseModel):
    arxiv_id: str
    title: str = ""
    authors: str = ""
    abstract: str = ""
    tldr: str = ""
    summary_short: str = ""
    categories: str = ""
    primary_category: str = ""
    published: str = ""
    updated: str = ""
    doi: str | None = None
    comment: str | None = None
    journal_ref: str | None = None
    pdf_url: str = ""
    html_url: str = ""
    citation_count: int = 0
    influential_citation_count: int = 0
    semantic_scholar_id: str | None = None
    code_url: str | None = None
    # Provenance + candidate history for ``code_url`` (#46). See PaperORM
    # for authoritative column docs; ``code_url_source`` in {None,
    # "extracted", "hf_papers", "admin"} tells consumers whether the URL
    # came from the ranker, Hugging Face paper pages, or a human pin.
    # Admin pins must not be overwritten by automatic re-index runs.
    code_url_source: str | None = None
    code_url_candidates: list[str] | None = None
    # Paper-code ingestion state (#5). See PaperORM.code_index_status /
    # code_is_python / code_python_reason / code_manifest for the
    # authoritative column docs.
    code_index_status: str = "none"
    code_is_python: bool | None = None
    code_python_reason: str | None = None
    code_manifest: dict[str, Any] | None = None
    # Peer-review venue signal (#39 PR B). See PaperORM.venue / venue_year /
    # venue_source for column docs.
    venue: str | None = None
    venue_year: int | None = None
    venue_source: str | None = None
    # Author-signal harvest provenance (#39 PR C). See PaperORM.top_author_s2_id.
    top_author_s2_id: str | None = None
    source_type: str | None = None
    index_status: str = "discovered"
    indexed_at: datetime | None = None


class PaperResponse(PaperMetadata):
    paper_id: str


class PaperSelectionPoint(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class PaperSelectionBox(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(ge=0, le=1)
    height: float = Field(ge=0, le=1)


class PaperSelectionRegion(BaseModel):
    points: list[PaperSelectionPoint] = Field(default_factory=list, max_length=200)
    bbox: PaperSelectionBox
    image_base64: str | None = Field(default=None, max_length=1_000_000)
    content_type: Literal["image/png", "image/webp"] | None = None


class PaperSelectionPayload(BaseModel):
    paper_id: str = Field(min_length=1, max_length=128)
    page_number: int = Field(ge=1, le=10_000)
    action: Literal["explain", "trace", "challenge", "reproduce"]
    selected_text: str | None = Field(default=None, max_length=6_000)
    nearby_text: str | None = Field(default=None, max_length=8_000)
    section_title: str | None = Field(default=None, max_length=200)
    region: PaperSelectionRegion | None = None


class PaperVoteSummary(BaseModel):
    paper_id: str
    upvote_count: int = 1
    viewer_has_upvoted: bool = False


class PaperCommentAuthor(BaseModel):
    user_id: str
    display_name: str


class PaperCommentRecord(BaseModel):
    comment_id: str
    paper_id: str
    parent_comment_id: str | None = None
    user_id: str
    author_display_name: str
    body: str
    created_at: datetime
    updated_at: datetime


class PaperCommentResponse(BaseModel):
    comment_id: str
    paper_id: str
    parent_comment_id: str | None = None
    author: PaperCommentAuthor
    body: str
    created_at: datetime
    updated_at: datetime
    replies: list["PaperCommentResponse"] = Field(default_factory=list)


class PaperCommentsResponse(BaseModel):
    paper_id: str
    comments: list[PaperCommentResponse] = Field(default_factory=list)


class PaperDiscussionResponse(BaseModel):
    paper_id: str
    upvote_count: int = 1
    viewer_has_upvoted: bool = False
    comments: list[PaperCommentResponse] = Field(default_factory=list)


class TrendingPaperCardResponse(BaseModel):
    paper_id: str
    arxiv_id: str
    title: str
    authors: str
    published: str
    html_url: str
    pdf_url: str
    image_url: str | None = None
    upvote_count: int = 1
    viewer_has_upvoted: bool = False


class TrendingPaperDetailResponse(BaseModel):
    paper_id: str
    arxiv_id: str
    title: str
    authors: str
    published: str
    primary_category: str
    categories: str
    summary_short: str
    html_url: str
    pdf_url: str
    image_url: str | None = None
    upvote_count: int = 1
    viewer_has_upvoted: bool = False
    comments: list[PaperCommentResponse] = Field(default_factory=list)


class TrendingFeedResponse(BaseModel):
    date: str
    sort: Literal["default", "upvotes"] = "default"
    page: int = 1
    page_size: int = 20
    has_next: bool = False
    has_previous: bool = False
    papers: list[TrendingPaperCardResponse] = Field(default_factory=list)


class CreatePaperCommentRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)
    parent_comment_id: str | None = None


PaperCommentResponse.model_rebuild()


class SearchRequest(BaseModel):
    query: str
    limit: int = Field(default=20, ge=1, le=100)
    category: str | None = None
    year: str | None = None
    # Deprecated compatibility flag: user-facing search now returns only
    # fully indexed local papers. Discovery belongs to harvest/index jobs.
    include_live_sources: bool = False
    # Opt-in CrossEncoder rerank pass on top-k local candidates. Slower (~1.5s
    # CPU vs ~150ms without), but recovers body-content matches that paper-level
    # search misses. Off by default to keep type-as-you-search snappy.
    rerank: bool = False
    # Persist a row in search_logs for this query (see issue #23). Default on so
    # we can build a real-user-query benchmark; disable for healthchecks,
    # internal grounding calls, or anywhere the query is synthetic.
    log_query: bool = True


class SearchPaperResult(PaperResponse):
    score: float | None = None
    source: str | None = None
    tldr: str | None = None


class SearchResponse(BaseModel):
    result_set_id: str
    query: str
    papers: list[SearchPaperResult]
    expires_at: datetime
    # Stable identifier for this search event; returned so a follow-up call to
    # POST /v1/search/{query_id}/click can attribute the click back to this
    # exact query. Null when log_query=False was passed on the request.
    query_id: str | None = None


class SearchClickRequest(BaseModel):
    paper_id: str = Field(..., min_length=1)


class SearchSectionRequest(BaseModel):
    query: str
    limit: int = Field(default=20, ge=1, le=100)
    result_set_id: str | None = None
    paper_ids: list[str] | None = None


class SearchSectionResult(BaseModel):
    paper_id: str
    arxiv_id: str
    title: str
    path: str
    chunk_kind: str
    preview: str
    score: float | None = None


class SearchSectionResponse(BaseModel):
    query: str
    sections: list[SearchSectionResult]


class ResultSetResponse(BaseModel):
    result_set_id: str
    query: str | None = None
    created_at: datetime
    expires_at: datetime
    papers: list[SearchPaperResult]


class GrepRequest(BaseModel):
    pattern: str
    limit: int = Field(default=50, ge=1, le=200)


class GrepPaperMatch(BaseModel):
    paper_id: str
    arxiv_id: str
    title: str
    match_count: int
    previews: list[str]


class GrepResponse(BaseModel):
    result_set_id: str
    parent_result_set_id: str
    pattern: str
    papers: list[GrepPaperMatch]
    expires_at: datetime


class LiteratureShellRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=4000)


class LiteratureShellResponse(BaseModel):
    command: str
    stdout: str = ""
    result_set_id: str | None = None
    parent_result_set_id: str | None = None
    papers: list[dict[str, Any]] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    matches: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False
    error: str | None = None


class JobResponse(BaseModel):
    job_id: str
    root_job_id: str | None = None
    job_type: str
    status: str
    effective_status: str | None = None
    payload: dict[str, Any]
    error: str | None = None
    attempts: int = 0
    max_attempts: int = 3
    next_run_at: datetime | None = None
    last_attempted_at: datetime | None = None
    worker_id: str | None = None
    heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    lease_token: str | None = Field(default=None, exclude=True)
    # Smaller = claimed first. Interactive on-demand work sits at -10, hourly
    # sync at 0, bulk backfill at 10.
    priority: int = 0
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def _default_effective_status(self):
        if self.effective_status is None:
            self.effective_status = self.status
        return self


class JobPaperRecord(BaseModel):
    job_id: str
    paper_id: str
    arxiv_id: str | None = None
    title: str | None = None
    outcome: str
    skip_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class AdminWorkerSummary(BaseModel):
    status: str
    stalled: bool = False
    reachable: bool = False
    source: str = "unavailable"
    last_loop_at: datetime | None = None
    retry_scheduled_count: int = 0
    dead_count: int = 0
    last_error: str | None = None
    last_terminal_error: str | None = None


class AdminJobChildRow(BaseModel):
    job_id: str
    root_job_id: str
    job_type: str
    status: str
    display_status: str
    payload: dict[str, Any]
    error: str | None = None
    attempts: int = 0
    max_attempts: int = 3
    priority: int = 0
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AdminJobOverviewRow(BaseModel):
    job_id: str
    root_job_id: str
    job_type: str
    status: str
    display_status: str
    payload: dict[str, Any]
    error: str | None = None
    attempts: int = 0
    max_attempts: int = 3
    priority: int = 0
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    is_synthetic: bool = False
    tracking_available: bool = False
    indexed_paper_count: int | None = None
    tracked_paper_count: int | None = None
    paper_outcome_counts: dict[str, int] = Field(default_factory=dict)
    latest_status_summary: str = ""
    child_job_count: int = 0
    child_jobs: list[AdminJobChildRow] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class AdminJobsOverviewResponse(BaseModel):
    jobs: list[AdminJobOverviewRow]
    worker: AdminWorkerSummary | None = None


class AdminJobPaperResponse(BaseModel):
    root_job_id: str
    job_id: str
    paper_id: str
    arxiv_id: str | None = None
    title: str | None = None
    outcome: str
    skip_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    paper: PaperResponse | None = None


class AdminJobPapersResponse(BaseModel):
    root_job_id: str
    total_count: int = 0
    offset: int = 0
    limit: int = 100
    has_more: bool = False
    outcome_counts: dict[str, int] = Field(default_factory=dict)
    skipped_reason_counts: dict[str, int] = Field(default_factory=dict)
    papers: list[AdminJobPaperResponse]


class AdminPaperJobTouchResponse(BaseModel):
    root_job_id: str
    job_id: str
    job_type: str
    status: str
    display_status: str
    payload: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    outcome: str
    skip_reason: str | None = None
    attempts: int = 0
    max_attempts: int = 3
    priority: int = 0
    record_created_at: datetime
    record_updated_at: datetime
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class FileObject(BaseModel):
    path: str
    size: int
    content_type: str | None = None


class FileListResponse(BaseModel):
    paper_id: str
    files: list[FileObject]


class FileContentResponse(BaseModel):
    paper_id: str
    path: str
    content: str


class PaperAssetResponse(BaseModel):
    paper_id: str
    path: str
    url: str
    content_type: str | None = None


class FigureResponse(BaseModel):
    paper_id: str
    figure_id: str
    caption: str
    image_url: str | None = None
    content_type: str | None = None


class PaperPreviewResponse(BaseModel):
    paper_id: str
    image_url: str
    content_type: str


class TableResponse(BaseModel):
    paper_id: str
    table_id: str
    caption: str | None = None
    rows: list[list[str]] = Field(default_factory=list)
    csv_url: str | None = None


class ManifestResponse(BaseModel):
    paper_id: str
    manifest: dict[str, Any]


class IntegrationProbeComponent(BaseModel):
    ok: bool
    backend: str
    detail: str
    target: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntegrationProbeResponse(BaseModel):
    ok: bool
    checked_at: datetime
    database: IntegrationProbeComponent
    storage: IntegrationProbeComponent


class BrowserUser(BaseModel):
    user_id: str
    email: str
    display_name: str = ""
    is_admin: bool = False


class BrowserUserSettingsResponse(BaseModel):
    openai_api_key_hint: str = ""
    has_elevenlabs_api_key: bool = False
    elevenlabs_api_key_hint: str = ""
    has_modal_credentials: bool = False
    modal_token_id_hint: str = ""
    has_github_connection: bool = False
    github_login: str = ""
    github_sync_enabled: bool = False
    github_oauth_available: bool = False
    github_manage_access_url: str = "https://github.com/settings/connections/applications"
    weekly_papers_review_email: bool = False
    appearance_theme: Literal["light", "dark"] = "light"

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_openai_key_hint(cls, value: Any):
        if isinstance(value, dict) and not value.get("openai_api_key_hint") and value.get("anthropic_api_key_hint"):
            value = dict(value)
            value["openai_api_key_hint"] = value.get("anthropic_api_key_hint") or ""
        return value

    @computed_field
    def anthropic_api_key_hint(self) -> str:
        return self.openai_api_key_hint


class BrowserSessionResponse(BaseModel):
    authenticated: bool
    user: BrowserUser | None = None
    has_openai_api_key: bool = False
    settings: BrowserUserSettingsResponse = Field(default_factory=BrowserUserSettingsResponse)
    csrf_token: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_openai_flag(cls, value: Any):
        if isinstance(value, dict) and "has_openai_api_key" not in value and "has_anthropic_api_key" in value:
            value = dict(value)
            value["has_openai_api_key"] = bool(value.get("has_anthropic_api_key"))
        return value

    @computed_field
    def has_anthropic_api_key(self) -> bool:
        return self.has_openai_api_key


class SignupRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=8)
    display_name: str = ""


class LoginRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=8)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=8)
    password: str = Field(min_length=8)


class SaveOpenAIKeyRequest(BaseModel):
    api_key: str = Field(min_length=8)


class SaveAnthropicKeyRequest(SaveOpenAIKeyRequest):
    pass


class SaveElevenLabsKeyRequest(BaseModel):
    api_key: str = Field(min_length=8)


class SaveModalCredentialsRequest(BaseModel):
    token_id: str = Field(min_length=4)
    token_secret: str = Field(min_length=8)


class UpdateNotificationsRequest(BaseModel):
    weekly_papers_review_email: bool


class UpdateAppearanceRequest(BaseModel):
    theme: Literal["light", "dark"]


class UpdateGitHubSyncSettingsRequest(BaseModel):
    enabled: bool


class UploadedDocumentResponse(BaseModel):
    document_id: str
    user_id: str
    title: str
    source_filename: str
    content_type: str
    tags: list[str] = Field(default_factory=list)
    status: str
    created_at: datetime
    updated_at: datetime


class UploadListResponse(BaseModel):
    documents: list[UploadedDocumentResponse] = Field(default_factory=list)
    available_tags: list[str] = Field(default_factory=list)


class UploadedDocumentSectionResult(BaseModel):
    document_id: str
    title: str
    path: str
    chunk_kind: str
    preview: str
    tags: list[str] = Field(default_factory=list)
    score: float | None = None


class ChatCitation(BaseModel):
    source_id: str
    source_kind: str
    title: str
    path: str
    excerpt: str
    paper_id: str | None = None
    document_id: str | None = None
    tags: list[str] = Field(default_factory=list)


def _coerce_recommended_prompts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    prompts: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = " ".join(str(item or "").split()).strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        prompts.append(text[:240])
        if len(prompts) >= 5:
            break
    return prompts


def _recommended_prompts_from_content_json(content_json: Any) -> list[str]:
    if not isinstance(content_json, dict):
        return []
    return _coerce_recommended_prompts(content_json.get("recommended_prompts"))


class ChatMessageResponse(BaseModel):
    message_id: str
    role: str
    content_markdown: str
    citations: list[ChatCitation] = Field(default_factory=list)
    recommended_prompts: list[str] = Field(default_factory=list)
    created_at: datetime
    status: str = "completed"
    kind: str = "default"


class ChatThreadItemResponse(BaseModel):
    item_id: str
    thread_id: str
    turn_id: str
    item_type: str
    role: str
    phase: Literal["commentary", "final_answer"] | None = None
    call_id: str | None = None
    name: str | None = None
    content_text: str = ""
    content_json: Any | None = None
    citations: list[ChatCitation] = Field(default_factory=list)
    created_at: datetime
    status: str = "completed"

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_message_fields(cls, value: Any):
        if not isinstance(value, dict) or "item_id" in value or "message_id" not in value:
            return value
        content_json = value.get("content_json")
        if content_json is None and value.get("recommended_prompts"):
            content_json = {"recommended_prompts": _coerce_recommended_prompts(value.get("recommended_prompts"))}
        kind = str(value.get("kind") or "default")
        role = str(value.get("role") or "assistant")
        return {
            "item_id": value.get("message_id"),
            "thread_id": value.get("thread_id") or "",
            "turn_id": value.get("turn_id") or "legacy_turn",
            "item_type": "compaction" if kind == "compaction" else "assistant_message" if role == "assistant" else "user_message",
            "role": role,
            "phase": value.get("phase"),
            "call_id": value.get("call_id"),
            "name": value.get("name"),
            "content_text": value.get("content_markdown") or value.get("content_text") or "",
            "content_json": content_json,
            "citations": value.get("citations") or [],
            "created_at": value.get("created_at"),
            "status": value.get("status") or "completed",
        }

    @computed_field
    def message_id(self) -> str:
        return self.item_id

    @computed_field
    def content_markdown(self) -> str:
        return self.content_text

    @computed_field
    def kind(self) -> str:
        if self.item_type == "compaction":
            return "compaction"
        return "default"

    @computed_field
    def recommended_prompts(self) -> list[str]:
        return _recommended_prompts_from_content_json(self.content_json)


class ChatTurnResponse(BaseModel):
    turn_id: str
    thread_id: str
    title: str = ""
    status: str = "completed"
    provider_response_id: str = ""
    started_at: datetime
    completed_at: datetime | None = None
    usage: dict[str, Any] | None = None
    diff_summary: str = ""
    changed_paths: list[str] = Field(default_factory=list)
    exec_session_ids: list[str] = Field(default_factory=list)
    items: list[ChatThreadItemResponse] = Field(default_factory=list)


class ChatThreadSummary(BaseModel):
    thread_id: str
    project_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    last_message_preview: str = ""


class ChatThreadResponse(BaseModel):
    thread_id: str
    project_id: str
    project_name: str = ""
    project_instructions: str = ""
    title: str
    created_at: datetime
    updated_at: datetime
    items: list[ChatThreadItemResponse] = Field(default_factory=list)
    turns: list[ChatTurnResponse] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_messages(cls, value: Any):
        if not isinstance(value, dict) or value.get("items"):
            return value
        raw_messages = value.get("messages")
        if not isinstance(raw_messages, list):
            return value
        thread_id = str(value.get("thread_id") or "")
        coerced_items: list[dict[str, Any]] = []
        for index, message in enumerate(raw_messages):
            if isinstance(message, BaseModel):
                message = message.model_dump(mode="python")
            if not isinstance(message, dict):
                continue
            kind = str(message.get("kind") or "default")
            role = str(message.get("role") or "assistant")
            content_json = message.get("content_json")
            if content_json is None and message.get("recommended_prompts"):
                content_json = {
                    "recommended_prompts": _coerce_recommended_prompts(message.get("recommended_prompts"))
                }
            coerced_items.append(
                {
                    "item_id": message.get("message_id") or f"legacy_msg_{index + 1}",
                    "thread_id": thread_id,
                    "turn_id": message.get("turn_id") or "legacy_turn",
                    "item_type": "compaction" if kind == "compaction" else "assistant_message" if role == "assistant" else "user_message",
                    "role": role,
                    "content_text": message.get("content_markdown") or "",
                    "content_json": content_json,
                    "citations": message.get("citations") or [],
                    "created_at": message.get("created_at"),
                    "status": message.get("status") or "completed",
                }
            )
        value = dict(value)
        value["items"] = coerced_items
        return value

    @computed_field
    def messages(self) -> list[ChatMessageResponse]:
        messages: list[ChatMessageResponse] = []
        for item in self.items:
            if item.item_type not in {"user_message", "assistant_message", "compaction"}:
                continue
            messages.append(
                ChatMessageResponse(
                    message_id=item.item_id,
                    role=item.role,
                    content_markdown=item.content_text,
                    citations=item.citations,
                    recommended_prompts=item.recommended_prompts,
                    created_at=item.created_at,
                    status=item.status,
                    kind="compaction" if item.item_type == "compaction" else "default",
                )
            )
        return messages


class CreateChatThreadRequest(BaseModel):
    title: str = ""
    project_id: str | None = None


class MoveChatThreadProjectRequest(BaseModel):
    project_id: str = Field(min_length=1)


class ProjectSummary(BaseModel):
    project_id: str
    name: str
    description: str = ""
    is_default: bool = False
    session_count: int = 0
    github_repo_url: str = ""
    created_at: datetime
    updated_at: datetime


class ProjectResponse(ProjectSummary):
    pass


class CreateProjectGitHubRepoRequest(BaseModel):
    mode: Literal["existing", "create"]
    owner: str = ""
    name: str = Field(min_length=1)
    base_branch: str = ""
    import_paths: list[str] = Field(default_factory=list)


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    github_repo: CreateProjectGitHubRepoRequest | None = None


class UpdateProjectRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""


class ProjectListResponse(BaseModel):
    projects: list[ProjectSummary] = Field(default_factory=list)


class ProjectSessionPageResponse(BaseModel):
    project_id: str
    threads: list[ChatThreadSummary] = Field(default_factory=list)
    offset: int = 0
    limit: int = 0
    total_count: int = 0
    has_more: bool = False


class PythonFileSummary(BaseModel):
    file_id: str
    project_id: str
    filename: str
    file_kind: Literal["python", "notebook", "text"] = "python"
    repo_path: str = ""
    created_at: datetime
    updated_at: datetime


class PythonFileResponse(PythonFileSummary):
    content: str = ""


class UpdatePythonFileRequest(BaseModel):
    filename: str | None = None
    content: str | None = None


class RunPythonFileRequest(BaseModel):
    mode: Literal["smoke", "full"] = "full"


class ProjectItemSummary(BaseModel):
    item_id: str
    item_kind: str
    project_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    thread_id: str | None = None
    file_id: str | None = None
    last_message_preview: str = ""
    filename: str = ""
    file_kind: Literal["python", "notebook", "text"] = "python"
    repo_path: str = ""


class ProjectItemPageResponse(BaseModel):
    project_id: str
    items: list[ProjectItemSummary] = Field(default_factory=list)
    offset: int = 0
    limit: int = 0
    total_count: int = 0
    has_more: bool = False


class ProjectPresentationExplorationSnapshot(BaseModel):
    exploration_id: str = ""
    paper_id: str = ""
    chat_thread_id: str = ""
    path: str = ""
    title: str = ""
    authors: str = ""
    preview_image_url: str = ""
    pdf_url: str = ""
    annotations: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class ProjectPresentationStartRequest(BaseModel):
    explorations: list[ProjectPresentationExplorationSnapshot] = Field(default_factory=list)
    regenerate: bool = False


class ProjectPresentationShareRequest(BaseModel):
    is_public: bool


ProjectPresentationStatus = Literal["missing", "pending", "running", "completed", "failed", "canceled"]


ProjectPresentationSlideVisualType = Literal["code", "image"]


class ProjectPresentationSlideResponse(BaseModel):
    slide_id: str
    title: str
    description: str
    visual_type: ProjectPresentationSlideVisualType = "code"
    visual_code: str = ""
    image_prompt: str = ""
    image_url: str = ""
    image_storage_path: str = Field(default="", exclude=True)


class ProjectPresentationProgressResponse(BaseModel):
    phase_key: str = "idle"
    phase_label: str = "Presentation not started"
    detail: str = ""
    percent: int = 0
    estimated_seconds_remaining: int = 0
    can_leave: bool = True
    email_notice: str = "We will email you once it is done. You can leave this page."
    updated_at: datetime | None = None


class ProjectPresentationResponse(BaseModel):
    presentation_id: str = ""
    project_id: str
    status: ProjectPresentationStatus = "missing"
    visual_code: str = ""
    summary_markdown: str = ""
    slides: list[ProjectPresentationSlideResponse] = Field(default_factory=list)
    audio_url: str = ""
    audio_content_type: str = "audio/mpeg"
    audio_storage_path: str = Field(default="", exclude=True)
    is_public: bool = False
    share_token: str | None = None
    share_url: str = ""
    error: str = ""
    job_id: str | None = None
    progress: ProjectPresentationProgressResponse = Field(default_factory=ProjectPresentationProgressResponse)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


class RunPythonFileResponse(BaseModel):
    file_id: str
    status: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    started_at: datetime
    finished_at: datetime


class GitHubRepoBranchSummary(BaseModel):
    name: str
    commit_sha: str = ""
    is_default: bool = False


class GitHubRepoSummary(BaseModel):
    owner: str
    name: str
    full_name: str
    html_url: str = ""
    default_branch: str = "main"
    private: bool = False


class GitHubRepoListResponse(BaseModel):
    repos: list[GitHubRepoSummary] = Field(default_factory=list)


class GitHubRepoMetadataResponse(BaseModel):
    owner: str
    name: str
    html_url: str = ""
    default_branch: str = "main"
    branches: list[GitHubRepoBranchSummary] = Field(default_factory=list)


class GitHubRepoImportCandidate(BaseModel):
    path: str
    file_kind: Literal["python", "notebook", "text"]
    size: int = 0


class GitHubRepoImportCandidateResponse(BaseModel):
    owner: str
    name: str
    base_branch: str
    candidates: list[GitHubRepoImportCandidate] = Field(default_factory=list)


class ProjectRepoSyncRequest(BaseModel):
    commit_message: str = Field(min_length=1)
    expected_remote_head_sha: str | None = None
    conflict_resolutions: list["ProjectRepoConflictResolution"] = Field(default_factory=list)


class ProjectRepoConflictResolution(BaseModel):
    path: str = Field(min_length=1)
    content: str = ""


class ProjectRepoConflictFile(BaseModel):
    path: str
    file_kind: Literal["python", "notebook", "text"] = "python"
    base_content: str = ""
    local_content: str = ""
    remote_content: str = ""


class ProjectRepoConflictDetail(BaseModel):
    code: str = "github_merge_conflict"
    message: str
    project_id: str
    repo_exists: bool = True
    github_owner_login: str = ""
    github_repo_name: str = ""
    github_repo_url: str = ""
    base_branch: str = "main"
    default_branch: str = "main"
    base_commit_sha: str = ""
    remote_head_sha: str = ""
    conflicts: list[ProjectRepoConflictFile] = Field(default_factory=list)


class ProjectRepoSyncResponse(BaseModel):
    project_id: str
    repo_exists: bool = False
    github_owner_login: str = ""
    github_repo_name: str = ""
    github_repo_url: str = ""
    base_branch: str = "main"
    default_branch: str = "main"
    last_sync_commit_sha: str = ""
    last_sync_commit_url: str = ""
    last_synced_at: datetime | None = None


class SendChatMessageRequest(BaseModel):
    content: str = Field(min_length=1)
    tag_filters: list[str] = Field(default_factory=list)
    include_uploads: bool = True
    include_papers: bool = True
    # Paper IDs the user @-mentioned in the composer. The send path resolves
    # each id and fetches+indexes it from arXiv if it is not already at
    # index_status='indexed', so the chat turn always sees a fully indexed
    # paper for anything the user tagged.
    paper_ids: list[str] = Field(default_factory=list)
    paper_selection: PaperSelectionPayload | None = None


class SendChatMessageResponse(BaseModel):
    thread: ChatThreadResponse
    turn: ChatTurnResponse | None = None
    final_item: ChatThreadItemResponse | None = None
    has_openai_api_key: bool = False
    state: str = "completed"

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_send_chat_response(cls, value: Any):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        if "has_openai_api_key" not in value and "has_anthropic_api_key" in value:
            value["has_openai_api_key"] = bool(value.get("has_anthropic_api_key"))
        if value.get("final_item") is None and isinstance(value.get("assistant_message"), dict):
            assistant_message = dict(value["assistant_message"])
            kind = str(assistant_message.get("kind") or "default")
            role = str(assistant_message.get("role") or "assistant")
            thread_id = ""
            if isinstance(value.get("thread"), dict):
                thread_id = str(value["thread"].get("thread_id") or "")
            value["final_item"] = {
                "item_id": assistant_message.get("message_id") or "legacy_msg",
                "thread_id": thread_id,
                "turn_id": assistant_message.get("turn_id") or "legacy_turn",
                "item_type": "compaction" if kind == "compaction" else "assistant_message" if role == "assistant" else "user_message",
                "role": role,
                "content_text": assistant_message.get("content_markdown") or "",
                "content_json": assistant_message.get("content_json")
                or (
                    {"recommended_prompts": _coerce_recommended_prompts(assistant_message.get("recommended_prompts"))}
                    if assistant_message.get("recommended_prompts")
                    else None
                ),
                "citations": assistant_message.get("citations") or [],
                "created_at": assistant_message.get("created_at"),
                "status": assistant_message.get("status") or "completed",
            }
        return value

    @computed_field
    def assistant_message(self) -> ChatMessageResponse | None:
        if self.final_item is not None and self.final_item.item_type in {"user_message", "assistant_message", "compaction"}:
            return ChatMessageResponse(
                message_id=self.final_item.item_id,
                role=self.final_item.role,
                content_markdown=self.final_item.content_text,
                citations=self.final_item.citations,
                recommended_prompts=self.final_item.recommended_prompts,
                created_at=self.final_item.created_at,
                status=self.final_item.status,
                kind="compaction" if self.final_item.item_type == "compaction" else "default",
            )
        assistant_messages = [
            message
            for message in self.thread.messages
            if message.role == "assistant" or message.kind == "compaction"
        ]
        return assistant_messages[-1] if assistant_messages else None

    @computed_field
    def has_anthropic_api_key(self) -> bool:
        return self.has_openai_api_key


class ChatThreadListResponse(BaseModel):
    threads: list[ChatThreadSummary] = Field(default_factory=list)


class UpdateChatThreadRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)


class CreateChatFeedbackRequest(BaseModel):
    message_id: str = Field(min_length=1)
    sentiment: Literal["positive", "negative"]
    reasons: list[str] = Field(default_factory=list)
    note: str = ""
    debug_bundle: dict[str, Any] | None = None


class ChatFeedbackResponse(BaseModel):
    feedback_id: str
    thread_id: str
    message_id: str
    sentiment: str
    reasons: list[str] = Field(default_factory=list)
    note: str = ""
    debug_bundle: dict[str, Any] | None = None
    created_at: datetime


# --------------------------------------------------------------------------- #
# Paper-code retrieval (#5 PR 2)
# --------------------------------------------------------------------------- #


class CodeEligibility(BaseModel):
    """Structured gate on the paper-code retrieval primitives.

    ``eligible`` is ``True`` iff the paper's linked repo has been ingested,
    classified as Python, and currently sits in ``code_index_status='indexed'``.
    Anything else surfaces as ``eligible=False`` with the underlying status
    and classifier reason so agents / UIs can distinguish "not yet fetched"
    from "fetched and rejected as non-Python" without a second round-trip.
    """

    eligible: bool
    status: str = "none"
    reason: str | None = None


class CodeFileSummary(BaseModel):
    repo_path: str
    size_bytes: int = 0
    indexed: bool = False


class CodeFileListResponse(BaseModel):
    paper_id: str
    eligibility: CodeEligibility
    resolved_sha: str | None = None
    files: list[CodeFileSummary] = Field(default_factory=list)


class AdminPaperDetailResponse(BaseModel):
    paper: PaperResponse
    jobs: list[AdminPaperJobTouchResponse] = Field(default_factory=list)
    files: list[FileObject] = Field(default_factory=list)
    code_files: CodeFileListResponse | None = None


class CodeReadResponse(BaseModel):
    paper_id: str
    eligibility: CodeEligibility
    repo_path: str
    content: str = ""
    size_bytes: int = 0


class CodeSearchHit(BaseModel):
    repo_path: str
    chunk_kind: str
    preview: str
    score: float = 0.0


class CodeSearchResponse(BaseModel):
    paper_id: str
    eligibility: CodeEligibility
    query: str = ""
    hits: list[CodeSearchHit] = Field(default_factory=list)


class CodeContextResponse(BaseModel):
    paper_id: str
    eligibility: CodeEligibility
    query: str = ""
    context: str = ""
    hits: list[CodeSearchHit] = Field(default_factory=list)
