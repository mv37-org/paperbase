"""Configuration for the shared paperbase plugin."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _settings_env_file() -> Path:
    current = Path(__file__).resolve().parent
    for candidate in (current, *current.parents):
        if (
            (candidate / "Makefile").is_file()
            and (candidate / "chat").is_dir()
            and (candidate / "paperbase").is_dir()
            and (candidate / "paperbase-plugin").is_dir()
        ):
            return candidate / ".env"
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate / ".env"
    return Path(".env")


class PluginSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PAPERBASE_",
        env_file=str(_settings_env_file()),
        extra="ignore",
    )

    base_url: str = "http://127.0.0.1:8080"
    api_key: str = "development-token"


@lru_cache(maxsize=1)
def get_settings() -> PluginSettings:
    return PluginSettings()
