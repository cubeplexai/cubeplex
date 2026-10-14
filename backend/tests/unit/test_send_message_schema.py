import pytest
from pydantic import ValidationError

from cubeplex.api.routes.v1.conversations import SendMessageRequest


def test_request_accepts_model_key_and_reasoning() -> None:
    body = SendMessageRequest.model_validate(
        {
            "client_message_id": "client-message-1",
            "content": "hi",
            "model_key": "pro",
            "reasoning": {"mode": "on", "effort": "high", "summary": "none"},
        }
    )

    assert body.client_message_id == "client-message-1"
    assert body.model_key == "pro"
    assert body.reasoning.mode == "on"
    assert body.reasoning.effort == "high"


def test_reasoning_defaults_to_model_default() -> None:
    body = SendMessageRequest.model_validate(
        {"client_message_id": "client-message-2", "content": "hi"}
    )

    assert body.reasoning is None


def test_request_rejects_legacy_thinking() -> None:
    with pytest.raises(ValidationError):
        SendMessageRequest.model_validate(
            {
                "client_message_id": "client-message-3",
                "content": "hi",
                "thinking": "high",
            }
        )


def test_request_accepts_auto_for_chain_specific_validation() -> None:
    body = SendMessageRequest.model_validate(
        {
            "client_message_id": "client-message-4",
            "content": "hi",
            "reasoning": {"mode": "auto", "effort": "medium"},
        }
    )
    assert body.reasoning is not None and body.reasoning.mode == "auto"


@pytest.mark.parametrize("payload", [{"content": "hi"}, {"client_message_id": "", "content": "hi"}])
def test_request_requires_nonempty_client_message_id(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        SendMessageRequest.model_validate(payload)
