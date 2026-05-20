"""E2E tests for the admin LLM preset-catalog endpoint."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.e2e  # ensure marker even though conftest auto-adds

# Tracks the cubepi bundled provider-preset catalog (feat branch). Bump if the
# upstream catalog changes.
EXPECTED_PRESET_COUNT = 37


async def test_list_provider_presets(
    admin_client: tuple[AsyncClient, str],
) -> None:
    """Admin sees the full cubepi preset catalog with the expected shape."""
    client, _ws_id = admin_client

    res = await client.get("/api/v1/admin/llm/presets")
    assert res.status_code == 200
    data = res.json()

    assert isinstance(data, list)
    assert len(data) == EXPECTED_PRESET_COUNT

    anthropic = next(p for p in data if p["slug"] == "anthropic")
    assert anthropic["api"] == "anthropic-messages"
    assert anthropic["logo"] == "anthropic"
    assert anthropic["capability"]["reasoning_level"]["kind"] == "int_budget"
