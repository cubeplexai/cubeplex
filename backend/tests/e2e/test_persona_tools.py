"""E2E: persona tools write the same AgentConfig row as Settings → Persona."""

from __future__ import annotations

import json
from typing import Any

import pytest
from cubepi.hitl import ScriptedChannel
from fastapi.testclient import TestClient
from sqlmodel import select

import cubeplex.db as cubeplex_db
from cubeplex.models.workspace import Workspace
from cubeplex.tools.builtin.persona import PersonaGetArgs, PersonaUpdateArgs, create_persona_tools
from tests.e2e.conftest import DEFAULT_WS_ID

pytestmark = pytest.mark.e2e


def _parse(result: Any) -> dict[str, Any]:
    return json.loads(result.content[0].text)


async def _default_org_id() -> str:
    async with cubeplex_db.async_session_maker() as session:
        result = await session.execute(select(Workspace).where(Workspace.id == DEFAULT_WS_ID))
        return result.scalar_one().org_id


class TestPersonaToolsAgainstSettings:
    @pytest.mark.asyncio
    async def test_tool_write_visible_via_settings_api(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty → first write via tool; GET /settings/agent returns the new text."""
        monkeypatch.setattr(
            "cubeplex.tools.builtin.persona.async_session_maker",
            cubeplex_db.async_session_maker,
        )

        put = client.put(
            f"/api/v1/ws/{DEFAULT_WS_ID}/settings/agent",
            json={"system_prompt": ""},
        )
        assert put.status_code == 200, put.text

        org_id = await _default_org_id()
        channel = ScriptedChannel([])
        tools = create_persona_tools(
            org_id=org_id,
            workspace_id=DEFAULT_WS_ID,
            channel=channel,
            include_update=True,
        )
        update = next(t for t in tools if t.name == "persona_update")
        get_tool = next(t for t in tools if t.name == "persona_get")
        persona = "E2E persona written by persona_update tool."

        result = await update.execute(
            "tc-e2e",
            PersonaUpdateArgs(system_prompt=persona, reason="e2e"),
            signal=None,
            on_update=None,
        )
        payload = _parse(result)
        assert payload["updated"] is True, payload

        got = await get_tool.execute("tc-get", PersonaGetArgs(), signal=None, on_update=None)
        assert _parse(got)["system_prompt"] == persona

        get2 = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/agent")
        assert get2.status_code == 200
        assert get2.json()["system_prompt"] == persona

    @pytest.mark.asyncio
    async def test_tool_overwrite_denied_leaves_settings_unchanged(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "cubeplex.tools.builtin.persona.async_session_maker",
            cubeplex_db.async_session_maker,
        )

        original = "Keep this persona — deny path."
        put = client.put(
            f"/api/v1/ws/{DEFAULT_WS_ID}/settings/agent",
            json={"system_prompt": original},
        )
        assert put.status_code == 200, put.text

        org_id = await _default_org_id()
        channel = ScriptedChannel([{"confirm": "no"}])
        tools = create_persona_tools(
            org_id=org_id,
            workspace_id=DEFAULT_WS_ID,
            channel=channel,
            include_update=True,
        )
        update = next(t for t in tools if t.name == "persona_update")
        result = await update.execute(
            "tc-e2e-deny",
            PersonaUpdateArgs(system_prompt="Should not land"),
            signal=None,
            on_update=None,
        )
        payload = _parse(result)
        assert payload["updated"] is False
        assert payload["reason"] == "denied"

        get = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/agent")
        assert get.json()["system_prompt"] == original

    @pytest.mark.asyncio
    async def test_tool_overwrite_approve_updates_settings(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "cubeplex.tools.builtin.persona.async_session_maker",
            cubeplex_db.async_session_maker,
        )

        put = client.put(
            f"/api/v1/ws/{DEFAULT_WS_ID}/settings/agent",
            json={"system_prompt": "Previous persona for approve path."},
        )
        assert put.status_code == 200, put.text

        org_id = await _default_org_id()
        channel = ScriptedChannel([{"confirm": "yes"}])
        tools = create_persona_tools(
            org_id=org_id,
            workspace_id=DEFAULT_WS_ID,
            channel=channel,
            include_update=True,
        )
        update = next(t for t in tools if t.name == "persona_update")
        new_text = "Approved replacement persona."
        result = await update.execute(
            "tc-e2e-yes",
            PersonaUpdateArgs(system_prompt=new_text, reason="approve e2e"),
            signal=None,
            on_update=None,
        )
        payload = _parse(result)
        assert payload["updated"] is True, payload
        assert len(channel.history) == 1

        get = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/agent")
        assert get.json()["system_prompt"] == new_text
