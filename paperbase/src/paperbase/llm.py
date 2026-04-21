"""Anthropic-backed answer generation for browser chat."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
import json
import logging
from typing import Any

import requests

from .config import Settings


logger = logging.getLogger(__name__)


def _extract_text(payload: dict[str, Any]) -> str:
    parts = [item.get("text", "") for item in payload.get("content", []) if item.get("type") == "text"]
    return "\n".join(part for part in parts if part).strip()


class ChatModelClient(ABC):
    @abstractmethod
    def create_message(
        self,
        *,
        api_key: str,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def stream_message(
        self,
        *,
        api_key: str,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> Iterator[str]:
        raise NotImplementedError

    def complete(self, *, api_key: str, prompt: str, system_prompt: str) -> str:
        payload = self.create_message(
            api_key=api_key,
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt,
        )
        return _extract_text(payload)

    def stream_complete(self, *, api_key: str, prompt: str, system_prompt: str) -> Iterator[str]:
        yield from self.stream_message(
            api_key=api_key,
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt,
        )


class AnthropicChatModelClient(ChatModelClient):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _request_body(
        self,
        *,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]] | None,
        tool_choice: dict[str, Any] | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._settings.anthropic_model,
            "max_tokens": 1200,
            "system": system_prompt,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
        if stream:
            payload["stream"] = True
        return payload

    def create_message(
        self,
        *,
        api_key: str,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = requests.post(
            f"{self._settings.anthropic_base_url.rstrip('/')}/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=self._request_body(
                messages=messages,
                system_prompt=system_prompt,
                tools=tools,
                tool_choice=tool_choice,
                stream=False,
            ),
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        logger.info(
            "Completed Anthropic chat request",
            extra={
                "event": "chat.model.completed",
                "model": self._settings.anthropic_model,
                "stop_reason": payload.get("stop_reason"),
            },
        )
        return payload

    def stream_message(
        self,
        *,
        api_key: str,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> Iterator[str]:
        response = requests.post(
            f"{self._settings.anthropic_base_url.rstrip('/')}/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=self._request_body(
                messages=messages,
                system_prompt=system_prompt,
                tools=tools,
                tool_choice=tool_choice,
                stream=True,
            ),
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
