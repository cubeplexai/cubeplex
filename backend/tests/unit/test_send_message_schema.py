"""Schema tests for SendMessageRequest (preset_label + thinking fields)."""

import pytest
from pydantic import ValidationError

from cubebox.api.routes.v1.conversations import SendMessageRequest


def test_request_accepts_preset_label_and_thinking() -> None:
    body = SendMessageRequest.model_validate(
        {
            "content": "hi",
            "preset_label": "ultra",
            "thinking": "high",
        }
    )
    assert body.preset_label == "ultra"
    assert body.thinking == "high"


def test_thinking_defaults_to_off() -> None:
    body = SendMessageRequest.model_validate({"content": "hi"})
    assert body.thinking == "off"
    assert body.preset_label is None


def test_thinking_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        SendMessageRequest.model_validate({"content": "hi", "thinking": "nuclear"})
