"""OpenAI Responses-backed browser chat runtime primitives."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
import json
import logging
from typing import Any

import requests

from .config import Settings


logger = logging.getLogger(__name__)


def _coerce_usage_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class ChatModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass(frozen=True)
class ChatModelStreamEvent:
    """Legacy test helper shim for the pre-Responses fake chat model."""

    text: str = ""
    response: dict[str, Any] | None = None
    usage: ChatModelUsage | None = None


@dataclass(frozen=True)
class Prompt:
    input: list[dict[str, Any]]
    base_instructions: str
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_choice: str | dict[str, Any] | None = "auto"
    parallel_tool_calls: bool = False


@dataclass(frozen=True)
class ResponseItem:
    item_id: str
    item_type: str
    role: str = ""
    status: str = "completed"
    call_id: str = ""
    name: str = ""
    content_text: str = ""
    content_json: Any | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResponseEnvelope:
    response_id: str = ""
    items: list[ResponseItem] = field(default_factory=list)
    usage: ChatModelUsage | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResponseEvent:
    type: str
    response_id: str = ""
    item: ResponseItem | None = None
    item_id: str = ""
    delta: str = ""
    usage: ChatModelUsage | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _tool_choice_value(tool_choice: str | dict[str, Any] | None) -> str | None:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        value = tool_choice.strip().lower()
        if value == "any":
            return "required"
        return value or None
    value = str(tool_choice.get("type") or "").strip().lower()
    if value == "any":
        return "required"
    return value or None


def _tool_spec_to_responses(tool: dict[str, Any]) -> dict[str, Any]:
    if tool.get("type") == "function":
        return tool
    return {
        "type": "function",
        "name": str(tool.get("name") or ""),
        "description": str(tool.get("description") or ""),
        "parameters": tool.get("input_schema") if isinstance(tool.get("input_schema"), dict) else {"type": "object"},
    }


def _message_content_blocks_from_text(text: str) -> list[dict[str, Any]]:
    return [{"type": "input_text", "text": text}]


def _normalize_message_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    role = str(item.get("role") or "user")
    content = item.get("content")
    if isinstance(content, str):
        return [{"type": "message", "role": role, "content": _message_content_blocks_from_text(content)}]
    if not isinstance(content, list):
        return [{"type": "message", "role": role, "content": _message_content_blocks_from_text(str(content or ""))}]

    normalized: list[dict[str, Any]] = []
    text_parts: list[str] = []

    def flush_text() -> None:
        if not text_parts:
            return
        normalized.append(
            {
                "type": "message",
                "role": role,
                "content": _message_content_blocks_from_text("\n".join(part for part in text_parts if part).strip()),
            }
        )
        text_parts.clear()

    for block in content:
        if not isinstance(block, dict):
            text_parts.append(str(block))
            continue
        block_type = str(block.get("type") or "")
        if block_type in {"text", "input_text", "output_text"}:
            text_parts.append(str(block.get("text") or ""))
            continue
        if block_type == "tool_use":
            flush_text()
            normalized.append(
                {
                    "type": "function_call",
                    "id": str(block.get("id") or ""),
                    "call_id": str(block.get("id") or block.get("call_id") or ""),
                    "name": str(block.get("name") or ""),
                    "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                }
            )
            continue
        if block_type == "tool_result":
            flush_text()
            output = block.get("content")
            if isinstance(output, (dict, list)):
                output_text = json.dumps(output, ensure_ascii=False)
            else:
                output_text = str(output or "")
            normalized.append(
                {
                    "type": "function_call_output",
                    "call_id": str(block.get("tool_use_id") or block.get("call_id") or ""),
                    "output": output_text,
                }
            )
            continue
        text_parts.append(json.dumps(block, ensure_ascii=False))
    flush_text()
    return [entry for entry in normalized if entry.get("content") or entry.get("type") != "message"]


def _normalize_input_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            normalized.append({"type": "message", "role": "user", "content": _message_content_blocks_from_text(str(item))})
            continue
        item_type = str(item.get("type") or "")
        if item_type in {
            "message",
            "function_call",
            "function_call_output",
            "custom_tool_call",
            "custom_tool_call_output",
            "web_search_call",
            "tool_search_call",
            "tool_search_output",
            "reasoning",
        }:
            if item_type == "message" and isinstance(item.get("content"), str):
                normalized.append(
                    {
                        **item,
                        "content": _message_content_blocks_from_text(str(item.get("content") or "")),
                    }
                )
            else:
                normalized.append(item)
            continue
        if "role" in item:
            normalized.extend(_normalize_message_item(item))
            continue
        normalized.append({"type": "message", "role": "user", "content": _message_content_blocks_from_text(json.dumps(item, ensure_ascii=False))})
    return normalized


def _usage_from_payload(payload: Any) -> ChatModelUsage | None:
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens = _coerce_usage_int(usage.get("input_tokens"))
    output_tokens = _coerce_usage_int(usage.get("output_tokens"))
    total_tokens = _coerce_usage_int(usage.get("total_tokens")) or (input_tokens + output_tokens)
    return ChatModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def response_usage(payload: Any) -> ChatModelUsage | None:
    if isinstance(payload, ResponseEnvelope):
        return payload.usage
    return _usage_from_payload(payload)


def _response_item_text(item: dict[str, Any]) -> str:
    if item.get("type") != "message":
        if item.get("type") in {"function_call", "custom_tool_call"}:
            return str(item.get("arguments") or "")
        if item.get("type") in {"function_call_output", "custom_tool_call_output"}:
            return str(item.get("output") or "")
        return ""
    content = item.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "")
        if part_type in {"output_text", "input_text"}:
            parts.append(str(part.get("text") or ""))
        elif part_type == "refusal":
            parts.append(str(part.get("refusal") or ""))
    return "".join(parts)


def _response_item_json(item: dict[str, Any]) -> Any | None:
    item_type = str(item.get("type") or "")
    if item_type in {"function_call", "custom_tool_call"}:
        raw = str(item.get("arguments") or "")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"partial_json": raw}
    if item_type in {"function_call_output", "custom_tool_call_output"}:
        raw = item.get("output")
        if isinstance(raw, (dict, list)):
            return raw
        if not isinstance(raw, str):
            return raw
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"text": raw}
    if item_type == "reasoning":
        return item.get("summary") or item.get("content")
    return item.get("content")


def _normalize_response_item(item: dict[str, Any]) -> ResponseItem:
    item_type = str(item.get("type") or "other")
    return ResponseItem(
        item_id=str(item.get("id") or item.get("item_id") or ""),
        item_type=item_type,
        role=str(item.get("role") or ""),
        status=str(item.get("status") or "completed"),
        call_id=str(item.get("call_id") or item.get("id") or ""),
        name=str(item.get("name") or ""),
        content_text=_response_item_text(item),
        content_json=_response_item_json(item),
        raw=item,
    )


class ChatModelClient(ABC):
    @abstractmethod
    def create_response(self, *, api_key: str, prompt: Prompt) -> ResponseEnvelope:
        raise NotImplementedError

    @abstractmethod
    def stream_response(self, *, api_key: str, prompt: Prompt) -> Iterator[ResponseEvent]:
        raise NotImplementedError

    def count_prompt_tokens(self, *, api_key: str, prompt: Prompt) -> int | None:
        del api_key, prompt
        return None


class OpenAIResponsesChatModelClient(ChatModelClient):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _request_body(self, *, prompt: Prompt, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._settings.openai_model,
            "input": _normalize_input_items(prompt.input),
            "instructions": prompt.base_instructions,
            "tools": [_tool_spec_to_responses(tool) for tool in prompt.tools],
            "parallel_tool_calls": prompt.parallel_tool_calls,
            "stream": stream,
        }
        tool_choice = _tool_choice_value(prompt.tool_choice)
        if tool_choice:
            payload["tool_choice"] = tool_choice
        return payload

    def create_response(self, *, api_key: str, prompt: Prompt) -> ResponseEnvelope:
        response = requests.post(
            f"{self._settings.openai_base_url.rstrip('/')}/v1/responses",
            headers={
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            json=self._request_body(prompt=prompt, stream=False),
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        items = [_normalize_response_item(item) for item in (payload.get("output") or []) if isinstance(item, dict)]
        return ResponseEnvelope(
            response_id=str(payload.get("id") or ""),
            items=items,
            usage=_usage_from_payload(payload),
            raw=payload,
        )

    def stream_response(self, *, api_key: str, prompt: Prompt) -> Iterator[ResponseEvent]:
        response = requests.post(
            f"{self._settings.openai_base_url.rstrip('/')}/v1/responses",
            headers={
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            json=self._request_body(prompt=prompt, stream=True),
            timeout=120,
            stream=True,
        )
        response.raise_for_status()
        response_id = ""
        completed_usage: ChatModelUsage | None = None
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            if raw_line.startswith(":") or raw_line.startswith("event:"):
                continue
            if not raw_line.startswith("data:"):
                continue
            payload_text = raw_line[5:].strip()
            if not payload_text or payload_text == "[DONE]":
                continue
            payload = json.loads(payload_text)
            event_type = str(payload.get("type") or "")
            if event_type == "response.created":
                response_id = str((payload.get("response") or {}).get("id") or "")
            elif event_type == "response.completed":
                response_id = response_id or str((payload.get("response") or {}).get("id") or "")
                completed_usage = _usage_from_payload(payload.get("response") or payload)
            item_payload = payload.get("item") if isinstance(payload.get("item"), dict) else None
            yield ResponseEvent(
                type=event_type,
                response_id=response_id,
                item=_normalize_response_item(item_payload) if item_payload is not None else None,
                item_id=str(payload.get("item_id") or (item_payload or {}).get("id") or ""),
                delta=str(payload.get("delta") or ""),
                usage=completed_usage if event_type == "response.completed" else None,
                raw=payload,
            )
        if completed_usage is not None:
            logger.info(
                "Completed OpenAI Responses stream",
                extra={
                    "event": "chat.model.stream.completed",
                    "model": self._settings.openai_model,
                    "response_id": response_id,
                },
            )
