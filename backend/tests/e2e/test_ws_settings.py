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


class TestSkillsSettings:
    """Workspace skill binding and private skill management."""

    def test_list_skills_returns_structure(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert "org_skills" in data
        assert "workspace_skills" in data
        assert isinstance(data["org_skills"], list)
        assert isinstance(data["workspace_skills"], list)

    def test_toggle_org_skill_if_available(self, client: TestClient) -> None:
        skills_resp = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/skills")
        org_skills = skills_resp.json()["org_skills"]
        if not org_skills:
            pytest.skip("No org skills installed")

        install_id = org_skills[0]["install_id"]
        resp = client.patch(
            f"/api/v1/ws/{DEFAULT_WS_ID}/settings/skills/{install_id}",
            json={"enabled": False},
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        resp = client.patch(
            f"/api/v1/ws/{DEFAULT_WS_ID}/settings/skills/{install_id}",
            json={"enabled": True},
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True


class TestMCPSettings:
    """Workspace MCP settings routes."""

    def test_list_mcp(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/mcp")
        assert resp.status_code == 200
        data = resp.json()
        assert "org_servers" in data
        assert "workspace_servers" in data
        assert isinstance(data["org_servers"], list)
        assert isinstance(data["workspace_servers"], list)
