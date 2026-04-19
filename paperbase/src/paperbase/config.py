"""Configuration for the hosted paperbase service."""

from __future__ import annotations

from functools import lru_cache

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


class Settings(BaseSettings):
    """Runtime configuration sourced from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="PAPERBASE_",
        env_file=".env",
        extra="ignore",
    )

    api_key: str = Field(default="development-token")
    database_url: str = Field(default="postgresql+psycopg://postgres:postgres@localhost:5432/paperbase")
    storage_backend: str = Field(default="gcs")
    local_storage_root: str = Field(default=".paperbase-data")
    gcs_project: str | None = None
    gcs_bucket: str = "paperbase"
    gcs_public_base_url: str | None = None
    gcs_service_account_json: str | None = None
    presign_ttl_seconds: int = 3600
    sync_interval_seconds: int = 3600
    worker_poll_interval_seconds: int = 10
    arxiv_categories: list[str] = Field(default_factory=lambda: list(DEFAULT_AI_CATEGORIES))
    latest_sync_days: int = 7
    latest_sync_limit_per_category: int = 100
    result_ttl_days: int = 7
    embedding_model: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
