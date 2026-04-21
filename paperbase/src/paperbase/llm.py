"""Anthropic-backed answer generation for browser chat."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
import json
import logging

import requests

from .config import Settings


logger = logging.getLogger(__name__)


class ChatModelClient(ABC):
    @abstractmethod
    def complete(self, *, api_key: str, prompt: str, system_prompt: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def stream_complete(self, *, api_key: str, prompt: str, system_prompt: str) -> Iterator[str]:
        raise NotImplementedError


class AnthropicChatModelClient(ChatModelClient):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def complete(self, *, api_key: str, prompt: str, system_prompt: str) -> str:
        response = requests.post(
            f"{self._settings.anthropic_base_url.rstrip('/')}/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self._settings.anthropic_model,
                "max_tokens": 1200,
                "system": system_prompt,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        parts = [item.get("text", "") for item in payload.get("content", []) if item.get("type") == "text"]
        text = "\n".join(part for part in parts if part).strip()
        logger.info("Completed Anthropic chat request", extra={"event": "chat.model.completed", "model": self._settings.anthropic_model})
        return text

    def stream_complete(self, *, api_key: str, prompt: str, system_prompt: str) -> Iterator[str]:
        response = requests.post(
            f"{self._settings.anthropic_base_url.rstrip('/')}/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self._settings.anthropic_model,
                "max_tokens": 1200,
                "system": system_prompt,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
            },
            timeout=120,
            stream=True,
        )
        response.raise_for_status()

        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            if raw_line.startswith(":"):
                continue
            if raw_line.startswith("event:"):
                continue
            if not raw_line.startswith("data:"):
                continue
            payload = raw_line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            message = json.loads(payload)
            if message.get("type") != "content_block_delta":
                continue
            delta = message.get("delta") or {}
            if delta.get("type") != "text_delta":
                continue
            text = delta.get("text") or ""
            if text:
                yield text

        logger.info(
            "Completed Anthropic streaming chat request",
            extra={"event": "chat.model.stream.completed", "model": self._settings.anthropic_model},
        )
