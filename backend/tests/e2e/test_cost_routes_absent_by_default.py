"""E2E: the cost reporting routes do not exist without the optional package.

The companion suite in tests/e2e/licensed/ proves they work when installed. This
proves they are gone when not, which is the half that runs in default CI and is
therefore the one actually guarding the boundary.
"""

import importlib.util

import httpx
import pytest

if importlib.util.find_spec("cubeplex_ee") is not None:
    pytest.skip(
        "optional package installed; absence is not the contract in that environment",
        allow_module_level=True,
    )

pytestmark = pytest.mark.e2e

_OLD_PATH = "/api/v1/admin/cost/summary"
_NEW_PATH = "/api/v1/admin/_extensions/cubeplex_ee/cost/summary"


@pytest.mark.asyncio
async def test_cost_endpoints_are_not_served(admin_client: tuple[httpx.AsyncClient, str]) -> None:
    """Authenticated deliberately: a 401 would say nothing about the route existing."""
    client, _ws_id = admin_client
    for path in (_OLD_PATH, _NEW_PATH):
        resp = await client.get(path, params={"from": "2026-01-01", "to": "2026-01-31"})
        assert resp.status_code == 404, f"{path} answered {resp.status_code}: {resp.text[:200]}"


@pytest.mark.asyncio
async def test_cost_endpoints_absent_from_openapi(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Extension routers mount during lifespan, so the schema reflects the edition."""
    client, _ws_id = admin_client
    schema = (await client.get("/openapi.json")).json()
    cost_paths = [p for p in schema["paths"] if "/cost/" in p]
    assert cost_paths == [], f"unlicensed schema still advertises {cost_paths}"
