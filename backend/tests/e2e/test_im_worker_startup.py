"""E2E for app-startup IM runtime wiring (Task 13)."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def test_worker_attached_to_app_state(async_client: httpx.AsyncClient) -> None:
    """``_start_im_runtime`` (called from lifespan) must attach the worker +
    long-connection dict to ``app.state``."""
    asgi_transport = async_client._transport  # type: ignore[attr-defined]
    app = asgi_transport.app
    assert hasattr(app.state, "im_run_queue_worker"), (
        "im_run_queue_worker missing — startup wiring not invoked"
    )
    assert app.state.im_run_queue_worker is not None
    assert hasattr(app.state, "im_long_connections")
    assert isinstance(app.state.im_long_connections, dict)
