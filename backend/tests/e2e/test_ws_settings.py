"""E2E tests for workspace settings API (M4)."""

import io
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.e2e.conftest import DEFAULT_WS_ID, collect_sse_events

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

    def test_persona_rejects_over_max_length(self, client: TestClient) -> None:
        resp = client.put(
            f"/api/v1/ws/{DEFAULT_WS_ID}/settings/agent",
            json={"system_prompt": "x" * 8001},
        )
        assert resp.status_code == 422


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

    def test_upload_creates_workspace_private_install(self, client: TestClient) -> None:
        """Upload a zip — Skill row lands in catalog, install row is workspace-private."""
        import secrets

        slug = f"upload-{secrets.token_hex(3)}"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(
                "SKILL.md",
                f"---\nname: {slug}\ndescription: workspace private upload\nversion: 1.0.0\n---\n# {slug}\n",
            )

        resp = client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/settings/skills/upload",
            files={"file": ("a.zip", buf.getvalue(), "application/zip")},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["version"] == "1.0.0"
        skill_id = data["skill_id"]

        # The new install must show up in workspace_skills (not org_skills).
        skills_resp = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/skills")
        ws = skills_resp.json()["workspace_skills"]
        assert any(s["skill_id"] == skill_id for s in ws), (
            f"uploaded skill should be workspace-private, got {ws}"
        )
        org = skills_resp.json()["org_skills"]
        assert not any(s["skill_id"] == skill_id for s in org), (
            f"uploaded skill must not be org-installed, got {org}"
        )


class TestSettingsScoping:
    """Settings are scoped per workspace — non-members cannot read workspace data."""

    def test_other_workspace_not_found(self, client: TestClient) -> None:
        """A request for a workspace the user is not a member of returns 403 or 404."""
        resp = client.get("/api/v1/ws/ws-nonexistent-000/settings/agent")
        assert resp.status_code in (403, 404)

    def test_persona_key_always_present(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/agent")
        assert resp.status_code == 200
        assert "system_prompt" in resp.json()

    def test_skills_returns_lists(self, client: TestClient) -> None:
        skills_resp = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/skills")
        assert skills_resp.status_code == 200
        assert isinstance(skills_resp.json()["org_skills"], list)


class TestPersonaRuntimeApplied:
    """Workspace persona system_prompt is actually injected into the running agent."""

    @pytest.mark.asyncio
    @pytest.mark.real_llm
    async def test_persona_sentinel_appears_in_agent_response(
        self,
        authenticated_client: tuple[httpx.AsyncClient, str],
    ) -> None:
        """Persona set on a workspace is injected into the agent system prompt at runtime.

        Strategy: configure a persona that instructs the LLM to echo a fixed sentinel
        token when asked to identify itself.  Send 'Who are you?' and assert that the
        sentinel appears somewhere in the collected SSE text_delta stream.
        """
        client, workspace_id = authenticated_client
        sentinel = "SENTINEL-PERSONA-ACTIVE"
        persona = (
            f"When asked who you are, ALWAYS reply with exactly '{sentinel}'. "
            "Do not add any other words."
        )

        # 1. Write the sentinel persona for this isolated workspace.
        put_resp = await client.put(
            f"/api/v1/ws/{workspace_id}/settings/agent",
            json={"system_prompt": persona},
        )
        assert put_resp.status_code == 200, put_resp.text

        try:
            # 2. Create a new conversation inside the same workspace.
            conv_resp = await client.post(
                f"/api/v1/ws/{workspace_id}/conversations",
                params={"title": "persona-runtime-test"},
            )
            assert conv_resp.status_code == 201, conv_resp.text
            conv_id = conv_resp.json()["id"]

            # 3. Send a message and collect all SSE events.
            events = await collect_sse_events(
                client,
                f"/api/v1/ws/{workspace_id}/conversations/{conv_id}/messages",
                json_data={"content": "Who are you?"},
            )

            # 4. Reconstruct the full assistant text from text_delta events.
            text_events = [e for e in events if e["type"] == "text_delta"]
            assert text_events, (
                f"No text_delta events in SSE stream; got types: {[e['type'] for e in events]}"
            )
            full_text = "".join(e["data"]["content"] for e in text_events)

            assert sentinel in full_text, (
                f"Sentinel '{sentinel}' not found in agent response.\nFull response: {full_text!r}"
            )
        finally:
            # 5. Reset the persona so the isolated workspace is left clean.
            await client.put(
                f"/api/v1/ws/{workspace_id}/settings/agent",
                json={"system_prompt": ""},
            )
