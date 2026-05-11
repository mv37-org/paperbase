"""Application service that orchestrates indexing, search, and artifact retrieval."""

from __future__ import annotations

import difflib
import base64
import inspect
import json
import logging
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from urllib import error as urllib_error
from urllib.parse import quote
from urllib import request as urllib_request
from collections.abc import Generator, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

import requests

from . import code_ingest

from fastapi import HTTPException
from cryptography.fernet import InvalidToken

from .browser_code import (
    ModalExecutionError,
    ModalPythonRunner,
    GitHubProjectRepoManager,
    GitHubSyncConflictError,
    GitHubSyncError,
)
from .config import Settings, get_settings
from .emailer import EmailSender, ResendEmailSender
from .extractors import (
    build_manifest,
    extract_github_candidates,
    rank_github_candidates,
    extract_uploaded_document,
    figure_index_payload,
    index_paper_artifacts,
    table_csv_bytes,
    table_index_payload,
    table_json_bytes,
)
from .llm import (
    OpenAIResponsesChatModelClient,
    Prompt,
    ResponseEnvelope,
    ResponseEvent,
    ResponseItem,
    ChatModelClient,
    ChatModelUsage,
)
from .models import (
    AdminJobChildRow,
    AdminJobOverviewRow,
    AdminJobPaperResponse,
    AdminJobPapersResponse,
    AdminJobsOverviewResponse,
    AdminPaperDetailResponse,
    AdminPaperJobTouchResponse,
    AdminWorkerSummary,
    BrowserSessionResponse,
    BrowserUser,
    BrowserUserSettingsResponse,
    ChatCitation,
    ChatFeedbackResponse,
    ChatMessageResponse,
    ChatThreadItemResponse,
    ChatThreadListResponse,
    ChatThreadResponse,
    ChatTurnResponse,
    CodeContextResponse,
    CodeEligibility,
    CodeFileListResponse,
    CodeFileSummary,
    CodeReadResponse,
    CodeSearchHit,
    CodeSearchResponse,
    CreatePaperCommentRequest,
    CreateChatFeedbackRequest,
    CreateChatThreadRequest,
    MoveChatThreadProjectRequest,
    FileContentResponse,
    FileListResponse,
    FileObject,
    FigureResponse,
    ForgotPasswordRequest,
    GrepResponse,
    IntegrationProbeComponent,
    IntegrationProbeResponse,
    JobPaperRecord,
    JobResponse,
    LoginRequest,
    ManifestResponse,
    PaperCommentsResponse,
    PaperDiscussionResponse,
    PaperMetadata,
    PaperAssetResponse,
    PaperCommentAuthor,
    PaperCommentRecord,
    PaperCommentResponse,
    PaperPreviewResponse,
    PaperResponse,
    PaperSelectionPayload,
    PaperVoteSummary,
    GitHubRepoBranchSummary,
    GitHubRepoImportCandidate,
    GitHubRepoImportCandidateResponse,
    GitHubRepoListResponse,
    GitHubRepoMetadataResponse,
    GitHubRepoSummary,
    ProjectItemPageResponse,
    ProjectPresentationResponse,
    ProjectPresentationShareRequest,
    ProjectPresentationSlideResponse,
    ProjectPresentationStartRequest,
    ProjectRepoConflictDetail,
    ProjectRepoConflictFile,
    ProjectRepoSyncRequest,
    ProjectRepoSyncResponse,
    ProjectListResponse,
    ProjectResponse,
    ProjectSessionPageResponse,
    PythonFileResponse,
    ResetPasswordRequest,
    ResultSetResponse,
    RunPythonFileResponse,
    SaveElevenLabsKeyRequest,
    SaveOpenAIKeyRequest,
    SaveModalCredentialsRequest,
    SearchPaperResult,
    SearchRequest,
    SearchResponse,
    SearchSectionRequest,
    SearchSectionResponse,
    SendChatMessageRequest,
    SendChatMessageResponse,
    SignupRequest,
    TableResponse,
    TrendingFeedResponse,
    TrendingPaperDetailResponse,
    UpdateAppearanceRequest,
    UpdateChatThreadRequest,
    UpdateNotificationsRequest,
    UpdateProjectRequest,
    CreateProjectRequest,
    UploadListResponse,
    UploadedDocumentResponse,
)
from .repository import JOB_LEASE_SECONDS, PaperRepository, build_repository
from .security import (
    SecretCipher,
    api_key_hint,
    hash_password,
    new_browser_token,
    password_needs_rehash,
    token_hash,
    verify_password,
)
from .summaries import build_paper_summary
from .storage import ObjectStore, build_object_store
from . import sources
from .errors import JobCanceled, PermanentError, RetryableError
from .utils import canonical_paper_id, new_id, slugify, utcnow


logger = logging.getLogger(__name__)
_CURRENT_JOB: ContextVar[JobResponse | None] = ContextVar("paperbase_current_job", default=None)
JOB_HEARTBEAT_INTERVAL_SECONDS = 60


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Windows drive-letter prefix for `_normalize_code_path` (#5 PR 2 review).
# Matches ``C:/...`` / ``C:\...`` / ``c:/...`` but deliberately NOT bare
# ``a:b.txt`` or ``v:1/notes.md``, which are valid GitHub filenames.
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:[\\/]")
_PROMPT_ASSET_ROOT = Path(__file__).resolve().parent / "prompt_assets"
_PROJECT_WORKSPACE_BINARY_EXTENSIONS = {
    ".7z",
    ".a",
    ".bin",
    ".class",
    ".dll",
    ".dylib",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".npy",
    ".npz",
    ".pdf",
    ".pkl",
    ".png",
    ".pyc",
    ".so",
    ".tar",
    ".tif",
    ".tiff",
    ".wav",
    ".webp",
    ".zip",
}
_TEXTUAL_MIME_PREFIXES = ("text/",)
_TEXTUAL_MIME_TYPES = {
    "application/json",
    "application/ld+json",
    "application/xml",
    "application/x-yaml",
    "image/svg+xml",
}
_PROJECT_PRESENTATION_MAX_CONTEXT_CHARS = 80_000
_PROJECT_PRESENTATION_MAX_RESEARCH_MATERIALS = 10
_PROJECT_PRESENTATION_MAX_PASSAGES_PER_PAPER = 4
_PROJECT_PRESENTATION_MAX_COMMENTS_PER_PAPER = 4
_PROJECT_PRESENTATION_MAX_FIGURES_PER_PAPER = 4
_PROJECT_PRESENTATION_MAX_TABLES_PER_PAPER = 2
_PROJECT_PRESENTATION_MAX_TABLE_ROWS = 4
_PROJECT_PRESENTATION_MAX_SLIDES = 8
_PROJECT_PRESENTATION_MAX_IMAGE_SLIDES = 3
_PROJECT_PRESENTATION_IMAGE_MODEL = "gpt-image-2"
_PROJECT_PRESENTATION_IMAGE_TIMEOUT_SECONDS = 45
_PROJECT_PRESENTATION_TTS_TIMEOUT_SECONDS = 45
_PROJECT_PRESENTATION_TEXT_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "project_presentation",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary_markdown": {
                "type": "string",
                "description": "Markdown research briefing about the actual research material, including tables when useful.",
            },
            "visual_code": {
                "type": "string",
                "description": "Legacy first-slide plain browser JavaScript fallback that renders into #root.",
            },
            "slides": {
                "type": "array",
                "description": "Ordered research presentation slides derived from the full project history.",
                "minItems": 1,
                "maxItems": _PROJECT_PRESENTATION_MAX_SLIDES,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "slide_id": {
                            "type": "string",
                            "description": "Stable lowercase identifier using letters, numbers, and hyphens.",
                        },
                        "title": {
                            "type": "string",
                            "description": (
                                "Short direct research fact or conclusion. No wordplay, metaphor, "
                                "question, or theme label."
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": (
                                "Evidence-heavy explanation of that one fact or conclusion. "
                                "Title plus description must be at most 80 words."
                            ),
                        },
                        "visual_type": {"type": "string", "enum": ["code", "image"]},
                        "visual_code": {
                            "type": "string",
                            "description": (
                                "Plain browser JavaScript for responsive D3/SVG/chart code slides, otherwise empty."
                            ),
                        },
                        "image_prompt": {
                            "type": "string",
                            "description": "Detailed GPT Image 2 prompt for image slides, otherwise empty.",
                        },
                    },
                    "required": [
                        "slide_id",
                        "title",
                        "description",
                        "visual_type",
                        "visual_code",
                        "image_prompt",
                    ],
                },
            },
        },
        "required": ["summary_markdown", "visual_code", "slides"],
    },
}
_PROJECT_PRESENTATION_AUDIO_TEXT_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "project_presentation_audio_script",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "turns": {
                "type": "array",
                "description": "Ordered two-person dialogue turns for text-to-speech.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "speaker": {"type": "string", "enum": ["noob", "expert"]},
                        "text": {"type": "string"},
                    },
                    "required": ["speaker", "text"],
                },
            }
        },
        "required": ["turns"],
    },
}
_PROJECT_PRESENTATION_MAX_THREADS = 30
_PROJECT_PRESENTATION_MAX_THREAD_ITEMS = 80
_PROJECT_PRESENTATION_MAX_EXPLORATIONS = 80
_PROJECT_PRESENTATION_MAX_ANNOTATIONS = 40
_PROJECT_PRESENTATION_MAX_CODE_ARTIFACTS = 12
_PROJECT_PRESENTATION_MAX_CODE_CHARS = 8000


def _validate_date(value: str, *, name: str) -> None:
    """Raise PermanentError if ``value`` is not ``YYYY-MM-DD``.

    Harvest payloads come from operators via the CLI/API; a bad date
    should dead-letter the planner job, not loop through the retry ladder.
    """
    from datetime import date

    if not value or not _DATE_RE.match(value):
        raise PermanentError(f"{name} must be YYYY-MM-DD, got {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise PermanentError(f"{name}={value!r} is not a valid calendar date: {exc}") from exc


def _month_windows(start_date: str, end_date: str, *, chunk_months: int) -> list[tuple[str, str]]:
    """Split the inclusive ``[start_date, end_date]`` range into
    ``chunk_months``-wide windows. Each window is inclusive on both ends.

    The last window is clamped to ``end_date`` so the planner never
    enqueues chunks past the requested end.
    """
    from datetime import date, timedelta

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    windows: list[tuple[str, str]] = []
    cursor = start
    while cursor <= end:
        year = cursor.year
        month = cursor.month + chunk_months
        year += (month - 1) // 12
        month = ((month - 1) % 12) + 1
        next_window_start = date(year, month, 1)
        window_end = min(end, next_window_start - timedelta(days=1))
        windows.append((cursor.isoformat(), window_end.isoformat()))
        cursor = next_window_start
    return windows


def _harvest_chunk_dedupe_key(
    *,
    harvest_id: str,
    source: str,
    category: str,
    start_date: str,
    end_date: str,
    cursor: int,
) -> str:
    return f"harvest_chunk:{harvest_id}:{source}:{category}:{start_date}:{end_date}:{cursor}"


def _retry_delay_for(attempts: int):
    """Exponential backoff with full jitter, capped at ~1 hour.

    ``attempts`` is the number of attempts already used (including the one
    that just failed). The first retry waits ~60s, the second ~120s, etc.,
    doubling up to a 3600s ceiling. Jitter spreads a flood of retries so a
    backend blip doesn't produce a thundering herd when the backoff window
    expires.
    """
    import random
    from datetime import timedelta
    base = min(60 * (2 ** max(0, attempts - 1)), 3600)
    jitter = random.uniform(0.5, 1.5)
    return utcnow() + timedelta(seconds=base * jitter)


_MODEL_CACHE: dict[str, Any] = {}
_RERANKER_CACHE: dict[str, Any] = {}
_ADMIN_LATEST_SYNC_JOB_ID = "env:latest_sync"
_ADMIN_STALLED_AFTER = timedelta(minutes=10)
_ADMIN_TRACKED_PAPER_JOB_TYPES = frozenset(
    {
        "enrich_missing_metadata",
        "harvest_author",
        "harvest_chunk",
        "index_paper",
        "ingest_repo",
        "reindex_embeddings",
        "reindex_missing_embeddings",
        "sync_latest",
        "harvest_range",
        "harvest_top_cited",
        "harvest_venue",
    }
)


def _scheduler_latest_sync_configured(settings: Settings) -> bool:
    return bool((settings.scheduler_service_account_email or "").strip() and (settings.scheduler_audience or "").strip())

_RECENCY_SIGNAL_RE = re.compile(
    r"\b(recent(?:ly)?|latest|newest|new|this\s+year|202[4-9]|203[0-9])\b",
    re.IGNORECASE,
)

_GROUNDING_INTENT_RE = re.compile(
    r"\b("
    r"paper|papers|research|study|studies|citation|citations|cite|source|sources|reference|references|"
    r"figure|figures|table|tables|benchmark|benchmarks|result|results|method|methods|experiment|experiments|"
    r"ablation|dataset|datasets|arxiv|appendix|section|sections|survey|summarize|summary|compare|comparison|"
    r"uploaded|upload|document|documents|docs|notes?|link|links|url|urls|pdf|doi"
    r")\b",
    re.IGNORECASE,
)

_FORCE_TOOL_QUERY_RE = re.compile(
    r"\b("
    r"cite|citations|source|sources|reference|references|according to|find|search|compare|comparison|"
    r"summarize|summary|survey|method|methods|result|results|figure|figures|table|tables|benchmark|benchmarks|"
    r"uploaded|document|documents|notes?|link|links|url|urls|pdf|doi"
    r")\b",
    re.IGNORECASE,
)
_RECOMMENDED_READING_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?")


def _admin_display_status(status: str) -> str:
    if status in {"dead", "failed"}:
        return "error"
    return status


def _admin_status_sort_key(status: str) -> int:
    order = {
        "stalled": 0,
        "running": 1,
        "pending": 2,
        "error": 3,
        "completed": 4,
        "scheduled": 5,
    }
    return order.get(status, 9)


def _job_activity_stalled(updated_at, *, now) -> bool:
    return bool(updated_at and now - updated_at > _ADMIN_STALLED_AFTER)


def _job_type_tracks_papers(job_type: str) -> bool:
    return job_type in _ADMIN_TRACKED_PAPER_JOB_TYPES


def _job_public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    public_payload = dict(payload or {})
    public_payload.pop("__debug", None)
    return public_payload


def _admin_child_row(job: JobResponse) -> AdminJobChildRow:
    return AdminJobChildRow(
        job_id=job.job_id,
        root_job_id=job.root_job_id or job.job_id,
        job_type=job.job_type,
        status=job.status,
        display_status=_admin_display_status(job.status),
        payload=_job_public_payload(job.payload),
        error=job.error,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        priority=job.priority,
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _job_debug_payload(payload: dict[str, Any]) -> dict[str, Any]:
    debug = (payload or {}).get("__debug")
    return debug if isinstance(debug, dict) else {}


def _job_paper_outcome_rank(value: str) -> int:
    return {"indexed": 0, "updated": 0, "failed": 1, "skipped": 2}.get(value, 9)


def _group_job_paper_records(records: list[JobPaperRecord]) -> dict[str, JobPaperRecord]:
    grouped: dict[str, JobPaperRecord] = {}
    for record in records:
        existing = grouped.get(record.paper_id)
        if existing is None:
            grouped[record.paper_id] = record
            continue
        if _job_paper_outcome_rank(record.outcome) < _job_paper_outcome_rank(existing.outcome) or (
            _job_paper_outcome_rank(record.outcome) == _job_paper_outcome_rank(existing.outcome)
            and record.updated_at > existing.updated_at
        ):
            grouped[record.paper_id] = record

    return grouped


def _job_paper_outcome_counts(records: list[JobPaperRecord]) -> dict[str, int]:
    grouped = _group_job_paper_records(records)
    counts: dict[str, int] = {}
    for record in grouped.values():
        counts[record.outcome] = counts.get(record.outcome, 0) + 1
    return counts


def _job_paper_skipped_reason_counts(records: list[JobPaperRecord]) -> dict[str, int]:
    grouped = _group_job_paper_records(records)
    counts: dict[str, int] = {}
    for record in grouped.values():
        if record.outcome != "skipped":
            continue
        reason = (record.skip_reason or "unknown").strip() or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _latest_status_summary(status_counts: dict[str, int], *, total: int) -> str:
    parts: list[str] = []
    for key in ("running", "pending", "completed", "dead", "failed"):
        count = status_counts.get(key, 0)
        if count > 0:
            parts.append(f"{count} {key}")
    if not parts:
        return "No recorded activity yet."
    return f"{' · '.join(parts)} · {total} total"

_CHAT_TITLE_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*")
_CHAT_TITLE_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "can",
    "could",
    "for",
    "from",
    "help",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "please",
    "show",
    "tell",
    "that",
    "the",
    "this",
    "to",
    "us",
    "we",
    "with",
    "would",
}

_PROJECT_CODE_DIRECT_TOOL_RE = re.compile(r"\b(project_(?:list_files|read_file|grep|touch|edit|delete|run))\b", re.IGNORECASE)
_PROJECT_CODE_ACTION_RE = re.compile(
    r"\b("
    r"write|save|create|make|add|implement|edit|modify|update|refactor|fix|rename|delete|remove|read|show|open|grep|search|run"
    r")\b",
    re.IGNORECASE,
)
_PROJECT_CODE_WRITE_ACTION_RE = re.compile(
    r"\b("
    r"write|save|create|make|add|implement|edit|modify|update|refactor|fix|rename|delete|remove"
    r")\b",
    re.IGNORECASE,
)
_PROJECT_CODE_TARGET_RE = re.compile(
    r"\b("
    r"code|python|pytorch|torch|script|module|file|files|function|class|train(?:ing)?|eval(?:uation)?|workflow|repo|project"
    r")\b",
    re.IGNORECASE,
)
_PROJECT_CODE_PATH_HINT_RE = re.compile(r"(?:^|[\s`'\"])([A-Za-z0-9_./-]+\.py)(?:$|[\s`'\",.:;!?])")
_PROJECT_CODE_WRITE_TOOL_RE = re.compile(r"\b(project_(?:touch|edit|delete))\b", re.IGNORECASE)
_PROJECT_CODE_SNIPPET_RE = re.compile(
    r"\b(snippet|example|sample|template|starter|boilerplate)\b",
    re.IGNORECASE,
)
_PROJECT_CODE_BUILD_OBJECT_RE = re.compile(
    r"\b("
    r"sgd|adam|optimizer|optimiser|scheduler|model|network|layer|loss|dataset|dataloader|loader|"
    r"pipeline|trainer|baseline|agent|parser|client|server|api|cli|demo|prototype"
    r")\b",
    re.IGNORECASE,
)
_PROJECT_CODE_CONFIRMATION_RE = re.compile(
    r"^\s*(?:"
    r"yes|yeah|yep|yup|sure|ok(?:ay)?|please|please do|go ahead|do it|do that|"
    r"save it|save that|write it|write that|create it|create that|make it|make that|"
    r"edit it|edit that|update it|update that|run it|run that"
    r")\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_PROJECT_CODE_RETRY_RE = re.compile(
    r"^\s*(?:try again|retry|please retry|again|one more time|try it again|retry that)\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_PROJECT_CODE_FOLLOWUP_PROMPT_RE = re.compile(
    r"\b(want me to|should i|would you like me to|i can|if you want|if you'd like)\b",
    re.IGNORECASE,
)
_PROJECT_CODE_FAILURE_FOLLOWUP_RE = re.compile(
    r"\b(wasn't able to persist code|no project file was changed|tell me the exact file|please retry)\b",
    re.IGNORECASE,
)
_PYTHON_FENCE_RE = re.compile(r"(?ms)(```|~~~)\s*(?:python|py)\s*\n.+?\n\1")

_INLINE_SOURCE_ID_RE = re.compile(r"\[(S\d+)\]")
_PYTHON_FILENAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_GITHUB_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_MAX_REPO_IMPORT_BYTES = 1_000_000


def _query_signals_recency(query: str) -> bool:
    return bool(_RECENCY_SIGNAL_RE.search(query or ""))


_APPENDIX_TITLE_RE = re.compile(r"\b(appendix|supplement|acknowledg|references?|bibliography)\b", re.IGNORECASE)


def _section_depth_for(section: Any) -> int:
    """Infer section depth heuristically.

    Appendices / references / acknowledgements → 3 so they rank below main
    body. Sections introduced via an explicit depth attribute would override
    this when the extractor starts supplying one.
    """
    title = getattr(section, "title", "") or ""
    if _APPENDIX_TITLE_RE.search(title):
        return 3
    return 1


def _normalize_python_filename(raw_filename: str) -> str:
    value = (raw_filename or "").strip()
    if not value:
        raise HTTPException(status_code=422, detail="Python filename is required")
    if "/" in value or "\\" in value or value.startswith(".") or " " in value:
        raise HTTPException(status_code=422, detail="Use a valid Python filename")
    if value.endswith(".py"):
        stem = value[:-3]
    elif "." in value:
        raise HTTPException(status_code=422, detail="Only .py filenames are supported")
    else:
        stem = value
    if not _PYTHON_FILENAME_RE.fullmatch(stem):
        raise HTTPException(status_code=422, detail="Use a valid Python filename")
    return f"{stem}.py"


def _github_scope_allows_repo_write(scope: str) -> bool:
    values = {part.strip() for part in (scope or "").split(",") if part.strip()}
    return "repo" in values or "public_repo" in values


def _github_scope_values(scope: str) -> list[str]:
    return sorted({part.strip() for part in (scope or "").split(",") if part.strip()})


def _validate_repo_name(value: str) -> str:
    repo_name = (value or "").strip()
    if not repo_name or "/" in repo_name or "\\" in repo_name or repo_name in {".", ".."}:
        raise HTTPException(status_code=422, detail="Use a valid GitHub repository name")
    if not _GITHUB_REPO_NAME_RE.fullmatch(repo_name):
        raise HTTPException(status_code=422, detail="Use a valid GitHub repository name")
    return repo_name


def _normalize_repo_path(value: str) -> str:
    path = (value or "").strip()
    if not path or "\\" in path:
        raise HTTPException(status_code=422, detail="Use a valid repository path")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or any(part in {"", ".", "..", ".git"} for part in parsed.parts):
        raise HTTPException(status_code=422, detail="Use a valid repository path")
    return parsed.as_posix()


def _normalize_project_code_repo_path(value: str) -> str:
    path = _normalize_repo_path(value)
    if not path.lower().endswith(".py"):
        raise HTTPException(status_code=422, detail="Use a valid Python repository path")
    return path


def _normalize_project_workspace_repo_path(value: str) -> str:
    path = _normalize_repo_path(value)
    if path == ".paperbase" or path.startswith(".paperbase/"):
        raise HTTPException(status_code=422, detail="The .paperbase manifest path is reserved")
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in _PROJECT_WORKSPACE_BINARY_EXTENSIONS:
        raise HTTPException(status_code=422, detail="Only text workspace files are supported")
    return path


def _normalize_paper_artifact_path(value: str) -> str:
    path = (value or "").strip()
    if not path or "\\" in path:
        raise HTTPException(status_code=422, detail="Use a valid paper artifact path")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise HTTPException(status_code=422, detail="Use a valid paper artifact path")
    return parsed.as_posix()


def _is_textual_content_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    normalized = content_type.split(";", 1)[0].strip().lower()
    return normalized.startswith(_TEXTUAL_MIME_PREFIXES) or normalized in _TEXTUAL_MIME_TYPES


def _file_kind_for_repo_path(path: str) -> str:
    lower_path = path.lower()
    if lower_path.endswith(".py"):
        return "python"
    if lower_path.endswith(".ipynb"):
        return "notebook"
    raise HTTPException(status_code=422, detail=f"Repository path '{path}' is not importable")


def _workspace_file_kind_for_repo_path(path: str) -> str:
    lower_path = path.lower()
    if lower_path.endswith(".py"):
        return "python"
    if lower_path.endswith(".ipynb"):
        return "notebook"
    return "text"


@lru_cache(maxsize=None)
def _load_prompt_asset(relative_path: str) -> str:
    target = (_PROMPT_ASSET_ROOT / relative_path).resolve()
    if _PROMPT_ASSET_ROOT not in target.parents:
        raise RuntimeError(f"Prompt asset path escapes asset root: {relative_path}")
    return target.read_text(encoding="utf-8").strip()


def _notebook_to_python_source(raw_notebook: str) -> str:
    try:
        import jupytext

        notebook = jupytext.reads(raw_notebook, fmt="ipynb")
        return jupytext.writes(notebook, fmt="py:percent")
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="Jupytext is not installed. Run setup again.") from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Notebook could not be converted to Python") from exc


def _python_source_to_notebook(raw_python: str) -> str:
    try:
        import jupytext

        notebook = jupytext.reads(raw_python, fmt="py:percent")
        return jupytext.writes(notebook, fmt="ipynb")
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="Jupytext is not installed. Run setup again.") from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Notebook could not be converted from Python") from exc


def _notebook_to_python_source_or_raw(raw_notebook: str) -> str:
    if not raw_notebook:
        return ""
    try:
        return _notebook_to_python_source(raw_notebook)
    except HTTPException:
        return raw_notebook


def _managed_paths_from_manifest_content(content: str) -> set[str]:
    try:
        payload = json.loads(content)
    except Exception:
        return set()
    paths = payload.get("managed_paths") if isinstance(payload, dict) else None
    if not isinstance(paths, list):
        return set()
    normalized = set()
    for path in paths:
        if isinstance(path, str):
            try:
                normalized.add(_normalize_repo_path(path))
            except HTTPException:
                continue
    return normalized


def _stride_chunks(text: str, *, max_chars: int, overlap: int) -> list[str]:
    """Split text into overlapping windows.

    Returns the text unchanged when it already fits, so short sections don't
    turn into two-copy chunks. Boundary tries to snap to paragraph breaks to
    avoid cutting mid-sentence when possible.
    """
    if not text:
        return [""]
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]
    if overlap < 0:
        overlap = 0
    if overlap >= max_chars:
        overlap = max_chars // 4
    step = max(1, max_chars - overlap)
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            # Prefer a paragraph break within the last 20% of the window.
            window_start = max(start + int(max_chars * 0.8), start)
            break_at = text.rfind("\n\n", window_start, end)
            if break_at == -1:
                break_at = text.rfind(". ", window_start, end)
                if break_at != -1:
                    break_at += 1  # include the period
            if break_at != -1 and break_at > start + overlap:
                end = break_at
        chunks.append(text[start:end].strip())
        if end >= n:
            break
        start = max(end - overlap, start + step)
    return [chunk for chunk in chunks if chunk]


def _table_to_indexable_text(table: Any) -> str:
    """Render a TableArtifact as Markdown-table text so its cell values are
    visible to lexical + vector retrieval. Caps rows so one giant table can't
    dominate the chunk pool.
    """
    caption = (getattr(table, "caption", None) or "").strip()
    rows = list(getattr(table, "rows", []) or [])
    parts: list[str] = []
    if caption:
        parts.append(f"Caption: {caption}")
    if rows:
        # Keep the header + first 40 rows; 40 × ~80 chars ≈ 3 KB.
        header, *body = rows
        parts.append("| " + " | ".join(str(c) for c in header) + " |")
        parts.append("|" + "|".join(["---"] * len(header)) + "|")
        for row in body[:40]:
            cells = [str(c) for c in row]
            if len(cells) < len(header):
                cells += [""] * (len(header) - len(cells))
            parts.append("| " + " | ".join(cells[: len(header)]) + " |")
        if len(body) > 40:
            parts.append(f"(+{len(body) - 40} more rows)")
    return "\n".join(parts).strip()


class _TransformersCLSEmbedder:
    """Wrap a raw HuggingFace AutoModel with CLS pooling + L2 normalization.

    Used for SPECTER2-family models that don't ship a sentence-transformers
    ``modules.json`` (they're published as adapter-format checkpoints). The
    SPECTER2 authors document the intended usage as:
        1. tokenize ``title + SEP + abstract`` up to 512 tokens,
        2. run through the base encoder,
        3. take the ``[CLS]`` token,
        4. L2-normalize.
    That is exactly what this class does.
    """

    def __init__(self, model_name: str) -> None:
        from transformers import AutoModel, AutoTokenizer
        import torch

        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(model_name)
        self._model = AutoModel.from_pretrained(model_name).eval()
        # Pick the best available device once, cache it on self; keeps encode
        # batches off the default CPU path when we have MPS or CUDA.
        if torch.cuda.is_available():
            self._device = "cuda"
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            self._device = "mps"
        else:
            self._device = "cpu"
        self._model.to(self._device)

    def encode(
        self,
        texts: list[str],
        *,
        normalize_embeddings: bool = True,
        convert_to_numpy: bool = True,
        show_progress_bar: bool = False,
    ):
        """Single-sequence tokenization path. Used for chunks (section text,
        figure captions, etc.) where there is no natural ``(title, body)``
        pair to feed."""
        return self._run(
            [(text, None) for text in texts],
            normalize_embeddings=normalize_embeddings,
            convert_to_numpy=convert_to_numpy,
        )

    def encode_pairs(
        self,
        pairs: list[tuple[str, str | None]],
        *,
        normalize_embeddings: bool = True,
        convert_to_numpy: bool = True,
    ):
        """Pair-tokenization path. Each ``(title, body)`` pair is fed to the
        tokenizer with ``text=title`` and ``text_pair=body``, which produces
        the ``title [SEP] body`` input shape SPECTER2 was trained on.

        ``body`` may be None / empty — in that case the call degrades
        gracefully to the single-sequence path for that one example.
        """
        return self._run(pairs, normalize_embeddings=normalize_embeddings, convert_to_numpy=convert_to_numpy)

    def _run(
        self,
        pairs: list[tuple[str, str | None]],
        *,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
    ):
        torch = self._torch
        # The HF tokenizer accepts parallel lists for batched pair inputs;
        # None entries must be filtered out of text_pair so we don't force
        # an empty SEP-suffix on chunks that have no natural pair.
        texts = [pair[0] for pair in pairs]
        text_pairs = [pair[1] for pair in pairs]
        # If no example has a pair, skip the text_pair argument entirely so
        # the tokenizer uses its single-sequence fast path.
        if any(pair is not None and pair != "" for pair in text_pairs):
            normalized_pairs = [pair if pair else "" for pair in text_pairs]
            inputs = self._tokenizer(
                text=texts,
                text_pair=normalized_pairs,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
        else:
            inputs = self._tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
        inputs = inputs.to(self._device)
        with torch.inference_mode():
            hidden = self._model(**inputs).last_hidden_state
        pooled = hidden[:, 0, :]
        if normalize_embeddings:
            pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
        if convert_to_numpy:
            return pooled.detach().cpu().numpy()
        return pooled


# HuggingFace model names that do not ship a sentence-transformers bundle and
# must be loaded via raw transformers + CLS pooling. Match on substring so that
# both ``allenai/specter2_base`` and future Allen AI SciRepEval variants
# (``scirepeval_*``) pick up the same path.
_TRANSFORMERS_CLS_MODEL_TOKENS: tuple[str, ...] = (
    "specter2",
    "scirepeval",
)


def _needs_raw_transformers_path(model_name: str) -> bool:
    lowered = model_name.lower()
    return any(token in lowered for token in _TRANSFORMERS_CLS_MODEL_TOKENS)


# Model-aware query prefixes. Retrieval models often expect a model-specific
# string prepended to the query (but not the document) at encode time — their
# training recipe pairs the prefixed query against an un-prefixed passage.
# Omitting the prefix leaves ~5–15 points of top-1 on the table for those
# models. SPECTER2 and generic sentence-transformers models have no prefix.
_QUERY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("BAAI/bge-", "Represent this sentence for searching relevant passages: "),
    ("intfloat/e5-", "query: "),
    ("intfloat/multilingual-e5-", "query: "),
)


def _query_prefix_for_model(model_name: str | None) -> str:
    if not model_name:
        return ""
    for prefix, query_prefix in _QUERY_PREFIXES:
        if model_name.startswith(prefix):
            return query_prefix
    return ""


class OptionalEmbeddingProvider:
    # Module-level flags so we log each distinct disabled-state exactly once,
    # rather than once per query.
    _logged_unconfigured: bool = False
    _logged_import_failure: bool = False
    _logged_load_failure: bool = False

    def __init__(self, settings: Settings) -> None:
        self._model_name = settings.embedding_model
        self._expected_dim = settings.embedding_dim
        self._query_prefix = _query_prefix_for_model(self._model_name)

    def _model(self):
        if not self._model_name:
            if not OptionalEmbeddingProvider._logged_unconfigured:
                logger.info(
                    "Embeddings disabled: no PAPERBASE_EMBEDDING_MODEL set",
                    extra={"event": "embed.unconfigured"},
                )
                OptionalEmbeddingProvider._logged_unconfigured = True
            return None
        cached = _MODEL_CACHE.get(self._model_name)
        if cached is not None:
            return cached

        # SPECTER2 and the SciRepEval family are published as adapter models
        # (no sentence-transformers modules.json); load them via raw
        # transformers with CLS pooling. Everything else goes through the
        # sentence-transformers auto-loader, which handles its own pooling.
        if _needs_raw_transformers_path(self._model_name):
            try:
                model = _TransformersCLSEmbedder(self._model_name)
            except ImportError as exc:
                if not OptionalEmbeddingProvider._logged_import_failure:
                    logger.warning(
                        "Embeddings disabled: transformers import failed",
                        extra={"event": "embed.import_failed", "error": str(exc)},
                    )
                    OptionalEmbeddingProvider._logged_import_failure = True
                return None
            except Exception as exc:
                if not OptionalEmbeddingProvider._logged_load_failure:
                    logger.warning(
                        "Embeddings disabled: model failed to load (transformers path)",
                        extra={
                            "event": "embed.load_failed",
                            "model": self._model_name,
                            "error": str(exc),
                        },
                    )
                    OptionalEmbeddingProvider._logged_load_failure = True
                return None
            _MODEL_CACHE[self._model_name] = model
            return model

        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:
            if not OptionalEmbeddingProvider._logged_import_failure:
                logger.warning(
                    "Embeddings disabled: sentence-transformers import failed",
                    extra={"event": "embed.import_failed", "error": str(exc)},
                )
                OptionalEmbeddingProvider._logged_import_failure = True
            return None
        try:
            model = SentenceTransformer(self._model_name)
        except Exception as exc:
            if not OptionalEmbeddingProvider._logged_load_failure:
                logger.warning(
                    "Embeddings disabled: model failed to load",
                    extra={
                        "event": "embed.load_failed",
                        "model": self._model_name,
                        "error": str(exc),
                    },
                )
                OptionalEmbeddingProvider._logged_load_failure = True
            return None
        _MODEL_CACHE[self._model_name] = model
        return model

    def encode(self, text: str) -> list[float] | None:
        # encode() is the query-side entry point (search_local passes the
        # user's query here). Model-aware prefix is applied here, not in
        # encode_batch, so document-side callers (encode_paper_batch) keep
        # their un-prefixed input shape intact.
        batch = self.encode_batch([self._query_prefix + text])
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
            vectors = model.encode(
                texts,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            # This one we log every time — query-level failures are actionable,
            # and we don't want to hide a flapping model behind a once-flag.
            logger.warning("Embedding encode failed", extra={"event": "embed.encode_failed", "error": str(exc)})
            return None
        return [[float(v) for v in vector.tolist()] for vector in vectors]

    def encode_paper(self, title: str, body: str) -> list[float] | None:
        """Paper-level embedding with backend-correct input shape.

        SPECTER2-family models expect ``title + [SEP] + abstract`` as a pair
        input to the tokenizer; concatenated strings land in a subtly
        different embedding space. For sentence-transformers-compatible
        models we just join with a newline, which is their trained input.
        """
        batch = self.encode_paper_batch([(title, body)])
        if batch is None or not batch:
            return None
        return batch[0]

    def encode_paper_batch(self, pairs: list[tuple[str, str]]) -> list[list[float]] | None:
        if not pairs:
            return []
        model = self._model()
        if model is None:
            return None
        try:
            if self._model_name and _needs_raw_transformers_path(self._model_name):
                # Route through the pair tokenizer path so SPECTER2 sees the
                # title [SEP] body shape it was trained on.
                vectors = model.encode_pairs(
                    [(title or "", body or "") for title, body in pairs],
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                )
            else:
                # sentence-transformers models take a single string; match
                # what BGE / MPNet / all-MiniLM were trained on.
                joined = ["\n\n".join(part for part in (title, body) if part) for title, body in pairs]
                vectors = model.encode(
                    joined,
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )
        except Exception as exc:
            logger.warning(
                "Paper embedding encode failed",
                extra={"event": "embed.encode_failed", "error": str(exc)},
            )
            return None
        return [[float(v) for v in (vector.tolist() if hasattr(vector, "tolist") else vector)] for vector in vectors]


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


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _normalize_tags(tags: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        cleaned = slugify(tag)
        if cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)
    return normalized


def _split_sentences(text: str) -> list[str]:
    collapsed = " ".join(text.split()).strip()
    if not collapsed:
        return []
    parts = [segment.strip() for segment in collapsed.replace("\n", " ").split(". ") if segment.strip()]
    return parts


def _best_excerpt(text: str, question: str, *, max_sentences: int = 2, max_chars: int = 420) -> str:
    question_words = {word for word in slugify(question).split("-") if word}
    candidates: list[tuple[int, int, str]] = []
    for index, sentence in enumerate(_split_sentences(text) or [text.strip()]):
        sentence_words = {word for word in slugify(sentence).split("-") if word}
        overlap = len(question_words & sentence_words)
        candidates.append((overlap * 10 + max(0, 3 - index), index, sentence.strip()))
    chosen = sorted(candidates, key=lambda item: (item[0], -item[1]), reverse=True)[:max_sentences]
    excerpt = ". ".join(sentence for _, _, sentence in sorted(chosen, key=lambda item: item[1])).strip()
    return excerpt[:max_chars].rstrip()


def _query_variants(question: str) -> list[str]:
    words = [word for word in slugify(question).split("-") if len(word) > 3]
    variants = [question.strip()]
    if words:
        variants.append(" ".join(words[:4]))
        variants.extend(words[:4])
    deduped: list[str] = []
    seen: set[str] = set()
    for value in variants:
        normalized = value.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(value.strip())
    return deduped[:5]


def _format_chat_title_word(word: str) -> str:
    if not word:
        return word
    if word.isupper():
        return word
    if any(char.isupper() for char in word[1:]):
        return word
    return word.capitalize()


def _summarize_chat_title(content: str) -> str:
    tokens = _CHAT_TITLE_TOKEN_RE.findall(content)
    if not tokens:
        return "New chat"
    informative_tokens = [token for token in tokens if token.lower() not in _CHAT_TITLE_STOP_WORDS]
    source_tokens = informative_tokens if len(informative_tokens) >= 3 else tokens
    desired_count = min(5, max(4, len(source_tokens)))
    selected_tokens = source_tokens[:desired_count]
    if len(selected_tokens) < 4 and len(tokens) >= 4:
        selected_tokens = tokens[:4]
    return " ".join(_format_chat_title_word(token) for token in selected_tokens).strip() or "New chat"


def _clean_generated_chat_title(content: str) -> str:
    line = next((candidate.strip() for candidate in content.splitlines() if candidate.strip()), "")
    if not line:
        return ""
    line = re.sub(r"^[-*#\s]+", "", line).strip()
    line = re.sub(r"^(?:title|headline)\s*:\s*", "", line, flags=re.IGNORECASE).strip()
    line = line.strip("\"'`. ")
    tokens = _CHAT_TITLE_TOKEN_RE.findall(line)
    if len(tokens) < 4:
        return ""
    selected_tokens = tokens[:5]
    return " ".join(_format_chat_title_word(token) for token in selected_tokens).strip()


def _question_mentions_uploads(question: str) -> bool:
    lowered = question.lower()
    return any(token in lowered for token in ("upload", "uploaded", "document", "documents", "docs", "notes", "note"))


def _question_has_project_code_intent(question: str) -> bool:
    if not question.strip():
        return False
    if _PROJECT_CODE_DIRECT_TOOL_RE.search(question):
        return True
    if _PROJECT_CODE_PATH_HINT_RE.search(question) and (
        _PROJECT_CODE_ACTION_RE.search(question) or any(token in question.lower() for token in ("read", "edit", "run"))
    ):
        return True
    if _PROJECT_CODE_SNIPPET_RE.search(question) and (
        _PROJECT_CODE_TARGET_RE.search(question) or _PROJECT_CODE_BUILD_OBJECT_RE.search(question)
    ):
        return True
    if _PROJECT_CODE_ACTION_RE.search(question) and (
        _PROJECT_CODE_SNIPPET_RE.search(question) or _PROJECT_CODE_BUILD_OBJECT_RE.search(question)
    ):
        return True
    return bool(_PROJECT_CODE_ACTION_RE.search(question) and _PROJECT_CODE_TARGET_RE.search(question))


def _question_is_project_code_confirmation(question: str) -> bool:
    return bool(_PROJECT_CODE_CONFIRMATION_RE.match(question.strip()))


def _question_is_project_code_retry(question: str) -> bool:
    return bool(_PROJECT_CODE_RETRY_RE.match(question.strip()))


def _last_pending_project_code_followup(thread: ChatThreadResponse | None) -> ChatMessageResponse | None:
    if thread is None or not thread.messages:
        return None
    assistant_index: int | None = None
    for index in range(len(thread.messages) - 1, -1, -1):
        message = thread.messages[index]
        if message.kind == "compaction" or not message.content_markdown.strip():
            continue
        if message.role == "assistant":
            assistant_index = index
            break
        if message.role == "user":
            return None
    if assistant_index is None:
        return None
    assistant_message = thread.messages[assistant_index]
    prior_user_message = next(
        (
            message
            for message in reversed(thread.messages[:assistant_index])
            if message.role == "user" and message.kind != "compaction" and message.content_markdown.strip()
        ),
        None,
    )
    if prior_user_message is None or not _question_has_project_code_intent(prior_user_message.content_markdown):
        return None
    assistant_text = assistant_message.content_markdown.strip()
    lowered = assistant_text.lower()
    if _PROJECT_CODE_FAILURE_FOLLOWUP_RE.search(assistant_text):
        return assistant_message
    if not (_PROJECT_CODE_FOLLOWUP_PROMPT_RE.search(assistant_text) or assistant_text.endswith("?")):
        return None
    if _PROJECT_CODE_ACTION_RE.search(assistant_text) and (
        _PROJECT_CODE_TARGET_RE.search(assistant_text)
        or _PROJECT_CODE_PATH_HINT_RE.search(assistant_text)
        or "project" in lowered
    ):
        return assistant_message
    if _PROJECT_CODE_PATH_HINT_RE.search(assistant_text) and "project" in lowered:
        return assistant_message
    return None


def _question_confirms_pending_project_code_followup(question: str, *, thread: ChatThreadResponse | None) -> bool:
    return (
        (_question_is_project_code_confirmation(question) or _question_is_project_code_retry(question))
        and _last_pending_project_code_followup(thread) is not None
    )


def _pending_project_code_followup_requires_write(thread: ChatThreadResponse | None) -> bool:
    assistant_message = _last_pending_project_code_followup(thread)
    if assistant_message is None:
        return False
    assistant_text = assistant_message.content_markdown.strip()
    return bool(_PROJECT_CODE_WRITE_ACTION_RE.search(assistant_text) or "into your project" in assistant_text.lower())


def _should_offer_research_chat_tools(question: str, request: SendChatMessageRequest) -> bool:
    return True


def _should_offer_project_code_tools(question: str, *, thread: ChatThreadResponse | None) -> bool:
    return bool(thread and thread.project_id)


def _should_require_project_code_write(question: str, *, thread: ChatThreadResponse | None) -> bool:
    if not _should_offer_project_code_tools(question, thread=thread):
        return False
    if _question_confirms_pending_project_code_followup(question, thread=thread):
        return _pending_project_code_followup_requires_write(thread)
    if _PROJECT_CODE_WRITE_TOOL_RE.search(question):
        return True
    if _PROJECT_CODE_SNIPPET_RE.search(question) and (
        _PROJECT_CODE_TARGET_RE.search(question) or _PROJECT_CODE_BUILD_OBJECT_RE.search(question)
    ):
        return True
    if _PROJECT_CODE_WRITE_ACTION_RE.search(question) and _PROJECT_CODE_BUILD_OBJECT_RE.search(question):
        return True
    return bool(_PROJECT_CODE_WRITE_ACTION_RE.search(question) and _PROJECT_CODE_TARGET_RE.search(question))


def _project_code_tool_use_completed_write(tool_use: dict[str, Any], *, is_error: bool) -> bool:
    if is_error:
        return False
    tool_name = str(tool_use.get("name") or "").strip().lower()
    if tool_name == "project_touch":
        tool_input = tool_use.get("input")
        if not isinstance(tool_input, dict):
            return False
        return bool(str(tool_input.get("content") or "").strip())
    return tool_name in {"project_edit", "project_delete"}


def _project_code_tool_use_completed_read(tool_use: dict[str, Any], *, is_error: bool) -> bool:
    if is_error:
        return False
    tool_name = str(tool_use.get("name") or "").strip().lower()
    return tool_name == "project_read_file"


def _answer_contains_python_snippet(answer: str) -> bool:
    if not answer.strip():
        return False
    return bool(_PYTHON_FENCE_RE.search(answer))


def _project_code_write_retry_prompt() -> str:
    return (
        "No managed project Python file has been updated yet. "
        "This turn still requires successful project_* tool calls before any user-facing narrative text. "
        "If the target file already exists, update it with project_edit instead of claiming success. "
        "If the target file exists and is empty, you may populate it with project_edit using old_string=\"\"."
    )


def _project_code_write_failure_answer() -> str:
    return (
        "I wasn't able to persist code into a managed project Python file in this turn, so no project file was changed. "
        "Please retry, or tell me the exact file you want updated."
    )


def _project_code_answer_requires_persisted_python(
    answer: str,
    *,
    thread: ChatThreadResponse | None,
    project_write_completed: bool,
    project_read_completed: bool,
) -> bool:
    if thread is None or not thread.project_id:
        return False
    if not _answer_contains_python_snippet(answer):
        return False
    return not (project_write_completed or project_read_completed)


def _should_offer_chat_tools(question: str, request: SendChatMessageRequest, *, thread: ChatThreadResponse | None) -> bool:
    return _should_offer_research_chat_tools(question, request) or _should_offer_project_code_tools(question, thread=thread)


def _should_force_chat_tool_use(question: str, request: SendChatMessageRequest, *, thread: ChatThreadResponse | None) -> bool:
    return _should_offer_chat_tools(question, request, thread=thread)


def _sanitize_inline_citations(answer: str, citations: list[ChatCitation]) -> str:
    if not answer:
        return answer

    allowed_source_ids = {citation.source_id for citation in citations}

    def _replace(match: re.Match[str]) -> str:
        source_id = match.group(1)
        return match.group(0) if source_id in allowed_source_ids else ""

    sanitized = _INLINE_SOURCE_ID_RE.sub(_replace, answer)
    return sanitized.strip()


def _tool_int(value: Any, *, default: int, minimum: int = 1, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _tool_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _truncate_tool_text(text: str, *, limit: int) -> tuple[str, bool]:
    normalized = text or ""
    if len(normalized) <= limit:
        return normalized, False
    clipped = normalized[:limit].rstrip()
    return f"{clipped}\n\n[truncated]", True


def _merge_chat_usage(previous: ChatModelUsage | None, current: ChatModelUsage | None) -> ChatModelUsage | None:
    if previous is None:
        return current
    if current is None:
        return previous
    return ChatModelUsage(
        input_tokens=previous.input_tokens + current.input_tokens,
        output_tokens=previous.output_tokens + current.output_tokens,
        total_tokens=previous.total_tokens + current.total_tokens,
    )


def _extract_message_text(payload: dict[str, Any]) -> str:
    parts = [item.get("text", "") for item in payload.get("content", []) if item.get("type") == "text"]
    return "\n".join(part for part in parts if part).strip()


def _anthropic_context_window_tokens(model_name: str) -> int:
    normalized = model_name.strip().lower().replace(".", "-")
    if any(token in normalized for token in ("opus-4-7", "opus-4-6", "sonnet-4-6")):
        return 1_000_000
    return 200_000


def _openai_context_window_tokens(model_name: str) -> int:
    normalized = model_name.strip().lower().replace(".", "-")
    if normalized.startswith("gpt-5"):
        return 400_000
    if normalized.startswith("gpt-4.1") or normalized.startswith("gpt-4o"):
        return 128_000
    return 128_000


_CHAT_TOOL_MAX_FILE_CHARS = 12_000
_CHAT_TOOL_MAX_TABLE_ROWS = 40
_CHAT_TOOL_MAX_FILE_LIST = 200
_CHAT_COMPACTION_THRESHOLD_RATIO = 0.9
_CHAT_COMPACTION_MAX_PER_TURN = 2
_CHAT_COMPACTION_PLACEHOLDER = "Compacting conversation..."
_CHAT_TOOL_DIRECT_EXPOSURE_THRESHOLD = 100
_CHAT_TURN_DIFF_MAX_CHARS = 12_000
_PROJECT_EXEC_DEFAULT_WAIT_SECONDS = 5
_PROJECT_EXEC_MAX_WAIT_SECONDS = 60
_PROJECT_EXEC_LOG_CHAR_LIMIT = 12_000


@dataclass(frozen=True)
class ChatSamplingPrompt:
    input: list[dict[str, Any]]
    base_instructions: str
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_choice: dict[str, Any] | None = None
    parallel_tool_calls: bool = False


@dataclass(frozen=True)
class ChatTurnItem:
    kind: str
    role: str
    content: str | list[dict[str, Any]]

    def as_model_input(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}


@dataclass
class ChatTurnDiffTracker:
    original_contents: dict[str, str | None] = field(default_factory=dict)
    current_contents: dict[str, str | None] = field(default_factory=dict)

    def record(self, *, repo_path: str, before: str | None, after: str | None) -> None:
        if repo_path not in self.original_contents:
            self.original_contents[repo_path] = before
        self.current_contents[repo_path] = after

    def changed_files(self) -> list[str]:
        changed = [
            path
            for path, before in self.original_contents.items()
            if self.current_contents.get(path) != before
        ]
        return sorted(changed)

    def unified_diff(self, *, limit_chars: int = _CHAT_TURN_DIFF_MAX_CHARS) -> str:
        rendered: list[str] = []
        for repo_path in self.changed_files():
            before = self.original_contents.get(repo_path)
            after = self.current_contents.get(repo_path)
            before_lines = [] if before is None else before.splitlines()
            after_lines = [] if after is None else after.splitlines()
            diff = "\n".join(
                difflib.unified_diff(
                    before_lines,
                    after_lines,
                    fromfile=f"a/{repo_path}",
                    tofile=f"b/{repo_path}",
                    lineterm="",
                )
            ).strip()
            if diff:
                rendered.append(diff)
        combined = "\n\n".join(rendered).strip()
        clipped, truncated = _truncate_tool_text(combined, limit=limit_chars)
        if truncated and clipped.endswith("[truncated]"):
            return clipped
        return clipped


@dataclass
class ChatTurnState:
    active_goal: str
    inspected_files: set[str] = field(default_factory=set)
    running_exec_session_ids: set[str] = field(default_factory=set)
    diff_tracker: ChatTurnDiffTracker = field(default_factory=ChatTurnDiffTracker)

    def note_inspected(self, *repo_paths: str) -> None:
        for repo_path in repo_paths:
            if repo_path:
                self.inspected_files.add(repo_path)

    def note_exec_session(self, exec_id: str, *, running: bool) -> None:
        if not exec_id:
            return
        if running:
            self.running_exec_session_ids.add(exec_id)
        else:
            self.running_exec_session_ids.discard(exec_id)

    def visible_summary(self) -> str:
        parts = ["Current turn state:"]
        goal = self.active_goal.strip()
        if goal:
            parts.append(f"- Active goal: {goal}")
        inspected = sorted(self.inspected_files)
        if inspected:
            parts.append(f"- Files inspected: {', '.join(inspected)}")
        changed = self.diff_tracker.changed_files()
        if changed:
            parts.append(f"- Files changed: {', '.join(changed)}")
            diff = self.diff_tracker.unified_diff()
            if diff:
                parts.extend(["- Unified diff summary:", "```diff", diff, "```"])
        running_execs = sorted(self.running_exec_session_ids)
        if running_execs:
            parts.append(f"- Running exec sessions: {', '.join(running_execs)}")
        return "\n".join(parts)


@dataclass(frozen=True)
class ChatToolSpecEntry:
    spec: dict[str, Any]
    supports_parallel: bool = False
    deferred: bool = False

    @property
    def name(self) -> str:
        return str(self.spec.get("name") or "").strip()


class ChatToolRouter:
    def __init__(
        self,
        entries: list[ChatToolSpecEntry],
        *,
        direct_exposure_threshold: int = _CHAT_TOOL_DIRECT_EXPOSURE_THRESHOLD,
        aliases: dict[str, str] | None = None,
    ) -> None:
        direct_entries = [entry for entry in entries if not entry.deferred]
        optional_entries = [entry for entry in entries if entry.deferred]
        if len(direct_entries) + len(optional_entries) <= direct_exposure_threshold:
            direct_entries.extend(optional_entries)
            optional_entries = []
        self._direct_entries = direct_entries
        self._deferred_entries = optional_entries
        self._by_name = {entry.name: entry for entry in direct_entries + optional_entries}
        self._aliases = dict(aliases or {})

    def model_visible_specs(self) -> list[dict[str, Any]]:
        return [entry.spec for entry in self._direct_entries]

    def deferred_specs(self) -> list[dict[str, Any]]:
        return [entry.spec for entry in self._deferred_entries]

    def find_spec(self, tool_name: str) -> ChatToolSpecEntry | None:
        direct = self._by_name.get(tool_name)
        if direct is not None:
            return direct
        alias_target = self._aliases.get(tool_name)
        if alias_target is None:
            return None
        return self._by_name.get(alias_target)

    def tool_supports_parallel(self, tool_name: str) -> bool:
        entry = self.find_spec(tool_name)
        return bool(entry and entry.supports_parallel)

    def parallel_tool_calls_enabled(self) -> bool:
        return any(entry.supports_parallel for entry in self._direct_entries)

    def has_tool_prefix(self, prefix: str) -> bool:
        return any(entry.name.startswith(prefix) for entry in self._direct_entries)


@dataclass
class ChatCitationRegistry:
    citations: list[ChatCitation] = field(default_factory=list)
    _by_key: dict[tuple[str, str], ChatCitation] = field(default_factory=dict)
    _next_source_index: int = 1

    def seed(self, *, key: tuple[str, str], citation: ChatCitation) -> ChatCitation:
        existing = self._by_key.get(key)
        if existing is not None:
            return existing
        self._by_key[key] = citation
        self.citations.append(citation)
        match = re.fullmatch(r"S(\d+)", citation.source_id)
        if match is not None:
            self._next_source_index = max(self._next_source_index, int(match.group(1)) + 1)
        return citation

    def register(
        self,
        *,
        key: tuple[str, str],
        source_kind: str,
        title: str,
        path: str,
        excerpt: str,
        paper_id: str | None = None,
        document_id: str | None = None,
        tags: list[str] | None = None,
    ) -> ChatCitation:
        existing = self._by_key.get(key)
        if existing is not None:
            return existing
        citation = ChatCitation(
            source_id=f"S{self._next_source_index}",
            source_kind=source_kind,
            title=title,
            path=path,
            excerpt=excerpt,
            paper_id=paper_id,
            document_id=document_id,
            tags=list(tags or []),
        )
        self._next_source_index += 1
        self._by_key[key] = citation
        self.citations.append(citation)
        return citation


@dataclass
class PreparedChatGeneration:
    thread: ChatThreadResponse
    turn: ChatTurnResponse
    user_item: ChatThreadItemResponse
    resolved_user: BrowserUser
    api_key_value: str
    updated_thread_title: str | None = None


@dataclass
class ChatTurnContext:
    items: list[dict[str, Any]]
    active_item_ids: list[str]
    compactions_used: int = 0

    @property
    def prompt_input(self) -> list[dict[str, Any]]:
        return list(self.items)


@dataclass
class ChatToolRoundsResult:
    context: ChatTurnContext
    citation_registry: ChatCitationRegistry
    require_project_code_write: bool = False
    project_write_completed: bool = False
    project_read_completed: bool = False


@dataclass
class ChatTurnExecutionResult:
    turn: ChatTurnResponse
    final_item: ChatThreadItemResponse | None
    citation_registry: ChatCitationRegistry
    usage: ChatModelUsage | None = None


@dataclass
class ProjectExecSession:
    exec_id: str
    thread_id: str
    project_id: str
    command: list[str]
    workspace_path: str
    started_at: object
    process: subprocess.Popen[str]
    status: str = "running"
    exit_code: int | None = None
    finished_at: object | None = None
    stdout_chunks: list[str] = field(default_factory=list)
    stderr_chunks: list[str] = field(default_factory=list)
    completion_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self, *, log_limit: int = _PROJECT_EXEC_LOG_CHAR_LIMIT) -> dict[str, Any]:
        with self.lock:
            stdout, stdout_truncated = _truncate_tool_text("".join(self.stdout_chunks), limit=log_limit)
            stderr, stderr_truncated = _truncate_tool_text("".join(self.stderr_chunks), limit=log_limit)
            return {
                "exec_id": self.exec_id,
                "thread_id": self.thread_id,
                "project_id": self.project_id,
                "command": self.command,
                "workspace_path": self.workspace_path,
                "status": self.status,
                "exit_code": self.exit_code,
                "started_at": self.started_at.isoformat() if hasattr(self.started_at, "isoformat") else self.started_at,
                "finished_at": self.finished_at.isoformat() if hasattr(self.finished_at, "isoformat") else self.finished_at,
                "stdout": stdout,
                "stderr": stderr,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            }


class PaperbaseService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: PaperRepository,
        object_store: ObjectStore,
        email_sender: EmailSender | None = None,
        chat_model: ChatModelClient | None = None,
        github_repo_manager: GitHubProjectRepoManager | None = None,
        modal_runner: ModalPythonRunner | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.object_store = object_store
        self.embeddings = OptionalEmbeddingProvider(settings)
        self.reranker = OptionalReranker(settings)
        self.email_sender = email_sender or ResendEmailSender(settings)
        self.chat_model = chat_model or OpenAIResponsesChatModelClient(settings)
        self.secret_cipher = SecretCipher(settings)
        self.github_repo_manager = github_repo_manager or GitHubProjectRepoManager()
        self.modal_runner = modal_runner or ModalPythonRunner()
        self._project_exec_sessions: dict[str, ProjectExecSession] = {}
        self._project_exec_index: dict[tuple[str, str], set[str]] = {}
        self._project_exec_lock = threading.Lock()
        self._worker_id = f"{os.environ.get('HOSTNAME') or 'local'}:{os.getpid()}:{new_id('worker')}"

    def _call_index_arxiv_paper(
        self,
        arxiv_id: str,
        *,
        paper: PaperMetadata | None = None,
        force: bool = False,
        tracking_job: JobResponse | None = None,
    ) -> PaperResponse:
        parameters = inspect.signature(self.index_arxiv_paper).parameters
        kwargs: dict[str, Any] = {}
        if paper is not None and "paper" in parameters:
            kwargs["paper"] = paper
        if "force" in parameters:
            kwargs["force"] = force
        if tracking_job is not None and "tracking_job" in parameters:
            kwargs["tracking_job"] = tracking_job
        return self.index_arxiv_paper(arxiv_id, **kwargs)

    def init(self) -> None:
        self.repository.init()

    def _start_job_heartbeat(self, job: JobResponse) -> tuple[threading.Event, threading.Thread | None]:
        if not job.lease_token:
            return threading.Event(), None
        stop = threading.Event()

        def _loop() -> None:
            while not stop.wait(JOB_HEARTBEAT_INTERVAL_SECONDS):
                refreshed = self.repository.heartbeat_job(
                    job.job_id,
                    lease_token=job.lease_token or "",
                    worker_id=self._worker_id,
                    lease_seconds=JOB_LEASE_SECONDS,
                )
                if refreshed is None:
                    logger.warning(
                        "Job heartbeat lost its lease",
                        extra={"event": "jobs.heartbeat_lost", "job_id": job.job_id},
                    )
                    return

        thread = threading.Thread(
            target=_loop,
            name=f"paperbase-job-heartbeat-{job.job_id}",
            daemon=True,
        )
        thread.start()
        return stop, thread

    def _raise_if_current_job_canceled(self) -> None:
        job = _CURRENT_JOB.get()
        if job is None or not job.lease_token:
            return
        if self.repository.is_job_cancel_requested(job.job_id, lease_token=job.lease_token):
            raise JobCanceled(f"Job {job.job_id} was canceled")

    def _browser_user_settings_response(
        self,
        user: BrowserUser,
        *,
        openai_api_key_hint: str = "",
    ) -> BrowserUserSettingsResponse:
        settings = self.repository.get_browser_user_settings(user.user_id)  # type: ignore[attr-defined]
        return settings.model_copy(
            update={
                "openai_api_key_hint": openai_api_key_hint,
                "github_oauth_available": bool(self.settings.github_client_id and self.settings.github_client_secret),
                "github_manage_access_url": (
                    f"https://github.com/settings/connections/applications/{self.settings.github_client_id}"
                    if self.settings.github_client_id
                    else "https://github.com/settings/connections/applications"
                ),
            }
        )

    def _decorate_browser_user(self, user: BrowserUser | None) -> BrowserUser | None:
        if user is None:
            return None
        is_admin = self.settings.is_admin_email(user.email)
        if user.is_admin == is_admin:
            return user
        return user.model_copy(update={"is_admin": is_admin})

    def _browser_session_response(self, user: BrowserUser | None) -> BrowserSessionResponse:
        user = self._decorate_browser_user(user)
        if user is None:
            return BrowserSessionResponse(authenticated=False)
        api_key_record = self.repository.get_openai_api_key_record(user.user_id)  # type: ignore[attr-defined]
        return BrowserSessionResponse(
            authenticated=True,
            user=user,
            has_openai_api_key=api_key_record is not None,
            settings=self._browser_user_settings_response(
                user,
                openai_api_key_hint=(api_key_record or {}).get("api_key_hint", ""),
            ),
        )

    def register_browser_user(self, request: SignupRequest) -> tuple[BrowserSessionResponse, str]:
        email = _normalize_email(request.email)
        if self.repository.get_browser_user_by_email(email):  # type: ignore[attr-defined]
            raise HTTPException(status_code=409, detail="An account with that email already exists")
        user = self.repository.create_browser_user(  # type: ignore[attr-defined]
            email=email,
            display_name=request.display_name.strip(),
            password_hash=hash_password(request.password),
        )
        self.repository.ensure_default_project(user.user_id)  # type: ignore[attr-defined]
        session_token = self._create_browser_session(user.user_id)
        return self._browser_session_response(user), session_token

    def authenticate_browser_user(self, request: LoginRequest) -> tuple[BrowserSessionResponse, str]:
        email = _normalize_email(request.email)
        user = self.repository.get_browser_user_by_email(email)  # type: ignore[attr-defined]
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        password_hash_value = self.repository.get_browser_password_hash(user.user_id)  # type: ignore[attr-defined]
        if not password_hash_value or not verify_password(password_hash_value, request.password):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        if password_needs_rehash(password_hash_value):
            self.repository.set_browser_user_password(user.user_id, hash_password(request.password))  # type: ignore[attr-defined]
        session_token = self._create_browser_session(user.user_id)
        return self._browser_session_response(user), session_token

    def _create_browser_session(self, user_id: str) -> str:
        raw_token = new_browser_token()
        self.repository.create_browser_session(  # type: ignore[attr-defined]
            user_id=user_id,
            token_hash_value=token_hash(raw_token),
            expires_at=utcnow() + timedelta(seconds=self.settings.browser_session_ttl_seconds),
        )
        return raw_token

    def get_browser_session(self, raw_token: str | None) -> BrowserSessionResponse:
        if not raw_token:
            return BrowserSessionResponse(authenticated=False)
        user = self.repository.get_browser_user_by_session_token_hash(token_hash(raw_token))  # type: ignore[attr-defined]
        return self._browser_session_response(user)

    def get_current_browser_user(self, raw_token: str | None) -> BrowserUser | None:
        if not raw_token:
            return None
        user = self.repository.get_browser_user_by_session_token_hash(token_hash(raw_token))  # type: ignore[attr-defined]
        return self._decorate_browser_user(user)

    def logout_browser_session(self, raw_token: str | None) -> None:
        if raw_token:
            self.repository.delete_browser_session(token_hash(raw_token))  # type: ignore[attr-defined]

    def request_password_reset(self, request: ForgotPasswordRequest) -> dict[str, str]:
        email = _normalize_email(request.email)
        user = self.repository.get_browser_user_by_email(email)  # type: ignore[attr-defined]
        if user is None:
            return {"status": "ok"}
        raw_token = new_browser_token()
        self.repository.create_password_reset_token(  # type: ignore[attr-defined]
            user_id=user.user_id,
            token_hash_value=token_hash(raw_token),
            expires_at=utcnow() + timedelta(seconds=self.settings.password_reset_ttl_seconds),
        )
        reset_link = f"{self.settings.chat_public_url.rstrip('/')}/?reset_token={raw_token}"
        self.email_sender.send_password_reset(email=user.email, reset_link=reset_link)
        return {"status": "ok"}

    def reset_password(self, request: ResetPasswordRequest) -> dict[str, str]:
        user = self.repository.consume_password_reset_token(token_hash(request.token))  # type: ignore[attr-defined]
        if user is None:
            raise HTTPException(status_code=400, detail="Invalid or expired reset token")
        self.repository.set_browser_user_password(user.user_id, hash_password(request.password))  # type: ignore[attr-defined]
        return {"status": "ok"}

    def upsert_google_browser_user(
        self, *, google_subject: str, email: str, display_name: str
    ) -> tuple[BrowserSessionResponse, str]:
        email = _normalize_email(email)
        user = self.repository.get_browser_user_by_google_subject(google_subject)  # type: ignore[attr-defined]
        if user is None:
            existing = self.repository.get_browser_user_by_email(email)  # type: ignore[attr-defined]
            user = existing or self.repository.create_browser_user(  # type: ignore[attr-defined]
                email=email,
                display_name=display_name.strip(),
                password_hash=None,
            )
            self.repository.link_google_identity(user_id=user.user_id, google_subject=google_subject, email=email)  # type: ignore[attr-defined]
        session_token = self._create_browser_session(user.user_id)
        return self._browser_session_response(user), session_token

    def save_user_openai_key(self, user: BrowserUser, request: SaveOpenAIKeyRequest) -> BrowserSessionResponse:
        self.repository.save_openai_api_key(  # type: ignore[attr-defined]
            user_id=user.user_id,
            encrypted_api_key=self.secret_cipher.encrypt(request.api_key.strip()),
            hint=api_key_hint(request.api_key),
        )
        return self._browser_session_response(user)

    def delete_user_openai_key(self, user: BrowserUser) -> BrowserSessionResponse:
        self.repository.delete_openai_api_key(user.user_id)  # type: ignore[attr-defined]
        return self._browser_session_response(user)

    def save_user_anthropic_key(self, user: BrowserUser, request: SaveOpenAIKeyRequest) -> BrowserSessionResponse:
        return self.save_user_openai_key(user, request)

    def delete_user_anthropic_key(self, user: BrowserUser) -> BrowserSessionResponse:
        return self.delete_user_openai_key(user)

    def save_user_modal_credentials(
        self,
        user: BrowserUser,
        request: SaveModalCredentialsRequest,
    ) -> BrowserSessionResponse:
        self.repository.save_modal_credentials(  # type: ignore[attr-defined]
            user_id=user.user_id,
            encrypted_token_id=self.secret_cipher.encrypt(request.token_id.strip()),
            encrypted_token_secret=self.secret_cipher.encrypt(request.token_secret.strip()),
            token_id_hint=api_key_hint(request.token_id),
        )
        return self._browser_session_response(user)

    def delete_user_modal_credentials(self, user: BrowserUser) -> BrowserSessionResponse:
        self.repository.delete_modal_credentials(user.user_id)  # type: ignore[attr-defined]
        return self._browser_session_response(user)

    def save_user_elevenlabs_key(
        self,
        user: BrowserUser,
        request: SaveElevenLabsKeyRequest,
    ) -> BrowserSessionResponse:
        self.repository.save_elevenlabs_api_key(  # type: ignore[attr-defined]
            user_id=user.user_id,
            encrypted_api_key=self.secret_cipher.encrypt(request.api_key.strip()),
            hint=api_key_hint(request.api_key),
        )
        return self._browser_session_response(user)

    def delete_user_elevenlabs_key(self, user: BrowserUser) -> BrowserSessionResponse:
        self.repository.delete_elevenlabs_api_key(user.user_id)  # type: ignore[attr-defined]
        return self._browser_session_response(user)

    def update_user_notification_preferences(
        self,
        user: BrowserUser,
        request: UpdateNotificationsRequest,
    ) -> BrowserSessionResponse:
        self.repository.set_weekly_papers_review_email(  # type: ignore[attr-defined]
            user_id=user.user_id,
            enabled=request.weekly_papers_review_email,
        )
        return self._browser_session_response(user)

    def update_user_appearance(
        self,
        user: BrowserUser,
        request: UpdateAppearanceRequest,
    ) -> BrowserSessionResponse:
        self.repository.set_appearance_theme(  # type: ignore[attr-defined]
            user_id=user.user_id,
            theme=request.theme,
        )
        return self._browser_session_response(user)

    def save_github_connection(
        self,
        user: BrowserUser,
        *,
        github_user_id: str,
        github_login: str,
        access_token: str,
        scope: str,
    ) -> BrowserSessionResponse:
        self.repository.save_github_connection(  # type: ignore[attr-defined]
            user_id=user.user_id,
            github_user_id=github_user_id,
            github_login=github_login,
            encrypted_access_token=self.secret_cipher.encrypt(access_token.strip()),
            scope=scope.strip(),
        )
        logger.info(
            "Saved GitHub repo permission connection",
            extra={
                "event": "github.connection_saved",
                "user_id": user.user_id,
                "github_user_id": github_user_id,
                "github_login": github_login,
                "scopes": _github_scope_values(scope),
                "repo_write_allowed": _github_scope_allows_repo_write(scope),
            },
        )
        return self._browser_session_response(user)

    def delete_github_connection(self, user: BrowserUser) -> BrowserSessionResponse:
        self.repository.delete_github_connection(user.user_id)  # type: ignore[attr-defined]
        return self._browser_session_response(user)

    def _get_modal_credentials(self, user: BrowserUser) -> tuple[str, str]:
        record = self.repository.get_modal_credentials_record(user.user_id)  # type: ignore[attr-defined]
        if record is None:
            raise HTTPException(status_code=409, detail="Add Modal credentials before running Python files")
        return (
            self.secret_cipher.decrypt(record["encrypted_token_id"]),
            self.secret_cipher.decrypt(record["encrypted_token_secret"]),
        )

    def _get_elevenlabs_api_key_optional(self, user: BrowserUser) -> str | None:
        record = self.repository.get_elevenlabs_api_key_record(user.user_id)  # type: ignore[attr-defined]
        if record is None:
            return None
        try:
            api_key_value = self.secret_cipher.decrypt(record["encrypted_api_key"]).strip()
        except InvalidToken:
            logger.warning(
                "Saved ElevenLabs API key could not be decrypted",
                extra={"event": "settings.elevenlabs_key_invalid", "user_id": user.user_id},
            )
            self.repository.delete_elevenlabs_api_key(user.user_id)  # type: ignore[attr-defined]
            return None
        return api_key_value or None

    def _get_github_connection(self, user: BrowserUser) -> dict[str, str]:
        record = self.repository.get_github_connection_record(user.user_id)  # type: ignore[attr-defined]
        if record is None:
            logger.info(
                "GitHub repo permission connection is missing",
                extra={"event": "github.connection_missing", "user_id": user.user_id},
            )
            raise HTTPException(
                status_code=409,
                detail="Connect GitHub before creating or saving project files",
            )
        if not _github_scope_allows_repo_write(record.get("scope", "")):
            logger.info(
                "GitHub repo permission connection lacks repo write scope",
                extra={
                    "event": "github.connection_scope_denied",
                    "user_id": user.user_id,
                    "github_login": record.get("github_login", ""),
                    "scopes": _github_scope_values(record.get("scope", "")),
                },
            )
            raise HTTPException(
                status_code=409,
                detail="Reconnect GitHub with repo access before creating or saving project files",
            )
        logger.info(
            "GitHub repo permission connection is ready",
            extra={
                "event": "github.connection_ready",
                "user_id": user.user_id,
                "github_login": record.get("github_login", ""),
                "scopes": _github_scope_values(record.get("scope", "")),
            },
        )
        return record

    def list_uploaded_documents(self, user: BrowserUser) -> UploadListResponse:
        documents = self.repository.list_uploaded_documents(user.user_id)  # type: ignore[attr-defined]
        tag_set = sorted({tag for document in documents for tag in document.tags})
        return UploadListResponse(documents=documents, available_tags=tag_set)

    def upload_document(
        self,
        *,
        user: BrowserUser,
        filename: str,
        content_type: str,
        data: bytes,
        tags: list[str],
    ) -> UploadedDocumentResponse:
        if len(data) > self.settings.upload_max_bytes:
            raise HTTPException(status_code=413, detail="Upload is too large")
        normalized_tags = _normalize_tags(tags)
        basename = Path(filename).name or "upload.bin"
        title = Path(basename).stem.replace("-", " ").replace("_", " ").strip() or basename
        document = self.repository.create_uploaded_document(  # type: ignore[attr-defined]
            user_id=user.user_id,
            title=title,
            source_filename=basename,
            content_type=content_type or "application/octet-stream",
            tags=normalized_tags,
            storage_path="",
            extracted_text_path=None,
            status="processing",
        )
        prefix = f"uploads/{user.user_id}/{document.document_id}"
        original_path = f"{prefix}/original/{basename}"
        self.object_store.put_bytes(original_path, data, content_type=content_type)
        try:
            extraction = extract_uploaded_document(basename, data, content_type=content_type)
            text_chunks: list[dict[str, str]] = [
                {"path": "text/full.md", "chunk_kind": "full_text", "content": extraction.text_markdown}
            ]
            self.object_store.put_text(f"{prefix}/text/full.md", extraction.text_markdown, content_type="text/markdown")
            for section in extraction.sections:
                self.object_store.put_text(
                    f"{prefix}/sections/{section.slug}.md", section.markdown, content_type="text/markdown"
                )
                text_chunks.append(
                    {
                        "path": f"sections/{section.slug}.md",
                        "chunk_kind": "section",
                        "content": section.markdown,
                    }
                )
            self.repository.replace_uploaded_document_chunks(document.document_id, text_chunks)  # type: ignore[attr-defined]
            updated = self.repository.update_uploaded_document(  # type: ignore[attr-defined]
                document_id=document.document_id,
                title=title,
                tags=normalized_tags,
                extracted_text_path=f"{prefix}/text/full.md",
                status="ready",
            )
            if updated is None:
                raise RuntimeError("Uploaded document record vanished during processing")
            return updated
        except Exception:
            failed = self.repository.update_uploaded_document(  # type: ignore[attr-defined]
                document_id=document.document_id,
                title=title,
                tags=normalized_tags,
                extracted_text_path=None,
                status="failed",
            )
            if failed is None:
                raise
            raise

    def list_chat_threads(self, user: BrowserUser) -> ChatThreadListResponse:
        return ChatThreadListResponse(threads=self.repository.list_chat_threads(user.user_id))  # type: ignore[attr-defined]

    def list_projects(self, user: BrowserUser) -> ProjectListResponse:
        return ProjectListResponse(projects=self.repository.list_projects(user.user_id))  # type: ignore[attr-defined]

    def _github_access(self, user: BrowserUser) -> tuple[dict[str, str], str]:
        github = self._get_github_connection(user)
        access_token = self.secret_cipher.decrypt(github["encrypted_access_token"])
        logger.info(
            "Decrypted GitHub repo permission token",
            extra={
                "event": "github.connection_token_ready",
                "user_id": user.user_id,
                "github_login": github.get("github_login", ""),
                "access_token_present": bool(access_token),
            },
        )
        return github, access_token

    def get_github_repo_metadata(
        self,
        user: BrowserUser,
        *,
        owner: str,
        repo: str,
    ) -> GitHubRepoMetadataResponse:
        github, access_token = self._github_access(user)
        owner_login = (owner or github["github_login"]).strip()
        repo_name = _validate_repo_name(repo)
        try:
            repo_info = self.github_repo_manager.get_repo(
                access_token=access_token,
                owner_login=owner_login,
                repo_name=repo_name,
            )
            if repo_info is None:
                raise HTTPException(status_code=404, detail="GitHub repository not found")
            branches = self.github_repo_manager.list_branches(
                access_token=access_token,
                owner_login=owner_login,
                repo_name=repo_name,
            )
        except GitHubSyncError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return GitHubRepoMetadataResponse(
            owner=repo_info.owner_login or owner_login,
            name=repo_info.repo_name,
            html_url=repo_info.repo_url,
            default_branch=repo_info.default_branch or "main",
            branches=[
                GitHubRepoBranchSummary(
                    name=branch.name,
                    commit_sha=branch.commit_sha,
                    is_default=branch.name == (repo_info.default_branch or "main"),
                )
                for branch in branches
            ],
        )

    def list_github_repositories(self, user: BrowserUser) -> GitHubRepoListResponse:
        _, access_token = self._github_access(user)
        try:
            repos = self.github_repo_manager.list_repositories(access_token=access_token)
        except GitHubSyncError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return GitHubRepoListResponse(
            repos=[
                GitHubRepoSummary(
                    owner=repo.owner_login,
                    name=repo.repo_name,
                    full_name=repo.full_name,
                    html_url=repo.repo_url,
                    default_branch=repo.default_branch or "main",
                    private=repo.private,
                )
                for repo in repos
            ],
        )

    def list_github_repo_import_candidates(
        self,
        user: BrowserUser,
        *,
        owner: str,
        repo: str,
        branch: str,
    ) -> GitHubRepoImportCandidateResponse:
        github, access_token = self._github_access(user)
        owner_login = (owner or github["github_login"]).strip()
        repo_name = _validate_repo_name(repo)
        base_branch = (branch or "").strip()
        if not base_branch:
            raise HTTPException(status_code=422, detail="Base branch is required")
        try:
            candidates = self.github_repo_manager.list_importable_files(
                access_token=access_token,
                owner_login=owner_login,
                repo_name=repo_name,
                branch=base_branch,
            )
        except GitHubSyncError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return GitHubRepoImportCandidateResponse(
            owner=owner_login,
            name=repo_name,
            base_branch=base_branch,
            candidates=[
                GitHubRepoImportCandidate(
                    path=candidate.path,
                    file_kind=candidate.file_kind,  # type: ignore[arg-type]
                    size=candidate.size,
                )
                for candidate in candidates
            ],
        )

    def create_project(self, user: BrowserUser, request: CreateProjectRequest) -> ProjectResponse:
        name = request.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Project name is required")
        if request.github_repo is None:
            raise HTTPException(status_code=422, detail="GitHub repository information is required")
        github, access_token = self._github_access(user)
        repo_request = request.github_repo
        import_rows: list[tuple[str, str, str, str]] = []
        remote_head_sha = ""
        base_branch = "main"
        logger.info(
            "Starting project create GitHub permission flow",
            extra={
                "event": "project.create.github_start",
                "user_id": user.user_id,
                "project_name": name,
                "repo_mode": repo_request.mode,
                "repo_owner": repo_request.owner or github.get("github_login", ""),
                "repo_name": repo_request.name,
                "base_branch": repo_request.base_branch or "",
                "import_path_count": len(repo_request.import_paths),
            },
        )
        try:
            if repo_request.mode == "create":
                repo_name = _validate_repo_name(repo_request.name)
                logger.info(
                    "Creating GitHub repository for project",
                    extra={
                        "event": "project.create.github_repo_create",
                        "user_id": user.user_id,
                        "github_login": github["github_login"],
                        "repo_name": repo_name,
                    },
                )
                repo_info = self.github_repo_manager.create_repo(
                    access_token=access_token,
                    owner_login=github["github_login"],
                    repo_name=repo_name,
                    description=f"Paperbase project: {name}",
                )
                owner_login = repo_info.owner_login or github["github_login"]
                default_branch = "main"
                base_branch = "main"
                logger.info(
                    "Created GitHub repository for project",
                    extra={
                        "event": "project.create.github_repo_created",
                        "user_id": user.user_id,
                        "owner_login": owner_login,
                        "repo_name": repo_info.repo_name,
                        "repo_url": repo_info.repo_url,
                        "default_branch": default_branch,
                    },
                )
            else:
                owner_login = (repo_request.owner or "").strip()
                if not owner_login:
                    raise HTTPException(status_code=422, detail="GitHub repository owner is required")
                repo_name = _validate_repo_name(repo_request.name)
                logger.info(
                    "Loading existing GitHub repository for project import",
                    extra={
                        "event": "project.create.github_existing_load",
                        "user_id": user.user_id,
                        "owner_login": owner_login,
                        "repo_name": repo_name,
                        "requested_base_branch": repo_request.base_branch or "",
                    },
                )
                repo_info = self.github_repo_manager.get_repo(
                    access_token=access_token,
                    owner_login=owner_login,
                    repo_name=repo_name,
                )
                if repo_info is None:
                    raise HTTPException(status_code=404, detail="GitHub repository not found")
                owner_login = repo_info.owner_login or owner_login
                default_branch = repo_info.default_branch or "main"
                base_branch = (repo_request.base_branch or default_branch).strip()
                if not base_branch:
                    raise HTTPException(status_code=422, detail="Base branch is required")
                branches = self.github_repo_manager.list_branches(
                    access_token=access_token,
                    owner_login=owner_login,
                    repo_name=repo_name,
                )
                if base_branch not in {branch.name for branch in branches}:
                    raise HTTPException(status_code=422, detail="Selected base branch does not exist")
                remote_head_sha = self.github_repo_manager.get_branch_head(
                    access_token=access_token,
                    owner_login=owner_login,
                    repo_name=repo_name,
                    branch=base_branch,
                )
                logger.info(
                    "Loaded existing GitHub repository branch for project import",
                    extra={
                        "event": "project.create.github_existing_branch",
                        "user_id": user.user_id,
                        "owner_login": owner_login,
                        "repo_name": repo_name,
                        "base_branch": base_branch,
                        "remote_head_sha": remote_head_sha,
                        "branch_count": len(branches),
                    },
                )
                candidate_map = {
                    candidate.path: candidate
                    for candidate in self.github_repo_manager.list_importable_files(
                        access_token=access_token,
                        owner_login=owner_login,
                        repo_name=repo_name,
                        branch=base_branch,
                    )
                }
                for raw_path in repo_request.import_paths:
                    path = _normalize_repo_path(raw_path)
                    candidate = candidate_map.get(path)
                    if candidate is None:
                        raise HTTPException(status_code=422, detail=f"Repository path '{path}' is not importable")
                    if candidate.size > _MAX_REPO_IMPORT_BYTES:
                        raise HTTPException(status_code=422, detail=f"Repository path '{path}' is too large to import")
                    raw_content = self.github_repo_manager.get_file_text(
                        access_token=access_token,
                        owner_login=owner_login,
                        repo_name=repo_name,
                        path=path,
                        ref=base_branch,
                    )
                    file_kind = _file_kind_for_repo_path(path)
                    content = _notebook_to_python_source(raw_content) if file_kind == "notebook" else raw_content
                    import_rows.append((Path(path).name, file_kind, path, content))
                logger.info(
                    "Prepared GitHub repository imports for project",
                    extra={
                        "event": "project.create.github_imports_ready",
                        "user_id": user.user_id,
                        "owner_login": owner_login,
                        "repo_name": repo_name,
                        "base_branch": base_branch,
                        "import_path_count": len(import_rows),
                    },
                )
        except GitHubSyncError as exc:
            logger.warning(
                "GitHub project create flow failed",
                extra={
                    "event": "project.create.github_failed",
                    "user_id": user.user_id,
                    "repo_mode": repo_request.mode,
                    "repo_owner": repo_request.owner or github.get("github_login", ""),
                    "repo_name": repo_request.name,
                    "error": str(exc),
                },
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        project = self.repository.create_project(  # type: ignore[attr-defined]
            user_id=user.user_id,
            name=name,
            description=request.description.strip(),
        )
        saved = self.repository.save_project_repo_sync(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project.project_id,
            github_owner_login=owner_login,
            github_repo_name=repo_info.repo_name,
            github_repo_url=repo_info.repo_url,
            base_branch=base_branch,
            default_branch=default_branch,
            last_sync_commit_sha=remote_head_sha,
            last_sync_commit_url=f"{repo_info.repo_url}/commit/{remote_head_sha}" if remote_head_sha else "",
            last_synced_at=None,
        )
        if saved is None:
            raise HTTPException(status_code=500, detail="Project repository metadata could not be saved")
        for filename, file_kind, repo_path, content in import_rows:
            imported = self.repository.create_python_file(  # type: ignore[attr-defined]
                user_id=user.user_id,
                project_id=project.project_id,
                filename=filename,
                file_kind=file_kind,
                repo_path=repo_path,
                content=content,
            )
            if imported is None:
                raise HTTPException(status_code=500, detail=f"Repository path '{repo_path}' could not be imported")
        logger.info(
            "Finished project create GitHub permission flow",
            extra={
                "event": "project.create.github_completed",
                "user_id": user.user_id,
                "project_id": project.project_id,
                "owner_login": owner_login,
                "repo_name": repo_info.repo_name,
                "base_branch": base_branch,
                "import_path_count": len(import_rows),
            },
        )
        return self.repository.get_project(user_id=user.user_id, project_id=project.project_id) or project  # type: ignore[attr-defined]

    def update_project(self, user: BrowserUser, project_id: str, request: UpdateProjectRequest) -> ProjectResponse:
        name = request.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Project name is required")
        project = self.repository.update_project(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
            name=name,
            description=request.description.strip(),
        )
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return project

    def delete_project(self, user: BrowserUser, project_id: str) -> None:
        project = self.repository.get_project(user_id=user.user_id, project_id=project_id)  # type: ignore[attr-defined]
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        deleted = self.repository.delete_project(user_id=user.user_id, project_id=project_id)  # type: ignore[attr-defined]
        if not deleted:
            raise HTTPException(status_code=404, detail="Project not found")

    def list_project_chat_threads(
        self,
        user: BrowserUser,
        project_id: str,
        *,
        limit: int = 5,
        offset: int = 0,
    ) -> ProjectSessionPageResponse:
        page = self.repository.list_project_chat_threads(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
            limit=limit,
            offset=offset,
        )
        if page is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return page

    def list_project_items(
        self,
        user: BrowserUser,
        project_id: str,
        *,
        limit: int = 25,
        offset: int = 0,
        item_kind: str | None = None,
    ) -> ProjectItemPageResponse:
        page = self.repository.list_project_items(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
            limit=limit,
            offset=offset,
            item_kind=item_kind,
        )
        if page is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return page

    def _project_presentation_with_share_url(
        self,
        presentation: ProjectPresentationResponse,
    ) -> ProjectPresentationResponse:
        updates: dict[str, Any] = {}
        if presentation.audio_storage_path:
            if presentation.is_public and presentation.share_token:
                updates["audio_url"] = (
                    f"/v1/browser/project-presentations/public/{quote(presentation.share_token, safe='')}/audio"
                )
            else:
                updates["audio_url"] = (
                    f"/v1/browser/projects/{quote(presentation.project_id, safe='')}/presentation/audio"
                )
        else:
            updates["audio_url"] = ""
        slides: list[ProjectPresentationSlideResponse] = []
        for raw_slide in presentation.slides:
            slide = self._project_presentation_slide_response(raw_slide)
            image_url = ""
            if slide.image_storage_path:
                if presentation.is_public and presentation.share_token:
                    image_url = (
                        f"/v1/browser/project-presentations/public/"
                        f"{quote(presentation.share_token, safe='')}/slides/{quote(slide.slide_id, safe='')}/image"
                    )
                else:
                    image_url = (
                        f"/v1/browser/projects/{quote(presentation.project_id, safe='')}/presentation/"
                        f"slides/{quote(slide.slide_id, safe='')}/image"
                    )
            slides.append(slide.model_copy(update={"image_url": image_url}))
        updates["slides"] = slides
        if not presentation.is_public or not presentation.share_token:
            updates["share_url"] = ""
            return presentation.model_copy(update=updates)
        updates["share_url"] = self._project_presentation_share_url(presentation.share_token)
        return presentation.model_copy(update=updates)

    def _project_presentation_slide_response(self, slide: Any) -> ProjectPresentationSlideResponse:
        if isinstance(slide, ProjectPresentationSlideResponse):
            return slide
        if isinstance(slide, dict):
            try:
                return ProjectPresentationSlideResponse.model_validate(slide)
            except ValueError:
                pass
        return ProjectPresentationSlideResponse(
            slide_id="slide",
            title="Slide",
            description="",
            visual_type="code",
        )

    def _project_presentation_slide_storage_dict(self, slide: ProjectPresentationSlideResponse) -> dict[str, Any]:
        return {
            "slide_id": slide.slide_id,
            "title": slide.title,
            "description": slide.description,
            "visual_type": slide.visual_type,
            "visual_code": slide.visual_code,
            "image_prompt": slide.image_prompt,
            "image_url": "",
            "image_storage_path": slide.image_storage_path,
        }

    def get_project_presentation(self, user: BrowserUser, project_id: str) -> ProjectPresentationResponse:
        project = self.repository.get_project(user_id=user.user_id, project_id=project_id)  # type: ignore[attr-defined]
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        presentation = self.repository.get_project_presentation(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
        )
        if presentation is None:
            return ProjectPresentationResponse(project_id=project_id, status="missing")
        return self._project_presentation_with_share_url(presentation)

    def get_public_project_presentation(self, share_token: str) -> ProjectPresentationResponse:
        token = share_token.strip()
        if not token:
            raise HTTPException(status_code=404, detail="Presentation not found")
        presentation = self.repository.get_public_project_presentation(token)  # type: ignore[attr-defined]
        if presentation is None:
            raise HTTPException(status_code=404, detail="Presentation not found")
        return self._project_presentation_with_share_url(presentation)

    def get_project_presentation_audio(self, user: BrowserUser, project_id: str) -> tuple[bytes, str]:
        presentation = self.repository.get_project_presentation(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
        )
        if (
            presentation is None
            or presentation.status != "completed"
            or not presentation.audio_storage_path
            or not self.object_store.exists(presentation.audio_storage_path)
        ):
            raise HTTPException(status_code=404, detail="Presentation audio not found")
        return self.object_store.get_bytes(presentation.audio_storage_path), (
            presentation.audio_content_type or "audio/mpeg"
        )

    def get_public_project_presentation_audio(self, share_token: str) -> tuple[bytes, str]:
        token = share_token.strip()
        if not token:
            raise HTTPException(status_code=404, detail="Presentation audio not found")
        presentation = self.repository.get_public_project_presentation(token)  # type: ignore[attr-defined]
        if (
            presentation is None
            or not presentation.audio_storage_path
            or not self.object_store.exists(presentation.audio_storage_path)
        ):
            raise HTTPException(status_code=404, detail="Presentation audio not found")
        return self.object_store.get_bytes(presentation.audio_storage_path), (
            presentation.audio_content_type or "audio/mpeg"
        )

    def get_project_presentation_slide_image(
        self,
        user: BrowserUser,
        project_id: str,
        slide_id: str,
    ) -> tuple[bytes, str]:
        presentation = self.repository.get_project_presentation(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
        )
        return self._project_presentation_slide_image_payload(presentation, slide_id)

    def get_public_project_presentation_slide_image(self, share_token: str, slide_id: str) -> tuple[bytes, str]:
        token = share_token.strip()
        if not token:
            raise HTTPException(status_code=404, detail="Presentation image not found")
        presentation = self.repository.get_public_project_presentation(token)  # type: ignore[attr-defined]
        return self._project_presentation_slide_image_payload(presentation, slide_id)

    def _project_presentation_slide_image_payload(
        self,
        presentation: ProjectPresentationResponse | None,
        slide_id: str,
    ) -> tuple[bytes, str]:
        if presentation is None or presentation.status != "completed":
            raise HTTPException(status_code=404, detail="Presentation not found")
        requested_slide_id = slide_id.strip()
        slide = next(
            (
                self._project_presentation_slide_response(item)
                for item in presentation.slides
                if self._project_presentation_slide_response(item).slide_id == requested_slide_id
            ),
            None,
        )
        if (
            slide is None
            or not slide.image_storage_path
            or not self.object_store.exists(slide.image_storage_path)
        ):
            raise HTTPException(status_code=404, detail="Presentation image not found")
        return self.object_store.get_bytes(slide.image_storage_path), "image/png"

    def start_project_presentation(
        self,
        user: BrowserUser,
        project_id: str,
        request: ProjectPresentationStartRequest,
    ) -> ProjectPresentationResponse:
        project = self.repository.get_project(user_id=user.user_id, project_id=project_id)  # type: ignore[attr-defined]
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        existing = self.repository.get_project_presentation(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
        )
        if (
            existing is not None
            and not request.regenerate
            and existing.status in {"pending", "running", "completed"}
        ):
            return self._project_presentation_with_share_url(existing)

        resolved_user = self.repository.get_browser_user_by_id(user.user_id) or user  # type: ignore[attr-defined]
        api_key_value, api_key_state = self._choose_openai_api_key(resolved_user)
        if not api_key_value:
            detail = (
                "Saved OpenAI API key is no longer valid"
                if api_key_state == "api_key_invalid"
                else "Add your OpenAI API key before creating a presentation"
            )
            raise HTTPException(status_code=409, detail=detail)

        context_snapshot = self._project_presentation_context_snapshot(
            user=user,
            project=project,
            explorations=request.explorations,
        )
        pending = self.repository.upsert_project_presentation_pending(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
            context_snapshot=context_snapshot,
            job_id="",
        )
        if pending is None:
            raise HTTPException(status_code=404, detail="Project not found")
        job = self.repository.create_job(  # type: ignore[attr-defined]
            "project_presentation",
            {
                "user_id": user.user_id,
                "project_id": project_id,
                "presentation_id": pending.presentation_id,
                "context_snapshot": context_snapshot,
            },
        )
        pending = self.repository.upsert_project_presentation_pending(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
            context_snapshot=context_snapshot,
            job_id=job.job_id,
        )
        if pending is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return self._project_presentation_with_share_url(pending)

    def cancel_project_presentation(
        self,
        user: BrowserUser,
        project_id: str,
    ) -> ProjectPresentationResponse:
        current = self.repository.get_project_presentation(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
        )
        if current is None:
            raise HTTPException(status_code=404, detail="Presentation not found")
        if current.job_id:
            self.repository.cancel_job(current.job_id)  # type: ignore[attr-defined]
        if current.status == "completed":
            updated = self._update_project_presentation_progress(
                current.presentation_id,
                phase_key="complete",
                phase_label="Presentation ready",
                detail="The presentation is ready to view and share. Optional media generation was canceled.",
                percent=100,
                estimated_seconds_remaining=0,
            )
            return self._project_presentation_with_share_url(updated or current)
        if current.status in {"failed", "canceled"}:
            return self._project_presentation_with_share_url(current)
        canceled = self.repository.cancel_project_presentation(current.presentation_id)  # type: ignore[attr-defined]
        if canceled is None:
            raise HTTPException(status_code=404, detail="Presentation not found")
        return self._project_presentation_with_share_url(canceled)

    def update_project_presentation_share(
        self,
        user: BrowserUser,
        project_id: str,
        request: ProjectPresentationShareRequest,
    ) -> ProjectPresentationResponse:
        current = self.repository.get_project_presentation(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
        )
        if current is None:
            raise HTTPException(status_code=404, detail="Presentation not found")
        share_token = current.share_token
        if request.is_public and not share_token:
            share_token = new_id("share")
        updated = self.repository.update_project_presentation_share(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
            is_public=request.is_public,
            share_token=share_token,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Presentation not found")
        return self._project_presentation_with_share_url(updated)

    def _project_presentation_context_snapshot(
        self,
        *,
        user: BrowserUser,
        project: ProjectResponse,
        explorations,
    ) -> dict[str, Any]:
        exploration_rows: list[dict[str, Any]] = []
        paper_ids: set[str] = set()
        paper_order: list[str] = []

        def remember_paper_id(value: str) -> None:
            paper_id = value.strip()
            if not paper_id or paper_id in paper_ids:
                return
            paper_ids.add(paper_id)
            paper_order.append(paper_id)

        for exploration in list(explorations or [])[:_PROJECT_PRESENTATION_MAX_EXPLORATIONS]:
            annotations: list[dict[str, Any]] = []
            for annotation in list(exploration.annotations or [])[:_PROJECT_PRESENTATION_MAX_ANNOTATIONS]:
                if not isinstance(annotation, dict):
                    continue
                annotations.append(
                    {
                        "kind": str(annotation.get("kind") or "")[:24],
                        "note": self._truncate_text(str(annotation.get("note") or ""), 1800),
                        "selected_text": self._truncate_text(
                            str(annotation.get("selected_text") or annotation.get("selectedText") or ""),
                            3000,
                        ),
                        "nearby_text": self._truncate_text(
                            str(annotation.get("nearby_text") or annotation.get("nearbyText") or ""),
                            3000,
                        ),
                        "section_title": self._truncate_text(
                            str(annotation.get("section_title") or annotation.get("sectionTitle") or ""),
                            240,
                        ),
                        "created_at": str(annotation.get("created_at") or annotation.get("createdAt") or ""),
                    }
                )
            paper_id = exploration.paper_id.strip()
            if paper_id:
                remember_paper_id(paper_id)
            exploration_rows.append(
                {
                    "exploration_id": exploration.exploration_id,
                    "paper_id": paper_id,
                    "chat_thread_id": exploration.chat_thread_id,
                    "title": self._truncate_text(exploration.title, 500),
                    "authors": self._truncate_text(exploration.authors, 500),
                    "path": exploration.path,
                    "annotations": annotations,
                    "created_at": exploration.created_at,
                    "updated_at": exploration.updated_at,
                }
            )

        threads_page = self.repository.list_project_chat_threads(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project.project_id,
            limit=_PROJECT_PRESENTATION_MAX_THREADS,
            offset=0,
        )
        thread_rows: list[dict[str, Any]] = []
        for summary in (threads_page.threads if threads_page is not None else []):
            thread = self.repository.get_chat_thread(  # type: ignore[attr-defined]
                user_id=user.user_id,
                thread_id=summary.thread_id,
            )
            if thread is None:
                continue
            items: list[dict[str, Any]] = []
            for item in thread.items[:_PROJECT_PRESENTATION_MAX_THREAD_ITEMS]:
                if item.item_type not in {"user_message", "assistant_message", "compaction"}:
                    continue
                if not item.content_text.strip():
                    continue
                items.append(
                    {
                        "role": item.role,
                        "item_type": item.item_type,
                        "content": self._truncate_text(item.content_text, 5000),
                        "created_at": item.created_at.isoformat(),
                        "citations": [
                            {
                                "paper_id": citation.paper_id,
                                "title": citation.title,
                                "path": citation.path,
                            }
                            for citation in item.citations[:8]
                        ],
                    }
                )
                for citation in item.citations:
                    if citation.paper_id:
                        remember_paper_id(citation.paper_id)
            thread_rows.append(
                {
                    "thread_id": thread.thread_id,
                    "title": thread.title,
                    "created_at": thread.created_at.isoformat(),
                    "updated_at": thread.updated_at.isoformat(),
                    "items": items,
                }
            )

        comments: list[dict[str, Any]] = []
        for paper_id in paper_order[:_PROJECT_PRESENTATION_MAX_EXPLORATIONS]:
            for comment in self.repository.list_paper_comment_records(paper_id)[:30]:
                comments.append(
                    {
                        "paper_id": paper_id,
                        "author": comment.author_display_name,
                        "body": self._truncate_text(comment.body, 1600),
                        "created_at": comment.created_at.isoformat(),
                    }
                )

        research_materials = self._project_presentation_research_materials(
            paper_ids=paper_order,
            exploration_rows=exploration_rows,
            thread_rows=thread_rows,
            comments=comments,
        )
        code_artifacts = self._project_presentation_code_artifacts(user=user, project=project)

        snapshot = {
            "project": {
                "project_id": project.project_id,
                "name": project.name,
                "description": project.description,
                "created_at": project.created_at.isoformat(),
                "updated_at": project.updated_at.isoformat(),
            },
            "research_materials": research_materials,
            "explorations": exploration_rows,
            "chat_threads": thread_rows,
            "paper_comments": comments,
            "code_artifacts": code_artifacts,
            "generated_at": utcnow().isoformat(),
        }
        return self._truncate_project_presentation_snapshot(snapshot)

    def _project_presentation_code_artifacts(
        self,
        *,
        user: BrowserUser,
        project: ProjectResponse,
    ) -> list[dict[str, str]]:
        files = self.repository.list_project_python_files(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project.project_id,
        )
        artifacts: list[dict[str, str]] = []
        for file in list(files or [])[:_PROJECT_PRESENTATION_MAX_CODE_ARTIFACTS]:
            content = self._truncate_text(file.content or "", _PROJECT_PRESENTATION_MAX_CODE_CHARS)
            artifacts.append(
                {
                    "file_id": file.file_id,
                    "filename": self._truncate_text(file.filename, 240),
                    "repo_path": self._truncate_text(file.repo_path, 360),
                    "file_kind": file.file_kind,
                    "content": content,
                    "updated_at": file.updated_at.isoformat(),
                }
            )
        return artifacts

    def _project_presentation_research_materials(
        self,
        *,
        paper_ids: list[str],
        exploration_rows: list[dict[str, Any]],
        thread_rows: list[dict[str, Any]],
        comments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        source_by_paper: dict[str, dict[str, Any]] = {}
        for exploration in exploration_rows:
            paper_id = str(exploration.get("paper_id") or "").strip()
            if not paper_id:
                continue
            source = source_by_paper.setdefault(
                paper_id,
                {"title": "", "authors": "", "path": "", "selected_passages": [], "user_notes": []},
            )
            source["title"] = source.get("title") or str(exploration.get("title") or "")
            source["authors"] = source.get("authors") or str(exploration.get("authors") or "")
            source["path"] = source.get("path") or str(exploration.get("path") or "")
            for annotation in list(exploration.get("annotations") or [])[:_PROJECT_PRESENTATION_MAX_PASSAGES_PER_PAPER]:
                if not isinstance(annotation, dict):
                    continue
                note = self._truncate_text(str(annotation.get("note") or ""), 700)
                selected_text = self._truncate_text(str(annotation.get("selected_text") or ""), 1200)
                nearby_text = self._truncate_text(str(annotation.get("nearby_text") or ""), 800)
                section_title = self._truncate_text(str(annotation.get("section_title") or ""), 180)
                if selected_text or nearby_text:
                    source["selected_passages"].append(
                        {
                            "section_title": section_title,
                            "selected_text": selected_text,
                            "nearby_text": nearby_text,
                            "note": note,
                        }
                    )
                if note:
                    source["user_notes"].append(note)

        for thread in thread_rows:
            for item in list(thread.get("items") or []):
                if not isinstance(item, dict):
                    continue
                for citation in list(item.get("citations") or []):
                    if not isinstance(citation, dict):
                        continue
                    paper_id = str(citation.get("paper_id") or "").strip()
                    if not paper_id:
                        continue
                    source = source_by_paper.setdefault(
                        paper_id,
                        {"title": "", "authors": "", "path": "", "selected_passages": [], "user_notes": []},
                    )
                    source["title"] = source.get("title") or str(citation.get("title") or "")
                    source["path"] = source.get("path") or str(citation.get("path") or "")

        comments_by_paper: dict[str, list[dict[str, str]]] = {}
        for comment in comments:
            paper_id = str(comment.get("paper_id") or "").strip()
            if not paper_id:
                continue
            bucket = comments_by_paper.setdefault(paper_id, [])
            if len(bucket) >= _PROJECT_PRESENTATION_MAX_COMMENTS_PER_PAPER:
                continue
            body = self._truncate_text(str(comment.get("body") or ""), 700)
            if body:
                bucket.append(
                    {
                        "author": self._truncate_text(str(comment.get("author") or ""), 120),
                        "body": body,
                    }
                )

        materials: list[dict[str, Any]] = []
        for paper_id in paper_ids[:_PROJECT_PRESENTATION_MAX_RESEARCH_MATERIALS]:
            source = source_by_paper.get(paper_id, {})
            paper = self._project_presentation_resolve_paper(paper_id)
            material = {
                "paper_id": paper_id,
                "arxiv_id": "",
                "title": self._truncate_text(str(source.get("title") or ""), 500),
                "authors": self._truncate_text(str(source.get("authors") or ""), 500),
                "published": "",
                "updated": "",
                "primary_category": "",
                "categories": "",
                "abstract": "",
                "tldr": "",
                "summary_short": "",
                "citation_count": 0,
                "influential_citation_count": 0,
                "selected_passages": list(source.get("selected_passages") or [])[
                    :_PROJECT_PRESENTATION_MAX_PASSAGES_PER_PAPER
                ],
                "user_notes": list(source.get("user_notes") or [])[:_PROJECT_PRESENTATION_MAX_PASSAGES_PER_PAPER],
                "paper_comments": comments_by_paper.get(paper_id, []),
                "figures": [],
                "tables": [],
            }
            if paper is not None:
                material.update(
                    {
                        "paper_id": paper.paper_id,
                        "arxiv_id": paper.arxiv_id,
                        "title": self._truncate_text(paper.title or material["title"], 500),
                        "authors": self._truncate_text(paper.authors or material["authors"], 500),
                        "published": paper.published,
                        "updated": paper.updated,
                        "primary_category": paper.primary_category,
                        "categories": paper.categories,
                        "abstract": self._truncate_text(paper.abstract, 1800),
                        "tldr": self._truncate_text(paper.tldr, 1000),
                        "summary_short": self._truncate_text(paper.summary_short, 1000),
                        "citation_count": paper.citation_count,
                        "influential_citation_count": paper.influential_citation_count,
                        "figures": self._project_presentation_figure_snippets(paper.paper_id),
                        "tables": self._project_presentation_table_snippets(paper.paper_id),
                    }
                )
            if any(
                material.get(key)
                for key in (
                    "title",
                    "abstract",
                    "tldr",
                    "summary_short",
                    "selected_passages",
                    "user_notes",
                    "paper_comments",
                    "figures",
                    "tables",
                )
            ):
                materials.append(material)
        return materials

    def _project_presentation_resolve_paper(self, paper_id: str) -> PaperResponse | None:
        paper = self.repository.get_paper(paper_id)  # type: ignore[attr-defined]
        if paper is not None:
            return paper
        if paper_id.startswith("arxiv:"):
            return self.repository.get_paper_by_arxiv_id(paper_id.removeprefix("arxiv:"))  # type: ignore[attr-defined]
        paper = self.repository.get_paper(canonical_paper_id(paper_id))  # type: ignore[attr-defined]
        if paper is not None:
            return paper
        return self.repository.get_paper_by_arxiv_id(paper_id)  # type: ignore[attr-defined]

    def _project_presentation_figure_snippets(self, paper_id: str) -> list[dict[str, str]]:
        index_path = f"papers/{paper_id}/figures/index.json"
        if not self.object_store.exists(index_path):
            return []
        try:
            index_payload = json.loads(self.object_store.get_text(index_path))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
            return []
        figures: list[dict[str, str]] = []
        if not isinstance(index_payload, list):
            return figures
        for item in index_payload[:_PROJECT_PRESENTATION_MAX_FIGURES_PER_PAPER]:
            if not isinstance(item, dict):
                continue
            figure_id = str(item.get("figure_id") or "").strip()
            caption = str(item.get("caption") or "").strip()
            caption_path = f"papers/{paper_id}/figures/{figure_id}/caption.md"
            if figure_id and self.object_store.exists(caption_path):
                try:
                    caption = self.object_store.get_text(caption_path)
                except (KeyError, UnicodeDecodeError):
                    pass
            if figure_id or caption:
                figures.append(
                    {
                        "figure_id": self._truncate_text(figure_id, 120),
                        "caption": self._truncate_text(caption, 700),
                    }
                )
        return figures

    def _project_presentation_table_snippets(self, paper_id: str) -> list[dict[str, Any]]:
        index_path = f"papers/{paper_id}/tables/index.json"
        if not self.object_store.exists(index_path):
            return []
        try:
            index_payload = json.loads(self.object_store.get_text(index_path))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
            return []
        tables: list[dict[str, Any]] = []
        if not isinstance(index_payload, list):
            return tables
        for item in index_payload[:_PROJECT_PRESENTATION_MAX_TABLES_PER_PAPER]:
            if not isinstance(item, dict):
                continue
            table_id = str(item.get("table_id") or "").strip()
            rows: list[Any] = []
            json_path = f"papers/{paper_id}/tables/{table_id}.json"
            if table_id and self.object_store.exists(json_path):
                try:
                    rows_payload = json.loads(self.object_store.get_text(json_path))
                    if isinstance(rows_payload, list):
                        rows = self._project_presentation_table_rows(rows_payload)
                except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
                    rows = []
            tables.append(
                {
                    "table_id": self._truncate_text(table_id, 120),
                    "caption": self._truncate_text(str(item.get("caption") or ""), 500),
                    "row_count": item.get("row_count") if isinstance(item.get("row_count"), int) else None,
                    "sample_rows": rows,
                }
            )
        return tables

    def _project_presentation_table_rows(self, rows_payload: list[Any]) -> list[Any]:
        rows: list[Any] = []
        for row in rows_payload[:_PROJECT_PRESENTATION_MAX_TABLE_ROWS]:
            if isinstance(row, dict):
                trimmed: dict[str, str] = {}
                for key, value in list(row.items())[:6]:
                    trimmed[self._truncate_text(str(key), 120)] = self._truncate_text(str(value), 160)
                if trimmed:
                    rows.append(trimmed)
            elif isinstance(row, list):
                trimmed_row = [self._truncate_text(str(value), 160) for value in row[:6]]
                if trimmed_row:
                    rows.append(trimmed_row)
        return rows

    def _truncate_text(self, value: str, max_chars: int) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - 1)].rstrip() + "..."

    def _truncate_project_presentation_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        rendered = json.dumps(snapshot, ensure_ascii=False, default=str)
        if len(rendered) <= _PROJECT_PRESENTATION_MAX_CONTEXT_CHARS:
            return snapshot

        trimmed = dict(snapshot)
        trimmed["chat_threads"] = [
            {
                **thread,
                "items": list(thread.get("items", []))[:20],
            }
            for thread in list(trimmed.get("chat_threads", []))[:12]
            if isinstance(thread, dict)
        ]
        trimmed["explorations"] = [
            {
                **exploration,
                "annotations": list(exploration.get("annotations", []))[:12],
            }
            for exploration in list(trimmed.get("explorations", []))[:30]
            if isinstance(exploration, dict)
        ]
        trimmed["paper_comments"] = list(trimmed.get("paper_comments", []))[:60]
        trimmed["code_artifacts"] = [
            {
                **artifact,
                "content": self._truncate_text(str(artifact.get("content") or ""), 5000),
            }
            for artifact in list(trimmed.get("code_artifacts", []))[:8]
            if isinstance(artifact, dict)
        ]
        trimmed["research_materials"] = [
            {
                **material,
                "abstract": self._truncate_text(str(material.get("abstract") or ""), 1000),
                "tldr": self._truncate_text(str(material.get("tldr") or ""), 700),
                "summary_short": self._truncate_text(str(material.get("summary_short") or ""), 700),
                "selected_passages": [
                    {
                        **passage,
                        "selected_text": self._truncate_text(str(passage.get("selected_text") or ""), 700),
                        "nearby_text": self._truncate_text(str(passage.get("nearby_text") or ""), 500),
                        "note": self._truncate_text(str(passage.get("note") or ""), 400),
                    }
                    for passage in list(material.get("selected_passages", []))[:3]
                    if isinstance(passage, dict)
                ],
                "user_notes": [
                    self._truncate_text(str(note), 400)
                    for note in list(material.get("user_notes", []))[:3]
                ],
                "paper_comments": list(material.get("paper_comments", []))[:3],
                "figures": list(material.get("figures", []))[:3],
                "tables": list(material.get("tables", []))[:2],
            }
            for material in list(trimmed.get("research_materials", []))[:8]
            if isinstance(material, dict)
        ]
        if len(json.dumps(trimmed, ensure_ascii=False, default=str)) <= _PROJECT_PRESENTATION_MAX_CONTEXT_CHARS:
            return trimmed
        trimmed["chat_threads"] = [
            {
                **thread,
                "items": [
                    {
                        **item,
                        "content": self._truncate_text(str(item.get("content") or ""), 1800),
                    }
                    for item in list(thread.get("items", []))[:10]
                    if isinstance(item, dict)
                ],
            }
            for thread in list(trimmed.get("chat_threads", []))[:8]
            if isinstance(thread, dict)
        ]
        trimmed["paper_comments"] = list(trimmed.get("paper_comments", []))[:30]
        trimmed["code_artifacts"] = [
            {
                **artifact,
                "content": self._truncate_text(str(artifact.get("content") or ""), 2500),
            }
            for artifact in list(trimmed.get("code_artifacts", []))[:5]
            if isinstance(artifact, dict)
        ]
        trimmed["research_materials"] = [
            {
                **material,
                "abstract": self._truncate_text(str(material.get("abstract") or ""), 600),
                "tldr": self._truncate_text(str(material.get("tldr") or ""), 450),
                "summary_short": self._truncate_text(str(material.get("summary_short") or ""), 450),
                "selected_passages": [
                    {
                        **passage,
                        "selected_text": self._truncate_text(str(passage.get("selected_text") or ""), 500),
                        "nearby_text": self._truncate_text(str(passage.get("nearby_text") or ""), 300),
                        "note": self._truncate_text(str(passage.get("note") or ""), 240),
                    }
                    for passage in list(material.get("selected_passages", []))[:2]
                    if isinstance(passage, dict)
                ],
                "user_notes": [
                    self._truncate_text(str(note), 240)
                    for note in list(material.get("user_notes", []))[:2]
                ],
                "paper_comments": list(material.get("paper_comments", []))[:2],
                "figures": list(material.get("figures", []))[:2],
                "tables": list(material.get("tables", []))[:1],
            }
            for material in list(trimmed.get("research_materials", []))[:5]
            if isinstance(material, dict)
        ]
        return trimmed

    def generate_project_presentation(
        self,
        *,
        user_id: str,
        project_id: str,
        presentation_id: str,
        context_snapshot: dict[str, Any],
    ) -> ProjectPresentationResponse:
        existing = self.repository.get_project_presentation(  # type: ignore[attr-defined]
            user_id=user_id,
            project_id=project_id,
        )
        if existing is not None and existing.presentation_id == presentation_id and existing.status == "completed":
            logger.info(
                "Project presentation job already completed; skipping regeneration",
                extra={
                    "event": "project.presentation.already_completed",
                    "presentation_id": presentation_id,
                    "project_id": project_id,
                },
            )
            return self._project_presentation_with_share_url(existing)
        self.repository.set_project_presentation_running(presentation_id)  # type: ignore[attr-defined]
        self._update_project_presentation_progress(
            presentation_id,
            phase_key="making_bullet_points",
            phase_label="Making bullet points",
            detail="Reading the project history, papers, annotations, comments, chats, and code to find the presentation story.",
            percent=12,
            estimated_seconds_remaining=330,
        )
        user = self.repository.get_browser_user_by_id(user_id)  # type: ignore[attr-defined]
        if user is None:
            self.repository.fail_project_presentation(  # type: ignore[attr-defined]
                presentation_id,
                error="User not found",
            )
            raise PermanentError("User not found for project presentation")
        api_key_value, api_key_state = self._choose_openai_api_key(user)
        if not api_key_value:
            detail = (
                "Saved OpenAI API key is no longer valid"
                if api_key_state == "api_key_invalid"
                else "OpenAI API key is required"
            )
            self.repository.fail_project_presentation(presentation_id, error=detail)  # type: ignore[attr-defined]
            raise PermanentError(detail)

        try:
            logger.info(
                "Project presentation generation started",
                extra={
                    "event": "project.presentation.generate_started",
                    "presentation_id": presentation_id,
                    "project_id": project_id,
                },
            )
            prompt = self._project_presentation_prompt(context_snapshot)
            self._raise_if_current_job_canceled()
            response = self._chat_model_create_response(api_key=api_key_value, prompt=prompt)
            response_text = self._assistant_response_text(response)
            logger.info(
                "Project presentation model response received",
                extra={
                    "event": "project.presentation.model_completed",
                    "presentation_id": presentation_id,
                    "project_id": project_id,
                    "response_length": len(response_text),
                },
            )
            self._raise_if_current_job_canceled()
            self._update_project_presentation_progress(
                presentation_id,
                phase_key="drawing_diagrams",
                phase_label="Drawing diagrams",
                detail="Turning the slide plan into custom diagrams, visual code, and fallback artwork.",
                percent=54,
                estimated_seconds_remaining=180,
            )
            try:
                payload = self._parse_project_presentation_payload(response_text)
            except PermanentError as exc:
                logger.warning(
                    "Project presentation model payload was invalid; using fallback presentation",
                    extra={
                        "event": "project.presentation.payload_fallback",
                        "presentation_id": presentation_id,
                        "project_id": project_id,
                        "response_length": len(response_text),
                        "response_preview": self._truncate_text(response_text, 240),
                        "error": str(exc),
                    },
                )
                payload = {}
            project_name = str(context_snapshot.get("project", {}).get("name") or "Project")
            summary_markdown = str(payload.get("summary_markdown") or "").strip()
            visual_code = str(payload.get("visual_code") or "").strip()
            slides = self._normalize_project_presentation_slides(payload, context_snapshot)
            if not summary_markdown:
                summary_markdown = self._project_presentation_summary_from_slides(context_snapshot, slides)
            if not visual_code:
                visual_code = self._project_presentation_visual_from_slides(context_snapshot, slides)
            visible_slides = self._project_presentation_slides_with_image_fallbacks(
                context_snapshot,
                slides,
            )
            slide_payloads = [self._project_presentation_slide_storage_dict(slide) for slide in visible_slides]
            self._raise_if_current_job_canceled()
            self._update_project_presentation_progress(
                presentation_id,
                phase_key="assembling_presentation",
                phase_label="Assembling presentation",
                detail="Saving the slide text, visuals, share state, and the first viewable version.",
                percent=72,
                estimated_seconds_remaining=120,
            )
            completed = self.repository.complete_project_presentation(  # type: ignore[attr-defined]
                presentation_id,
                visual_code=visual_code,
                summary_markdown=summary_markdown,
                slides=slide_payloads,
                audio_storage_path="",
                audio_content_type="audio/mpeg",
            )
            if completed is None:
                raise PermanentError("Presentation record disappeared")
            if completed.status == "canceled":
                raise JobCanceled(f"Job canceled while saving presentation {presentation_id}")
            completed = self._project_presentation_with_share_url(completed)
            logger.info(
                "Project presentation content completed",
                extra={
                    "event": "project.presentation.content_completed",
                    "presentation_id": presentation_id,
                    "project_id": project_id,
                    "slide_count": len(visible_slides),
                },
            )
            self._send_project_presentation_ready_email(
                user=user,
                project_name=project_name,
                presentation=completed,
            )
            if any(slide.visual_type == "image" and slide.image_prompt.strip() for slide in slides):
                self._raise_if_current_job_canceled()
                self._update_project_presentation_progress(
                    presentation_id,
                    phase_key="generating_images",
                    phase_label="Drawing generated images",
                    detail="Trying generated research artwork for image-based slides. Code diagrams are already available as fallback.",
                    percent=78,
                    estimated_seconds_remaining=120,
                )
                visible_slides = self._generate_and_store_project_presentation_slide_images(
                    api_key=api_key_value,
                    presentation_id=presentation_id,
                    slides=slides,
                )
                slide_payloads = [
                    self._project_presentation_slide_storage_dict(slide)
                    for slide in visible_slides
                ]
                completed_with_images = self.repository.complete_project_presentation(  # type: ignore[attr-defined]
                    presentation_id,
                    visual_code=visual_code,
                    summary_markdown=summary_markdown,
                    slides=slide_payloads,
                    audio_storage_path="",
                    audio_content_type="audio/mpeg",
                )
                if completed_with_images is not None:
                    if completed_with_images.status == "canceled":
                        raise JobCanceled(f"Job canceled while saving presentation images {presentation_id}")
                    completed = self._project_presentation_with_share_url(completed_with_images)
            audio_storage_path = ""
            audio_content_type = "audio/mpeg"
            elevenlabs_api_key = self._get_elevenlabs_api_key_optional(user)
            if elevenlabs_api_key:
                try:
                    self._raise_if_current_job_canceled()
                    self._update_project_presentation_progress(
                        presentation_id,
                        phase_key="generating_audio",
                        phase_label="Generating audio walkthrough",
                        detail="Writing and producing the two-person audio explainer for the presentation.",
                        percent=88,
                        estimated_seconds_remaining=120,
                    )
                    logger.info(
                        "Project presentation audio generation started",
                        extra={
                            "event": "project.presentation.audio_started",
                            "presentation_id": presentation_id,
                            "project_id": project_id,
                        },
                    )
                    audio_storage_path, audio_content_type = self._generate_and_store_project_presentation_audio(
                        openai_api_key=api_key_value,
                        elevenlabs_api_key=elevenlabs_api_key,
                        presentation_id=presentation_id,
                        context_snapshot=context_snapshot,
                        summary_markdown=summary_markdown,
                    )
                except Exception:
                    logger.exception(
                        "Project presentation audio generation failed; continuing without audio",
                        extra={
                            "event": "project.presentation.audio_fallback",
                            "presentation_id": presentation_id,
                            "project_id": project_id,
                        },
                    )
                else:
                    completed_with_audio = self.repository.complete_project_presentation(  # type: ignore[attr-defined]
                        presentation_id,
                        visual_code=visual_code,
                        summary_markdown=summary_markdown,
                        slides=slide_payloads,
                        audio_storage_path=audio_storage_path,
                        audio_content_type=audio_content_type,
                    )
                    if completed_with_audio is not None:
                        if completed_with_audio.status == "canceled":
                            raise JobCanceled(f"Job canceled while saving presentation audio {presentation_id}")
                        completed = self._project_presentation_with_share_url(completed_with_audio)
                        logger.info(
                            "Project presentation audio completed",
                            extra={
                                "event": "project.presentation.audio_completed",
                                "presentation_id": presentation_id,
                                "project_id": project_id,
                            },
                        )
            self._raise_if_current_job_canceled()
            progress_complete = self._update_project_presentation_progress(
                presentation_id,
                phase_key="complete",
                phase_label="Presentation ready",
                detail="The presentation is ready to view and share.",
                percent=100,
                estimated_seconds_remaining=0,
            )
            if progress_complete is not None:
                completed = self._project_presentation_with_share_url(progress_complete)
            return completed
        except JobCanceled:
            raise
        except Exception as exc:
            self._raise_if_current_job_canceled()
            self.repository.fail_project_presentation(presentation_id, error=str(exc))  # type: ignore[attr-defined]
            raise

    def _update_project_presentation_progress(
        self,
        presentation_id: str,
        *,
        phase_key: str,
        phase_label: str,
        detail: str,
        percent: int,
        estimated_seconds_remaining: int,
    ) -> ProjectPresentationResponse | None:
        updater = getattr(self.repository, "update_project_presentation_progress", None)
        if updater is None:
            return None
        try:
            return updater(
                presentation_id,
                phase_key=phase_key,
                phase_label=phase_label,
                detail=detail,
                percent=percent,
                estimated_seconds_remaining=estimated_seconds_remaining,
            )
        except Exception:
            logger.exception(
                "Failed to update project presentation progress",
                extra={"event": "project.presentation.progress_update_failed", "presentation_id": presentation_id},
            )
            return None

    def _project_presentation_prompt(self, context_snapshot: dict[str, Any]) -> Prompt:
        return Prompt(
            input=[
                {
                    "type": "message",
                    "role": "user",
                    "content": json.dumps(context_snapshot, ensure_ascii=False, default=str),
                }
            ],
            base_instructions=(
                "Research presentation designer. First infer the key bullet points from the entire "
                "project history: all chats, explorations, annotations, explanations, challenges, paper "
                "comments, cited papers, selected passages, figures, tables, and `code_artifacts` written "
                "inside the project. Then turn each bullet point into one slide. Return those slides in "
                "`slides` as the main product. Each bullet point and slide must state exactly one direct "
                "research fact or conclusion. Use plain technical language; no wordplay, no clever titles, "
                "no rhetorical questions, no metaphors, and no vague theme labels. Make the slides heavy "
                "with research conclusions, evidence, comparisons, caveats, and implications. Each slide "
                "must have a title and description whose combined length is at most 80 words. The title "
                "must itself be the fact or conclusion, not a teaser. Treat `research_materials` as the "
                "source of truth for science "
                "and treat `chat_threads`, `explorations`, `paper_comments`, and `code_artifacts` as clues "
                "about what the user cared about. Do not write meta commentary about the Paperbase project, "
                "chat history, number of papers, number of comments, notes, or explorations unless a count "
                "is itself a scientific result. The presentation must teach the actual research material, "
                "technical ideas, evidence, code outputs, disagreements, caveats, and implications to an "
                "external audience. `summary_markdown` is a legacy concise briefing outline of the same "
                "direct conclusions; write it like research findings, not narrative copy. "
                "slides. `visual_code` is a legacy fallback containing the first code slide visual."
                "\n\n"
                "For each slide, choose exactly one visual mode. Use `visual_type: \"code\"` when an "
                "interactive or data-driven custom visual is better; fill `visual_code` with plain browser "
                "JavaScript that builds the visual from data arrays, D3 scales, SVG marks, axes, legends, "
                "and annotations. Use `visual_type: \"image\"` when a generated art image would better explain "
                "the concept; leave `visual_code` empty and write an `image_prompt` for GPT Image 2. Image "
                "prompts must ask for an editorial research infographic with on-brand restrained colors, "
                "minimal readable labels, and no tiny body copy because image models can struggle with exact "
                "text rendering. Prefer code for charts, quantitative comparisons, precise diagrams, and "
                "anything with exact text. Use images only for evocative conceptual maps, mechanism scenes, "
                "or artsy visual metaphors."
                "\n\n"
                "Code visuals must be art-directed infographics that explain the research itself, closer to "
                "a clean print/editorial research spread, custom editorial data poster, or museum wall "
                "graphic than a slide. They must be high information density and must directly support the "
                "slide's stated conclusion with visible chart evidence, comparison, trend, mechanism, or "
                "evidence map. Prefer real chart/diagram systems over manually positioned decorative boxes: "
                "Use D3 for rich data-driven charting with scales, axes, force layouts, timelines, "
                "annotations, Sankey-like flows, "
                "scatter/line/bar/slope charts, heatmaps, network layouts, confidence intervals, or "
                "small-multiple comparisons when useful; React can own controls and state. Every visual "
                "must include a concise conclusion callout that points at the decisive evidence. Use the "
                "Paperbase brand direction: warm paper/ink foundation, restrained "
                "blue accents, terracotta #99582A for emphasis, crisp 4px radii, thin rules, strong "
                "typography, and no generic dashboard chrome. The visual must fill 100% width and 100% "
                "height of the iframe and must fit inside the iframe viewport without body/page scrolling, "
                "clipping, or overlap. Use responsive measurements from the provided "
                "`paperbasePresentation.size()` / `paperbasePresentation.onResize(callback)` helper, "
                "ResizeObserver, SVG viewBox, or window dimensions so charts re-render when the project "
                "sidebar opens or closes; never rely on fixed oversized canvases, fixed pixel-only layouts, "
                "manually hard-coded node grids, min-h-screen/min-w-screen, wide CSS grids, or off-canvas "
                "columns. Keep text to short labels and tooltips: no large paragraphs, "
                "no text blocks over 14 words, and all labels must be clamped or abbreviated inside the "
                "visible bounds. Avoid decorative cards, spreadsheet-like matrices of boxes as the main "
                "visual, filler copy, marketing layout, and generic dashboard UI. If interaction helps, add "
                "hover/click states to reveal details, switch concepts, or focus evidence points. The visual "
                "code must run inside a browser iframe where React, ReactDOM, d3, and Tailwind are available "
                "as globals. Do not use JSX, imports, external network calls, or markdown inside visual "
                "code. Render into document.getElementById('root')."
            ),
            tools=[],
            tool_choice=None,
            parallel_tool_calls=False,
            text_format=_PROJECT_PRESENTATION_TEXT_FORMAT,
        )

    def _assistant_response_text(self, response: ResponseEnvelope) -> str:
        return "\n".join(
            item.content_text
            for item in response.items
            if item.item_type in {"message", "assistant_message"}
            and item.role in {"", "assistant"}
            and item.content_text
        ).strip()

    def _parse_project_presentation_payload(self, text: str) -> dict[str, Any]:
        payload_text = text.strip()
        if payload_text.startswith("```"):
            payload_text = re.sub(r"^```(?:json)?\s*", "", payload_text, flags=re.IGNORECASE)
            payload_text = re.sub(r"\s*```$", "", payload_text).strip()
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            start = payload_text.find("{")
            end = payload_text.rfind("}")
            if start < 0 or end <= start:
                raise PermanentError("Presentation model did not return JSON")
            payload = json.loads(payload_text[start : end + 1])
        if not isinstance(payload, dict):
            raise PermanentError("Presentation model returned an invalid payload")
        return payload

    def _normalize_project_presentation_slides(
        self,
        payload: dict[str, Any],
        context_snapshot: dict[str, Any],
    ) -> list[ProjectPresentationSlideResponse]:
        raw_slides = payload.get("slides")
        slides: list[ProjectPresentationSlideResponse] = []
        if isinstance(raw_slides, list):
            image_slide_count = 0
            used_ids: set[str] = set()
            for index, raw_slide in enumerate(raw_slides[:_PROJECT_PRESENTATION_MAX_SLIDES]):
                if not isinstance(raw_slide, dict):
                    continue
                title = self._truncate_text(str(raw_slide.get("title") or f"Slide {index + 1}"), 140)
                description = self._truncate_slide_words(title, str(raw_slide.get("description") or ""))
                slide_id = self._project_presentation_slide_id(
                    str(raw_slide.get("slide_id") or title or f"slide-{index + 1}"),
                    index=index,
                    used_ids=used_ids,
                )
                visual_type = str(raw_slide.get("visual_type") or "code").strip().lower()
                visual_code = str(raw_slide.get("visual_code") or "").strip()
                image_prompt = str(raw_slide.get("image_prompt") or "").strip()
                if visual_type == "image" and image_prompt and image_slide_count < _PROJECT_PRESENTATION_MAX_IMAGE_SLIDES:
                    image_slide_count += 1
                    slides.append(
                        ProjectPresentationSlideResponse(
                            slide_id=slide_id,
                            title=title,
                            description=description,
                            visual_type="image",
                            visual_code="",
                            image_prompt=self._truncate_text(image_prompt, 2000),
                        )
                    )
                    continue
                if not visual_code:
                    visual_code = self._default_project_presentation_slide_visual(
                        context_snapshot,
                        title=title,
                        description=description,
                        index=index,
                    )
                slides.append(
                    ProjectPresentationSlideResponse(
                        slide_id=slide_id,
                        title=title,
                        description=description,
                        visual_type="code",
                        visual_code=visual_code,
                        image_prompt="",
                    )
                )
        if slides:
            return slides
        return self._default_project_presentation_slides(context_snapshot)

    def _project_presentation_slides_with_image_fallbacks(
        self,
        context_snapshot: dict[str, Any],
        slides: list[ProjectPresentationSlideResponse],
    ) -> list[ProjectPresentationSlideResponse]:
        visible: list[ProjectPresentationSlideResponse] = []
        for index, slide in enumerate(slides):
            if slide.visual_type == "image" and slide.image_prompt.strip():
                visible.append(
                    slide.model_copy(
                        update={
                            "visual_type": "code",
                            "visual_code": self._default_project_presentation_slide_visual(
                                context_snapshot,
                                title=slide.title,
                                description=slide.description,
                                index=index,
                            ),
                            "image_storage_path": "",
                        }
                    )
                )
                continue
            visible.append(slide)
        return visible

    def _truncate_slide_words(self, title: str, description: str) -> str:
        title_words = re.findall(r"\S+", title)
        description_words = re.findall(r"\S+", description)
        available = max(8, 80 - len(title_words))
        if len(description_words) <= available:
            return self._truncate_text(description, 520)
        return " ".join(description_words[:available]).rstrip(".,;:") + "..."

    def _project_presentation_slide_id(self, value: str, *, index: int, used_ids: set[str]) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:48] or f"slide-{index + 1}"
        slide_id = base
        suffix = 2
        while slide_id in used_ids:
            slide_id = f"{base}-{suffix}"
            suffix += 1
        used_ids.add(slide_id)
        return slide_id

    def _project_presentation_summary_from_slides(
        self,
        context_snapshot: dict[str, Any],
        slides: list[ProjectPresentationSlideResponse],
    ) -> str:
        if not slides:
            return self._default_project_presentation_summary(context_snapshot)
        project = context_snapshot.get("project", {}) if isinstance(context_snapshot.get("project"), dict) else {}
        project_name = str(project.get("name") or "Project")
        lines = [f"# {project_name}", "", "## Presentation Outline", ""]
        for slide in slides:
            lines.append(f"- **{slide.title}:** {slide.description}")
        return "\n".join(lines).strip() + "\n"

    def _project_presentation_visual_from_slides(
        self,
        context_snapshot: dict[str, Any],
        slides: list[ProjectPresentationSlideResponse],
    ) -> str:
        for slide in slides:
            if slide.visual_type == "code" and slide.visual_code.strip():
                return slide.visual_code
        if slides:
            return self._default_project_presentation_slide_visual(
                context_snapshot,
                title=slides[0].title,
                description=slides[0].description,
                index=0,
            )
        return self._default_project_presentation_visual(context_snapshot)

    def _default_project_presentation_slides(
        self,
        context_snapshot: dict[str, Any],
    ) -> list[ProjectPresentationSlideResponse]:
        project = context_snapshot.get("project", {}) if isinstance(context_snapshot.get("project"), dict) else {}
        project_name = str(project.get("name") or "Project")
        materials = [
            material
            for material in list(context_snapshot.get("research_materials", []))
            if isinstance(material, dict)
        ]
        if not materials:
            description = (
                "Add explored papers, annotations, comments, chats, or code artifacts, then regenerate "
                "to create a research presentation."
            )
            return [
                ProjectPresentationSlideResponse(
                    slide_id="empty-research-map",
                    title=project_name,
                    description=description,
                    visual_type="code",
                    visual_code=self._default_project_presentation_slide_visual(
                        context_snapshot,
                        title=project_name,
                        description=description,
                        index=0,
                    ),
                    image_prompt="",
                )
            ]
        slides: list[ProjectPresentationSlideResponse] = []
        used_ids: set[str] = set()
        for index, material in enumerate(materials[:_PROJECT_PRESENTATION_MAX_SLIDES]):
            title = self._truncate_text(str(material.get("title") or f"Research point {index + 1}"), 120)
            description = self._truncate_slide_words(title, self._project_presentation_material_blurb(material))
            slide_id = self._project_presentation_slide_id(title, index=index, used_ids=used_ids)
            slides.append(
                ProjectPresentationSlideResponse(
                    slide_id=slide_id,
                    title=title,
                    description=description,
                    visual_type="code",
                    visual_code=self._default_project_presentation_slide_visual(
                        context_snapshot,
                        title=title,
                        description=description,
                        index=index,
                    ),
                    image_prompt="",
                )
            )
        return slides

    def _generate_and_store_project_presentation_slide_images(
        self,
        *,
        api_key: str,
        presentation_id: str,
        slides: list[ProjectPresentationSlideResponse],
    ) -> list[ProjectPresentationSlideResponse]:
        updated: list[ProjectPresentationSlideResponse] = []
        for index, slide in enumerate(slides):
            if slide.visual_type != "image" or not slide.image_prompt.strip():
                updated.append(slide)
                continue
            self._raise_if_current_job_canceled()
            try:
                logger.info(
                    "Project presentation image generation started",
                    extra={
                        "event": "project.presentation.image_started",
                        "presentation_id": presentation_id,
                        "slide_id": slide.slide_id,
                    },
                )
                image = self._request_openai_presentation_image(api_key=api_key, prompt=slide.image_prompt)
                self._raise_if_current_job_canceled()
                storage_path = f"project-presentations/{presentation_id}/slides/{slide.slide_id}.png"
                self.object_store.put_bytes(storage_path, image, content_type="image/png")
                updated.append(slide.model_copy(update={"image_storage_path": storage_path}))
                logger.info(
                    "Project presentation image generation completed",
                    extra={
                        "event": "project.presentation.image_completed",
                        "presentation_id": presentation_id,
                        "slide_id": slide.slide_id,
                    },
                )
            except JobCanceled:
                raise
            except Exception as exc:
                logger.warning(
                    "Project presentation image generation failed; falling back to code visual",
                    extra={
                        "event": "project.presentation.image_fallback",
                        "presentation_id": presentation_id,
                        "slide_id": slide.slide_id,
                        "error": str(exc),
                    },
                )
                updated.append(
                    slide.model_copy(
                        update={
                            "visual_type": "code",
                            "visual_code": self._default_project_presentation_slide_visual(
                                {},
                                title=slide.title,
                                description=slide.description,
                                index=index,
                            ),
                            "image_prompt": "",
                            "image_storage_path": "",
                        }
                    )
                )
        return updated

    def _request_openai_presentation_image(self, *, api_key: str, prompt: str) -> bytes:
        response = requests.post(
            f"{self.settings.openai_base_url.rstrip('/')}/v1/images/generations",
            headers={
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            json={
                "model": _PROJECT_PRESENTATION_IMAGE_MODEL,
                "prompt": prompt,
                "size": "1536x1024",
                "quality": "medium",
            },
            timeout=_PROJECT_PRESENTATION_IMAGE_TIMEOUT_SECONDS,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = self._truncate_text(response.text, 600)
            raise requests.HTTPError(f"{exc}. OpenAI image generation error: {detail}", response=response) from exc
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        first = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
        image_base64 = str(first.get("b64_json") or "")
        if not image_base64:
            raise PermanentError("OpenAI image response did not include b64_json")
        return base64.b64decode(image_base64)

    def _generate_and_store_project_presentation_audio(
        self,
        *,
        openai_api_key: str,
        elevenlabs_api_key: str,
        presentation_id: str,
        context_snapshot: dict[str, Any],
        summary_markdown: str,
    ) -> tuple[str, str]:
        script_turns = self._generate_project_presentation_audio_script(
            api_key=openai_api_key,
            context_snapshot=context_snapshot,
            summary_markdown=summary_markdown,
        )
        audio_segments: list[bytes] = []
        for segment in self._project_presentation_audio_segments(script_turns):
            self._raise_if_current_job_canceled()
            segment_audio = self._request_elevenlabs_tts(
                api_key=elevenlabs_api_key,
                voice_id=segment["voice_id"],
                text=segment["text"],
            )
            self._raise_if_current_job_canceled()
            audio_segments.append(segment_audio)
        if not audio_segments:
            raise PermanentError("Presentation audio script was empty")
        audio = self._stitch_audio_segments(audio_segments)
        storage_path = f"project-presentations/{presentation_id}/audio/presentation.mp3"
        self.object_store.put_bytes(storage_path, audio, content_type="audio/mpeg")
        return storage_path, "audio/mpeg"

    def _generate_project_presentation_audio_script(
        self,
        *,
        api_key: str,
        context_snapshot: dict[str, Any],
        summary_markdown: str,
    ) -> list[dict[str, str]]:
        prompt = Prompt(
            input=[
                {
                    "type": "message",
                    "role": "user",
                    "content": json.dumps(
                        {
                            "presentation_summary_markdown": summary_markdown,
                            "project_context": context_snapshot,
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                }
            ],
            base_instructions=(
                "Project presentation audio script writer. Write a `turns` array. Each turn must "
                "have `speaker` equal to `noob` or `expert`, and `text` as natural spoken dialogue. "
                "Write a 5 to 10 minute two-person explainer about the research substance and visual "
                "story in the presentation. The noob knows nothing and asks simple questions. The "
                "industry expert explains the actual concepts, evidence, comparisons, caveats, and "
                "implications to a total beginner. Do not make the app workflow, chat history, number "
                "of papers, comments, notes, or explorations the topic. Mention user interactions only "
                "when they point to a substantive research question. Do not include stage directions, "
                "markdown, or speaker labels inside `text`. Keep each turn concise enough for "
                "text-to-speech."
            ),
            tools=[],
            tool_choice=None,
            parallel_tool_calls=False,
            text_format=_PROJECT_PRESENTATION_AUDIO_TEXT_FORMAT,
        )
        response = self._chat_model_create_response(api_key=api_key, prompt=prompt)
        payload = self._parse_project_presentation_payload(self._assistant_response_text(response))
        raw_turns = payload.get("turns") or payload.get("dialogue") or []
        turns: list[dict[str, str]] = []
        if isinstance(raw_turns, list):
            for raw_turn in raw_turns:
                if not isinstance(raw_turn, dict):
                    continue
                speaker = str(raw_turn.get("speaker") or "").strip().lower()
                if speaker not in {"noob", "expert"}:
                    speaker = "expert"
                text = str(raw_turn.get("text") or raw_turn.get("line") or "").strip()
                if text:
                    turns.append({"speaker": speaker, "text": text})
        if turns:
            return turns
        return [
            {
                "speaker": "noob",
                "text": "I am completely new to this project. What is the main point I should understand?",
            },
            {
                "speaker": "expert",
                "text": (
                    "Start with the presentation summary. "
                    f"{self._plain_text_excerpt(summary_markdown, max_chars=3600)}"
                ),
            },
        ]

    def _project_presentation_audio_segments(self, turns: list[dict[str, str]]) -> list[dict[str, str]]:
        voices = {
            "noob": "21m00Tcm4TlvDq8ikWAM",
            "expert": "ErXwobaYiN019PkySvjV",
        }
        segments: list[dict[str, str]] = []
        for turn in turns:
            speaker = turn.get("speaker", "expert")
            voice_id = voices.get(speaker, voices["expert"])
            for text in self._split_tts_text(turn.get("text", ""), max_chars=4500):
                segments.append({"speaker": speaker, "voice_id": voice_id, "text": text})
        return segments

    def _split_tts_text(self, text: str, *, max_chars: int) -> list[str]:
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            return []
        if len(normalized) <= max_chars:
            return [normalized]
        pieces = re.split(r"(?<=[.!?])\s+", normalized)
        chunks: list[str] = []
        current = ""
        for piece in pieces:
            if not piece:
                continue
            if len(piece) > max_chars:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(piece[index : index + max_chars] for index in range(0, len(piece), max_chars))
                continue
            candidate = f"{current} {piece}".strip()
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = piece
        if current:
            chunks.append(current)
        return chunks

    def _request_elevenlabs_tts(self, *, api_key: str, voice_id: str, text: str) -> bytes:
        payload = {
            "text": text,
            "model_id": "eleven_flash_v2_5",
            "voice_settings": {
                "stability": 0.56,
                "similarity_boost": 0.78,
                "style": 0.12,
                "use_speaker_boost": True,
            },
        }
        request = urllib_request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{quote(voice_id, safe='')}"
            "?output_format=mp3_44100_128",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": api_key,
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=_PROJECT_PRESENTATION_TTS_TIMEOUT_SECONDS) as response:
                data = response.read()
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"ElevenLabs audio generation failed ({exc.code}): {detail}") from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(f"ElevenLabs audio generation failed: {exc.reason}") from exc
        if not data:
            raise RuntimeError("ElevenLabs audio generation returned empty audio")
        return data

    def _stitch_audio_segments(self, segments: list[bytes]) -> bytes:
        if len(segments) == 1:
            return segments[0]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest_lines: list[str] = []
            for index, segment in enumerate(segments):
                segment_path = tmp_path / f"segment_{index:04d}.mp3"
                segment_path.write_bytes(segment)
                manifest_lines.append(f"file '{segment_path.name}'")
            manifest_path = tmp_path / "segments.txt"
            manifest_path.write_text("\n".join(manifest_lines), encoding="utf-8")
            output_path = tmp_path / "presentation.mp3"
            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        str(manifest_path),
                        "-c",
                        "copy",
                        str(output_path),
                    ],
                    cwd=tmp_path,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                return output_path.read_bytes()
            except (OSError, subprocess.CalledProcessError):
                logger.exception(
                    "Falling back to direct MP3 concatenation for project presentation audio",
                    extra={"event": "project.presentation.audio_stitch_fallback"},
                )
                return b"".join(segments)

    def _plain_text_excerpt(self, text: str, *, max_chars: int) -> str:
        plain = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
        plain = re.sub(r"[#*_`>|\\-]+", " ", plain)
        plain = re.sub(r"\s+", " ", plain).strip()
        return plain[:max_chars].strip()

    def _default_project_presentation_summary(self, context_snapshot: dict[str, Any]) -> str:
        project = context_snapshot.get("project", {}) if isinstance(context_snapshot.get("project"), dict) else {}
        materials = [
            material
            for material in list(context_snapshot.get("research_materials", []))
            if isinstance(material, dict)
        ]
        project_name = str(project.get("name") or "Project")
        if not materials:
            return (
                f"# {project_name}\n\n"
                "## Research Thesis\n\n"
                "The available material does not include enough paper text, selected passages, or notes "
                "to infer a defensible research thesis yet.\n\n"
                "## Core Material\n\n"
                "Add explored papers with abstracts, selected passages, figure captions, table excerpts, "
                "or research notes, then regenerate the presentation.\n"
            )
        primary = materials[0]
        thesis = self._project_presentation_material_blurb(primary)
        if not thesis:
            thesis = "The available research material points to a focused topic, but needs a clearer extracted claim."
        concept_lines: list[str] = []
        evidence_lines: list[str] = []
        for material in materials[:5]:
            title = self._truncate_text(str(material.get("title") or material.get("paper_id") or "Research item"), 120)
            blurb = self._project_presentation_material_blurb(material)
            if blurb:
                concept_lines.append(f"- **{title}:** {blurb}")
            for passage in list(material.get("selected_passages") or [])[:2]:
                if not isinstance(passage, dict):
                    continue
                selected = self._truncate_text(str(passage.get("selected_text") or passage.get("nearby_text") or ""), 220)
                note = self._truncate_text(str(passage.get("note") or ""), 160)
                if selected:
                    evidence_lines.append(f"- **{title}:** {selected}" + (f" _Note: {note}_" if note else ""))
            for figure in list(material.get("figures") or [])[:1]:
                if isinstance(figure, dict) and figure.get("caption"):
                    evidence_lines.append(
                        f"- **{title} figure:** {self._truncate_text(str(figure.get('caption') or ''), 220)}"
                    )
            for table in list(material.get("tables") or [])[:1]:
                if isinstance(table, dict) and table.get("caption"):
                    evidence_lines.append(
                        f"- **{title} table:** {self._truncate_text(str(table.get('caption') or ''), 220)}"
                    )
        concept_text = "\n".join(concept_lines) or "- The research material needs stronger extracted concepts."
        evidence_text = "\n".join(evidence_lines[:8]) or "- No specific passages, figures, or tables were available."
        return (
            f"# {project_name}\n\n"
            "## Research Thesis\n\n"
            f"{thesis}\n\n"
            "## Core Concepts\n\n"
            f"{concept_text}\n\n"
            "## Evidence To Present\n\n"
            f"{evidence_text}\n\n"
            "## Caveats\n\n"
            "This fallback briefing uses only the extracted paper metadata and saved excerpts. Regenerate "
            "with more selected passages or indexed paper artifacts for a more complete research narrative.\n"
        )

    def _project_presentation_material_blurb(self, material: dict[str, Any]) -> str:
        for key in ("summary_short", "tldr", "abstract"):
            value = self._truncate_text(str(material.get(key) or ""), 360)
            if value:
                return value
        for passage in list(material.get("selected_passages") or []):
            if not isinstance(passage, dict):
                continue
            value = self._truncate_text(str(passage.get("selected_text") or passage.get("nearby_text") or ""), 280)
            if value:
                return value
        for note in list(material.get("user_notes") or []):
            value = self._truncate_text(str(note), 280)
            if value:
                return value
        return ""

    def _default_project_presentation_slide_visual(
        self,
        context_snapshot: dict[str, Any],
        *,
        title: str,
        description: str,
        index: int,
    ) -> str:
        project = context_snapshot.get("project", {}) if isinstance(context_snapshot.get("project"), dict) else {}
        project_name = str(project.get("name") or "Project")
        safe_data = json.dumps(
            {
                "projectName": project_name,
                "title": self._truncate_text(title, 160),
                "description": self._truncate_text(description, 520),
                "index": index,
            },
            ensure_ascii=False,
        )
        return (
            "const root = document.getElementById('root');"
            f"const data = {safe_data};"
            "function App(){"
            "const accent=['#99582A','#1e7ce8','#57b8a6','#e0a83b'][data.index%4];"
            "return React.createElement('div',{className:'h-full w-full overflow-hidden bg-[#f6f0e8] text-[#1b1611]'},"
            "React.createElement('svg',{className:'h-full w-full',viewBox:'0 0 1000 760',preserveAspectRatio:'none'},"
            "React.createElement('rect',{width:1000,height:760,fill:'#f6f0e8'}),"
            "React.createElement('path',{d:'M-40 590 C180 420 214 190 430 250 C650 312 694 112 1040 188',fill:'none',stroke:accent,strokeWidth:92,strokeOpacity:.18,strokeLinecap:'round'}),"
            "React.createElement('path',{d:'M60 606 C238 470 318 286 506 330 C692 372 774 254 948 302',fill:'none',stroke:accent,strokeWidth:5,strokeOpacity:.82,strokeLinecap:'round'}),"
            "React.createElement('circle',{cx:500,cy:382,r:122,fill:'#1b1611',fillOpacity:.05,stroke:'#1b1611',strokeOpacity:.22}),"
            "[0,1,2,3,4,5].map((item)=>React.createElement('circle',{key:item,cx:250+item*96,cy:180+Math.sin(item+data.index)*42,r:10+item*2,fill:accent,fillOpacity:.35+item*.07})),"
            "React.createElement('text',{x:72,y:98,fontSize:13,fontWeight:700,letterSpacing:3,fill:accent},'RESEARCH SLIDE'),"
            "React.createElement('text',{x:72,y:168,fontSize:52,fontWeight:650,fill:'#1b1611'},data.title.slice(0,42)),"
            "React.createElement('foreignObject',{x:72,y:214,width:380,height:170},"
            "React.createElement('div',{style:{font:'16px/1.45 Georgia,serif',color:'#4b4238'}},data.description)),"
            "React.createElement('text',{x:72,y:696,fontSize:12,fill:'#756b60'},data.projectName)));"
            "}"
            "ReactDOM.createRoot(root).render(React.createElement(App));"
        )

    def _default_project_presentation_visual(self, context_snapshot: dict[str, Any]) -> str:
        project = context_snapshot.get("project", {}) if isinstance(context_snapshot.get("project"), dict) else {}
        project_name = str(project.get("name") or "Project")
        materials = [
            material
            for material in list(context_snapshot.get("research_materials", []))
            if isinstance(material, dict)
        ]
        visual_materials = []
        for material in materials[:5]:
            evidence = []
            for passage in list(material.get("selected_passages") or [])[:2]:
                if isinstance(passage, dict):
                    text = self._truncate_text(str(passage.get("selected_text") or passage.get("nearby_text") or ""), 160)
                    if text:
                        evidence.append(text)
            for figure in list(material.get("figures") or [])[:1]:
                if isinstance(figure, dict) and figure.get("caption"):
                    evidence.append(self._truncate_text(str(figure.get("caption") or ""), 160))
            visual_materials.append(
                {
                    "title": self._truncate_text(
                        str(material.get("title") or material.get("paper_id") or "Research item"),
                        80,
                    ),
                    "subtitle": self._truncate_text(
                        str(material.get("primary_category") or material.get("published") or "Research material"),
                        80,
                    ),
                    "summary": self._project_presentation_material_blurb(material),
                    "evidence": evidence[:3],
                }
            )
        safe_data = json.dumps({"projectName": project_name, "materials": visual_materials})
        return (
            "const root = document.getElementById('root');"
            f"const data = {safe_data};"
            "const {useState} = React;"
            "function short(value,max){value=String(value||'');return value.length>max?value.slice(0,max-1)+'...':value;}"
            "function pick(value,fallback){return String(value||fallback||'').trim();}"
            "function App(){"
            "const fallback={title:data.projectName,subtitle:'Research field',summary:'Add paper abstracts, selected passages, figures, or table excerpts, then regenerate for a richer research visualization.',evidence:[]};"
            "const materials=(data.materials&&data.materials.length?data.materials:[fallback]);"
            "const [active,setActive]=useState(0);"
            "const current=materials[Math.min(active,materials.length-1)]||fallback;"
            "const palette=['#e85d4f','#57b8a6','#f0b94f','#4f77c8','#99582a','#6b5dd3'];"
            "const angle=d3.scaleLinear().domain([0,Math.max(materials.length-1,1)]).range([-124,124]);"
            "const marks=materials.map((material,index)=>{const a=angle(index)*Math.PI/180;const r=190+(index%2)*48;return {material,index,x:500+Math.cos(a)*r,y:398+Math.sin(a)*r,color:palette[index%palette.length]};});"
            "return React.createElement('div',{className:'h-full w-full overflow-hidden bg-[#15120f] p-3 text-[#1b1611]'},"
            "React.createElement('main',{className:'relative h-full w-full overflow-hidden bg-[#fbf7ed]'},"
            "React.createElement('svg',{className:'absolute inset-0 h-full w-full',viewBox:'0 0 1000 760',preserveAspectRatio:'none'},"
            "React.createElement('defs',null,"
            "React.createElement('linearGradient',{id:'wash',x1:'0%',x2:'100%',y1:'0%',y2:'100%'},React.createElement('stop',{offset:'0%',stopColor:'#fffaf0'}),React.createElement('stop',{offset:'100%',stopColor:'#ece3d4'}))),"
            "React.createElement('rect',{x:0,y:0,width:1000,height:760,fill:'url(#wash)'}),"
            "React.createElement('path',{d:'M72 582 C220 456 278 164 486 222 C696 282 702 520 928 394',fill:'none',stroke:'#1b1611',strokeOpacity:.14,strokeWidth:38,strokeLinecap:'round'}),"
            "React.createElement('path',{d:'M80 612 C260 500 320 214 518 256 C704 296 736 554 936 420',fill:'none',stroke:'#57b8a6',strokeOpacity:.45,strokeWidth:6,strokeLinecap:'round'}),"
            "React.createElement('circle',{cx:500,cy:398,r:108,fill:'#1b1611',fillOpacity:.05,stroke:'#1b1611',strokeOpacity:.22,strokeWidth:1}),"
            "React.createElement('circle',{cx:500,cy:398,r:16,fill:'#1b1611'}),"
            "marks.map(mark=>React.createElement('g',{key:mark.index,onMouseEnter:()=>setActive(mark.index),onClick:()=>setActive(mark.index),style:{cursor:'pointer'}},"
            "React.createElement('line',{x1:500,y1:398,x2:mark.x,y2:mark.y,stroke:mark.color,strokeOpacity:mark.index===active ? .9 : .32,strokeWidth:mark.index===active ? 4 : 2}),"
            "React.createElement('circle',{cx:mark.x,cy:mark.y,r:mark.index===active ? 34 : 25,fill:mark.color,fillOpacity:mark.index===active ? .96 : .74,stroke:'#1b1611',strokeWidth:1}),"
            "React.createElement('circle',{cx:mark.x,cy:mark.y,r:7,fill:'#fbf7ed'}),"
            "React.createElement('text',{x:mark.x,y:mark.y+52,textAnchor:'middle',fontSize:14,fontWeight:700,fill:'#1b1611'},short(mark.material.title,28))))),"
            "React.createElement('section',{className:'absolute left-6 top-6',style:{width:'min(390px,44%)'}},"
            "React.createElement('p',{className:'mb-2 text-[11px] font-bold uppercase tracking-[0.18em] text-[#99582a]'},'Research field map'),"
            "React.createElement('h1',{className:'m-0 text-[clamp(24px,4.2vw,44px)] font-semibold leading-[0.95] tracking-normal'},short(data.projectName,72))),"
            "React.createElement('aside',{className:'absolute bottom-5 left-6 max-h-[30%] overflow-auto border-t border-[#1b1611] pt-3 pr-3 text-[12px] leading-5',style:{width:'min(430px,48%)'}},"
            "React.createElement('p',{className:'mb-1 text-[11px] font-bold uppercase tracking-[0.16em]',style:{color:palette[active%palette.length]}},pick(current.subtitle,'Research material')),"
            "React.createElement('h2',{className:'m-0 mb-2 text-[18px] font-semibold leading-tight'},short(current.title,72)),"
            "React.createElement('p',{className:'m-0 text-[#51483e]'},short(current.summary,260)),"
            "React.createElement('div',{className:'mt-3 grid gap-1'},(current.evidence&&current.evidence.length?current.evidence:['No extracted figure, table, or passage evidence was available.']).slice(0,2).map((item,index)=>React.createElement('p',{key:index,className:'m-0 text-[11px] leading-4 text-[#665c50]'},short(item,120))))),"
            "React.createElement('div',{className:'absolute bottom-5 right-6 flex gap-2'},materials.slice(0,6).map((material,index)=>React.createElement('button',{key:index,type:'button',onClick:()=>setActive(index),className:'h-3 w-8 border border-[#1b1611]',style:{background:index===active?palette[index%palette.length]:'transparent'}})))));"
            "}"
            "ReactDOM.createRoot(root).render(React.createElement(App));"
        )

    def _send_project_presentation_ready_email(
        self,
        *,
        user: BrowserUser,
        project_name: str,
        presentation: ProjectPresentationResponse,
    ) -> None:
        presentation_url = presentation.share_url or self._browser_app_url("/")
        try:
            self.email_sender.send_project_presentation_ready(
                email=user.email,
                project_name=project_name,
                presentation_url=presentation_url,
            )
        except Exception:
            logger.exception(
                "Failed to send project presentation ready email",
                extra={"event": "project.presentation.email_failed", "user_id": user.user_id},
            )

    def create_python_file(self, user: BrowserUser, project_id: str) -> PythonFileResponse:
        self._get_github_connection(user)
        project = self.repository.get_project(user_id=user.user_id, project_id=project_id)  # type: ignore[attr-defined]
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        created = self.repository.create_python_file(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
            filename="something.py",
            content="",
        )
        if created is None:
            raise HTTPException(status_code=500, detail="Python file could not be created")
        return created

    def import_project_notebook_upload(
        self,
        user: BrowserUser,
        project_id: str,
        *,
        filename: str,
        data: bytes,
    ) -> PythonFileResponse:
        self._get_github_connection(user)
        project = self.repository.get_project(user_id=user.user_id, project_id=project_id)  # type: ignore[attr-defined]
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        basename = Path(filename).name
        if not basename.lower().endswith(".ipynb"):
            raise HTTPException(status_code=422, detail="Upload an .ipynb notebook")
        if len(data) > _MAX_REPO_IMPORT_BYTES:
            raise HTTPException(status_code=422, detail="Notebook is too large to import")
        try:
            raw_notebook = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=422, detail="Notebook must be UTF-8 encoded") from exc
        content = _notebook_to_python_source(raw_notebook)
        created = self.repository.create_python_file(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
            filename=basename,
            file_kind="notebook",
            repo_path=f"notebooks/{basename}",
            content=content,
        )
        if created is None:
            raise HTTPException(status_code=500, detail="Notebook could not be imported")
        return created

    def get_python_file(self, user: BrowserUser, file_id: str) -> PythonFileResponse:
        python_file = self.repository.get_python_file(user_id=user.user_id, file_id=file_id)  # type: ignore[attr-defined]
        if python_file is None:
            raise HTTPException(status_code=404, detail="Python file not found")
        return python_file

    def list_project_workspace_files(self, user: BrowserUser, project_id: str) -> list[PythonFileResponse]:
        project = self.repository.get_project(user_id=user.user_id, project_id=project_id)  # type: ignore[attr-defined]
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        project_files = self.repository.list_project_python_files(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
        )
        if project_files is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return sorted(project_files, key=lambda item: item.repo_path)

    def list_project_code_files(self, user: BrowserUser, project_id: str) -> list[PythonFileResponse]:
        return [
            project_file
            for project_file in self.list_project_workspace_files(user, project_id)
            if project_file.file_kind == "python"
        ]

    def _get_project_workspace_file(self, user: BrowserUser, project_id: str, repo_path: str) -> PythonFileResponse:
        project = self.repository.get_project(user_id=user.user_id, project_id=project_id)  # type: ignore[attr-defined]
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        normalized_repo_path = _normalize_project_workspace_repo_path(repo_path)
        project_file = self.repository.get_project_python_file_by_path(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
            repo_path=normalized_repo_path,
        )
        if project_file is None:
            raise HTTPException(status_code=404, detail="Project workspace file not found")
        return project_file

    def _get_project_code_file(self, user: BrowserUser, project_id: str, repo_path: str) -> PythonFileResponse:
        project_file = self._get_project_workspace_file(user, project_id, repo_path)
        if project_file.file_kind != "python":
            raise HTTPException(status_code=404, detail="Project code file not found")
        return project_file

    def read_project_workspace_file(
        self,
        user: BrowserUser,
        project_id: str,
        repo_path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        project_file = self._get_project_workspace_file(user, project_id, repo_path)
        all_lines = project_file.content.splitlines()
        total_lines = len(all_lines)
        start = 1 if start_line is None else start_line
        end = total_lines if end_line is None else end_line
        if start < 1 or end < start:
            raise HTTPException(status_code=422, detail="Use a valid line range")
        start = min(start, total_lines + 1 if total_lines == 0 else total_lines)
        end = min(end, total_lines)
        numbered_lines = [
            {"line_number": line_number, "text": all_lines[line_number - 1]}
            for line_number in range(start, end + 1)
        ]
        rendered = "\n".join(f"{line['line_number']}: {line['text']}" for line in numbered_lines)
        return {
            "file_id": project_file.file_id,
            "repo_path": project_file.repo_path,
            "filename": project_file.filename,
            "file_kind": project_file.file_kind,
            "start_line": start,
            "end_line": end if total_lines else 0,
            "total_lines": total_lines,
            "content": rendered,
            "lines": numbered_lines,
        }

    def read_project_code_file(
        self,
        user: BrowserUser,
        project_id: str,
        repo_path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        project_file = self._get_project_code_file(user, project_id, repo_path)
        return self.read_project_workspace_file(
            user,
            project_id,
            project_file.repo_path,
            start_line=start_line,
            end_line=end_line,
        )

    def grep_project_workspace(
        self,
        user: BrowserUser,
        project_id: str,
        *,
        pattern: str,
        literal: bool = False,
        case_sensitive: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        query = str(pattern or "").strip()
        if not query:
            raise HTTPException(status_code=422, detail="Pattern is required")
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            matcher = re.compile(re.escape(query) if literal else query, flags)
        except re.error as exc:
            raise HTTPException(status_code=422, detail=f"Invalid grep pattern: {exc}") from exc
        matches: list[dict[str, Any]] = []
        for project_file in self.list_project_workspace_files(user, project_id):
            for line_number, line in enumerate(project_file.content.splitlines(), start=1):
                if not matcher.search(line):
                    continue
                matches.append(
                    {
                        "file_id": project_file.file_id,
                        "repo_path": project_file.repo_path,
                        "filename": project_file.filename,
                        "file_kind": project_file.file_kind,
                        "line_number": line_number,
                        "line": line,
                    }
                )
                if len(matches) >= limit:
                    return {
                        "pattern": query,
                        "literal": literal,
                        "case_sensitive": case_sensitive,
                        "matches": matches,
                        "truncated": True,
                    }
        return {
            "pattern": query,
            "literal": literal,
            "case_sensitive": case_sensitive,
            "matches": matches,
            "truncated": False,
        }

    def grep_project_code(
        self,
        user: BrowserUser,
        project_id: str,
        *,
        pattern: str,
        literal: bool = False,
        case_sensitive: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        workspace_result = self.grep_project_workspace(
            user,
            project_id,
            pattern=pattern,
            literal=literal,
            case_sensitive=case_sensitive,
            limit=limit,
        )
        workspace_result["matches"] = [
            match for match in workspace_result["matches"] if match.get("file_kind") == "python"
        ]
        return workspace_result

    def create_project_workspace_file(
        self,
        user: BrowserUser,
        project_id: str,
        *,
        repo_path: str,
        content: str = "",
    ) -> PythonFileResponse:
        self._get_github_connection(user)
        project = self.repository.get_project(user_id=user.user_id, project_id=project_id)  # type: ignore[attr-defined]
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        normalized_repo_path = _normalize_project_workspace_repo_path(repo_path)
        existing = self.repository.get_project_python_file_by_path(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
            repo_path=normalized_repo_path,
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail=f"Project workspace file '{normalized_repo_path}' already exists")
        file_kind = _workspace_file_kind_for_repo_path(normalized_repo_path)
        created = self.repository.create_python_file(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
            filename=Path(normalized_repo_path).name,
            repo_path=normalized_repo_path,
            file_kind=file_kind,
            content=content,
        )
        if created is None:
            raise HTTPException(status_code=500, detail="Project workspace file could not be created")
        return created

    def write_project_workspace_file(
        self,
        user: BrowserUser,
        project_id: str,
        *,
        repo_path: str,
        content: str,
    ) -> dict[str, Any]:
        self._get_github_connection(user)
        normalized_repo_path = _normalize_project_workspace_repo_path(repo_path)
        existing = self.repository.get_project_python_file_by_path(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
            repo_path=normalized_repo_path,
        )
        if existing is None:
            created = self.create_project_workspace_file(
                user,
                project_id,
                repo_path=normalized_repo_path,
                content=content,
            )
            return {
                "file_id": created.file_id,
                "repo_path": created.repo_path,
                "filename": created.filename,
                "file_kind": created.file_kind,
                "content": created.content,
                "created": True,
                "replaced": False,
            }
        updated = self.repository.update_python_file(  # type: ignore[attr-defined]
            user_id=user.user_id,
            file_id=existing.file_id,
            content=content,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Project workspace file not found")
        return {
            "file_id": updated.file_id,
            "repo_path": updated.repo_path,
            "filename": updated.filename,
            "file_kind": updated.file_kind,
            "content": updated.content,
            "created": False,
            "replaced": True,
        }

    def create_project_python_file(
        self,
        user: BrowserUser,
        project_id: str,
        *,
        repo_path: str,
        content: str = "",
    ) -> PythonFileResponse:
        return self.create_project_workspace_file(
            user,
            project_id,
            repo_path=_normalize_project_code_repo_path(repo_path),
            content=content,
        )

    def apply_patch_project_workspace_file(
        self,
        user: BrowserUser,
        project_id: str,
        *,
        repo_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        self._get_github_connection(user)
        project_file = self._get_project_workspace_file(user, project_id, repo_path)
        if old_string == "":
            if not new_string.strip():
                raise HTTPException(status_code=422, detail="new_string must be non-empty when populating an empty file")
            if project_file.content != "":
                raise HTTPException(status_code=422, detail="old_string is required unless the file is empty")
            match_count = 1
            updated_content = new_string
        else:
            match_count = project_file.content.count(old_string)
            if match_count == 0:
                raise HTTPException(status_code=409, detail=f"Exact text was not found in '{project_file.repo_path}'")
            if match_count > 1 and not replace_all:
                raise HTTPException(
                    status_code=409,
                    detail=f"Exact text matched {match_count} times in '{project_file.repo_path}'. Read the file and retry with a unique snippet or replace_all=true.",
                )
            updated_content = (
                project_file.content.replace(old_string, new_string)
                if replace_all
                else project_file.content.replace(old_string, new_string, 1)
            )
        if updated_content == project_file.content:
            raise HTTPException(status_code=409, detail=f"Patch did not change '{project_file.repo_path}'")
        updated = self.repository.update_python_file(  # type: ignore[attr-defined]
            user_id=user.user_id,
            file_id=project_file.file_id,
            content=updated_content,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Project workspace file not found")
        return {
            "file_id": updated.file_id,
            "repo_path": updated.repo_path,
            "filename": updated.filename,
            "file_kind": updated.file_kind,
            "match_count": match_count,
            "replace_all": replace_all,
            "content": updated.content,
        }

    def edit_project_code_file(
        self,
        user: BrowserUser,
        project_id: str,
        *,
        repo_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        return self.apply_patch_project_workspace_file(
            user,
            project_id,
            repo_path=_normalize_project_code_repo_path(repo_path),
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )

    def move_project_workspace_file(
        self,
        user: BrowserUser,
        project_id: str,
        *,
        repo_path: str,
        new_repo_path: str,
    ) -> dict[str, Any]:
        self._get_github_connection(user)
        project_file = self._get_project_workspace_file(user, project_id, repo_path)
        normalized_new_repo_path = _normalize_project_workspace_repo_path(new_repo_path)
        if project_file.repo_path == normalized_new_repo_path:
            raise HTTPException(status_code=409, detail="Move target matches the current repository path")
        existing_target = self.repository.get_project_python_file_by_path(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
            repo_path=normalized_new_repo_path,
        )
        if existing_target is not None:
            raise HTTPException(status_code=409, detail=f"Project workspace file '{normalized_new_repo_path}' already exists")
        updated = self.repository.update_python_file(  # type: ignore[attr-defined]
            user_id=user.user_id,
            file_id=project_file.file_id,
            repo_path=normalized_new_repo_path,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Project workspace file not found")
        return {
            "file_id": updated.file_id,
            "repo_path": updated.repo_path,
            "filename": updated.filename,
            "file_kind": updated.file_kind,
            "previous_repo_path": project_file.repo_path,
        }

    def delete_project_workspace_file(self, user: BrowserUser, project_id: str, *, repo_path: str) -> dict[str, Any]:
        self._get_github_connection(user)
        project_file = self._get_project_workspace_file(user, project_id, repo_path)
        deleted = self.repository.delete_python_file(user_id=user.user_id, file_id=project_file.file_id)  # type: ignore[attr-defined]
        if not deleted:
            raise HTTPException(status_code=404, detail="Project workspace file not found")
        return {
            "file_id": project_file.file_id,
            "repo_path": project_file.repo_path,
            "filename": project_file.filename,
            "file_kind": project_file.file_kind,
            "deleted": True,
        }

    def delete_project_code_file(self, user: BrowserUser, project_id: str, *, repo_path: str) -> dict[str, Any]:
        return self.delete_project_workspace_file(
            user,
            project_id,
            repo_path=_normalize_project_code_repo_path(repo_path),
        )

    def _project_workspace_files_map(self, user: BrowserUser, project_id: str) -> dict[str, PythonFileResponse]:
        return {
            project_file.repo_path: project_file
            for project_file in self.list_project_workspace_files(user, project_id)
        }

    def _project_workspace_snapshot_files(self, user: BrowserUser, project_id: str) -> dict[str, str]:
        files: dict[str, str] = {}
        for project_file in self.list_project_workspace_files(user, project_id):
            if project_file.file_kind == "notebook":
                files[project_file.repo_path] = _python_source_to_notebook(project_file.content)
            else:
                files[project_file.repo_path] = project_file.content
        return files

    def _project_exec_command(self, command: str) -> list[str]:
        parts = shlex.split(str(command or "").strip())
        if not parts:
            raise HTTPException(status_code=422, detail="A command is required")
        if parts[0] in {"python", "python3"}:
            parts[0] = sys.executable
        return parts

    def _record_project_exec_output(self, exec_id: str, stream_name: str, stream) -> None:
        try:
            for chunk in iter(stream.readline, ""):
                with self._project_exec_lock:
                    session = self._project_exec_sessions.get(exec_id)
                    if session is None:
                        break
                    target = session.stdout_chunks if stream_name == "stdout" else session.stderr_chunks
                    target.append(chunk)
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _watch_project_exec_session(self, exec_id: str) -> None:
        with self._project_exec_lock:
            session = self._project_exec_sessions.get(exec_id)
        if session is None:
            return
        exit_code = session.process.wait()
        finished_at = utcnow()
        with session.lock:
            if session.status != "canceled":
                session.status = "completed" if exit_code == 0 else "failed"
            session.exit_code = exit_code
            session.finished_at = finished_at
            session.completion_event.set()
        shutil.rmtree(session.workspace_path, ignore_errors=True)

    def _start_project_exec_session(
        self,
        *,
        user: BrowserUser,
        thread_id: str,
        project_id: str,
        command: list[str],
    ) -> ProjectExecSession:
        workspace_path = tempfile.mkdtemp(prefix="paperbase-project-exec-")
        try:
            for repo_path, content in self._project_workspace_snapshot_files(user, project_id).items():
                target = Path(workspace_path) / repo_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = workspace_path + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            process = subprocess.Popen(
                command,
                cwd=workspace_path,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception:
            shutil.rmtree(workspace_path, ignore_errors=True)
            raise
        session = ProjectExecSession(
            exec_id=new_id("pex"),
            thread_id=thread_id,
            project_id=project_id,
            command=list(command),
            workspace_path=workspace_path,
            started_at=utcnow(),
            process=process,
        )
        with self._project_exec_lock:
            self._project_exec_sessions[session.exec_id] = session
            self._project_exec_index.setdefault((thread_id, project_id), set()).add(session.exec_id)
        if process.stdout is not None:
            threading.Thread(
                target=self._record_project_exec_output,
                args=(session.exec_id, "stdout", process.stdout),
                daemon=True,
            ).start()
        if process.stderr is not None:
            threading.Thread(
                target=self._record_project_exec_output,
                args=(session.exec_id, "stderr", process.stderr),
                daemon=True,
            ).start()
        threading.Thread(target=self._watch_project_exec_session, args=(session.exec_id,), daemon=True).start()
        return session

    def _get_project_exec_session(self, *, exec_id: str, thread_id: str, project_id: str) -> ProjectExecSession:
        with self._project_exec_lock:
            session = self._project_exec_sessions.get(exec_id)
        if session is None or session.thread_id != thread_id or session.project_id != project_id:
            raise HTTPException(status_code=404, detail="Project exec session not found")
        return session

    def project_exec(
        self,
        user: BrowserUser,
        thread_id: str,
        project_id: str,
        *,
        command: str,
    ) -> dict[str, Any]:
        session = self._start_project_exec_session(
            user=user,
            thread_id=thread_id,
            project_id=project_id,
            command=self._project_exec_command(command),
        )
        return session.snapshot()

    def project_wait(
        self,
        user: BrowserUser,
        thread_id: str,
        project_id: str,
        *,
        exec_id: str,
        timeout_seconds: int = _PROJECT_EXEC_DEFAULT_WAIT_SECONDS,
    ) -> dict[str, Any]:
        session = self._get_project_exec_session(exec_id=exec_id, thread_id=thread_id, project_id=project_id)
        session.completion_event.wait(timeout=max(0, min(timeout_seconds, _PROJECT_EXEC_MAX_WAIT_SECONDS)))
        return session.snapshot()

    def project_cancel(
        self,
        user: BrowserUser,
        thread_id: str,
        project_id: str,
        *,
        exec_id: str,
    ) -> dict[str, Any]:
        session = self._get_project_exec_session(exec_id=exec_id, thread_id=thread_id, project_id=project_id)
        with session.lock:
            if session.status == "running":
                session.status = "canceled"
                try:
                    session.process.kill()
                except Exception:
                    pass
        session.completion_event.wait(timeout=1)
        return session.snapshot()

    def project_logs(
        self,
        user: BrowserUser,
        thread_id: str,
        project_id: str,
        *,
        exec_id: str,
    ) -> dict[str, Any]:
        session = self._get_project_exec_session(exec_id=exec_id, thread_id=thread_id, project_id=project_id)
        return session.snapshot()

    def _project_run_command(self, user: BrowserUser, project_id: str, repo_path: str, mode: str) -> list[str]:
        project_file = self._get_project_workspace_file(user, project_id, repo_path)
        if project_file.file_kind not in {"python", "notebook"}:
            raise HTTPException(status_code=422, detail="project_run only supports Python or notebook files")
        if project_file.file_kind == "notebook":
            raise HTTPException(status_code=422, detail="project_run does not support notebook files in chat tools yet")
        command = [sys.executable, project_file.repo_path]
        entry_source = project_file.content
        if "--run-mode" in entry_source or "run_mode" in entry_source:
            command.extend(["--run-mode", mode])
        return command

    def project_run_tool(
        self,
        user: BrowserUser,
        thread_id: str,
        project_id: str,
        *,
        repo_path: str,
        mode: str = "full",
    ) -> dict[str, Any]:
        normalized_mode = str(mode or "full").strip().lower() or "full"
        if normalized_mode not in {"smoke", "full"}:
            raise HTTPException(status_code=422, detail="Run mode must be 'smoke' or 'full'")
        session = self._start_project_exec_session(
            user=user,
            thread_id=thread_id,
            project_id=project_id,
            command=self._project_run_command(user, project_id, repo_path, normalized_mode),
        )
        session.completion_event.wait(timeout=_PROJECT_EXEC_MAX_WAIT_SECONDS)
        payload = session.snapshot()
        payload["mode"] = normalized_mode
        return payload

    def project_test_tool(self, user: BrowserUser, thread_id: str, project_id: str) -> dict[str, Any]:
        workspace_files = self._project_workspace_files_map(user, project_id)
        has_pytest = any(
            repo_path.startswith("tests/") or PurePosixPath(repo_path).name.startswith("test_")
            for repo_path in workspace_files
        )
        command = [sys.executable, "-m", "pytest", "-q"] if has_pytest else [sys.executable, "-m", "compileall", "."]
        session = self._start_project_exec_session(
            user=user,
            thread_id=thread_id,
            project_id=project_id,
            command=command,
        )
        session.completion_event.wait(timeout=_PROJECT_EXEC_MAX_WAIT_SECONDS)
        payload = session.snapshot()
        payload["tool"] = "project_test"
        return payload

    def project_lint_tool(self, user: BrowserUser, thread_id: str, project_id: str) -> dict[str, Any]:
        ruff = shutil.which("ruff")
        command = [ruff, "check", "."] if ruff else [sys.executable, "-m", "compileall", "."]
        session = self._start_project_exec_session(
            user=user,
            thread_id=thread_id,
            project_id=project_id,
            command=command,
        )
        session.completion_event.wait(timeout=_PROJECT_EXEC_MAX_WAIT_SECONDS)
        payload = session.snapshot()
        payload["tool"] = "project_lint"
        return payload

    def _project_remote_files(self, user: BrowserUser, project_id: str) -> dict[str, str] | None:
        sync = self.repository.get_project_repo_sync(user_id=user.user_id, project_id=project_id)  # type: ignore[attr-defined]
        if sync is None or not sync.repo_exists or not sync.github_repo_name:
            return None
        try:
            github = self._get_github_connection(user)
            access_token = self.secret_cipher.decrypt(github["encrypted_access_token"])
            manifest_content = self.github_repo_manager.get_file_text(
                access_token=access_token,
                owner_login=sync.github_owner_login or github["github_login"],
                repo_name=sync.github_repo_name,
                path=".paperbase",
                ref=sync.base_branch or sync.default_branch or "main",
            )
        except Exception:
            return None
        remote_files: dict[str, str] = {}
        for repo_path in sorted(_managed_paths_from_manifest_content(manifest_content)):
            if repo_path == ".paperbase":
                continue
            try:
                remote_files[repo_path] = self.github_repo_manager.get_file_text(
                    access_token=access_token,
                    owner_login=sync.github_owner_login or github["github_login"],
                    repo_name=sync.github_repo_name,
                    path=repo_path,
                    ref=sync.base_branch or sync.default_branch or "main",
                )
            except Exception:
                continue
        return remote_files

    def project_git_status(self, user: BrowserUser, project_id: str) -> dict[str, Any]:
        local_files = self._project_workspace_files_map(user, project_id)
        remote_files = self._project_remote_files(user, project_id) or {}
        added = sorted(path for path in local_files if path not in remote_files)
        deleted = sorted(path for path in remote_files if path not in local_files)
        modified = sorted(
            path
            for path, project_file in local_files.items()
            if path in remote_files
            and project_file.content
            != (
                _notebook_to_python_source_or_raw(remote_files[path])
                if project_file.file_kind == "notebook"
                else remote_files[path]
            )
        )
        clean = not (added or deleted or modified)
        return {
            "clean": clean,
            "added": added,
            "deleted": deleted,
            "modified": modified,
            "base_ref": None if not remote_files else "project_repo_sync",
        }

    def project_git_diff(self, user: BrowserUser, project_id: str) -> dict[str, Any]:
        local_files = self._project_workspace_files_map(user, project_id)
        remote_files = self._project_remote_files(user, project_id) or {}
        diffs: list[str] = []
        all_paths = sorted(set(local_files) | set(remote_files))
        for repo_path in all_paths:
            before = remote_files.get(repo_path)
            if repo_path in local_files and local_files[repo_path].file_kind == "notebook" and before is not None:
                before = _notebook_to_python_source_or_raw(before)
            after = local_files.get(repo_path).content if repo_path in local_files else None
            before_lines = [] if before is None else before.splitlines()
            after_lines = [] if after is None else after.splitlines()
            diff = "\n".join(
                difflib.unified_diff(
                    before_lines,
                    after_lines,
                    fromfile=f"a/{repo_path}",
                    tofile=f"b/{repo_path}",
                    lineterm="",
                )
            ).strip()
            if diff:
                diffs.append(diff)
        rendered, truncated = _truncate_tool_text("\n\n".join(diffs).strip(), limit=_CHAT_TURN_DIFF_MAX_CHARS)
        return {
            "diff": rendered,
            "truncated": truncated,
            "base_ref": None if not remote_files else "project_repo_sync",
        }

    def update_python_file(
        self,
        user: BrowserUser,
        file_id: str,
        *,
        filename: str | None,
        content: str | None,
    ) -> PythonFileResponse:
        self._get_github_connection(user)
        normalized_filename = _normalize_python_filename(filename) if filename is not None else None
        python_file = self.repository.update_python_file(  # type: ignore[attr-defined]
            user_id=user.user_id,
            file_id=file_id,
            filename=normalized_filename,
            content=content,
        )
        if python_file is None:
            raise HTTPException(status_code=404, detail="Python file not found")
        return python_file

    def delete_python_file(self, user: BrowserUser, file_id: str) -> dict[str, Any]:
        self._get_github_connection(user)
        project_file = self.repository.get_python_file(user_id=user.user_id, file_id=file_id)  # type: ignore[attr-defined]
        if project_file is None:
            raise HTTPException(status_code=404, detail="Python file not found")
        deleted = self.repository.delete_python_file(user_id=user.user_id, file_id=file_id)  # type: ignore[attr-defined]
        if not deleted:
            raise HTTPException(status_code=404, detail="Python file not found")
        return {
            "file_id": project_file.file_id,
            "project_id": project_file.project_id,
            "repo_path": project_file.repo_path,
            "filename": project_file.filename,
            "deleted": True,
        }

    def get_project_repo_sync(self, user: BrowserUser, project_id: str) -> ProjectRepoSyncResponse:
        sync = self.repository.get_project_repo_sync(user_id=user.user_id, project_id=project_id)  # type: ignore[attr-defined]
        if sync is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return sync

    def sync_project_repo(
        self,
        user: BrowserUser,
        project_id: str,
        request: ProjectRepoSyncRequest,
    ) -> ProjectRepoSyncResponse:
        github = self._get_github_connection(user)
        commit_message = request.commit_message.strip()
        if not commit_message:
            raise HTTPException(status_code=422, detail="Commit message is required")
        if request.conflict_resolutions and not request.expected_remote_head_sha:
            raise HTTPException(
                status_code=422,
                detail="Conflict resolutions require the latest remote head reference",
            )
        project = self.repository.get_project(user_id=user.user_id, project_id=project_id)  # type: ignore[attr-defined]
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        project_files = self.repository.list_project_python_files(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
        )
        if project_files is None:
            raise HTTPException(status_code=404, detail="Project not found")
        sync = self.repository.get_project_repo_sync(user_id=user.user_id, project_id=project_id)  # type: ignore[attr-defined]
        try:
            access_token = self.secret_cipher.decrypt(github["encrypted_access_token"])
            project_files_by_path = {
                _normalize_repo_path(project_file.repo_path or f"src/{project_file.filename}"): project_file
                for project_file in project_files
            }
            for resolution in request.conflict_resolutions:
                path = resolution.path.strip()
                if path == ".paperbase":
                    continue
                path = _normalize_repo_path(path)
                project_file = project_files_by_path.get(path)
                if project_file is None:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Conflict path '{path}' does not map to a project file.",
                    )
                updated = self.repository.update_python_file(  # type: ignore[attr-defined]
                    user_id=user.user_id,
                    file_id=project_file.file_id,
                    filename=None,
                    content=resolution.content,
                )
                if updated is None:
                    raise HTTPException(status_code=404, detail="Project file not found")
                project_files_by_path.pop(path, None)
                project_files_by_path[_normalize_repo_path(updated.repo_path or f"src/{updated.filename}")] = updated
            project_files = sorted(
                project_files_by_path.values(),
                key=lambda item: (item.updated_at, item.created_at),
                reverse=True,
            )
            if sync is None or not sync.repo_exists:
                repo_info = self.github_repo_manager.ensure_repo(
                    access_token=access_token,
                    owner_login=github["github_login"],
                    project_name=project.name,
                    project_id=project.project_id,
                    description=f"Paperbase project: {project.name}",
                )
                repo_owner_login = repo_info.owner_login or github["github_login"]
                repo_name = repo_info.repo_name
                repo_url = repo_info.repo_url
                default_branch = repo_info.default_branch or "main"
                base_branch = default_branch
            else:
                repo_owner_login = sync.github_owner_login or github["github_login"]
                repo_name = sync.github_repo_name
                repo_url = sync.github_repo_url
                default_branch = sync.default_branch or sync.base_branch or "main"
                base_branch = sync.base_branch or default_branch
            synced_at = utcnow()
            author_name = (user.display_name or "").strip() or github["github_login"]
            author_email = f"{github['github_login']}@users.noreply.github.com"
            file_manifests = [
                {
                    "file_id": project_file.file_id,
                    "filename": project_file.filename,
                    "file_kind": project_file.file_kind,
                    "repo_path": _normalize_repo_path(project_file.repo_path or f"src/{project_file.filename}"),
                }
                for project_file in project_files
            ]
            managed_paths = sorted({".paperbase", *(item["repo_path"] for item in file_manifests)})
            manifest = {
                "schema_version": 1,
                "project_id": project.project_id,
                "project_name": project.name,
                "custom_instructions": project.description or "",
                "project_description": project.description or "",
                "created_at": project.created_at.isoformat(),
                "updated_at": project.updated_at.isoformat(),
                "last_pushed_at": synced_at.isoformat(),
                "author_name": author_name,
                "author_email": author_email,
                "github_login": github["github_login"],
                "github_owner_login": repo_owner_login,
                "github_repo_name": repo_name,
                "base_branch": base_branch,
                "default_branch": default_branch,
                "project_file_count": len(project_files),
                "python_file_count": len([item for item in file_manifests if item["file_kind"] == "python"]),
                "notebook_file_count": len([item for item in file_manifests if item["file_kind"] == "notebook"]),
                "text_file_count": len([item for item in file_manifests if item["file_kind"] == "text"]),
                "managed_paths": managed_paths,
                "files": file_manifests,
            }
            files_to_sync = {
                ".paperbase": json.dumps(manifest, indent=2, sort_keys=True),
            }
            for project_file in project_files:
                repo_path = _normalize_repo_path(project_file.repo_path or f"src/{project_file.filename}")
                files_to_sync[repo_path] = (
                    _python_source_to_notebook(project_file.content)
                    if project_file.file_kind == "notebook"
                    else project_file.content
                )
            previous_managed_paths: set[str] = set()
            if sync and sync.repo_exists:
                try:
                    previous_manifest = self.github_repo_manager.get_file_text(
                        access_token=access_token,
                        owner_login=repo_owner_login,
                        repo_name=repo_name,
                        path=".paperbase",
                        ref=base_branch,
                    )
                    previous_managed_paths = _managed_paths_from_manifest_content(previous_manifest)
                except GitHubSyncError:
                    previous_managed_paths = set()
            commit_info = self.github_repo_manager.sync_project_files(
                access_token=access_token,
                owner_login=repo_owner_login,
                repo_name=repo_name,
                repo_url=repo_url,
                default_branch=base_branch,
                files=files_to_sync,
                commit_message=commit_message,
                author_name=author_name,
                author_email=author_email,
                base_commit_sha=sync.last_sync_commit_sha if sync and sync.last_sync_commit_sha else None,
                expected_remote_head_sha=request.expected_remote_head_sha,
                previous_managed_paths=previous_managed_paths,
            )
        except GitHubSyncConflictError as exc:
            project_file_by_path = {
                _normalize_repo_path(project_file.repo_path or f"src/{project_file.filename}"): project_file
                for project_file in project_files
            }
            conflict_files: list[ProjectRepoConflictFile] = []
            for conflict in exc.conflicts:
                project_file = project_file_by_path.get(_normalize_repo_path(conflict.path))
                file_kind = project_file.file_kind if project_file is not None else "python"
                is_notebook = file_kind == "notebook"
                conflict_files.append(
                    ProjectRepoConflictFile(
                        path=conflict.path,
                        file_kind=file_kind,  # type: ignore[arg-type]
                        base_content=(
                            _notebook_to_python_source_or_raw(conflict.base_content)
                            if is_notebook
                            else conflict.base_content
                        ),
                        local_content=(
                            _notebook_to_python_source_or_raw(conflict.local_content)
                            if is_notebook
                            else conflict.local_content
                        ),
                        remote_content=(
                            _notebook_to_python_source_or_raw(conflict.remote_content)
                            if is_notebook
                            else conflict.remote_content
                        ),
                    )
                )
            if sync is not None:
                conflict_owner_login = sync.github_owner_login or github["github_login"]
                conflict_base_branch = sync.base_branch or exc.default_branch
            else:
                conflict_owner_login = github["github_login"]
                conflict_base_branch = exc.default_branch
            detail = ProjectRepoConflictDetail(
                message=str(exc),
                project_id=project_id,
                repo_exists=True,
                github_owner_login=conflict_owner_login,
                github_repo_name=exc.repo_name,
                github_repo_url=exc.repo_url,
                base_branch=conflict_base_branch,
                default_branch=exc.default_branch,
                base_commit_sha=exc.base_commit_sha,
                remote_head_sha=exc.remote_head_sha,
                conflicts=conflict_files,
            )
            raise HTTPException(status_code=409, detail=detail.model_dump()) from exc
        except GitHubSyncError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        saved = self.repository.save_project_repo_sync(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
            github_owner_login=repo_owner_login,
            github_repo_name=commit_info.repo_name,
            github_repo_url=commit_info.repo_url,
            base_branch=base_branch,
            default_branch=commit_info.default_branch,
            last_sync_commit_sha=commit_info.commit_sha,
            last_sync_commit_url=commit_info.commit_url,
            last_synced_at=synced_at,
        )
        if saved is None:
            raise HTTPException(status_code=500, detail="Project repository metadata could not be saved")
        return saved

    def run_project_python_file(
        self,
        user: BrowserUser,
        project_id: str,
        *,
        repo_path: str,
        mode: str = "full",
    ) -> RunPythonFileResponse:
        project_file = self._get_project_code_file(user, project_id, repo_path)
        return self._run_project_file_workspace(user, project_id, project_file=project_file, mode=mode)

    def _run_project_file_workspace(
        self,
        user: BrowserUser,
        project_id: str,
        *,
        project_file: PythonFileResponse,
        mode: str,
    ) -> RunPythonFileResponse:
        normalized_mode = str(mode or "full").strip().lower() or "full"
        if normalized_mode not in {"smoke", "full"}:
            raise HTTPException(status_code=422, detail="Run mode must be 'smoke' or 'full'")
        project_files = self.repository.list_project_python_files(  # type: ignore[attr-defined]
            user_id=user.user_id,
            project_id=project_id,
        )
        if project_files is None:
            raise HTTPException(status_code=404, detail="Project not found")
        workspace_files: dict[str, str] = {}
        for item in project_files:
            if item.file_kind == "python":
                workspace_files[item.repo_path] = item.content
            elif item.file_kind == "notebook":
                workspace_files[item.repo_path] = _python_source_to_notebook(item.content)
        entrypoint_path = project_file.repo_path
        if project_file.file_kind == "notebook":
            entrypoint_path = "__paperbase_notebook_entry__.py"
            workspace_files[entrypoint_path] = project_file.content
        token_id, token_secret = self._get_modal_credentials(user)
        try:
            result = self.modal_runner.run_project(
                token_id=token_id,
                token_secret=token_secret,
                files=workspace_files,
                entrypoint_path=entrypoint_path,
                run_mode=normalized_mode,
            )
        except ModalExecutionError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return RunPythonFileResponse(
            file_id=project_file.file_id,
            status=result.status,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            started_at=result.started_at,
            finished_at=result.finished_at,
        )

    def run_python_file(self, user: BrowserUser, file_id: str, *, mode: str = "full") -> RunPythonFileResponse:
        python_file = self.get_python_file(user, file_id)
        return self._run_project_file_workspace(
            user,
            python_file.project_id,
            project_file=python_file,
            mode=mode,
        )

    def create_chat_thread(self, user: BrowserUser, request: CreateChatThreadRequest) -> ChatThreadResponse:
        project: ProjectResponse | None = None
        if request.project_id:
            project = self.repository.get_project(user_id=user.user_id, project_id=request.project_id)  # type: ignore[attr-defined]
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
        else:
            project = self.repository.get_default_project(user.user_id)  # type: ignore[attr-defined]
            if project is None:
                first_project = next(iter(self.repository.list_projects(user.user_id)), None)  # type: ignore[attr-defined]
                if first_project is not None:
                    project = self.repository.get_project(  # type: ignore[attr-defined]
                        user_id=user.user_id,
                        project_id=first_project.project_id,
                    )
            if project is None:
                raise HTTPException(status_code=409, detail="Create a project before starting a chat")
        summary = self.repository.create_chat_thread(  # type: ignore[attr-defined]
            user_id=user.user_id,
            title=request.title.strip() or "New chat",
            project_id=project.project_id,
            project_name=project.name,
            project_instructions=project.description.strip(),
        )
        thread = self.repository.get_chat_thread(user_id=user.user_id, thread_id=summary.thread_id)  # type: ignore[attr-defined]
        if thread is None:
            raise HTTPException(status_code=500, detail="Chat thread could not be created")
        return thread

    def get_chat_thread(self, user: BrowserUser, thread_id: str) -> ChatThreadResponse:
        thread = self.repository.get_chat_thread(user_id=user.user_id, thread_id=thread_id)  # type: ignore[attr-defined]
        if thread is None:
            raise HTTPException(status_code=404, detail="Chat thread not found")
        return thread

    def update_chat_thread(
        self,
        user: BrowserUser,
        thread_id: str,
        request: UpdateChatThreadRequest,
    ) -> ChatThreadResponse:
        thread = self.get_chat_thread(user, thread_id)
        title = request.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Chat title is required")
        self.repository.rename_chat_thread(thread_id=thread.thread_id, title=title)  # type: ignore[attr-defined]
        return self.get_chat_thread(user, thread_id)

    def create_chat_feedback(
        self,
        user: BrowserUser,
        thread_id: str,
        request: CreateChatFeedbackRequest,
    ) -> ChatFeedbackResponse:
        reasons = [reason.strip() for reason in request.reasons if reason.strip()]
        feedback = self.repository.add_chat_feedback(  # type: ignore[attr-defined]
            user_id=user.user_id,
            thread_id=thread_id,
            message_id=request.message_id,
            sentiment=request.sentiment,
            reasons=reasons[:12],
            note=request.note.strip()[:4000],
            debug_bundle=request.debug_bundle,
        )
        if feedback is None:
            raise HTTPException(status_code=404, detail="Chat message not found")
        return feedback

    def move_chat_thread_to_project(
        self,
        user: BrowserUser,
        thread_id: str,
        request: MoveChatThreadProjectRequest,
    ) -> ChatThreadResponse:
        thread = self.get_chat_thread(user, thread_id)
        if thread.messages:
            raise HTTPException(status_code=409, detail="Only empty chats can be moved between projects")
        project = self.repository.get_project(user_id=user.user_id, project_id=request.project_id)  # type: ignore[attr-defined]
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        moved = self.repository.move_chat_thread_to_project(  # type: ignore[attr-defined]
            user_id=user.user_id,
            thread_id=thread_id,
            project_id=project.project_id,
            project_name=project.name,
            project_instructions=project.description.strip(),
        )
        if moved is None:
            raise HTTPException(status_code=404, detail="Chat thread not found")
        return moved

    def delete_chat_thread(self, user: BrowserUser, thread_id: str) -> None:
        deleted = self.repository.delete_chat_thread(user_id=user.user_id, thread_id=thread_id)  # type: ignore[attr-defined]
        if not deleted:
            raise HTTPException(status_code=404, detail="Chat thread not found")

    def _choose_openai_api_key(self, user: BrowserUser) -> tuple[str | None, str]:
        user_key_record = self.repository.get_openai_api_key_record(user.user_id)  # type: ignore[attr-defined]
        if user_key_record:
            try:
                return self.secret_cipher.decrypt(user_key_record["encrypted_api_key"]), "completed"
            except InvalidToken:
                logger.warning(
                    "Saved OpenAI API key could not be decrypted; deleting stale record",
                    extra={"event": "browser.auth.openai_key.invalid", "user_id": user.user_id},
                )
                self.repository.delete_openai_api_key(user.user_id)  # type: ignore[attr-defined]
                return None, "api_key_invalid"
        legacy_user_key_record = self.repository.get_anthropic_api_key_record(user.user_id)  # type: ignore[attr-defined]
        if legacy_user_key_record:
            try:
                return self.secret_cipher.decrypt(legacy_user_key_record["encrypted_api_key"]), "completed"
            except InvalidToken:
                logger.warning(
                    "Saved legacy Anthropic API key could not be decrypted; deleting stale record",
                    extra={"event": "browser.auth.openai_key.legacy_invalid", "user_id": user.user_id},
                )
                self.repository.delete_anthropic_api_key(user.user_id)  # type: ignore[attr-defined]
                return None, "api_key_invalid"
        return None, "api_key_required"

    def _choose_anthropic_api_key(self, user: BrowserUser) -> tuple[str | None, bool, str]:
        return self._choose_openai_api_key(user)

    def _ensure_mentioned_papers_indexed(
        self, paper_ids: list[str]
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """Resolve each @-mentioned ``paper_id`` and, if the row is not already
        at ``index_status='indexed'``, synchronously fetch and index it from
        arXiv before the chat turn runs. Yields SSE-shaped events
        (``mention_indexing_started`` / ``mention_indexing_completed`` /
        ``mention_indexing_failed``) so the streaming chat path can surface
        per-paper progress; the non-streaming caller drains the iterator and
        discards the events. Failures (unknown paper, missing arxiv_id, or a
        ``PermanentError`` from ``index_arxiv_paper``) are reported as a
        ``mention_indexing_failed`` event but do not abort the turn — the chat
        will simply run without that paper at full fidelity."""
        seen: set[str] = set()
        for paper_id in paper_ids:
            if not paper_id or paper_id in seen:
                continue
            seen.add(paper_id)
            existing = self.repository.get_paper(paper_id)  # type: ignore[attr-defined]
            if existing is None:
                yield (
                    "mention_indexing_failed",
                    {"paper_id": paper_id, "detail": "Paper not found"},
                )
                continue
            if existing.index_status == "indexed":
                continue
            if not existing.arxiv_id:
                yield (
                    "mention_indexing_failed",
                    {
                        "paper_id": paper_id,
                        "title": existing.title or "",
                        "detail": "Paper has no arxiv_id; cannot fetch on demand",
                    },
                )
                continue
            yield (
                "mention_indexing_started",
                {
                    "paper_id": paper_id,
                    "arxiv_id": existing.arxiv_id,
                    "title": existing.title or "",
                },
            )
            try:
                indexed = self.index_arxiv_paper(existing.arxiv_id, force=False)
            except Exception as exc:
                logger.exception(
                    "On-demand mention indexing failed",
                    extra={
                        "event": "chat.mention_indexing_failed",
                        "paper_id": paper_id,
                        "arxiv_id": existing.arxiv_id,
                    },
                )
                yield (
                    "mention_indexing_failed",
                    {
                        "paper_id": paper_id,
                        "arxiv_id": existing.arxiv_id,
                        "title": existing.title or "",
                        "detail": str(exc),
                    },
                )
                continue
            yield (
                "mention_indexing_completed",
                {
                    "paper_id": indexed.paper_id,
                    "arxiv_id": indexed.arxiv_id,
                    "title": indexed.title or "",
                },
            )

    def _prepare_chat_generation(
        self,
        *,
        user: BrowserUser,
        thread_id: str,
        request: SendChatMessageRequest,
        resolved_user: BrowserUser,
        api_key_value: str,
    ) -> PreparedChatGeneration:
        thread = self.get_chat_thread(user, thread_id)
        content = request.content.strip()
        reply_title = self._generate_chat_title(api_key=api_key_value, content=content)
        turn = self.repository.create_chat_turn(  # type: ignore[attr-defined]
            thread_id=thread_id,
            title=reply_title,
            status="running",
        )
        user_item = self.repository.add_chat_thread_item(  # type: ignore[attr-defined]
            thread_id=thread_id,
            turn_id=turn.turn_id,
            item_type="user_message",
            role="user",
            content_text=content,
            content_json={
                "paper_selection": request.paper_selection.model_dump()
            } if request.paper_selection is not None else None,
        )
        updated_thread_title = reply_title if thread.title == "New chat" else None
        if updated_thread_title:
            self.repository.rename_chat_thread(  # type: ignore[attr-defined]
                thread_id=thread_id,
                title=updated_thread_title,
            )
        refreshed = self.get_chat_thread(user, thread_id)
        return PreparedChatGeneration(
            thread=refreshed,
            turn=turn,
            user_item=user_item,
            resolved_user=resolved_user,
            api_key_value=api_key_value,
            updated_thread_title=updated_thread_title,
        )

    def _generate_chat_title(self, *, api_key: str, content: str) -> str:
        fallback = _summarize_chat_title(content)
        prompt = Prompt(
            input=[
                {
                    "type": "message",
                    "role": "user",
                    "content": content[:4000],
                }
            ],
            base_instructions=(
                "Write one 4 to 5 word title that reads like an article headline. "
                "Use title case. Return only the title, with no quotes, markdown, or punctuation."
            ),
            tools=[],
            tool_choice=None,
            parallel_tool_calls=False,
        )
        try:
            response = self._chat_model_create_response(api_key=api_key, prompt=prompt)
        except Exception:
            logger.exception(
                "Chat title generation failed",
                extra={"event": "chat.title_generation.failed"},
            )
            return fallback
        response_text = self._assistant_response_text(response)
        return _clean_generated_chat_title(response_text) or fallback

    def _project_prompt_suffix(self, thread: ChatThreadResponse) -> str:
        instructions = thread.project_instructions.strip()
        if not instructions:
            return ""
        project_name = thread.project_name.strip()
        label = f" for the project '{project_name}'" if project_name else ""
        return (
            f"\n\nAdditional instructions{label}:\n"
            f"{instructions}\n"
            "Treat these instructions as active context for this session."
        )

    def _chat_base_instructions(
        self,
        thread: ChatThreadResponse,
        *,
        include_research_tools: bool,
        include_project_code_tools: bool,
    ) -> str:
        parts = [_load_prompt_asset("v1/shared_base.md")]
        if include_project_code_tools and thread.project_id:
            parts.extend(
                [
                    _load_prompt_asset("v1/core_harness.md"),
                    _load_prompt_asset("v1/coding_core.md"),
                    _load_prompt_asset("v1/project_coding.md"),
                ]
            )
            if include_research_tools:
                parts.append(_load_prompt_asset("v1/research_optional.md"))
        elif include_research_tools:
            parts.append(_load_prompt_asset("v1/research_base.md"))
        return "\n\n".join(part for part in parts if part).strip() + self._project_prompt_suffix(thread)

    def _chat_tool_system_prompt(
        self,
        thread: ChatThreadResponse,
        *,
        include_research_tools: bool,
        include_project_code_tools: bool,
    ) -> str:
        return self._chat_base_instructions(
            thread,
            include_research_tools=include_research_tools,
            include_project_code_tools=include_project_code_tools,
        )

    def _chat_answer_system_prompt(self, *, thread: ChatThreadResponse, has_evidence_sources: bool) -> str:
        del has_evidence_sources
        return self._chat_base_instructions(
            thread,
            include_research_tools=True,
            include_project_code_tools=bool(thread.project_id),
        )

    def _render_compaction_checkpoint(self, item: ChatThreadItemResponse) -> str:
        summary = item.content_text.strip() or "Conversation compacted."
        parts = ["Conversation compaction checkpoint.", "", summary]
        if item.citations:
            citation_lines = [
                f"- [{citation.source_id}] {citation.title} ({citation.source_kind}, {citation.path}): {citation.excerpt}"
                for citation in item.citations
            ]
            parts.extend(["", "## Persisted Source IDs", "", *citation_lines])
        return "\n".join(parts).strip()

    def _paper_selection_prompt_text(self, user_text: str, selection: PaperSelectionPayload) -> str:
        payload: dict[str, Any] = {
            "paper_id": selection.paper_id,
            "page_number": selection.page_number,
            "action": selection.action,
            "section_title": selection.section_title or "",
            "selected_text": (selection.selected_text or "")[:6000],
            "nearby_text": (selection.nearby_text or "")[:8000],
        }
        if selection.region is not None:
            payload["region"] = {
                "bbox": selection.region.bbox.model_dump(),
                "point_count": len(selection.region.points),
                "has_image": bool(selection.region.image_base64),
            }
            if selection.region.image_base64:
                payload["region"]["image_context"] = (
                    "The selected freeform PDF area is attached as an image input after this text block. "
                    "Inspect that crop directly; if selected_text is empty, use the crop and nearby_text."
                )
        return (
            f"{user_text.strip()}\n\n"
            "PDF selection context:\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        ).strip()

    def _selection_image_block(self, selection: PaperSelectionPayload) -> dict[str, Any] | None:
        region = selection.region
        if region is None or not region.image_base64:
            return None
        content_type = region.content_type or "image/png"
        return {
            "type": "input_image",
            "image_url": f"data:{content_type};base64,{region.image_base64}",
            "detail": "high",
        }

    def _chat_content_for_user_item(self, item: ChatThreadItemResponse) -> str | list[dict[str, Any]]:
        content_json = item.content_json if isinstance(item.content_json, dict) else {}
        raw_selection = content_json.get("paper_selection")
        if not isinstance(raw_selection, dict):
            return item.content_text
        try:
            selection = PaperSelectionPayload.model_validate(raw_selection)
        except Exception:
            return item.content_text
        blocks: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": self._paper_selection_prompt_text(item.content_text, selection),
            }
        ]
        image_block = self._selection_image_block(selection)
        if image_block is not None:
            blocks.append(image_block)
        return blocks

    def _chat_input_item_from_thread_item(self, item: ChatThreadItemResponse) -> dict[str, Any] | None:
        if item.item_type == "compaction":
            return {
                "type": "message",
                "role": "developer",
                "content": self._render_compaction_checkpoint(item),
            }
        if item.item_type == "function_call":
            arguments = item.content_text
            if not arguments and item.content_json is not None:
                arguments = json.dumps(item.content_json, ensure_ascii=False)
            return {
                "type": "function_call",
                "call_id": item.call_id or item.item_id,
                "name": item.name or "",
                "arguments": arguments,
            }
        if item.item_type == "function_call_output":
            output = item.content_text
            if not output and item.content_json is not None:
                output = json.dumps(item.content_json, ensure_ascii=False)
            return {
                "type": "function_call_output",
                "call_id": item.call_id or "",
                "output": output,
            }
        if item.item_type == "reasoning":
            if not isinstance(item.content_json, list) or not item.content_json:
                return None
            return {
                "type": "reasoning",
                "id": item.item_id,
                "summary": item.content_json,
            }
        return {
            "type": "message",
            "role": item.role or "assistant",
            "content": self._chat_content_for_user_item(item) if item.item_type == "user_message" else item.content_text,
        }

    def _chat_turn_context(self, prepared: PreparedChatGeneration) -> ChatTurnContext:
        prompt_items = [
            normalized
            for item in prepared.thread.items
            if (normalized := self._chat_input_item_from_thread_item(item)) is not None
        ]
        return ChatTurnContext(
            items=prompt_items,
            active_item_ids=[item.item_id for item in prepared.thread.items],
        )

    def _chat_model_create_response(self, *, api_key: str, prompt: Prompt) -> ResponseEnvelope:
        return self.chat_model.create_response(api_key=api_key, prompt=prompt)

    def _chat_model_stream_response(self, *, api_key: str, prompt: Prompt) -> Iterator[ResponseEvent]:
        return self.chat_model.stream_response(api_key=api_key, prompt=prompt)

    def _chat_model_count_tokens(self, *, api_key: str, prompt: Prompt) -> int | None:
        return self.chat_model.count_prompt_tokens(api_key=api_key, prompt=prompt)

    def _chat_context_needs_compaction(self, *, token_count: int | None) -> bool:
        if token_count is None:
            return False
        context_window = _openai_context_window_tokens(self.settings.openai_model)
        return token_count >= int(context_window * _CHAT_COMPACTION_THRESHOLD_RATIO)

    def _render_compaction_source(
        self,
        messages: list[dict[str, Any]],
        citations: list[ChatCitation],
        turn_state: ChatTurnState,
    ) -> str:
        parts: list[str] = []
        for index, message in enumerate(messages, start=1):
            role = str(message.get("role") or "unknown")
            content = message.get("content")
            if isinstance(content, str):
                rendered = content
            else:
                rendered = json.dumps(content, ensure_ascii=False, indent=2)
            parts.append(f"## Message {index}: {role}\n\n{rendered.strip()}")
        turn_summary = turn_state.visible_summary().strip()
        if turn_summary and turn_summary != "Current turn state:":
            parts.append(f"## Turn State\n\n{turn_summary}")
        if citations:
            citation_lines = [
                f"- [{citation.source_id}] {citation.title} ({citation.source_kind}, {citation.path}): {citation.excerpt}"
                for citation in citations
            ]
            parts.append("## Active Source IDs\n\n" + "\n".join(citation_lines))
        return "\n\n".join(parts).strip()

    def _chat_compaction_prompt(
        self,
        messages: list[dict[str, Any]],
        citations: list[ChatCitation],
        turn_state: ChatTurnState,
    ) -> str:
        source = self._render_compaction_source(messages, citations, turn_state)
        return (
            "Compact the conversation transcript below into a strict GitHub-flavored markdown handoff summary. "
            "Do not answer the user's request. Preserve the user goals, completed work, current turn state, "
            "tool findings and results, source ids that still matter, decisions, constraints, and next steps. "
            "The summary will replace the raw transcript as future context, so include enough detail to continue "
            "the conversation faithfully. Do not mention this instruction.\n\n"
            f"{source}"
        )

    def _create_compaction_placeholder(self, *, thread_id: str, turn_id: str) -> ChatThreadItemResponse:
        return self.repository.add_chat_thread_item(  # type: ignore[attr-defined]
            thread_id=thread_id,
            turn_id=turn_id,
            item_type="compaction",
            role="assistant",
            content_text=_CHAT_COMPACTION_PLACEHOLDER,
            status="in_progress",
        )

    def _finish_chat_compaction(
        self,
        *,
        prepared: PreparedChatGeneration,
        context: ChatTurnContext,
        citation_registry: ChatCitationRegistry,
        turn_state: ChatTurnState,
        placeholder: ChatThreadItemResponse,
    ) -> tuple[ChatTurnContext, ChatThreadItemResponse, ChatThreadResponse]:
        try:
            payload = self._chat_model_create_response(
                api_key=prepared.api_key_value or "",
                prompt=Prompt(
                    input=[
                        {
                            "role": "user",
                            "content": self._chat_compaction_prompt(
                                context.prompt_input,
                                citation_registry.citations,
                                turn_state,
                            ),
                        }
                    ],
                    base_instructions="",
                ),
            )
            summary = "\n\n".join(
                item.content_text
                for item in payload.items
                if item.item_type in {"message", "assistant_message"}
            ).strip()
            if not summary:
                summary = "Conversation compacted, but the compaction model returned an empty summary."
            compaction_message = self.repository.update_chat_thread_item(  # type: ignore[attr-defined]
                placeholder.item_id,
                item_type="compaction",
                phase=None,
                content_text=summary,
                citations=[citation.model_dump() for citation in citation_registry.citations],
                status="completed",
            )
            if compaction_message is None:
                raise RuntimeError("Compaction checkpoint vanished while updating")
            refreshed = self.get_chat_thread(prepared.resolved_user, prepared.thread.thread_id)
            return (
                ChatTurnContext(
                    items=[
                        normalized
                        for normalized in [self._chat_input_item_from_thread_item(compaction_message)]
                        if normalized is not None
                    ],
                    active_item_ids=[compaction_message.item_id],
                    compactions_used=context.compactions_used + 1,
                ),
                compaction_message,
                refreshed,
            )
        except Exception:
            self.repository.update_chat_thread_item(  # type: ignore[attr-defined]
                placeholder.item_id,
                item_type="compaction",
                content_text="Conversation compaction failed.",
                status="failed",
            )
            raise

    def _ensure_chat_context_budget(
        self,
        *,
        prepared: PreparedChatGeneration,
        context: ChatTurnContext,
        citation_registry: ChatCitationRegistry,
        turn_state: ChatTurnState,
        router: ChatToolRouter,
    ) -> tuple[ChatTurnContext, Prompt, list[tuple[str, dict[str, Any]]]]:
        events: list[tuple[str, dict[str, Any]]] = []
        while True:
            prompt = self._chat_sampling_prompt(
                prepared=prepared,
                context=context,
                turn_state=turn_state,
                router=router,
            )
            token_count = self._chat_model_count_tokens(
                api_key=prepared.api_key_value or "",
                prompt=prompt,
            )
            if not self._chat_context_needs_compaction(token_count=token_count):
                return context, prompt, events
            if context.compactions_used >= _CHAT_COMPACTION_MAX_PER_TURN:
                raise RuntimeError("Conversation context is still too large after compaction")
            placeholder = self._create_compaction_placeholder(thread_id=prepared.thread.thread_id, turn_id=prepared.turn.turn_id)
            events.append(("item_started", {"item": placeholder.model_dump(mode="json")}))
            next_context, compaction_message, refreshed = self._finish_chat_compaction(
                prepared=prepared,
                context=context,
                citation_registry=citation_registry,
                turn_state=turn_state,
                placeholder=placeholder,
            )
            del refreshed
            events.append(("item_completed", {"item": compaction_message.model_dump(mode="json")}))
            context = next_context

    def _ensure_chat_context_budget_stream(
        self,
        *,
        prepared: PreparedChatGeneration,
        context: ChatTurnContext,
        citation_registry: ChatCitationRegistry,
        turn_state: ChatTurnState,
        router: ChatToolRouter,
    ) -> Generator[tuple[str, dict[str, Any]], None, tuple[ChatTurnContext, Prompt]]:
        context, prompt, events = self._ensure_chat_context_budget(
            prepared=prepared,
            context=context,
            citation_registry=citation_registry,
            turn_state=turn_state,
            router=router,
        )
        for event in events:
            yield event
        return context, prompt

    def _research_chat_tool_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "paperbase_search",
                "description": "Search fully indexed Paperbase papers by topic, title, abstract, or author. Use this to find candidate papers and result-set ids before narrowing to sections or specific files. Set rerank=true for a slower, higher-recall deep-search pass that also surfaces body-content matches.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                        "category": {"type": "string"},
                        "year": {"type": "string"},
                        "rerank": {"type": "boolean"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "paperbase_search_sections",
                "description": "Search grounded paper sections and captions inside Paperbase. Use this when the user asks about methods, results, experiments, figures, tables, or any specific detail that needs evidence.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 12},
                        "result_set_id": {"type": "string"},
                        "paper_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "paperbase_get_results",
                "description": "Load a previously saved search result set by id. Use this when you already have a result_set_id from an earlier tool call and need the shortlisted papers again.",
                "input_schema": {
                    "type": "object",
                    "properties": {"result_set_id": {"type": "string"}},
                    "required": ["result_set_id"],
                },
            },
            {
                "name": "paperbase_grep_results",
                "description": "Filter a saved result set by a literal or regex-like pattern and get matching previews. Use this after search when you need a narrower subset of papers containing a specific phrase.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "result_set_id": {"type": "string"},
                        "pattern": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "required": ["result_set_id", "pattern"],
                },
            },
            {
                "name": "paperbase_get_paper",
                "description": "Fetch full metadata for a specific paper id. Use this when you need authors, abstract, summary, venue metadata, dates, or citation counts for a paper you already identified.",
                "input_schema": {
                    "type": "object",
                    "properties": {"paper_id": {"type": "string"}},
                    "required": ["paper_id"],
                },
            },
            {
                "name": "paperbase_list_files",
                "description": "List the extracted files available for a paper. Use this when you need to discover which sections, figure captions, raw files, or tables exist before reading one.",
                "input_schema": {
                    "type": "object",
                    "properties": {"paper_id": {"type": "string"}},
                    "required": ["paper_id"],
                },
            },
            {
                "name": "paperbase_read_file",
                "description": "Read the textual content of a specific extracted file for a paper. Use this for section markdown, captions, manifests, or other text files when you need exact content.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "paper_id": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    "required": ["paper_id", "path"],
                },
            },
            {
                "name": "paperbase_get_figure",
                "description": "Fetch figure caption metadata for a paper figure id. Use this when the user asks about a figure or architecture diagram and the caption is the primary evidence.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "paper_id": {"type": "string"},
                        "figure_id": {"type": "string"},
                    },
                    "required": ["paper_id", "figure_id"],
                },
            },
            {
                "name": "paperbase_get_table",
                "description": "Fetch structured table rows and caption metadata for a paper table id. Use this when the user asks for metrics, benchmarks, ablations, or comparisons stored in a table.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "paper_id": {"type": "string"},
                        "table_id": {"type": "string"},
                    },
                    "required": ["paper_id", "table_id"],
                },
            },
            {
                "name": "paperbase_get_related_papers",
                "description": "Fetch related papers for a known paper id. Use this when the user wants similar work, follow-ons, or neighboring papers in the citation graph.",
                "input_schema": {
                    "type": "object",
                    "properties": {"paper_id": {"type": "string"}},
                    "required": ["paper_id"],
                },
            },
            {
                "name": "paperbase_get_references",
                "description": "Fetch the outgoing references for a paper id. Use this when the user asks what a paper cites or wants the citation graph expanded from a known paper.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "paper_id": {"type": "string"},
                        "refresh": {"type": "boolean"},
                    },
                    "required": ["paper_id"],
                },
            },
            {
                "name": "paperbase_list_uploaded_documents",
                "description": "List the user's uploaded documents and tags. Use this when you need to discover which uploads are available before searching within them.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "paperbase_search_uploaded_sections",
                "description": "Search the user's uploaded documents by content and optional tags. Use this when the question may be answered by uploaded notes, PDFs, or documents rather than indexed papers.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 12},
                        "tag_filters": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["query"],
                },
            },
        ]

    def _project_chat_tool_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "project_list_files",
                "description": "List the current project's repo-relative workspace text files, including Python, notebooks, and other text files created earlier in this chat. Use this before edits when you need to discover which files already exist.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "project_read_file",
                "description": "Read a specific workspace text file from the current project, optionally limited to a line range. Read files before patching them.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "repo_path": {"type": "string"},
                        "start_line": {"type": "integer", "minimum": 1},
                        "end_line": {"type": "integer", "minimum": 1},
                    },
                    "required": ["repo_path"],
                },
            },
            {
                "name": "project_grep",
                "description": "Search the current project's workspace text files by regex or literal pattern and return matching lines with paths and line numbers.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "literal": {"type": "boolean"},
                        "case_sensitive": {"type": "boolean"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "required": ["pattern"],
                },
            },
            {
                "name": "project_write_file",
                "description": "Create or replace one repo-relative workspace text file in the current project. Use this when you want to write the full file content in one step.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "repo_path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["repo_path"],
                },
            },
            {
                "name": "project_apply_patch",
                "description": "Apply an exact-match text patch to one workspace text file. Fails if the old text is missing or ambiguous unless replace_all=true. If the file is empty, you may use old_string='' once to populate it.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "repo_path": {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                        "replace_all": {"type": "boolean"},
                    },
                    "required": ["repo_path", "old_string", "new_string"],
                },
            },
            {
                "name": "project_move",
                "description": "Move or rename one workspace file to a new repo-relative path. Use this for renames and path moves only when the destination should replace the current path exactly.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "repo_path": {"type": "string"},
                        "new_repo_path": {"type": "string"},
                    },
                    "required": ["repo_path", "new_repo_path"],
                },
            },
            {
                "name": "project_delete",
                "description": "Delete one workspace file from the current project by exact repo-relative path. Use this only when the file truly needs deletion, such as an explicit user request or a superseded rename path.",
                "input_schema": {
                    "type": "object",
                    "properties": {"repo_path": {"type": "string"}},
                    "required": ["repo_path"],
                },
            },
            {
                "name": "project_git_status",
                "description": "Compare the current project workspace against the last synced project repository state when available, or otherwise report the current local-only workspace status.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "project_git_diff",
                "description": "Show a unified diff between the current workspace files and the last synced project repository state when available, or otherwise against an empty baseline.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "project_exec",
                "description": "Start a long-running command inside the current project's workspace snapshot and return an exec_id that can be polled later.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "project_wait",
                "description": "Wait for a previously started project_exec session and return its latest status and logs.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "exec_id": {"type": "string"},
                        "timeout_seconds": {"type": "integer", "minimum": 0, "maximum": 60},
                    },
                    "required": ["exec_id"],
                },
            },
            {
                "name": "project_cancel",
                "description": "Cancel a running project_exec session by exec_id.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "exec_id": {"type": "string"},
                    },
                    "required": ["exec_id"],
                },
            },
            {
                "name": "project_logs",
                "description": "Read the latest logs for a project_exec session by exec_id.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "exec_id": {"type": "string"},
                    },
                    "required": ["exec_id"],
                },
            },
            {
                "name": "project_test",
                "description": "Run the project's tests when they exist, or fall back to a compile-style verification pass for Python files.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "project_lint",
                "description": "Run lint or a compile-style syntax pass across the project workspace.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "project_run",
                "description": "Run one project Python entrypoint with run mode smoke or full. This is a compatibility alias over the stateful execution flow for simple cases.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "repo_path": {"type": "string"},
                        "mode": {"type": "string", "enum": ["smoke", "full"]},
                    },
                    "required": ["repo_path"],
                },
            },
        ]

    def _chat_tool_router(
        self,
        request: SendChatMessageRequest,
        *,
        thread: ChatThreadResponse | None = None,
    ) -> ChatToolRouter:
        entries: list[ChatToolSpecEntry] = []
        if _should_offer_research_chat_tools(request.content, request):
            entries.extend(ChatToolSpecEntry(spec=tool) for tool in self._research_chat_tool_specs())
        if _should_offer_project_code_tools(request.content, thread=thread):
            entries.extend(ChatToolSpecEntry(spec=tool) for tool in self._project_chat_tool_specs())
        return ChatToolRouter(
            entries,
            aliases={
                "project_touch": "project_write_file",
                "project_edit": "project_apply_patch",
            },
        )

    def _chat_sampling_prompt(
        self,
        *,
        prepared: PreparedChatGeneration,
        context: ChatTurnContext,
        turn_state: ChatTurnState,
        router: ChatToolRouter,
    ) -> Prompt:
        tools = router.model_visible_specs()
        base_instructions = self._chat_base_instructions(
            prepared.thread,
            include_research_tools=router.has_tool_prefix("paperbase_"),
            include_project_code_tools=router.has_tool_prefix("project_"),
        )
        turn_summary = turn_state.visible_summary().strip()
        if turn_summary and turn_summary != "Current turn state:":
            base_instructions = f"{base_instructions}\n\n{turn_summary}"
        return Prompt(
            input=context.prompt_input,
            base_instructions=base_instructions,
            tools=tools,
            tool_choice="auto" if tools else None,
            parallel_tool_calls=router.parallel_tool_calls_enabled(),
        )

    def _chat_tool_definitions(
        self,
        request: SendChatMessageRequest,
        *,
        thread: ChatThreadResponse | None = None,
    ) -> list[dict[str, Any]]:
        return self._chat_tool_router(request, thread=thread).model_visible_specs()

    def _paper_path_citation(
        self,
        citation_registry: ChatCitationRegistry,
        *,
        paper_id: str,
        title: str,
        path: str,
        content: str,
        question: str,
    ) -> ChatCitation:
        return citation_registry.register(
            key=("paperbase_paper", f"{paper_id}:{path}"),
            source_kind="paperbase_paper",
            title=title,
            path=path,
            excerpt=_best_excerpt(content, question),
            paper_id=paper_id,
        )

    def _uploaded_citation(
        self,
        citation_registry: ChatCitationRegistry,
        *,
        document_id: str,
        title: str,
        path: str,
        content: str,
        question: str,
        tags: list[str],
    ) -> ChatCitation:
        return citation_registry.register(
            key=("uploaded_document", f"{document_id}:{path}"),
            source_kind="uploaded_document",
            title=title,
            path=path,
            excerpt=_best_excerpt(content, question),
            document_id=document_id,
            tags=tags,
        )

    def _execute_chat_tool(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any],
        user: BrowserUser,
        citation_registry: ChatCitationRegistry,
        thread_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        if tool_name == "paperbase_search":
            query = str(tool_input.get("query") or "").strip()
            # Chat-tool searches are synthetic LLM reformulations, not real user
            # queries, so they must never pollute the search_logs benchmark dataset.
            request = SearchRequest(
                query=query,
                limit=_tool_int(tool_input.get("limit"), default=8, maximum=20),
                category=(str(tool_input.get("category") or "").strip() or None),
                year=(str(tool_input.get("year") or "").strip() or None),
                include_live_sources=_tool_bool(tool_input.get("include_live_sources"), default=False),
                rerank=_tool_bool(tool_input.get("rerank"), default=False),
                log_query=False,
            )
            response = self.search(request)
            papers = []
            for paper in response.papers:
                summary_text = paper.summary_short or paper.tldr or paper.abstract or paper.title
                abstract_excerpt, truncated = _truncate_tool_text(paper.abstract or summary_text, limit=900)
                papers.append(
                    {
                        "paper_id": paper.paper_id,
                        "title": paper.title,
                        "authors": paper.authors,
                        "published": paper.published,
                        "summary_short": paper.summary_short,
                        "tldr": paper.tldr,
                        "abstract_excerpt": abstract_excerpt,
                        "abstract_truncated": truncated,
                        "citation_count": paper.citation_count,
                        "influential_citation_count": paper.influential_citation_count,
                        "source": paper.source,
                        "score": paper.score,
                        "index_status": paper.index_status,
                    }
                )
            return {
                "query": response.query,
                "result_set_id": response.result_set_id,
                "expires_at": response.expires_at.isoformat(),
                "papers": papers,
            }

        if tool_name == "paperbase_search_sections":
            query = str(tool_input.get("query") or "").strip()
            raw_paper_ids = tool_input.get("paper_ids")
            paper_ids = [str(value) for value in raw_paper_ids] if isinstance(raw_paper_ids, list) else None
            response = self.search_sections(
                SearchSectionRequest(
                    query=query,
                    limit=_tool_int(tool_input.get("limit"), default=8, maximum=12),
                    result_set_id=(str(tool_input.get("result_set_id") or "").strip() or None),
                    paper_ids=paper_ids or None,
                )
            )
            sections = []
            for hit in response.sections:
                citation = self._paper_path_citation(
                    citation_registry,
                    paper_id=hit.paper_id,
                    title=hit.title,
                    path=hit.path,
                    content=hit.preview,
                    question=query,
                )
                sections.append(
                    {
                        "source_id": citation.source_id,
                        "paper_id": hit.paper_id,
                        "title": hit.title,
                        "path": hit.path,
                        "chunk_kind": hit.chunk_kind,
                        "preview": hit.preview,
                        "score": hit.score,
                    }
                )
            return {"query": response.query, "sections": sections}

        if tool_name == "paperbase_get_results":
            result_set_id = str(tool_input.get("result_set_id") or "").strip()
            response = self.get_results(result_set_id)
            papers = []
            for paper in response.papers:
                papers.append(
                    {
                        "paper_id": paper.paper_id,
                        "title": paper.title,
                        "summary_short": paper.summary_short,
                        "tldr": paper.tldr,
                        "source": paper.source,
                        "score": paper.score,
                    }
                )
            return {
                "result_set_id": response.result_set_id,
                "query": response.query,
                "expires_at": response.expires_at.isoformat(),
                "papers": papers,
            }

        if tool_name == "paperbase_grep_results":
            result_set_id = str(tool_input.get("result_set_id") or "").strip()
            pattern = str(tool_input.get("pattern") or "").strip()
            response = self.grep_results(
                result_set_id,
                pattern,
                limit=_tool_int(tool_input.get("limit"), default=10, maximum=50),
            )
            papers = []
            for match in response.papers:
                papers.append(
                    {
                        "paper_id": match.paper_id,
                        "title": match.title,
                        "match_count": match.match_count,
                        "previews": match.previews[:5],
                    }
                )
            return {
                "result_set_id": response.result_set_id,
                "parent_result_set_id": response.parent_result_set_id,
                "pattern": response.pattern,
                "papers": papers,
                "expires_at": response.expires_at.isoformat(),
            }

        if tool_name == "paperbase_get_paper":
            paper_id = str(tool_input.get("paper_id") or "").strip()
            paper = self.get_indexed_paper(paper_id)
            return {
                "paper": paper.model_dump(mode="json"),
            }

        if tool_name == "paperbase_list_files":
            paper_id = str(tool_input.get("paper_id") or "").strip()
            files = self.list_files(paper_id)
            return {
                "paper_id": paper_id,
                "files": [item.model_dump(mode="json") for item in files.files[:_CHAT_TOOL_MAX_FILE_LIST]],
                "truncated": len(files.files) > _CHAT_TOOL_MAX_FILE_LIST,
            }

        if tool_name == "paperbase_read_file":
            paper_id = str(tool_input.get("paper_id") or "").strip()
            path = str(tool_input.get("path") or "").strip()
            file_response = self.read_file(paper_id, path)
            paper = self.get_paper(paper_id)
            trimmed, truncated = _truncate_tool_text(file_response.content, limit=_CHAT_TOOL_MAX_FILE_CHARS)
            citation = self._paper_path_citation(
                citation_registry,
                paper_id=paper_id,
                title=paper.title,
                path=path,
                content=trimmed,
                question=path,
            )
            return {
                "source_id": citation.source_id,
                "paper_id": paper_id,
                "path": path,
                "content": trimmed,
                "truncated": truncated,
            }

        if tool_name == "paperbase_get_figure":
            paper_id = str(tool_input.get("paper_id") or "").strip()
            figure_id = str(tool_input.get("figure_id") or "").strip()
            figure = self.get_figure(paper_id, figure_id)
            paper = self.get_paper(paper_id)
            path = f"figures/{figure_id}/caption.md"
            citation = self._paper_path_citation(
                citation_registry,
                paper_id=paper_id,
                title=paper.title,
                path=path,
                content=figure.caption,
                question=figure_id,
            )
            return {
                "source_id": citation.source_id,
                "paper_id": paper_id,
                "figure_id": figure.figure_id,
                "caption": figure.caption,
                "image_url": figure.image_url,
                "content_type": figure.content_type,
            }

        if tool_name == "paperbase_get_table":
            paper_id = str(tool_input.get("paper_id") or "").strip()
            table_id = str(tool_input.get("table_id") or "").strip()
            table = self.get_table(paper_id, table_id)
            paper = self.get_paper(paper_id)
            limited_rows = table.rows[:_CHAT_TOOL_MAX_TABLE_ROWS]
            table_text = "\n".join(" | ".join(row) for row in limited_rows[:6])
            citation = self._paper_path_citation(
                citation_registry,
                paper_id=paper_id,
                title=paper.title,
                path=f"tables/{table_id}.json",
                content=f"{table.caption or ''}\n{table_text}".strip(),
                question=table_id,
            )
            return {
                "source_id": citation.source_id,
                "paper_id": paper_id,
                "table_id": table.table_id,
                "caption": table.caption,
                "rows": limited_rows,
                "truncated": len(table.rows) > len(limited_rows),
                "csv_url": table.csv_url,
            }

        if tool_name == "paperbase_get_related_papers":
            paper_id = str(tool_input.get("paper_id") or "").strip()
            papers = self.get_related_papers(paper_id)
            related = []
            for paper in papers:
                related.append(
                    {
                        "paper_id": paper.paper_id,
                        "title": paper.title,
                        "summary_short": paper.summary_short,
                        "tldr": paper.tldr,
                        "source": paper.source,
                    }
                )
            return {"paper_id": paper_id, "related_papers": related}

        if tool_name == "paperbase_get_references":
            paper_id = str(tool_input.get("paper_id") or "").strip()
            refresh = _tool_bool(tool_input.get("refresh"), default=False)
            references = self.get_paper_references(paper_id, refresh=refresh)
            return {
                "paper_id": paper_id,
                "references": references[:50],
                "truncated": len(references) > 50,
            }

        if tool_name == "paperbase_list_uploaded_documents":
            uploads = self.list_uploaded_documents(user)
            return {
                "documents": [document.model_dump(mode="json") for document in uploads.documents],
                "available_tags": uploads.available_tags,
            }

        if tool_name == "paperbase_search_uploaded_sections":
            query = str(tool_input.get("query") or "").strip()
            raw_tags = tool_input.get("tag_filters")
            tag_filters = [str(value) for value in raw_tags] if isinstance(raw_tags, list) else []
            hits = self.repository.search_uploaded_document_sections(  # type: ignore[attr-defined]
                user_id=user.user_id,
                query=query,
                limit=_tool_int(tool_input.get("limit"), default=8, maximum=12),
                tag_filters=tag_filters,
            )
            sections = []
            for hit in hits:
                citation = self._uploaded_citation(
                    citation_registry,
                    document_id=hit.document_id,
                    title=hit.title,
                    path=hit.path,
                    content=hit.preview,
                    question=query,
                    tags=hit.tags,
                )
                sections.append(
                    {
                        "source_id": citation.source_id,
                        "document_id": hit.document_id,
                        "title": hit.title,
                        "path": hit.path,
                        "chunk_kind": hit.chunk_kind,
                        "preview": hit.preview,
                        "tags": hit.tags,
                        "score": hit.score,
                    }
                )
            return {"query": query, "sections": sections}

        if tool_name.startswith("project_"):
            if not project_id:
                raise HTTPException(status_code=409, detail="Project-scoped coding tools require a project thread")

            if tool_name == "project_list_files":
                project_files = self.list_project_workspace_files(user, project_id)
                return {
                    "project_id": project_id,
                    "files": [
                        {
                            "file_id": project_file.file_id,
                            "repo_path": project_file.repo_path,
                            "filename": project_file.filename,
                            "file_kind": project_file.file_kind,
                            "updated_at": project_file.updated_at.isoformat(),
                        }
                        for project_file in project_files[:_CHAT_TOOL_MAX_FILE_LIST]
                    ],
                    "truncated": len(project_files) > _CHAT_TOOL_MAX_FILE_LIST,
                }

            if tool_name == "project_read_file":
                read_result = self.read_project_workspace_file(
                    user,
                    project_id,
                    str(tool_input.get("repo_path") or "").strip(),
                    start_line=tool_input.get("start_line") if tool_input.get("start_line") is not None else None,
                    end_line=tool_input.get("end_line") if tool_input.get("end_line") is not None else None,
                )
                trimmed, truncated = _truncate_tool_text(read_result["content"], limit=_CHAT_TOOL_MAX_FILE_CHARS)
                return {
                    **read_result,
                    "content": trimmed,
                    "truncated": truncated,
                }

            if tool_name == "project_grep":
                return self.grep_project_workspace(
                    user,
                    project_id,
                    pattern=str(tool_input.get("pattern") or "").strip(),
                    literal=_tool_bool(tool_input.get("literal"), default=False),
                    case_sensitive=_tool_bool(tool_input.get("case_sensitive"), default=False),
                    limit=_tool_int(tool_input.get("limit"), default=20, maximum=50),
                )

            if tool_name in {"project_write_file", "project_touch"}:
                updated = self.write_project_workspace_file(
                    user,
                    project_id,
                    repo_path=str(tool_input.get("repo_path") or "").strip(),
                    content=str(tool_input.get("content") or ""),
                )
                trimmed, truncated = _truncate_tool_text(str(updated.get("content") or ""), limit=_CHAT_TOOL_MAX_FILE_CHARS)
                return {
                    **updated,
                    "project_id": project_id,
                    "content": trimmed,
                    "truncated": truncated,
                }

            if tool_name in {"project_apply_patch", "project_edit"}:
                updated = self.apply_patch_project_workspace_file(
                    user,
                    project_id,
                    repo_path=str(tool_input.get("repo_path") or "").strip(),
                    old_string=str(tool_input.get("old_string") or ""),
                    new_string=str(tool_input.get("new_string") or ""),
                    replace_all=_tool_bool(tool_input.get("replace_all"), default=False),
                )
                trimmed, truncated = _truncate_tool_text(str(updated.get("content") or ""), limit=_CHAT_TOOL_MAX_FILE_CHARS)
                return {
                    **updated,
                    "content": trimmed,
                    "truncated": truncated,
                }

            if tool_name == "project_move":
                return self.move_project_workspace_file(
                    user,
                    project_id,
                    repo_path=str(tool_input.get("repo_path") or "").strip(),
                    new_repo_path=str(tool_input.get("new_repo_path") or "").strip(),
                )

            if tool_name == "project_delete":
                return self.delete_project_workspace_file(
                    user,
                    project_id,
                    repo_path=str(tool_input.get("repo_path") or "").strip(),
                )

            if tool_name == "project_git_status":
                return self.project_git_status(user, project_id)

            if tool_name == "project_git_diff":
                return self.project_git_diff(user, project_id)

            if tool_name == "project_exec":
                if not thread_id:
                    raise HTTPException(status_code=409, detail="Project exec requires a thread id")
                return self.project_exec(
                    user,
                    thread_id=thread_id,
                    project_id=project_id,
                    command=str(tool_input.get("command") or ""),
                )

            if tool_name == "project_wait":
                if not thread_id:
                    raise HTTPException(status_code=409, detail="Project exec requires a thread id")
                return self.project_wait(
                    user,
                    thread_id=thread_id,
                    project_id=project_id,
                    exec_id=str(tool_input.get("exec_id") or ""),
                    timeout_seconds=_tool_int(
                        tool_input.get("timeout_seconds"),
                        default=_PROJECT_EXEC_DEFAULT_WAIT_SECONDS,
                        minimum=0,
                        maximum=_PROJECT_EXEC_MAX_WAIT_SECONDS,
                    ),
                )

            if tool_name == "project_cancel":
                if not thread_id:
                    raise HTTPException(status_code=409, detail="Project exec requires a thread id")
                return self.project_cancel(
                    user,
                    thread_id=thread_id,
                    project_id=project_id,
                    exec_id=str(tool_input.get("exec_id") or ""),
                )

            if tool_name == "project_logs":
                if not thread_id:
                    raise HTTPException(status_code=409, detail="Project exec requires a thread id")
                return self.project_logs(
                    user,
                    thread_id=thread_id,
                    project_id=project_id,
                    exec_id=str(tool_input.get("exec_id") or ""),
                )

            if tool_name == "project_test":
                if not thread_id:
                    raise HTTPException(status_code=409, detail="Project exec requires a thread id")
                return self.project_test_tool(user, thread_id=thread_id, project_id=project_id)

            if tool_name == "project_lint":
                if not thread_id:
                    raise HTTPException(status_code=409, detail="Project exec requires a thread id")
                return self.project_lint_tool(user, thread_id=thread_id, project_id=project_id)

            if tool_name == "project_run":
                mode = str(tool_input.get("mode") or "full").strip().lower() or "full"
                if not thread_id:
                    raise HTTPException(status_code=409, detail="Project exec requires a thread id")
                return self.project_run_tool(
                    user,
                    thread_id=thread_id,
                    project_id=project_id,
                    repo_path=str(tool_input.get("repo_path") or "").strip(),
                    mode=mode,
                )

        raise ValueError(f"Unknown tool: {tool_name}")

    def _chat_tool_activity_label(self, *, tool_name: str, tool_input: dict[str, Any]) -> str:
        def detail(value: Any, *, limit: int = 56) -> str | None:
            text = str(value or "").strip()
            if not text:
                return None
            if len(text) > limit:
                return f"{text[: limit - 1].rstrip()}…"
            return text

        query = detail(tool_input.get("query"))
        path = detail(tool_input.get("path")) or detail(tool_input.get("repo_path"))
        new_path = detail(tool_input.get("new_repo_path"))
        pattern = detail(tool_input.get("pattern"))
        figure_id = detail(tool_input.get("figure_id"))
        table_id = detail(tool_input.get("table_id"))
        exec_id = detail(tool_input.get("exec_id"))

        if tool_name == "paperbase_search":
            return f'paper search for "{query}"' if query else "paper search"
        if tool_name == "paperbase_search_sections":
            return f'section search for "{query}"' if query else "section search"
        if tool_name == "paperbase_get_results":
            return "result lookup"
        if tool_name == "paperbase_grep_results":
            return f'result filtering for "{pattern}"' if pattern else "result filtering"
        if tool_name == "paperbase_get_paper":
            return "paper lookup"
        if tool_name == "paperbase_list_files":
            return "file listing"
        if tool_name == "paperbase_read_file":
            return f'file read for "{path}"' if path else "file read"
        if tool_name == "paperbase_get_figure":
            return f'figure lookup for "{figure_id}"' if figure_id else "figure lookup"
        if tool_name == "paperbase_get_table":
            return f'table lookup for "{table_id}"' if table_id else "table lookup"
        if tool_name == "paperbase_get_related_papers":
            return "related-paper lookup"
        if tool_name == "paperbase_get_references":
            return "reference lookup"
        if tool_name == "paperbase_list_uploaded_documents":
            return "upload listing"
        if tool_name == "paperbase_search_uploaded_sections":
            return f'uploaded-note search for "{query}"' if query else "uploaded-note search"
        if tool_name == "project_list_files":
            return "project file listing"
        if tool_name == "project_read_file":
            return f'project file read for "{path}"' if path else "project file read"
        if tool_name == "project_grep":
            return f'project grep for "{pattern}"' if pattern else "project grep"
        if tool_name in {"project_write_file", "project_touch"}:
            return f'project file write for "{path}"' if path else "project file write"
        if tool_name in {"project_apply_patch", "project_edit"}:
            return f'project file patch for "{path}"' if path else "project file patch"
        if tool_name == "project_move":
            if path and new_path:
                return f'project file move from "{path}" to "{new_path}"'
            return "project file move"
        if tool_name == "project_delete":
            return f'project file delete for "{path}"' if path else "project file delete"
        if tool_name == "project_git_status":
            return "project git status"
        if tool_name == "project_git_diff":
            return "project git diff"
        if tool_name == "project_exec":
            return "project exec"
        if tool_name == "project_wait":
            return f'project exec wait for "{exec_id}"' if exec_id else "project exec wait"
        if tool_name == "project_cancel":
            return f'project exec cancel for "{exec_id}"' if exec_id else "project exec cancel"
        if tool_name == "project_logs":
            return f'project exec logs for "{exec_id}"' if exec_id else "project exec logs"
        if tool_name == "project_test":
            return "project test"
        if tool_name == "project_lint":
            return "project lint"
        if tool_name == "project_run":
            return f'project run for "{path}"' if path else "project run"
        return "tool call"

    def _chat_tool_activity_command(self, *, tool_name: str, tool_input: dict[str, Any]) -> str:
        rendered_input = json.dumps(tool_input, ensure_ascii=False, indent=2)
        return f"{tool_name}({rendered_input})"

    def _chat_tool_activity_output(self, payload: Any) -> str:
        if isinstance(payload, (dict, list)):
            return json.dumps(payload, ensure_ascii=False, indent=2)
        return str(payload)

    def _run_single_chat_tool(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any],
        user: BrowserUser,
        citation_registry: ChatCitationRegistry,
        thread_id: str | None = None,
        project_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        try:
            payload = self._execute_chat_tool(
                tool_name=tool_name,
                tool_input=tool_input,
                user=user,
                citation_registry=citation_registry,
                thread_id=thread_id,
                project_id=project_id,
            )
            return payload, False
        except HTTPException as exc:
            return {"error": str(exc.detail)}, True
        except Exception as exc:
            logger.exception(
                "Chat tool execution failed",
                extra={"event": "chat.tool.failed", "tool_name": tool_name},
            )
            return {"error": str(exc) or "Tool execution failed"}, True

    def _tool_result_blocks(
        self,
        *,
        tool_uses: list[dict[str, Any]],
        user: BrowserUser,
        citation_registry: ChatCitationRegistry,
        thread_id: str | None = None,
        project_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool, bool]:
        blocks: list[dict[str, Any]] = []
        project_write_completed = False
        project_read_completed = False
        for tool_use in tool_uses:
            tool_use_id = str(tool_use.get("id") or "").strip()
            if not tool_use_id:
                continue
            tool_name = str(tool_use.get("name") or "").strip()
            tool_input = tool_use.get("input") if isinstance(tool_use.get("input"), dict) else {}
            payload, is_error = self._run_single_chat_tool(
                tool_name=tool_name,
                tool_input=tool_input,
                user=user,
                citation_registry=citation_registry,
                thread_id=thread_id,
                project_id=project_id,
            )
            project_write_completed = project_write_completed or _project_code_tool_use_completed_write(
                {"name": tool_name, "input": tool_input},
                is_error=is_error,
            )
            project_read_completed = project_read_completed or _project_code_tool_use_completed_read(
                {"name": tool_name, "input": tool_input},
                is_error=is_error,
            )
            block = {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": json.dumps(payload, ensure_ascii=False),
            }
            if is_error:
                block["is_error"] = True
            blocks.append(block)
        return blocks, project_write_completed, project_read_completed

    def _workspace_file_content_or_none(self, user: BrowserUser, project_id: str, repo_path: str) -> str | None:
        try:
            return self._get_project_workspace_file(user, project_id, repo_path).content
        except HTTPException as exc:
            if exc.status_code == 404:
                return None
            raise

    def _capture_turn_state_before_tool(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any],
        user: BrowserUser,
        project_id: str | None,
        turn_state: ChatTurnState,
    ) -> None:
        if not project_id:
            return
        if tool_name in {"project_read_file"}:
            repo_path = str(tool_input.get("repo_path") or "").strip()
            if repo_path:
                turn_state.note_inspected(repo_path)
            return
        if tool_name == "project_list_files":
            return
        if tool_name == "project_grep":
            return
        if tool_name in {"project_write_file", "project_touch", "project_apply_patch", "project_edit", "project_delete"}:
            repo_path = str(tool_input.get("repo_path") or "").strip()
            if repo_path:
                normalized = _normalize_project_workspace_repo_path(repo_path)
                before = self._workspace_file_content_or_none(user, project_id, normalized)
                turn_state.diff_tracker.record(repo_path=normalized, before=before, after=before)
            return
        if tool_name == "project_move":
            repo_path = str(tool_input.get("repo_path") or "").strip()
            new_repo_path = str(tool_input.get("new_repo_path") or "").strip()
            if repo_path:
                normalized = _normalize_project_workspace_repo_path(repo_path)
                before = self._workspace_file_content_or_none(user, project_id, normalized)
                turn_state.diff_tracker.record(repo_path=normalized, before=before, after=before)
            if new_repo_path:
                normalized_new = _normalize_project_workspace_repo_path(new_repo_path)
                before_new = self._workspace_file_content_or_none(user, project_id, normalized_new)
                turn_state.diff_tracker.record(repo_path=normalized_new, before=before_new, after=before_new)

    def _update_turn_state_from_tool_result(
        self,
        *,
        tool_name: str,
        payload: dict[str, Any],
        is_error: bool,
        user: BrowserUser,
        project_id: str | None,
        turn_state: ChatTurnState,
    ) -> None:
        if is_error:
            return
        if tool_name == "project_list_files":
            files = payload.get("files")
            if isinstance(files, list):
                turn_state.note_inspected(
                    *[
                        str(item.get("repo_path") or "").strip()
                        for item in files
                        if isinstance(item, dict) and str(item.get("repo_path") or "").strip()
                    ]
                )
            return
        if tool_name == "project_read_file":
            repo_path = str(payload.get("repo_path") or "").strip()
            if repo_path:
                turn_state.note_inspected(repo_path)
            return
        if tool_name == "project_grep":
            matches = payload.get("matches")
            if isinstance(matches, list):
                turn_state.note_inspected(
                    *[
                        str(item.get("repo_path") or "").strip()
                        for item in matches
                        if isinstance(item, dict) and str(item.get("repo_path") or "").strip()
                    ]
                )
            return
        if tool_name in {"project_write_file", "project_touch", "project_apply_patch", "project_edit"}:
            repo_path = str(payload.get("repo_path") or "").strip()
            if repo_path and project_id:
                turn_state.diff_tracker.current_contents[repo_path] = self._workspace_file_content_or_none(
                    user,
                    project_id,
                    repo_path,
                )
            return
        if tool_name == "project_delete":
            repo_path = str(payload.get("repo_path") or "").strip()
            if repo_path:
                turn_state.diff_tracker.current_contents[repo_path] = None
            return
        if tool_name == "project_move":
            previous_repo_path = str(payload.get("previous_repo_path") or "").strip()
            repo_path = str(payload.get("repo_path") or "").strip()
            if previous_repo_path:
                turn_state.diff_tracker.current_contents[previous_repo_path] = None
            if repo_path and project_id:
                turn_state.diff_tracker.current_contents[repo_path] = self._workspace_file_content_or_none(
                    user,
                    project_id,
                    repo_path,
                )
            return
        if tool_name in {"project_exec", "project_wait", "project_cancel", "project_logs", "project_test", "project_lint", "project_run"}:
            exec_id = str(payload.get("exec_id") or "").strip()
            status = str(payload.get("status") or "").strip().lower()
            if exec_id:
                turn_state.note_exec_session(exec_id, running=status in {"running", "queued"})

    def _thread_item_type_from_response_item(self, item: ResponseItem) -> str:
        mapping = {
            "assistant_message": "assistant_message",
            "message": "assistant_message",
            "reasoning": "reasoning",
            "function_call": "function_call",
            "function_call_output": "function_call_output",
            "custom_tool_call": "custom_tool_call",
            "custom_tool_call_output": "custom_tool_call_output",
            "tool_search_call": "tool_search_call",
            "tool_search_output": "tool_search_output",
            "web_search_call": "web_search_call",
        }
        return mapping.get(item.item_type, "other")

    def _thread_item_role_from_response_item(self, item: ResponseItem) -> str:
        item_type = self._thread_item_type_from_response_item(item)
        if item_type in {"function_call_output", "custom_tool_call_output"}:
            return "tool"
        if item_type in {"function_call", "custom_tool_call", "tool_search_call", "web_search_call"}:
            return "assistant"
        return item.role or "assistant"

    def _response_item_to_thread_item_kwargs(self, item: ResponseItem) -> dict[str, Any]:
        return {
            "item_type": self._thread_item_type_from_response_item(item),
            "role": self._thread_item_role_from_response_item(item),
            "phase": item.phase or None,
            "call_id": item.call_id or item.item_id,
            "name": item.name or None,
            "content_text": item.content_text,
            "content_json": item.content_json,
            "status": item.status or "completed",
        }

    def _assistant_message_phase(
        self,
        item: ChatThreadItemResponse,
        *,
        is_last_assistant_item: bool,
        pending_tool_calls: bool,
    ) -> str:
        if item.phase in {"commentary", "final_answer"}:
            return item.phase
        if not is_last_assistant_item:
            return "commentary"
        return "commentary" if pending_tool_calls else "final_answer"

    def _chat_runtime_mode(self) -> str:
        mode = str(getattr(self.settings, "chat_runtime_mode", "codex_kernel") or "codex_kernel").strip().lower()
        return "legacy" if mode == "legacy" else "codex_kernel"

    def _citation_registry_key(self, citation: ChatCitation) -> tuple[str, str]:
        if citation.source_kind == "paperbase_paper":
            return ("paperbase_paper", f"{citation.paper_id or citation.title}:{citation.path}")
        if citation.source_kind == "uploaded_document":
            return ("uploaded_document", f"{citation.document_id or citation.title}:{citation.path}")
        return (citation.source_kind, f"{citation.title}:{citation.path}:{citation.source_id}")

    def _seed_chat_citation_registry(
        self,
        citation_registry: ChatCitationRegistry,
        *,
        items: list[ChatThreadItemResponse],
    ) -> None:
        for item in items:
            for citation in item.citations:
                citation_registry.seed(
                    key=self._citation_registry_key(citation),
                    citation=citation,
                )

    def _persist_response_item(
        self,
        *,
        thread_id: str,
        turn_id: str,
        item: ResponseItem,
        status: str | None = None,
    ) -> ChatThreadItemResponse:
        return self.repository.add_chat_thread_item(  # type: ignore[attr-defined]
            thread_id=thread_id,
            turn_id=turn_id,
            item_id=new_id("item"),
            **{
                **self._response_item_to_thread_item_kwargs(item),
                **({"status": status} if status is not None else {}),
            },
        )

    def _log_chat_turn_progress(
        self,
        *,
        event: str,
        message: str,
        prepared: PreparedChatGeneration,
        sample_index: int | None = None,
        item: ChatThreadItemResponse | ResponseItem | None = None,
        **metadata: Any,
    ) -> None:
        extra: dict[str, Any] = {
            "event": event,
            "thread_id": prepared.thread.thread_id,
            "turn_id": prepared.turn.turn_id,
            "project_id": prepared.thread.project_id or "",
        }
        if sample_index is not None:
            extra["sample_index"] = sample_index
        if item is not None:
            extra["item_id"] = getattr(item, "item_id", "")
            extra["item_type"] = getattr(item, "item_type", "")
            extra["item_role"] = getattr(item, "role", "")
            extra["item_status"] = getattr(item, "status", "")
            extra["call_id"] = getattr(item, "call_id", "")
            extra["item_name"] = getattr(item, "name", "")
            phase = getattr(item, "phase", None)
            if phase:
                extra["item_phase"] = phase
        for key, value in metadata.items():
            if value is not None:
                extra[key] = value
        logger.info(message, extra=extra)

    def _run_chat_turn_engine(
        self,
        *,
        prepared: PreparedChatGeneration,
        user: BrowserUser,
        request: SendChatMessageRequest,
        stream_output: bool = False,
    ) -> Generator[tuple[str, dict[str, Any]], None, ChatTurnExecutionResult]:
        del stream_output
        context = self._chat_turn_context(prepared)
        citation_registry = ChatCitationRegistry()
        self._seed_chat_citation_registry(citation_registry, items=prepared.thread.items)
        router = self._chat_tool_router(request, thread=prepared.thread)
        turn_state = ChatTurnState(active_goal=prepared.user_item.content_text)
        aggregated_usage: ChatModelUsage | None = None
        self._log_chat_turn_progress(
            event="chat.turn.started",
            message="Chat turn started",
            prepared=prepared,
            prompt_item_count=len(context.items),
            direct_tool_count=len(router.model_visible_specs()),
            deferred_tool_count=len(router.deferred_specs()),
        )

        # Match Codex's task loop: keep sampling while the model emits tool calls.
        # Completion is the first model response that yields no tool follow-up.
        _sample_index = 0
        while True:
            context, prompt, compaction_events = self._ensure_chat_context_budget(
                prepared=prepared,
                context=context,
                citation_registry=citation_registry,
                turn_state=turn_state,
                router=router,
            )
            self._log_chat_turn_progress(
                event="chat.turn.step.started",
                message="Chat turn sampling step started",
                prepared=prepared,
                sample_index=_sample_index,
                prompt_item_count=len(context.items),
                direct_tool_count=len(prompt.tools),
                compactions_used=context.compactions_used,
                parallel_tool_calls=prompt.parallel_tool_calls,
            )
            if compaction_events:
                self._log_chat_turn_progress(
                    event="chat.turn.compacted",
                    message="Chat turn compacted context before sampling",
                    prepared=prepared,
                    sample_index=_sample_index,
                    compaction_event_count=len(compaction_events),
                    compactions_used=context.compactions_used,
                )
            for event in compaction_events:
                yield event

            response_id = ""
            step_usage: ChatModelUsage | None = None
            step_items: dict[str, ChatThreadItemResponse] = {}
            completed_assistant_item_ids: list[str] = []
            pending_tool_calls: list[ChatThreadItemResponse] = []

            for event in self._chat_model_stream_response(api_key=prepared.api_key_value or "", prompt=prompt):
                response_id = response_id or event.response_id
                if event.type == "response.output_item.added" and event.item is not None:
                    provider_item_id = event.item.item_id or new_id("item")
                    persisted_item = self._persist_response_item(
                        thread_id=prepared.thread.thread_id,
                        turn_id=prepared.turn.turn_id,
                        item=event.item,
                    )
                    step_items[provider_item_id] = persisted_item
                    self._log_chat_turn_progress(
                        event="chat.turn.item.started",
                        message="Chat turn item started",
                        prepared=prepared,
                        sample_index=_sample_index,
                        item=persisted_item,
                    )
                    yield ("item_started", {"item": persisted_item.model_dump(mode="json")})
                    continue
                if event.type in {"response.output_text.delta", "response.function_call_arguments.delta"}:
                    provider_item_id = event.item_id or new_id("item")
                    persisted_item = step_items.get(provider_item_id)
                    if persisted_item is None:
                        role = "assistant" if event.type == "response.output_text.delta" else "assistant"
                        item_type = "assistant_message" if event.type == "response.output_text.delta" else "function_call"
                        persisted_item = self.repository.add_chat_thread_item(  # type: ignore[attr-defined]
                            thread_id=prepared.thread.thread_id,
                            turn_id=prepared.turn.turn_id,
                            item_id=new_id("item"),
                            item_type=item_type,
                            role=role,
                            content_text="",
                            status="in_progress",
                        )
                    updated_item = self.repository.update_chat_thread_item(  # type: ignore[attr-defined]
                        persisted_item.item_id,
                        content_text=f"{persisted_item.content_text}{event.delta}",
                        status="in_progress",
                    )
                    if updated_item is None:
                        raise RuntimeError("Streaming item vanished during update")
                    step_items[provider_item_id] = updated_item
                    yield (
                        "item_delta",
                        {
                            "item_id": updated_item.item_id,
                            "delta": event.delta,
                            "delta_type": "output_text"
                            if event.type == "response.output_text.delta"
                            else "function_call_arguments",
                        },
                    )
                    continue
                if event.type == "response.output_item.done" and event.item is not None:
                    provider_item_id = event.item.item_id or new_id("item")
                    persisted_item = step_items.get(provider_item_id)
                    if persisted_item is None:
                        persisted_item = self._persist_response_item(
                            thread_id=prepared.thread.thread_id,
                            turn_id=prepared.turn.turn_id,
                            item=event.item,
                        )
                    else:
                        persisted_item = self.repository.update_chat_thread_item(  # type: ignore[attr-defined]
                            persisted_item.item_id,
                            item_type=self._thread_item_type_from_response_item(event.item),
                            phase=event.item.phase or persisted_item.phase,
                            name=event.item.name or persisted_item.name,
                            call_id=event.item.call_id or persisted_item.call_id,
                            content_text=event.item.content_text or persisted_item.content_text,
                            content_json=event.item.content_json,
                            status=event.item.status or "completed",
                        )
                    if persisted_item is None:
                        raise RuntimeError("Completed output item vanished during persistence")
                    step_items[provider_item_id] = persisted_item
                    normalized_item = self._chat_input_item_from_thread_item(persisted_item)
                    if normalized_item is not None:
                        context.items.append(normalized_item)
                    self._log_chat_turn_progress(
                        event="chat.turn.item.completed",
                        message="Chat turn item completed",
                        prepared=prepared,
                        sample_index=_sample_index,
                        item=persisted_item,
                    )
                    if persisted_item.item_type == "assistant_message":
                        completed_assistant_item_ids.append(provider_item_id)
                        continue
                    if persisted_item.item_type in {"function_call", "custom_tool_call"}:
                        pending_tool_calls.append(persisted_item)
                    yield ("item_completed", {"item": persisted_item.model_dump(mode="json")})
                    continue
                if event.type == "response.completed":
                    response_id = response_id or event.response_id
                    step_usage = _merge_chat_usage(step_usage, event.usage)
                    self._log_chat_turn_progress(
                        event="chat.turn.step.response.completed",
                        message="Chat model response stream completed",
                        prepared=prepared,
                        sample_index=_sample_index,
                        response_id=response_id,
                        input_tokens=step_usage.input_tokens if step_usage is not None else None,
                        output_tokens=step_usage.output_tokens if step_usage is not None else None,
                        total_tokens=step_usage.total_tokens if step_usage is not None else None,
                    )

            aggregated_usage = _merge_chat_usage(aggregated_usage, step_usage)
            if step_usage is not None:
                yield ("usage", self._chat_usage_payload(step_usage))

            completed_assistant_items = [
                step_items[item_id]
                for item_id in completed_assistant_item_ids
                if item_id in step_items
            ]
            final_item: ChatThreadItemResponse | None = None
            if completed_assistant_items:
                for provider_item_id, item in zip(completed_assistant_item_ids[:-1], completed_assistant_items[:-1], strict=False):
                    phase = self._assistant_message_phase(
                        item,
                        is_last_assistant_item=False,
                        pending_tool_calls=bool(pending_tool_calls),
                    )
                    updated_item = self.repository.update_chat_thread_item(  # type: ignore[attr-defined]
                        item.item_id,
                        phase=phase,
                        content_text=_sanitize_inline_citations(item.content_text, citation_registry.citations),
                        status="completed",
                    )
                    if updated_item is not None:
                        step_items[provider_item_id] = updated_item
                        yield ("item_completed", {"item": updated_item.model_dump(mode="json")})
                last_provider_item_id = completed_assistant_item_ids[-1]
                last_item = completed_assistant_items[-1]
                last_phase = self._assistant_message_phase(
                    last_item,
                    is_last_assistant_item=True,
                    pending_tool_calls=bool(pending_tool_calls),
                )
                updated_last_item = self.repository.update_chat_thread_item(  # type: ignore[attr-defined]
                    last_item.item_id,
                    phase=last_phase,
                    content_text=_sanitize_inline_citations(last_item.content_text, citation_registry.citations),
                    citations=[citation.model_dump() for citation in citation_registry.citations]
                    if last_phase == "final_answer"
                    else [],
                    status="completed",
                )
                if updated_last_item is not None:
                    step_items[last_provider_item_id] = updated_last_item
                    yield ("item_completed", {"item": updated_last_item.model_dump(mode="json")})
                    if last_phase == "final_answer":
                        final_item = updated_last_item

            if not pending_tool_calls:
                if final_item is not None:
                    updated_final_item = self._attach_recommended_reading_prompts(
                        api_key=prepared.api_key_value or "",
                        prepared=prepared,
                        final_item=final_item,
                    )
                    if updated_final_item != final_item:
                        final_item = updated_final_item
                        yield ("item_completed", {"item": final_item.model_dump(mode="json")})
                final_turn = self.repository.update_chat_turn(  # type: ignore[attr-defined]
                    prepared.turn.turn_id,
                    status="completed",
                    provider_response_id=response_id,
                    completed_at=utcnow(),
                    usage=self._chat_usage_payload(aggregated_usage) if aggregated_usage is not None else None,
                    diff_summary=turn_state.diff_tracker.unified_diff(),
                    changed_paths=turn_state.diff_tracker.changed_files(),
                    exec_session_ids=sorted(turn_state.running_exec_session_ids),
                )
                self._log_chat_turn_progress(
                    event="chat.turn.completed",
                    message="Chat turn completed",
                    prepared=prepared,
                    sample_index=_sample_index,
                    item=final_item,
                    changed_path_count=len(turn_state.diff_tracker.changed_files()),
                    exec_session_count=len(turn_state.running_exec_session_ids),
                    input_tokens=aggregated_usage.input_tokens if aggregated_usage is not None else None,
                    output_tokens=aggregated_usage.output_tokens if aggregated_usage is not None else None,
                    total_tokens=aggregated_usage.total_tokens if aggregated_usage is not None else None,
                )
                return ChatTurnExecutionResult(
                    turn=final_turn or prepared.turn,
                    final_item=final_item,
                    citation_registry=citation_registry,
                    usage=aggregated_usage,
                )

            self._log_chat_turn_progress(
                event="chat.turn.tools.queued",
                message="Chat turn queued tool calls for execution",
                prepared=prepared,
                sample_index=_sample_index,
                pending_tool_call_count=len(pending_tool_calls),
            )
            for index, tool_item in enumerate(pending_tool_calls):
                tool_name = tool_item.name or ""
                tool_input = tool_item.content_json if isinstance(tool_item.content_json, dict) else {}
                if not tool_input and tool_item.content_text.strip():
                    try:
                        parsed_tool_input = json.loads(tool_item.content_text)
                        if isinstance(parsed_tool_input, dict):
                            tool_input = parsed_tool_input
                    except json.JSONDecodeError:
                        tool_input = {}
                self._log_chat_turn_progress(
                    event="chat.turn.tool.started",
                    message="Chat tool execution started",
                    prepared=prepared,
                    sample_index=_sample_index,
                    item=tool_item,
                    tool_name=tool_name,
                    tool_call_index=index,
                )
                if router.find_spec(tool_name) is None:
                    payload = {"error": f"Unknown tool: {tool_name}"}
                    is_error = True
                else:
                    self._capture_turn_state_before_tool(
                        tool_name=tool_name,
                        tool_input=tool_input,
                        user=user,
                        project_id=prepared.thread.project_id,
                        turn_state=turn_state,
                    )
                    payload, is_error = self._run_single_chat_tool(
                        tool_name=tool_name,
                        tool_input=tool_input,
                        user=user,
                        citation_registry=citation_registry,
                        thread_id=prepared.thread.thread_id,
                        project_id=prepared.thread.project_id,
                    )
                    self._update_turn_state_from_tool_result(
                        tool_name=tool_name,
                        payload=payload,
                        is_error=is_error,
                        user=user,
                        project_id=prepared.thread.project_id,
                        turn_state=turn_state,
                    )
                output_item = self.repository.add_chat_thread_item(  # type: ignore[attr-defined]
                    thread_id=prepared.thread.thread_id,
                    turn_id=prepared.turn.turn_id,
                    item_type="function_call_output",
                    role="tool",
                    call_id=tool_item.call_id or tool_item.item_id,
                    name=tool_name,
                    content_text="",
                    status="in_progress",
                )
                yield ("item_started", {"item": output_item.model_dump(mode="json")})
                output_text = json.dumps(payload, ensure_ascii=False)
                completed_output_item = self.repository.update_chat_thread_item(  # type: ignore[attr-defined]
                    output_item.item_id,
                    content_text=output_text,
                    content_json=payload,
                    status="completed",
                )
                if completed_output_item is None:
                    raise RuntimeError("Tool output item vanished during persistence")
                normalized_output_item = self._chat_input_item_from_thread_item(completed_output_item)
                if normalized_output_item is not None:
                    context.items.append(normalized_output_item)
                self._log_chat_turn_progress(
                    event="chat.turn.tool.completed",
                    message="Chat tool execution completed",
                    prepared=prepared,
                    sample_index=_sample_index,
                    item=completed_output_item,
                    tool_name=tool_name,
                    tool_call_index=index,
                    is_error=is_error,
                )
                yield ("item_completed", {"item": completed_output_item.model_dump(mode="json")})
            _sample_index += 1

    def _api_key_required_response(
        self,
        *,
        user: BrowserUser,
        thread_id: str,
        state: str = "api_key_required",
    ) -> SendChatMessageResponse:
        refreshed = self.get_chat_thread(user, thread_id)
        return SendChatMessageResponse(
            thread=refreshed,
            turn=None,
            final_item=None,
            has_openai_api_key=self.repository.get_openai_api_key_record(user.user_id) is not None,  # type: ignore[attr-defined]
            state=state,
        )

    def _recommended_reading_relevant(
        self,
        *,
        user_prompt: str,
        final_item: ChatThreadItemResponse,
    ) -> bool:
        if final_item.citations:
            return True
        return bool(_GROUNDING_INTENT_RE.search(user_prompt))

    def _parse_recommended_reading_payload(self, text: str) -> list[str]:
        payload_text = text.strip()
        if payload_text.startswith("```"):
            payload_text = re.sub(r"^```(?:json)?\s*", "", payload_text, flags=re.IGNORECASE)
            payload_text = re.sub(r"\s*```$", "", payload_text).strip()
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            start = payload_text.find("[")
            end = payload_text.rfind("]")
            if start < 0 or end <= start:
                return []
            try:
                payload = json.loads(payload_text[start : end + 1])
            except json.JSONDecodeError:
                return []
        if isinstance(payload, dict):
            payload = payload.get("prompts")
        if not isinstance(payload, list):
            return []

        prompts: list[str] = []
        seen: set[str] = set()
        for item in payload:
            prompt = " ".join(str(item or "").split()).strip()
            if not prompt:
                continue
            word_count = len(_RECOMMENDED_READING_WORD_RE.findall(prompt))
            key = prompt.casefold()
            if 8 <= word_count <= 10 and key not in seen:
                prompts.append(prompt[:240])
                seen.add(key)
            if len(prompts) >= 5:
                break
        return prompts

    def _generate_recommended_reading_prompts(
        self,
        *,
        api_key: str,
        user_prompt: str,
        final_item: ChatThreadItemResponse,
    ) -> list[str]:
        if not self._recommended_reading_relevant(user_prompt=user_prompt, final_item=final_item):
            return []
        citation_titles = [citation.title for citation in final_item.citations[:8] if citation.title]
        prompt = Prompt(
            input=[
                {
                    "type": "message",
                    "role": "user",
                    "content": json.dumps(
                        {
                            "user_prompt": user_prompt[:4000],
                            "assistant_reply": final_item.content_text[:6000],
                            "cited_papers": citation_titles,
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
            base_instructions=(
                "Generate five concise follow-up research prompts for recommended reading. "
                "Only use the supplied user prompt, assistant reply, and cited paper titles. "
                "Each prompt must be 8 to 10 words. Return only a JSON array of strings."
            ),
            tools=[],
            tool_choice=None,
            parallel_tool_calls=False,
        )
        try:
            response = self._chat_model_create_response(api_key=api_key, prompt=prompt)
        except Exception:
            logger.exception(
                "Recommended reading prompt generation failed",
                extra={"event": "chat.recommended_reading.failed"},
            )
            return []
        response_text = "\n".join(
            item.content_text
            for item in response.items
            if item.item_type == "assistant_message" and item.content_text
        )
        return self._parse_recommended_reading_payload(response_text)

    def _attach_recommended_reading_prompts(
        self,
        *,
        api_key: str,
        prepared: PreparedChatGeneration,
        final_item: ChatThreadItemResponse,
    ) -> ChatThreadItemResponse:
        prompts = self._generate_recommended_reading_prompts(
            api_key=api_key,
            user_prompt=prepared.user_item.content_text,
            final_item=final_item,
        )
        if not prompts:
            return final_item
        content_json = dict(final_item.content_json) if isinstance(final_item.content_json, dict) else {}
        content_json["recommended_prompts"] = prompts
        updated_item = self.repository.update_chat_thread_item(  # type: ignore[attr-defined]
            final_item.item_id,
            content_json=content_json,
        )
        return updated_item or final_item

    def _chat_usage_payload(self, usage: ChatModelUsage) -> dict[str, Any]:
        return {
            "model": self.settings.openai_model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens or (usage.input_tokens + usage.output_tokens),
            "context_window_tokens": _openai_context_window_tokens(self.settings.openai_model),
        }

    def _validate_paper_selection(self, selection: PaperSelectionPayload | None) -> PaperSelectionPayload | None:
        if selection is None:
            return None
        self.get_indexed_paper(selection.paper_id)
        if not (selection.selected_text or "").strip() and selection.region is None:
            raise HTTPException(status_code=400, detail="Paper selection must include text or a region")
        if selection.region is not None:
            if not selection.region.image_base64:
                raise HTTPException(status_code=400, detail="Paper region selection must include an image")
            try:
                decoded = base64.b64decode(selection.region.image_base64, validate=True)
            except Exception as exc:
                raise HTTPException(status_code=400, detail="Paper selection image is not valid base64") from exc
            if len(decoded) > 750_000:
                raise HTTPException(status_code=400, detail="Paper selection image is too large")
        return selection

    def _normalize_chat_request_papers(self, request: SendChatMessageRequest) -> None:
        selection = self._validate_paper_selection(request.paper_selection)
        if selection is None:
            return
        request.paper_ids = list(dict.fromkeys([*request.paper_ids, selection.paper_id]))

    def send_chat_message(
        self, user: BrowserUser, thread_id: str, request: SendChatMessageRequest
    ) -> SendChatMessageResponse:
        resolved_user = self.repository.get_browser_user_by_id(user.user_id) or user  # type: ignore[attr-defined]
        api_key_value, api_key_state = self._choose_openai_api_key(resolved_user)
        if not api_key_value:
            return self._api_key_required_response(
                user=user,
                thread_id=thread_id,
                state=api_key_state,
            )
        self._normalize_chat_request_papers(request)
        # Drain mention-resolution events. The non-streaming path can't
        # surface them to the UI, so just run the side effect (fetch + index)
        # and discard the per-paper events.
        for _ in self._ensure_mentioned_papers_indexed(request.paper_ids):
            pass
        prepared = self._prepare_chat_generation(
            user=user,
            thread_id=thread_id,
            request=request,
            resolved_user=resolved_user,
            api_key_value=api_key_value,
        )

        _ = self._chat_runtime_mode()
        turn_engine = self._run_chat_turn_engine(
            prepared=prepared,
            user=user,
            request=request,
            stream_output=False,
        )
        while True:
            try:
                next(turn_engine)
            except StopIteration as stop:
                turn_result = stop.value
                break

        refreshed = self.get_chat_thread(user, thread_id)
        return SendChatMessageResponse(
            thread=refreshed,
            turn=turn_result.turn,
            final_item=turn_result.final_item,
            has_openai_api_key=self.repository.get_openai_api_key_record(user.user_id) is not None,  # type: ignore[attr-defined]
            state="completed",
        )

    def stream_chat_message(
        self,
        user: BrowserUser,
        thread_id: str,
        request: SendChatMessageRequest,
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        resolved_user = self.repository.get_browser_user_by_id(user.user_id) or user  # type: ignore[attr-defined]
        api_key_value, api_key_state = self._choose_openai_api_key(resolved_user)
        if not api_key_value:
            yield (
                "api_key_required",
                self._api_key_required_response(
                    user=user,
                    thread_id=thread_id,
                    state=api_key_state,
                ).model_dump(mode="json"),
            )
            return
        self._normalize_chat_request_papers(request)
        # Forward per-paper resolve-and-index events to the SSE stream so the
        # UI can show a "Fetching paper…" state for any unindexed mention.
        yield from self._ensure_mentioned_papers_indexed(request.paper_ids)
        prepared = self._prepare_chat_generation(
            user=user,
            thread_id=thread_id,
            request=request,
            resolved_user=resolved_user,
            api_key_value=api_key_value,
        )

        _ = self._chat_runtime_mode()
        if prepared.turn.title.strip():
            yield (
                "reply_title",
                {
                    "thread_id": prepared.thread.thread_id,
                    "turn_id": prepared.turn.turn_id,
                    "title": prepared.turn.title,
                },
            )
        if prepared.updated_thread_title:
            yield (
                "thread_title",
                {"thread_id": prepared.thread.thread_id, "title": prepared.updated_thread_title},
            )
        yield ("turn_started", {"turn": prepared.turn.model_dump(mode="json")})
        turn_engine = self._run_chat_turn_engine(
            prepared=prepared,
            user=user,
            request=request,
            stream_output=True,
        )
        try:
            while True:
                try:
                    event_name, payload = next(turn_engine)
                except StopIteration as stop:
                    turn_result = stop.value
                    break
                yield (event_name, payload)
        except Exception as exc:
            logger.exception(
                "Chat turn failed during streaming",
                extra={
                    "event": "chat.turn.failed",
                    "thread_id": prepared.thread.thread_id,
                    "turn_id": prepared.turn.turn_id,
                    "project_id": prepared.thread.project_id or "",
                },
            )
            failed_turn = self.repository.update_chat_turn(  # type: ignore[attr-defined]
                prepared.turn.turn_id,
                status="failed",
                completed_at=utcnow(),
            )
            yield (
                "turn_failed",
                {
                    "turn": (failed_turn or prepared.turn).model_dump(mode="json"),
                    "detail": str(exc),
                },
            )
            return

        refreshed = self.get_chat_thread(user, thread_id)

        yield (
            "turn_completed",
            {
                "thread": refreshed.model_dump(mode="json"),
                "turn": turn_result.turn.model_dump(mode="json"),
                "final_item": turn_result.final_item.model_dump(mode="json") if turn_result.final_item is not None else None,
                "has_openai_api_key": self.repository.get_openai_api_key_record(user.user_id) is not None,  # type: ignore[attr-defined]
                "state": "completed",
                "usage": self._chat_usage_payload(turn_result.usage) if turn_result.usage is not None else None,
            },
        )

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

    def enqueue_reindex_embeddings_job(
        self,
        *,
        limit: int = 100_000,
        include_chunks: bool = True,
        rewrite_existing: bool = True,
    ) -> JobResponse:
        job_type = "reindex_embeddings" if rewrite_existing else "reindex_missing_embeddings"
        job = self.repository.create_job(
            job_type,
            {"limit": limit, "include_chunks": include_chunks, "rewrite_existing": rewrite_existing},
        )
        logger.info(
            "Created embedding reindex job",
            extra={
                "event": "jobs.reindex.created",
                "job_id": job.job_id,
                "job_type": job_type,
                "limit": limit,
                "include_chunks": include_chunks,
                "rewrite_existing": rewrite_existing,
            },
        )
        return job

    def reindex_missing_embeddings(self, *, limit: int, include_chunks: bool = True) -> dict[str, int]:
        return self.reindex_embeddings(limit=limit, include_chunks=include_chunks, rewrite_existing=False)

    def reindex_embeddings(
        self,
        *,
        limit: int,
        include_chunks: bool = True,
        rewrite_existing: bool = True,
        tracking_job: JobResponse | None = None,
    ) -> dict[str, int]:
        if self.embeddings is None:
            logger.warning(
                "reindex_embeddings invoked without an embedding provider",
                extra={"event": "reindex.noop", "rewrite_existing": rewrite_existing},
            )
            return {"papers_updated": 0, "chunks_updated": 0}

        papers_updated = 0
        chunks_updated = 0
        page = max(64, min(limit, 512))
        chunk_page = 128
        remaining = limit
        # Keyset cursor over the same ordering used by the repo method. Advances
        # after every fetched page so a paper that failed to encode this round
        # does not keep the frontier stuck (failed rows still match the
        # backfill predicate).
        after: PaperResponse | None = None

        while remaining > 0:
            self._raise_if_current_job_canceled()
            batch = self.repository.list_papers_needing_embedding_backfill(
                limit=min(page, remaining),
                include_chunks=include_chunks,
                rewrite_existing=rewrite_existing,
                after=after,
            )
            if not batch:
                break

            # Paper-level embeddings in one batched encode. Use the same
            # (title, body) pair contract as index_arxiv_paper so the
            # reindex path produces vectors in the same space as normal ingest
            # — summary_short is intentionally excluded.
            pairs = [(paper.title or "", paper.tldr or paper.abstract or "") for paper in batch]
            vectors = self.embeddings.encode_paper_batch(pairs)
            if vectors is not None and len(vectors) == len(batch):
                for paper, vector in zip(batch, vectors):
                    self._raise_if_current_job_canceled()
                    self.repository.update_paper_embedding(paper.paper_id, vector)
                    papers_updated += 1
                    if tracking_job is not None:
                        self.repository.record_job_paper(
                            tracking_job.job_id,
                            paper.paper_id,
                            outcome="updated",
                            arxiv_id=paper.arxiv_id,
                            title=paper.title,
                        )

            if include_chunks:
                for paper in batch:
                    self._raise_if_current_job_canceled()
                    chunks = self.repository.list_chunks_missing_embedding(
                        paper.paper_id,
                        include_existing=rewrite_existing,
                    )
                    if not chunks:
                        continue
                    paper_chunks_updated = 0
                    cap = self.settings.chunk_max_chars + self.settings.chunk_overlap_chars
                    for start in range(0, len(chunks), chunk_page):
                        self._raise_if_current_job_canceled()
                        chunk_batch = chunks[start : start + chunk_page]
                        chunk_inputs = [(chunk.get("content") or "")[:cap] for chunk in chunk_batch]
                        chunk_vectors = self.embeddings.encode_batch(chunk_inputs)
                        if chunk_vectors is None or len(chunk_vectors) != len(chunk_batch):
                            continue
                        for chunk, vector in zip(chunk_batch, chunk_vectors):
                            self._raise_if_current_job_canceled()
                            self.repository.update_chunk_embedding(chunk["chunk_id"], vector)
                            chunks_updated += 1
                            paper_chunks_updated += 1
                    if tracking_job is not None and paper_chunks_updated > 0:
                        self.repository.record_job_paper(
                            tracking_job.job_id,
                            paper.paper_id,
                            outcome="updated",
                            arxiv_id=paper.arxiv_id,
                            title=paper.title,
                        )

            after = batch[-1]
            remaining -= len(batch)
            logger.info(
                "Embedding reindex progress",
                extra={
                    "event": "reindex.progress",
                    "papers_updated": papers_updated,
                    "chunks_updated": chunks_updated,
                    "rewrite_existing": rewrite_existing,
                },
            )

        logger.info(
            "Completed embedding reindex",
            extra={
                "event": "reindex.completed",
                "papers_updated": papers_updated,
                "chunks_updated": chunks_updated,
                "rewrite_existing": rewrite_existing,
            },
        )
        return {"papers_updated": papers_updated, "chunks_updated": chunks_updated}

    def enqueue_enrich_missing_metadata_job(
        self,
        *,
        limit: int = 50_000,
        min_age_seconds: int = 3600,
    ) -> JobResponse:
        job = self.repository.create_job(
            "enrich_missing_metadata",
            {"limit": limit, "min_age_seconds": min_age_seconds},
        )
        logger.info(
            "Created enrich_missing_metadata job",
            extra={
                "event": "jobs.enrich_missing_metadata.created",
                "job_id": job.job_id,
                "limit": limit,
                "min_age_seconds": min_age_seconds,
            },
        )
        return job

    def enrich_missing_metadata(
        self,
        *,
        limit: int,
        min_age_seconds: int = 3600,
        batch_size: int = 20,
        raise_on_failed_batches: bool = False,
        tracking_job: JobResponse | None = None,
    ) -> dict[str, int]:
        """Re-run Semantic Scholar enrichment for any paper that inline
        enrichment left unfilled. Uses aggressive retry (default max_retries)
        since this runs off the request path.

        Returns a counts dict for observability: papers scanned, updated, and
        batches that failed even after retries.
        """
        total_scanned = 0
        total_updated = 0
        total_batches_failed = 0
        page_size = max(batch_size, min(limit, 256))
        remaining = limit
        after: PaperResponse | None = None

        while remaining > 0:
            self._raise_if_current_job_canceled()
            batch = self.repository.list_papers_needing_enrichment(
                limit=min(page_size, remaining),
                min_age_seconds=min_age_seconds,
                after=after,
            )
            if not batch:
                break
            total_scanned += len(batch)
            # Make mutable PaperMetadata stand-ins so enrich_with_semantic_scholar
            # can write back citation / tldr / s2_id in place.
            metas = [
                PaperMetadata(
                    arxiv_id=paper.arxiv_id,
                    title=paper.title or "",
                    authors=paper.authors or "",
                    abstract=paper.abstract or "",
                    categories=paper.categories or "",
                    primary_category=paper.primary_category or "",
                    published=paper.published or "",
                    updated=paper.updated or "",
                    pdf_url=paper.pdf_url or "",
                    html_url=paper.html_url or "",
                )
                for paper in batch
            ]
            outcome = sources.enrich_with_semantic_scholar(metas, batch_size=batch_size)
            total_batches_failed += outcome.batches_failed
            for paper, meta in zip(batch, metas):
                self._raise_if_current_job_canceled()
                if not (meta.semantic_scholar_id or (meta.citation_count and meta.citation_count > 0)):
                    if tracking_job is not None:
                        self.repository.record_job_paper(
                            tracking_job.job_id,
                            paper.paper_id,
                            outcome="skipped",
                            arxiv_id=paper.arxiv_id,
                            title=paper.title,
                            skip_reason="no_enrichment_data",
                        )
                    continue
                # Re-use the shallow upsert path to persist enrichment —
                # it writes citation_count / influential_citation_count /
                # semantic_scholar_id / tldr without touching index artifacts.
                self.repository.upsert_paper_shallow(meta)
                total_updated += 1
                if tracking_job is not None:
                    self.repository.record_job_paper(
                        tracking_job.job_id,
                        paper.paper_id,
                        outcome="updated",
                        arxiv_id=paper.arxiv_id,
                        title=paper.title,
                    )
                # #39 PR C: persist S2 author rows alongside the enrichment.
                if outcome is not None:
                    author_rows = outcome.authors_by_arxiv.get(meta.arxiv_id) or []
                    if author_rows:
                        self.repository.replace_paper_authors(paper.paper_id, authors=author_rows)

            after = batch[-1]
            remaining -= len(batch)
            logger.info(
                "enrich_missing_metadata progress",
                extra={
                    "event": "enrich.progress",
                    "scanned": total_scanned,
                    "updated": total_updated,
                    "batches_failed": total_batches_failed,
                },
            )

        logger.info(
            "Completed enrich_missing_metadata",
            extra={
                "event": "enrich.completed",
                "scanned": total_scanned,
                "updated": total_updated,
                "batches_failed": total_batches_failed,
            },
        )
        result = {
            "papers_scanned": total_scanned,
            "papers_updated": total_updated,
            "batches_failed": total_batches_failed,
        }
        # Worker jobs opt into retry signaling. Direct operator calls through
        # `paperbase-dev enrich-missing` keep the stats return path so the CLI
        # can print partial progress cleanly instead of surfacing a traceback.
        if total_batches_failed > 0 and raise_on_failed_batches:
            raise RetryableError(
                f"Semantic Scholar enrichment left {total_batches_failed} "
                f"batch(es) un-persisted after internal retries"
            )
        return result

    def backfill_code_urls_from_hf_papers(
        self, *, limit: int = 100_000, page_size: int = 200, dry_run: bool = False
    ) -> dict[str, Any]:
        """Sweep indexed papers, ask Hugging Face paper search for the
        paper-page ``githubRepo`` field, and overwrite ``code_url`` where
        the source is not ``admin`` (#46 cleanup story).

        Returns a counts dict so callers (CLI + tests) can verify how
        many rows were inspected vs changed. ``dry_run=True`` logs the
        diffs without writing — use that to sanity-check before a real
        run against 10k+ papers.
        """
        total_scanned = 0
        total_crosslink_hits = 0
        total_updated = 0
        total_skipped_admin = 0
        total_unchanged = 0
        after_paper_id: str | None = None
        remaining = limit
        while remaining > 0:
            batch_size = min(page_size, remaining)
            batch = self.repository.list_papers_for_code_url_backfill(
                limit=batch_size, after_paper_id=after_paper_id
            )
            if not batch:
                break
            for paper in batch:
                total_scanned += 1
                if paper.code_url_source == "admin":
                    # Defensive — the repo method already filters these out,
                    # but a race during long backfill shouldn't clobber a
                    # newly-created admin pin.
                    total_skipped_admin += 1
                    continue
                crosslink_url = sources.fetch_hf_paper_repo(paper.arxiv_id)
                if not crosslink_url:
                    total_unchanged += 1
                    continue
                total_crosslink_hits += 1
                if crosslink_url == paper.code_url and paper.code_url_source in {"hf_papers", "pwc"}:
                    total_unchanged += 1
                    continue
                logger.info(
                    "Code URL backfill diff",
                    extra={
                        "event": "code_url.backfill.diff",
                        "paper_id": paper.paper_id,
                        "arxiv_id": paper.arxiv_id,
                        "old_code_url": paper.code_url,
                        "old_source": paper.code_url_source,
                        "new_code_url": crosslink_url,
                        "new_source": "hf_papers",
                        "dry_run": dry_run,
                    },
                )
                if not dry_run:
                    self.repository.update_code_url(
                        paper.paper_id, code_url=crosslink_url, code_url_source="hf_papers"
                    )
                    # update_code_url wipes prior code-ingest state on
                    # any URL transition (#45 review). Queue a fresh
                    # ingest so the chat surface sees the new repo's
                    # contents without waiting on a manual reindex.
                    self.enqueue_ingest_repo_job(paper.paper_id, force=True)
                total_updated += 1
            last = batch[-1]
            after_paper_id = last.paper_id
            remaining -= len(batch)
            if len(batch) < batch_size:
                break
        return {
            "scanned": total_scanned,
            "crosslink_hits": total_crosslink_hits,
            "updated": total_updated,
            "skipped_admin": total_skipped_admin,
            "unchanged": total_unchanged,
            "dry_run": dry_run,
        }

    def set_admin_code_url(self, paper_id: str, *, code_url: str | None) -> PaperResponse | None:
        """Pin ``papers.code_url`` to an operator-chosen value (#46 layer 3).

        Passing ``code_url=None`` clears the pin (and any extracted URL
        alongside it, returning the paper to "no code attached"). Any
        non-empty value must parse through ``code_ingest.parse_github_url``
        — unsupported hosts / malformed strings raise ``HTTPException`` so
        the API surface can return a clean 400. Pins survive re-index
        runs via the ``code_url_source='admin'`` sticky bit checked in
        :meth:`index_arxiv_paper`.

        When the URL changes, the underlying ``update_code_url`` resets
        the code-ingest state (status, manifest, code chunks) so the
        paper isn't left serving the previous repo's metadata. For
        non-null new URLs we then enqueue a fresh ``ingest_repo`` job so
        the chat surface picks up the new repo's contents without
        waiting for an unrelated reindex.
        """
        paper = self.repository.get_paper(paper_id)
        if paper is None:
            return None
        previous_url = paper.code_url
        if code_url is None or not code_url.strip():
            return self.repository.update_code_url(
                paper_id, code_url=None, code_url_source=None
            )
        ref = code_ingest.parse_github_url(code_url)
        if ref is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported code_url: {code_url!r}",
            )
        canonical = f"https://github.com/{ref.owner}/{ref.repo}"
        updated = self.repository.update_code_url(
            paper_id, code_url=canonical, code_url_source="admin"
        )
        if updated is not None and previous_url != canonical:
            self.enqueue_ingest_repo_job(paper_id, force=True)
        return updated

    def enqueue_ingest_repo_job(
        self,
        paper_id: str,
        *,
        force: bool = False,
        root_job_id: str | None = None,
    ) -> JobResponse:
        """Queue an ``ingest_repo`` job for a single paper (#5).

        Called automatically from :meth:`index_arxiv_paper` when the indexed
        paper carries a GitHub ``code_url``, and exposed on the public
        service surface so the dev CLI / admin API can trigger a re-ingest
        without re-running full paper indexing.
        """
        job = self.repository.create_job(
            "ingest_repo",
            {"paper_id": paper_id, "force": force},
            root_job_id=root_job_id,
        )
        logger.info(
            "Created ingest_repo job",
            extra={
                "event": "jobs.ingest_repo.created",
                "job_id": job.job_id,
                "paper_id": paper_id,
                "force": force,
            },
        )
        return job

    def ingest_repo(
        self,
        paper_id: str,
        *,
        force: bool = False,
        tracking_job: JobResponse | None = None,
    ) -> PaperResponse | None:
        """Fetch, extract, classify, and persist the paper's linked GitHub
        repo artifacts (#5).

        Transitions ``code_index_status`` through ``indexing`` → one of
        ``indexed`` / ``skipped`` / ``failed``. Short-circuits to a no-op
        when a prior manifest already records the same ``code_url`` and a
        matching ``resolved_sha``, unless ``force`` is set. This is the
        read-only ingestion foundation; indexing the extracted files into
        ``text_chunks`` is PR 2 scope.
        """
        self._raise_if_current_job_canceled()
        paper = self.repository.get_paper(paper_id)
        if paper is None:
            if tracking_job is not None:
                self.repository.record_job_paper(tracking_job.job_id, paper_id, outcome="failed")
            return None
        code_url = (paper.code_url or "").strip()
        if not code_url:
            manifest = code_ingest.skipped_manifest(code_url="", reason="no_code_url")
            updated = self.repository.update_code_ingest_state(
                paper_id,
                status="skipped",
                is_python=None,
                python_reason="no_code_url",
                manifest=manifest,
            )
            if tracking_job is not None:
                self.repository.record_job_paper(
                    tracking_job.job_id,
                    paper_id,
                    outcome="skipped",
                    arxiv_id=paper.arxiv_id,
                    title=paper.title,
                    skip_reason="no_code_url",
                )
            return updated
        ref = code_ingest.parse_github_url(code_url)
        if ref is None:
            manifest = code_ingest.skipped_manifest(code_url=code_url, reason="unsupported_host")
            updated = self.repository.update_code_ingest_state(
                paper_id,
                status="skipped",
                is_python=None,
                python_reason="unsupported_host",
                manifest=manifest,
            )
            if tracking_job is not None:
                self.repository.record_job_paper(
                    tracking_job.job_id,
                    paper_id,
                    outcome="skipped",
                    arxiv_id=paper.arxiv_id,
                    title=paper.title,
                    skip_reason="unsupported_host",
                )
            return updated
        self.repository.update_code_ingest_state(paper_id, status="indexing")
        try:
            archive = code_ingest.fetch_github_archive(
                ref,
                token=self.settings.github_token,
                max_bytes=self.settings.code_repo_max_archive_bytes,
            )
            self._raise_if_current_job_canceled()
            prev_manifest = paper.code_manifest or {}
            if (
                not force
                and archive.resolved_sha
                and prev_manifest.get("resolved_sha") == archive.resolved_sha
                and prev_manifest.get("code_url") == code_url
                and paper.code_index_status == "indexed"
            ):
                # Same commit as last time — restore the terminal state after
                # the transient indexing marker and skip re-extracting.
                self._raise_if_current_job_canceled()
                updated = self.repository.update_code_ingest_state(
                    paper_id,
                    status="indexed",
                    is_python=paper.code_is_python,
                    python_reason=paper.code_python_reason,
                    manifest=paper.code_manifest,
                )
                if tracking_job is not None:
                    self.repository.record_job_paper(
                        tracking_job.job_id,
                        paper_id,
                        outcome="skipped",
                        arxiv_id=paper.arxiv_id,
                        title=paper.title,
                        skip_reason="same_resolved_sha",
                    )
                return updated
            with tempfile.TemporaryDirectory() as tmp:
                scratch = Path(tmp) / "repo"
                report = code_ingest.extract_archive_safely(
                    archive.data,
                    scratch,
                    max_stored_file_bytes=self.settings.code_repo_max_stored_file_bytes,
                    max_scanned_files=self.settings.code_repo_max_scanned_files,
                )
                self._raise_if_current_job_canceled()
                is_python, reason = code_ingest.classify_python(report)
                # Selective code indexing (#5 PR 2): only Python-eligible
                # repos get high-signal chunks (README / docs / manifests /
                # entrypoints / ast.parse signatures). Non-Python repos keep
                # their extracted files in object store for future tooling
                # but stay absent from any index so the paper-code retrieval
                # primitives cleanly reject them on eligibility alone.
                code_chunks = (
                    code_ingest.build_code_chunks(scratch, report) if is_python else []
                )
                self._raise_if_current_job_canceled()
                indexed_paths = sorted({chunk.repo_path for chunk in code_chunks})
                manifest = code_ingest.build_manifest(
                    code_url=code_url,
                    ref=ref,
                    resolved_sha=archive.resolved_sha,
                    report=report,
                    is_python=is_python,
                    python_reason=reason,
                )
                manifest["indexed_paths"] = indexed_paths
                code_prefix = f"papers/{paper_id}/code"
                self._raise_if_current_job_canceled()
                self.object_store.delete_prefix(f"{code_prefix}/")
                self.object_store.put_bytes(
                    f"{code_prefix}/raw/source.tar.gz",
                    archive.data,
                    content_type="application/gzip",
                )
                self.object_store.put_text(
                    f"{code_prefix}/manifest.json",
                    json.dumps(manifest, indent=2),
                    content_type="application/json",
                )
                for rel in report.stored_paths:
                    self._raise_if_current_job_canceled()
                    source = scratch / rel
                    if not source.exists():
                        continue
                    self.object_store.put_bytes(f"{code_prefix}/files/{rel}", source.read_bytes())
                # Persist chunks after files land so a crash between
                # ``put_bytes`` and ``replace_code_chunks`` cannot leave
                # index rows pointing at files that aren't in object store.
                self.repository.replace_code_chunks(
                    paper_id,
                    [
                        {
                            "path": f"code/files/{chunk.repo_path}",
                            "chunk_kind": chunk.chunk_kind,
                            "content": chunk.content,
                            "chunk_index": idx,
                            "section_depth": 1,
                            "section_order": idx,
                        }
                        for idx, chunk in enumerate(code_chunks)
                    ],
                )
            self._raise_if_current_job_canceled()
            logger.info(
                "Completed code ingest",
                extra={
                    "event": "code_ingest.completed",
                    "paper_id": paper_id,
                    "owner": ref.owner,
                    "repo": ref.repo,
                    "resolved_sha": archive.resolved_sha,
                    "python_file_count": report.python_file_count,
                    "notebook_count": report.notebook_count,
                    "is_python": is_python,
                    "python_reason": reason,
                    "stored_file_count": len(report.stored_paths),
                    "scanned_file_count": len(report.scanned_paths),
                    "indexed_chunk_count": len(code_chunks),
                },
            )
            updated = self.repository.update_code_ingest_state(
                paper_id,
                status="indexed",
                is_python=is_python,
                python_reason=reason,
                manifest=manifest,
            )
            if tracking_job is not None:
                self.repository.record_job_paper(
                    tracking_job.job_id,
                    paper_id,
                    outcome="updated",
                    arxiv_id=paper.arxiv_id,
                    title=paper.title,
                )
            return updated
        except code_ingest.CodeIngestError as exc:
            reason = f"fetch_failed:{type(exc).__name__}"
            logger.warning(
                "Code ingest fetch failed",
                extra={
                    "event": "code_ingest.fetch_failed",
                    "paper_id": paper_id,
                    "code_url": code_url,
                    "error": str(exc),
                },
            )
        except Exception as exc:
            reason = f"ingest_failed:{type(exc).__name__}"
            logger.exception(
                "Code ingest failed",
                extra={
                    "event": "code_ingest.failed",
                    "paper_id": paper_id,
                    "code_url": code_url,
                },
            )
        manifest = code_ingest.skipped_manifest(code_url=code_url, reason=reason)
        updated = self.repository.update_code_ingest_state(
            paper_id,
            status="failed",
            is_python=None,
            python_reason=reason,
            manifest=manifest,
        )
        if tracking_job is not None:
            self.repository.record_job_paper(
                tracking_job.job_id,
                paper_id,
                outcome="failed",
                arxiv_id=paper.arxiv_id,
                title=paper.title,
            )
        return updated

    # ------------------------------------------------------------------ #
    # Paper-code retrieval primitives (#5 PR 2) — read-only.
    # ------------------------------------------------------------------ #

    def _code_eligibility(self, paper: PaperResponse) -> CodeEligibility:
        if paper.code_is_python is True and paper.code_index_status == "indexed":
            return CodeEligibility(eligible=True, status=paper.code_index_status, reason=None)
        return CodeEligibility(
            eligible=False,
            status=paper.code_index_status or "none",
            reason=paper.code_python_reason,
        )

    def list_paper_code_files(self, paper_id: str) -> CodeFileListResponse | None:
        """List repo files stored under ``papers/{paper_id}/code/files/``.

        Manifest is source-of-truth for which files got ingested, but sizes
        come from a single ``object_store.list_prefix()`` call — the GCS
        backend returns blob metadata (including size) without downloading
        content, so listing a 100-file repo stays O(one paginated list) of
        network I/O instead of O(total repo bytes) full-blob reads.
        Non-Python repos surface a structured ineligible response — callers
        branch on ``eligibility.eligible`` rather than empty-list vs
        populated-list.
        """
        paper = self.repository.get_paper(paper_id)
        if paper is None or paper.index_status != "indexed":
            return None
        eligibility = self._code_eligibility(paper)
        manifest = paper.code_manifest or {}
        resolved_sha = manifest.get("resolved_sha")
        if not eligibility.eligible:
            return CodeFileListResponse(
                paper_id=paper_id, eligibility=eligibility, resolved_sha=resolved_sha, files=[]
            )
        indexed = set(manifest.get("indexed_paths") or [])
        files_prefix = f"papers/{paper_id}/code/files/"
        try:
            stored_objects = self.object_store.list_prefix(files_prefix)
        except Exception:  # pragma: no cover — listing failure falls back to manifest-only
            stored_objects = []
        size_by_path: dict[str, int] = {}
        for obj in stored_objects:
            rel = obj.path.removeprefix(files_prefix)
            if rel:
                size_by_path[rel] = int(obj.size or 0)
        files: list[CodeFileSummary] = []
        for rel_path in manifest.get("stored_paths") or []:
            files.append(
                CodeFileSummary(
                    repo_path=rel_path,
                    size_bytes=size_by_path.get(rel_path, 0),
                    indexed=rel_path in indexed,
                )
            )
        return CodeFileListResponse(
            paper_id=paper_id, eligibility=eligibility, resolved_sha=resolved_sha, files=files
        )

    def read_paper_code_file(self, paper_id: str, repo_path: str) -> CodeReadResponse | None:
        """Stream a single repo file back as text.

        Repo-relative path; any ``..`` segment is rejected. Preserves lazy
        reads for files that were stored but not ingested into ``text_chunks``
        (PR 2 spec's "full file read works for scanned stored text files
        that were not fully indexed").
        """
        paper = self.repository.get_paper(paper_id)
        if paper is None or paper.index_status != "indexed":
            return None
        eligibility = self._code_eligibility(paper)
        safe_path = self._normalize_code_path(repo_path)
        base = CodeReadResponse(paper_id=paper_id, eligibility=eligibility, repo_path=safe_path or repo_path)
        if safe_path is None or not eligibility.eligible:
            return base
        try:
            data = self.object_store.get_bytes(f"papers/{paper_id}/code/files/{safe_path}")
        except Exception:
            return base
        text_content = data.decode("utf-8", errors="replace")
        return CodeReadResponse(
            paper_id=paper_id,
            eligibility=eligibility,
            repo_path=safe_path,
            content=text_content,
            size_bytes=len(data),
        )

    @staticmethod
    def _normalize_code_path(raw: str) -> str | None:
        """Return a safe repo-relative path, or ``None`` if the input is
        unsafe or empty.

        Rejects absolute paths (``/etc/passwd``, ``\\absolute``), Windows-
        drive prefixes (``C:/...``, ``C:\\...``), ``..`` / ``.`` segments,
        and empty segments. Must NOT strip a leading slash before the
        absolute-path check — doing so lets ``/absolute/path`` slip
        through as ``absolute/path`` and the read endpoint would then
        happily serve it if such a stored file existed.
        """
        raw = (raw or "").strip()
        if not raw:
            return None
        if raw.startswith("/") or raw.startswith("\\"):
            return None
        if PurePosixPath(raw).is_absolute():
            return None
        # Reject true Windows drive prefixes (``C:/...``, ``C:\\...``) only
        # — NOT any second-character ``:``. Valid GitHub filenames can
        # contain a colon (e.g. ``a:b.txt``, ``v:1/notes.md``), so a broad
        # ``raw[1] == ':'`` guard would make those unreadable.
        if _DRIVE_PREFIX_RE.match(raw):
            return None
        parts = raw.split("/")
        if any(segment in {"", "..", "."} for segment in parts):
            return None
        return "/".join(parts)

    def search_paper_code(
        self, paper_id: str, query: str, *, limit: int = 10
    ) -> CodeSearchResponse | None:
        """Paper-scoped code search over indexed README / docs / manifest /
        entrypoint / signature chunks. Non-Python papers return ineligible
        (empty ``hits``) without hitting the chunk table."""
        paper = self.repository.get_paper(paper_id)
        if paper is None or paper.index_status != "indexed":
            return None
        eligibility = self._code_eligibility(paper)
        base = CodeSearchResponse(paper_id=paper_id, eligibility=eligibility, query=query, hits=[])
        if not eligibility.eligible or not (query or "").strip():
            return base
        rows = self.repository.search_paper_code_sections(
            paper_id, query, limit=max(1, min(limit, 50))
        )
        hits = [
            CodeSearchHit(
                repo_path=row.path.removeprefix("code/files/") if row.path.startswith("code/files/") else row.path,
                chunk_kind=row.chunk_kind,
                preview=row.preview,
                score=row.score or 0.0,
            )
            for row in rows
        ]
        return CodeSearchResponse(
            paper_id=paper_id, eligibility=eligibility, query=query, hits=hits
        )

    def get_paper_code_context(
        self, paper_id: str, query: str, *, limit: int = 5
    ) -> CodeContextResponse | None:
        """Compact top-N chunks concatenated into a single context blob.

        Useful when the caller wants "give me the strongest implementation
        evidence for this query" rather than a ranked hit list. Returns
        ineligible for non-Python papers.
        """
        paper = self.repository.get_paper(paper_id)
        if paper is None or paper.index_status != "indexed":
            return None
        eligibility = self._code_eligibility(paper)
        base = CodeContextResponse(paper_id=paper_id, eligibility=eligibility, query=query)
        if not eligibility.eligible or not (query or "").strip():
            return base
        rows = self.repository.search_paper_code_sections(
            paper_id, query, limit=max(1, min(limit, 20))
        )
        hits: list[CodeSearchHit] = []
        blocks: list[str] = []
        for row in rows:
            clean_path = (
                row.path.removeprefix("code/files/") if row.path.startswith("code/files/") else row.path
            )
            hits.append(
                CodeSearchHit(
                    repo_path=clean_path,
                    chunk_kind=row.chunk_kind,
                    preview=row.preview,
                    score=row.score or 0.0,
                )
            )
            blocks.append(f"### {clean_path} ({row.chunk_kind})\n{row.preview}")
        return CodeContextResponse(
            paper_id=paper_id,
            eligibility=eligibility,
            query=query,
            context="\n\n".join(blocks),
            hits=hits,
        )

    def enqueue_harvest_range_job(
        self,
        *,
        categories: list[str],
        start_date: str,
        end_date: str,
        limit_per_window: int | None = None,
        chunk_months: int = 3,
        source: str = "arxiv",
    ) -> JobResponse:
        """Queue a ``harvest_range`` planner job. Planner splits the date
        range into ``harvest_chunk`` sub-jobs, one per category per window.

        ``limit_per_window`` caps papers per (category, window). Total
        papers for a category across the whole range is
        ``limit_per_window * number_of_windows`` — the arg is deliberately
        named after the chunk so operators don't read it as a per-category
        total across the whole date range.
        """
        _validate_date(start_date, name="start_date")
        _validate_date(end_date, name="end_date")
        if start_date > end_date:
            raise PermanentError("start_date must be on or before end_date")
        if chunk_months < 1:
            raise PermanentError("chunk_months must be >= 1")
        if limit_per_window is not None and limit_per_window < 1:
            raise PermanentError("limit_per_window must be >= 1 when provided")
        if not categories:
            raise PermanentError("categories must not be empty")
        harvest_id = new_id("harvest")
        job = self.repository.create_job(
            "harvest_range",
            {
                "harvest_id": harvest_id,
                "source": source,
                "categories": list(categories),
                "start_date": start_date,
                "end_date": end_date,
                "limit_per_window": limit_per_window,
                "chunk_months": chunk_months,
            },
        )
        logger.info(
            "Created harvest_range job",
            extra={
                "event": "jobs.harvest_range.created",
                "job_id": job.job_id,
                "harvest_id": harvest_id,
                "source": source,
                "categories": list(categories),
                "start_date": start_date,
                "end_date": end_date,
                "chunk_months": chunk_months,
                "limit_per_window": limit_per_window,
            },
        )
        return job

    def run_harvest_range(
        self,
        *,
        source: str,
        categories: list[str],
        start_date: str,
        end_date: str,
        limit_per_window: int | None,
        chunk_months: int,
        harvest_id: str | None = None,
        root_job_id: str | None = None,
    ) -> dict[str, int]:
        """Planner: split ``[start_date, end_date]`` into ``chunk_months``
        windows per category and enqueue a ``harvest_chunk`` per window.

        v1 only supports ``source='arxiv'``. Raises PermanentError otherwise
        so the job dead-letters instead of looping on a config mismatch.
        """
        if source != "arxiv":
            raise PermanentError(f"harvest source {source!r} is not supported; v1 handles arxiv only")
        _validate_date(start_date, name="start_date")
        _validate_date(end_date, name="end_date")
        if start_date > end_date:
            raise PermanentError("start_date must be on or before end_date")
        if chunk_months < 1:
            raise PermanentError("chunk_months must be >= 1")
        if limit_per_window is not None and limit_per_window < 1:
            raise PermanentError("limit_per_window must be >= 1 when provided")
        if not categories:
            raise PermanentError("categories must not be empty")

        run_harvest_id = harvest_id or new_id("harvest")
        windows = _month_windows(start_date, end_date, chunk_months=chunk_months)
        chunks_created = 0
        for category in categories:
            self._raise_if_current_job_canceled()
            for win_start, win_end in windows:
                self._raise_if_current_job_canceled()
                self.repository.create_job(
                    "harvest_chunk",
                    {
                        "harvest_id": run_harvest_id,
                        "source": source,
                        "category": category,
                        "start_date": win_start,
                        "end_date": win_end,
                        "limit": limit_per_window,
                        "cursor": 0,
                        "fetched": 0,
                    },
                    dedupe_key=_harvest_chunk_dedupe_key(
                        harvest_id=run_harvest_id,
                        source=source,
                        category=category,
                        start_date=win_start,
                        end_date=win_end,
                        cursor=0,
                    ),
                    root_job_id=root_job_id or run_harvest_id,
                )
                chunks_created += 1
        logger.info(
            "harvest_range planner enqueued chunks",
            extra={
                "event": "harvest_range.planned",
                "harvest_id": run_harvest_id,
                "chunks_created": chunks_created,
                "category_count": len(categories),
                "window_count": len(windows),
            },
        )
        return {"chunks_created": chunks_created}

    def run_harvest_chunk(
        self,
        *,
        source: str,
        category: str,
        start_date: str,
        end_date: str,
        limit: int | None,
        cursor: int,
        fetched: int = 0,
        harvest_id: str | None = None,
        root_job_id: str | None = None,
        tracking_job: JobResponse | None = None,
    ) -> dict[str, Any]:
        """Worker: fetch ONE page of one arxiv date window and index each
        paper, then enqueue a continuation ``harvest_chunk`` if more pages
        remain.

        Processing a single page per claim does two things arslnb called
        out on PR #37: it bounds worker time per claim (long windows no
        longer monopolize the loop), and it re-enters the priority queue
        between pages so a newly-arrived ``index_paper`` can cut in front.

        Resume semantics: the ``cursor`` and ``fetched`` fields are carried
        on the continuation's payload. If a chunk fails mid-page, the retry
        policy requeues the SAME payload, so work resumes at the cursor
        that was live when the failure happened — not at cursor=0.

        Idempotent — ``index_arxiv_paper`` no-ops on re-ingest, so a double
        claim (e.g. ``mark_job_completed`` crash between index and
        continuation enqueue) replays only the current page; continuation
        enqueue is deduped by the harvest run/window/cursor key.
        """
        if source != "arxiv":
            raise PermanentError(f"harvest source {source!r} is not supported; v1 handles arxiv only")
        _validate_date(start_date, name="start_date")
        _validate_date(end_date, name="end_date")
        if start_date > end_date:
            raise PermanentError("start_date must be on or before end_date")
        if not category:
            raise PermanentError("category must not be empty")
        if limit is not None and limit < 1:
            raise PermanentError("harvest chunk limit must be >= 1 when provided")

        run_harvest_id = harvest_id or new_id("harvest")
        page_size = 100
        offset = max(0, cursor or 0)
        running_fetched = max(0, fetched or 0)
        cap = limit

        if cap is not None and running_fetched >= cap:
            return {"fetched": 0, "indexed": 0, "enqueued_continuation": False}

        page, next_cursor = sources.fetch_range(
            category,
            start_date=start_date,
            end_date=end_date,
            start_offset=offset,
            page_size=page_size,
        )
        if not page:
            return {"fetched": 0, "indexed": 0, "enqueued_continuation": False}

        fetched_this_page = 0
        indexed = 0
        for paper in page:
            self._raise_if_current_job_canceled()
            if cap is not None and running_fetched + fetched_this_page >= cap:
                break
            fetched_this_page += 1
            try:
                self._call_index_arxiv_paper(
                    paper.arxiv_id,
                    paper=paper,
                    force=False,
                    tracking_job=tracking_job,
                )
                indexed += 1
            except PermanentError:
                if tracking_job is not None:
                    self.repository.record_job_paper(
                        tracking_job.job_id,
                        canonical_paper_id(paper.arxiv_id),
                        outcome="failed",
                        arxiv_id=paper.arxiv_id,
                        title=paper.title,
                    )
                # Individual paper can't be indexed (e.g. withdrawn) —
                # swallow so the chunk keeps going.
                continue

        new_total = running_fetched + fetched_this_page
        should_continue = (
            next_cursor is not None
            and (cap is None or new_total < cap)
        )
        enqueued_continuation = False
        if should_continue:
            # Persist cursor before returning success. The dedupe key keeps a
            # retried parent from forking a second continuation chain.
            self.repository.create_job(
                "harvest_chunk",
                {
                    "harvest_id": run_harvest_id,
                    "source": source,
                    "category": category,
                    "start_date": start_date,
                    "end_date": end_date,
                    "limit": limit,
                    "cursor": next_cursor,
                    "fetched": new_total,
                },
                dedupe_key=_harvest_chunk_dedupe_key(
                    harvest_id=run_harvest_id,
                    source=source,
                    category=category,
                    start_date=start_date,
                    end_date=end_date,
                    cursor=next_cursor,
                ),
                root_job_id=root_job_id or run_harvest_id,
            )
            enqueued_continuation = True

        logger.info(
            "harvest_chunk page completed",
            extra={
                "event": "harvest_chunk.page_completed",
                "harvest_id": run_harvest_id,
                "source": source,
                "category": category,
                "start_date": start_date,
                "end_date": end_date,
                "page_fetched": fetched_this_page,
                "indexed": indexed,
                "running_fetched": new_total,
                "enqueued_continuation": enqueued_continuation,
            },
        )
        return {
            "fetched": fetched_this_page,
            "indexed": indexed,
            "enqueued_continuation": enqueued_continuation,
        }

    # --- Top-cited harvest (#39 PR A) ----------------------------------- #

    def enqueue_harvest_top_cited_job(
        self,
        *,
        min_citations: int = 100,
        max_papers: int | None = None,
        chunk_size: int = 50,
    ) -> JobResponse:
        """Queue a ``harvest_top_cited`` planner job.

        Planner paginates Semantic Scholar's bulk search ordered by
        ``citationCount:desc``, filters to arxiv-linked papers, and
        enqueues ``harvest_chunk`` jobs (one per ``chunk_size`` arxiv
        ids) with ``source='s2_top_cited'``. Workers index each id;
        existing priority + retry + dedupe machinery from the date-range
        harvest applies unchanged.

        ``categories`` is intentionally absent: S2's bulk search does
        not surface arxiv categories, so any pre-index filter would
        require a separate per-candidate arxiv lookup that defeats the
        bulk-fetch advantage. A future PR can add the gate once we have
        a cheaper signal — see PR #50 review for the rationale.
        """
        if min_citations < 0:
            raise PermanentError("min_citations must be >= 0")
        if max_papers is not None and max_papers < 1:
            raise PermanentError("max_papers must be >= 1 when provided")
        if chunk_size < 1 or chunk_size > 500:
            raise PermanentError("chunk_size must be in [1, 500]")
        job = self.repository.create_job(
            "harvest_top_cited",
            {
                "min_citations": int(min_citations),
                "max_papers": max_papers,
                "chunk_size": int(chunk_size),
            },
        )
        logger.info(
            "Created harvest_top_cited job",
            extra={
                "event": "jobs.harvest_top_cited.created",
                "job_id": job.job_id,
                "min_citations": min_citations,
                "max_papers": max_papers,
                "chunk_size": chunk_size,
            },
        )
        return job

    def run_harvest_top_cited(
        self,
        *,
        min_citations: int,
        max_papers: int | None,
        chunk_size: int,
        harvest_id: str | None = None,
        root_job_id: str | None = None,
    ) -> dict[str, int]:
        """Planner: stream S2 bulk-search pages and enqueue
        ``harvest_chunk`` jobs in ``chunk_size`` arxiv-id batches.

        Each enqueued chunk carries ``source='s2_top_cited'`` and a list
        of ``arxiv_ids``; the chunk worker then iterates and calls
        ``index_arxiv_paper`` per id. Per-id ``PermanentError`` (e.g. a
        404 on arxiv) is swallowed so one bad id doesn't kill the chunk.

        Pagination contract (PR #50 review): ``fetch_top_cited_cs`` may
        return an empty ``page`` while ``next_cursor`` is still set,
        because non-arxiv rows are filtered out client-side. The planner
        must keep paging until S2 itself signals end-of-stream (i.e.
        ``next_cursor is None``) — never break on an empty filtered page.
        """
        if min_citations < 0:
            raise PermanentError("min_citations must be >= 0")
        if max_papers is not None and max_papers < 1:
            raise PermanentError("max_papers must be >= 1 when provided")
        if chunk_size < 1 or chunk_size > 500:
            raise PermanentError("chunk_size must be in [1, 500]")

        run_harvest_id = harvest_id or new_id("harvest")
        cursor: str | None = None
        chunks_created = 0
        candidates_emitted = 0
        pages_fetched = 0
        pending_batch: list[str] = []

        def _flush_batch() -> None:
            nonlocal chunks_created, pending_batch
            if not pending_batch:
                return
            self.repository.create_job(
                "harvest_chunk",
                {
                    "harvest_id": run_harvest_id,
                    "source": "s2_top_cited",
                    "arxiv_ids": list(pending_batch),
                    "min_citations": int(min_citations),
                },
                dedupe_key=(
                    f"harvest_chunk:{run_harvest_id}:s2_top_cited:"
                    f"{pending_batch[0]}-{pending_batch[-1]}-{len(pending_batch)}"
                ),
                root_job_id=root_job_id or run_harvest_id,
            )
            chunks_created += 1
            pending_batch = []

        while True:
            self._raise_if_current_job_canceled()
            if max_papers is not None and candidates_emitted >= max_papers:
                break
            page, next_cursor = sources.fetch_top_cited_cs(
                min_citations=min_citations,
                cursor=cursor,
            )
            pages_fetched += 1
            for candidate in page:
                self._raise_if_current_job_canceled()
                if max_papers is not None and candidates_emitted >= max_papers:
                    break
                pending_batch.append(candidate.arxiv_id)
                candidates_emitted += 1
                if len(pending_batch) >= chunk_size:
                    _flush_batch()
            # Keep paging until S2 itself signals last page. An empty
            # filtered ``page`` does NOT mean end-of-stream — bulk search
            # often returns pages with all non-arxiv rows that get
            # filtered to nothing client-side.
            if next_cursor is None:
                break
            cursor = next_cursor
        _flush_batch()

        logger.info(
            "harvest_top_cited planner enqueued chunks",
            extra={
                "event": "harvest_top_cited.planned",
                "harvest_id": run_harvest_id,
                "chunks_created": chunks_created,
                "candidates_emitted": candidates_emitted,
                "pages_fetched": pages_fetched,
                "min_citations": min_citations,
                "max_papers": max_papers,
            },
        )
        return {
            "chunks_created": chunks_created,
            "candidates_emitted": candidates_emitted,
            "pages_fetched": pages_fetched,
        }

    def run_harvest_chunk_top_cited(
        self,
        *,
        arxiv_ids: list[str],
        min_citations: int = 0,
        harvest_id: str | None = None,
        tracking_job: JobResponse | None = None,
    ) -> dict[str, int]:
        """Chunk worker for ``source='s2_top_cited'``.

        Iterates ``arxiv_ids`` and calls ``index_arxiv_paper`` per id.
        Per-id ``PermanentError`` is swallowed so a 404 doesn't fail the
        whole chunk; the retry policy still catches transient failures
        on the chunk as a whole. ``min_citations`` is carried for
        observability — server-side bulk search already filtered by
        citation count.
        """
        if not arxiv_ids:
            return {"requested": 0, "indexed": 0, "skipped": 0}
        run_harvest_id = harvest_id or new_id("harvest")
        indexed = 0
        skipped = 0
        for arxiv_id in arxiv_ids:
            self._raise_if_current_job_canceled()
            if not arxiv_id:
                skipped += 1
                continue
            try:
                self._call_index_arxiv_paper(
                    arxiv_id,
                    force=False,
                    tracking_job=tracking_job,
                )
                indexed += 1
            except PermanentError:
                if tracking_job is not None:
                    self.repository.record_job_paper(
                        tracking_job.job_id,
                        canonical_paper_id(arxiv_id),
                        outcome="failed",
                        arxiv_id=arxiv_id,
                    )
                skipped += 1
                continue
        logger.info(
            "harvest_top_cited chunk completed",
            extra={
                "event": "harvest_top_cited.chunk_completed",
                "harvest_id": run_harvest_id,
                "requested": len(arxiv_ids),
                "indexed": indexed,
                "skipped": skipped,
            },
        )
        return {
            "requested": len(arxiv_ids),
            "indexed": indexed,
            "skipped": skipped,
        }

    # --- Venue-accepted harvest (#39 PR B) ------------------------------ #

    def enqueue_harvest_venue_job(
        self,
        *,
        venue: str,
        year: int,
        max_papers: int | None = None,
        chunk_size: int = 50,
    ) -> JobResponse:
        """Queue a ``harvest_venue`` planner job.

        Planner pages Semantic Scholar's bulk search filtered by
        ``venue`` + ``year``, batches arxiv-linked candidates into
        ``chunk_size`` groups, and enqueues ``harvest_chunk`` jobs with
        ``source='s2_venue'``. The chunk worker indexes each paper and
        stamps the ``venue`` / ``venue_year`` / ``venue_source`` columns
        on the resulting row.
        """
        if not venue or not venue.strip():
            raise PermanentError("venue must not be empty")
        if year < 1900 or year > 2100:
            raise PermanentError("year must be a 4-digit year")
        if max_papers is not None and max_papers < 1:
            raise PermanentError("max_papers must be >= 1 when provided")
        if chunk_size < 1 or chunk_size > 500:
            raise PermanentError("chunk_size must be in [1, 500]")
        job = self.repository.create_job(
            "harvest_venue",
            {
                "venue": venue,
                "year": int(year),
                "max_papers": max_papers,
                "chunk_size": int(chunk_size),
            },
        )
        logger.info(
            "Created harvest_venue job",
            extra={
                "event": "jobs.harvest_venue.created",
                "job_id": job.job_id,
                "venue": venue,
                "year": year,
                "max_papers": max_papers,
                "chunk_size": chunk_size,
            },
        )
        return job

    def run_harvest_venue(
        self,
        *,
        venue: str,
        year: int,
        max_papers: int | None,
        chunk_size: int,
        harvest_id: str | None = None,
        root_job_id: str | None = None,
    ) -> dict[str, int]:
        """Planner: page S2 bulk-search by ``venue`` + ``year`` and
        enqueue ``harvest_chunk`` jobs (``source='s2_venue'``) in
        ``chunk_size`` batches.

        Each chunk payload carries a list of dicts with ``arxiv_id`` +
        ``venue`` (the canonical S2 venue string per paper) so the
        chunk worker can stamp the row without re-querying S2.
        """
        if not venue or not venue.strip():
            raise PermanentError("venue must not be empty")
        if year < 1900 or year > 2100:
            raise PermanentError("year must be a 4-digit year")
        if max_papers is not None and max_papers < 1:
            raise PermanentError("max_papers must be >= 1 when provided")
        if chunk_size < 1 or chunk_size > 500:
            raise PermanentError("chunk_size must be in [1, 500]")

        run_harvest_id = harvest_id or new_id("harvest")
        cursor: str | None = None
        chunks_created = 0
        candidates_emitted = 0
        pages_fetched = 0
        pending_batch: list[dict[str, Any]] = []

        def _flush_batch() -> None:
            nonlocal chunks_created, pending_batch
            if not pending_batch:
                return
            first_id = pending_batch[0]["arxiv_id"]
            last_id = pending_batch[-1]["arxiv_id"]
            self.repository.create_job(
                "harvest_chunk",
                {
                    "harvest_id": run_harvest_id,
                    "source": "s2_venue",
                    "papers": list(pending_batch),
                    "venue": venue,
                    "year": int(year),
                },
                dedupe_key=(
                    f"harvest_chunk:{run_harvest_id}:s2_venue:"
                    f"{first_id}-{last_id}-{len(pending_batch)}"
                ),
                root_job_id=root_job_id or run_harvest_id,
            )
            chunks_created += 1
            pending_batch = []

        while True:
            self._raise_if_current_job_canceled()
            if max_papers is not None and candidates_emitted >= max_papers:
                break
            page, next_cursor = sources.fetch_venue_accepted(
                venue=venue,
                year=year,
                cursor=cursor,
            )
            pages_fetched += 1
            for candidate in page:
                self._raise_if_current_job_canceled()
                if max_papers is not None and candidates_emitted >= max_papers:
                    break
                pending_batch.append({
                    "arxiv_id": candidate.arxiv_id,
                    "venue": candidate.venue_string,
                    "year": candidate.year,
                })
                candidates_emitted += 1
                if len(pending_batch) >= chunk_size:
                    _flush_batch()
            # Keep paging until S2 itself signals last page (PR #52 review).
            # An empty filtered ``page`` does NOT mean end-of-stream — bulk
            # search may return pages where every result is non-arxiv and
            # gets filtered out client-side.
            if next_cursor is None:
                break
            cursor = next_cursor
        _flush_batch()

        logger.info(
            "harvest_venue planner enqueued chunks",
            extra={
                "event": "harvest_venue.planned",
                "harvest_id": run_harvest_id,
                "venue": venue,
                "year": year,
                "chunks_created": chunks_created,
                "candidates_emitted": candidates_emitted,
                "pages_fetched": pages_fetched,
                "max_papers": max_papers,
            },
        )
        return {
            "chunks_created": chunks_created,
            "candidates_emitted": candidates_emitted,
            "pages_fetched": pages_fetched,
        }

    def run_harvest_chunk_venue(
        self,
        *,
        papers: list[dict[str, Any]],
        venue: str | None = None,
        year: int | None = None,
        harvest_id: str | None = None,
        tracking_job: JobResponse | None = None,
    ) -> dict[str, int]:
        """Chunk worker for ``source='s2_venue'``.

        Iterates ``papers`` (each a dict of ``arxiv_id`` + ``venue`` +
        ``year``), calls ``index_arxiv_paper`` to ingest, then
        ``set_paper_venue`` to stamp the venue columns. Per-id
        ``PermanentError`` is swallowed so a 404 doesn't fail the chunk;
        retry policy still catches transient failures on the chunk as a
        whole.
        """
        if not papers:
            return {"requested": 0, "indexed": 0, "stamped": 0, "skipped": 0}
        run_harvest_id = harvest_id or new_id("harvest")
        indexed = 0
        stamped = 0
        skipped = 0
        for entry in papers:
            self._raise_if_current_job_canceled()
            arxiv_id = (entry or {}).get("arxiv_id") or ""
            if not arxiv_id:
                skipped += 1
                continue
            try:
                stored = self._call_index_arxiv_paper(
                    arxiv_id,
                    force=False,
                    tracking_job=tracking_job,
                )
                indexed += 1
            except PermanentError:
                if tracking_job is not None:
                    self.repository.record_job_paper(
                        tracking_job.job_id,
                        canonical_paper_id(arxiv_id),
                        outcome="failed",
                        arxiv_id=arxiv_id,
                        title=str((entry or {}).get("title") or "") or None,
                    )
                skipped += 1
                continue
            paper_venue = entry.get("venue") or venue
            paper_year = entry.get("year") or year
            updated = self.repository.set_paper_venue(
                stored.paper_id,
                venue=paper_venue,
                venue_year=int(paper_year) if paper_year is not None else None,
                venue_source="s2_bulk",
            )
            if updated is not None:
                stamped += 1
        logger.info(
            "harvest_venue chunk completed",
            extra={
                "event": "harvest_venue.chunk_completed",
                "harvest_id": run_harvest_id,
                "venue": venue,
                "year": year,
                "requested": len(papers),
                "indexed": indexed,
                "stamped": stamped,
                "skipped": skipped,
            },
        )
        return {
            "requested": len(papers),
            "indexed": indexed,
            "stamped": stamped,
            "skipped": skipped,
        }

    # --- Author-signal harvest (#39 PR C) -------------------------------- #

    def enqueue_harvest_author_job(
        self,
        *,
        min_paper_count: int = 2,
        min_cite_mass: int = 5_000,
        max_authors: int = 100,
        max_papers_per_author: int | None = None,
        chunk_size: int = 50,
    ) -> JobResponse:
        """Queue a ``harvest_author`` planner job (#39 PR C).

        The planner derives the top-author list from the indexed
        corpus by aggregating ``paper_authors`` ↔ ``papers``,
        picks the top ``max_authors`` by cite mass, then for each
        pages S2's ``/author/{id}/papers`` and enqueues
        ``harvest_chunk`` jobs (``source='s2_author'``) for the
        arxiv-linked papers.

        Defaults: ``min_cite_mass=5000`` matches the #39 spec
        threshold ("≥5000 in Computer Science"). Operators raise
        the bar for tighter coverage or lower it for wider sweep.
        """
        if min_paper_count < 1:
            raise PermanentError("min_paper_count must be >= 1")
        if min_cite_mass < 0:
            raise PermanentError("min_cite_mass must be >= 0")
        if max_authors < 1:
            raise PermanentError("max_authors must be >= 1")
        if max_papers_per_author is not None and max_papers_per_author < 1:
            raise PermanentError("max_papers_per_author must be >= 1 when provided")
        if chunk_size < 1 or chunk_size > 500:
            raise PermanentError("chunk_size must be in [1, 500]")
        job = self.repository.create_job(
            "harvest_author",
            {
                "min_paper_count": int(min_paper_count),
                "min_cite_mass": int(min_cite_mass),
                "max_authors": int(max_authors),
                "max_papers_per_author": max_papers_per_author,
                "chunk_size": int(chunk_size),
            },
        )
        logger.info(
            "Created harvest_author job",
            extra={
                "event": "jobs.harvest_author.created",
                "job_id": job.job_id,
                "min_paper_count": min_paper_count,
                "min_cite_mass": min_cite_mass,
                "max_authors": max_authors,
                "max_papers_per_author": max_papers_per_author,
                "chunk_size": chunk_size,
            },
        )
        return job

    def run_harvest_author(
        self,
        *,
        min_paper_count: int,
        min_cite_mass: int,
        max_authors: int,
        max_papers_per_author: int | None,
        chunk_size: int,
        harvest_id: str | None = None,
        root_job_id: str | None = None,
    ) -> dict[str, int]:
        """Planner: derive top authors from the indexed corpus, then for
        each page S2 and enqueue ``harvest_chunk`` jobs
        (``source='s2_author'``) for arxiv-linked papers.

        The chunk payload carries ``arxiv_ids`` plus the
        ``top_author_s2_id`` so the chunk worker can stamp the
        provenance column when it indexes each paper.
        """
        if min_paper_count < 1:
            raise PermanentError("min_paper_count must be >= 1")
        if min_cite_mass < 0:
            raise PermanentError("min_cite_mass must be >= 0")
        if max_authors < 1:
            raise PermanentError("max_authors must be >= 1")
        if chunk_size < 1 or chunk_size > 500:
            raise PermanentError("chunk_size must be in [1, 500]")

        run_harvest_id = harvest_id or new_id("harvest")
        top_authors = self.repository.list_top_authors(
            min_paper_count=min_paper_count,
            min_cite_mass=min_cite_mass,
            limit=max_authors,
        )
        chunks_created = 0
        candidates_emitted = 0
        authors_visited = 0

        for top_author in top_authors:
            self._raise_if_current_job_canceled()
            author_s2_id = top_author["author_s2_id"]
            authors_visited += 1
            cursor: int | None = 0
            per_author_emitted = 0
            pending_batch: list[str] = []

            def _flush_batch(author_id: str = author_s2_id) -> None:
                nonlocal chunks_created, pending_batch
                if not pending_batch:
                    return
                self.repository.create_job(
                    "harvest_chunk",
                    {
                        "harvest_id": run_harvest_id,
                        "source": "s2_author",
                        "arxiv_ids": list(pending_batch),
                        "top_author_s2_id": author_id,
                    },
                    dedupe_key=(
                        f"harvest_chunk:{run_harvest_id}:s2_author:{author_id}:"
                        f"{pending_batch[0]}-{pending_batch[-1]}-{len(pending_batch)}"
                    ),
                    root_job_id=root_job_id or run_harvest_id,
                )
                chunks_created += 1
                pending_batch = []

            while cursor is not None:
                self._raise_if_current_job_canceled()
                if max_papers_per_author is not None and per_author_emitted >= max_papers_per_author:
                    break
                page, next_cursor = sources.fetch_author_papers(
                    s2_author_id=author_s2_id,
                    cursor=cursor,
                )
                for candidate in page:
                    self._raise_if_current_job_canceled()
                    if max_papers_per_author is not None and per_author_emitted >= max_papers_per_author:
                        break
                    pending_batch.append(candidate.arxiv_id)
                    candidates_emitted += 1
                    per_author_emitted += 1
                    if len(pending_batch) >= chunk_size:
                        _flush_batch()
                # Keep paging until S2 itself signals last page (same shape
                # as the PR #50 / #52 EOF fixes). ``fetch_author_papers``
                # filters non-arxiv rows client-side and may return an
                # empty ``page`` while ``next_cursor`` is still set; only
                # ``next_cursor is None`` means end-of-stream.
                cursor = next_cursor
            _flush_batch()

        logger.info(
            "harvest_author planner enqueued chunks",
            extra={
                "event": "harvest_author.planned",
                "harvest_id": run_harvest_id,
                "authors_visited": authors_visited,
                "chunks_created": chunks_created,
                "candidates_emitted": candidates_emitted,
            },
        )
        return {
            "authors_visited": authors_visited,
            "chunks_created": chunks_created,
            "candidates_emitted": candidates_emitted,
        }

    def run_harvest_chunk_author(
        self,
        *,
        arxiv_ids: list[str],
        top_author_s2_id: str | None = None,
        harvest_id: str | None = None,
        tracking_job: JobResponse | None = None,
    ) -> dict[str, int]:
        """Chunk worker for ``source='s2_author'``.

        Iterates ``arxiv_ids``, calls ``index_arxiv_paper``, and stamps
        ``top_author_s2_id`` via ``set_paper_top_author``. Per-id
        ``PermanentError`` is swallowed.

        ``arxiv_ids`` is deduped on entry — multiple top authors can
        surface the same paper, and the planner can produce duplicate
        ids in a single chunk. ``index_arxiv_paper`` is idempotent so
        duplicates are safe, but the work is wasted.

        ``stamped`` counts papers whose post-call ``top_author_s2_id``
        equals what we asked for, so under first-write-wins a paper
        already stamped by a different author does not get
        double-counted as our success.
        """
        if not arxiv_ids:
            return {"requested": 0, "indexed": 0, "stamped": 0, "skipped": 0}
        run_harvest_id = harvest_id or new_id("harvest")
        # Preserve order while deduping; drop empties up front.
        deduped_ids = list(dict.fromkeys(aid for aid in arxiv_ids if aid))
        indexed = 0
        stamped = 0
        skipped = len(arxiv_ids) - len(deduped_ids)  # empty/duplicate entries
        for arxiv_id in deduped_ids:
            self._raise_if_current_job_canceled()
            try:
                stored = self._call_index_arxiv_paper(arxiv_id, force=False, tracking_job=tracking_job)
                indexed += 1
            except PermanentError:
                if tracking_job is not None:
                    self.repository.record_job_paper(
                        tracking_job.job_id,
                        canonical_paper_id(arxiv_id),
                        outcome="failed",
                        arxiv_id=arxiv_id,
                    )
                skipped += 1
                continue
            if top_author_s2_id:
                updated = self.repository.set_paper_top_author(
                    stored.paper_id,
                    top_author_s2_id=top_author_s2_id,
                )
                if updated is not None and updated.top_author_s2_id == top_author_s2_id:
                    stamped += 1
        logger.info(
            "harvest_author chunk completed",
            extra={
                "event": "harvest_author.chunk_completed",
                "harvest_id": run_harvest_id,
                "top_author_s2_id": top_author_s2_id,
                "requested": len(arxiv_ids),
                "indexed": indexed,
                "stamped": stamped,
                "skipped": skipped,
            },
        )
        return {
            "requested": len(arxiv_ids),
            "indexed": indexed,
            "stamped": stamped,
            "skipped": skipped,
        }

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
        job = self.repository.claim_pending_job(worker_id=self._worker_id, lease_seconds=JOB_LEASE_SECONDS)
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
        heartbeat_stop, heartbeat_thread = self._start_job_heartbeat(job)
        current_job_token = _CURRENT_JOB.set(job)
        try:
            self._raise_if_current_job_canceled()
            if job.job_type == "index_paper":
                self._call_index_arxiv_paper(
                    job.payload["arxiv_id"],
                    force=bool(job.payload.get("force")),
                    tracking_job=job,
                )
            elif job.job_type == "sync_latest":
                self.sync_latest_papers(
                    days=int(job.payload.get("days", self.settings.latest_sync_days)),
                    categories=list(job.payload.get("categories", self.settings.arxiv_categories)),
                    limit_per_category=int(
                        job.payload.get("limit_per_category", self.settings.latest_sync_limit_per_category)
                    ),
                    tracking_job=job,
                )
            elif job.job_type == "harvest_range":
                # Accept legacy ``limit_per_category`` payloads written
                # before the rename so in-flight planner jobs still run
                # after deploy.
                limit_per_window = job.payload.get("limit_per_window")
                if limit_per_window is None:
                    limit_per_window = job.payload.get("limit_per_category")
                self.run_harvest_range(
                    source=str(job.payload.get("source", "arxiv")),
                    harvest_id=str(job.payload.get("harvest_id") or job.job_id),
                    categories=list(job.payload.get("categories", [])),
                    start_date=str(job.payload["start_date"]),
                    end_date=str(job.payload["end_date"]),
                    limit_per_window=limit_per_window,
                    chunk_months=int(job.payload.get("chunk_months", 3)),
                    root_job_id=job.root_job_id or job.job_id,
                )
            elif job.job_type == "harvest_chunk":
                source = str(job.payload.get("source", "arxiv"))
                if source == "s2_top_cited":
                    # New chunk shape from #39 PR A: arxiv_ids list,
                    # not category/window. Routed to its own handler;
                    # priority + retry come from the shared harvest_chunk
                    # job-type wiring.
                    self.run_harvest_chunk_top_cited(
                        arxiv_ids=list(job.payload.get("arxiv_ids", [])),
                        min_citations=int(job.payload.get("min_citations", 0)),
                        harvest_id=str(job.payload.get("harvest_id") or job.job_id),
                        tracking_job=job,
                    )
                elif source == "s2_venue":
                    # #39 PR B: list of {arxiv_id, venue, year} dicts.
                    self.run_harvest_chunk_venue(
                        papers=list(job.payload.get("papers", [])),
                        venue=job.payload.get("venue"),
                        year=job.payload.get("year"),
                        harvest_id=str(job.payload.get("harvest_id") or job.job_id),
                        tracking_job=job,
                    )
                elif source == "s2_author":
                    # #39 PR C: arxiv_ids list + top_author_s2_id stamp.
                    self.run_harvest_chunk_author(
                        arxiv_ids=list(job.payload.get("arxiv_ids", [])),
                        top_author_s2_id=job.payload.get("top_author_s2_id"),
                        harvest_id=str(job.payload.get("harvest_id") or job.job_id),
                        tracking_job=job,
                    )
                else:
                    self.run_harvest_chunk(
                        source=source,
                        harvest_id=str(job.payload.get("harvest_id") or job.job_id),
                        category=str(job.payload["category"]),
                        start_date=str(job.payload["start_date"]),
                        end_date=str(job.payload["end_date"]),
                        limit=job.payload.get("limit"),
                        cursor=int(job.payload.get("cursor", 0)),
                        fetched=int(job.payload.get("fetched", 0)),
                        root_job_id=job.root_job_id or job.job_id,
                        tracking_job=job,
                    )
            elif job.job_type == "harvest_top_cited":
                self.run_harvest_top_cited(
                    min_citations=int(job.payload.get("min_citations", 0)),
                    max_papers=job.payload.get("max_papers"),
                    chunk_size=int(job.payload.get("chunk_size", 50)),
                    harvest_id=str(job.payload.get("harvest_id") or job.job_id),
                    root_job_id=job.root_job_id or job.job_id,
                )
            elif job.job_type == "harvest_venue":
                self.run_harvest_venue(
                    venue=str(job.payload["venue"]),
                    year=int(job.payload["year"]),
                    max_papers=job.payload.get("max_papers"),
                    chunk_size=int(job.payload.get("chunk_size", 50)),
                    harvest_id=str(job.payload.get("harvest_id") or job.job_id),
                    root_job_id=job.root_job_id or job.job_id,
                )
            elif job.job_type == "harvest_author":
                self.run_harvest_author(
                    min_paper_count=int(job.payload.get("min_paper_count", 2)),
                    min_cite_mass=int(job.payload.get("min_cite_mass", 5_000)),
                    max_authors=int(job.payload.get("max_authors", 100)),
                    max_papers_per_author=job.payload.get("max_papers_per_author"),
                    chunk_size=int(job.payload.get("chunk_size", 50)),
                    harvest_id=str(job.payload.get("harvest_id") or job.job_id),
                    root_job_id=job.root_job_id or job.job_id,
                )
            elif job.job_type in {"reindex_embeddings", "reindex_missing_embeddings"}:
                self.reindex_embeddings(
                    limit=int(job.payload.get("limit", 100_000)),
                    include_chunks=bool(job.payload.get("include_chunks", True)),
                    rewrite_existing=bool(
                        job.payload.get("rewrite_existing", job.job_type == "reindex_embeddings")
                    ),
                    tracking_job=job,
                )
            elif job.job_type == "enrich_missing_metadata":
                self.enrich_missing_metadata(
                    limit=int(job.payload.get("limit", 50_000)),
                    min_age_seconds=int(job.payload.get("min_age_seconds", 3600)),
                    raise_on_failed_batches=True,
                    tracking_job=job,
                )
            elif job.job_type == "ingest_repo":
                self.ingest_repo(
                    job.payload["paper_id"],
                    force=bool(job.payload.get("force")),
                    tracking_job=job,
                )
            elif job.job_type == "project_presentation":
                self.generate_project_presentation(
                    user_id=str(job.payload["user_id"]),
                    project_id=str(job.payload["project_id"]),
                    presentation_id=str(job.payload["presentation_id"]),
                    context_snapshot=dict(job.payload.get("context_snapshot") or {}),
                )
            else:
                raise ValueError(f"Unsupported job type: {job.job_type}")
            self._raise_if_current_job_canceled()
            self.repository.mark_job_completed(job.job_id, lease_token=job.lease_token)
            logger.info(
                "Completed job",
                extra={
                    "event": "jobs.completed",
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                },
            )
        except JobCanceled:
            self.repository.mark_job_canceled(job.job_id, lease_token=job.lease_token)
            self._sync_canceled_job_side_effects(job)
            logger.info(
                "Canceled job",
                extra={
                    "event": "jobs.canceled",
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                },
            )
        except Exception as exc:  # pragma: no cover - surfaced via job state and API
            self._handle_job_failure(job, exc)
        finally:
            _CURRENT_JOB.reset(current_job_token)
            heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=1)
        return self.repository.get_job(job.job_id)

    def _handle_job_failure(self, job: JobResponse, exc: Exception) -> None:
        """Classify the exception, compute the next-attempt schedule, and
        hand the decision to the repository. ``PermanentError`` dead-letters
        immediately. ``RetryableError`` and uncaught exceptions back off
        exponentially with jitter until ``max_attempts`` is reached; after
        that they too dead-letter. Uncaught defaults to retryable — safer
        to retry a bug than to silently drop work."""
        permanent = isinstance(exc, PermanentError) or self._job_error_is_permanent(job, exc)
        attempts = (job.attempts or 0) + 0  # claim already incremented
        max_attempts = job.max_attempts or 3
        will_retry = (not permanent) and attempts < max_attempts
        next_run_at = _retry_delay_for(attempts) if will_retry else None
        error_message = self._job_error_message(job, exc, will_retry=will_retry)
        self.repository.mark_job_failed(
            job.job_id,
            error_message,
            permanent=permanent,
            next_run_at=next_run_at,
            lease_token=job.lease_token,
            error_trace=traceback.format_exc(),
        )
        self._sync_failed_job_side_effects(job, error_message=error_message, will_retry=will_retry)
        logger.exception(
            "Job failed",
            extra={
                "event": "jobs.failed",
                "job_id": job.job_id,
                "job_type": job.job_type,
                "attempts": attempts,
                "max_attempts": max_attempts,
                "classification": "permanent" if permanent else "retryable",
                "will_retry": will_retry,
                "next_run_at": next_run_at.isoformat() if next_run_at else None,
            },
        )

    def _job_error_is_permanent(self, job: JobResponse, exc: Exception) -> bool:
        if job.job_type != "project_presentation":
            return False
        status_code = self._http_status_code(exc)
        if status_code is None:
            return False
        return 400 <= status_code < 500 and status_code not in {408, 409, 425, 429}

    def _job_error_message(self, job: JobResponse, exc: Exception, *, will_retry: bool) -> str:
        if job.job_type == "project_presentation":
            message = self._project_presentation_error_message(exc, will_retry=will_retry)
            if message:
                return message
        return self._sanitize_job_error_message(str(exc))

    def _sync_failed_job_side_effects(self, job: JobResponse, *, error_message: str, will_retry: bool) -> None:
        if job.job_type != "project_presentation":
            return
        presentation_id = str(job.payload.get("presentation_id") or "")
        if not presentation_id:
            return
        if will_retry:
            self.repository.upsert_project_presentation_pending(  # type: ignore[attr-defined]
                user_id=str(job.payload.get("user_id") or ""),
                project_id=str(job.payload.get("project_id") or ""),
                context_snapshot=dict(job.payload.get("context_snapshot") or {}),
                job_id=job.job_id,
            )
            return
        self.repository.fail_project_presentation(presentation_id, error=error_message)  # type: ignore[attr-defined]

    def _sync_canceled_job_side_effects(self, job: JobResponse) -> None:
        if job.job_type != "project_presentation":
            return
        presentation_id = str(job.payload.get("presentation_id") or "")
        project_id = str(job.payload.get("project_id") or "")
        user_id = str(job.payload.get("user_id") or "")
        if not presentation_id or not project_id or not user_id:
            return
        current = self.repository.get_project_presentation(  # type: ignore[attr-defined]
            user_id=user_id,
            project_id=project_id,
        )
        if current is not None and current.status == "completed":
            return
        self.repository.cancel_project_presentation(presentation_id)  # type: ignore[attr-defined]

    def _project_presentation_error_message(self, exc: Exception, *, will_retry: bool) -> str:
        status_code = self._http_status_code(exc)
        if status_code is None:
            return self._sanitize_job_error_message(str(exc))
        if status_code in {408, 409, 425, 429} or 500 <= status_code < 600:
            suffix = " It will retry automatically." if will_retry else " Retry the presentation in a few minutes."
            return f"OpenAI is temporarily unavailable for presentation generation (HTTP {status_code}).{suffix}"
        if status_code in {401, 403}:
            return f"OpenAI rejected the saved API key for presentation generation (HTTP {status_code}). Update the OpenAI key in Settings."
        if status_code == 404:
            return (
                "OpenAI returned HTTP 404 for presentation generation. Check that "
                f"`PAPERBASE_OPENAI_MODEL` (`{self.settings.openai_model}`) is available for this API key "
                "and that `PAPERBASE_OPENAI_BASE_URL` points to the OpenAI API."
            )
        if 400 <= status_code < 500:
            return (
                f"OpenAI rejected the presentation request (HTTP {status_code}). Check the configured "
                f"model `{self.settings.openai_model}` and saved OpenAI API key."
            )
        return self._sanitize_job_error_message(str(exc))

    @staticmethod
    def _http_status_code(exc: Exception) -> int | None:
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            return exc.response.status_code
        return None

    @staticmethod
    def _sanitize_job_error_message(message: str) -> str:
        text = str(message or "").strip()
        if "<html" in text[:1000].lower():
            text = re.sub(r"<script\b.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
            text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:1000] if text else "Job failed"

    def retry_dead_job(self, job_id: str) -> JobResponse | None:
        """Promote a dead job back to pending with attempts reset. Returns
        the updated job, or None if the id isn't dead/failed."""
        return self.repository.requeue_dead_job(job_id)

    def cancel_job(self, job_id: str) -> JobResponse | None:
        """Cancel a pending job immediately or request cooperative
        cancellation from the worker currently holding the running lease."""
        return self.repository.cancel_job(job_id)

    def list_dead_jobs(self, *, limit: int = 50) -> list[JobResponse]:
        return self.list_jobs_by_status("dead", limit=limit)

    def list_jobs_by_status(self, status: str, *, limit: int = 50) -> list[JobResponse]:
        if status == "all":
            jobs: list[JobResponse] = []
            seen: set[str] = set()
            for bucket in ("pending", "running", "completed", "dead", "failed"):
                for job in self.repository.list_jobs_by_status(bucket, limit=limit):
                    if job.job_id in seen:
                        continue
                    seen.add(job.job_id)
                    jobs.append(job)
            jobs.sort(key=lambda job: job.updated_at, reverse=True)
            return jobs[:limit]
        if status == "dead":
            jobs = [
                *self.repository.list_jobs_by_status("dead", limit=limit),
                *self.repository.list_jobs_by_status("failed", limit=limit),
            ]
            jobs.sort(key=lambda job: job.updated_at, reverse=True)
            return jobs[:limit]
        return self.repository.list_jobs_by_status(status, limit=limit)

    def _load_worker_summary(self) -> AdminWorkerSummary:
        status_url = (self.settings.worker_status_url or "").strip().rstrip("/")
        if not status_url:
            return AdminWorkerSummary(status="unknown", source="unconfigured")

        try:
            with urllib_request.urlopen(f"{status_url}/health", timeout=2) as response:
                health_payload = json.loads(response.read().decode("utf-8"))
            with urllib_request.urlopen(f"{status_url}/metrics", timeout=2) as response:
                metrics_payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, urllib_error.URLError):
            return AdminWorkerSummary(status="unknown", source="unreachable")

        last_loop_at = metrics_payload.get("last_loop_at")
        loop_dt = None
        if isinstance(last_loop_at, (int, float)) and last_loop_at > 0:
            from datetime import datetime, timezone

            loop_dt = datetime.fromtimestamp(float(last_loop_at), tz=timezone.utc)
        status = str(health_payload.get("status") or "unknown")
        return AdminWorkerSummary(
            status=status,
            stalled=status == "stalled",
            reachable=True,
            source="worker_status_url",
            last_loop_at=loop_dt,
            retry_scheduled_count=int(metrics_payload.get("retry_scheduled_count") or 0),
            dead_count=int(metrics_payload.get("dead_count") or 0),
            last_error=(str(metrics_payload.get("last_error")) if metrics_payload.get("last_error") else None),
            last_terminal_error=(
                str(metrics_payload.get("last_terminal_error"))
                if metrics_payload.get("last_terminal_error")
                else None
            ),
        )

    def _admin_row_from_jobs(
        self,
        jobs: list[JobResponse],
        *,
        worker_summary: AdminWorkerSummary,
        now,
    ) -> AdminJobOverviewRow:
        representative = next((job for job in jobs if job.job_id == (job.root_job_id or job.job_id)), jobs[0])
        root_job_id = representative.root_job_id or representative.job_id
        status_counts: dict[str, int] = {}
        latest_error = representative.error
        latest_error_at = representative.updated_at
        latest_error_job = representative if representative.error else None
        latest_updated_at = representative.updated_at
        earliest_created_at = representative.created_at
        for job in jobs:
            status_counts[job.status] = status_counts.get(job.status, 0) + 1
            if job.updated_at > latest_updated_at:
                latest_updated_at = job.updated_at
            if job.created_at < earliest_created_at:
                earliest_created_at = job.created_at
            if job.error and (latest_error is None or job.updated_at >= latest_error_at):
                latest_error = job.error
                latest_error_at = job.updated_at
                latest_error_job = job

        base_status = representative.status
        if status_counts.get("running", 0) > 0:
            base_status = "running"
        elif status_counts.get("pending", 0) > 0:
            base_status = "pending"
        elif status_counts.get("dead", 0) > 0 or status_counts.get("failed", 0) > 0:
            base_status = "dead"
        elif status_counts.get("completed", 0) == len(jobs):
            base_status = "completed"

        display_status = _admin_display_status(base_status)
        if base_status == "running" and (
            worker_summary.stalled or _job_activity_stalled(latest_updated_at, now=now)
        ):
            display_status = "stalled"

        tracked: list[Any] = []
        tracking_available = _job_type_tracks_papers(representative.job_type)
        indexed_paper_count: int | None = 0 if tracking_available else None
        tracked_paper_count: int | None = 0 if tracking_available else None
        paper_outcome_counts: dict[str, int] = {}
        if tracking_available:
            tracked = self.repository.list_job_papers_for_root(root_job_id)
        if tracking_available:
            paper_outcome_counts = _job_paper_outcome_counts(tracked)
            indexed_paper_count = paper_outcome_counts.get("indexed", 0)
            tracked_paper_count = sum(paper_outcome_counts.values())

        child_jobs = [_admin_child_row(job) for job in jobs if job.job_id != representative.job_id]
        child_job_ids = [job.job_id for job in jobs if job.job_id != representative.job_id]
        latest_error_debug = _job_debug_payload(latest_error_job.payload) if latest_error_job is not None else {}
        details: dict[str, Any] = {
            "status_counts": status_counts,
            "job_count": len(jobs),
            "job_ids": [job.job_id for job in jobs],
            "child_job_ids": child_job_ids,
            "root_job_id": root_job_id,
            "representative_job_id": representative.job_id,
            "latest_error_job_id": latest_error_job.job_id if latest_error_job is not None else None,
            "latest_error_trace": latest_error_debug.get("last_error_trace") or None,
            "latest_error_failed_at": latest_error_debug.get("failed_at") or None,
            "latest_error_permanent": latest_error_debug.get("permanent"),
            "latest_error_next_run_at": latest_error_debug.get("next_run_at"),
            "worker_id": representative.worker_id,
            "heartbeat_at": representative.heartbeat_at,
            "lease_expires_at": representative.lease_expires_at,
            "next_run_at": representative.next_run_at,
            "last_attempted_at": representative.last_attempted_at,
            "cancel_requested_at": representative.cancel_requested_at,
            "cloud_run_log_filter": (
                f'jsonPayload.job_id="{root_job_id}" OR jsonPayload.root_job_id="{root_job_id}" '
                f'OR textPayload:"{root_job_id}"'
            ),
        }
        payload = _job_public_payload(representative.payload)
        details["payload_json"] = json.dumps(payload, indent=2, sort_keys=True)
        if representative.job_type == "harvest_range":
            details.update(
                {
                    "harvest_id": payload.get("harvest_id"),
                    "categories": payload.get("categories") or [],
                    "start_date": payload.get("start_date"),
                    "end_date": payload.get("end_date"),
                    "chunk_months": payload.get("chunk_months"),
                    "limit_per_window": payload.get("limit_per_window") or payload.get("limit_per_category"),
                }
            )
        elif representative.job_type == "harvest_top_cited":
            details.update(
                {
                    "min_citations": payload.get("min_citations"),
                    "max_papers": payload.get("max_papers"),
                    "chunk_size": payload.get("chunk_size"),
                    "harvest_id": payload.get("harvest_id"),
                }
            )
        elif representative.job_type == "harvest_venue":
            details.update(
                {
                    "venue": payload.get("venue"),
                    "year": payload.get("year"),
                    "max_papers": payload.get("max_papers"),
                    "chunk_size": payload.get("chunk_size"),
                    "harvest_id": payload.get("harvest_id"),
                }
            )
        elif representative.job_type == "sync_latest":
            details.update(
                {
                    "days": payload.get("days", self.settings.latest_sync_days),
                    "categories": payload.get("categories", self.settings.arxiv_categories),
                    "limit_per_category": payload.get(
                        "limit_per_category", self.settings.latest_sync_limit_per_category
                    ),
                }
            )
        elif representative.job_type == "index_paper":
            details.update(
                {
                    "arxiv_id": payload.get("arxiv_id"),
                    "force": bool(payload.get("force")),
                }
            )
        elif representative.job_type == "enrich_missing_metadata":
            details.update(
                {
                    "limit": payload.get("limit"),
                    "min_age_seconds": payload.get("min_age_seconds"),
                }
            )

        return AdminJobOverviewRow(
            job_id=representative.job_id,
            root_job_id=root_job_id,
            job_type=representative.job_type,
            status=base_status,
            display_status=display_status,
            payload=payload,
            error=latest_error,
            attempts=representative.attempts,
            max_attempts=representative.max_attempts,
            priority=representative.priority,
            created_at=earliest_created_at,
            updated_at=latest_updated_at,
            started_at=representative.started_at,
            finished_at=representative.finished_at,
            is_synthetic=False,
            tracking_available=tracking_available,
            indexed_paper_count=indexed_paper_count,
            tracked_paper_count=tracked_paper_count,
            paper_outcome_counts=paper_outcome_counts,
            latest_status_summary=_latest_status_summary(status_counts, total=len(jobs)),
            child_job_count=len(child_jobs),
            child_jobs=child_jobs,
            details=details,
        )

    def get_admin_jobs_overview(self, *, limit: int = 50) -> AdminJobsOverviewResponse:
        worker_summary = self._load_worker_summary()
        recent_jobs = self.repository.list_recent_jobs(limit=max(limit * 20, 250))
        grouped: dict[str, list[JobResponse]] = {}
        sync_jobs: list[JobResponse] = []

        for job in recent_jobs:
            if job.job_type == "sync_latest":
                sync_jobs.append(job)
                continue
            if job.job_type == "harvest_chunk" and job.root_job_id is None:
                continue
            root_job_id = job.root_job_id or job.job_id
            grouped.setdefault(root_job_id, []).append(job)

        now = utcnow()
        rows = [self._admin_row_from_jobs(jobs, worker_summary=worker_summary, now=now) for jobs in grouped.values()]

        scheduler_configured = _scheduler_latest_sync_configured(self.settings)
        if scheduler_configured or sync_jobs:
            latest_sync = sync_jobs[0] if sync_jobs else None
            sync_status_counts: dict[str, int] = {}
            for job in sync_jobs:
                sync_status_counts[job.status] = sync_status_counts.get(job.status, 0) + 1
            latest_sync_root_job_id = latest_sync.root_job_id or latest_sync.job_id if latest_sync is not None else None
            latest_sync_tracking_available = latest_sync is not None and _job_type_tracks_papers(latest_sync.job_type)
            latest_sync_indexed_paper_count: int | None = 0 if latest_sync_tracking_available else None
            latest_sync_tracked_paper_count: int | None = 0 if latest_sync_tracking_available else None
            latest_sync_paper_outcome_counts: dict[str, int] = {}
            if latest_sync_tracking_available and latest_sync_root_job_id is not None:
                tracked = self.repository.list_job_papers_for_root(latest_sync_root_job_id)
            else:
                tracked = []
            if latest_sync_tracking_available:
                latest_sync_paper_outcome_counts = _job_paper_outcome_counts(tracked)
                latest_sync_indexed_paper_count = latest_sync_paper_outcome_counts.get("indexed", 0)
                latest_sync_tracked_paper_count = sum(latest_sync_paper_outcome_counts.values())
            if latest_sync and latest_sync.status == "running":
                display_status = "stalled" if (
                    worker_summary.stalled or _job_activity_stalled(latest_sync.updated_at, now=now)
                ) else "running"
            elif latest_sync and latest_sync.status in {"dead", "failed"}:
                display_status = "error"
            elif latest_sync and latest_sync.status == "pending":
                display_status = "pending"
            else:
                display_status = "scheduled"
            if latest_sync is not None:
                next_expected_run_at = latest_sync.updated_at + timedelta(seconds=self.settings.sync_interval_seconds)
                latest_run_status = _admin_display_status(latest_sync.status)
                latest_run_updated_at = latest_sync.updated_at
                latest_run_job_id = latest_sync.job_id
            else:
                next_expected_run_at = None
                latest_run_status = None
                latest_run_updated_at = None
                latest_run_job_id = None
            rows.append(
                AdminJobOverviewRow(
                    job_id=_ADMIN_LATEST_SYNC_JOB_ID,
                    root_job_id=latest_sync_root_job_id or _ADMIN_LATEST_SYNC_JOB_ID,
                    job_type="latest_sync_schedule",
                    status="scheduled",
                    display_status=display_status,
                    payload={},
                    error=latest_sync.error if latest_sync is not None else None,
                    attempts=0,
                    max_attempts=0,
                    priority=0,
                    created_at=latest_sync.created_at if latest_sync is not None else now,
                    updated_at=latest_run_updated_at or now,
                    started_at=None,
                    finished_at=None,
                    is_synthetic=True,
                    tracking_available=latest_sync_tracking_available,
                    indexed_paper_count=latest_sync_indexed_paper_count,
                    tracked_paper_count=latest_sync_tracked_paper_count,
                    paper_outcome_counts=latest_sync_paper_outcome_counts,
                    latest_status_summary=(
                        _latest_status_summary(sync_status_counts, total=len(sync_jobs))
                        if sync_jobs
                        else "No latest sync run has been queued yet."
                    ),
                    child_job_count=0,
                    child_jobs=[],
                    details={
                        "scheduler_configured": scheduler_configured,
                        "scheduler_audience": self.settings.scheduler_audience,
                        "scheduler_service_account_email": self.settings.scheduler_service_account_email,
                        "sync_interval_seconds": self.settings.sync_interval_seconds,
                        "categories": self.settings.arxiv_categories,
                        "days": self.settings.latest_sync_days,
                        "limit_per_category": self.settings.latest_sync_limit_per_category,
                        "next_expected_run_at": next_expected_run_at,
                        "latest_run_status": latest_run_status,
                        "latest_run_updated_at": latest_run_updated_at,
                        "latest_run_job_id": latest_run_job_id,
                        "status_counts": sync_status_counts,
                        "root_job_id": latest_sync_root_job_id or _ADMIN_LATEST_SYNC_JOB_ID,
                        "job_ids": [job.job_id for job in sync_jobs],
                        "cloud_run_log_filter": 'jsonPayload.job_type="sync_latest" OR textPayload:"sync_latest"',
                    },
                )
            )

        rows.sort(key=lambda row: (row.updated_at, -_admin_status_sort_key(row.display_status)), reverse=True)
        return AdminJobsOverviewResponse(jobs=rows[:limit], worker=worker_summary)

    def get_admin_job_papers(self, root_job_id: str, *, offset: int = 0, limit: int = 100) -> AdminJobPapersResponse:
        jobs = self.repository.list_jobs_for_root(root_job_id)
        if not jobs:
            raise HTTPException(status_code=404, detail=f"Job run {root_job_id} not found")
        normalized_offset = max(0, int(offset))
        normalized_limit = max(1, int(limit))

        records = self.repository.list_job_papers_for_root(root_job_id)
        grouped = _group_job_paper_records(records)

        ordered_records = sorted(grouped.values(), key=lambda item: item.updated_at, reverse=True)
        total_count = len(ordered_records)
        page_records = ordered_records[normalized_offset : normalized_offset + normalized_limit]

        papers = []
        for record in page_records:
            paper = self.repository.get_paper(record.paper_id)
            papers.append(
                AdminJobPaperResponse(
                    root_job_id=root_job_id,
                    job_id=record.job_id,
                    paper_id=record.paper_id,
                    arxiv_id=record.arxiv_id or (paper.arxiv_id if paper is not None else None),
                    title=record.title or (paper.title if paper is not None else None),
                    outcome=record.outcome,
                    skip_reason=record.skip_reason,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                    paper=paper,
                )
            )
        return AdminJobPapersResponse(
            root_job_id=root_job_id,
            total_count=total_count,
            offset=normalized_offset,
            limit=normalized_limit,
            has_more=normalized_offset + len(page_records) < total_count,
            outcome_counts=_job_paper_outcome_counts(records),
            skipped_reason_counts=_job_paper_skipped_reason_counts(records),
            papers=papers,
        )

    def get_admin_paper_detail(self, paper_id: str) -> AdminPaperDetailResponse:
        paper = self.repository.get_paper(paper_id)
        if paper is None:
            raise HTTPException(status_code=404, detail=f"Paper {paper_id} not found")

        jobs: list[AdminPaperJobTouchResponse] = []
        for record in self.repository.list_job_papers_for_paper(paper.paper_id):
            job = self.repository.get_job(record.job_id)
            if job is None:
                jobs.append(
                    AdminPaperJobTouchResponse(
                        root_job_id=record.job_id,
                        job_id=record.job_id,
                        job_type="unknown",
                        status="unknown",
                        display_status="unknown",
                        payload={},
                        error=None,
                        outcome=record.outcome,
                        skip_reason=record.skip_reason,
                        attempts=0,
                        max_attempts=0,
                        priority=0,
                        record_created_at=record.created_at,
                        record_updated_at=record.updated_at,
                        created_at=record.created_at,
                        updated_at=record.updated_at,
                    )
                )
                continue
            jobs.append(
                AdminPaperJobTouchResponse(
                    root_job_id=job.root_job_id or job.job_id,
                    job_id=job.job_id,
                    job_type=job.job_type,
                    status=job.status,
                    display_status=_admin_display_status(job.status),
                    payload=_job_public_payload(job.payload),
                    error=job.error,
                    outcome=record.outcome,
                    skip_reason=record.skip_reason,
                    attempts=job.attempts,
                    max_attempts=job.max_attempts,
                    priority=job.priority,
                    record_created_at=record.created_at,
                    record_updated_at=record.updated_at,
                    created_at=job.created_at,
                    updated_at=job.updated_at,
                    started_at=job.started_at,
                    finished_at=job.finished_at,
                )
            )

        files: list[FileObject] = []
        if paper.index_status == "indexed":
            try:
                files = self.list_files(paper.paper_id).files
            except HTTPException:
                files = []

        code_files = self.list_paper_code_files(paper.paper_id)
        return AdminPaperDetailResponse(paper=paper, jobs=jobs, files=files, code_files=code_files)

    def list_papers(
        self,
        *,
        query: str | None = None,
        index_status: str | None = None,
        limit: int = 50,
    ) -> list[PaperResponse]:
        return self.repository.list_papers(query=query, index_status=index_status or "indexed", limit=limit)

    def sync_latest_papers(
        self,
        *,
        days: int,
        categories: list[str],
        limit_per_category: int,
        tracking_job: JobResponse | None = None,
    ) -> list[PaperResponse]:
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
            self._raise_if_current_job_canceled()
            papers = sources.fetch_recent(category, days=days, max_results=limit_per_category)
            sources.enrich_with_semantic_scholar(papers)
            for paper in papers:
                self._raise_if_current_job_canceled()
                if paper.arxiv_id in seen:
                    continue
                seen.add(paper.arxiv_id)
                indexed.append(
                    self._call_index_arxiv_paper(
                        paper.arxiv_id,
                        paper=paper,
                        force=False,
                        tracking_job=tracking_job,
                    )
                )
        logger.info(
            "Completed latest sync",
            extra={
                "event": "sync_latest.completed",
                "indexed_count": len(indexed),
                "unique_paper_count": len(seen),
            },
        )
        return indexed

    def index_arxiv_paper(
        self,
        arxiv_id: str,
        *,
        paper: PaperMetadata | None = None,
        force: bool = False,
        tracking_job: JobResponse | None = None,
    ) -> PaperResponse:
        self._raise_if_current_job_canceled()
        existing = self.repository.get_paper_by_arxiv_id(arxiv_id)
        if existing and existing.index_status == "indexed" and not force:
            if tracking_job is not None:
                self.repository.record_job_paper(
                    tracking_job.job_id,
                    existing.paper_id,
                    outcome="skipped",
                    arxiv_id=existing.arxiv_id,
                    title=existing.title,
                    skip_reason="already_indexed",
                )
            logger.info(
                "Skipped already indexed paper",
                extra={
                    "event": "index.skipped",
                    "arxiv_id": arxiv_id,
                    "paper_id": existing.paper_id,
                    "skip_reason": "already_indexed",
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
        self._raise_if_current_job_canceled()
        if metadata is None:
            # Worker context: a missing arxiv id will never resolve, so this
            # must dead-letter rather than spin through the retry ladder.
            raise PermanentError(f"Paper {arxiv_id} not found on arXiv")
        # Inline enrichment: single-shot (max_retries=0). A 429 or transient
        # failure here leaves citation_count=0 rather than blocking ingestion;
        # the enrich_missing_metadata backfill job will pick this paper up
        # later with aggressive retry.
        enrich_outcome = sources.enrich_with_semantic_scholar([metadata], max_retries=0)
        self._raise_if_current_job_canceled()
        # Snapshot an existing admin pin BEFORE the intermediate upsert so
        # it flows through every subsequent upsert on this metadata object.
        # A later ``get_paper_by_arxiv_id`` can't recover the pin because
        # this upsert would have wiped it onto an empty code_url (#46).
        existing_for_admin = self.repository.get_paper_by_arxiv_id(metadata.arxiv_id)
        if (
            existing_for_admin is not None
            and existing_for_admin.code_url_source == "admin"
            and existing_for_admin.code_url
        ):
            metadata.code_url = existing_for_admin.code_url
            metadata.code_url_source = "admin"
        metadata.index_status = "indexing"
        stored = self.repository.upsert_paper(metadata, search_document=metadata.abstract)
        try:
            # #39 PR C: persist S2 author rows so the top-author discovery
            # aggregation has data. Skipped silently if enrichment was rate-
            # limited — the backfill CLI fills in the gap. ``enrich_outcome``
            # can be None in test contexts that stub the source-layer call.
            if enrich_outcome is not None:
                author_rows = enrich_outcome.authors_by_arxiv.get(metadata.arxiv_id) or []
                if author_rows:
                    self.repository.replace_paper_authors(stored.paper_id, authors=author_rows)
            extraction = index_paper_artifacts(metadata)
            self._raise_if_current_job_canceled()

            metadata.source_type = extraction.source_type
            metadata.index_status = "indexed"
            metadata.indexed_at = utcnow()
            metadata.summary_short = build_paper_summary(metadata, extraction, self.settings)

            # Populate ``code_url`` + ``code_url_source`` with the 3-layer
            # decision from #46. Priority order:
            #   1. admin (already on ``metadata`` from a ``set_admin_code_url``
            #      call or persisted row) — sticky, never overwritten here.
            #   2. Hugging Face paper-page ``githubRepo`` crosslink, when
            #      ``fetch_code_crosslinks_at_index`` is enabled.
            #   3. In-text ranker over all extracted GitHub candidates.
            # Rejected candidates are always stored in ``code_url_candidates``
            # so admins + future agent-side logic can revisit them without
            # re-scanning paper text.
            candidates = extract_github_candidates(
                metadata.abstract or "", extraction.text_markdown or ""
            )
            metadata.code_url_candidates = [c.url for c in candidates] or None
            existing = self.repository.get_paper_by_arxiv_id(metadata.arxiv_id)
            if existing is not None and existing.code_url_source == "admin" and existing.code_url:
                # Admin pin survives re-index regardless of what's in the paper.
                metadata.code_url = existing.code_url
                metadata.code_url_source = "admin"
            else:
                crosslink_url: str | None = None
                if self.settings.fetch_code_crosslinks_at_index:
                    crosslink_url = sources.fetch_hf_paper_repo(metadata.arxiv_id)
                if crosslink_url:
                    metadata.code_url = crosslink_url
                    metadata.code_url_source = "hf_papers"
                else:
                    winner = rank_github_candidates(
                        candidates,
                        abstract=metadata.abstract or "",
                        body=extraction.text_markdown or "",
                        authors=metadata.authors or "",
                    )
                    if winner is not None:
                        metadata.code_url = winner.url
                        metadata.code_url_source = "extracted"
                    else:
                        # Ambiguous / no candidates: decline rather than best-guess
                        # so the code-ingest pipeline (#41) does not auto-enqueue
                        # an ingest job against a repo we are not confident about.
                        metadata.code_url = None
                        metadata.code_url_source = None

                    # GitHub README verifier (#46 follow-up). This remains an
                    # optional precision filter on top of the in-text ranker:
                    # fetch the candidate repo's README and confirm it
                    # references the paper. Off by default — see config docs
                    # for the precision/recall tradeoff measured on the
                    # 30-paper benchmark.
                    if (
                        winner is not None
                        and self.settings.verify_code_url_via_github_readme
                    ):
                        verified = sources.verify_repo_owns_paper(
                            winner.owner,
                            winner.repo,
                            metadata.arxiv_id,
                            title=metadata.title or None,
                            token=self.settings.github_token,
                        )
                        if not verified:
                            metadata.code_url = None
                            metadata.code_url_source = None

            search_document_parts = [metadata.title, metadata.tldr, metadata.summary_short, extraction.text_markdown]
            search_document = "\n\n".join(part for part in search_document_parts if part)

            # Paper-level embedding uses the (title, body) pair contract that
            # SPECTER2 is trained on. summary_short is intentionally *not* fed
            # into this vector — it is heuristic text with a noisy signal, and
            # SPECTER2's training distribution never saw it. It stays in
            # search_document for lexical scoring and lives on chunks.
            paper_title = metadata.title or ""
            paper_body = metadata.tldr or metadata.abstract or ""
            paper_embedding = (
                self.embeddings.encode_paper(paper_title, paper_body) if (paper_title or paper_body) else None
            )

            stored = self.repository.upsert_paper(
                metadata,
                search_document=search_document,
                embedding=paper_embedding,
            )

            prefix = f"papers/{stored.paper_id}"
            self.object_store.delete_prefix(prefix)
            self.object_store.put_text(
                f"{prefix}/meta.json",
                json.dumps(metadata.model_dump(mode="json"), indent=2),
                content_type="application/json",
            )
            self.object_store.put_text(
                f"{prefix}/text/full.md",
                extraction.text_markdown,
                content_type="text/markdown",
            )

            text_chunks: list[dict[str, Any]] = [
                {
                    "path": "text/full.md",
                    "chunk_kind": "full_text",
                    "content": extraction.text_markdown,
                    "chunk_index": 0,
                    "section_depth": 0,
                    "section_order": 0,
                }
            ]

            for section_order, section in enumerate(extraction.sections):
                self._raise_if_current_job_canceled()
                section_path = f"{prefix}/sections/{section.slug}.md"
                self.object_store.put_text(section_path, section.markdown, content_type="text/markdown")
                section_depth = _section_depth_for(section)
                for chunk_index, sub_content in enumerate(
                    _stride_chunks(
                        section.markdown,
                        max_chars=self.settings.chunk_max_chars,
                        overlap=self.settings.chunk_overlap_chars,
                    )
                ):
                    text_chunks.append(
                        {
                            "path": f"sections/{section.slug}.md",
                            "chunk_kind": "section",
                            "content": sub_content,
                            "chunk_index": chunk_index,
                            "section_depth": section_depth,
                            "section_order": section_order,
                        }
                    )

            if extraction.figures:
                self.object_store.put_text(
                    f"{prefix}/figures/index.json",
                    json.dumps(figure_index_payload(extraction.figures), indent=2),
                    content_type="application/json",
                )
                for figure_order, figure in enumerate(extraction.figures):
                    self._raise_if_current_job_canceled()
                    caption_path = f"{prefix}/figures/{figure.figure_id}/caption.md"
                    self.object_store.put_text(caption_path, figure.caption, content_type="text/markdown")
                    text_chunks.append(
                        {
                            "path": f"figures/{figure.figure_id}/caption.md",
                            "chunk_kind": "figure_caption",
                            "content": figure.caption,
                            "chunk_index": 0,
                            "section_depth": 1,
                            "section_order": figure_order,
                        }
                    )
                    if figure.image_bytes is not None:
                        image_path = f"{prefix}/figures/{figure.figure_id}/image.{figure.file_extension}"
                        self.object_store.put_bytes(
                            image_path,
                            figure.image_bytes,
                            content_type=figure.content_type,
                        )

            if extraction.tables:
                self.object_store.put_text(
                    f"{prefix}/tables/index.json",
                    json.dumps(table_index_payload(extraction.tables), indent=2),
                    content_type="application/json",
                )
                for table_order, table in enumerate(extraction.tables):
                    self._raise_if_current_job_canceled()
                    json_path = f"{prefix}/tables/{table.table_id}.json"
                    csv_path = f"{prefix}/tables/{table.table_id}.csv"
                    self.object_store.put_bytes(
                        json_path,
                        table_json_bytes(table),
                        content_type="application/json",
                    )
                    self.object_store.put_bytes(csv_path, table_csv_bytes(table), content_type="text/csv")
                    table_text = _table_to_indexable_text(table)
                    if table_text:
                        text_chunks.append(
                            {
                                "path": f"tables/{table.table_id}.json",
                                "chunk_kind": "table",
                                "content": table_text,
                                "chunk_index": 0,
                                "section_depth": 1,
                                "section_order": table_order,
                            }
                        )

            # Truncate per-kind before embedding — full_text stays large so that
            # vector search can still recover it as a coarse fallback, but
            # sections/tables stay focused.
            chunk_texts: list[str] = []
            for chunk in text_chunks:
                self._raise_if_current_job_canceled()
                cap = (
                    4000
                    if chunk["chunk_kind"] == "full_text"
                    else self.settings.chunk_max_chars + self.settings.chunk_overlap_chars
                )
                chunk_texts.append((chunk["content"] or "")[:cap])
            chunk_embeddings = self.embeddings.encode_batch(chunk_texts)
            self._raise_if_current_job_canceled()
            if chunk_embeddings is not None and len(chunk_embeddings) == len(text_chunks):
                for chunk, vector in zip(text_chunks, chunk_embeddings):
                    chunk["embedding"] = vector

            for artifact in extraction.raw_artifacts:
                self._raise_if_current_job_canceled()
                self.object_store.put_bytes(
                    f"{prefix}/{artifact.path}",
                    artifact.data,
                    content_type=artifact.content_type,
                )

            manifest = build_manifest(stored.paper_id, metadata, extraction)
            self.object_store.put_text(
                f"{prefix}/manifest.json",
                json.dumps(manifest, indent=2),
                content_type="application/json",
            )

            self.repository.replace_text_chunks(stored.paper_id, text_chunks)

            if self.settings.fetch_references_at_index:
                try:
                    refs = sources.fetch_references(metadata.arxiv_id, metadata.semantic_scholar_id)
                    if refs:
                        self.repository.replace_paper_refs(stored.paper_id, refs)
                except Exception as exc:
                    logger.warning(
                        "Failed to fetch references",
                        extra={"event": "index.refs_failed", "arxiv_id": arxiv_id, "error": str(exc)},
                    )

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
            if tracking_job is not None:
                self.repository.record_job_paper(
                    tracking_job.job_id,
                    stored.paper_id,
                    outcome="indexed",
                    arxiv_id=stored.arxiv_id,
                    title=stored.title,
                )
            # Paper-code ingestion (#5): auto-enqueue ingest_repo when the paper
            # ships a GitHub code_url. We enqueue unconditionally — the worker
            # itself short-circuits on matching resolved_sha, so repeated
            # indexing of the same commit is cheap. force=True here mirrors the
            # caller's intent to refresh all artifacts.
            if stored.code_url and code_ingest.parse_github_url(stored.code_url) is not None:
                prev_url = (stored.code_manifest or {}).get("code_url")
                url_changed = prev_url is not None and prev_url != stored.code_url
                if force or url_changed or stored.code_index_status in {"none", "stale"}:
                    self.enqueue_ingest_repo_job(
                        stored.paper_id,
                        force=force or url_changed,
                        root_job_id=(
                            (tracking_job.root_job_id or tracking_job.job_id)
                            if tracking_job is not None
                            else None
                        ),
                    )
            return stored
        except Exception:
            if tracking_job is not None:
                self.repository.record_job_paper(
                    tracking_job.job_id,
                    stored.paper_id,
                    outcome="failed",
                    arxiv_id=stored.arxiv_id,
                    title=stored.title,
                )
            raise

    def search(self, request: SearchRequest, *, user_id: str | None = None) -> SearchResponse:
        import time as _time
        _search_started_at = _time.monotonic()
        k = self.settings.rrf_k
        pool_size = max(request.limit * 4, 50)
        query_embedding = self.embeddings.encode(request.query)

        local_results = self.repository.search_local(
            request.query,
            limit=pool_size,
            query_embedding=query_embedding,
            rrf_k=k,
            citation_boost_scale=self.settings.citation_boost_scale,
            citation_boost_max=self.settings.citation_boost_max,
            recency_boost_max=self.settings.recency_boost_max,
            recency_half_life_years=self.settings.recency_half_life_years,
        )

        candidates: dict[str, SearchPaperResult] = {}
        rrf: dict[str, float] = {}

        for rank, result in enumerate(local_results):
            pid = result.paper_id
            candidates[pid] = result
            rrf[pid] = rrf.get(pid, 0.0) + 1.0 / (k + rank)

        from math import log10

        fused = sorted(rrf.items(), key=lambda item: item[1], reverse=True)
        fused_papers = [candidates[pid] for pid, _ in fused if pid in candidates]

        if request.rerank and self.reranker.enabled():
            rerank_head = fused_papers[: self.reranker.top_k]
            rerank_tail = fused_papers[self.reranker.top_k :]
            best_chunks = self.repository.best_chunk_for_papers(
                request.query,
                [p.paper_id for p in rerank_head],
                query_embedding=query_embedding,
            )
            doc_texts = [
                " ".join(
                    part
                    for part in (
                        p.title,
                        best_chunks.get(p.paper_id) or p.tldr or p.summary_short or p.abstract,
                    )
                    if part
                )[:2000]
                for p in rerank_head
            ]
            rerank_scores = self.reranker.score(request.query, doc_texts)
            if rerank_scores is not None and len(rerank_scores) == len(rerank_head):
                nudge = self.settings.rerank_citation_nudge
                adjusted = [
                    (
                        paper,
                        float(score) + nudge * log10(1 + (paper.citation_count or 0)),
                    )
                    for paper, score in zip(rerank_head, rerank_scores)
                ]
                head_ranked = sorted(adjusted, key=lambda pair: pair[1], reverse=True)
                for paper, score in head_ranked:
                    paper.score = score
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

        query_id: str | None = None
        if request.log_query:
            query_id = new_id("qry")
            latency_ms = int((_time.monotonic() - _search_started_at) * 1000)
            try:
                self.repository.record_search_log(
                    query_id=query_id,
                    user_id=user_id,
                    query_text=request.query,
                    request_rerank=bool(request.rerank),
                    top_paper_ids=[p.paper_id for p in ordered],
                    result_set_id=result_set.result_set_id,
                    latency_ms=latency_ms,
                )
            except Exception as exc:
                # Never fail the search on a logging failure — ranking quality
                # matters more than observability in the hot path. Warn so the
                # operator notices a DB-side regression.
                logger.warning(
                    "search_logs write failed",
                    extra={"event": "search_log.write_failed", "error": str(exc)},
                )
                query_id = None

        return SearchResponse(
            result_set_id=result_set.result_set_id,
            query=request.query,
            papers=ordered,
            expires_at=result_set.expires_at,
            query_id=query_id,
        )

    def record_search_click(self, query_id: str, paper_id: str) -> bool:
        """Attach a clicked_paper_id to a previously-logged search event.
        Returns False when the query_id is unknown (caller translates to 404)."""
        return self.repository.record_search_click(query_id, paper_id)

    def get_results(self, result_set_id: str) -> ResultSetResponse:
        result_set = self.repository.get_result_set(result_set_id)
        if result_set is None:
            raise HTTPException(status_code=404, detail=f"Result set {result_set_id} not found")
        indexed_papers: list[SearchPaperResult] = []
        for result in result_set.papers:
            stored = self.repository.get_paper(result.paper_id)
            if stored is None or stored.index_status != "indexed":
                continue
            indexed_papers.append(result.model_copy(update={"index_status": "indexed"}))
        return result_set.model_copy(update={"papers": indexed_papers})

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

    def _trending_preview_image_url(self, paper_id: str) -> str | None:
        preview_path = f"papers/{paper_id}/preview/first-page-top.webp"
        if not self.object_store.exists(preview_path):
            return None
        return self._browser_app_url(f"/v1/browser/trending/papers/{quote(paper_id, safe='')}/preview/content")

    def _build_paper_comment_tree(self, records: list[PaperCommentRecord]) -> list[PaperCommentResponse]:
        nodes: dict[str, PaperCommentResponse] = {}
        roots: list[PaperCommentResponse] = []
        for record in records:
            nodes[record.comment_id] = PaperCommentResponse(
                comment_id=record.comment_id,
                paper_id=record.paper_id,
                parent_comment_id=record.parent_comment_id,
                author=PaperCommentAuthor(
                    user_id=record.user_id,
                    display_name=record.author_display_name,
                ),
                body=record.body,
                created_at=record.created_at,
                updated_at=record.updated_at,
                replies=[],
            )
        for record in records:
            node = nodes[record.comment_id]
            if record.parent_comment_id and record.parent_comment_id in nodes:
                nodes[record.parent_comment_id].replies.append(node)
            else:
                roots.append(node)
        return roots

    def list_browser_trending_papers(
        self,
        *,
        date: str | None = None,
        sort: str,
        page: int = 1,
        page_size: int = 20,
        viewer: BrowserUser | None = None,
    ) -> TrendingFeedResponse:
        if date is not None:
            _validate_date(date, name="date")
        if sort not in {"default", "upvotes"}:
            raise HTTPException(status_code=400, detail=f"Unsupported trending sort {sort!r}")
        if page < 1:
            raise HTTPException(status_code=400, detail="Trending page must be at least 1")
        if page_size < 1 or page_size > 50:
            raise HTTPException(status_code=400, detail="Trending page size must be between 1 and 50")
        papers = self.repository.list_trending_papers(
            sort=sort,
            viewer_user_id=viewer.user_id if viewer else None,
            limit=page_size + 1,
            offset=(page - 1) * page_size,
        )
        visible_papers = papers[:page_size]
        return TrendingFeedResponse(
            date=date or utcnow().date().isoformat(),
            sort=sort,  # type: ignore[arg-type]
            page=page,
            page_size=page_size,
            has_next=len(papers) > page_size,
            has_previous=page > 1,
            papers=[
                paper.model_copy(update={"image_url": self._trending_preview_image_url(paper.paper_id)})
                for paper in visible_papers
            ],
        )

    def get_browser_trending_paper(
        self,
        paper_id: str,
        *,
        viewer: BrowserUser | None = None,
    ) -> TrendingPaperDetailResponse:
        detail = self.repository.get_trending_paper(
            paper_id,
            viewer_user_id=viewer.user_id if viewer else None,
        )
        if detail is None:
            raise HTTPException(status_code=404, detail=f"Trending paper {paper_id} not found")
        comments = self.repository.list_paper_comment_records(paper_id)
        return detail.model_copy(
            update={
                "image_url": self._trending_preview_image_url(paper_id),
                "comments": self._build_paper_comment_tree(comments),
            }
        )

    def get_browser_paper_discussion(
        self,
        paper_id: str,
        *,
        viewer: BrowserUser | None = None,
    ) -> PaperDiscussionResponse:
        summary = self.repository.get_paper_vote_summary(
            paper_id,
            viewer_user_id=viewer.user_id if viewer else None,
        )
        if summary is None:
            raise HTTPException(status_code=404, detail=f"Paper {paper_id} not found")
        comments = self.repository.list_paper_comment_records(paper_id)
        return PaperDiscussionResponse(
            paper_id=paper_id,
            upvote_count=summary.upvote_count,
            viewer_has_upvoted=summary.viewer_has_upvoted,
            comments=self._build_paper_comment_tree(comments),
        )

    def get_browser_trending_paper_preview_content(self, paper_id: str) -> tuple[bytes, str]:
        detail = self.repository.get_trending_paper(paper_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"Trending paper {paper_id} not found")
        preview_path = f"papers/{paper_id}/preview/first-page-top.webp"
        if not self.object_store.exists(preview_path):
            raise HTTPException(status_code=404, detail=f"Preview for {paper_id} not found")
        return self.object_store.get_bytes(preview_path), "image/webp"

    def set_browser_trending_paper_upvote(self, paper_id: str, *, user: BrowserUser, upvoted: bool) -> PaperVoteSummary:
        summary = self.repository.set_paper_upvote(paper_id=paper_id, user_id=user.user_id, upvoted=upvoted)
        if summary is None:
            raise HTTPException(status_code=404, detail=f"Trending paper {paper_id} not found")
        return summary

    def set_browser_paper_upvote(self, paper_id: str, *, user: BrowserUser, upvoted: bool) -> PaperVoteSummary:
        summary = self.repository.set_paper_upvote(paper_id=paper_id, user_id=user.user_id, upvoted=upvoted)
        if summary is None:
            raise HTTPException(status_code=404, detail=f"Paper {paper_id} not found")
        return summary

    def create_browser_trending_paper_comment(
        self,
        paper_id: str,
        *,
        user: BrowserUser,
        request: CreatePaperCommentRequest,
    ) -> PaperCommentsResponse:
        detail = self.repository.get_trending_paper(paper_id, viewer_user_id=user.user_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"Trending paper {paper_id} not found")
        body = request.body.strip()
        if not body:
            raise HTTPException(status_code=400, detail="Comment body must not be empty")
        if request.parent_comment_id:
            parent = self.repository.get_paper_comment(request.parent_comment_id)
            if parent is None or parent.paper_id != paper_id:
                raise HTTPException(status_code=400, detail="Reply target is invalid for this paper")
        created = self.repository.create_paper_comment(
            paper_id=paper_id,
            user_id=user.user_id,
            body=body,
            parent_comment_id=request.parent_comment_id,
        )
        if created is None:
            raise HTTPException(status_code=404, detail=f"Trending paper {paper_id} not found")
        comments = self.repository.list_paper_comment_records(paper_id)
        return PaperCommentsResponse(
            paper_id=paper_id,
            comments=self._build_paper_comment_tree(comments),
        )

    def create_browser_paper_comment(
        self,
        paper_id: str,
        *,
        user: BrowserUser,
        request: CreatePaperCommentRequest,
    ) -> PaperCommentsResponse:
        summary = self.repository.get_paper_vote_summary(paper_id, viewer_user_id=user.user_id)
        if summary is None:
            raise HTTPException(status_code=404, detail=f"Paper {paper_id} not found")
        body = request.body.strip()
        if not body:
            raise HTTPException(status_code=400, detail="Comment body must not be empty")
        if request.parent_comment_id:
            parent = self.repository.get_paper_comment(request.parent_comment_id)
            if parent is None or parent.paper_id != paper_id:
                raise HTTPException(status_code=400, detail="Reply target is invalid for this paper")
        created = self.repository.create_paper_comment(
            paper_id=paper_id,
            user_id=user.user_id,
            body=body,
            parent_comment_id=request.parent_comment_id,
        )
        if created is None:
            raise HTTPException(status_code=404, detail=f"Paper {paper_id} not found")
        comments = self.repository.list_paper_comment_records(paper_id)
        return PaperCommentsResponse(
            paper_id=paper_id,
            comments=self._build_paper_comment_tree(comments),
        )

    def get_paper(self, paper_id: str) -> PaperResponse:
        paper = self.repository.get_paper(paper_id)
        if paper is None:
            raise HTTPException(status_code=404, detail=f"Paper {paper_id} not found")
        return paper

    def get_indexed_paper(self, paper_id: str) -> PaperResponse:
        paper = self.get_paper(paper_id)
        if paper.index_status != "indexed":
            raise HTTPException(status_code=404, detail=f"Paper {paper_id} is not indexed")
        return paper

    def get_manifest(self, paper_id: str) -> ManifestResponse:
        path = f"papers/{paper_id}/manifest.json"
        if not self.object_store.exists(path):
            raise HTTPException(status_code=404, detail=f"Manifest for {paper_id} not found")
        return ManifestResponse(paper_id=paper_id, manifest=json.loads(self.object_store.get_text(path)))

    def _browser_app_url(self, path: str) -> str:
        base = self.settings.chat_public_url.rstrip("/")
        if not base:
            return path
        return f"{base}{path}"

    def _project_presentation_share_url(self, share_token: str) -> str:
        return self._browser_app_url(f"/?presentation={quote(share_token)}")

    def _paper_artifact_location(self, paper_id: str, path: str) -> tuple[str, str]:
        self.get_indexed_paper(paper_id)
        safe_path = _normalize_paper_artifact_path(path)
        full_path = f"papers/{paper_id}/{safe_path}"
        if not self.object_store.exists(full_path):
            raise HTTPException(status_code=404, detail=f"File {safe_path} not found for {paper_id}")
        return safe_path, full_path

    def _paper_artifact_content_type(self, paper_id: str, safe_path: str) -> str | None:
        files = {item.path: item for item in self.list_files(paper_id).files}
        content_type = files.get(safe_path, FileObject(path=safe_path, size=0)).content_type
        if content_type:
            return content_type
        return mimetypes.guess_type(safe_path)[0]

    def _preview_object_path(self, paper_id: str) -> str:
        self.get_indexed_paper(paper_id)
        preview_path = f"papers/{paper_id}/preview/first-page-top.webp"
        if not self.object_store.exists(preview_path):
            raise HTTPException(status_code=404, detail=f"Preview not found for {paper_id}")
        return preview_path

    def list_files(self, paper_id: str) -> FileListResponse:
        self.get_indexed_paper(paper_id)
        prefix = f"papers/{paper_id}/"
        objects = self.object_store.list_prefix(prefix)
        files = [
            FileObject(path=item.path[len(prefix) :], size=item.size, content_type=item.content_type)
            for item in objects
        ]
        return FileListResponse(paper_id=paper_id, files=files)

    def read_file(self, paper_id: str, path: str) -> FileContentResponse:
        safe_path, full_path = self._paper_artifact_location(paper_id, path)
        try:
            content = self.object_store.get_text(full_path)
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=415, detail=f"File {safe_path} is not a UTF-8 text artifact") from exc
        return FileContentResponse(paper_id=paper_id, path=safe_path, content=content)

    def get_paper_asset(self, paper_id: str, path: str) -> PaperAssetResponse:
        safe_path, full_path = self._paper_artifact_location(paper_id, path)
        if self.settings.storage_backend in {"local", "memory"}:
            url = self._browser_app_url(
                "/v1/browser/admin/papers/"
                f"{quote(paper_id, safe='')}/asset-content/{quote(safe_path, safe='/')}"
            )
        else:
            url = self.object_store.presign_get(full_path, expires_in=self.settings.presign_ttl_seconds)
            if not url:
                raise HTTPException(status_code=404, detail=f"File {safe_path} is not available for {paper_id}")
        content_type = self._paper_artifact_content_type(paper_id, safe_path)
        return PaperAssetResponse(
            paper_id=paper_id,
            path=safe_path,
            url=url,
            content_type=content_type,
        )

    def get_paper_asset_content(self, paper_id: str, path: str) -> tuple[bytes, str | None]:
        safe_path, full_path = self._paper_artifact_location(paper_id, path)
        return self.object_store.get_bytes(full_path), self._paper_artifact_content_type(paper_id, safe_path)

    def get_paper_preview(self, paper_id: str) -> PaperPreviewResponse:
        """Return a short-lived signed URL for the paper's first-page preview.

        Fixed-function: the object path is resolved server-side from ``paper_id`` only.
        Raises 404 when the paper or the preview artifact is missing.
        """
        preview_path = self._preview_object_path(paper_id)
        if self.settings.storage_backend in {"local", "memory"}:
            image_url = self._browser_app_url(
                f"/v1/browser/papers/{quote(paper_id, safe='')}/preview/content"
            )
        else:
            image_url = self.object_store.presign_get(
                preview_path,
                expires_in=self.settings.presign_ttl_seconds,
            )
            if not image_url:
                raise HTTPException(status_code=404, detail=f"Preview not available for {paper_id}")
        return PaperPreviewResponse(paper_id=paper_id, image_url=image_url, content_type="image/webp")

    def get_paper_preview_content(self, paper_id: str) -> tuple[bytes, str]:
        preview_path = self._preview_object_path(paper_id)
        return self.object_store.get_bytes(preview_path), "image/webp"

    # Extension → content-type for the figure-serving probe. Order is
    # render-cheap-first so when a figure was stored under multiple
    # representations (rare, but the LaTeX path has produced both pdf
    # raw bytes and a png rasterization for the same figure under early
    # ingest paths) the browser-renderable one wins. eps/pdf cover the
    # raw-bytes fallback the LaTeX rasterizer takes when Ghostscript /
    # pypdfium2 fail (#48); without their MIME types here, those figures
    # came back as ``image_url=None``.
    _FIGURE_EXTENSION_CONTENT_TYPES: tuple[tuple[str, str], ...] = (
        ("png", "image/png"),
        ("jpg", "image/jpeg"),
        ("jpeg", "image/jpeg"),
        ("svg", "image/svg+xml"),
        ("gif", "image/gif"),
        ("webp", "image/webp"),
        ("pdf", "application/pdf"),
        ("eps", "application/postscript"),
        ("bin", "application/octet-stream"),
    )

    def get_figure(self, paper_id: str, figure_id: str) -> FigureResponse:
        self.get_indexed_paper(paper_id)
        caption_path = f"papers/{paper_id}/figures/{figure_id}/caption.md"
        if not self.object_store.exists(caption_path):
            raise HTTPException(status_code=404, detail=f"Figure {figure_id} not found for {paper_id}")
        image_url = None
        content_type = None
        for extension, mime in self._FIGURE_EXTENSION_CONTENT_TYPES:
            image_path = f"papers/{paper_id}/figures/{figure_id}/image.{extension}"
            if self.object_store.exists(image_path):
                image_url = self.object_store.presign_get(image_path, expires_in=self.settings.presign_ttl_seconds)
                content_type = mime
                break
        return FigureResponse(
            paper_id=paper_id,
            figure_id=figure_id,
            caption=self.object_store.get_text(caption_path),
            image_url=image_url,
            content_type=content_type,
        )

    def get_table(self, paper_id: str, table_id: str) -> TableResponse:
        self.get_indexed_paper(paper_id)
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

    def get_paper_references(self, paper_id: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        paper = self.get_indexed_paper(paper_id)
        refs = self.repository.get_paper_refs(paper_id)
        if refs and not refresh:
            return refs
        fetched = sources.fetch_references(paper.arxiv_id, paper.semantic_scholar_id)
        if fetched:
            self.repository.replace_paper_refs(paper_id, fetched)
            return self.repository.get_paper_refs(paper_id)
        return refs

    def get_related_papers(self, paper_id: str) -> list[SearchPaperResult]:
        paper = self.get_indexed_paper(paper_id)
        related = sources.get_related_papers(paper.arxiv_id, paper.semantic_scholar_id)
        papers: list[SearchPaperResult] = []
        for item in related:
            stored = self.repository.get_paper_by_arxiv_id(item.arxiv_id)
            if stored is None or stored.index_status != "indexed":
                continue
            papers.append(SearchPaperResult(**stored.model_dump(), source="related"))
        return papers

    def get_job(self, job_id: str) -> JobResponse:
        job = self.repository.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        return job

    def cleanup_expired_results(self) -> None:
        self.repository.cleanup_expired_results()

    def suggest_papers(self, q: str, *, limit: int = 10) -> list[dict[str, Any]]:
        return self.repository.suggest_papers(q, limit=limit)

    def suggest_fallback_papers(self, *, limit: int = 6) -> list[dict[str, Any]]:
        return self.repository.suggest_fallback_papers(limit=limit)

    def get_stats(self) -> dict[str, Any]:
        return self.repository.stats()

    def expand_query(self, query: str, *, variants: int | None = None) -> list[str]:
        from .summaries import expand_query_with_gemini

        want = variants if variants is not None else self.settings.query_expansion_variants
        expanded = expand_query_with_gemini(query, self.settings, variants=want)
        if not expanded:
            return [query]
        return expanded

    def probe_integrations(self) -> IntegrationProbeResponse:
        checked_at = utcnow()
        database_backend = "memory" if self.settings.database_url.startswith("memory://") else "sqlalchemy"
        database_target = (
            "memory://"
            if database_backend == "memory"
            else self.settings.database_url.split(":", 1)[0].split("+", 1)[0]
        )
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


_SERVICE_LOCK = threading.Lock()
_SERVICE: PaperbaseService | None = None


def get_service() -> PaperbaseService:
    global _SERVICE
    if _SERVICE is not None:
        return _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            settings = get_settings()
            repository = build_repository(settings)
            object_store = build_object_store(settings)
            service = PaperbaseService(settings=settings, repository=repository, object_store=object_store)
            service.init()
            _SERVICE = service
    assert _SERVICE is not None
    return _SERVICE
