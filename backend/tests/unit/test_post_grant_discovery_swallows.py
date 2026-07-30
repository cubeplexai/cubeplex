"""``run_post_grant_discovery`` must never bubble exceptions.

The grant is already committed by the time discovery runs, so any
exception escaping the wrapper would turn a successful save into a
client-visible 500. ``MCPDiscoveryFailed`` and ``ValueError`` were
already handled; this test guards the broader-Exception branch added
after a cubepi error surfaced as ``save_failed`` while the row sat in
the DB, blocking retries on ``uq_mcp_credential_grant_org``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from cubeplex.services import mcp_discovery


def test_concrete_network_subclasses_are_transient() -> None:
    request = httpx.Request("POST", "https://mcp.example.com")
    assert mcp_discovery._is_transient_discovery_error(httpx.WriteError("reset", request=request))
    assert mcp_discovery._is_transient_discovery_error(ConnectionResetError())


def test_http_auth_error_is_not_transient() -> None:
    request = httpx.Request("POST", "https://mcp.example.com")
    response = httpx.Response(401, request=request)
    error = httpx.HTTPStatusError("unauthorized", request=request, response=response)
    assert not mcp_discovery._is_transient_discovery_error(error)


@pytest.mark.asyncio
async def test_run_post_grant_discovery_swallows_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(**_kwargs: Any) -> None:
        raise RuntimeError("cubepi exploded in an unexpected way")

    monkeypatch.setattr(mcp_discovery, "discover_tools_for_install", _boom)

    await mcp_discovery.run_post_grant_discovery(
        connector_id="mcpco-test",
        workspace_id=None,
        actor_user_id="usr_test",
        session=None,  # type: ignore[arg-type]
        cred_service=None,  # type: ignore[arg-type]
        signer=None,  # type: ignore[arg-type]
        token_mgr=None,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_run_post_grant_discovery_does_not_swallow_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    async def _cancel(**_kwargs: Any) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setattr(mcp_discovery, "discover_tools_for_install", _cancel)

    with pytest.raises(asyncio.CancelledError):
        await mcp_discovery.run_post_grant_discovery(
            connector_id="mcpco-test",
            workspace_id=None,
            actor_user_id="usr_test",
            session=None,  # type: ignore[arg-type]
            cred_service=None,  # type: ignore[arg-type]
            signer=None,  # type: ignore[arg-type]
            token_mgr=None,  # type: ignore[arg-type]
        )
