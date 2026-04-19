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
