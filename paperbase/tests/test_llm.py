from __future__ import annotations

import requests

from paperbase.config import Settings
from paperbase.llm import (
    OpenAIResponsesChatModelClient,
    Prompt,
    _normalize_input_items,
    _normalize_response_item,
    _response_error_detail,
)


def test_normalize_input_items_uses_output_text_for_assistant_messages():
    normalized = _normalize_input_items(
        [
            {"type": "message", "role": "assistant", "content": "Earlier answer"},
            {"role": "assistant", "content": "Another earlier answer"},
            {"role": "user", "content": "New question"},
        ]
    )

    assert normalized[0] == {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Earlier answer"}],
    }
    assert normalized[1] == {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Another earlier answer"}],
    }
    assert normalized[2] == {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "New question"}],
    }


def test_normalize_response_item_preserves_assistant_message_phase():
    item = _normalize_response_item(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "phase": "commentary",
            "content": [{"type": "output_text", "text": "I will inspect the project first."}],
        }
    )

    assert item.phase == "commentary"
    assert item.content_text == "I will inspect the project first."


def test_responses_request_body_includes_structured_text_format():
    text_format = {
        "type": "json_schema",
        "name": "project_presentation",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"summary_markdown": {"type": "string"}},
            "required": ["summary_markdown"],
        },
    }
    prompt = Prompt(
        input=[{"type": "message", "role": "user", "content": "Summarize this project."}],
        base_instructions="Return a structured project presentation.",
        tools=[],
        tool_choice=None,
        text_format=text_format,
    )

    body = OpenAIResponsesChatModelClient(Settings(openai_model="gpt-5.5"))._request_body(
        prompt=prompt,
        stream=False,
    )

    assert body["model"] == "gpt-5.5"
    assert body["text"] == {"format": text_format}


def test_response_error_detail_summarizes_html_errors():
    response = requests.Response()
    response.status_code = 502
    response._content = b"""
    <!doctype html>
    <html><head><title> | 502: Bad gateway</title></head><body>long cloudflare page</body></html>
    """

    detail = _response_error_detail(response)

    assert detail == "HTTP 502: | 502: Bad gateway"
