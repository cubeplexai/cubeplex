"""E2E tests for workspace settings API (M4)."""

import pytest
from fastapi.testclient import TestClient

from tests.e2e.conftest import DEFAULT_WS_ID

pytestmark = pytest.mark.e2e


class TestPersonaRuntime:
    """Persona is applied to the agent system prompt."""

    def test_get_agent_config_default(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/agent")
        assert resp.status_code == 200
        data = resp.json()
        assert "system_prompt" in data

    def test_update_and_read_persona(self, client: TestClient) -> None:
        persona = "You are a Python expert."
        resp = client.put(
            f"/api/v1/ws/{DEFAULT_WS_ID}/settings/agent",
            json={"system_prompt": persona},
        )
        assert resp.status_code == 200
        assert resp.json()["system_prompt"] == persona

        get_resp = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/agent")
        assert get_resp.json()["system_prompt"] == persona
