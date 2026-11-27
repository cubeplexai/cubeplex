"""Unit contracts for the artifact bridge registry."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from cubeplex.services.artifact_bridge import catalogue, get_function, register_function
from cubeplex.services.artifact_bridge.types import (
    ArtifactFunction,
    ArtifactFunctionNotFound,
)


class _NoopInput(BaseModel):
    value: int = 0


async def _noop(ctx: object, args: _NoopInput) -> dict[str, object]:
    return {"value": args.value}


def test_chat_function_is_registered() -> None:
    function = get_function("chat")
    assert function.name == "chat"
    schema = function.input_model.model_json_schema()
    assert "prompt" in schema["properties"]
    assert schema["required"] == ["prompt"]


def test_catalogue_describes_chat_for_clients() -> None:
    entries = {entry["name"]: entry for entry in catalogue()}
    assert "chat" in entries
    assert entries["chat"]["description"]
    assert "prompt" in entries["chat"]["input_schema"]["properties"]


def test_unknown_function_raises_not_found() -> None:
    with pytest.raises(ArtifactFunctionNotFound):
        get_function("does-not-exist")


def test_duplicate_registration_is_rejected() -> None:
    function = ArtifactFunction(
        name="registry-test-dup",
        description="d",
        input_model=_NoopInput,
        handler=_noop,
    )
    register_function(function)
    with pytest.raises(ValueError, match="already registered"):
        register_function(function)
