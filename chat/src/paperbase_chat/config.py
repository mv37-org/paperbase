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


@lru_cache(maxsize=1)
def get_settings() -> ChatSettings:
    return ChatSettings()
