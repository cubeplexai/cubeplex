"""Memory injection E2E (issue #64, plan task 8.1).

Asserts saved memory items actually shape model behavior:

  1. A personal-scope memory item set in workspace A continues to
     apply when the same user starts a fresh conversation in
     workspace B (personal scope crosses workspace boundaries).
  2. A workspace-scope memory item set by user A applies when user B
     (a different member of the same workspace) starts a fresh
     conversation (workspace scope crosses user boundaries).
"""

from __future__ import annotations

import re

import httpx
import pytest

from tests.e2e.memory._helpers import send_message_and_collect_text

pytestmark = pytest.mark.real_llm

# A reply containing at least one CJK Unified Ideograph character.
_CJK_RE = re.compile(r"[一-鿿]")


async def _save_memory(
    client: httpx.AsyncClient,
    ws_id: str,
    *,
    scope: str,
    type_: str,
    text: str,
) -> None:
    """Create a memory item via the API."""
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/memory",
        json={"scope": scope, "type": type_, "content": text},
    )
    assert resp.status_code == 201, f"Failed to save memory: {resp.text}"


async def _new_conversation(client: httpx.AsyncClient, ws_id: str, *, title: str) -> str:
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations",
        params={"title": title},
    )
    assert resp.status_code == 201, f"Failed to create conversation: {resp.text}"
    return resp.json()["id"]


async def _get_user_org_id(client: httpx.AsyncClient) -> str:
    """Return the org_id of the first workspace the user belongs to."""
    resp = await client.get("/api/v1/workspaces")
    assert resp.status_code == 200, f"Failed to list workspaces: {resp.text}"
    workspaces = resp.json()
    assert workspaces, "User has no workspaces"
    return workspaces[0]["org_id"]


async def _create_workspace(client: httpx.AsyncClient, org_id: str, name: str) -> str:
    """Create a new workspace in the given org; return workspace id."""
    resp = await client.post(
        "/api/v1/workspaces",
        json={"name": name, "org_id": org_id},
    )
    assert resp.status_code == 201, f"Failed to create workspace: {resp.text}"
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_personal_preference_applies_in_different_workspace(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """A personal-scope preference set in ws A is honored in ws B."""
    client, ws_a_id = member_client

    # Create a second workspace in the same org so the same user can
    # access both via member_client.
    org_id = await _get_user_org_id(client)
    ws_b_id = await _create_workspace(client, org_id, "ws-b-injection-test")

    # Save personal preference in ws_a (any workspace will do for personal scope).
    await _save_memory(
        client,
        ws_a_id,
        scope="personal",
        type_="preference",
        text="Always reply in 中文 (Chinese).",
    )

    # Start fresh conversation in ws_b — personal memory should cross workspace.
    conv_id = await _new_conversation(client, ws_b_id, title="injection-personal")
    reply = await send_message_and_collect_text(
        client, ws_b_id, conv_id, "Tell me a fun fact about cats."
    )

    assert _CJK_RE.search(reply), (
        f"Expected the reply to contain Chinese characters because the "
        f"personal-scope memory said so, but got:\n{reply}"
    )


@pytest.mark.asyncio
async def test_workspace_procedure_applies_for_second_member(
    member_client: tuple[httpx.AsyncClient, str],
    second_member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """User A saves a workspace procedure; user B (same ws) sees it applied."""
    client_a, ws_id = member_client
    client_b, ws_id_b = second_member_client
    assert ws_id == ws_id_b

    # User A saves workspace procedure.
    await _save_memory(
        client_a,
        ws_id,
        scope="workspace",
        type_="procedure",
        text=(
            "When the user asks about deploys, ALWAYS first remind them to "
            "run `make check` before pushing."
        ),
    )

    # User B starts a fresh conversation in the same workspace.
    conv_id = await _new_conversation(client_b, ws_id, title="injection-procedure")
    reply = await send_message_and_collect_text(
        client_b, ws_id, conv_id, "How should I deploy the staging service?"
    )

    assert "make check" in reply, (
        f"Expected 'make check' to appear in user B's reply because of the "
        f"workspace-scope procedure saved by user A, but got:\n{reply}"
    )
