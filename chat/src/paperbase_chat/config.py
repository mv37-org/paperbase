"""Configuration for the lightweight paperbase chat app."""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChatSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    host: str = Field(default="0.0.0.0", validation_alias=AliasChoices("PAPERBASE_CHAT_HOST"))
    port: int = Field(default=8091, validation_alias=AliasChoices("PAPERBASE_CHAT_PORT", "PORT"))
    paperbase_base_url: str = Field(
        default="http://127.0.0.1:8080",
        validation_alias=AliasChoices("PAPERBASE_BASE_URL", "PAPERBASE_CHAT_PAPERBASE_BASE_URL"),
    )
    paperbase_api_key: str = Field(
        default="development-token",
        validation_alias=AliasChoices("PAPERBASE_API_KEY", "PAPERBASE_CHAT_PAPERBASE_API_KEY"),
    )
    request_timeout_seconds: int = Field(
        default=60,
        ge=5,
        le=300,
        validation_alias=AliasChoices("PAPERBASE_CHAT_TIMEOUT_SECONDS"),
    )
    search_limit: int = Field(
        default=8,
        ge=1,
        le=20,
        validation_alias=AliasChoices("PAPERBASE_CHAT_SEARCH_LIMIT"),
    )
    section_limit: int = Field(
        default=6,
        ge=1,
        le=20,
        validation_alias=AliasChoices("PAPERBASE_CHAT_SECTION_LIMIT"),
    )
    evidence_limit: int = Field(
        default=4,
        ge=1,
        le=10,
        validation_alias=AliasChoices("PAPERBASE_CHAT_EVIDENCE_LIMIT"),
    )


@lru_cache(maxsize=1)
def get_settings() -> ChatSettings:
    return ChatSettings()
