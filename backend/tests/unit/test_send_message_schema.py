"""Schema tests for SendMessageRequest (model_key + thinking fields)."""

import pytest
from pydantic import ValidationError

from cubebox.api.routes.v1.conversations import SendMessageRequest


def test_request_accepts_model_key_and_thinking() -> None:
    body = SendMessageRequest.model_validate(
        {
            "content": "hi",
            "model_key": "ultra",
            "thinking": "high",
        }
    )
    assert body.model_key == "ultra"
    assert body.thinking == "high"


def test_thinking_defaults_to_off() -> None:
    body = SendMessageRequest.model_validate({"content": "hi"})
    assert body.thinking == "off"
    assert body.model_key is None


def test_thinking_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        SendMessageRequest.model_validate({"content": "hi", "thinking": "nuclear"})
