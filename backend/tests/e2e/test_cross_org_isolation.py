"""E2E cross-org isolation negative paths.

Locks in the AGENTS.md "Scope-isolated APIs" rule: when an org-B user issues
a request against ``/api/v1/ws/{org_a_ws}/...``, the response MUST be 404 —
not 403, not 200.

Why 404 specifically:
- 200 is the data leak we're guarding against.
- 403 leaks the *existence* of the workspace (the attacker now knows the
  workspace id is valid; only the membership is missing). 404 makes a
  workspace the user doesn't belong to indistinguishable from one that
  doesn't exist.

This file exists as a single concentrated sweep so adding a new business
table (or a new route family) has an obvious place to add a "and this one
too" assertion. The org_a / org_b fixtures spin up separate FastAPI apps
sharing the same backend test DB, so a row written by app A is real to
app B's queries — exactly the threat model.

**Current state (2026-06):** ``/api/v1/ws/{ws}/settings`` already returns 404.
Every other workspace-scoped route returns 403 ``"You are not a member of
this workspace"`` — that's the existence-leak we want gone. Those cases are
marked ``xfail`` below with the contract still asserted as 404; the moment
the route layer is tightened to return 404 the xfail flips to xpass and the
strict assertion takes over without anyone editing this file.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------- helpers


async def _create_conversation(client: httpx.AsyncClient, ws_id: str) -> str:
    r = await client.post(f"/api/v1/ws/{ws_id}/conversations", params={"title": "cross-org"})
    r.raise_for_status()
    return r.json()["id"]


async def _create_scheduled_task(client: httpx.AsyncClient, ws_id: str) -> str:
    r = await client.post(
        f"/api/v1/ws/{ws_id}/scheduled-tasks",
        json={
            "name": "cross-org",
            "prompt": "ignored",
            "schedule_kind": "interval",
            "interval_seconds": 3600,
            "target_mode": "new_each_run",
        },
    )
    r.raise_for_status()
    return r.json()["id"]


async def _create_memory(client: httpx.AsyncClient, ws_id: str) -> str:
    r = await client.post(
        f"/api/v1/ws/{ws_id}/memory",
        json={"scope": "personal", "type": "preference", "content": "cross-org-test"},
    )
    r.raise_for_status()
    return r.json()["id"]


async def _create_trigger(client: httpx.AsyncClient, ws_id: str) -> str:
    r = await client.post(
        f"/api/v1/ws/{ws_id}/triggers",
        json={
            "name": "cross-org",
            "kind": "webhook",
            "config": {},
            "enabled": True,
        },
    )
    # Triggers route may 404 in deployments that disable the feature; only
    # use this resource type when the create succeeds.
    if r.status_code >= 400:
        pytest.skip(f"triggers feature unavailable: {r.status_code} {r.text}")
    return r.json()["id"]


# ---------------------------------------------------------------- seeded org_a


@pytest_asyncio.fixture
async def org_a_seeded(
    member_client_org_a: tuple[httpx.AsyncClient, str],
) -> AsyncIterator[tuple[httpx.AsyncClient, str, dict[str, str]]]:
    """org A with one of each business resource pre-seeded."""
    client_a, ws_a = member_client_org_a
    ids = {
        "conversation": await _create_conversation(client_a, ws_a),
        "scheduled_task": await _create_scheduled_task(client_a, ws_a),
        "memory": await _create_memory(client_a, ws_a),
    }
    yield client_a, ws_a, ids


# ---------------------------------------------------------------- negative paths


# Each entry: (label, path_template, returns_403_today). Path uses ``{ws}``
# for the org_a workspace id and ``{conversation}/{scheduled_task}/{memory}``
# for the resource id from ``org_a_seeded.ids``. The list is intentionally
# route-level (not service-level) — the rule we're enforcing lives at the
# HTTP boundary, not below it.
#
# ``returns_403_today=True`` marks a case where the route currently returns
# 403 instead of 404. The test still ASSERTS 404 (xfail keeps the contract
# visible); a route fix turns the xfail into an xpass with no edit here.

_XFAIL_403 = pytest.mark.xfail(
    reason=(
        "Route currently returns 403 ('You are not a member of this workspace'). "
        "AGENTS.md 'Scope-isolated APIs' mandates 404 to avoid leaking workspace "
        "existence. Fix lives in the workspace-membership dependency; the xfail "
        "lifts itself (xpass) once it lands so this file doesn't need editing."
    ),
    strict=False,
)


def _case(label: str, *args: str, leaks_403: bool = False):  # type: ignore[no-untyped-def]
    return pytest.param(label, *args, id=label, marks=([_XFAIL_403] if leaks_403 else []))


_CROSS_ORG_GET_CASES = [
    # Workspace-scoped LIST endpoints — leak the workspace existence if not 404.
    _case("conversations:list", "/api/v1/ws/{ws}/conversations", leaks_403=True),
    _case("scheduled-tasks:list", "/api/v1/ws/{ws}/scheduled-tasks", leaks_403=True),
    _case("memory:list", "/api/v1/ws/{ws}/memory", leaks_403=True),
    _case("triggers:list", "/api/v1/ws/{ws}/triggers", leaks_403=True),
    _case("im-accounts:list", "/api/v1/ws/{ws}/im/accounts", leaks_403=True),
    _case("mcp-installs:list", "/api/v1/ws/{ws}/mcp/installs", leaks_403=True),
    _case("skills:list", "/api/v1/ws/{ws}/skills", leaks_403=True),
    _case("ws-settings:get", "/api/v1/ws/{ws}/settings"),
]

_CROSS_ORG_RESOURCE_CASES = [
    _case("conversation:get", "GET", "/api/v1/ws/{ws}/conversations/{conversation}", leaks_403=True),
    _case(
        "conversation:messages",
        "GET",
        "/api/v1/ws/{ws}/conversations/{conversation}/messages",
        leaks_403=True,
    ),
    _case(
        "conversation:delete",
        "DELETE",
        "/api/v1/ws/{ws}/conversations/{conversation}",
        leaks_403=True,
    ),
    _case(
        "scheduled-task:get",
        "GET",
        "/api/v1/ws/{ws}/scheduled-tasks/{scheduled_task}",
        leaks_403=True,
    ),
    _case(
        "scheduled-task:delete",
        "DELETE",
        "/api/v1/ws/{ws}/scheduled-tasks/{scheduled_task}",
        leaks_403=True,
    ),
    _case("memory:get", "GET", "/api/v1/ws/{ws}/memory/{memory}", leaks_403=True),
    _case("memory:delete", "DELETE", "/api/v1/ws/{ws}/memory/{memory}", leaks_403=True),
]


@pytest.mark.parametrize("label,path", _CROSS_ORG_GET_CASES)
async def test_org_b_gets_404_on_org_a_list_route(
    label: str,
    path: str,
    org_a_seeded: tuple[httpx.AsyncClient, str, dict[str, str]],
    member_client_org_b: tuple[httpx.AsyncClient, str],
) -> None:
    """Listing under org_a's workspace from an org_b client must return 404.

    A 200 here would leak rows; a 403 would confirm the workspace exists.
    Both are reportable security findings — the test fails on either.
    """
    _client_a, ws_a, _ids = org_a_seeded
    client_b, _ws_b = member_client_org_b
    resp = await client_b.get(path.format(ws=ws_a))
    assert resp.status_code == 404, (
        f"[{label}] expected 404 for cross-org access, got {resp.status_code}: {resp.text[:200]}"
    )


@pytest.mark.parametrize("label,method,path", _CROSS_ORG_RESOURCE_CASES)
async def test_org_b_gets_404_on_org_a_resource(
    label: str,
    method: str,
    path: str,
    org_a_seeded: tuple[httpx.AsyncClient, str, dict[str, str]],
    member_client_org_b: tuple[httpx.AsyncClient, str],
) -> None:
    """GET/DELETE of an org_a resource id by an org_b client must return 404.

    Picking up where AGENTS.md leaves off: the workspace_id is in the URL,
    not the body. The route MUST validate membership against the *path*
    workspace and treat a non-member request the same as a missing row.
    """
    _client_a, ws_a, ids = org_a_seeded
    client_b, _ws_b = member_client_org_b
    url = path.format(ws=ws_a, **ids)
    resp = await client_b.request(method, url)
    assert resp.status_code == 404, (
        f"[{label}] expected 404 for cross-org access, got {resp.status_code}: {resp.text[:200]}"
    )


@_XFAIL_403
async def test_org_b_cannot_send_message_to_org_a_conversation(
    org_a_seeded: tuple[httpx.AsyncClient, str, dict[str, str]],
    member_client_org_b: tuple[httpx.AsyncClient, str],
) -> None:
    """Posting a message to an org_a conversation from org_b must 404.

    Separate from the parametrized cases because it's a POST with a body —
    the body shape is route-specific and not worth generalizing.
    """
    _client_a, ws_a, ids = org_a_seeded
    client_b, _ws_b = member_client_org_b
    resp = await client_b.post(
        f"/api/v1/ws/{ws_a}/conversations/{ids['conversation']}/messages",
        json={"content": "leak this"},
    )
    assert resp.status_code == 404, (
        f"expected 404 for cross-org POST, got {resp.status_code}: {resp.text[:200]}"
    )
