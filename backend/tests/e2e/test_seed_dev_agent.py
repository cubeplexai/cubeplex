"""E2E test for scripts/dev/seed_dev_agent.py's HTTP flow.

Exercises the real register -> login -> onboarding -> api-key flow against
a real test app (ASGI transport, real lifespan/Redis), then proves the
returned Bearer token authenticates as the seeded user on both /auth/me
and a workspace-scoped route.

Pure helper logic (credential derivation, .worktree.env section write) is
covered in tests/unit/test_seed_dev_agent.py.
"""

import importlib.machinery
import importlib.util
import secrets
from pathlib import Path

import httpx
import pytest

from tests.e2e.helpers import csrf_cookie_name

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "dev" / "seed_dev_agent.py"


def _load_seed_module():
    loader = importlib.machinery.SourceFileLoader("seed_dev_agent_e2e", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader("seed_dev_agent_e2e", loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.e2e
async def test_seed_via_http_creates_working_bearer_token(
    unauthenticated_memory_client: httpx.AsyncClient,
) -> None:
    seed = _load_seed_module()
    client = unauthenticated_memory_client

    suffix = secrets.token_hex(4)
    email = f"seed-e2e-{suffix}@example.com"
    password = "DevAgent1!-e2e"
    org_slug = f"e2e-{suffix}"

    result = await seed.seed_via_http(
        client,
        csrf_cookie_name=csrf_cookie_name(),
        email=email,
        password=password,
        org_name=f"E2E Org {suffix}",
        org_slug=org_slug,
        workspace_name="Personal",
        key_label="e2e",
    )

    # Token shape + scope ids populated.
    assert result.token.startswith("sk-")
    assert result.email == email
    assert result.workspace_id
    assert result.org_id

    # The token authenticates as the seeded user. Switch the client to a
    # Bearer-only session (no cookies) so we exercise DefaultAuthProvider's
    # bearer path, not the login cookie.
    client.cookies.clear()
    client.headers["Authorization"] = f"Bearer {result.token}"

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["email"] == email

    # And it works on a workspace-scoped route (request_context -> membership
    # check passes: the seeder's onboarding granted ADMIN on this workspace).
    convs = await client.get(f"/api/v1/ws/{result.workspace_id}/conversations")
    assert convs.status_code == 200, convs.text


@pytest.mark.e2e
async def test_seed_via_http_is_idempotent(
    unauthenticated_memory_client: httpx.AsyncClient,
) -> None:
    """Re-running reuses the user/org/workspace and rotates the token."""
    seed = _load_seed_module()
    client = unauthenticated_memory_client

    suffix = secrets.token_hex(4)
    email = f"seed-e2e-idem-{suffix}@example.com"
    common = {
        "csrf_cookie_name": csrf_cookie_name(),
        "email": email,
        "password": "DevAgent1!-idem",
        "org_name": f"Idem Org {suffix}",
        "org_slug": f"idem-{suffix}",
        "workspace_name": "Personal",
        "key_label": "e2e-idem",
    }

    first = await seed.seed_via_http(client, **common)  # type: ignore[arg-type]
    # Second run: register 409, onboarding 409 -> reuse; old key deleted, new minted.
    second = await seed.seed_via_http(client, **common)  # type: ignore[arg-type]

    assert first.workspace_id == second.workspace_id  # same workspace reused
    assert first.org_id == second.org_id
    assert first.token != second.token  # token rotated (plaintext isn't stored)

    # The new (rotated) token authenticates; the old one must NOT.
    client.cookies.clear()
    client.headers["Authorization"] = f"Bearer {second.token}"
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text

    client.headers["Authorization"] = f"Bearer {first.token}"
    stale = await client.get("/api/v1/auth/me")
    assert stale.status_code == 401  # old key was deleted
