"""Configuration for the shared paperbase plugin."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class PluginSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PAPERBASE_",
        env_file=".env",
        extra="ignore",
    )

    base_url: str = "http://localhost:8080"
    api_key: str = "development-token"


@lru_cache(maxsize=1)
def get_settings() -> PluginSettings:
    return PluginSettings()
