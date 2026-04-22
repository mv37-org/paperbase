"""Pydantic models exposed by the paperbase API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PaperMetadata(BaseModel):
    arxiv_id: str
    title: str = ""
    authors: str = ""
    abstract: str = ""
    tldr: str = ""
    summary_short: str = ""
    summary_eli5: str = ""
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
    source_type: str | None = None
    index_status: str = "discovered"
    indexed_at: datetime | None = None


class PaperResponse(PaperMetadata):
    paper_id: str


class SearchRequest(BaseModel):
    query: str
    limit: int = Field(default=20, ge=1, le=100)
    category: str | None = None
    year: str | None = None
    include_live_sources: bool = True


class SearchPaperResult(PaperResponse):
    score: float | None = None
    source: str | None = None
    tldr: str | None = None


class SearchResponse(BaseModel):
    result_set_id: str
    query: str
    papers: list[SearchPaperResult]
    expires_at: datetime


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


class JobResponse(BaseModel):
    job_id: str
    job_type: str
    status: str
    payload: dict[str, Any]
    error: str | None = None
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


class FigureResponse(BaseModel):
    paper_id: str
    figure_id: str
    caption: str
    image_url: str | None = None
    content_type: str | None = None


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
    free_responses_used: int = 0


class BrowserUserSettingsResponse(BaseModel):
    anthropic_api_key_hint: str = ""
    has_daytona_api_key: bool = False
    daytona_api_key_hint: str = ""
    has_github_connection: bool = False
    github_login: str = ""
    github_oauth_available: bool = False
    weekly_papers_review_email: bool = False


class BrowserSessionResponse(BaseModel):
    authenticated: bool
    user: BrowserUser | None = None
    remaining_free_responses: int = 0
    has_anthropic_api_key: bool = False
    settings: BrowserUserSettingsResponse = Field(default_factory=BrowserUserSettingsResponse)
    csrf_token: str | None = None


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


class SaveAnthropicKeyRequest(BaseModel):
    api_key: str = Field(min_length=8)


class SaveDaytonaKeyRequest(BaseModel):
    api_key: str = Field(min_length=8)


class UpdateNotificationsRequest(BaseModel):
    weekly_papers_review_email: bool


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


class ChatMessageResponse(BaseModel):
    message_id: str
    role: str
    content_markdown: str
    citations: list[ChatCitation] = Field(default_factory=list)
    created_at: datetime
    status: str = "completed"


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
    messages: list[ChatMessageResponse] = Field(default_factory=list)


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
    created_at: datetime
    updated_at: datetime


class ProjectResponse(ProjectSummary):
    pass


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""


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


class SendChatMessageRequest(BaseModel):
    content: str = Field(min_length=1)
    tag_filters: list[str] = Field(default_factory=list)
    include_uploads: bool = True
    include_papers: bool = True


class SendChatMessageResponse(BaseModel):
    thread: ChatThreadResponse
    assistant_message: ChatMessageResponse | None = None
    remaining_free_responses: int = 0
    has_anthropic_api_key: bool = False
    state: str = "completed"


class ChatThreadListResponse(BaseModel):
    threads: list[ChatThreadSummary] = Field(default_factory=list)
