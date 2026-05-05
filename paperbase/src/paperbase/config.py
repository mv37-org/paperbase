"""Configuration for the hosted paperbase service."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_AI_CATEGORIES = [
    "cs.AI",
    "cs.LG",
    "cs.CL",
    "cs.CV",
    "cs.NE",
    "cs.MA",
    "cs.RO",
    "stat.ML",
    "cs.IR",
    "cs.SD",
    "eess.AS",
    "cs.HC",
    "cs.CR",
]

_ADMIN_EMAIL_SPLIT_RE = re.compile(r"[\s,]+")


def repo_root(start: Path | None = None) -> Path | None:
    anchor = Path(__file__).resolve() if start is None else start.resolve()
    current = anchor if anchor.is_dir() else anchor.parent
    for candidate in (current, *current.parents):
        if (
            (candidate / "Makefile").is_file()
            and (candidate / "chat").is_dir()
            and (candidate / "paperbase").is_dir()
            and (candidate / "paperbase-plugin").is_dir()
        ):
            return candidate
    return None


def settings_env_file() -> Path:
    root = repo_root()
    if root is not None:
        return root / ".env"

    current = Path(__file__).resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate / ".env"
    return Path(".env")


def resolve_repo_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path.resolve()

    root = repo_root()
    if root is not None:
        return (root / path).resolve()
    return path.resolve()


class Settings(BaseSettings):
    """Runtime configuration sourced from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="PAPERBASE_",
        env_file=str(settings_env_file()),
        extra="ignore",
    )

    api_key: str = Field(default="development-token")
    database_url: str = Field(default="postgresql+psycopg://postgres:postgres@localhost:5432/paperbase")
    storage_backend: str = Field(default="gcs")
    local_storage_root: str = Field(default=".paperbase-data")
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="text")
    browser_origin: str = Field(default="http://127.0.0.1:5173")
    chat_public_url: str = Field(default="http://127.0.0.1:8080")
    app_secret: str = Field(default="paperbase-development-secret")
    encryption_key: str | None = None
    browser_session_cookie_name: str = Field(default="paperbase_browser_session")
    browser_session_ttl_seconds: int = Field(default=60 * 60 * 24 * 30)
    browser_session_secure: bool = Field(default=False)
    password_reset_ttl_seconds: int = Field(default=60 * 60)
    admin_emails: str = Field(default="")
    resend_api_key: str | None = None
    resend_from_email: str = Field(default="Paperbase <auth@paperbase.local>")
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_oauth_redirect_url: str | None = None
    github_client_id: str | None = None
    github_client_secret: str | None = None
    github_oauth_redirect_url: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str = Field(default="https://api.openai.com")
    openai_model: str = Field(default="gpt-5")
    anthropic_api_key: str | None = None
    anthropic_base_url: str = Field(default="https://api.anthropic.com")
    anthropic_model: str = Field(default="claude-opus-4-7")
    chat_runtime_mode: str = Field(default="codex_kernel")
    upload_max_bytes: int = Field(default=25_000_000)
    gcs_project: str | None = None
    gcs_bucket: str = "paperbase"
    gcs_public_base_url: str | None = None
    gcs_service_account_json: str | None = None
    presign_ttl_seconds: int = 3600
    sync_interval_seconds: int = 3600
    worker_status_url: str | None = None
    worker_poll_interval_seconds: int = 10
    worker_auto_sync_latest: bool = Field(default=True)
    worker_initial_sync_latest: bool = Field(default=True)
    arxiv_categories: list[str] = Field(default_factory=lambda: list(DEFAULT_AI_CATEGORIES))
    latest_sync_days: int = 7
    latest_sync_limit_per_category: int = 100
    result_ttl_days: int = 7
    # Default embedding model: BAAI/bge-base-en-v1.5. Swapped from SPECTER2
    # after an end-to-end benchmark (see PR #19 follow-up eval + issue #22):
    # SPECTER2 is trained for paper-to-paper similarity (citation prediction)
    # and scores poorly on query-to-paper retrieval — pure vector-only top-1
    # was 10/30 vs 27/30 for BGE-base on the same 30-paper benchmark.
    # BGE-base matches SPECTER2's dim (768) and encoding throughput so the
    # switch is a zero-cost quality lift. Existing deployments should queue a
    # full rewrite after upgrading via `paperbase-dev reindex-embeddings` or
    # POST /v1/index/reindex-embeddings (both default to rewrite_existing=true).
    embedding_model: str | None = Field(default="BAAI/bge-base-en-v1.5")
    embedding_dim: int = 768
    rerank_model: str | None = None
    rerank_top_k: int = 50
    # Small log-citation nudge added to rerank scores so a seminal highly-cited
    # paper wins ties against a zero-cite recent variant when rerank scores are
    # close. Kept small so it can only break near-ties, not flip clear winners.
    rerank_citation_nudge: float = 0.02
    # Summaries at ingest: "none" (tldr-or-first-sentence, empty ELI5 — intended for mass indexing),
    # "heuristic" (local text wrangling), or "vertex" (Gemini on Vertex AI, expensive at scale).
    summary_provider: str = Field(default="none")
    summary_model: str = "gemini-2.5-flash-lite"
    vertex_project: str | None = None
    vertex_location: str = "us-central1"
    lazy_summary_on_read: bool = Field(default=True)
    featured_tier_concurrency: int = 16
    rrf_k: int = 60
    # Citation boost: log10(1+cites) * scale, capped at max. Defaults keep
    # boost <= one RRF rank unit (1/60 ≈ 0.017) so it never dominates relevance.
    citation_boost_scale: float = 0.003
    citation_boost_max: float = 0.015
    # Recency boost: tie-breaker, kept smaller than citation_boost_max and the
    # top RRF rank unit (1/60 ≈ 0.017) so a recent-but-irrelevant paper can't
    # outrank an older paper with a real lexical or vector hit.
    recency_boost_max: float = 0.005
    recency_half_life_years: int = 10
    # Section chunking windows. Applied to sections whose raw markdown exceeds
    # chunk_max_chars; produces overlapping sub-chunks that are all embedded.
    chunk_max_chars: int = 1500
    chunk_overlap_chars: int = 300
    # RRF weights for search() sources (local is the reference = 1.0).
    live_source_weight: float = 0.8
    live_source_weight_recent: float = 1.2
    # Query expansion (server-side, for research_qa): how many variants Gemini
    # is asked to produce. Keeps the Vertex call small.
    query_expansion_variants: int = 4
    # Citation graph: fetch and persist S2 references at index time. Adds one
    # extra S2 call per indexed paper, so turn off for mass harvests and leave
    # on for featured-tier / on-demand indexing.
    fetch_references_at_index: bool = Field(default=False)
    # Paper-code ingestion (#5). github_token is service-level (NOT the user
    # OAuth connection used for project repo sync) and is only used to bump
    # the anonymous 60 req/hr archive-fetch limit to 5000. The caps below
    # bound worst-case disk + scan cost so a 10 GB data-weights repo cannot
    # wedge the ingest_repo worker; any repo hitting a cap is recorded with
    # a skip reason in code_manifest instead of being partially ingested.
    github_token: str | None = None
    code_repo_max_archive_bytes: int = Field(default=100 * 1024 * 1024)
    code_repo_max_scanned_files: int = Field(default=50_000)
    code_repo_max_stored_file_bytes: int = Field(default=1 * 1024 * 1024)
    code_repo_max_indexed_file_bytes: int = Field(default=1 * 1024 * 1024)

    @property
    def effective_encryption_key(self) -> str:
        if self.encryption_key:
            return self.encryption_key
        digest = hashlib.sha256(self.app_secret.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii")

    @property
    def admin_email_allowlist(self) -> frozenset[str]:
        raw = (self.admin_emails or "").strip()
        if not raw:
            return frozenset()
        values: list[str]
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw
            if isinstance(parsed, list):
                values = [str(item) for item in parsed]
            else:
                values = [str(parsed)]
        else:
            values = _ADMIN_EMAIL_SPLIT_RE.split(raw)
        return frozenset(value.strip().lower() for value in values if value.strip())

    def is_admin_email(self, email: str) -> bool:
        return email.strip().lower() in self.admin_email_allowlist


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
