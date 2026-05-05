"""Metadata, result-set, and job persistence for paperbase."""

from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any, TypeVar

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    func,
    or_,
    select,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import Settings
from .models import (
    BrowserUser,
    BrowserUserSettingsResponse,
    ChatCitation,
    ChatMessageResponse,
    ChatThreadItemResponse,
    ChatThreadResponse,
    ChatThreadSummary,
    ChatTurnResponse,
    GrepPaperMatch,
    IntegrationProbeComponent,
    JobPaperRecord,
    JobResponse,
    PaperMetadata,
    PaperResponse,
    ProjectItemPageResponse,
    ProjectItemSummary,
    ProjectRepoSyncResponse,
    ProjectResponse,
    ProjectSessionPageResponse,
    ProjectSummary,
    PythonFileResponse,
    PythonFileSummary,
    ResultSetResponse,
    UploadedDocumentResponse,
    UploadedDocumentSectionResult,
    SearchPaperResult,
    SearchSectionResult,
)
from .utils import canonical_arxiv_id, canonical_paper_id, literal_hint_from_pattern, new_id, utcnow


logger = logging.getLogger(__name__)


# Postgres SQLSTATE for ``deadlock_detected``. We retry the loser of a
# deadlock cycle a few times so a transient AB-BA contention on
# ``papers`` ↔ ``text_chunks`` row locks (#51) self-heals inside the
# repository layer instead of bubbling up to the chunk worker and
# burning a whole retry attempt's worth of arxiv/S2 quota.
_DEADLOCK_SQLSTATE = "40P01"


_T = TypeVar("_T")


def _is_deadlock_error(exc: OperationalError) -> bool:
    pgcode = getattr(getattr(exc, "orig", None), "sqlstate", None)
    return pgcode == _DEADLOCK_SQLSTATE


def _retry_on_deadlock(
    fn: Callable[[], _T],
    *,
    max_attempts: int = 4,
    base_backoff_seconds: float = 0.05,
    label: str = "",
    sleep_fn: Callable[[float], None] = time.sleep,
) -> _T:
    """Run ``fn()`` and retry on Postgres deadlock detection.

    Postgres aborts ONE side of a deadlock cycle; that side raises
    ``DeadlockDetected`` (SQLSTATE 40P01, surfaced through SQLAlchemy
    as ``OperationalError``). The other side's transaction has already
    committed and released its locks by the time we observe the error,
    so retrying the loser usually succeeds on the next attempt.

    Used by ``upsert_paper`` and ``replace_text_chunks`` (#51): under
    tight indexing loops the same Python process can hold two pooled
    connections in concurrent transactions that contend on the same
    ``papers`` row's FK lock. Without this retry the failure bubbles
    up to the chunk worker, which burns an entire retry attempt
    (re-fetching arxiv + S2 for every paper in the batch) just to
    re-do work the deadlock-loser had already half-completed.
    """
    last_exc: OperationalError | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except OperationalError as exc:
            if not _is_deadlock_error(exc):
                raise
            last_exc = exc
            if attempt + 1 >= max_attempts:
                break
            sleep_for = base_backoff_seconds * (2 ** attempt)
            logger.warning(
                "Postgres deadlock; retrying repository call",
                extra={
                    "event": "repo.deadlock_retry",
                    "label": label,
                    "attempt": attempt + 1,
                    "sleep_seconds": sleep_for,
                },
            )
            sleep_fn(sleep_for)
    assert last_exc is not None
    raise last_exc


class Base(DeclarativeBase):
    pass


# Chunk-kind namespace split (#5 PR 2). Paper search must never surface code
# chunks: a query like "transformer attention" should return the paper's
# methodology section, not a random README snippet of a linked repo that
# happens to mention "attention". Code retrieval lives on its own
# paper-scoped primitives (`search_paper_code_sections`) and operates on a
# disjoint kind set. Any future chunk kind must be opted into exactly one of
# these two buckets.
PAPER_SECTION_CHUNK_KINDS: tuple[str, ...] = ("section", "table", "figure_caption")
CODE_CHUNK_KINDS: tuple[str, ...] = (
    "code_readme",
    "code_doc",
    "code_manifest",
    "code_entrypoint",
    "code_signature",
)


class PaperORM(Base):
    __tablename__ = "papers"

    paper_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    arxiv_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    authors: Mapped[str] = mapped_column(Text, default="")
    abstract: Mapped[str] = mapped_column(Text, default="")
    summary_short: Mapped[str] = mapped_column(Text, default="")
    summary_eli5: Mapped[str] = mapped_column(Text, default="")
    categories: Mapped[str] = mapped_column(Text, default="")
    primary_category: Mapped[str] = mapped_column(String(64), default="")
    published: Mapped[str] = mapped_column(String(32), default="")
    updated: Mapped[str] = mapped_column(String(32), default="")
    doi: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    journal_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_url: Mapped[str] = mapped_column(Text, default="")
    html_url: Mapped[str] = mapped_column(Text, default="")
    citation_count: Mapped[int] = mapped_column(Integer, default=0)
    influential_citation_count: Mapped[int] = mapped_column(Integer, default=0)
    semantic_scholar_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    code_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Paper-code ingestion state (#5). code_index_status transitions through
    # none → pending → indexing → indexed / skipped / failed / stale as the
    # ingest_repo worker processes the paper's code_url. code_is_python is
    # the Python-eligibility verdict from paperbase.code_ingest; None means
    # the repo has not yet been classified. code_manifest carries the full
    # manifest dict (scanned paths, entrypoints, classification reason, etc.)
    # so later tools do not have to re-scan the archive.
    code_index_status: Mapped[str] = mapped_column(String(16), default="none")
    code_is_python: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    code_python_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    code_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Peer-review venue signal (#39 PR B). venue is the S2 canonical venue
    # string (e.g. "International Conference on Learning Representations").
    # venue_year is the conference year. venue_source records provenance
    # ("s2_bulk", "manual", etc.) so a future OpenReview enrichment pass
    # can know which rows it is allowed to overwrite.
    venue: Mapped[str | None] = mapped_column(Text, nullable=True)
    venue_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    venue_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    index_status: Mapped[str] = mapped_column(String(32), default="discovered")
    search_document: Mapped[str] = mapped_column(Text, default="")
    tldr: Mapped[str] = mapped_column(Text, default="")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    indexed_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    chunks: Mapped[list["TextChunkORM"]] = relationship(back_populates="paper", cascade="all, delete-orphan")


class TextChunkORM(Base):
    __tablename__ = "text_chunks"

    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.paper_id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(Text)
    chunk_kind: Mapped[str] = mapped_column(String(32), default="section")
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    section_depth: Mapped[int] = mapped_column(Integer, default=1)
    section_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)

    paper: Mapped[PaperORM] = relationship(back_populates="chunks")


class PaperRefORM(Base):
    __tablename__ = "paper_refs"

    src_paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.paper_id", ondelete="CASCADE"),
        primary_key=True,
    )
    dst_arxiv_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dst_paper_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ref_type: Mapped[str] = mapped_column(String(32), default="citation")
    rank: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResultSetORM(Base):
    __tablename__ = "result_sets"

    result_set_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_result_set_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pattern: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[Any] = mapped_column(DateTime(timezone=True), index=True)


class ResultItemORM(Base):
    __tablename__ = "result_items"

    result_item_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    result_set_id: Mapped[str] = mapped_column(ForeignKey("result_sets.result_set_id", ondelete="CASCADE"), index=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.paper_id", ondelete="CASCADE"), index=True)
    rank: Mapped[int] = mapped_column(Integer, index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tldr: Mapped[str | None] = mapped_column(Text, nullable=True)


# Per-job-type default max_attempts. Longer-running / more-expensive jobs
# get fewer retries (manual re-run is cheaper than auto-spamming). Jobs not
# in this map fall back to 3 — the same default encoded on the ORM column.
_JOB_TYPE_MAX_ATTEMPTS: dict[str, int] = {
    "index_paper": 3,
    "sync_latest": 5,
    "enrich_missing_metadata": 5,
    "reindex_missing_embeddings": 2,
    "reindex_embeddings": 2,
    "summarize_top_cited": 2,
    "harvest_range": 3,
    "harvest_chunk": 5,
    "harvest_top_cited": 3,  # planner — paginates S2, enqueues harvest_chunk per batch
    "harvest_venue": 3,  # planner — paginates S2 by venue+year, enqueues harvest_chunk per batch
}


def default_max_attempts(job_type: str) -> int:
    return _JOB_TYPE_MAX_ATTEMPTS.get(job_type, 3)


# Per-job-type priority for claim ordering. Lower values are claimed first,
# so interactive/on-demand work never waits behind a long backfill run.
# Jobs not in this map fall back to 0 — the same default encoded on the ORM.
_JOB_TYPE_PRIORITY: dict[str, int] = {
    "index_paper": -10,
    "sync_latest": 0,
    "enrich_missing_metadata": 0,
    "reindex_missing_embeddings": 0,
    "reindex_embeddings": 0,
    "summarize_top_cited": 0,
    "harvest_range": 5,
    "harvest_chunk": 10,
    "harvest_top_cited": 5,  # planner; same priority tier as harvest_range
    "harvest_venue": 5,  # planner; same priority tier as harvest_range
}


def default_priority(job_type: str) -> int:
    return _JOB_TYPE_PRIORITY.get(job_type, 0)


class JobORM(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    root_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # `pending`, `running`, `completed`, or `dead`. The old `failed` terminal
    # status still shows up on rows written before #32; admin retry still
    # accepts those rows for compatibility.
    status: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    # When NULL, the job is claimable immediately. Set by the retry loop to
    # schedule the next attempt after backoff.
    next_run_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_attempted_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Claim ordering: smaller = higher priority. Default 0 keeps legacy rows
    # behaving like everything else; new bulk-harvest jobs set a higher value
    # so they can't starve interactive on-demand work.
    priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobPaperORM(Base):
    __tablename__ = "job_papers"

    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.job_id"), primary_key=True)
    paper_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), default="indexed", index=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SearchLogORM(Base):
    """Append-only log of /v1/search calls — groundwork for the real-user-query
    benchmark in #21 and for diagnosing ranking regressions at scale.

    Stores query text, the top paper IDs returned, whether the rerank path was
    used, request latency, and (updated later, when the UI sends it) which
    paper the user actually clicked on. Never stores tokens or other auth
    material — see service.search for the logging discipline.
    """

    __tablename__ = "search_logs"

    query_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    query_text: Mapped[str] = mapped_column(Text)
    request_rerank: Mapped[bool] = mapped_column(default=False)
    top_paper_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    result_set_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clicked_paper_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    clicked_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


_SUGGEST_HIGHLIGHT_RADIUS = 80
_SUGGEST_HIGHLIGHT_FALLBACK = 160


def _extract_highlight(text_value: str | None, query: str) -> str:
    """Produce a short snippet of ``text_value`` anchored on ``query``.

    Case-insensitive substring search; if the query isn't a literal substring
    (fuzzy-only trigram match), return the leading slice so the frontend still
    has something coherent to render.
    """
    text_value = (text_value or "").strip()
    if not text_value:
        return ""
    haystack = text_value.lower()
    needle = query.strip().lower()
    if needle and needle in haystack:
        idx = haystack.find(needle)
        start = max(0, idx - _SUGGEST_HIGHLIGHT_RADIUS)
        end = min(len(text_value), idx + len(needle) + _SUGGEST_HIGHLIGHT_RADIUS)
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(text_value) else ""
        return f"{prefix}{text_value[start:end].strip()}{suffix}"
    return text_value[:_SUGGEST_HIGHLIGHT_FALLBACK].strip()


def _suggest_row_to_payload(q: str, row: Any) -> dict[str, Any]:
    matched_field = row["matched_field"]
    if matched_field == "authors":
        highlight_source = row.get("authors")
    elif matched_field == "summary":
        highlight_source = row.get("summary_short") or row.get("tldr")
    else:
        # title_prefix or title
        highlight_source = row.get("title")
    return {
        "paper_id": row["paper_id"],
        "arxiv_id": row["arxiv_id"],
        "title": row["title"],
        "authors": row["authors"],
        "summary_short": row.get("summary_short") or row.get("tldr") or "",
        "citation_count": row.get("citation_count") or 0,
        "published": row.get("published") or "",
        "score": float(row.get("score") or 0.0),
        "matched_field": matched_field,
        "highlight_snippet": _extract_highlight(highlight_source, q),
    }


def _fallback_row_to_payload(row: Any) -> dict[str, Any]:
    summary_text = row.get("summary_short") or row.get("tldr") or ""
    highlight_source = summary_text or row.get("title") or ""
    return {
        "paper_id": row["paper_id"],
        "arxiv_id": row["arxiv_id"],
        "title": row["title"],
        "authors": row.get("authors") or "",
        "summary_short": summary_text,
        "citation_count": row.get("citation_count") or 0,
        "published": row.get("published") or "",
        "score": float(row.get("score") or 0.0),
        "matched_field": "fallback",
        "highlight_snippet": _extract_highlight(highlight_source, ""),
    }


def _resolve_unique_python_filename(
    filename: str,
    existing_filenames: Iterable[str],
    *,
    exclude_filename: str | None = None,
) -> str:
    taken = {value for value in existing_filenames if value and value != exclude_filename}
    if filename not in taken:
        return filename
    stem = filename[:-3]
    index = 2
    candidate = f"{stem}_{index}.py"
    while candidate in taken:
        index += 1
        candidate = f"{stem}_{index}.py"
    return candidate


def _resolve_unique_project_filename(
    filename: str,
    existing_filenames: Iterable[str],
    *,
    exclude_filename: str | None = None,
) -> str:
    taken = {value for value in existing_filenames if value and value != exclude_filename}
    if filename not in taken:
        return filename
    if "." in filename:
        stem, suffix = filename.rsplit(".", 1)
        suffix = f".{suffix}"
    else:
        stem, suffix = filename, ""
    index = 2
    candidate = f"{stem}_{index}{suffix}"
    while candidate in taken:
        index += 1
        candidate = f"{stem}_{index}{suffix}"
    return candidate


def _filename_from_repo_path(repo_path: str) -> str:
    return PurePosixPath(repo_path).name


def _resolve_unique_repo_path(
    repo_path: str,
    existing_repo_paths: Iterable[str],
    *,
    exclude_repo_path: str | None = None,
) -> str:
    taken = {value for value in existing_repo_paths if value and value != exclude_repo_path}
    if repo_path not in taken:
        return repo_path
    path = PurePosixPath(repo_path)
    parent = "" if str(path.parent) == "." else str(path.parent)
    filename = path.name
    if "." in filename:
        stem, suffix = filename.rsplit(".", 1)
        suffix = f".{suffix}"
    else:
        stem, suffix = filename, ""
    index = 2
    candidate_name = f"{stem}_{index}{suffix}"
    candidate_path = f"{parent}/{candidate_name}" if parent else candidate_name
    while candidate_path in taken:
        index += 1
        candidate_name = f"{stem}_{index}{suffix}"
        candidate_path = f"{parent}/{candidate_name}" if parent else candidate_name
    return candidate_path


def _repo_path_for_filename(filename: str, file_kind: str, repo_path: str | None = None) -> str:
    if repo_path:
        return repo_path
    if file_kind == "notebook":
        return f"notebooks/{filename}"
    return f"src/{filename}"


def _repo_path_with_filename(repo_path: str, filename: str) -> str:
    if "/" not in repo_path:
        return filename
    prefix = repo_path.rsplit("/", 1)[0]
    return f"{prefix}/{filename}"


class BrowserUserORM(Base):
    __tablename__ = "browser_users"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(Text, default="")
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class GoogleIdentityORM(Base):
    __tablename__ = "google_identities"

    google_identity_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("browser_users.user_id", ondelete="CASCADE"), index=True)
    google_subject: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), default="")
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)


