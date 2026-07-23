"""Unit tests for persona_get / persona_update tools."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from cubepi.hitl import ScriptedChannel

from cubeplex.services.agent_config import (
    PERSONA_MAX_LENGTH,
    PersonaConflictError,
    persona_fingerprint,
)
from cubeplex.tools.builtin.persona import (
    PersonaGetArgs,
    PersonaUpdateArgs,
    create_persona_tools,
)

pytestmark = pytest.mark.asyncio


def _parse(result: Any) -> dict[str, Any]:
    return json.loads(result.content[0].text)


class _Cfg:
    def __init__(self, text: str) -> None:
        self.system_prompt = text


async def test_create_persona_tools_read_only_omits_update() -> None:
    tools = create_persona_tools(
        org_id="o1",
        workspace_id="w1",
        include_update=False,
    )
    assert [t.name for t in tools] == ["persona_get"]


async def test_create_persona_tools_with_update_requires_channel() -> None:
    with pytest.raises(ValueError, match="HITL channel"):
        create_persona_tools(org_id="o1", workspace_id="w1", include_update=True)


async def test_persona_get_returns_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cubeplex.tools.builtin.persona.get_system_prompt",
        AsyncMock(return_value="You are a research assistant."),
    )
    # session factory is entered; stub it to a no-op async CM
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock())
    cm.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "cubeplex.tools.builtin.persona.async_session_maker",
        MagicMock(return_value=cm),
    )

    tools = create_persona_tools(org_id="o1", workspace_id="w1", include_update=False)
    get = tools[0]
    result = await get.execute("tc", PersonaGetArgs(), signal=None, on_update=None)
    payload = _parse(result)
    assert payload["system_prompt"] == "You are a research assistant."
    assert payload["length"] == len("You are a research assistant.")
    assert payload["max_length"] == PERSONA_MAX_LENGTH
    assert payload["fingerprint"] == persona_fingerprint("You are a research assistant.")


async def test_persona_update_empty_first_write_no_hitl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_mock = AsyncMock(return_value=_Cfg("New persona"))
    monkeypatch.setattr(
        "cubeplex.tools.builtin.persona.get_system_prompt",
        AsyncMock(return_value=""),
    )
    monkeypatch.setattr(
        "cubeplex.tools.builtin.persona.set_system_prompt",
        set_mock,
    )
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock())
    cm.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "cubeplex.tools.builtin.persona.async_session_maker",
        MagicMock(return_value=cm),
    )

    channel = ScriptedChannel([])  # must not be consumed
    tools = create_persona_tools(
        org_id="o1",
        workspace_id="w1",
        channel=channel,
        include_update=True,
    )
    update = next(t for t in tools if t.name == "persona_update")
    assert getattr(update, "hitl_builtin", False) is True

    result = await update.execute(
        "tc",
        PersonaUpdateArgs(system_prompt="New persona", reason="init"),
        signal=None,
        on_update=None,
    )
    payload = _parse(result)
    assert payload["updated"] is True
    assert payload["previous_length"] == 0
    assert payload["length"] == len("New persona")
    set_mock.assert_awaited_once()
    # empty → first write: no expected_fingerprint
    kwargs = set_mock.await_args.kwargs
    assert kwargs.get("expected_fingerprint") is None
    assert channel.history == []


async def test_persona_update_overwrite_requires_confirm_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = "Old persona text"
    set_mock = AsyncMock(return_value=_Cfg("Replacement persona"))
    monkeypatch.setattr(
        "cubeplex.tools.builtin.persona.get_system_prompt",
        AsyncMock(return_value=current),
    )
    monkeypatch.setattr(
        "cubeplex.tools.builtin.persona.set_system_prompt",
        set_mock,
    )
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock())
    cm.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "cubeplex.tools.builtin.persona.async_session_maker",
        MagicMock(return_value=cm),
    )

    channel = ScriptedChannel([{"confirm": "yes"}])
    tools = create_persona_tools(
        org_id="o1",
        workspace_id="w1",
        channel=channel,
        include_update=True,
    )
    update = next(t for t in tools if t.name == "persona_update")
    result = await update.execute(
        "tc",
        PersonaUpdateArgs(system_prompt="Replacement persona", reason="user asked"),
        signal=None,
        on_update=None,
    )
    payload = _parse(result)
    assert payload["updated"] is True
    assert payload["previous_length"] == len(current)
    assert len(channel.history) == 1
    kwargs = set_mock.await_args.kwargs
    assert kwargs["expected_fingerprint"] == persona_fingerprint(current)


async def test_persona_update_overwrite_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_mock = AsyncMock()
    monkeypatch.setattr(
        "cubeplex.tools.builtin.persona.get_system_prompt",
        AsyncMock(return_value="Existing"),
    )
    monkeypatch.setattr(
        "cubeplex.tools.builtin.persona.set_system_prompt",
        set_mock,
    )
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock())
    cm.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "cubeplex.tools.builtin.persona.async_session_maker",
        MagicMock(return_value=cm),
    )

    channel = ScriptedChannel([{"confirm": "no"}])
    tools = create_persona_tools(
        org_id="o1",
        workspace_id="w1",
        channel=channel,
        include_update=True,
    )
    update = next(t for t in tools if t.name == "persona_update")
    result = await update.execute(
        "tc",
        PersonaUpdateArgs(system_prompt="Would replace"),
        signal=None,
        on_update=None,
    )
    payload = _parse(result)
    assert payload["updated"] is False
    assert payload["reason"] == "denied"
    set_mock.assert_not_awaited()


async def test_persona_update_conflict_on_stale_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cubeplex.tools.builtin.persona.get_system_prompt",
        AsyncMock(return_value="Existing"),
    )
    monkeypatch.setattr(
        "cubeplex.tools.builtin.persona.set_system_prompt",
        AsyncMock(side_effect=PersonaConflictError("stale")),
    )
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock())
    cm.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "cubeplex.tools.builtin.persona.async_session_maker",
        MagicMock(return_value=cm),
    )

    channel = ScriptedChannel([{"confirm": "yes"}])
    tools = create_persona_tools(
        org_id="o1",
        workspace_id="w1",
        channel=channel,
        include_update=True,
    )
    update = next(t for t in tools if t.name == "persona_update")
    result = await update.execute(
        "tc",
        PersonaUpdateArgs(system_prompt="Newer"),
        signal=None,
        on_update=None,
    )
    payload = _parse(result)
    assert payload["updated"] is False
    assert payload["reason"] == "conflict"
    assert result.is_error is True


async def test_persona_update_unchanged_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_mock = AsyncMock()
    monkeypatch.setattr(
        "cubeplex.tools.builtin.persona.get_system_prompt",
        AsyncMock(return_value="Same"),
    )
    monkeypatch.setattr(
        "cubeplex.tools.builtin.persona.set_system_prompt",
        set_mock,
    )
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock())
    cm.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "cubeplex.tools.builtin.persona.async_session_maker",
        MagicMock(return_value=cm),
    )

    channel = ScriptedChannel([])
    tools = create_persona_tools(
        org_id="o1",
        workspace_id="w1",
        channel=channel,
        include_update=True,
    )
    update = next(t for t in tools if t.name == "persona_update")
    result = await update.execute(
        "tc",
        PersonaUpdateArgs(system_prompt="Same"),
        signal=None,
        on_update=None,
    )
    payload = _parse(result)
    assert payload["updated"] is False
    assert payload["reason"] == "unchanged"
    set_mock.assert_not_awaited()
