from __future__ import annotations

from paperbase.llm import _normalize_input_items, _normalize_response_item


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