class BrowserSessionORM(Base):
    __tablename__ = "browser_sessions"

    browser_session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("browser_users.user_id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[Any] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)


class PasswordResetTokenORM(Base):
    __tablename__ = "password_reset_tokens"

    password_reset_token_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("browser_users.user_id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[Any] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnthropicAPIKeyORM(Base):
    __tablename__ = "anthropic_api_keys"

    anthropic_api_key_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("browser_users.user_id", ondelete="CASCADE"), unique=True, index=True
    )
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    api_key_hint: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class OpenAIAPIKeyORM(Base):
    __tablename__ = "openai_api_keys"

    openai_api_key_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("browser_users.user_id", ondelete="CASCADE"), unique=True, index=True
    )
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    api_key_hint: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BrowserUserSettingsORM(Base):
    __tablename__ = "browser_user_settings"

    browser_user_settings_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("browser_users.user_id", ondelete="CASCADE"), unique=True, index=True
    )
    encrypted_daytona_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    daytona_api_key_hint: Mapped[str] = mapped_column(String(32), default="")
    encrypted_modal_token_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_modal_token_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    modal_token_id_hint: Mapped[str] = mapped_column(String(32), default="")
    weekly_papers_review_email: Mapped[bool] = mapped_column(Boolean, default=False)
    appearance_theme: Mapped[str] = mapped_column(String(16), default="light")
    github_user_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    github_login: Mapped[str] = mapped_column(String(255), default="")
    encrypted_github_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_scope: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UploadedDocumentORM(Base):
    __tablename__ = "uploaded_documents"

    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("browser_users.user_id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    source_filename: Mapped[str] = mapped_column(Text, default="")
    content_type: Mapped[str] = mapped_column(String(255), default="application/octet-stream")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="ready")
    storage_path: Mapped[str] = mapped_column(Text, default="")
    extracted_text_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UploadedDocumentChunkORM(Base):
    __tablename__ = "uploaded_document_chunks"

    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("uploaded_documents.document_id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(Text)
    chunk_kind: Mapped[str] = mapped_column(String(32), default="section")
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProjectORM(Base):
    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("browser_users.user_id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text, default="General")
    description: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChatThreadORM(Base):
    __tablename__ = "chat_threads"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("browser_users.user_id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    project_name: Mapped[str] = mapped_column(Text, default="")
    project_instructions: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(Text, default="New chat")
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PythonFileORM(Base):
    __tablename__ = "python_files"

    file_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("browser_users.user_id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(Text, default="something.py")
    file_kind: Mapped[str] = mapped_column(String(32), default="python")
    repo_path: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ProjectRepoSyncORM(Base):
    __tablename__ = "project_repo_syncs"

    project_repo_sync_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("browser_users.user_id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    github_owner_login: Mapped[str] = mapped_column(String(255), default="")
    github_repo_name: Mapped[str] = mapped_column(String(255), default="")
    github_repo_url: Mapped[str] = mapped_column(Text, default="")
    base_branch: Mapped[str] = mapped_column(String(255), default="main")
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    last_sync_commit_sha: Mapped[str] = mapped_column(String(255), default="")
    last_sync_commit_url: Mapped[str] = mapped_column(Text, default="")
    last_synced_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChatMessageORM(Base):
    __tablename__ = "chat_messages"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("chat_threads.thread_id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32), index=True)
    content_markdown: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="completed")
    kind: Mapped[str] = mapped_column(String(32), default="default")
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    archived_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ChatTurnORM(Base):
    __tablename__ = "chat_turns"

    turn_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("chat_threads.thread_id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    provider_response_id: Mapped[str] = mapped_column(String(128), default="")
    started_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    diff_summary: Mapped[str] = mapped_column(Text, default="")
    changed_paths: Mapped[list[str]] = mapped_column(JSON, default=list)
    exec_session_ids: Mapped[list[str]] = mapped_column(JSON, default=list)


class ChatThreadItemORM(Base):
    __tablename__ = "chat_thread_items"

    item_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("chat_threads.thread_id", ondelete="CASCADE"), index=True)
    turn_id: Mapped[str] = mapped_column(ForeignKey("chat_turns.turn_id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0, index=True)
    item_type: Mapped[str] = mapped_column(String(64), default="other")
    role: Mapped[str] = mapped_column(String(32), default="assistant")
    phase: Mapped[str | None] = mapped_column(String(32), nullable=True)
    call_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    content_text: Mapped[str] = mapped_column(Text, default="")
    content_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    citations_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)


def _paper_from_orm(row: PaperORM) -> PaperResponse:
    return PaperResponse(
        paper_id=row.paper_id,
        arxiv_id=row.arxiv_id,
        title=row.title,
        authors=row.authors,
        abstract=row.abstract,
        tldr=row.tldr,
        summary_short=row.summary_short,
        summary_eli5=row.summary_eli5,
        categories=row.categories,
        primary_category=row.primary_category,
        published=row.published,
        updated=row.updated,
        doi=row.doi,
        comment=row.comment,
        journal_ref=row.journal_ref,
        pdf_url=row.pdf_url,
        html_url=row.html_url,
        citation_count=row.citation_count,
        influential_citation_count=row.influential_citation_count,
        semantic_scholar_id=row.semantic_scholar_id,
        code_url=row.code_url,
        code_index_status=row.code_index_status or "none",
        code_is_python=row.code_is_python,
        code_python_reason=row.code_python_reason,
        code_manifest=row.code_manifest,
        venue=row.venue,
        venue_year=row.venue_year,
        venue_source=row.venue_source,
        source_type=row.source_type,
        index_status=row.index_status,
        indexed_at=row.indexed_at,
    )


def _paper_needs_summary_after(paper: PaperResponse, after: PaperResponse) -> bool:
    if paper.influential_citation_count != after.influential_citation_count:
        return paper.influential_citation_count < after.influential_citation_count
    if paper.citation_count != after.citation_count:
        return paper.citation_count < after.citation_count
    if paper.published != after.published:
        return paper.published < after.published
    return paper.paper_id > after.paper_id


def _browser_user_from_orm(row: BrowserUserORM) -> BrowserUser:
    return BrowserUser(
        user_id=row.user_id,
        email=row.email,
        display_name=row.display_name,
    )


def _browser_user_settings_from_orm(row: BrowserUserSettingsORM | None) -> BrowserUserSettingsResponse:
    if row is None:
        return BrowserUserSettingsResponse()
    return BrowserUserSettingsResponse(
        openai_api_key_hint="",
        has_modal_credentials=bool(row.encrypted_modal_token_id and row.encrypted_modal_token_secret),
        modal_token_id_hint=row.modal_token_id_hint or "",
        has_github_connection=bool(row.encrypted_github_access_token and row.github_login),
        github_login=row.github_login or "",
        weekly_papers_review_email=bool(row.weekly_papers_review_email),
        appearance_theme=row.appearance_theme if row.appearance_theme in {"light", "dark"} else "light",
    )


def _uploaded_document_from_orm(row: UploadedDocumentORM) -> UploadedDocumentResponse:
    return UploadedDocumentResponse(
        document_id=row.document_id,
        user_id=row.user_id,
        title=row.title,
        source_filename=row.source_filename,
        content_type=row.content_type,
        tags=list(row.tags or []),
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _project_from_orm(
    row: ProjectORM,
    *,
    session_count: int = 0,
    github_repo_url: str = "",
) -> ProjectSummary:
    return ProjectSummary(
        project_id=row.project_id,
        name=row.name,
        description=row.description or "",
        is_default=bool(row.is_default),
        session_count=session_count,
        github_repo_url=github_repo_url,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _chat_thread_summary_from_orm(row: ChatThreadORM, *, last_message_preview: str = "") -> ChatThreadSummary:
    return ChatThreadSummary(
        thread_id=row.thread_id,
        project_id=row.project_id or "",
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_message_preview=last_message_preview,
    )


def _python_file_summary_from_orm(row: PythonFileORM) -> PythonFileSummary:
    file_kind = row.file_kind or "python"
    repo_path = row.repo_path or _repo_path_for_filename(row.filename, file_kind)
    return PythonFileSummary(
        file_id=row.file_id,
        project_id=row.project_id,
        filename=row.filename,
        file_kind=file_kind,  # type: ignore[arg-type]
        repo_path=repo_path,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _python_file_from_orm(row: PythonFileORM) -> PythonFileResponse:
    file_kind = row.file_kind or "python"
    repo_path = row.repo_path or _repo_path_for_filename(row.filename, file_kind)
    return PythonFileResponse(
        file_id=row.file_id,
        project_id=row.project_id,
        filename=row.filename,
        file_kind=file_kind,  # type: ignore[arg-type]
        repo_path=repo_path,
        content=row.content or "",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _project_repo_sync_from_orm(
    row: ProjectRepoSyncORM | None,
    *,
    project_id: str,
) -> ProjectRepoSyncResponse:
    if row is None:
        return ProjectRepoSyncResponse(project_id=project_id, repo_exists=False)
    return ProjectRepoSyncResponse(
        project_id=project_id,
        repo_exists=True,
        github_owner_login=row.github_owner_login or "",
        github_repo_name=row.github_repo_name or "",
        github_repo_url=row.github_repo_url or "",
        base_branch=row.base_branch or row.default_branch or "main",
        default_branch=row.default_branch or "main",
        last_sync_commit_sha=row.last_sync_commit_sha or "",
        last_sync_commit_url=row.last_sync_commit_url or "",
        last_synced_at=row.last_synced_at,
    )


def _project_item_from_thread_summary(summary: ChatThreadSummary) -> ProjectItemSummary:
    return ProjectItemSummary(
        item_id=summary.thread_id,
        item_kind="thread",
        project_id=summary.project_id,
        title=summary.title,
        created_at=summary.created_at,
        updated_at=summary.updated_at,
        thread_id=summary.thread_id,
        last_message_preview=summary.last_message_preview,
    )


def _project_item_from_python_file(summary: PythonFileSummary) -> ProjectItemSummary:
    return ProjectItemSummary(
        item_id=summary.file_id,
        item_kind="python_file",
        project_id=summary.project_id,
        title=summary.filename,
        created_at=summary.created_at,
        updated_at=summary.updated_at,
        file_id=summary.file_id,
        filename=summary.filename,
        file_kind=summary.file_kind,
        repo_path=summary.repo_path,
    )


def _chat_message_from_orm(row: ChatMessageORM) -> ChatMessageResponse:
    return ChatMessageResponse(
        message_id=row.message_id,
        role=row.role,
        content_markdown=row.content_markdown,
        citations=[ChatCitation.model_validate(citation) for citation in (row.citations or [])],
        created_at=row.created_at,
        status=row.status,
        kind=row.kind or "default",
    )


def _chat_thread_item_from_orm(row: ChatThreadItemORM) -> ChatThreadItemResponse:
    phase = row.phase if row.phase in {"commentary", "final_answer"} else None
    return ChatThreadItemResponse(
        item_id=row.item_id,
        thread_id=row.thread_id,
        turn_id=row.turn_id,
        item_type=row.item_type,
        role=row.role,
        phase=phase,  # type: ignore[arg-type]
        call_id=row.call_id,
        name=row.name,
        content_text=row.content_text or "",
        content_json=row.content_json,
        citations=[ChatCitation.model_validate(citation) for citation in (row.citations_json or [])],
        created_at=row.created_at,
        status=row.status or "completed",
    )


def _chat_turn_from_orm(row: ChatTurnORM, *, items: list[ChatThreadItemResponse] | None = None) -> ChatTurnResponse:
    return ChatTurnResponse(
        turn_id=row.turn_id,
        thread_id=row.thread_id,
        status=row.status or "completed",
        provider_response_id=row.provider_response_id or "",
        started_at=row.started_at,
        completed_at=row.completed_at,
        usage=row.usage_json,
        diff_summary=row.diff_summary or "",
        changed_paths=list(row.changed_paths or []),
        exec_session_ids=list(row.exec_session_ids or []),
        items=list(items or []),
    )


class PaperRepository(ABC):
    @abstractmethod
    def init(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def probe(self) -> IntegrationProbeComponent:
        raise NotImplementedError

    @abstractmethod
    def upsert_paper(
        self,
        paper: PaperMetadata,
        *,
        search_document: str = "",
        embedding: list[float] | None = None,
    ) -> PaperResponse:
        raise NotImplementedError

    @abstractmethod
    def upsert_paper_shallow(self, paper: PaperMetadata) -> PaperResponse:
        """Write-through for live-source hits: creates new rows if missing and
        enriches citation/tldr/s2 id fields for existing rows without ever
        touching indexed artifacts (index_status, indexed_at, search_document,
        embedding).
        """
        raise NotImplementedError

    @abstractmethod
    def get_paper(self, paper_id: str) -> PaperResponse | None:
        raise NotImplementedError

    @abstractmethod
    def get_paper_by_arxiv_id(self, arxiv_id: str) -> PaperResponse | None:
        raise NotImplementedError

    @abstractmethod
    def list_papers(
        self,
        *,
        query: str | None = None,
        index_status: str | None = None,
        limit: int = 50,
    ) -> list[PaperResponse]:
        raise NotImplementedError

    @abstractmethod
    def replace_text_chunks(self, paper_id: str, chunks: list[dict[str, Any]]) -> None:
        raise NotImplementedError

    @abstractmethod
    def update_summaries(self, paper_id: str, *, summary_short: str, summary_eli5: str) -> PaperResponse | None:
        raise NotImplementedError

    @abstractmethod
    def update_code_ingest_state(
        self,
        paper_id: str,
        *,
        status: str,
        is_python: bool | None = None,
        python_reason: str | None = None,
        manifest: dict[str, Any] | None = None,
    ) -> PaperResponse | None:
        """Persist a transition of the paper-code ingestion state (#5).

        Used by ``PaperbaseService.ingest_repo`` to move a paper through
        ``none`` → ``pending`` → ``indexing`` → ``indexed`` / ``skipped`` /
        ``failed`` / ``stale`` as the ``ingest_repo`` worker processes it.

        Fields are optional so intermediate transitions (for example, just
        flipping ``status`` to ``indexing`` before the fetch starts) don't
        clobber the classification verdict from a prior run. Returns the
        refreshed paper row, or ``None`` if the paper disappeared mid-job.
        """
        raise NotImplementedError

    def set_paper_venue(
        self,
        paper_id: str,
        *,
        venue: str | None,
        venue_year: int | None,
        venue_source: str | None,
    ) -> PaperResponse | None:
        """Stamp peer-review venue columns on an existing paper (#39 PR B).

        Kept separate from :meth:`upsert_paper` so the venue stamp is not
        wiped on the next regular re-index — arxiv metadata does not
        carry venue info, so a re-index from arxiv would leave ``venue``
        as ``None`` if it were part of the upsert path.

        Returns the refreshed paper row, or ``None`` if the paper does
        not exist (caller should index it first).
        """
        raise NotImplementedError

    @abstractmethod
    def replace_code_chunks(self, paper_id: str, chunks: list[dict[str, Any]]) -> None:
        """Replace only the code-kind chunks for a paper (#5 PR 2).

        Unlike :meth:`replace_text_chunks`, this preserves paper-section
        chunks (``section`` / ``table`` / ``figure_caption`` / ``full_text``)
        so a re-ingest of the linked repo doesn't force a full paper
        re-extraction. Implementations must delete all existing chunks
        whose ``chunk_kind`` is in :data:`CODE_CHUNK_KINDS` for the given
        paper, then insert the supplied chunks.
        """
        raise NotImplementedError

    @abstractmethod
    def search_paper_code_sections(
        self,
        paper_id: str,
        query: str,
        *,
        limit: int,
        query_embedding: list[float] | None = None,
    ) -> list[SearchSectionResult]:
        """Paper-scoped lexical + optional vector search restricted to
        code chunks (#5 PR 2). Returns :class:`SearchSectionResult` rows
        whose ``chunk_kind`` is in :data:`CODE_CHUNK_KINDS`. Callers must
        gate on ``paper.code_is_python is True`` — this method does not
        re-check eligibility.
        """
        raise NotImplementedError

    @abstractmethod
    def list_papers_needing_summary(
        self,
        *,
        limit: int,
        after: PaperResponse | None = None,
    ) -> list[PaperResponse]:
        raise NotImplementedError

    @abstractmethod
    def list_papers_needing_embedding_backfill(
        self,
        *,
        limit: int,
        include_chunks: bool = True,
        rewrite_existing: bool = False,
        after: PaperResponse | None = None,
    ) -> list[PaperResponse]:
        """Return papers whose embeddings should be (re)written.

        When ``rewrite_existing`` is false, this is the NULL-only backfill
        frontier: papers whose paper-level embedding is missing OR, when
        ``include_chunks`` is true, papers that have at least one chunk with a
        missing embedding. When ``rewrite_existing`` is true, return the full
        indexed-paper frontier so callers can rewrite every paper/chunk vector
        into the current embedding space.

        Always returns the current frontier — callers should fetch without an
        offset and rely on the predicate to drop rows as they get repaired.
        """
        raise NotImplementedError

    @abstractmethod
    def list_papers_needing_enrichment(
        self,
        *,
        limit: int,
        min_age_seconds: int = 3600,
        after: PaperResponse | None = None,
    ) -> list[PaperResponse]:
        """Return papers that still lack Semantic Scholar enrichment.

        Predicate: indexed_at + ``min_age_seconds`` has elapsed, the paper
        has no S2 id, AND has zero citations. The age floor keeps the
        backfill job from racing with inline enrichment on freshly-ingested
        papers.
        """
        raise NotImplementedError

    @abstractmethod
    def enrichment_stats(self) -> dict[str, int]:
        """Return counts for observability / doctor. Keys:

        - ``total_indexed`` — indexed papers
        - ``with_semantic_scholar_id`` — of those, how many have a populated S2 id
        - ``with_citations`` — of those, how many have ``citation_count > 0``
        - ``enriched`` — has S2 id OR non-zero citations. This is the metric
          that represents "S2 enrichment succeeded"; a zero-citation recent
          paper with a populated S2 id still counts as enriched.
        """
        raise NotImplementedError

    @abstractmethod
    def update_paper_embedding(self, paper_id: str, embedding: list[float]) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_chunks_missing_embedding(self, paper_id: str, *, include_existing: bool = False) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def update_chunk_embedding(self, chunk_id: str, embedding: list[float]) -> None:
        raise NotImplementedError

    @abstractmethod
    def best_chunk_for_papers(
        self,
        query: str,
        paper_ids: list[str],
        *,
        query_embedding: list[float] | None = None,
    ) -> dict[str, str]:
        """Return {paper_id: best_chunk_content} for each paper id."""
        raise NotImplementedError

    @abstractmethod
    def suggest_papers(self, q: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Typeahead-style lookup over title/authors trigram match."""
        raise NotImplementedError

    @abstractmethod
    def suggest_fallback_papers(self, *, limit: int = 6) -> list[dict[str, Any]]:
        """Return a small set of indexed papers when no typeahead matches exist."""
        raise NotImplementedError

    @abstractmethod
    def stats(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def replace_paper_refs(self, paper_id: str, refs: list[dict[str, Any]]) -> None:
        """Replace the outgoing citation set for a paper."""
        raise NotImplementedError

    @abstractmethod
    def get_paper_refs(self, paper_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def search_local(
        self,
        query: str,
        *,
        limit: int,
        query_embedding: list[float] | None = None,
        rrf_k: int = 60,
        citation_boost_scale: float = 0.003,
        citation_boost_max: float = 0.015,
        recency_boost_max: float = 0.005,
        recency_half_life_years: int = 10,
    ) -> list[SearchPaperResult]:
        raise NotImplementedError

    @abstractmethod
    def search_sections(
        self,
        query: str,
        *,
        limit: int,
        result_set_id: str | None = None,
        paper_ids: list[str] | None = None,
        query_embedding: list[float] | None = None,
        rrf_k: int = 60,
    ) -> list[SearchSectionResult]:
        raise NotImplementedError

    @abstractmethod
    def save_result_set(
        self,
        *,
        query: str | None,
        papers: list[SearchPaperResult],
        parent_result_set_id: str | None = None,
        pattern: str | None = None,
        ttl_days: int = 7,
    ) -> ResultSetResponse:
        raise NotImplementedError

    @abstractmethod
    def get_result_set(self, result_set_id: str) -> ResultSetResponse | None:
        raise NotImplementedError

    @abstractmethod
    def grep_result_set(self, result_set_id: str, pattern: str, *, limit: int) -> list[GrepPaperMatch]:
        raise NotImplementedError

    @abstractmethod
    def create_job(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        max_attempts: int | None = None,
        priority: int | None = None,
        dedupe_key: str | None = None,
        root_job_id: str | None = None,
    ) -> JobResponse:
        raise NotImplementedError

    @abstractmethod
    def get_job(self, job_id: str) -> JobResponse | None:
        raise NotImplementedError

    @abstractmethod
    def claim_pending_job(self) -> JobResponse | None:
        raise NotImplementedError

    @abstractmethod
    def mark_job_running(self, job_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def mark_job_completed(self, job_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def mark_job_failed(
        self,
        job_id: str,
        error: str,
        *,
        permanent: bool = False,
        next_run_at: Any | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def requeue_dead_job(self, job_id: str) -> JobResponse | None:
        raise NotImplementedError

    @abstractmethod
    def list_jobs_by_status(self, status: str, *, limit: int = 50) -> list[JobResponse]:
        raise NotImplementedError

    @abstractmethod
    def list_recent_jobs(self, *, limit: int = 200) -> list[JobResponse]:
        raise NotImplementedError

    @abstractmethod
    def list_jobs_for_root(self, root_job_id: str) -> list[JobResponse]:
        raise NotImplementedError

    @abstractmethod
    def record_job_paper(
        self,
        job_id: str,
        paper_id: str,
        *,
        outcome: str,
        arxiv_id: str | None = None,
        title: str | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_job_papers_for_root(self, root_job_id: str) -> list[JobPaperRecord]:
        raise NotImplementedError

    @abstractmethod
    def cleanup_expired_results(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def record_search_log(
        self,
        *,
        query_id: str,
        user_id: str | None,
        query_text: str,
        request_rerank: bool,
        top_paper_ids: list[str],
        result_set_id: str | None,
        latency_ms: int | None,
    ) -> None:
        """Persist a row to search_logs. Must not raise on the hot path —
        implementations should swallow storage errors and log them; a search
        response must not fail because logging failed."""
        raise NotImplementedError

    @abstractmethod
    def record_search_click(self, query_id: str, paper_id: str) -> bool:
        """Attach a clicked_paper_id + clicked_at to an existing search_logs
        row. Returns False when the query_id is unknown OR when the paper_id
        was not part of the logged top_paper_ids, so the API layer can
        return 404. The membership check exists to keep the click-label
        dataset trustworthy for retrieval evaluation."""
        raise NotImplementedError


class SqlAlchemyPaperRepository(PaperRepository):
    def __init__(self, settings: Settings) -> None:
        self._embedding_dim = settings.embedding_dim
        self._engine = create_engine(settings.database_url, future=True)
        self._session_factory = sessionmaker(self._engine, expire_on_commit=False)

    @contextmanager
    def _session(self) -> Iterable[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _ensure_default_project_row(self, session: Session, user_id: str) -> ProjectORM:
        project = session.scalar(
            select(ProjectORM).where(ProjectORM.user_id == user_id, ProjectORM.is_default.is_(True))
        )
        if project is not None:
            return project
        project = ProjectORM(
            project_id=new_id("proj"),
            user_id=user_id,
            name="General",
            description="",
            is_default=True,
        )
        session.add(project)
        session.flush()
        return project

    def _get_or_create_browser_user_settings_row(
        self,
        session: Session,
        user_id: str,
    ) -> BrowserUserSettingsORM:
        row = session.scalar(select(BrowserUserSettingsORM).where(BrowserUserSettingsORM.user_id == user_id))
        if row is not None:
            return row
        row = BrowserUserSettingsORM(
            browser_user_settings_id=new_id("uset"),
            user_id=user_id,
        )
        session.add(row)
        session.flush()
        return row

    def _backfill_projects(self) -> None:
        with self._session() as session:
            users = session.scalars(select(BrowserUserORM)).all()
            default_projects = {user.user_id: self._ensure_default_project_row(session, user.user_id) for user in users}
            threads = session.scalars(
                select(ChatThreadORM).where((ChatThreadORM.project_id.is_(None)) | (ChatThreadORM.project_id == ""))
            ).all()
            for thread in threads:
                project = default_projects.get(thread.user_id) or self._ensure_default_project_row(
                    session, thread.user_id
                )
                thread.project_id = project.project_id
                if not thread.project_name:
                    thread.project_name = project.name
                if not thread.project_instructions:
                    thread.project_instructions = project.description or ""

    def init(self) -> None:
        vector_type = f"VECTOR({self._embedding_dim})"
        with self._engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            connection.execute(
                text("ALTER TABLE IF EXISTS papers ADD COLUMN IF NOT EXISTS summary_short TEXT DEFAULT ''")
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS papers ADD COLUMN IF NOT EXISTS summary_eli5 TEXT DEFAULT ''")
            )
            connection.execute(text("ALTER TABLE IF EXISTS papers ADD COLUMN IF NOT EXISTS tldr TEXT DEFAULT ''"))
            connection.execute(text(f"ALTER TABLE IF EXISTS papers ADD COLUMN IF NOT EXISTS embedding {vector_type}"))
            connection.execute(
                text(
                    "ALTER TABLE IF EXISTS papers "
                    "ADD COLUMN IF NOT EXISTS code_index_status VARCHAR(16) DEFAULT 'none'"
                )
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS papers ADD COLUMN IF NOT EXISTS code_is_python BOOLEAN")
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS papers ADD COLUMN IF NOT EXISTS code_python_reason TEXT")
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS papers ADD COLUMN IF NOT EXISTS code_manifest JSONB")
            )
            # #39 PR B: peer-review venue signal
            connection.execute(
                text("ALTER TABLE IF EXISTS papers ADD COLUMN IF NOT EXISTS venue TEXT")
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS papers ADD COLUMN IF NOT EXISTS venue_year INTEGER")
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS papers ADD COLUMN IF NOT EXISTS venue_source VARCHAR(32)")
            )
            connection.execute(
                text(f"ALTER TABLE IF EXISTS text_chunks ADD COLUMN IF NOT EXISTS embedding {vector_type}")
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS text_chunks ADD COLUMN IF NOT EXISTS chunk_index INTEGER DEFAULT 0")
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS text_chunks ADD COLUMN IF NOT EXISTS section_depth INTEGER DEFAULT 1")
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS text_chunks ADD COLUMN IF NOT EXISTS section_order INTEGER DEFAULT 0")
            )
            # #32: worker retry policy + dead-letter
            connection.execute(
                text("ALTER TABLE IF EXISTS jobs ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0")
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS jobs ADD COLUMN IF NOT EXISTS max_attempts INTEGER NOT NULL DEFAULT 3")
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS jobs ADD COLUMN IF NOT EXISTS next_run_at TIMESTAMPTZ")
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS jobs ADD COLUMN IF NOT EXISTS last_attempted_at TIMESTAMPTZ")
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS jobs ADD COLUMN IF NOT EXISTS dedupe_key VARCHAR(512)")
            )
            # Indexes on next_run_at (#32) and priority (#33) go in the
            # post-create_all block below — on a fresh CI DB the `jobs` table
            # doesn't exist yet when ALTERs run, and CREATE INDEX (unlike
            # ALTER TABLE IF EXISTS) will error without the table present.
            # #33: worker priority so backfill harvests cannot starve on-demand work
            connection.execute(
                text("ALTER TABLE IF EXISTS jobs ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 0")
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS chat_threads ADD COLUMN IF NOT EXISTS project_id VARCHAR(64)")
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS chat_threads ADD COLUMN IF NOT EXISTS project_name TEXT DEFAULT ''")
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS chat_threads ADD COLUMN IF NOT EXISTS project_instructions TEXT DEFAULT ''")
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS chat_messages ADD COLUMN IF NOT EXISTS kind VARCHAR(32) DEFAULT 'default'")
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS chat_messages ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ")
            )
            connection.execute(
                text(
                    "ALTER TABLE IF EXISTS chat_messages "
                    "ADD COLUMN IF NOT EXISTS archived_by_message_id VARCHAR(64)"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE IF EXISTS browser_user_settings ADD COLUMN IF NOT EXISTS encrypted_modal_token_id TEXT"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE IF EXISTS browser_user_settings "
                    "ADD COLUMN IF NOT EXISTS encrypted_modal_token_secret TEXT"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE IF EXISTS browser_user_settings "
                    "ADD COLUMN IF NOT EXISTS modal_token_id_hint VARCHAR(32) DEFAULT ''"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE IF EXISTS browser_user_settings "
                    "ADD COLUMN IF NOT EXISTS appearance_theme VARCHAR(16) DEFAULT 'light'"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE IF EXISTS jobs "
                    "ADD COLUMN IF NOT EXISTS root_job_id VARCHAR(64)"
                )
            )
        Base.metadata.create_all(self._engine)
        with self._engine.begin() as connection:
            # search_tsv is added after create_all() so it works for both
            # fresh installs (papers table just created by ORM) and existing
            # databases (column added via IF NOT EXISTS).
            connection.execute(
                text(
                    """
                    ALTER TABLE papers ADD COLUMN IF NOT EXISTS search_tsv tsvector
                    GENERATED ALWAYS AS (
                        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
                        setweight(to_tsvector('english', coalesce(tldr, '')), 'A') ||
                        setweight(to_tsvector('english', coalesce(summary_short, '')), 'A') ||
                        setweight(to_tsvector('english', coalesce(abstract, '')), 'B') ||
                        setweight(to_tsvector('english', coalesce(summary_eli5, '')), 'B') ||
                        setweight(to_tsvector('simple', coalesce(authors, '')), 'C') ||
                        setweight(to_tsvector('english', coalesce(search_document, '')), 'C')
                    ) STORED
                    """
                )
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS projects_default_per_user ON projects (user_id) WHERE is_default"
                )
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS python_files ADD COLUMN IF NOT EXISTS file_kind VARCHAR(32) DEFAULT 'python'")
            )
            connection.execute(
                text("ALTER TABLE IF EXISTS python_files ADD COLUMN IF NOT EXISTS repo_path TEXT DEFAULT ''")
            )
            connection.execute(
                text(
                    "UPDATE python_files SET file_kind = 'python' "
                    "WHERE file_kind IS NULL OR file_kind = ''"
                )
            )
            connection.execute(
                text(
                    "UPDATE python_files SET repo_path = 'src/' || filename "
                    "WHERE repo_path IS NULL OR repo_path = ''"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE IF EXISTS project_repo_syncs "
                    "ADD COLUMN IF NOT EXISTS github_owner_login VARCHAR(255) DEFAULT ''"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE IF EXISTS project_repo_syncs "
                    "ADD COLUMN IF NOT EXISTS base_branch VARCHAR(255) DEFAULT 'main'"
                )
            )
            connection.execute(
                text(
                    "UPDATE project_repo_syncs SET base_branch = default_branch "
                    "WHERE base_branch IS NULL OR base_branch = ''"
                )
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS chat_threads_project_id_idx ON chat_threads (project_id)")
            )
            connection.execute(
                text(
                    "DROP INDEX IF EXISTS python_files_project_filename_unique"
                )
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS python_files_project_repo_path_unique "
                    "ON python_files (project_id, repo_path)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS python_files_project_updated_at_idx "
                    "ON python_files (project_id, updated_at DESC)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS papers_embedding_hnsw "
                    "ON papers USING hnsw (embedding vector_cosine_ops) "
                    "WITH (m = 16, ef_construction = 64)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS text_chunks_embedding_hnsw "
                    "ON text_chunks USING hnsw (embedding vector_cosine_ops) "
                    "WITH (m = 16, ef_construction = 64)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS papers_search_document_trgm "
                    "ON papers USING gin (search_document gin_trgm_ops)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS papers_search_tsv_gin "
                    "ON papers USING gin (search_tsv)"
                )
            )
            # #32 / #33: next_run_at + priority indexes for claim ordering.
            # Live here (after create_all) so fresh CI DBs have `jobs` before
            # the CREATE INDEX runs.
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS jobs_next_run_at ON jobs (next_run_at)")
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS jobs_priority ON jobs (priority)")
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS jobs_root_job_id ON jobs (root_job_id)")
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS jobs_dedupe_key_unique "
                    "ON jobs (dedupe_key) WHERE dedupe_key IS NOT NULL"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS job_papers_outcome_idx "
                    "ON job_papers (outcome)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS job_papers_updated_at_idx "
                    "ON job_papers (updated_at DESC)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS text_chunks_content_trgm "
                    "ON text_chunks USING gin (content gin_trgm_ops)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS text_chunks_content_tsv_gin "
                    "ON text_chunks USING gin (to_tsvector('english', coalesce(content, '')))"
                )
            )
            connection.execute(text("CREATE INDEX IF NOT EXISTS paper_refs_dst_arxiv_id ON paper_refs (dst_arxiv_id)"))
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS paper_refs_dst_paper_id "
                    "ON paper_refs (dst_paper_id) WHERE dst_paper_id IS NOT NULL"
                )
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS papers_title_trgm ON papers USING gin (title gin_trgm_ops)")
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS papers_authors_trgm ON papers USING gin (authors gin_trgm_ops)")
            )
            # Summary / tldr trigram indexes back the suggest endpoint's
            # widened ILIKE '%q%' branch. Without these, summary-only
            # typeahead hits the papers table sequentially on every keystroke.
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS papers_summary_short_trgm "
                    "ON papers USING gin (summary_short gin_trgm_ops)"
                )
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS papers_tldr_trgm ON papers USING gin (tldr gin_trgm_ops)")
            )
        self._backfill_projects()

    def probe(self) -> IntegrationProbeComponent:
        result = 0
        with self._engine.connect() as connection:
            result = int(connection.execute(text("SELECT 1")).scalar_one())
        return IntegrationProbeComponent(
            ok=result == 1,
            backend="sqlalchemy",
            detail="SELECT 1 succeeded" if result == 1 else f"Unexpected probe result: {result}",
            target=self._engine.url.get_backend_name(),
            metadata={"driver": self._engine.url.get_driver_name()},
        )

    def upsert_paper(
        self,
        paper: PaperMetadata,
        *,
        search_document: str = "",
        embedding: list[float] | None = None,
    ) -> PaperResponse:
        canon_arxiv = canonical_arxiv_id(paper.arxiv_id)
        statement = insert(PaperORM).values(
            paper_id=canonical_paper_id(paper.arxiv_id),
            arxiv_id=canon_arxiv,
            title=paper.title,
            authors=paper.authors,
            abstract=paper.abstract,
            tldr=paper.tldr,
            summary_short=paper.summary_short,
            summary_eli5=paper.summary_eli5,
            categories=paper.categories,
            primary_category=paper.primary_category,
            published=paper.published,
            updated=paper.updated,
            doi=paper.doi,
            comment=paper.comment,
            journal_ref=paper.journal_ref,
            pdf_url=paper.pdf_url,
            html_url=paper.html_url,
            citation_count=paper.citation_count,
            influential_citation_count=paper.influential_citation_count,
            semantic_scholar_id=paper.semantic_scholar_id,
            code_url=paper.code_url,
            source_type=paper.source_type,
            index_status=paper.index_status,
            search_document=search_document,
            embedding=embedding,
            indexed_at=paper.indexed_at,
        )
        update_values = {
            "title": paper.title,
            "authors": paper.authors,
            "abstract": paper.abstract,
            "tldr": paper.tldr,
            "summary_short": paper.summary_short,
            "summary_eli5": paper.summary_eli5,
            "categories": paper.categories,
            "primary_category": paper.primary_category,
            "published": paper.published,
            "updated": paper.updated,
            "doi": paper.doi,
            "comment": paper.comment,
            "journal_ref": paper.journal_ref,
            "pdf_url": paper.pdf_url,
            "html_url": paper.html_url,
            "citation_count": paper.citation_count,
            "influential_citation_count": paper.influential_citation_count,
            "semantic_scholar_id": paper.semantic_scholar_id,
            "code_url": paper.code_url,
            "source_type": paper.source_type,
            "index_status": paper.index_status,
            "search_document": search_document,
            "embedding": embedding,
            "indexed_at": paper.indexed_at,
            "updated_at": utcnow(),
        }
        statement = statement.on_conflict_do_update(index_elements=[PaperORM.paper_id], set_=update_values)

        # Wrapped in deadlock-retry per #51 (symmetric to the
        # ``replace_text_chunks`` wrap below). The INSERT ... ON CONFLICT
        # path can deadlock against an in-flight ``replace_text_chunks``
        # in another pooled connection that holds an FK lock on the
        # same paper row.
        def _do() -> PaperResponse:
            with self._session() as session:
                session.execute(statement)
                row = session.get(PaperORM, canonical_paper_id(paper.arxiv_id))
                assert row is not None
                return _paper_from_orm(row)

        return _retry_on_deadlock(_do, label="upsert_paper")

    def upsert_paper_shallow(self, paper: PaperMetadata) -> PaperResponse:
        paper_id = canonical_paper_id(paper.arxiv_id)
        canon_arxiv = canonical_arxiv_id(paper.arxiv_id)
        statement = insert(PaperORM).values(
            paper_id=paper_id,
            arxiv_id=canon_arxiv,
            title=paper.title,
            authors=paper.authors,
            abstract=paper.abstract,
            tldr=paper.tldr,
            categories=paper.categories,
            primary_category=paper.primary_category,
            published=paper.published,
            updated=paper.updated,
            doi=paper.doi,
            comment=paper.comment,
            journal_ref=paper.journal_ref,
            pdf_url=paper.pdf_url,
            html_url=paper.html_url,
            citation_count=paper.citation_count,
            influential_citation_count=paper.influential_citation_count,
            semantic_scholar_id=paper.semantic_scholar_id,
            code_url=paper.code_url,
            # Fresh row discovered via a live search — not yet indexed.
            index_status="discovered",
        )
        # On conflict, refresh only metadata that live sources are authoritative for.
        # Never touch index_status, indexed_at, search_document, embedding, or
        # any summary the local indexer has already filled in.
        shallow_update: dict[str, Any] = {
            "citation_count": func.greatest(PaperORM.citation_count, statement.excluded.citation_count),
            "influential_citation_count": func.greatest(
                PaperORM.influential_citation_count, statement.excluded.influential_citation_count
            ),
            "semantic_scholar_id": func.coalesce(PaperORM.semantic_scholar_id, statement.excluded.semantic_scholar_id),
            "tldr": func.coalesce(func.nullif(PaperORM.tldr, ""), statement.excluded.tldr, ""),
            "doi": func.coalesce(PaperORM.doi, statement.excluded.doi),
            "code_url": func.coalesce(PaperORM.code_url, statement.excluded.code_url),
            "updated_at": utcnow(),
        }
        statement = statement.on_conflict_do_update(index_elements=[PaperORM.paper_id], set_=shallow_update)
        with self._session() as session:
            session.execute(statement)
            row = session.get(PaperORM, paper_id)
            assert row is not None
            return _paper_from_orm(row)

    def get_paper(self, paper_id: str) -> PaperResponse | None:
        with self._session() as session:
            row = session.get(PaperORM, paper_id)
            return _paper_from_orm(row) if row else None

    def get_paper_by_arxiv_id(self, arxiv_id: str) -> PaperResponse | None:
        with self._session() as session:
            row = session.scalar(select(PaperORM).where(PaperORM.arxiv_id == arxiv_id))
            return _paper_from_orm(row) if row else None

    def list_papers(
        self,
        *,
        query: str | None = None,
        index_status: str | None = None,
        limit: int = 50,
    ) -> list[PaperResponse]:
        statement = (
            select(PaperORM)
            .order_by(PaperORM.indexed_at.desc().nullslast(), PaperORM.updated_at.desc(), PaperORM.paper_id.asc())
            .limit(limit)
        )
        if index_status and index_status != "all":
            statement = statement.where(PaperORM.index_status == index_status)
        if query:
            needle = f"%{query.strip().lower()}%"
            statement = statement.where(
                or_(
                    func.lower(PaperORM.title).like(needle),
                    func.lower(PaperORM.arxiv_id).like(needle),
                    func.lower(PaperORM.authors).like(needle),
                )
            )
        with self._session() as session:
            rows = session.scalars(statement).all()
        return [_paper_from_orm(row) for row in rows]

    def replace_text_chunks(self, paper_id: str, chunks: list[dict[str, Any]]) -> None:
        # Wrapped in deadlock-retry per #51. The DELETE + INSERT pattern
        # against ``text_chunks`` takes a RowShareLock on the parent
        # ``papers`` row for FK validation, which can deadlock against
        # an in-flight ``upsert_paper`` running in a different pooled
        # connection in the same process. Retrying the deadlock loser
        # is cheaper than letting the chunk worker fail and re-fetch
        # arxiv + S2 metadata from scratch.
        def _do() -> None:
            with self._session() as session:
                session.execute(delete(TextChunkORM).where(TextChunkORM.paper_id == paper_id))
                for chunk in chunks:
                    session.add(
                        TextChunkORM(
                            chunk_id=new_id("chunk"),
                            paper_id=paper_id,
                            path=chunk["path"],
                            chunk_kind=chunk["chunk_kind"],
                            content=chunk["content"],
                            embedding=chunk.get("embedding"),
                            chunk_index=int(chunk.get("chunk_index", 0)),
                            section_depth=int(chunk.get("section_depth", 1)),
                            section_order=int(chunk.get("section_order", 0)),
                        )
                    )

        _retry_on_deadlock(_do, label="replace_text_chunks")

    def update_summaries(self, paper_id: str, *, summary_short: str, summary_eli5: str) -> PaperResponse | None:
        with self._session() as session:
            row = session.get(PaperORM, paper_id)
            if row is None:
                return None
            row.summary_short = summary_short
            row.summary_eli5 = summary_eli5
            row.updated_at = utcnow()
            session.flush()
            return _paper_from_orm(row)

    def update_code_ingest_state(
        self,
        paper_id: str,
        *,
        status: str,
        is_python: bool | None = None,
        python_reason: str | None = None,
        manifest: dict[str, Any] | None = None,
    ) -> PaperResponse | None:
        with self._session() as session:
            row = session.get(PaperORM, paper_id)
            if row is None:
                return None
            row.code_index_status = status
            # Only overwrite verdict fields when the caller supplied them so
            # intermediate transitions (e.g. flipping to ``indexing`` before
            # the fetch) don't wipe a prior run's classification.
            if is_python is not None or python_reason is not None or manifest is not None:
                row.code_is_python = is_python
                row.code_python_reason = python_reason
                row.code_manifest = manifest
            row.updated_at = utcnow()
            session.flush()
            return _paper_from_orm(row)

    def set_paper_venue(
        self,
        paper_id: str,
        *,
        venue: str | None,
        venue_year: int | None,
        venue_source: str | None,
    ) -> PaperResponse | None:
        with self._session() as session:
            row = session.get(PaperORM, paper_id)
            if row is None:
                return None
            row.venue = venue
            row.venue_year = venue_year
            row.venue_source = venue_source
            row.updated_at = utcnow()
            session.flush()
            return _paper_from_orm(row)

    def replace_code_chunks(self, paper_id: str, chunks: list[dict[str, Any]]) -> None:
        with self._session() as session:
            session.execute(
                delete(TextChunkORM).where(
                    TextChunkORM.paper_id == paper_id,
                    TextChunkORM.chunk_kind.in_(CODE_CHUNK_KINDS),
                )
            )
            for chunk in chunks:
                session.add(
                    TextChunkORM(
                        chunk_id=new_id("chunk"),
                        paper_id=paper_id,
                        path=chunk["path"],
                        chunk_kind=chunk["chunk_kind"],
                        content=chunk["content"],
                        embedding=chunk.get("embedding"),
                        chunk_index=int(chunk.get("chunk_index", 0)),
                        section_depth=int(chunk.get("section_depth", 1)),
                        section_order=int(chunk.get("section_order", 0)),
                    )
                )

    def search_paper_code_sections(
        self,
        paper_id: str,
        query: str,
        *,
        limit: int,
        query_embedding: list[float] | None = None,
    ) -> list[SearchSectionResult]:
        if not query or not paper_id:
            return []
        candidate_pool = max(limit * 4, 20)
        params: dict[str, Any] = {
            "query": query,
            "paper_id": paper_id,
            "candidate_pool": candidate_pool,
            "code_kinds": list(CODE_CHUNK_KINDS),
        }
        lexical_sql = text(
            """
            SELECT
                tc.chunk_id,
                tc.paper_id,
                p.arxiv_id,
                p.title,
                tc.path,
                tc.chunk_kind,
                tc.content,
                ts_rank_cd(
                    to_tsvector('english', coalesce(tc.content, '')),
                    websearch_to_tsquery('english', :query)
                ) AS lexical_score
            FROM text_chunks tc
            JOIN papers p ON p.paper_id = tc.paper_id
            WHERE tc.paper_id = :paper_id
              AND tc.chunk_kind = ANY(CAST(:code_kinds AS text[]))
              AND (
                to_tsvector('english', coalesce(tc.content, '')) @@ websearch_to_tsquery('english', :query)
                OR similarity(coalesce(tc.content, ''), :query) > 0.1
              )
            ORDER BY lexical_score DESC
            LIMIT :candidate_pool
            """
        )
        with self._session() as session:
            lexical_rows = session.execute(lexical_sql, params).mappings().all()
        vector_rows: list[Any] = []
        if query_embedding:
            embedding_literal = "[" + ",".join(f"{value:.6f}" for value in query_embedding) + "]"
            vector_params = {
                "query_embedding": embedding_literal,
                "paper_id": paper_id,
                "candidate_pool": candidate_pool,
                "code_kinds": list(CODE_CHUNK_KINDS),
            }
            vector_sql = text(
                """
                SELECT
                    tc.chunk_id,
                    tc.paper_id,
                    p.arxiv_id,
                    p.title,
                    tc.path,
                    tc.chunk_kind,
                    tc.content,
                    1 - (tc.embedding <=> CAST(:query_embedding AS vector)) AS vector_score
                FROM text_chunks tc
                JOIN papers p ON p.paper_id = tc.paper_id
                WHERE tc.paper_id = :paper_id
                  AND tc.chunk_kind = ANY(CAST(:code_kinds AS text[]))
                  AND tc.embedding IS NOT NULL
                ORDER BY tc.embedding <=> CAST(:query_embedding AS vector)
                LIMIT :candidate_pool
                """
            )
            with self._session() as session:
                vector_rows = session.execute(vector_sql, vector_params).mappings().all()
        # Simple RRF fuse: lexical first pass, vector second. Code search is
        # paper-scoped and tight, so no need for the heavier kind_score /
        # depth_weight apparatus used by paper-section search.
        rrf_k = 60
        scored: dict[str, tuple[float, Any]] = {}
        for rank, row in enumerate(lexical_rows):
            cid = row["chunk_id"]
            scored[cid] = (1.0 / (rrf_k + rank), row)
        for rank, row in enumerate(vector_rows):
            cid = row["chunk_id"]
            prev_score, prev_row = scored.get(cid, (0.0, row))
            scored[cid] = (prev_score + 1.2 / (rrf_k + rank), prev_row or row)
        fused = sorted(scored.items(), key=lambda kv: kv[1][0], reverse=True)
        results: list[SearchSectionResult] = []
        for _cid, (score, row) in fused[:limit]:
            content = row["content"] or ""
            results.append(
                SearchSectionResult(
                    paper_id=row["paper_id"],
                    arxiv_id=row["arxiv_id"],
                    title=row["title"],
                    path=row["path"],
                    chunk_kind=row["chunk_kind"],
                    preview=content[:320],
                    score=score,
                )
            )
        return results

    def list_papers_needing_summary(
        self,
        *,
        limit: int,
        after: PaperResponse | None = None,
    ) -> list[PaperResponse]:
        query = (
            select(PaperORM)
            .where((PaperORM.summary_eli5 == "") | (PaperORM.summary_eli5.is_(None)))
            .where(PaperORM.index_status == "indexed")
        )
        if after is not None:
            query = query.where(
                (PaperORM.influential_citation_count < after.influential_citation_count)
                | (
                    (PaperORM.influential_citation_count == after.influential_citation_count)
                    & (PaperORM.citation_count < after.citation_count)
                )
                | (
                    (PaperORM.influential_citation_count == after.influential_citation_count)
                    & (PaperORM.citation_count == after.citation_count)
                    & (PaperORM.published < after.published)
                )
                | (
                    (PaperORM.influential_citation_count == after.influential_citation_count)
                    & (PaperORM.citation_count == after.citation_count)
                    & (PaperORM.published == after.published)
                    & (PaperORM.paper_id > after.paper_id)
                )
            )
        query = query.order_by(
            PaperORM.influential_citation_count.desc(),
            PaperORM.citation_count.desc(),
            PaperORM.published.desc(),
            PaperORM.paper_id.asc(),
        ).limit(limit)
        with self._session() as session:
            rows = session.scalars(query).all()
        return [_paper_from_orm(row) for row in rows]

    def list_papers_needing_embedding_backfill(
        self,
        *,
        limit: int,
        include_chunks: bool = True,
        rewrite_existing: bool = False,
        after: PaperResponse | None = None,
    ) -> list[PaperResponse]:
        """Keyset-paginated backfill frontier. Use the same ordering as
        ``list_papers_needing_summary`` so both backfill loops share a single
        advancement story — passing the last returned paper as ``after`` on the
        next call skips past it regardless of whether its embedding actually
        got written.
        """
        base_sql = """
            SELECT papers.*
            FROM papers
            WHERE papers.index_status = 'indexed'
              {needs_embedding_predicate}
              {after_predicate}
            ORDER BY
              papers.influential_citation_count DESC NULLS LAST,
              papers.citation_count DESC NULLS LAST,
              papers.published DESC,
              papers.paper_id ASC
            LIMIT :limit
        """
        if rewrite_existing:
            needs_embedding_predicate = ""
        else:
            chunk_predicate = (
                "OR EXISTS (SELECT 1 FROM text_chunks WHERE text_chunks.paper_id = papers.paper_id "
                "AND text_chunks.embedding IS NULL)"
                if include_chunks
                else ""
            )
            needs_embedding_predicate = f"""AND (
                papers.embedding IS NULL
                {chunk_predicate}
              )"""
        params: dict[str, Any] = {"limit": limit}
        after_predicate = ""
        if after is not None:
            after_predicate = """AND (
                papers.influential_citation_count < :after_influential
                OR (papers.influential_citation_count = :after_influential
                    AND papers.citation_count < :after_citation)
                OR (papers.influential_citation_count = :after_influential
                    AND papers.citation_count = :after_citation
                    AND papers.published < :after_published)
                OR (papers.influential_citation_count = :after_influential
                    AND papers.citation_count = :after_citation
                    AND papers.published = :after_published
                    AND papers.paper_id > :after_paper_id)
            )"""
            params.update(
                {
                    "after_influential": after.influential_citation_count or 0,
                    "after_citation": after.citation_count or 0,
                    "after_published": after.published or "",
                    "after_paper_id": after.paper_id,
                }
            )
        sql = text(
            base_sql.format(
                needs_embedding_predicate=needs_embedding_predicate,
                after_predicate=after_predicate,
            )
        )
        with self._session() as session:
            rows = session.scalars(select(PaperORM).from_statement(sql).params(**params)).all()
        return [_paper_from_orm(row) for row in rows]

    def list_papers_needing_enrichment(
        self,
        *,
        limit: int,
        min_age_seconds: int = 3600,
        after: PaperResponse | None = None,
    ) -> list[PaperResponse]:
        # Predicate: indexed long enough ago for inline enrichment to have
        # run and failed, no S2 id recorded, and zero citations (so we don't
        # re-hit the API for papers that actually came back with 0 cites).
        base_sql = """
            SELECT papers.*
            FROM papers
            WHERE papers.index_status = 'indexed'
              AND (papers.semantic_scholar_id IS NULL OR papers.semantic_scholar_id = '')
              AND (papers.citation_count IS NULL OR papers.citation_count = 0)
              AND (papers.indexed_at IS NULL OR papers.indexed_at < NOW() - make_interval(secs => :min_age))
              {after_predicate}
            ORDER BY papers.indexed_at ASC NULLS FIRST, papers.paper_id ASC
            LIMIT :limit
        """
        params: dict[str, Any] = {"limit": limit, "min_age": min_age_seconds}
        after_predicate = ""
        # Keyset cursor must mirror the full ORDER BY tuple
        # (indexed_at ASC NULLS FIRST, paper_id ASC). Advancing on paper_id alone
        # skips rows whenever an earlier page ended on a lexicographically large
        # id and newer eligible rows have smaller ids.
        if after is not None:
            params["after_paper_id"] = after.paper_id
            if after.indexed_at is None:
                # Still in the NULLS-first group: advance by paper_id within
                # the NULL-indexed rows, then include every non-NULL row.
                after_predicate = (
                    "AND ((papers.indexed_at IS NULL AND papers.paper_id > :after_paper_id) "
                    "OR papers.indexed_at IS NOT NULL)"
                )
            else:
                # Past the NULL-indexed rows. Within non-NULL rows, order by
                # (indexed_at, paper_id).
                params["after_indexed_at"] = after.indexed_at
                after_predicate = (
                    "AND papers.indexed_at IS NOT NULL AND ("
                    "papers.indexed_at > :after_indexed_at "
                    "OR (papers.indexed_at = :after_indexed_at AND papers.paper_id > :after_paper_id)"
                    ")"
                )
        sql = text(base_sql.format(after_predicate=after_predicate))
        with self._session() as session:
            rows = session.scalars(select(PaperORM).from_statement(sql).params(**params)).all()
        return [_paper_from_orm(row) for row in rows]

    def enrichment_stats(self) -> dict[str, int]:
        # "enriched" must include papers with a populated S2 id even if their
        # citation_count is legitimately zero (e.g. freshly-published).
        # Using citation_count > 0 alone would flag a healthy recent corpus
        # as under-enriched and trigger false doctor warnings.
        sql = text(
            """
            SELECT
              COUNT(*) FILTER (WHERE index_status = 'indexed') AS total_indexed,
              COUNT(*) FILTER (
                WHERE index_status = 'indexed'
                AND (semantic_scholar_id IS NOT NULL AND semantic_scholar_id <> '')
              ) AS with_semantic_scholar_id,
              COUNT(*) FILTER (
                WHERE index_status = 'indexed' AND citation_count > 0
              ) AS with_citations,
              COUNT(*) FILTER (
                WHERE index_status = 'indexed'
                AND (
                  (semantic_scholar_id IS NOT NULL AND semantic_scholar_id <> '')
                  OR citation_count > 0
                )
              ) AS enriched
            FROM papers
            """
        )
        with self._session() as session:
            row = session.execute(sql).mappings().one()
        return {
            "total_indexed": int(row["total_indexed"] or 0),
            "with_semantic_scholar_id": int(row["with_semantic_scholar_id"] or 0),
            "with_citations": int(row["with_citations"] or 0),
            "enriched": int(row["enriched"] or 0),
        }

    def update_paper_embedding(self, paper_id: str, embedding: list[float]) -> None:
        with self._session() as session:
            row = session.get(PaperORM, paper_id)
            if row is not None:
                row.embedding = embedding
                row.updated_at = utcnow()

    def list_chunks_missing_embedding(self, paper_id: str, *, include_existing: bool = False) -> list[dict[str, Any]]:
        with self._session() as session:
            rows = session.execute(
                select(TextChunkORM)
                .where(
                    TextChunkORM.paper_id == paper_id,
                    true() if include_existing else TextChunkORM.embedding.is_(None),
                )
                .order_by(
                    TextChunkORM.chunk_index.asc(),
                    TextChunkORM.created_at.asc(),
                    TextChunkORM.chunk_id.asc(),
                )
                .scalars()
                .all()
            )
        return [
            {
                "chunk_id": row.chunk_id,
                "chunk_kind": row.chunk_kind,
                "content": row.content,
            }
            for row in rows
        ]

    def update_chunk_embedding(self, chunk_id: str, embedding: list[float]) -> None:
        with self._session() as session:
            row = session.get(TextChunkORM, chunk_id)
            if row is not None:
                row.embedding = embedding

    def replace_paper_refs(self, paper_id: str, refs: list[dict[str, Any]]) -> None:
        with self._session() as session:
            session.execute(delete(PaperRefORM).where(PaperRefORM.src_paper_id == paper_id))
            seen: set[str] = set()
            for ref in refs:
                dst_arxiv = canonical_arxiv_id(ref.get("arxiv_id") or "")
                if not dst_arxiv or dst_arxiv in seen:
                    continue
                seen.add(dst_arxiv)
                session.add(
                    PaperRefORM(
                        src_paper_id=paper_id,
                        dst_arxiv_id=dst_arxiv,
                        dst_paper_id=f"arxiv:{dst_arxiv}",
                        ref_type=ref.get("ref_type", "citation"),
                        rank=int(ref.get("rank", 0)),
                    )
                )

    def get_paper_refs(self, paper_id: str) -> list[dict[str, Any]]:
        with self._session() as session:
            rows = session.scalars(
                select(PaperRefORM).where(PaperRefORM.src_paper_id == paper_id).order_by(PaperRefORM.rank.asc())
            ).all()
        return [
            {
                "src_paper_id": row.src_paper_id,
                "dst_arxiv_id": row.dst_arxiv_id,
                "dst_paper_id": row.dst_paper_id,
                "ref_type": row.ref_type,
                "rank": row.rank,
            }
            for row in rows
        ]

    def best_chunk_for_papers(
        self,
        query: str,
        paper_ids: list[str],
        *,
        query_embedding: list[float] | None = None,
    ) -> dict[str, str]:
        if not paper_ids:
            return {}
        params: dict[str, Any] = {
            "query": query,
            "paper_ids": paper_ids,
            "section_kinds": list(PAPER_SECTION_CHUNK_KINDS),
        }
        order_clause = "lexical_score DESC, kind_score DESC"
        select_vector_score = "NULL AS vector_score"
        if query_embedding:
            embedding_literal = "[" + ",".join(f"{v:.6f}" for v in query_embedding) + "]"
            params["query_embedding"] = embedding_literal
            select_vector_score = "1 - (embedding <=> CAST(:query_embedding AS vector)) AS vector_score"
            order_clause = "(0.5 * coalesce(vector_score,0) + 0.5 * lexical_score) DESC, kind_score DESC"
        sql = text(
            f"""
            SELECT DISTINCT ON (paper_id)
                paper_id,
                content
            FROM (
                SELECT
                    tc.paper_id,
                    tc.content,
                    tc.chunk_kind,
                    tc.section_depth,
                    ts_rank_cd(
                        to_tsvector('english', coalesce(tc.content, '')),
                        websearch_to_tsquery('english', :query)
                    ) AS lexical_score,
                    CASE tc.chunk_kind
                        WHEN 'section' THEN 0.30
                        WHEN 'table' THEN 0.20
                        WHEN 'figure_caption' THEN 0.15
                        WHEN 'full_text' THEN 0.05
                        ELSE 0.0
                    END AS kind_score,
                    {select_vector_score}
                FROM text_chunks tc
                WHERE tc.paper_id = ANY(CAST(:paper_ids AS text[]))
                  AND tc.chunk_kind = ANY(CAST(:section_kinds AS text[]))
            ) scored
            ORDER BY paper_id, {order_clause}
            """
        )
        with self._session() as session:
            rows = session.execute(sql, params).mappings().all()
        return {row["paper_id"]: (row["content"] or "")[:2000] for row in rows}

    def suggest_papers(self, q: str, *, limit: int = 10) -> list[dict[str, Any]]:
        q = (q or "").strip()
        if not q:
            return []
        # Match shape:
        #   - ILIKE '%q%' against title/authors/summary/tldr — the primary
        #     signal for typeahead because pg_trgm's `%` operator (default
        #     similarity threshold 0.3) is too strict for a short query
        #     against a long field (e.g. "diffusion" vs "High-Resolution
        #     Image Synthesis with Latent Diffusion Models" scores ~0.13,
        #     well below threshold). GIN gin_trgm_ops indexes accelerate
        #     ILIKE '%term%' lookups, so this stays fast.
        #   - `%` operator as a fuzzy fallback (catches typos and word
        #     reorderings that a substring match misses).
        #   - Title prefix ILIKE as the primary-ranked hit.
        sql = text(
            """
            WITH scored AS (
                SELECT
                    paper_id,
                    arxiv_id,
                    title,
                    authors,
                    summary_short,
                    tldr,
                    citation_count,
                    published,
                    similarity(coalesce(title, ''), :q) AS title_sim,
                    similarity(coalesce(authors, ''), :q) AS authors_sim,
                    GREATEST(
                        similarity(coalesce(summary_short, ''), :q),
                        similarity(coalesce(tldr, ''), :q)
                    ) AS summary_sim,
                    (title ILIKE :contains_like) AS title_contains,
                    (authors ILIKE :contains_like) AS authors_contains,
                    (summary_short ILIKE :contains_like OR tldr ILIKE :contains_like) AS summary_contains
                FROM papers
                WHERE
                    title ILIKE :contains_like
                    OR authors ILIKE :contains_like
                    OR summary_short ILIKE :contains_like
                    OR tldr ILIKE :contains_like
                    OR title % :q
                    OR authors % :q
                    OR summary_short % :q
                    OR tldr % :q
            )
            SELECT
                paper_id, arxiv_id, title, authors, summary_short, tldr,
                citation_count, published,
                title_sim, authors_sim, summary_sim,
                title_contains, authors_contains, summary_contains,
                GREATEST(
                    CASE WHEN title_contains THEN 1.0 ELSE title_sim END,
                    CASE WHEN authors_contains THEN 0.9 ELSE authors_sim END,
                    CASE WHEN summary_contains THEN 0.7 ELSE summary_sim END
                ) AS score,
                CASE
                    WHEN title ILIKE :prefix_like THEN 'title_prefix'
                    WHEN title_contains THEN 'title'
                    WHEN authors_contains THEN 'authors'
                    WHEN summary_contains THEN 'summary'
                    WHEN title_sim >= authors_sim AND title_sim >= summary_sim THEN 'title'
                    WHEN authors_sim >= summary_sim THEN 'authors'
                    ELSE 'summary'
                END AS matched_field
            FROM scored
            ORDER BY
                (CASE WHEN title ILIKE :prefix_like THEN 1 ELSE 0 END) DESC,
                score DESC,
                citation_count DESC NULLS LAST,
                published DESC
            LIMIT :limit
            """
        )
        with self._session() as session:
            rows = (
                session.execute(
                    sql,
                    {
                        "q": q,
                        "prefix_like": f"{q}%",
                        "contains_like": f"%{q}%",
                        "limit": limit,
                    },
                )
                .mappings()
                .all()
            )
        return [_suggest_row_to_payload(q, row) for row in rows]

    def suggest_fallback_papers(self, *, limit: int = 6) -> list[dict[str, Any]]:
        sql = text(
            """
            SELECT
                paper_id,
                arxiv_id,
                title,
                authors,
                summary_short,
                tldr,
                citation_count,
                published
            FROM papers
            WHERE index_status = 'indexed'
            ORDER BY
                influential_citation_count DESC NULLS LAST,
                citation_count DESC NULLS LAST,
                published DESC,
                paper_id ASC
            LIMIT :limit
            """
        )
        with self._session() as session:
            rows = session.execute(sql, {"limit": limit}).mappings().all()
        return [_fallback_row_to_payload(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        sql = text(
            """
            SELECT
                COUNT(*) AS total_papers,
                COUNT(*) FILTER (WHERE index_status = 'indexed') AS indexed_papers,
                COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS papers_with_embedding,
                COUNT(*) FILTER (WHERE summary_eli5 IS NOT NULL AND summary_eli5 <> '') AS papers_with_summary,
                MAX(indexed_at) AS last_indexed_at
            FROM papers
            """
        )
        chunk_sql = text(
            """
            SELECT
                COUNT(*) AS total_chunks,
                COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS chunks_with_embedding
            FROM text_chunks
            """
        )
        with self._session() as session:
            paper_row = session.execute(sql).mappings().one()
            chunk_row = session.execute(chunk_sql).mappings().one()
        return {
            "total_papers": int(paper_row["total_papers"] or 0),
            "indexed_papers": int(paper_row["indexed_papers"] or 0),
            "papers_with_embedding": int(paper_row["papers_with_embedding"] or 0),
            "papers_with_summary": int(paper_row["papers_with_summary"] or 0),
            "total_chunks": int(chunk_row["total_chunks"] or 0),
            "chunks_with_embedding": int(chunk_row["chunks_with_embedding"] or 0),
            "last_indexed_at": paper_row["last_indexed_at"],
        }

    _SELECT_PAPER_COLUMNS = """
        paper_id,
        arxiv_id,
        title,
        authors,
        abstract,
        tldr,
        summary_short,
        summary_eli5,
        categories,
        primary_category,
        published,
        updated,
        doi,
        comment,
        journal_ref,
        pdf_url,
        html_url,
        citation_count,
        influential_citation_count,
        semantic_scholar_id,
        code_url,
        venue,
        venue_year,
        venue_source,
        source_type,
        index_status,
        indexed_at
    """

    @staticmethod
    def _row_to_paper_result(row: Any, *, score: float, source: str) -> SearchPaperResult:
        return SearchPaperResult(
            paper_id=row["paper_id"],
            arxiv_id=row["arxiv_id"],
            title=row["title"],
            authors=row["authors"],
            abstract=row["abstract"],
            tldr=row["tldr"] or "",
            summary_short=row["summary_short"],
            summary_eli5=row["summary_eli5"],
            categories=row["categories"],
            primary_category=row["primary_category"],
            published=row["published"],
            updated=row["updated"],
            doi=row["doi"],
            comment=row["comment"],
            journal_ref=row["journal_ref"],
            pdf_url=row["pdf_url"],
            html_url=row["html_url"],
            citation_count=row["citation_count"],
            influential_citation_count=row["influential_citation_count"],
            semantic_scholar_id=row["semantic_scholar_id"],
            code_url=row["code_url"],
            venue=row["venue"],
            venue_year=row["venue_year"],
            venue_source=row["venue_source"],
            source_type=row["source_type"],
            index_status=row["index_status"],
            indexed_at=row["indexed_at"],
            score=score,
            source=source,
        )

    def search_local(
        self,
        query: str,
        *,
        limit: int,
        query_embedding: list[float] | None = None,
        rrf_k: int = 60,
        citation_boost_scale: float = 0.003,
        citation_boost_max: float = 0.015,
        recency_boost_max: float = 0.005,
        recency_half_life_years: int = 10,
    ) -> list[SearchPaperResult]:
        """Hybrid retrieval: lexical (websearch_to_tsquery + weighted tsvector) RRF-fused
        with vector cosine similarity, plus log-citation and capped recency boosts.

        Returns candidates already fused — the caller can RRF again against live sources.
        """
        candidate_pool = max(limit * 4, 50)
        lexical_sql = text(
            f"""
            SELECT * FROM (
                SELECT
                    {self._SELECT_PAPER_COLUMNS},
                    ts_rank_cd(search_tsv, websearch_to_tsquery('english', :query)) AS lexical_score,
                    similarity(coalesce(title, '') || ' ' || coalesce(tldr, ''), :query) AS title_sim
                FROM papers
                WHERE
                    search_tsv @@ websearch_to_tsquery('english', :query)
                    OR similarity(coalesce(title, '') || ' ' || coalesce(tldr, ''), :query) > 0.15
            ) scored
            ORDER BY GREATEST(lexical_score * 5, title_sim) DESC,
                     lexical_score DESC,
                     title_sim DESC,
                     citation_count DESC,
                     published DESC
            LIMIT :candidate_pool
            """
        )
        lexical_params = {"query": query, "candidate_pool": candidate_pool}

        chunk_lexical_sql = text(
            f"""
            SELECT {self._SELECT_PAPER_COLUMNS}, scored.chunk_score
            FROM (
                SELECT
                    tc.paper_id AS pid,
                    MAX(
                        ts_rank_cd(
                            to_tsvector('english', coalesce(tc.content, '')),
                            websearch_to_tsquery('english', :query)
                        )
                    ) AS chunk_score
                FROM text_chunks tc
                WHERE tc.chunk_kind <> 'full_text'
                  AND to_tsvector('english', coalesce(tc.content, ''))
                      @@ websearch_to_tsquery('english', :query)
                GROUP BY tc.paper_id
                ORDER BY chunk_score DESC
                LIMIT :candidate_pool
            ) scored
            JOIN papers ON papers.paper_id = scored.pid
            """
        )

        vector_rows: list[Any] = []
        chunk_vector_rows: list[Any] = []
        if query_embedding:
            embedding_literal = "[" + ",".join(f"{value:.6f}" for value in query_embedding) + "]"
            vector_sql = text(
                f"""
                SELECT
                    {self._SELECT_PAPER_COLUMNS},
                    1 - (embedding <=> CAST(:query_embedding AS vector)) AS vector_score
                FROM papers
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:query_embedding AS vector)
                LIMIT :candidate_pool
                """
            )
            chunk_vector_sql = text(
                f"""
                SELECT {self._SELECT_PAPER_COLUMNS}, scored.chunk_vector_score
                FROM (
                    SELECT
                        nearest.pid,
                        MAX(nearest.chunk_vector_score) AS chunk_vector_score
                    FROM (
                        SELECT
                            tc.paper_id AS pid,
                            1 - (tc.embedding <=> CAST(:query_embedding AS vector))
                                AS chunk_vector_score
                        FROM text_chunks tc
                        WHERE tc.embedding IS NOT NULL
                          AND tc.chunk_kind <> 'full_text'
                        ORDER BY tc.embedding <=> CAST(:query_embedding AS vector)
                        LIMIT :candidate_pool
                    ) nearest
                    GROUP BY nearest.pid
                ) scored
                JOIN papers ON papers.paper_id = scored.pid
                ORDER BY scored.chunk_vector_score DESC
                """
            )
            with self._session() as session:
                vector_rows = (
                    session.execute(
                        vector_sql,
                        {"query_embedding": embedding_literal, "candidate_pool": candidate_pool},
                    )
                    .mappings()
                    .all()
                )
                chunk_vector_rows = (
                    session.execute(
                        chunk_vector_sql,
                        {"query_embedding": embedding_literal, "candidate_pool": candidate_pool},
                    )
                    .mappings()
                    .all()
                )

        with self._session() as session:
            lexical_rows = session.execute(lexical_sql, lexical_params).mappings().all()
            chunk_lexical_rows = session.execute(chunk_lexical_sql, lexical_params).mappings().all()

        row_by_id: dict[str, Any] = {}
        rrf: dict[str, float] = {}
        for rank, row in enumerate(lexical_rows):
            pid = row["paper_id"]
            row_by_id[pid] = row
            rrf[pid] = rrf.get(pid, 0.0) + 1.0 / (rrf_k + rank)
        for rank, row in enumerate(vector_rows):
            pid = row["paper_id"]
            row_by_id.setdefault(pid, row)
            # slight weighting towards semantic hits
            rrf[pid] = rrf.get(pid, 0.0) + 1.2 / (rrf_k + rank)
        for rank, row in enumerate(chunk_lexical_rows):
            pid = row["paper_id"]
            row_by_id.setdefault(pid, row)
            rrf[pid] = rrf.get(pid, 0.0) + 0.8 / (rrf_k + rank)
        for rank, row in enumerate(chunk_vector_rows):
            pid = row["paper_id"]
            row_by_id.setdefault(pid, row)
            rrf[pid] = rrf.get(pid, 0.0) + 0.8 / (rrf_k + rank)

        from math import log10

        fused: list[tuple[float, Any]] = []
        for pid, score in rrf.items():
            row = row_by_id[pid]
            citation_boost = min(
                citation_boost_scale * log10(1 + float(row.get("citation_count") or 0)),
                citation_boost_max,
            )
            recency_boost = 0.0
            published = (row.get("published") or "")[:4]
            if published.isdigit() and recency_half_life_years > 0:
                age_years = max(0, utcnow().year - int(published))
                recency_boost = recency_boost_max * max(
                    0.0, 1.0 - age_years / recency_half_life_years
                )
            fused.append((score + citation_boost + recency_boost, row))

        fused.sort(key=lambda item: item[0], reverse=True)
        return [self._row_to_paper_result(row, score=score, source="local") for score, row in fused[:limit]]

    def search_sections(
        self,
        query: str,
        *,
        limit: int,
        result_set_id: str | None = None,
        paper_ids: list[str] | None = None,
        query_embedding: list[float] | None = None,
        rrf_k: int = 60,
    ) -> list[SearchSectionResult]:
        candidate_pool = max(limit * 4, 50)
        params: dict[str, Any] = {
            "query": query,
            "candidate_pool": candidate_pool,
            "section_kinds": list(PAPER_SECTION_CHUNK_KINDS),
        }
        joins = ["JOIN papers p ON p.paper_id = tc.paper_id"]
        # Namespace gate (#5 PR 2): paper search operates on paper-section
        # chunk kinds only. Code chunks (``code_readme`` etc.) live in the
        # same ``text_chunks`` table but are reachable only through the
        # paper-scoped code retrieval primitives.
        filters = [
            "tc.chunk_kind = ANY(CAST(:section_kinds AS text[]))",
            "(to_tsvector('english', coalesce(tc.content, '')) @@ websearch_to_tsquery('english', :query) "
            "OR similarity(coalesce(tc.content, ''), :query) > 0.1)",
        ]
        if result_set_id:
            joins.append("JOIN result_items ri ON ri.paper_id = tc.paper_id")
            filters.append("ri.result_set_id = :result_set_id")
            params["result_set_id"] = result_set_id
        if paper_ids:
            filters.append("tc.paper_id = ANY(CAST(:paper_ids AS text[]))")
            params["paper_ids"] = paper_ids

        lexical_sql = text(
            f"""
            SELECT
                tc.chunk_id,
                tc.paper_id,
                p.arxiv_id,
                p.title,
                tc.path,
                tc.chunk_kind,
                tc.content,
                tc.section_depth,
                p.citation_count,
                p.published,
                ts_rank_cd(
                    to_tsvector('english', coalesce(tc.content, '')),
                    websearch_to_tsquery('english', :query)
                ) AS lexical_score,
                CASE tc.chunk_kind
                    WHEN 'section' THEN 0.30
                    WHEN 'table' THEN 0.20
                    WHEN 'figure_caption' THEN 0.15
                    WHEN 'full_text' THEN 0.05
                    ELSE 0.0
                END AS kind_score,
                CASE
                    WHEN tc.section_depth >= 3 THEN 0.5
                    WHEN tc.section_depth = 2 THEN 0.85
                    ELSE 1.0
                END AS depth_weight
            FROM text_chunks tc
            {" ".join(joins)}
            WHERE {" AND ".join(filters)}
            ORDER BY lexical_score DESC, depth_weight DESC, kind_score DESC, p.citation_count DESC, p.published DESC
            LIMIT :candidate_pool
            """
        )

        with self._session() as session:
            lexical_rows = session.execute(lexical_sql, params).mappings().all()

        vector_rows: list[Any] = []
        if query_embedding:
            embedding_literal = "[" + ",".join(f"{value:.6f}" for value in query_embedding) + "]"
            vector_joins = ["JOIN papers p ON p.paper_id = tc.paper_id"]
            vector_filters = [
                "tc.embedding IS NOT NULL",
                "tc.chunk_kind = ANY(CAST(:section_kinds AS text[]))",
            ]
            vector_params: dict[str, Any] = {
                "query_embedding": embedding_literal,
                "candidate_pool": candidate_pool,
                "section_kinds": list(PAPER_SECTION_CHUNK_KINDS),
            }
            if result_set_id:
                vector_joins.append("JOIN result_items ri ON ri.paper_id = tc.paper_id")
                vector_filters.append("ri.result_set_id = :result_set_id")
                vector_params["result_set_id"] = result_set_id
            if paper_ids:
                vector_filters.append("tc.paper_id = ANY(CAST(:paper_ids AS text[]))")
                vector_params["paper_ids"] = paper_ids
            vector_sql = text(
                f"""
                SELECT
                    tc.chunk_id,
                    tc.paper_id,
                    p.arxiv_id,
                    p.title,
                    tc.path,
                    tc.chunk_kind,
                    tc.content,
                    tc.section_depth,
                    p.citation_count,
                    p.published,
                    CASE tc.chunk_kind
                        WHEN 'section' THEN 0.30
                        WHEN 'table' THEN 0.20
                        WHEN 'figure_caption' THEN 0.15
                        WHEN 'full_text' THEN 0.05
                        ELSE 0.0
                    END AS kind_score,
                    CASE
                        WHEN tc.section_depth >= 3 THEN 0.5
                        WHEN tc.section_depth = 2 THEN 0.85
                        ELSE 1.0
                    END AS depth_weight,
                    1 - (tc.embedding <=> CAST(:query_embedding AS vector)) AS vector_score
                FROM text_chunks tc
                {" ".join(vector_joins)}
                WHERE {" AND ".join(vector_filters)}
                ORDER BY tc.embedding <=> CAST(:query_embedding AS vector)
                LIMIT :candidate_pool
                """
            )
            with self._session() as session:
                vector_rows = session.execute(vector_sql, vector_params).mappings().all()

        row_by_id: dict[str, Any] = {}
        rrf: dict[str, float] = {}
        for rank, row in enumerate(lexical_rows):
            cid = row["chunk_id"]
            row_by_id[cid] = row
            depth_w = float(row.get("depth_weight") or 1.0)
            rrf[cid] = rrf.get(cid, 0.0) + depth_w * (1.0 / (rrf_k + rank)) + float(row["kind_score"] or 0) * 0.05
        for rank, row in enumerate(vector_rows):
            cid = row["chunk_id"]
            row_by_id.setdefault(cid, row)
            depth_w = float(row.get("depth_weight") or 1.0)
            rrf[cid] = rrf.get(cid, 0.0) + depth_w * (1.2 / (rrf_k + rank))

        fused = sorted(
            ((score, row_by_id[cid]) for cid, score in rrf.items()),
            key=lambda pair: pair[0],
            reverse=True,
        )
        results: list[SearchSectionResult] = []
        for score, row in fused[:limit]:
            content = row["content"] or ""
            results.append(
                SearchSectionResult(
                    paper_id=row["paper_id"],
                    arxiv_id=row["arxiv_id"],
                    title=row["title"],
                    path=row["path"],
                    chunk_kind=row["chunk_kind"],
                    preview=content[:320].strip(),
                    score=score,
                )
            )
        return results

    def create_browser_user(self, *, email: str, display_name: str, password_hash: str | None) -> BrowserUser:
        row = BrowserUserORM(
            user_id=new_id("usr"),
            email=email,
            display_name=display_name,
            password_hash=password_hash,
        )
        with self._session() as session:
            session.add(row)
            self._ensure_default_project_row(session, row.user_id)
        return _browser_user_from_orm(row)

    def get_browser_user_by_email(self, email: str) -> BrowserUser | None:
        with self._session() as session:
            row = session.scalar(select(BrowserUserORM).where(BrowserUserORM.email == email))
            return _browser_user_from_orm(row) if row else None

    def get_browser_user_by_id(self, user_id: str) -> BrowserUser | None:
        with self._session() as session:
            row = session.get(BrowserUserORM, user_id)
            return _browser_user_from_orm(row) if row else None

    def get_browser_user_by_google_subject(self, google_subject: str) -> BrowserUser | None:
        with self._session() as session:
            row = session.execute(
                select(BrowserUserORM)
                .join(GoogleIdentityORM, GoogleIdentityORM.user_id == BrowserUserORM.user_id)
                .where(GoogleIdentityORM.google_subject == google_subject)
            ).scalar_one_or_none()
            return _browser_user_from_orm(row) if row else None

    def set_browser_user_password(self, user_id: str, password_hash: str) -> BrowserUser | None:
        with self._session() as session:
            row = session.get(BrowserUserORM, user_id)
            if row is None:
                return None
            row.password_hash = password_hash
            row.updated_at = utcnow()
        return self.get_browser_user_by_id(user_id)

    def get_browser_password_hash(self, user_id: str) -> str | None:
        with self._session() as session:
            row = session.get(BrowserUserORM, user_id)
            return row.password_hash if row else None

    def create_browser_session(self, *, user_id: str, token_hash_value: str, expires_at: Any) -> None:
        with self._session() as session:
            session.add(
                BrowserSessionORM(
                    browser_session_id=new_id("sess"),
                    user_id=user_id,
                    token_hash=token_hash_value,
                    expires_at=expires_at,
                    last_seen_at=utcnow(),
                )
            )

    def get_browser_user_by_session_token_hash(self, token_hash_value: str) -> BrowserUser | None:
        with self._session() as session:
            session.execute(delete(BrowserSessionORM).where(BrowserSessionORM.expires_at < utcnow()))
            session_row = session.scalar(
                select(BrowserSessionORM).where(BrowserSessionORM.token_hash == token_hash_value)
            )
            if session_row is None or session_row.expires_at < utcnow():
                return None
            session_row.last_seen_at = utcnow()
            user = session.get(BrowserUserORM, session_row.user_id)
            return _browser_user_from_orm(user) if user else None

    def delete_browser_session(self, token_hash_value: str) -> None:
        with self._session() as session:
            session.execute(delete(BrowserSessionORM).where(BrowserSessionORM.token_hash == token_hash_value))

    def create_password_reset_token(self, *, user_id: str, token_hash_value: str, expires_at: Any) -> None:
        with self._session() as session:
            session.add(
                PasswordResetTokenORM(
                    password_reset_token_id=new_id("prt"),
                    user_id=user_id,
                    token_hash=token_hash_value,
                    expires_at=expires_at,
                )
            )

    def consume_password_reset_token(self, token_hash_value: str) -> BrowserUser | None:
        with self._session() as session:
            row = session.scalar(
                select(PasswordResetTokenORM).where(
                    PasswordResetTokenORM.token_hash == token_hash_value,
                    PasswordResetTokenORM.used_at.is_(None),
                )
            )
            if row is None or row.expires_at < utcnow():
                return None
            row.used_at = utcnow()
            user = session.get(BrowserUserORM, row.user_id)
            return _browser_user_from_orm(user) if user else None

    def link_google_identity(self, *, user_id: str, google_subject: str, email: str) -> None:
        statement = insert(GoogleIdentityORM).values(
            google_identity_id=new_id("gid"),
            user_id=user_id,
            google_subject=google_subject,
            email=email,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[GoogleIdentityORM.google_subject],
            set_={"user_id": user_id, "email": email},
        )
        with self._session() as session:
            session.execute(statement)

    def save_anthropic_api_key(self, *, user_id: str, encrypted_api_key: str, hint: str) -> None:
        statement = insert(AnthropicAPIKeyORM).values(
            anthropic_api_key_id=new_id("uak"),
            user_id=user_id,
            encrypted_api_key=encrypted_api_key,
            api_key_hint=hint,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[AnthropicAPIKeyORM.user_id],
            set_={"encrypted_api_key": encrypted_api_key, "api_key_hint": hint, "updated_at": utcnow()},
        )
        with self._session() as session:
            session.execute(statement)

    def save_openai_api_key(self, *, user_id: str, encrypted_api_key: str, hint: str) -> None:
        statement = insert(OpenAIAPIKeyORM).values(
            openai_api_key_id=new_id("uak"),
            user_id=user_id,
            encrypted_api_key=encrypted_api_key,
            api_key_hint=hint,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[OpenAIAPIKeyORM.user_id],
            set_={"encrypted_api_key": encrypted_api_key, "api_key_hint": hint, "updated_at": utcnow()},
        )
        with self._session() as session:
            session.execute(statement)

    def get_anthropic_api_key_record(self, user_id: str) -> dict[str, str] | None:
        with self._session() as session:
            row = session.scalar(select(AnthropicAPIKeyORM).where(AnthropicAPIKeyORM.user_id == user_id))
            if row is None:
                return None
            return {"encrypted_api_key": row.encrypted_api_key, "api_key_hint": row.api_key_hint}

    def get_openai_api_key_record(self, user_id: str) -> dict[str, str] | None:
        with self._session() as session:
            row = session.scalar(select(OpenAIAPIKeyORM).where(OpenAIAPIKeyORM.user_id == user_id))
            if row is None:
                return None
            return {"encrypted_api_key": row.encrypted_api_key, "api_key_hint": row.api_key_hint}

    def delete_anthropic_api_key(self, user_id: str) -> None:
        with self._session() as session:
            session.execute(delete(AnthropicAPIKeyORM).where(AnthropicAPIKeyORM.user_id == user_id))

    def delete_openai_api_key(self, user_id: str) -> None:
        with self._session() as session:
            session.execute(delete(OpenAIAPIKeyORM).where(OpenAIAPIKeyORM.user_id == user_id))

    def get_browser_user_settings(self, user_id: str) -> BrowserUserSettingsResponse:
        with self._session() as session:
            row = session.scalar(select(BrowserUserSettingsORM).where(BrowserUserSettingsORM.user_id == user_id))
            settings = _browser_user_settings_from_orm(row)
            openai_record = session.scalar(select(OpenAIAPIKeyORM).where(OpenAIAPIKeyORM.user_id == user_id))
            if openai_record is not None:
                settings = settings.model_copy(update={"openai_api_key_hint": openai_record.api_key_hint or ""})
            return settings

    def get_modal_credentials_record(self, user_id: str) -> dict[str, str] | None:
        with self._session() as session:
            row = session.scalar(select(BrowserUserSettingsORM).where(BrowserUserSettingsORM.user_id == user_id))
            if row is None or not row.encrypted_modal_token_id or not row.encrypted_modal_token_secret:
                return None
            return {
                "encrypted_token_id": row.encrypted_modal_token_id,
                "encrypted_token_secret": row.encrypted_modal_token_secret,
                "token_id_hint": row.modal_token_id_hint or "",
            }

    def get_github_connection_record(self, user_id: str) -> dict[str, str] | None:
        with self._session() as session:
            row = session.scalar(select(BrowserUserSettingsORM).where(BrowserUserSettingsORM.user_id == user_id))
            if row is None or not row.encrypted_github_access_token or not row.github_login:
                return None
            return {
                "github_user_id": row.github_user_id or "",
                "github_login": row.github_login or "",
                "encrypted_access_token": row.encrypted_github_access_token,
                "scope": row.github_scope or "",
            }

    def save_modal_credentials(
        self,
        *,
        user_id: str,
        encrypted_token_id: str,
        encrypted_token_secret: str,
        token_id_hint: str,
    ) -> None:
        with self._session() as session:
            row = self._get_or_create_browser_user_settings_row(session, user_id)
            row.encrypted_modal_token_id = encrypted_token_id
            row.encrypted_modal_token_secret = encrypted_token_secret
            row.modal_token_id_hint = token_id_hint
            row.updated_at = utcnow()

    def delete_modal_credentials(self, user_id: str) -> None:
        with self._session() as session:
            row = session.scalar(select(BrowserUserSettingsORM).where(BrowserUserSettingsORM.user_id == user_id))
            if row is None:
                return
            row.encrypted_modal_token_id = None
            row.encrypted_modal_token_secret = None
            row.modal_token_id_hint = ""
            row.updated_at = utcnow()

    def set_weekly_papers_review_email(self, *, user_id: str, enabled: bool) -> None:
        with self._session() as session:
            row = self._get_or_create_browser_user_settings_row(session, user_id)
            row.weekly_papers_review_email = enabled
            row.updated_at = utcnow()

    def set_appearance_theme(self, *, user_id: str, theme: str) -> None:
        with self._session() as session:
            row = self._get_or_create_browser_user_settings_row(session, user_id)
            row.appearance_theme = theme if theme in {"light", "dark"} else "light"
            row.updated_at = utcnow()

    def save_github_connection(
        self,
        *,
        user_id: str,
        github_user_id: str,
        github_login: str,
        encrypted_access_token: str,
        scope: str,
    ) -> None:
        with self._session() as session:
            existing = session.scalar(
                select(BrowserUserSettingsORM).where(
                    BrowserUserSettingsORM.github_user_id == github_user_id,
                    BrowserUserSettingsORM.user_id != user_id,
                )
            )
            if existing is not None:
                existing.github_user_id = None
                existing.github_login = ""
                existing.encrypted_github_access_token = None
                existing.github_scope = ""
                existing.updated_at = utcnow()
            row = self._get_or_create_browser_user_settings_row(session, user_id)
            row.github_user_id = github_user_id
            row.github_login = github_login
            row.encrypted_github_access_token = encrypted_access_token
            row.github_scope = scope
            row.updated_at = utcnow()

    def delete_github_connection(self, user_id: str) -> None:
        with self._session() as session:
            row = session.scalar(select(BrowserUserSettingsORM).where(BrowserUserSettingsORM.user_id == user_id))
            if row is None:
                return
            row.github_user_id = None
            row.github_login = ""
            row.encrypted_github_access_token = None
            row.github_scope = ""
            row.updated_at = utcnow()

    def create_uploaded_document(
        self,
        *,
        user_id: str,
        title: str,
        source_filename: str,
        content_type: str,
        tags: list[str],
        storage_path: str,
        extracted_text_path: str | None,
        status: str,
    ) -> UploadedDocumentResponse:
        row = UploadedDocumentORM(
            document_id=new_id("doc"),
            user_id=user_id,
            title=title,
            source_filename=source_filename,
            content_type=content_type,
            tags=tags,
            storage_path=storage_path,
            extracted_text_path=extracted_text_path,
            status=status,
        )
        with self._session() as session:
            session.add(row)
        return _uploaded_document_from_orm(row)

    def update_uploaded_document(
        self,
        *,
        document_id: str,
        title: str,
        tags: list[str],
        extracted_text_path: str | None,
        status: str,
    ) -> UploadedDocumentResponse | None:
        with self._session() as session:
            row = session.get(UploadedDocumentORM, document_id)
            if row is None:
                return None
            row.title = title
            row.tags = tags
            row.extracted_text_path = extracted_text_path
            row.status = status
            row.updated_at = utcnow()
        return self.get_uploaded_document(document_id)

    def get_uploaded_document(self, document_id: str) -> UploadedDocumentResponse | None:
        with self._session() as session:
            row = session.get(UploadedDocumentORM, document_id)
            return _uploaded_document_from_orm(row) if row else None

    def replace_uploaded_document_chunks(self, document_id: str, chunks: list[dict[str, str]]) -> None:
        with self._session() as session:
            session.execute(delete(UploadedDocumentChunkORM).where(UploadedDocumentChunkORM.document_id == document_id))
            for chunk in chunks:
                session.add(
                    UploadedDocumentChunkORM(
                        chunk_id=new_id("dchunk"),
                        document_id=document_id,
                        path=chunk["path"],
                        chunk_kind=chunk["chunk_kind"],
                        content=chunk["content"],
                    )
                )

    def list_uploaded_documents(self, user_id: str) -> list[UploadedDocumentResponse]:
        with self._session() as session:
            rows = session.scalars(
                select(UploadedDocumentORM)
                .where(UploadedDocumentORM.user_id == user_id)
                .order_by(UploadedDocumentORM.updated_at.desc())
            ).all()
        return [_uploaded_document_from_orm(row) for row in rows]

    def search_uploaded_document_sections(
        self,
        *,
        user_id: str,
        query: str,
        limit: int,
        tag_filters: list[str] | None = None,
    ) -> list[UploadedDocumentSectionResult]:
        query_lower = query.lower()
        with self._session() as session:
            rows = session.execute(
                select(UploadedDocumentChunkORM, UploadedDocumentORM)
                .join(UploadedDocumentORM, UploadedDocumentORM.document_id == UploadedDocumentChunkORM.document_id)
                .where(
                    UploadedDocumentORM.user_id == user_id,
                    or_(
                        func.lower(UploadedDocumentChunkORM.content).contains(query_lower),
                        func.lower(UploadedDocumentORM.title).contains(query_lower),
                    ),
                )
                .order_by(UploadedDocumentORM.updated_at.desc())
            ).all()
        required_tags = set(tag_filters or [])
        results: list[UploadedDocumentSectionResult] = []
        for chunk, document in rows:
            tags = list(document.tags or [])
            if required_tags and not required_tags.issubset(set(tags)):
                continue
            content = chunk.content or ""
            score = (len(query_lower) / max(len(content), 1)) + (0.2 if chunk.chunk_kind == "section" else 0.05)
            results.append(
                UploadedDocumentSectionResult(
                    document_id=document.document_id,
                    title=document.title,
                    path=chunk.path,
                    chunk_kind=chunk.chunk_kind,
                    preview=content[:320],
                    tags=tags,
                    score=score,
                )
            )
        results.sort(key=lambda item: item.score or 0, reverse=True)
        return results[:limit]

    def ensure_default_project(self, user_id: str) -> ProjectResponse:
        with self._session() as session:
            project = self._ensure_default_project_row(session, user_id)
            return ProjectResponse(**_project_from_orm(project).model_dump())

    def get_default_project(self, user_id: str) -> ProjectResponse | None:
        with self._session() as session:
            project = session.scalar(
                select(ProjectORM).where(ProjectORM.user_id == user_id, ProjectORM.is_default.is_(True))
            )
            if project is None:
                return None
            thread_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(ChatThreadORM)
                    .where(ChatThreadORM.project_id == project.project_id)
                )
                or 0
            )
            file_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(PythonFileORM)
                    .where(PythonFileORM.project_id == project.project_id)
                )
                or 0
            )
            return ProjectResponse(**_project_from_orm(project, session_count=thread_count + file_count).model_dump())

    def list_projects(self, user_id: str) -> list[ProjectSummary]:
        with self._session() as session:
            projects = session.scalars(
                select(ProjectORM)
                .where(ProjectORM.user_id == user_id)
                .order_by(ProjectORM.is_default.desc(), ProjectORM.updated_at.desc(), ProjectORM.created_at.asc())
            ).all()
            thread_counts = {
                project_id: count
                for project_id, count in session.execute(
                    select(ChatThreadORM.project_id, func.count())
                    .where(ChatThreadORM.user_id == user_id, ChatThreadORM.project_id.is_not(None))
                    .group_by(ChatThreadORM.project_id)
                ).all()
            }
            file_counts = {
                project_id: count
                for project_id, count in session.execute(
                    select(PythonFileORM.project_id, func.count())
                    .where(PythonFileORM.user_id == user_id)
                    .group_by(PythonFileORM.project_id)
                ).all()
            }
            project_ids = [project.project_id for project in projects]
            repo_urls = (
                {
                    project_id: repo_url
                    for project_id, repo_url in session.execute(
                        select(ProjectRepoSyncORM.project_id, ProjectRepoSyncORM.github_repo_url).where(
                            ProjectRepoSyncORM.user_id == user_id,
                            ProjectRepoSyncORM.project_id.in_(project_ids),
                        )
                    ).all()
                }
                if project_ids
                else {}
            )
        return [
            _project_from_orm(
                project,
                session_count=int(thread_counts.get(project.project_id, 0))
                + int(file_counts.get(project.project_id, 0)),
                github_repo_url=repo_urls.get(project.project_id, "") or "",
            )
            for project in projects
        ]

    def get_project(self, *, user_id: str, project_id: str) -> ProjectResponse | None:
        with self._session() as session:
            project = session.scalar(
                select(ProjectORM).where(ProjectORM.project_id == project_id, ProjectORM.user_id == user_id)
            )
            if project is None:
                return None
            thread_count = int(
                session.scalar(
                    select(func.count()).select_from(ChatThreadORM).where(ChatThreadORM.project_id == project_id)
                )
                or 0
            )
            file_count = int(
                session.scalar(
                    select(func.count()).select_from(PythonFileORM).where(PythonFileORM.project_id == project_id)
                )
                or 0
            )
            repo_url = (
                session.scalar(
                    select(ProjectRepoSyncORM.github_repo_url).where(
                        ProjectRepoSyncORM.project_id == project_id,
                        ProjectRepoSyncORM.user_id == user_id,
                    )
                )
                or ""
            )
        return ProjectResponse(
            **_project_from_orm(
                project,
                session_count=thread_count + file_count,
                github_repo_url=repo_url,
            ).model_dump()
        )

    def create_project(self, *, user_id: str, name: str, description: str) -> ProjectResponse:
        row = ProjectORM(
            project_id=new_id("proj"),
            user_id=user_id,
            name=name,
            description=description,
            is_default=False,
        )
        with self._session() as session:
            session.add(row)
        return ProjectResponse(**_project_from_orm(row).model_dump())

    def update_project(self, *, user_id: str, project_id: str, name: str, description: str) -> ProjectResponse | None:
        with self._session() as session:
            row = session.scalar(
                select(ProjectORM).where(ProjectORM.project_id == project_id, ProjectORM.user_id == user_id)
            )
            if row is None:
                return None
            row.name = name
            row.description = description
            row.updated_at = utcnow()
        return self.get_project(user_id=user_id, project_id=project_id)

    def delete_project(self, *, user_id: str, project_id: str) -> bool:
        with self._session() as session:
            project = session.scalar(
                select(ProjectORM).where(ProjectORM.project_id == project_id, ProjectORM.user_id == user_id)
            )
            if project is None:
                return False
            session.execute(
                delete(ChatThreadORM).where(ChatThreadORM.user_id == user_id, ChatThreadORM.project_id == project_id)
            )
            session.execute(
                delete(ProjectORM).where(ProjectORM.project_id == project_id, ProjectORM.user_id == user_id)
            )
        return True

    def list_project_chat_threads(
        self,
        *,
        user_id: str,
        project_id: str,
        limit: int,
        offset: int,
    ) -> ProjectSessionPageResponse | None:
        with self._session() as session:
            project = session.scalar(
                select(ProjectORM).where(ProjectORM.project_id == project_id, ProjectORM.user_id == user_id)
            )
            if project is None:
                return None
            total_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(ChatThreadORM)
                    .where(ChatThreadORM.user_id == user_id, ChatThreadORM.project_id == project_id)
                )
                or 0
            )
            threads = session.scalars(
                select(ChatThreadORM)
                .where(ChatThreadORM.user_id == user_id, ChatThreadORM.project_id == project_id)
                .order_by(ChatThreadORM.updated_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            results: list[ChatThreadSummary] = []
            for thread in threads:
                last_item = session.scalar(
                    select(ChatThreadItemORM)
                    .where(ChatThreadItemORM.thread_id == thread.thread_id)
                    .order_by(ChatThreadItemORM.sequence.desc(), ChatThreadItemORM.created_at.desc())
                    .limit(1)
                )
                preview = (last_item.content_text[:120] if last_item else "").strip()
                results.append(_chat_thread_summary_from_orm(thread, last_message_preview=preview))
        return ProjectSessionPageResponse(
            project_id=project_id,
            threads=results,
            offset=offset,
            limit=limit,
            total_count=total_count,
            has_more=offset + len(results) < total_count,
        )

    def list_project_items(
        self,
        *,
        user_id: str,
        project_id: str,
        limit: int,
        offset: int,
        item_kind: str | None = None,
    ) -> ProjectItemPageResponse | None:
        with self._session() as session:
            project = session.scalar(
                select(ProjectORM).where(ProjectORM.project_id == project_id, ProjectORM.user_id == user_id)
            )
            if project is None:
                return None

            thread_rows = session.scalars(
                select(ChatThreadORM).where(ChatThreadORM.user_id == user_id, ChatThreadORM.project_id == project_id)
            ).all()
            items: list[ProjectItemSummary] = []
            for thread in thread_rows:
                last_item = session.scalar(
                    select(ChatThreadItemORM)
                    .where(ChatThreadItemORM.thread_id == thread.thread_id)
                    .order_by(ChatThreadItemORM.sequence.desc(), ChatThreadItemORM.created_at.desc())
                    .limit(1)
                )
                preview = (last_item.content_text[:120] if last_item else "").strip()
                items.append(
                    _project_item_from_thread_summary(
                        _chat_thread_summary_from_orm(thread, last_message_preview=preview)
                    )
                )

            file_rows = session.scalars(
                select(PythonFileORM).where(PythonFileORM.user_id == user_id, PythonFileORM.project_id == project_id)
            ).all()
            items.extend(_project_item_from_python_file(_python_file_summary_from_orm(row)) for row in file_rows)

        if item_kind:
            items = [item for item in items if item.item_kind == item_kind]
        items.sort(key=lambda item: (item.updated_at, item.created_at), reverse=True)
        page = items[offset : offset + limit]
        return ProjectItemPageResponse(
            project_id=project_id,
            items=page,
            offset=offset,
            limit=limit,
            total_count=len(items),
            has_more=offset + len(page) < len(items),
        )

    def list_project_python_files(
        self,
        *,
        user_id: str,
        project_id: str,
    ) -> list[PythonFileResponse] | None:
        with self._session() as session:
            project = session.scalar(
                select(ProjectORM).where(ProjectORM.project_id == project_id, ProjectORM.user_id == user_id)
            )
            if project is None:
                return None
            rows = session.scalars(
                select(PythonFileORM)
                .where(PythonFileORM.user_id == user_id, PythonFileORM.project_id == project_id)
                .order_by(PythonFileORM.updated_at.desc())
            ).all()
        return [_python_file_from_orm(row) for row in rows]

    def create_python_file(
        self,
        *,
        user_id: str,
        project_id: str,
        filename: str,
        content: str = "",
        file_kind: str = "python",
        repo_path: str | None = None,
    ) -> PythonFileResponse | None:
        with self._session() as session:
            project = session.scalar(
                select(ProjectORM).where(ProjectORM.project_id == project_id, ProjectORM.user_id == user_id)
            )
            if project is None:
                return None
            existing_repo_paths = session.scalars(
                select(PythonFileORM.repo_path).where(PythonFileORM.project_id == project_id)
            ).all()
            base_repo_path = _repo_path_for_filename(filename, file_kind, repo_path)
            resolved_repo_path = _resolve_unique_repo_path(base_repo_path, existing_repo_paths)
            resolved_filename = _filename_from_repo_path(resolved_repo_path)
            row = PythonFileORM(
                file_id=new_id("pyf"),
                user_id=user_id,
                project_id=project_id,
                filename=resolved_filename,
                file_kind=file_kind,
                repo_path=resolved_repo_path,
                content=content,
            )
            session.add(row)
            project.updated_at = utcnow()
        return _python_file_from_orm(row)

    def get_python_file(self, *, user_id: str, file_id: str) -> PythonFileResponse | None:
        with self._session() as session:
            row = session.scalar(
                select(PythonFileORM).where(PythonFileORM.file_id == file_id, PythonFileORM.user_id == user_id)
            )
            if row is None:
                return None
        return _python_file_from_orm(row)

    def update_python_file(
        self,
        *,
        user_id: str,
        file_id: str,
        filename: str | None = None,
        content: str | None = None,
        repo_path: str | None = None,
    ) -> PythonFileResponse | None:
        with self._session() as session:
            row = session.scalar(
                select(PythonFileORM).where(PythonFileORM.file_id == file_id, PythonFileORM.user_id == user_id)
            )
            if row is None:
                return None
            updated_at = utcnow()
            next_repo_path = row.repo_path or _repo_path_for_filename(row.filename, row.file_kind or "python")
            next_filename = row.filename
            if filename is not None and filename.strip():
                existing_repo_paths = session.scalars(
                    select(PythonFileORM.repo_path).where(PythonFileORM.project_id == row.project_id)
                ).all()
                requested_repo_path = _repo_path_with_filename(
                    next_repo_path,
                    filename,
                )
                next_repo_path = _resolve_unique_repo_path(
                    requested_repo_path,
                    existing_repo_paths,
                    exclude_repo_path=row.repo_path,
                )
                next_filename = _filename_from_repo_path(next_repo_path)
            if repo_path is not None and repo_path.strip():
                existing_repo_paths = session.scalars(
                    select(PythonFileORM.repo_path).where(PythonFileORM.project_id == row.project_id)
                ).all()
                next_repo_path = _resolve_unique_repo_path(
                    repo_path.strip(),
                    existing_repo_paths,
                    exclude_repo_path=row.repo_path,
                )
                next_filename = _filename_from_repo_path(next_repo_path)
            row.filename = next_filename
            row.repo_path = next_repo_path
            if content is not None:
                row.content = content
            row.updated_at = updated_at
            project = session.get(ProjectORM, row.project_id)
            if project is not None:
                project.updated_at = updated_at
        return self.get_python_file(user_id=user_id, file_id=file_id)

    def get_project_python_file_by_path(
        self,
        *,
        user_id: str,
        project_id: str,
        repo_path: str,
    ) -> PythonFileResponse | None:
        with self._session() as session:
            row = session.scalar(
                select(PythonFileORM).where(
                    PythonFileORM.user_id == user_id,
                    PythonFileORM.project_id == project_id,
                    PythonFileORM.repo_path == repo_path,
                )
            )
            if row is None:
                return None
        return _python_file_from_orm(row)

    def delete_python_file(self, *, user_id: str, file_id: str) -> bool:
        with self._session() as session:
            row = session.scalar(
                select(PythonFileORM).where(PythonFileORM.file_id == file_id, PythonFileORM.user_id == user_id)
            )
            if row is None:
                return False
            updated_at = utcnow()
            project = session.get(ProjectORM, row.project_id)
            if project is not None:
                project.updated_at = updated_at
            session.delete(row)
        return True

    def get_project_repo_sync(self, *, user_id: str, project_id: str) -> ProjectRepoSyncResponse | None:
        with self._session() as session:
            project = session.scalar(
                select(ProjectORM).where(ProjectORM.project_id == project_id, ProjectORM.user_id == user_id)
            )
            if project is None:
                return None
            row = session.scalar(
                select(ProjectRepoSyncORM).where(
                    ProjectRepoSyncORM.project_id == project_id,
                    ProjectRepoSyncORM.user_id == user_id,
                )
            )
        return _project_repo_sync_from_orm(row, project_id=project_id)

    def save_project_repo_sync(
        self,
        *,
        user_id: str,
        project_id: str,
        github_repo_name: str,
        github_repo_url: str,
        default_branch: str,
        last_sync_commit_sha: str,
        last_sync_commit_url: str,
        github_owner_login: str = "",
        base_branch: str | None = None,
        last_synced_at: Any = None,
    ) -> ProjectRepoSyncResponse | None:
        with self._session() as session:
            project = session.scalar(
                select(ProjectORM).where(ProjectORM.project_id == project_id, ProjectORM.user_id == user_id)
            )
            if project is None:
                return None
            row = session.scalar(
                select(ProjectRepoSyncORM).where(
                    ProjectRepoSyncORM.project_id == project_id,
                    ProjectRepoSyncORM.user_id == user_id,
                )
            )
            if row is None:
                row = ProjectRepoSyncORM(
                    project_repo_sync_id=new_id("prs"),
                    user_id=user_id,
                    project_id=project_id,
                )
                session.add(row)
            row.github_owner_login = github_owner_login
            row.github_repo_name = github_repo_name
            row.github_repo_url = github_repo_url
            row.base_branch = base_branch or default_branch
            row.default_branch = default_branch
            row.last_sync_commit_sha = last_sync_commit_sha
            row.last_sync_commit_url = last_sync_commit_url
            row.last_synced_at = last_synced_at
            row.updated_at = utcnow()
        return self.get_project_repo_sync(user_id=user_id, project_id=project_id)

    def create_chat_thread(
        self,
        *,
        user_id: str,
        title: str,
        project_id: str,
        project_name: str,
        project_instructions: str,
    ) -> ChatThreadSummary:
        row = ChatThreadORM(
            thread_id=new_id("thread"),
            user_id=user_id,
            project_id=project_id,
            project_name=project_name,
            project_instructions=project_instructions,
            title=title or "New chat",
        )
        with self._session() as session:
            session.add(row)
            project = session.get(ProjectORM, project_id)
            if project is not None:
                project.updated_at = utcnow()
        return _chat_thread_summary_from_orm(row)

    def rename_chat_thread(self, *, thread_id: str, title: str) -> None:
        with self._session() as session:
            row = session.get(ChatThreadORM, thread_id)
            if row:
                row.title = title
                row.updated_at = utcnow()
                project = session.get(ProjectORM, row.project_id) if row.project_id else None
                if project is not None:
                    project.updated_at = row.updated_at

    def move_chat_thread_to_project(
        self,
        *,
        user_id: str,
        thread_id: str,
        project_id: str,
        project_name: str,
        project_instructions: str,
    ) -> ChatThreadResponse | None:
        with self._session() as session:
            row = session.scalar(
                select(ChatThreadORM).where(ChatThreadORM.thread_id == thread_id, ChatThreadORM.user_id == user_id)
            )
            if row is None:
                return None
            updated_at = utcnow()
            previous_project_id = row.project_id
            row.project_id = project_id
            row.project_name = project_name
            row.project_instructions = project_instructions
            row.updated_at = updated_at
            if previous_project_id and previous_project_id != project_id:
                previous_project = session.get(ProjectORM, previous_project_id)
                if previous_project is not None:
                    previous_project.updated_at = updated_at
            next_project = session.get(ProjectORM, project_id)
            if next_project is not None:
                next_project.updated_at = updated_at
        return self.get_chat_thread(user_id=user_id, thread_id=thread_id)

    def list_chat_threads(self, user_id: str) -> list[ChatThreadSummary]:
        with self._session() as session:
            threads = session.scalars(
                select(ChatThreadORM).where(ChatThreadORM.user_id == user_id).order_by(ChatThreadORM.updated_at.desc())
            ).all()
            results: list[ChatThreadSummary] = []
            for thread in threads:
                last_item = session.scalar(
                    select(ChatThreadItemORM)
                    .where(ChatThreadItemORM.thread_id == thread.thread_id)
                    .order_by(ChatThreadItemORM.sequence.desc(), ChatThreadItemORM.created_at.desc())
                    .limit(1)
                )
                preview = (last_item.content_text[:120] if last_item else "").strip()
                results.append(_chat_thread_summary_from_orm(thread, last_message_preview=preview))
        return results

    def get_chat_thread(self, *, user_id: str, thread_id: str) -> ChatThreadResponse | None:
        with self._session() as session:
            thread = session.scalar(
                select(ChatThreadORM).where(ChatThreadORM.thread_id == thread_id, ChatThreadORM.user_id == user_id)
            )
            if thread is None:
                return None
            turn_rows = session.scalars(
                select(ChatTurnORM).where(ChatTurnORM.thread_id == thread_id).order_by(ChatTurnORM.started_at.asc())
            ).all()
            item_rows = session.scalars(
                select(ChatThreadItemORM)
                .where(ChatThreadItemORM.thread_id == thread_id)
                .order_by(ChatThreadItemORM.sequence.asc(), ChatThreadItemORM.created_at.asc())
            ).all()
        items = [_chat_thread_item_from_orm(row) for row in item_rows]
        items_by_turn: dict[str, list[ChatThreadItemResponse]] = defaultdict(list)
        for item in items:
            items_by_turn[item.turn_id].append(item)
        turns = [_chat_turn_from_orm(row, items=items_by_turn.get(row.turn_id, [])) for row in turn_rows]
        return ChatThreadResponse(
            thread_id=thread.thread_id,
            project_id=thread.project_id or "",
            project_name=thread.project_name or "",
            project_instructions=thread.project_instructions or "",
            title=thread.title,
            created_at=thread.created_at,
            updated_at=thread.updated_at,
            items=items,
            turns=turns,
        )

    def _next_chat_item_sequence(self, session: Session, thread_id: str) -> int:
        current = session.scalar(select(func.max(ChatThreadItemORM.sequence)).where(ChatThreadItemORM.thread_id == thread_id))
        return int(current or 0) + 1

    def create_chat_turn(
        self,
        *,
        thread_id: str,
        status: str = "running",
        provider_response_id: str = "",
    ) -> ChatTurnResponse:
        row = ChatTurnORM(
            turn_id=new_id("turn"),
            thread_id=thread_id,
            status=status,
            provider_response_id=provider_response_id,
            started_at=utcnow(),
        )
        with self._session() as session:
            session.add(row)
            thread = session.get(ChatThreadORM, thread_id)
            if thread is not None:
                thread.updated_at = utcnow()
        return _chat_turn_from_orm(row)

    def update_chat_turn(
        self,
        turn_id: str,
        *,
        status: str | None = None,
        provider_response_id: str | None = None,
        completed_at: Any | None = None,
        usage: dict[str, Any] | None = None,
        diff_summary: str | None = None,
        changed_paths: list[str] | None = None,
        exec_session_ids: list[str] | None = None,
    ) -> ChatTurnResponse | None:
        with self._session() as session:
            row = session.get(ChatTurnORM, turn_id)
            if row is None:
                return None
            if status is not None:
                row.status = status
            if provider_response_id is not None:
                row.provider_response_id = provider_response_id
            if completed_at is not None:
                row.completed_at = completed_at
            if usage is not None:
                row.usage_json = usage
            if diff_summary is not None:
                row.diff_summary = diff_summary
            if changed_paths is not None:
                row.changed_paths = list(changed_paths)
            if exec_session_ids is not None:
                row.exec_session_ids = list(exec_session_ids)
            item_rows = session.scalars(
                select(ChatThreadItemORM)
                .where(ChatThreadItemORM.turn_id == turn_id)
                .order_by(ChatThreadItemORM.sequence.asc(), ChatThreadItemORM.created_at.asc())
            ).all()
            items = [_chat_thread_item_from_orm(item) for item in item_rows]
            thread = session.get(ChatThreadORM, row.thread_id)
            if thread is not None:
                thread.updated_at = utcnow()
        return _chat_turn_from_orm(row, items=items)

    def add_chat_thread_item(
        self,
        *,
        thread_id: str,
        turn_id: str,
        item_type: str,
        role: str,
        phase: str | None = None,
        call_id: str | None = None,
        name: str | None = None,
        content_text: str = "",
        content_json: Any | None = None,
        citations: list[dict[str, Any]] | None = None,
        status: str = "completed",
        item_id: str | None = None,
    ) -> ChatThreadItemResponse:
        with self._session() as session:
            row = ChatThreadItemORM(
                item_id=item_id or new_id("item"),
                thread_id=thread_id,
                turn_id=turn_id,
                sequence=self._next_chat_item_sequence(session, thread_id),
                item_type=item_type,
                role=role,
                phase=phase,
                call_id=call_id,
                name=name,
                status=status,
                content_text=content_text,
                content_json=content_json,
                citations_json=citations or [],
            )
            session.add(row)
            thread = session.get(ChatThreadORM, thread_id)
            if thread is not None:
                thread.updated_at = utcnow()
                project = session.get(ProjectORM, thread.project_id) if thread.project_id else None
                if project is not None:
                    project.updated_at = thread.updated_at
        return _chat_thread_item_from_orm(row)

    def update_chat_thread_item(
        self,
        item_id: str,
        *,
        phase: str | None = None,
        content_text: str | None = None,
        content_json: Any | None = None,
        citations: list[dict[str, Any]] | None = None,
        status: str | None = None,
        name: str | None = None,
        call_id: str | None = None,
        item_type: str | None = None,
    ) -> ChatThreadItemResponse | None:
        with self._session() as session:
            row = session.get(ChatThreadItemORM, item_id)
            if row is None:
                return None
            if phase is not None:
                row.phase = phase
            if content_text is not None:
                row.content_text = content_text
            if content_json is not None:
                row.content_json = content_json
            if citations is not None:
                row.citations_json = citations
            if status is not None:
                row.status = status
            if name is not None:
                row.name = name
            if call_id is not None:
                row.call_id = call_id
            if item_type is not None:
                row.item_type = item_type
            thread = session.get(ChatThreadORM, row.thread_id)
            if thread is not None:
                thread.updated_at = utcnow()
                project = session.get(ProjectORM, thread.project_id) if thread.project_id else None
                if project is not None:
                    project.updated_at = thread.updated_at
        return _chat_thread_item_from_orm(row)

    def list_chat_turn_items(self, *, thread_id: str, turn_id: str) -> list[ChatThreadItemResponse]:
        with self._session() as session:
            rows = session.scalars(
                select(ChatThreadItemORM)
                .where(ChatThreadItemORM.thread_id == thread_id, ChatThreadItemORM.turn_id == turn_id)
                .order_by(ChatThreadItemORM.sequence.asc(), ChatThreadItemORM.created_at.asc())
            ).all()
        return [_chat_thread_item_from_orm(row) for row in rows]

    def add_chat_message(
        self,
        *,
        thread_id: str,
        role: str,
        content_markdown: str,
        citations: list[dict[str, Any]] | None = None,
        status: str = "completed",
        kind: str = "default",
    ) -> ChatMessageResponse:
        row = ChatMessageORM(
            message_id=new_id("msg"),
            thread_id=thread_id,
            role=role,
            content_markdown=content_markdown,
            citations=citations or [],
            status=status,
            kind=kind,
        )
        with self._session() as session:
            session.add(row)
            thread = session.get(ChatThreadORM, thread_id)
            if thread:
                thread.updated_at = utcnow()
                project = session.get(ProjectORM, thread.project_id) if thread.project_id else None
                if project is not None:
                    project.updated_at = thread.updated_at
        return _chat_message_from_orm(row)

    def update_chat_message(
        self,
        message_id: str,
        *,
        content_markdown: str | None = None,
        citations: list[dict[str, Any]] | None = None,
        status: str | None = None,
        kind: str | None = None,
    ) -> ChatMessageResponse | None:
        with self._session() as session:
            row = session.get(ChatMessageORM, message_id)
            if row is None:
                return None
            if content_markdown is not None:
                row.content_markdown = content_markdown
            if citations is not None:
                row.citations = citations
            if status is not None:
                row.status = status
            if kind is not None:
                row.kind = kind
            thread = session.get(ChatThreadORM, row.thread_id)
            if thread:
                thread.updated_at = utcnow()
                project = session.get(ProjectORM, thread.project_id) if thread.project_id else None
                if project is not None:
                    project.updated_at = thread.updated_at
        return _chat_message_from_orm(row)

    def archive_chat_messages(
        self,
        *,
        thread_id: str,
        message_ids: list[str],
        archived_by_message_id: str,
    ) -> int:
        if not message_ids:
            return 0
        archived_at = utcnow()
        with self._session() as session:
            rows = session.scalars(
                select(ChatMessageORM).where(
                    ChatMessageORM.thread_id == thread_id,
                    ChatMessageORM.message_id.in_(message_ids),
                    ChatMessageORM.archived_at.is_(None),
                )
            ).all()
            for row in rows:
                row.archived_at = archived_at
                row.archived_by_message_id = archived_by_message_id
            thread = session.get(ChatThreadORM, thread_id)
            if thread:
                thread.updated_at = archived_at
                project = session.get(ProjectORM, thread.project_id) if thread.project_id else None
                if project is not None:
                    project.updated_at = archived_at
        return len(rows)

    def delete_chat_thread(self, *, user_id: str, thread_id: str) -> bool:
        with self._session() as session:
            thread = session.scalar(
                select(ChatThreadORM).where(ChatThreadORM.thread_id == thread_id, ChatThreadORM.user_id == user_id)
            )
            if thread is None:
                return False
            project_id = thread.project_id
            session.execute(delete(ChatThreadORM).where(ChatThreadORM.thread_id == thread_id))
            if project_id:
                project = session.get(ProjectORM, project_id)
                if project is not None:
                    project.updated_at = utcnow()
        return True

    def save_result_set(
        self,
        *,
        query: str | None,
        papers: list[SearchPaperResult],
        parent_result_set_id: str | None = None,
        pattern: str | None = None,
        ttl_days: int = 7,
    ) -> ResultSetResponse:
        result_set_id = new_id("rs")
        now = utcnow()
        expires_at = now + timedelta(days=ttl_days)
        with self._session() as session:
            session.add(
                ResultSetORM(
                    result_set_id=result_set_id,
                    query=query,
                    parent_result_set_id=parent_result_set_id,
                    pattern=pattern,
                    created_at=now,
                    expires_at=expires_at,
                )
            )
            session.flush()
            for rank, paper in enumerate(papers):
                session.add(
                    ResultItemORM(
                        result_item_id=new_id("ri"),
                        result_set_id=result_set_id,
                        paper_id=paper.paper_id,
                        rank=rank,
                        score=paper.score,
                        source=paper.source,
                        tldr=paper.tldr,
                    )
                )
        return ResultSetResponse(
            result_set_id=result_set_id,
            query=query,
            created_at=now,
            expires_at=expires_at,
            papers=papers,
        )

    def get_result_set(self, result_set_id: str) -> ResultSetResponse | None:
        with self._session() as session:
            result_set = session.get(ResultSetORM, result_set_id)
            if result_set is None:
                return None
            rows = session.execute(
                select(ResultItemORM, PaperORM)
                .join(PaperORM, ResultItemORM.paper_id == PaperORM.paper_id)
                .where(ResultItemORM.result_set_id == result_set_id)
                .order_by(ResultItemORM.rank)
            ).all()
        papers: list[SearchPaperResult] = []
        for result_item, paper in rows:
            payload = _paper_from_orm(paper).model_dump(exclude={"tldr"})
            papers.append(
                SearchPaperResult(
                    **payload,
                    score=result_item.score,
                    source=result_item.source,
                    tldr=result_item.tldr,
                )
            )
        return ResultSetResponse(
            result_set_id=result_set.result_set_id,
            query=result_set.query,
            created_at=result_set.created_at,
            expires_at=result_set.expires_at,
            papers=papers,
        )

    def grep_result_set(self, result_set_id: str, pattern: str, *, limit: int) -> list[GrepPaperMatch]:
        literal = literal_hint_from_pattern(pattern)
        params = {"result_set_id": result_set_id, "pattern": pattern, "limit": limit * 10}
        similarity_clause = ""
        if literal:
            params["literal"] = literal
            similarity_clause = " OR similarity(tc.content, :literal) > 0.05"
        sql = text(
            f"""
            SELECT p.paper_id, p.arxiv_id, p.title, tc.path, tc.content
            FROM text_chunks tc
            JOIN result_items ri ON ri.paper_id = tc.paper_id
            JOIN papers p ON p.paper_id = tc.paper_id
            WHERE ri.result_set_id = :result_set_id
              AND (tc.content ~* :pattern{similarity_clause})
            LIMIT :limit
            """
        )
        regex = re.compile(pattern, re.IGNORECASE)
        with self._session() as session:
            rows = session.execute(sql, params).mappings().all()
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            matches = regex.findall(row["content"])
            if not matches and not regex.search(row["content"]):
                continue
            state = grouped.setdefault(
                row["paper_id"],
                {
                    "arxiv_id": row["arxiv_id"],
                    "title": row["title"],
                    "match_count": 0,
                    "previews": [],
                },
            )
            match_count = max(1, len(matches))
            state["match_count"] += match_count
            if len(state["previews"]) < 5:
                state["previews"].append(f"{row['path']}: {row['content'][:180]}")
        ordered = sorted(grouped.items(), key=lambda item: item[1]["match_count"], reverse=True)[:limit]
        return [
            GrepPaperMatch(
                paper_id=paper_id,
                arxiv_id=data["arxiv_id"],
                title=data["title"],
                match_count=data["match_count"],
                previews=data["previews"],
            )
            for paper_id, data in ordered
        ]

    def create_job(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        max_attempts: int | None = None,
        priority: int | None = None,
        dedupe_key: str | None = None,
        root_job_id: str | None = None,
    ) -> JobResponse:
        if dedupe_key:
            existing = self._get_job_by_dedupe_key(dedupe_key)
            if existing is not None:
                return existing
        job_id = new_id("job")
        job = JobORM(
            job_id=job_id,
            root_job_id=root_job_id or job_id,
            job_type=job_type,
            dedupe_key=dedupe_key,
            status="pending",
            payload=payload,
            max_attempts=max_attempts if max_attempts is not None else default_max_attempts(job_type),
            priority=priority if priority is not None else default_priority(job_type),
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        try:
            with self._session() as session:
                session.add(job)
        except IntegrityError:
            if not dedupe_key:
                raise
            existing = self._get_job_by_dedupe_key(dedupe_key)
            if existing is None:
                raise
            return existing
        return self.get_job(job.job_id)  # type: ignore[return-value]

    def _get_job_by_dedupe_key(self, dedupe_key: str) -> JobResponse | None:
        with self._session() as session:
            job_id = session.scalar(select(JobORM.job_id).where(JobORM.dedupe_key == dedupe_key))
        return self.get_job(job_id) if job_id is not None else None

    def get_job(self, job_id: str) -> JobResponse | None:
        with self._session() as session:
            row = session.get(JobORM, job_id)
        if row is None:
            return None
        return JobResponse(
            job_id=row.job_id,
            root_job_id=row.root_job_id,
            job_type=row.job_type,
            status=row.status,
            payload=row.payload,
            error=row.error,
            attempts=row.attempts or 0,
            max_attempts=row.max_attempts or 3,
            next_run_at=row.next_run_at,
            last_attempted_at=row.last_attempted_at,
            priority=row.priority or 0,
            created_at=row.created_at,
            updated_at=row.updated_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    def claim_pending_job(self) -> JobResponse | None:
        # Atomic lease via ``SELECT ... FOR UPDATE SKIP LOCKED``. Without the
        # row lock, two concurrent workers reading at the same instant could
        # both match the same row, both set status='running', and both believe
        # they had the claim. SKIP LOCKED makes each worker jump over a row
        # another worker has already locked inside this transaction.
        #
        # Claim ordering: priority first (lower wins), then next_run_at NULLS
        # FIRST so fresh jobs win over retries coming back around, then
        # created_at ASC for FIFO within a tier. Priority keeps long-running
        # backfills from starving interactive on-demand work.
        now = utcnow()
        with self._session() as session:
            row = session.scalar(
                select(JobORM)
                .where(
                    JobORM.status == "pending",
                    or_(JobORM.next_run_at.is_(None), JobORM.next_run_at <= now),
                )
                .order_by(
                    JobORM.priority.asc(),
                    JobORM.next_run_at.is_(None).desc(),
                    JobORM.created_at.asc(),
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            row.status = "running"
            row.started_at = utcnow()
            row.updated_at = utcnow()
            row.last_attempted_at = utcnow()
            row.attempts = (row.attempts or 0) + 1
            job_id = row.job_id
        return self.get_job(job_id)

    def mark_job_running(self, job_id: str) -> None:
        with self._session() as session:
            row = session.get(JobORM, job_id)
            if row:
                row.status = "running"
                row.started_at = row.started_at or utcnow()
                row.updated_at = utcnow()

    def mark_job_completed(self, job_id: str) -> None:
        with self._session() as session:
            row = session.get(JobORM, job_id)
            if row:
                row.status = "completed"
                row.finished_at = utcnow()
                row.updated_at = utcnow()
                # Clear retry metadata from prior failed attempts so the
                # completed JobResponse doesn't surface a stale transient
                # error message or a dangling next_run_at. The retry loop
                # sets these on failure; success must wipe them.
                row.error = None
                row.next_run_at = None

    def mark_job_failed(
        self,
        job_id: str,
        error: str,
        *,
        permanent: bool = False,
        next_run_at: Any | None = None,
    ) -> None:
        """Record a failed attempt.

        When ``permanent`` is true, jumps straight to ``dead``. Otherwise,
        if ``attempts`` has reached ``max_attempts`` the job moves to
        ``dead``; if not, it's requeued as ``pending`` with ``next_run_at``
        set (the caller computes the backoff).
        """
        with self._session() as session:
            row = session.get(JobORM, job_id)
            if row is None:
                return
            row.error = error
            row.updated_at = utcnow()
            attempts = row.attempts or 0
            max_attempts = row.max_attempts or 3
            if permanent or attempts >= max_attempts:
                row.status = "dead"
                row.finished_at = utcnow()
                row.next_run_at = None
            else:
                row.status = "pending"
                row.next_run_at = next_run_at

    def requeue_dead_job(self, job_id: str) -> JobResponse | None:
        """Promote a ``dead`` job back to ``pending`` and reset its attempt
        counter. Used by the admin retry endpoint / CLI."""
        with self._session() as session:
            row = session.get(JobORM, job_id)
            if row is None or row.status not in {"dead", "failed"}:
                return None
            row.status = "pending"
            row.attempts = 0
            row.error = None
            row.next_run_at = None
            row.finished_at = None
            row.updated_at = utcnow()
            job_id = row.job_id
        return self.get_job(job_id)

    def list_jobs_by_status(self, status: str, *, limit: int = 50) -> list[JobResponse]:
        with self._session() as session:
            rows = session.scalars(
                select(JobORM)
                .where(JobORM.status == status)
                .order_by(JobORM.updated_at.desc())
                .limit(limit)
            ).all()
        return [self.get_job(row.job_id) for row in rows if row is not None]  # type: ignore[list-item]

    def list_recent_jobs(self, *, limit: int = 200) -> list[JobResponse]:
        with self._session() as session:
            rows = session.scalars(select(JobORM).order_by(JobORM.updated_at.desc()).limit(limit)).all()
        return [self.get_job(row.job_id) for row in rows if row is not None]  # type: ignore[list-item]

    def list_jobs_for_root(self, root_job_id: str) -> list[JobResponse]:
        with self._session() as session:
            rows = session.scalars(
                select(JobORM)
                .where(JobORM.root_job_id == root_job_id)
                .order_by(JobORM.updated_at.desc())
            ).all()
        return [self.get_job(row.job_id) for row in rows if row is not None]  # type: ignore[list-item]

    def record_job_paper(
        self,
        job_id: str,
        paper_id: str,
        *,
        outcome: str,
        arxiv_id: str | None = None,
        title: str | None = None,
    ) -> None:
        statement = insert(JobPaperORM).values(
            job_id=job_id,
            paper_id=paper_id,
            arxiv_id=arxiv_id,
            title=title,
            outcome=outcome,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        statement = statement.on_conflict_do_update(
            index_elements=[JobPaperORM.job_id, JobPaperORM.paper_id],
            set_={
                "arxiv_id": arxiv_id,
                "title": title,
                "outcome": outcome,
                "updated_at": utcnow(),
            },
        )
        with self._session() as session:
            session.execute(statement)

    def list_job_papers_for_root(self, root_job_id: str) -> list[JobPaperRecord]:
        with self._session() as session:
            rows = session.scalars(
                select(JobPaperORM)
                .join(JobORM, JobORM.job_id == JobPaperORM.job_id)
                .where(JobORM.root_job_id == root_job_id)
                .order_by(JobPaperORM.updated_at.desc())
            ).all()
        return [
            JobPaperRecord(
                job_id=row.job_id,
                paper_id=row.paper_id,
                arxiv_id=row.arxiv_id,
                title=row.title,
                outcome=row.outcome,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def cleanup_expired_results(self) -> None:
        with self._session() as session:
            expired_ids = session.scalars(
                select(ResultSetORM.result_set_id).where(ResultSetORM.expires_at < utcnow())
            ).all()
            if expired_ids:
                session.execute(delete(ResultItemORM).where(ResultItemORM.result_set_id.in_(expired_ids)))
                session.execute(delete(ResultSetORM).where(ResultSetORM.result_set_id.in_(expired_ids)))

    def record_search_log(
        self,
        *,
        query_id: str,
        user_id: str | None,
        query_text: str,
        request_rerank: bool,
        top_paper_ids: list[str],
        result_set_id: str | None,
        latency_ms: int | None,
    ) -> None:
        with self._session() as session:
            session.add(
                SearchLogORM(
                    query_id=query_id,
                    user_id=user_id,
                    query_text=query_text,
                    request_rerank=bool(request_rerank),
                    top_paper_ids=list(top_paper_ids),
                    result_set_id=result_set_id,
                    latency_ms=latency_ms,
                    created_at=utcnow(),
                )
            )

    def record_search_click(self, query_id: str, paper_id: str) -> bool:
        with self._session() as session:
            row = session.get(SearchLogORM, query_id)
            if row is None:
                return False
            if paper_id not in (row.top_paper_ids or []):
                return False
            row.clicked_paper_id = paper_id
            row.clicked_at = utcnow()
            return True


class MemoryPaperRepository(PaperRepository):
    def __init__(self) -> None:
        self.papers: dict[str, PaperResponse] = {}
        self.search_documents: dict[str, str] = {}
        self.text_chunks: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.result_sets: dict[str, ResultSetResponse] = {}
        self.jobs: dict[str, JobResponse] = {}
        self.paper_refs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        # Paper-level embeddings, tracked out-of-band because PaperResponse
        # doesn't carry them on the wire.
        self.paper_embeddings: dict[str, list[float] | None] = {}
        self.browser_users: dict[str, BrowserUser] = {}
        self.browser_user_passwords: dict[str, str | None] = {}
        self.browser_users_by_email: dict[str, str] = {}
        self.google_identities: dict[str, str] = {}
        self.browser_sessions: dict[str, dict[str, Any]] = {}
        self.password_reset_tokens: dict[str, dict[str, Any]] = {}
        self.user_api_keys: dict[str, dict[str, str]] = {}
        self.browser_user_settings: dict[str, BrowserUserSettingsResponse] = {}
        self.modal_credentials_records: dict[str, dict[str, str]] = {}
        self.github_connection_records: dict[str, dict[str, str]] = {}
        self.uploaded_documents: dict[str, UploadedDocumentResponse] = {}
        self.uploaded_document_chunks: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.projects: dict[str, ProjectResponse] = {}
        self.project_users: dict[str, str] = {}
        self.default_projects_by_user: dict[str, str] = {}
        self.chat_threads: dict[str, ChatThreadResponse] = {}
        self.chat_thread_users: dict[str, str] = {}
        self.python_files: dict[str, PythonFileResponse] = {}
        self.python_file_users: dict[str, str] = {}
        self.project_repo_syncs: dict[str, ProjectRepoSyncResponse] = {}
        self.search_logs: dict[str, dict[str, Any]] = {}
        self.job_dedupe_keys: dict[str, str] = {}
        self.job_papers: dict[tuple[str, str], JobPaperRecord] = {}

    def init(self) -> None:
        return None

    def probe(self) -> IntegrationProbeComponent:
        return IntegrationProbeComponent(
            ok=True,
            backend="memory",
            detail="In-memory repository is reachable",
            target="memory://",
        )

    def _ensure_default_project(self, user_id: str) -> ProjectResponse:
        default_project_id = self.default_projects_by_user.get(user_id)
        if default_project_id:
            project = self.projects.get(default_project_id)
            if project is not None:
                return project
        now = utcnow()
        project = ProjectResponse(
            project_id=new_id("proj"),
            name="General",
            description="",
            is_default=True,
            session_count=0,
            created_at=now,
            updated_at=now,
        )
        self.projects[project.project_id] = project
        self.project_users[project.project_id] = user_id
        self.default_projects_by_user[user_id] = project.project_id
        return project

    def _get_or_create_browser_user_settings(self, user_id: str) -> BrowserUserSettingsResponse:
        settings = self.browser_user_settings.get(user_id)
        if settings is not None:
            return settings
        settings = BrowserUserSettingsResponse()
        self.browser_user_settings[user_id] = settings
        return settings

    def upsert_paper(
        self,
        paper: PaperMetadata,
        *,
        search_document: str = "",
        embedding: list[float] | None = None,
    ) -> PaperResponse:
        paper_id = canonical_paper_id(paper.arxiv_id)
        canonical = paper.model_copy(update={"arxiv_id": canonical_arxiv_id(paper.arxiv_id)})
        response = PaperResponse(paper_id=paper_id, **canonical.model_dump())
        self.papers[paper_id] = response
        self.search_documents[paper_id] = search_document
        if embedding is not None:
            self.paper_embeddings[paper_id] = embedding
        return response

    def upsert_paper_shallow(self, paper: PaperMetadata) -> PaperResponse:
        paper_id = canonical_paper_id(paper.arxiv_id)
        existing = self.papers.get(paper_id)
        if existing is None:
            discovered = paper.model_copy(
                update={
                    "index_status": "discovered",
                    "arxiv_id": canonical_arxiv_id(paper.arxiv_id),
                }
            )
            response = PaperResponse(paper_id=paper_id, **discovered.model_dump())
            self.papers[paper_id] = response
            return response
        # Never overwrite indexed artifacts; only refresh metadata that live
        # sources are authoritative for.
        updates: dict[str, Any] = {}
        if (paper.citation_count or 0) > (existing.citation_count or 0):
            updates["citation_count"] = paper.citation_count
        if (paper.influential_citation_count or 0) > (existing.influential_citation_count or 0):
            updates["influential_citation_count"] = paper.influential_citation_count
        if not existing.semantic_scholar_id and paper.semantic_scholar_id:
            updates["semantic_scholar_id"] = paper.semantic_scholar_id
        if not existing.tldr and paper.tldr:
            updates["tldr"] = paper.tldr
        if not existing.doi and paper.doi:
            updates["doi"] = paper.doi
        if not existing.code_url and paper.code_url:
            updates["code_url"] = paper.code_url
        if updates:
            refreshed = existing.model_copy(update=updates)
            self.papers[paper_id] = refreshed
            return refreshed
        return existing

    def get_paper(self, paper_id: str) -> PaperResponse | None:
        return self.papers.get(paper_id)

    def get_paper_by_arxiv_id(self, arxiv_id: str) -> PaperResponse | None:
        return self.papers.get(canonical_paper_id(arxiv_id))

    def list_papers(
        self,
        *,
        query: str | None = None,
        index_status: str | None = None,
        limit: int = 50,
    ) -> list[PaperResponse]:
        query_lower = (query or "").strip().lower()
        candidates = list(self.papers.values())
        if index_status and index_status != "all":
            candidates = [paper for paper in candidates if paper.index_status == index_status]
        if query_lower:
            candidates = [
                paper
                for paper in candidates
                if query_lower in paper.title.lower()
                or query_lower in paper.arxiv_id.lower()
                or query_lower in paper.authors.lower()
            ]
        floor = datetime.min.replace(tzinfo=timezone.utc)
        candidates.sort(key=lambda paper: paper.paper_id)
        candidates.sort(key=lambda paper: paper.updated or "", reverse=True)
        candidates.sort(key=lambda paper: paper.indexed_at or floor, reverse=True)
        candidates.sort(key=lambda paper: paper.indexed_at is None)
        return candidates[:limit]

    def replace_text_chunks(self, paper_id: str, chunks: list[dict[str, Any]]) -> None:
        # Match the SQL path: assign a stable chunk_id so downstream updates
        # like update_chunk_embedding can find the row again.
        stamped: list[dict[str, Any]] = []
        for chunk in chunks:
            stamped.append({**chunk, "chunk_id": chunk.get("chunk_id") or new_id("chunk")})
        self.text_chunks[paper_id] = stamped

    def update_summaries(self, paper_id: str, *, summary_short: str, summary_eli5: str) -> PaperResponse | None:
        existing = self.papers.get(paper_id)
        if existing is None:
            return None
        updated = existing.model_copy(update={"summary_short": summary_short, "summary_eli5": summary_eli5})
        self.papers[paper_id] = updated
        return updated

    def update_code_ingest_state(
        self,
        paper_id: str,
        *,
        status: str,
        is_python: bool | None = None,
        python_reason: str | None = None,
        manifest: dict[str, Any] | None = None,
    ) -> PaperResponse | None:
        existing = self.papers.get(paper_id)
        if existing is None:
            return None
        updates: dict[str, Any] = {"code_index_status": status}
        if is_python is not None or python_reason is not None or manifest is not None:
            updates["code_is_python"] = is_python
            updates["code_python_reason"] = python_reason
            updates["code_manifest"] = manifest
        updated = existing.model_copy(update=updates)
        self.papers[paper_id] = updated
        return updated

    def set_paper_venue(
        self,
        paper_id: str,
        *,
        venue: str | None,
        venue_year: int | None,
        venue_source: str | None,
    ) -> PaperResponse | None:
        existing = self.papers.get(paper_id)
        if existing is None:
            return None
        updated = existing.model_copy(update={
            "venue": venue,
            "venue_year": venue_year,
            "venue_source": venue_source,
        })
        self.papers[paper_id] = updated
        return updated

    def replace_code_chunks(self, paper_id: str, chunks: list[dict[str, Any]]) -> None:
        existing = self.text_chunks.get(paper_id, [])
        # Keep paper-section chunks untouched so a repo re-ingest doesn't
        # wipe the paper's existing sections / figures / tables.
        preserved = [c for c in existing if c["chunk_kind"] not in CODE_CHUNK_KINDS]
        stamped: list[dict[str, Any]] = []
        for chunk in chunks:
            stamped.append({**chunk, "chunk_id": chunk.get("chunk_id") or new_id("chunk")})
        self.text_chunks[paper_id] = preserved + stamped

    def search_paper_code_sections(
        self,
        paper_id: str,
        query: str,
        *,
        limit: int,
        query_embedding: list[float] | None = None,
    ) -> list[SearchSectionResult]:
        query_lower = (query or "").lower().strip()
        if not query_lower or paper_id not in self.text_chunks:
            return []
        paper = self.papers.get(paper_id)
        if paper is None:
            return []
        results: list[SearchSectionResult] = []
        for chunk in self.text_chunks[paper_id]:
            if chunk["chunk_kind"] not in CODE_CHUNK_KINDS:
                continue
            content = chunk["content"]
            if query_lower not in content.lower():
                continue
            score = len(query_lower) / max(len(content), 1)
            results.append(
                SearchSectionResult(
                    paper_id=paper.paper_id,
                    arxiv_id=paper.arxiv_id,
                    title=paper.title,
                    path=chunk["path"],
                    chunk_kind=chunk["chunk_kind"],
                    preview=content[:320],
                    score=score,
                )
            )
        results.sort(key=lambda item: item.score or 0, reverse=True)
        return results[:limit]

    def list_papers_needing_summary(
        self,
        *,
        limit: int,
        after: PaperResponse | None = None,
    ) -> list[PaperResponse]:
        candidates = [
            paper for paper in self.papers.values() if not paper.summary_eli5 and paper.index_status == "indexed"
        ]
        candidates.sort(
            key=lambda p: (
                -p.influential_citation_count,
                -p.citation_count,
                -int((p.published or "").replace("-", "") or 0),
                p.paper_id,
            ),
        )
        if after is not None:
            candidates = [paper for paper in candidates if _paper_needs_summary_after(paper, after)]
        return candidates[:limit]

    def list_papers_needing_embedding_backfill(
        self,
        *,
        limit: int,
        include_chunks: bool = True,
        rewrite_existing: bool = False,
        after: PaperResponse | None = None,
    ) -> list[PaperResponse]:
        def needs_backfill(paper: PaperResponse) -> bool:
            if paper.index_status != "indexed":
                return False
            if rewrite_existing:
                return True
            if self.paper_embeddings.get(paper.paper_id) is None:
                return True
            if include_chunks:
                return any(not chunk.get("embedding") for chunk in self.text_chunks.get(paper.paper_id, []))
            return False

        candidates = [paper for paper in self.papers.values() if needs_backfill(paper)]
        candidates.sort(
            key=lambda p: (
                -(p.influential_citation_count or 0),
                -(p.citation_count or 0),
                -int((p.published or "").replace("-", "") or 0),
                p.paper_id,
            )
        )
        if after is not None:
            candidates = [paper for paper in candidates if _paper_needs_summary_after(paper, after)]
        return candidates[:limit]

    def list_papers_needing_enrichment(
        self,
        *,
        limit: int,
        min_age_seconds: int = 3600,
        after: PaperResponse | None = None,
    ) -> list[PaperResponse]:
        now = utcnow()
        candidates: list[PaperResponse] = []
        for paper in self.papers.values():
            if paper.index_status != "indexed":
                continue
            if paper.semantic_scholar_id:
                continue
            if paper.citation_count and paper.citation_count > 0:
                continue
            if paper.indexed_at is not None:
                age = (now - paper.indexed_at).total_seconds()
                if age < min_age_seconds:
                    continue
            candidates.append(paper)
        # Match the SqlAlchemy ordering: NULL indexed_at sorts first, then by
        # (indexed_at, paper_id). Using utcnow() here would invert NULLS FIRST,
        # so sort NULL as a distinct bucket that precedes any datetime.
        def _sort_key(p: PaperResponse) -> tuple[int, Any, str]:
            return (0, None, p.paper_id) if p.indexed_at is None else (1, p.indexed_at, p.paper_id)

        candidates.sort(key=_sort_key)
        if after is not None:
            # Mirror the ORDER BY tuple: advance by (indexed_at, paper_id)
            # with NULL-indexed rows forming a distinct leading bucket.
            def _after(paper: PaperResponse) -> bool:
                if after.indexed_at is None:
                    if paper.indexed_at is None:
                        return paper.paper_id > after.paper_id
                    return True
                if paper.indexed_at is None:
                    return False
                if paper.indexed_at > after.indexed_at:
                    return True
                if paper.indexed_at < after.indexed_at:
                    return False
                return paper.paper_id > after.paper_id

            candidates = [paper for paper in candidates if _after(paper)]
        return candidates[:limit]

    def enrichment_stats(self) -> dict[str, int]:
        total_indexed = 0
        with_s2 = 0
        with_cites = 0
        enriched = 0
        for paper in self.papers.values():
            if paper.index_status != "indexed":
                continue
            total_indexed += 1
            has_s2 = bool(paper.semantic_scholar_id)
            has_cites = bool(paper.citation_count and paper.citation_count > 0)
            if has_s2:
                with_s2 += 1
            if has_cites:
                with_cites += 1
            # enriched = has populated S2 id OR non-zero citations. See the
            # SqlAlchemy docstring for why citation_count > 0 alone is wrong.
            if has_s2 or has_cites:
                enriched += 1
        return {
            "total_indexed": total_indexed,
            "with_semantic_scholar_id": with_s2,
            "with_citations": with_cites,
            "enriched": enriched,
        }

    def update_paper_embedding(self, paper_id: str, embedding: list[float]) -> None:
        if paper_id in self.papers:
            self.paper_embeddings[paper_id] = embedding

    def list_chunks_missing_embedding(self, paper_id: str, *, include_existing: bool = False) -> list[dict[str, Any]]:
        chunks = list(self.text_chunks.get(paper_id, []))
        if include_existing:
            return chunks
        return [chunk for chunk in chunks if not chunk.get("embedding")]

    def update_chunk_embedding(self, chunk_id: str, embedding: list[float]) -> None:
        for chunks in self.text_chunks.values():
            for chunk in chunks:
                if chunk.get("chunk_id") == chunk_id:
                    chunk["embedding"] = embedding
                    return

    def replace_paper_refs(self, paper_id: str, refs: list[dict[str, Any]]) -> None:
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for ref in refs:
            dst_arxiv = canonical_arxiv_id(ref.get("arxiv_id") or "")
            if not dst_arxiv or dst_arxiv in seen:
                continue
            seen.add(dst_arxiv)
            normalized.append(
                {
                    "src_paper_id": paper_id,
                    "dst_arxiv_id": dst_arxiv,
                    "dst_paper_id": f"arxiv:{dst_arxiv}",
                    "ref_type": ref.get("ref_type", "citation"),
                    "rank": int(ref.get("rank", 0)),
                }
            )
        self.paper_refs[paper_id] = normalized

    def get_paper_refs(self, paper_id: str) -> list[dict[str, Any]]:
        return list(self.paper_refs.get(paper_id, []))

    def best_chunk_for_papers(
        self,
        query: str,
        paper_ids: list[str],
        *,
        query_embedding: list[float] | None = None,
    ) -> dict[str, str]:
        query_lower = (query or "").lower()
        out: dict[str, str] = {}
        for paper_id in paper_ids:
            best = ""
            best_score = -1
            for chunk in self.text_chunks.get(paper_id, []):
                if chunk.get("chunk_kind") == "full_text":
                    continue
                content = chunk.get("content", "")
                score = content.lower().count(query_lower)
                if score > best_score:
                    best = content
                    best_score = score
            if best:
                out[paper_id] = best[:2000]
        return out

    def suggest_papers(self, q: str, *, limit: int = 10) -> list[dict[str, Any]]:
        q = (q or "").strip()
        if not q:
            return []
        q_lower = q.lower()
        hits: list[dict[str, Any]] = []
        for paper in self.papers.values():
            title = paper.title or ""
            authors = paper.authors or ""
            summary_text = paper.summary_short or paper.tldr or ""
            title_prefix = title.lower().startswith(q_lower)
            title_match = q_lower in title.lower()
            authors_match = q_lower in authors.lower()
            summary_match = q_lower in summary_text.lower()
            if not (title_prefix or title_match or authors_match or summary_match):
                continue
            if title_prefix:
                matched_field, score, highlight_source = "title_prefix", 1.0, title
            elif title_match:
                matched_field, score, highlight_source = "title", 0.8, title
            elif authors_match:
                matched_field, score, highlight_source = "authors", 0.6, authors
            else:
                matched_field, score, highlight_source = "summary", 0.5, summary_text
            hits.append(
                {
                    "paper_id": paper.paper_id,
                    "arxiv_id": paper.arxiv_id,
                    "title": paper.title,
                    "authors": paper.authors,
                    "summary_short": summary_text,
                    "citation_count": paper.citation_count or 0,
                    "published": paper.published or "",
                    "score": score,
                    "matched_field": matched_field,
                    "highlight_snippet": _extract_highlight(highlight_source, q),
                }
            )
        hits.sort(
            key=lambda item: (item["score"], item["citation_count"], item["published"]),
            reverse=True,
        )
        return hits[:limit]

    def suggest_fallback_papers(self, *, limit: int = 6) -> list[dict[str, Any]]:
        ranked = sorted(
            (paper for paper in self.papers.values() if paper.index_status == "indexed"),
            key=lambda paper: (
                paper.influential_citation_count or 0,
                paper.citation_count or 0,
                paper.published or "",
                paper.paper_id,
            ),
            reverse=True,
        )
        rows = [
            {
                "paper_id": paper.paper_id,
                "arxiv_id": paper.arxiv_id,
                "title": paper.title,
                "authors": paper.authors,
                "summary_short": paper.summary_short,
                "tldr": paper.tldr,
                "citation_count": paper.citation_count or 0,
                "published": paper.published or "",
                "score": 0.0,
            }
            for paper in ranked[:limit]
        ]
        return [_fallback_row_to_payload(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        total = len(self.papers)
        indexed = sum(1 for p in self.papers.values() if p.index_status == "indexed")
        with_summary = sum(1 for p in self.papers.values() if p.summary_eli5)
        with_embedding = sum(1 for v in self.paper_embeddings.values() if v is not None)
        total_chunks = sum(len(chunks) for chunks in self.text_chunks.values())
        chunks_with_embedding = sum(
            1 for chunks in self.text_chunks.values() for chunk in chunks if chunk.get("embedding")
        )
        last_indexed = max(
            (p.indexed_at for p in self.papers.values() if p.indexed_at),
            default=None,
        )
        return {
            "total_papers": total,
            "indexed_papers": indexed,
            "papers_with_embedding": with_embedding,
            "papers_with_summary": with_summary,
            "total_chunks": total_chunks,
            "chunks_with_embedding": chunks_with_embedding,
            "last_indexed_at": last_indexed,
        }

    def search_local(
        self,
        query: str,
        *,
        limit: int,
        query_embedding: list[float] | None = None,
        rrf_k: int = 60,
        citation_boost_scale: float = 0.003,
        citation_boost_max: float = 0.015,
        recency_boost_max: float = 0.005,
        recency_half_life_years: int = 10,
    ) -> list[SearchPaperResult]:
        from math import log10

        query_lower = query.lower()
        current_year = utcnow().year
        results: list[SearchPaperResult] = []
        for paper_id, paper in self.papers.items():
            score = 0.0
            if query_lower in paper.title.lower():
                score += 4.0
            if query_lower in paper.tldr.lower():
                score += 3.5
            if query_lower in paper.summary_short.lower():
                score += 3.5
            if query_lower in paper.abstract.lower():
                score += 2.5
            if query_lower in paper.summary_eli5.lower():
                score += 2.0
            if query_lower in self.search_documents.get(paper_id, "").lower():
                score += 1.0
            if score == 0:
                for chunk in self.text_chunks.get(paper_id, []):
                    if chunk.get("chunk_kind") == "full_text":
                        continue
                    if query_lower in (chunk.get("content") or "").lower():
                        score += 0.8
                        break
            if score > 0:
                # Bake citation + recency boosts into the score so the
                # caller (service.search) can treat MemoryPaperRepository
                # the same way as SqlAlchemyPaperRepository — that is, can
                # safely skip applying citation_boost again to local hits.
                citation_boost = min(
                    citation_boost_scale * log10(1 + (paper.citation_count or 0)),
                    citation_boost_max,
                )
                recency_boost = 0.0
                published = (paper.published or "")[:4]
                if published.isdigit() and recency_half_life_years > 0:
                    age_years = max(0, current_year - int(published))
                    recency_boost = recency_boost_max * max(
                        0.0, 1.0 - age_years / recency_half_life_years
                    )
                score += citation_boost + recency_boost
                results.append(SearchPaperResult(**paper.model_dump(), score=score, source="local"))
        results.sort(key=lambda item: ((item.score or 0), item.citation_count, item.published), reverse=True)
        return results[:limit]

    def search_sections(
        self,
        query: str,
        *,
        limit: int,
        result_set_id: str | None = None,
        paper_ids: list[str] | None = None,
        query_embedding: list[float] | None = None,
        rrf_k: int = 60,
    ) -> list[SearchSectionResult]:
        query_lower = query.lower()
        allowed_paper_ids = set(paper_ids or [])
        if result_set_id:
            allowed_paper_ids.update(paper.paper_id for paper in self.result_sets[result_set_id].papers)
        results: list[SearchSectionResult] = []
        for paper_id, chunks in self.text_chunks.items():
            if allowed_paper_ids and paper_id not in allowed_paper_ids:
                continue
            paper = self.papers.get(paper_id)
            if paper is None:
                continue
            for chunk in chunks:
                # Paper search must skip code chunks (#5 PR 2); those are
                # retrievable only via the paper-scoped code primitives.
                if chunk["chunk_kind"] not in PAPER_SECTION_CHUNK_KINDS:
                    continue
                content = chunk["content"]
                if query_lower not in content.lower():
                    continue
                kind_score = {
                    "section": 0.30,
                    "table": 0.20,
                    "figure_caption": 0.15,
                }.get(chunk["chunk_kind"], 0.0)
                score = len(query_lower) / max(len(content), 1) + kind_score
                results.append(
                    SearchSectionResult(
                        paper_id=paper.paper_id,
                        arxiv_id=paper.arxiv_id,
                        title=paper.title,
                        path=chunk["path"],
                        chunk_kind=chunk["chunk_kind"],
                        preview=content[:320],
                        score=score,
                    )
                )
        results.sort(key=lambda item: (item.score or 0, self.papers[item.paper_id].citation_count), reverse=True)
        return results[:limit]

    def save_result_set(
        self,
        *,
        query: str | None,
        papers: list[SearchPaperResult],
        parent_result_set_id: str | None = None,
        pattern: str | None = None,
        ttl_days: int = 7,
    ) -> ResultSetResponse:
        result_set = ResultSetResponse(
            result_set_id=new_id("rs"),
            query=query,
            created_at=utcnow(),
            expires_at=utcnow() + timedelta(days=ttl_days),
            papers=papers,
        )
        self.result_sets[result_set.result_set_id] = result_set
        return result_set

    def get_result_set(self, result_set_id: str) -> ResultSetResponse | None:
        return self.result_sets.get(result_set_id)

    def grep_result_set(self, result_set_id: str, pattern: str, *, limit: int) -> list[GrepPaperMatch]:
        regex = re.compile(pattern, re.IGNORECASE)
        result_set = self.result_sets[result_set_id]
        matches: list[GrepPaperMatch] = []
        for paper in result_set.papers:
            previews = []
            count = 0
            for chunk in self.text_chunks.get(paper.paper_id, []):
                if regex.search(chunk["content"]):
                    count += len(regex.findall(chunk["content"])) or 1
                    previews.append(f"{chunk['path']}: {chunk['content'][:180]}")
            if count:
                matches.append(
                    GrepPaperMatch(
                        paper_id=paper.paper_id,
                        arxiv_id=paper.arxiv_id,
                        title=paper.title,
                        match_count=count,
                        previews=previews[:5],
                    )
                )
        return matches[:limit]

    def create_browser_user(self, *, email: str, display_name: str, password_hash: str | None) -> BrowserUser:
        user = BrowserUser(user_id=new_id("usr"), email=email, display_name=display_name)
        self.browser_users[user.user_id] = user
        self.browser_users_by_email[email] = user.user_id
        self.browser_user_passwords[user.user_id] = password_hash
        self._ensure_default_project(user.user_id)
        return user

    def get_browser_user_by_email(self, email: str) -> BrowserUser | None:
        user_id = self.browser_users_by_email.get(email)
        return self.browser_users.get(user_id) if user_id else None

    def get_browser_user_by_id(self, user_id: str) -> BrowserUser | None:
        return self.browser_users.get(user_id)

    def get_browser_user_by_google_subject(self, google_subject: str) -> BrowserUser | None:
        user_id = self.google_identities.get(google_subject)
        return self.browser_users.get(user_id) if user_id else None

    def set_browser_user_password(self, user_id: str, password_hash: str) -> BrowserUser | None:
        user = self.browser_users.get(user_id)
        if user is None:
            return None
        self.browser_user_passwords[user_id] = password_hash
        return user

    def get_browser_password_hash(self, user_id: str) -> str | None:
        return self.browser_user_passwords.get(user_id)

    def create_browser_session(self, *, user_id: str, token_hash_value: str, expires_at: Any) -> None:
        self.browser_sessions[token_hash_value] = {
            "user_id": user_id,
            "expires_at": expires_at,
            "last_seen_at": utcnow(),
        }

    def get_browser_user_by_session_token_hash(self, token_hash_value: str) -> BrowserUser | None:
        record = self.browser_sessions.get(token_hash_value)
        if record is None or record["expires_at"] < utcnow():
            self.browser_sessions.pop(token_hash_value, None)
            return None
        record["last_seen_at"] = utcnow()
        return self.browser_users.get(record["user_id"])

    def delete_browser_session(self, token_hash_value: str) -> None:
        self.browser_sessions.pop(token_hash_value, None)

    def create_password_reset_token(self, *, user_id: str, token_hash_value: str, expires_at: Any) -> None:
        self.password_reset_tokens[token_hash_value] = {"user_id": user_id, "expires_at": expires_at, "used_at": None}

    def consume_password_reset_token(self, token_hash_value: str) -> BrowserUser | None:
        record = self.password_reset_tokens.get(token_hash_value)
        if record is None or record["used_at"] is not None or record["expires_at"] < utcnow():
            return None
        record["used_at"] = utcnow()
        return self.browser_users.get(record["user_id"])

    def link_google_identity(self, *, user_id: str, google_subject: str, email: str) -> None:
        self.google_identities[google_subject] = user_id

    def save_anthropic_api_key(self, *, user_id: str, encrypted_api_key: str, hint: str) -> None:
        self.user_api_keys[user_id] = {"encrypted_api_key": encrypted_api_key, "api_key_hint": hint}

    def save_openai_api_key(self, *, user_id: str, encrypted_api_key: str, hint: str) -> None:
        self.user_api_keys[user_id] = {"encrypted_api_key": encrypted_api_key, "api_key_hint": hint}

    def get_anthropic_api_key_record(self, user_id: str) -> dict[str, str] | None:
        return self.user_api_keys.get(user_id)

    def get_openai_api_key_record(self, user_id: str) -> dict[str, str] | None:
        return self.user_api_keys.get(user_id)

    def delete_anthropic_api_key(self, user_id: str) -> None:
        self.user_api_keys.pop(user_id, None)

    def delete_openai_api_key(self, user_id: str) -> None:
        self.user_api_keys.pop(user_id, None)

    def get_browser_user_settings(self, user_id: str) -> BrowserUserSettingsResponse:
        settings = self.browser_user_settings.get(user_id, BrowserUserSettingsResponse())
        record = self.user_api_keys.get(user_id)
        if record is None:
            return settings
        return settings.model_copy(update={"openai_api_key_hint": record.get("api_key_hint", "")})

    def get_modal_credentials_record(self, user_id: str) -> dict[str, str] | None:
        return self.modal_credentials_records.get(user_id)

    def get_github_connection_record(self, user_id: str) -> dict[str, str] | None:
        return self.github_connection_records.get(user_id)

    def save_modal_credentials(
        self,
        *,
        user_id: str,
        encrypted_token_id: str,
        encrypted_token_secret: str,
        token_id_hint: str,
    ) -> None:
        current = self._get_or_create_browser_user_settings(user_id)
        self.modal_credentials_records[user_id] = {
            "encrypted_token_id": encrypted_token_id,
            "encrypted_token_secret": encrypted_token_secret,
            "token_id_hint": token_id_hint,
        }
        self.browser_user_settings[user_id] = current.model_copy(
            update={
                "has_modal_credentials": True,
                "modal_token_id_hint": token_id_hint,
            }
        )

    def delete_modal_credentials(self, user_id: str) -> None:
        current = self._get_or_create_browser_user_settings(user_id)
        self.modal_credentials_records.pop(user_id, None)
        self.browser_user_settings[user_id] = current.model_copy(
            update={
                "has_modal_credentials": False,
                "modal_token_id_hint": "",
            }
        )

    def set_weekly_papers_review_email(self, *, user_id: str, enabled: bool) -> None:
        current = self._get_or_create_browser_user_settings(user_id)
        self.browser_user_settings[user_id] = current.model_copy(update={"weekly_papers_review_email": enabled})

    def set_appearance_theme(self, *, user_id: str, theme: str) -> None:
        current = self._get_or_create_browser_user_settings(user_id)
        self.browser_user_settings[user_id] = current.model_copy(
            update={"appearance_theme": theme if theme in {"light", "dark"} else "light"}
        )

    def save_github_connection(
        self,
        *,
        user_id: str,
        github_user_id: str,
        github_login: str,
        encrypted_access_token: str,
        scope: str,
    ) -> None:
        for candidate_user_id, settings in list(self.browser_user_settings.items()):
            if candidate_user_id == user_id:
                continue
            if settings.github_login == github_login:
                self.github_connection_records.pop(candidate_user_id, None)
                self.browser_user_settings[candidate_user_id] = settings.model_copy(
                    update={
                        "has_github_connection": False,
                        "github_login": "",
                    }
                )
        current = self._get_or_create_browser_user_settings(user_id)
        self.github_connection_records[user_id] = {
            "github_user_id": github_user_id,
            "github_login": github_login,
            "encrypted_access_token": encrypted_access_token,
            "scope": scope,
        }
        self.browser_user_settings[user_id] = current.model_copy(
            update={
                "has_github_connection": True,
                "github_login": github_login,
            }
        )

    def delete_github_connection(self, user_id: str) -> None:
        current = self._get_or_create_browser_user_settings(user_id)
        self.github_connection_records.pop(user_id, None)
        self.browser_user_settings[user_id] = current.model_copy(
            update={
                "has_github_connection": False,
                "github_login": "",
            }
        )

    def create_uploaded_document(
        self,
        *,
        user_id: str,
        title: str,
        source_filename: str,
        content_type: str,
        tags: list[str],
        storage_path: str,
        extracted_text_path: str | None,
        status: str,
    ) -> UploadedDocumentResponse:
        now = utcnow()
        document = UploadedDocumentResponse(
            document_id=new_id("doc"),
            user_id=user_id,
            title=title,
            source_filename=source_filename,
            content_type=content_type,
            tags=tags,
            status=status,
            created_at=now,
            updated_at=now,
        )
        self.uploaded_documents[document.document_id] = document
        return document

    def update_uploaded_document(
        self,
        *,
        document_id: str,
        title: str,
        tags: list[str],
        extracted_text_path: str | None,
        status: str,
    ) -> UploadedDocumentResponse | None:
        document = self.uploaded_documents.get(document_id)
        if document is None:
            return None
        updated = document.model_copy(update={"title": title, "tags": tags, "status": status, "updated_at": utcnow()})
        self.uploaded_documents[document_id] = updated
        return updated

    def get_uploaded_document(self, document_id: str) -> UploadedDocumentResponse | None:
        return self.uploaded_documents.get(document_id)

    def replace_uploaded_document_chunks(self, document_id: str, chunks: list[dict[str, str]]) -> None:
        self.uploaded_document_chunks[document_id] = chunks

    def list_uploaded_documents(self, user_id: str) -> list[UploadedDocumentResponse]:
        return sorted(
            [document for document in self.uploaded_documents.values() if document.user_id == user_id],
            key=lambda item: item.updated_at,
            reverse=True,
        )

    def search_uploaded_document_sections(
        self,
        *,
        user_id: str,
        query: str,
        limit: int,
        tag_filters: list[str] | None = None,
    ) -> list[UploadedDocumentSectionResult]:
        query_lower = query.lower()
        required_tags = set(tag_filters or [])
        results: list[UploadedDocumentSectionResult] = []
        for document in self.list_uploaded_documents(user_id):
            tags = set(document.tags)
            if required_tags and not required_tags.issubset(tags):
                continue
            for chunk in self.uploaded_document_chunks.get(document.document_id, []):
                if query_lower not in chunk["content"].lower() and query_lower not in document.title.lower():
                    continue
                content = chunk["content"]
                score = len(query_lower) / max(len(content), 1)
                results.append(
                    UploadedDocumentSectionResult(
                        document_id=document.document_id,
                        title=document.title,
                        path=chunk["path"],
                        chunk_kind=chunk["chunk_kind"],
                        preview=content[:320],
                        tags=document.tags,
                        score=score,
                    )
                )
        results.sort(key=lambda item: item.score or 0, reverse=True)
        return results[:limit]

    def ensure_default_project(self, user_id: str) -> ProjectResponse:
        return self._ensure_default_project(user_id)

    def get_default_project(self, user_id: str) -> ProjectResponse | None:
        project_id = self.default_projects_by_user.get(user_id, "")
        if not project_id:
            return None
        return self.get_project(user_id=user_id, project_id=project_id)

    def list_projects(self, user_id: str) -> list[ProjectSummary]:
        summaries: list[ProjectSummary] = []
        for project in self.projects.values():
            if self.project_users.get(project.project_id) != user_id:
                continue
            thread_count = sum(
                1
                for thread in self.chat_threads.values()
                if self.chat_thread_users.get(thread.thread_id) == user_id and thread.project_id == project.project_id
            )
            file_count = sum(
                1
                for python_file in self.python_files.values()
                if self.python_file_users.get(python_file.file_id) == user_id
                and python_file.project_id == project.project_id
            )
            sync = self.project_repo_syncs.get(project.project_id)
            summaries.append(
                ProjectSummary(
                    **project.model_copy(
                        update={
                            "session_count": thread_count + file_count,
                            "github_repo_url": sync.github_repo_url if sync else "",
                        }
                    ).model_dump()
                )
            )
        summaries.sort(
            key=lambda item: (not item.is_default, -item.updated_at.timestamp(), item.created_at.timestamp())
        )
        return summaries

    def get_project(self, *, user_id: str, project_id: str) -> ProjectResponse | None:
        project = self.projects.get(project_id)
        if project is None:
            return None
        if self.project_users.get(project_id) != user_id:
            return None
        thread_count = sum(
            1
            for thread in self.chat_threads.values()
            if self.chat_thread_users.get(thread.thread_id) == user_id and thread.project_id == project_id
        )
        file_count = sum(
            1
            for python_file in self.python_files.values()
            if self.python_file_users.get(python_file.file_id) == user_id and python_file.project_id == project_id
        )
        sync = self.project_repo_syncs.get(project_id)
        return project.model_copy(
            update={
                "session_count": thread_count + file_count,
                "github_repo_url": sync.github_repo_url if sync else "",
            }
        )

    def create_project(self, *, user_id: str, name: str, description: str) -> ProjectResponse:
        now = utcnow()
        project = ProjectResponse(
            project_id=new_id("proj"),
            name=name,
            description=description,
            is_default=False,
            session_count=0,
            created_at=now,
            updated_at=now,
        )
        self.projects[project.project_id] = project
        self.project_users[project.project_id] = user_id
        return project

    def update_project(self, *, user_id: str, project_id: str, name: str, description: str) -> ProjectResponse | None:
        project = self.get_project(user_id=user_id, project_id=project_id)
        if project is None:
            return None
        updated = project.model_copy(update={"name": name, "description": description, "updated_at": utcnow()})
        self.projects[project_id] = updated
        return updated

    def delete_project(self, *, user_id: str, project_id: str) -> bool:
        project = self.get_project(user_id=user_id, project_id=project_id)
        if project is None:
            return False
        for thread_id, thread in list(self.chat_threads.items()):
            if self.chat_thread_users.get(thread_id) == user_id and thread.project_id == project_id:
                self.chat_threads.pop(thread_id, None)
                self.chat_thread_users.pop(thread_id, None)
        for file_id, python_file in list(self.python_files.items()):
            if self.python_file_users.get(file_id) == user_id and python_file.project_id == project_id:
                self.python_files.pop(file_id, None)
                self.python_file_users.pop(file_id, None)
        self.project_repo_syncs.pop(project_id, None)
        if self.default_projects_by_user.get(user_id) == project_id:
            self.default_projects_by_user.pop(user_id, None)
        self.projects.pop(project_id, None)
        self.project_users.pop(project_id, None)
        return True

    def list_project_chat_threads(
        self,
        *,
        user_id: str,
        project_id: str,
        limit: int,
        offset: int,
    ) -> ProjectSessionPageResponse | None:
        project = self.get_project(user_id=user_id, project_id=project_id)
        if project is None:
            return None
        summaries = [
            ChatThreadSummary(
                thread_id=thread.thread_id,
                project_id=thread.project_id,
                title=thread.title,
                created_at=thread.created_at,
                updated_at=thread.updated_at,
                last_message_preview=thread.items[-1].content_text[:120] if thread.items else "",
            )
            for thread in self.chat_threads.values()
            if self.chat_thread_users.get(thread.thread_id) == user_id and thread.project_id == project_id
        ]
        summaries.sort(key=lambda item: item.updated_at, reverse=True)
        total_count = len(summaries)
        page = summaries[offset : offset + limit]
        return ProjectSessionPageResponse(
            project_id=project_id,
            threads=page,
            offset=offset,
            limit=limit,
            total_count=total_count,
            has_more=offset + len(page) < total_count,
        )

    def list_project_items(
        self,
        *,
        user_id: str,
        project_id: str,
        limit: int,
        offset: int,
        item_kind: str | None = None,
    ) -> ProjectItemPageResponse | None:
        project = self.get_project(user_id=user_id, project_id=project_id)
        if project is None:
            return None
        items: list[ProjectItemSummary] = [
            _project_item_from_thread_summary(summary)
            for summary in self.list_project_chat_threads(
                user_id=user_id,
                project_id=project_id,
                limit=10_000,
                offset=0,
            ).threads
        ]
        items.extend(
            _project_item_from_python_file(
                PythonFileSummary(
                    file_id=file.file_id,
                    project_id=file.project_id,
                    filename=file.filename,
                    created_at=file.created_at,
                    updated_at=file.updated_at,
                )
            )
            for file in self.python_files.values()
            if self.python_file_users.get(file.file_id) == user_id and file.project_id == project_id
        )
        if item_kind:
            items = [item for item in items if item.item_kind == item_kind]
        items.sort(key=lambda item: (item.updated_at, item.created_at), reverse=True)
        page = items[offset : offset + limit]
        return ProjectItemPageResponse(
            project_id=project_id,
            items=page,
            offset=offset,
            limit=limit,
            total_count=len(items),
            has_more=offset + len(page) < len(items),
        )

    def list_project_python_files(
        self,
        *,
        user_id: str,
        project_id: str,
    ) -> list[PythonFileResponse] | None:
        if self.get_project(user_id=user_id, project_id=project_id) is None:
            return None
        files = [
            file
            for file in self.python_files.values()
            if self.python_file_users.get(file.file_id) == user_id and file.project_id == project_id
        ]
        files.sort(key=lambda item: item.updated_at, reverse=True)
        return files

    def create_python_file(
        self,
        *,
        user_id: str,
        project_id: str,
        filename: str,
        content: str = "",
        file_kind: str = "python",
        repo_path: str | None = None,
    ) -> PythonFileResponse | None:
        project = self.get_project(user_id=user_id, project_id=project_id)
        if project is None:
            return None
        existing_repo_paths = [
            file.repo_path
            for file in self.python_files.values()
            if self.python_file_users.get(file.file_id) == user_id and file.project_id == project_id
        ]
        base_repo_path = _repo_path_for_filename(filename, file_kind, repo_path)
        resolved_repo_path = _resolve_unique_repo_path(base_repo_path, existing_repo_paths)
        resolved_filename = _filename_from_repo_path(resolved_repo_path)
        now = utcnow()
        file = PythonFileResponse(
            file_id=new_id("pyf"),
            project_id=project_id,
            filename=resolved_filename,
            file_kind=file_kind,  # type: ignore[arg-type]
            repo_path=resolved_repo_path,
            content=content,
            created_at=now,
            updated_at=now,
        )
        self.python_files[file.file_id] = file
        self.python_file_users[file.file_id] = user_id
        self.projects[project_id] = project.model_copy(update={"updated_at": now})
        return file

    def get_python_file(self, *, user_id: str, file_id: str) -> PythonFileResponse | None:
        if self.python_file_users.get(file_id) != user_id:
            return None
        return self.python_files.get(file_id)

    def update_python_file(
        self,
        *,
        user_id: str,
        file_id: str,
        filename: str | None = None,
        content: str | None = None,
        repo_path: str | None = None,
    ) -> PythonFileResponse | None:
        current = self.get_python_file(user_id=user_id, file_id=file_id)
        if current is None:
            return None
        next_repo_path = current.repo_path or _repo_path_for_filename(current.filename, current.file_kind)
        next_filename = current.filename
        if filename is not None and filename.strip():
            existing_repo_paths = [
                file.repo_path
                for file in self.python_files.values()
                if self.python_file_users.get(file.file_id) == user_id and file.project_id == current.project_id
            ]
            requested_repo_path = _repo_path_with_filename(
                next_repo_path,
                filename,
            )
            next_repo_path = _resolve_unique_repo_path(
                requested_repo_path,
                existing_repo_paths,
                exclude_repo_path=current.repo_path,
            )
            next_filename = _filename_from_repo_path(next_repo_path)
        if repo_path is not None and repo_path.strip():
            existing_repo_paths = [
                file.repo_path
                for file in self.python_files.values()
                if self.python_file_users.get(file.file_id) == user_id and file.project_id == current.project_id
            ]
            next_repo_path = _resolve_unique_repo_path(
                repo_path.strip(),
                existing_repo_paths,
                exclude_repo_path=current.repo_path,
            )
            next_filename = _filename_from_repo_path(next_repo_path)
        updated_at = utcnow()
        updated = current.model_copy(
            update={
                "filename": next_filename,
                "repo_path": next_repo_path,
                "content": current.content if content is None else content,
                "updated_at": updated_at,
            }
        )
        self.python_files[file_id] = updated
        project = self.projects.get(current.project_id)
        if project is not None:
            self.projects[current.project_id] = project.model_copy(update={"updated_at": updated_at})
        return updated

    def get_project_python_file_by_path(
        self,
        *,
        user_id: str,
        project_id: str,
        repo_path: str,
    ) -> PythonFileResponse | None:
        for file in self.python_files.values():
            if (
                self.python_file_users.get(file.file_id) == user_id
                and file.project_id == project_id
                and file.repo_path == repo_path
            ):
                return file
        return None

    def delete_python_file(self, *, user_id: str, file_id: str) -> bool:
        current = self.get_python_file(user_id=user_id, file_id=file_id)
        if current is None:
            return False
        self.python_files.pop(file_id, None)
        self.python_file_users.pop(file_id, None)
        project = self.projects.get(current.project_id)
        if project is not None:
            self.projects[current.project_id] = project.model_copy(update={"updated_at": utcnow()})
        return True

    def get_project_repo_sync(self, *, user_id: str, project_id: str) -> ProjectRepoSyncResponse | None:
        if self.get_project(user_id=user_id, project_id=project_id) is None:
            return None
        return self.project_repo_syncs.get(
            project_id,
            ProjectRepoSyncResponse(project_id=project_id, repo_exists=False),
        )

    def save_project_repo_sync(
        self,
        *,
        user_id: str,
        project_id: str,
        github_repo_name: str,
        github_repo_url: str,
        default_branch: str,
        last_sync_commit_sha: str,
        last_sync_commit_url: str,
        github_owner_login: str = "",
        base_branch: str | None = None,
        last_synced_at: Any = None,
    ) -> ProjectRepoSyncResponse | None:
        if self.get_project(user_id=user_id, project_id=project_id) is None:
            return None
        response = ProjectRepoSyncResponse(
            project_id=project_id,
            repo_exists=True,
            github_owner_login=github_owner_login,
            github_repo_name=github_repo_name,
            github_repo_url=github_repo_url,
            base_branch=base_branch or default_branch,
            default_branch=default_branch,
            last_sync_commit_sha=last_sync_commit_sha,
            last_sync_commit_url=last_sync_commit_url,
            last_synced_at=last_synced_at,
        )
        self.project_repo_syncs[project_id] = response
        return response

    def create_chat_thread(
        self,
        *,
        user_id: str,
        title: str,
        project_id: str,
        project_name: str,
        project_instructions: str,
    ) -> ChatThreadSummary:
        now = utcnow()
        thread = ChatThreadResponse(
            thread_id=new_id("thread"),
            project_id=project_id,
            project_name=project_name,
            project_instructions=project_instructions,
            title=title or "New chat",
            created_at=now,
            updated_at=now,
            items=[],
            turns=[],
        )
        self.chat_threads[thread.thread_id] = thread
        self.chat_thread_users[thread.thread_id] = user_id
        project = self.projects.get(project_id)
        if project is not None:
            self.projects[project_id] = project.model_copy(update={"updated_at": now})
        return ChatThreadSummary(
            thread_id=thread.thread_id,
            project_id=thread.project_id,
            title=thread.title,
            created_at=thread.created_at,
            updated_at=thread.updated_at,
            last_message_preview="",
        )

    def rename_chat_thread(self, *, thread_id: str, title: str) -> None:
        thread = self.chat_threads.get(thread_id)
        if thread:
            updated_at = utcnow()
            self.chat_threads[thread_id] = thread.model_copy(update={"title": title, "updated_at": updated_at})
            project = self.projects.get(thread.project_id)
            if project is not None:
                self.projects[thread.project_id] = project.model_copy(update={"updated_at": updated_at})

    def move_chat_thread_to_project(
        self,
        *,
        user_id: str,
        thread_id: str,
        project_id: str,
        project_name: str,
        project_instructions: str,
    ) -> ChatThreadResponse | None:
        thread = self.get_chat_thread(user_id=user_id, thread_id=thread_id)
        if thread is None:
            return None
        updated_at = utcnow()
        updated = thread.model_copy(
            update={
                "project_id": project_id,
                "project_name": project_name,
                "project_instructions": project_instructions,
                "updated_at": updated_at,
            }
        )
        self.chat_threads[thread_id] = updated
        previous_project = self.projects.get(thread.project_id)
        if previous_project is not None:
            self.projects[thread.project_id] = previous_project.model_copy(update={"updated_at": updated_at})
        next_project = self.projects.get(project_id)
        if next_project is not None:
            self.projects[project_id] = next_project.model_copy(update={"updated_at": updated_at})
        return updated

    def list_chat_threads(self, user_id: str) -> list[ChatThreadSummary]:
        summaries: list[ChatThreadSummary] = []
        for thread in self.chat_threads.values():
            if self.chat_thread_users.get(thread.thread_id) != user_id:
                continue
            last_message = thread.items[-1].content_text[:120] if thread.items else ""
            summaries.append(
                ChatThreadSummary(
                    thread_id=thread.thread_id,
                    project_id=thread.project_id,
                    title=thread.title,
                    created_at=thread.created_at,
                    updated_at=thread.updated_at,
                    last_message_preview=last_message,
                )
            )
        summaries.sort(key=lambda item: item.updated_at, reverse=True)
        return summaries

    def get_chat_thread(self, *, user_id: str, thread_id: str) -> ChatThreadResponse | None:
        if self.chat_thread_users.get(thread_id) != user_id:
            return None
        return self.chat_threads.get(thread_id)

    def create_chat_turn(
        self,
        *,
        thread_id: str,
        status: str = "running",
        provider_response_id: str = "",
    ) -> ChatTurnResponse:
        thread = self.chat_threads[thread_id]
        turn = ChatTurnResponse(
            turn_id=new_id("turn"),
            thread_id=thread_id,
            status=status,
            provider_response_id=provider_response_id,
            started_at=utcnow(),
            completed_at=None,
            usage=None,
            diff_summary="",
            changed_paths=[],
            exec_session_ids=[],
            items=[],
        )
        updated_at = utcnow()
        self.chat_threads[thread_id] = thread.model_copy(
            update={"turns": [*thread.turns, turn], "updated_at": updated_at}
        )
        return turn

    def update_chat_turn(
        self,
        turn_id: str,
        *,
        status: str | None = None,
        provider_response_id: str | None = None,
        completed_at: Any | None = None,
        usage: dict[str, Any] | None = None,
        diff_summary: str | None = None,
        changed_paths: list[str] | None = None,
        exec_session_ids: list[str] | None = None,
    ) -> ChatTurnResponse | None:
        for thread_id, thread in self.chat_threads.items():
            for index, turn in enumerate(thread.turns):
                if turn.turn_id != turn_id:
                    continue
                updated_turn = turn.model_copy(
                    update={
                        "status": turn.status if status is None else status,
                        "provider_response_id": turn.provider_response_id
                        if provider_response_id is None
                        else provider_response_id,
                        "completed_at": turn.completed_at if completed_at is None else completed_at,
                        "usage": turn.usage if usage is None else usage,
                        "diff_summary": turn.diff_summary if diff_summary is None else diff_summary,
                        "changed_paths": turn.changed_paths if changed_paths is None else list(changed_paths),
                        "exec_session_ids": turn.exec_session_ids if exec_session_ids is None else list(exec_session_ids),
                    }
                )
                turns = list(thread.turns)
                turns[index] = updated_turn
                updated_at = utcnow()
                self.chat_threads[thread_id] = thread.model_copy(update={"turns": turns, "updated_at": updated_at})
                return updated_turn
        return None

    def add_chat_thread_item(
        self,
        *,
        thread_id: str,
        turn_id: str,
        item_type: str,
        role: str,
        phase: str | None = None,
        call_id: str | None = None,
        name: str | None = None,
        content_text: str = "",
        content_json: Any | None = None,
        citations: list[dict[str, Any]] | None = None,
        status: str = "completed",
        item_id: str | None = None,
    ) -> ChatThreadItemResponse:
        item = ChatThreadItemResponse(
            item_id=item_id or new_id("item"),
            thread_id=thread_id,
            turn_id=turn_id,
            item_type=item_type,
            role=role,
            phase=phase,  # type: ignore[arg-type]
            call_id=call_id,
            name=name,
            content_text=content_text,
            content_json=content_json,
            citations=[ChatCitation.model_validate(citation) for citation in (citations or [])],
            created_at=utcnow(),
            status=status,
        )
        thread = self.chat_threads[thread_id]
        turns: list[ChatTurnResponse] = []
        for turn in thread.turns:
            if turn.turn_id == turn_id:
                turns.append(turn.model_copy(update={"items": [*turn.items, item]}))
            else:
                turns.append(turn)
        updated_at = utcnow()
        self.chat_threads[thread_id] = thread.model_copy(
            update={"items": [*thread.items, item], "turns": turns, "updated_at": updated_at}
        )
        return item

    def update_chat_thread_item(
        self,
        item_id: str,
        *,
        phase: str | None = None,
        content_text: str | None = None,
        content_json: Any | None = None,
        citations: list[dict[str, Any]] | None = None,
        status: str | None = None,
        name: str | None = None,
        call_id: str | None = None,
        item_type: str | None = None,
    ) -> ChatThreadItemResponse | None:
        for thread_id, thread in self.chat_threads.items():
            for index, item in enumerate(thread.items):
                if item.item_id != item_id:
                    continue
                updated_item = item.model_copy(
                    update={
                        "phase": item.phase if phase is None else phase,
                        "content_text": item.content_text if content_text is None else content_text,
                        "content_json": item.content_json if content_json is None else content_json,
                        "citations": item.citations
                        if citations is None
                        else [ChatCitation.model_validate(citation) for citation in citations],
                        "status": item.status if status is None else status,
                        "name": item.name if name is None else name,
                        "call_id": item.call_id if call_id is None else call_id,
                        "item_type": item.item_type if item_type is None else item_type,
                    }
                )
                items = list(thread.items)
                items[index] = updated_item
                turns: list[ChatTurnResponse] = []
                for turn in thread.turns:
                    turn_items = [updated_item if turn_item.item_id == item_id else turn_item for turn_item in turn.items]
                    turns.append(turn.model_copy(update={"items": turn_items}))
                updated_at = utcnow()
                self.chat_threads[thread_id] = thread.model_copy(
                    update={"items": items, "turns": turns, "updated_at": updated_at}
                )
                return updated_item
        return None

    def list_chat_turn_items(self, *, thread_id: str, turn_id: str) -> list[ChatThreadItemResponse]:
        thread = self.chat_threads.get(thread_id)
        if thread is None:
            return []
        return [item for item in thread.items if item.turn_id == turn_id]

    def add_chat_message(
        self,
        *,
        thread_id: str,
        role: str,
        content_markdown: str,
        citations: list[dict[str, Any]] | None = None,
        status: str = "completed",
        kind: str = "default",
    ) -> ChatMessageResponse:
        turn = self.create_chat_turn(thread_id=thread_id, status="completed")
        item = self.add_chat_thread_item(
            thread_id=thread_id,
            turn_id=turn.turn_id,
            item_type="compaction" if kind == "compaction" else "assistant_message" if role == "assistant" else "user_message",
            role=role,
            phase="final_answer" if role == "assistant" and kind != "compaction" else None,
            content_text=content_markdown,
            citations=citations,
            status=status,
        )
        return ChatMessageResponse(
            message_id=item.item_id,
            role=item.role,
            content_markdown=item.content_text,
            citations=item.citations,
            created_at=item.created_at,
            status=item.status,
            kind=kind,
        )

    def update_chat_message(
        self,
        message_id: str,
        *,
        content_markdown: str | None = None,
        citations: list[dict[str, Any]] | None = None,
        status: str | None = None,
        kind: str | None = None,
    ) -> ChatMessageResponse | None:
        updated_item = self.update_chat_thread_item(
            message_id,
            content_text=content_markdown,
            citations=citations,
            status=status,
            item_type="compaction" if kind == "compaction" else None,
        )
        if updated_item is None:
            return None
        return ChatMessageResponse(
            message_id=updated_item.item_id,
            role=updated_item.role,
            content_markdown=updated_item.content_text,
            citations=updated_item.citations,
            created_at=updated_item.created_at,
            status=updated_item.status,
            kind=kind or updated_item.kind,
        )

    def archive_chat_messages(
        self,
        *,
        thread_id: str,
        message_ids: list[str],
        archived_by_message_id: str,
    ) -> int:
        del archived_by_message_id
        thread = self.chat_threads.get(thread_id)
        if thread is None or not message_ids:
            return 0
        archived_ids = set(message_ids)
        items = [item for item in thread.items if item.item_id not in archived_ids]
        archived_count = len(thread.items) - len(items)
        if archived_count == 0:
            return 0
        turns: list[ChatTurnResponse] = []
        for turn in thread.turns:
            turns.append(turn.model_copy(update={"items": [item for item in turn.items if item.item_id not in archived_ids]}))
        updated_at = utcnow()
        self.chat_threads[thread_id] = thread.model_copy(
            update={"items": items, "turns": turns, "updated_at": updated_at}
        )
        project = self.projects.get(thread.project_id)
        if project is not None:
            self.projects[thread.project_id] = project.model_copy(update={"updated_at": updated_at})
        return archived_count

    def delete_chat_thread(self, *, user_id: str, thread_id: str) -> bool:
        if self.chat_thread_users.get(thread_id) != user_id:
            return False
        thread = self.chat_threads.pop(thread_id, None)
        self.chat_thread_users.pop(thread_id, None)
        if thread is None:
            return False
        project = self.projects.get(thread.project_id)
        if project is not None:
            self.projects[thread.project_id] = project.model_copy(update={"updated_at": utcnow()})
        return True

    def create_job(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        max_attempts: int | None = None,
        priority: int | None = None,
        dedupe_key: str | None = None,
        root_job_id: str | None = None,
    ) -> JobResponse:
        if dedupe_key:
            existing_id = self.job_dedupe_keys.get(dedupe_key)
            if existing_id is not None and existing_id in self.jobs:
                return self.jobs[existing_id]
        job_id = new_id("job")
        job = JobResponse(
            job_id=job_id,
            root_job_id=root_job_id or job_id,
            job_type=job_type,
            status="pending",
            payload=payload,
            max_attempts=max_attempts if max_attempts is not None else default_max_attempts(job_type),
            priority=priority if priority is not None else default_priority(job_type),
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self.jobs[job.job_id] = job
        if dedupe_key:
            self.job_dedupe_keys[dedupe_key] = job.job_id
        return job

    def get_job(self, job_id: str) -> JobResponse | None:
        return self.jobs.get(job_id)

    def claim_pending_job(self) -> JobResponse | None:
        now = utcnow()
        # priority ASC, next_run_at NULLS FIRST, created_at ASC — same
        # ordering as SQL. Priority keeps bulk backfill from starving
        # interactive on-demand work.
        candidates = [
            (job_id, job)
            for job_id, job in self.jobs.items()
            if job.status == "pending" and (job.next_run_at is None or job.next_run_at <= now)
        ]
        candidates.sort(
            key=lambda item: (item[1].priority or 0, item[1].next_run_at is not None, item[1].created_at)
        )
        if not candidates:
            return None
        job_id, job = candidates[0]
        claimed = job.model_copy(
            update={
                "status": "running",
                "started_at": utcnow(),
                "updated_at": utcnow(),
                "last_attempted_at": utcnow(),
                "attempts": (job.attempts or 0) + 1,
            }
        )
        self.jobs[job_id] = claimed
        return claimed

    def mark_job_running(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job:
            self.jobs[job_id] = job.model_copy(
                update={"status": "running", "updated_at": utcnow(), "started_at": job.started_at or utcnow()}
            )

    def mark_job_completed(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job:
            # Clear retry metadata from prior failed attempts so the
            # completed JobResponse doesn't surface a stale transient
            # error message or a dangling next_run_at.
            self.jobs[job_id] = job.model_copy(
                update={
                    "status": "completed",
                    "updated_at": utcnow(),
                    "finished_at": utcnow(),
                    "error": None,
                    "next_run_at": None,
                }
            )

    def mark_job_failed(
        self,
        job_id: str,
        error: str,
        *,
        permanent: bool = False,
        next_run_at: Any | None = None,
    ) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            return
        attempts = job.attempts or 0
        max_attempts = job.max_attempts or 3
        if permanent or attempts >= max_attempts:
            self.jobs[job_id] = job.model_copy(
                update={
                    "status": "dead",
                    "error": error,
                    "updated_at": utcnow(),
                    "finished_at": utcnow(),
                    "next_run_at": None,
                }
            )
        else:
            self.jobs[job_id] = job.model_copy(
                update={
                    "status": "pending",
                    "error": error,
                    "updated_at": utcnow(),
                    "next_run_at": next_run_at,
                }
            )

    def requeue_dead_job(self, job_id: str) -> JobResponse | None:
        job = self.jobs.get(job_id)
        if job is None or job.status not in {"dead", "failed"}:
            return None
        self.jobs[job_id] = job.model_copy(
            update={
                "status": "pending",
                "attempts": 0,
                "error": None,
                "next_run_at": None,
                "finished_at": None,
                "updated_at": utcnow(),
            }
        )
        return self.jobs[job_id]

    def list_jobs_by_status(self, status: str, *, limit: int = 50) -> list[JobResponse]:
        jobs = [job for job in self.jobs.values() if job.status == status]
        jobs.sort(key=lambda item: item.updated_at, reverse=True)
        return jobs[:limit]

    def list_recent_jobs(self, *, limit: int = 200) -> list[JobResponse]:
        jobs = sorted(self.jobs.values(), key=lambda item: item.updated_at, reverse=True)
        return jobs[:limit]

    def list_jobs_for_root(self, root_job_id: str) -> list[JobResponse]:
        jobs = [job for job in self.jobs.values() if job.root_job_id == root_job_id]
        jobs.sort(key=lambda item: item.updated_at, reverse=True)
        return jobs

    def record_job_paper(
        self,
        job_id: str,
        paper_id: str,
        *,
        outcome: str,
        arxiv_id: str | None = None,
        title: str | None = None,
    ) -> None:
        key = (job_id, paper_id)
        existing = self.job_papers.get(key)
        created_at = existing.created_at if existing is not None else utcnow()
        self.job_papers[key] = JobPaperRecord(
            job_id=job_id,
            paper_id=paper_id,
            arxiv_id=arxiv_id,
            title=title,
            outcome=outcome,
            created_at=created_at,
            updated_at=utcnow(),
        )

    def list_job_papers_for_root(self, root_job_id: str) -> list[JobPaperRecord]:
        job_ids = {job.job_id for job in self.jobs.values() if job.root_job_id == root_job_id}
        records = [record for record in self.job_papers.values() if record.job_id in job_ids]
        records.sort(key=lambda item: item.updated_at, reverse=True)
        return records

    def cleanup_expired_results(self) -> None:
        now = utcnow()
        for result_set_id in [key for key, value in self.result_sets.items() if value.expires_at < now]:
            del self.result_sets[result_set_id]

    def record_search_log(
        self,
        *,
        query_id: str,
        user_id: str | None,
        query_text: str,
        request_rerank: bool,
        top_paper_ids: list[str],
        result_set_id: str | None,
        latency_ms: int | None,
    ) -> None:
        self.search_logs[query_id] = {
            "query_id": query_id,
            "user_id": user_id,
            "query_text": query_text,
            "request_rerank": bool(request_rerank),
            "top_paper_ids": list(top_paper_ids),
            "result_set_id": result_set_id,
            "latency_ms": latency_ms,
            "clicked_paper_id": None,
            "clicked_at": None,
            "created_at": utcnow(),
        }

    def record_search_click(self, query_id: str, paper_id: str) -> bool:
        row = self.search_logs.get(query_id)
        if row is None:
            return False
        if paper_id not in (row.get("top_paper_ids") or []):
            return False
        row["clicked_paper_id"] = paper_id
        row["clicked_at"] = utcnow()
        return True


def build_repository(settings: Settings) -> PaperRepository:
    if settings.database_url.startswith("memory://"):
        return MemoryPaperRepository()
    return SqlAlchemyPaperRepository(settings)
