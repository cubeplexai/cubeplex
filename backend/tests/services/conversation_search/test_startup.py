"""Unit coverage for the three-way dim verification + a quick disabled-path
integration smoke test for start_search_subsystem.

The three-way check is the safety net for config-driven VECTOR_DIM, so it
deserves direct coverage with mocked sessions independent of a live DB.
"""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from cubebox.services.conversation_search.embedding import EmbeddingProvider
from cubebox.services.conversation_search.startup import (
    _verify_dim_alignment,
    start_search_subsystem,
    stop_search_subsystem,
)


def _build_provider(dims: int) -> EmbeddingProvider:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"data": []}))
    return EmbeddingProvider(
        base_url="https://example/v1",
        api_key="k",
        model="m",
        vector_dim=dims,
        api_dimensions=0,
        timeout_seconds=5,
        _transport=transport,
    )


def _mock_session_maker(schema_dim: int | None) -> Any:
    """Return an async_session_maker-shaped context manager that yields a
    session whose execute() returns format_type(...) = "vector(N)" (or no row).
    """

    class _Result:
        def first(self) -> tuple[str] | None:
            return None if schema_dim is None else (f"vector({schema_dim})",)

    session = MagicMock()
    session.execute = AsyncMock(return_value=_Result())

    @asynccontextmanager
    async def _maker():  # type: ignore[no-untyped-def]
        yield session

    return _maker


@pytest.mark.asyncio
async def test_verify_dim_alignment_all_match() -> None:
    provider = _build_provider(1024)
    with (
        patch(
            "cubebox.services.conversation_search.startup.async_session_maker",
            _mock_session_maker(1024),
        ),
        patch("cubebox.services.conversation_search.startup.config") as cfg,
    ):
        cfg.get.return_value = 1024
        assert await _verify_dim_alignment(provider) is True
    await provider.aclose()


@pytest.mark.asyncio
async def test_verify_dim_alignment_config_differs_from_schema() -> None:
    provider = _build_provider(1024)
    with (
        patch(
            "cubebox.services.conversation_search.startup.async_session_maker",
            _mock_session_maker(1024),
        ),
        patch("cubebox.services.conversation_search.startup.config") as cfg,
    ):
        cfg.get.return_value = 1536  # config disagrees
        assert await _verify_dim_alignment(provider) is False
    await provider.aclose()


@pytest.mark.asyncio
async def test_verify_dim_alignment_provider_differs_from_schema() -> None:
    provider = _build_provider(1536)  # provider disagrees
    with (
        patch(
            "cubebox.services.conversation_search.startup.async_session_maker",
            _mock_session_maker(1024),
        ),
        patch("cubebox.services.conversation_search.startup.config") as cfg,
    ):
        cfg.get.return_value = 1024
        assert await _verify_dim_alignment(provider) is False
    await provider.aclose()


@pytest.mark.asyncio
async def test_verify_dim_alignment_schema_missing() -> None:
    """Table missing (migration not run) is treated as a hard failure."""
    provider = _build_provider(1024)
    with (
        patch(
            "cubebox.services.conversation_search.startup.async_session_maker",
            _mock_session_maker(None),
        ),
        patch("cubebox.services.conversation_search.startup.config") as cfg,
    ):
        cfg.get.return_value = 1024
        assert await _verify_dim_alignment(provider) is False
    await provider.aclose()


@pytest.mark.asyncio
async def test_start_search_subsystem_disabled_leaves_state_none() -> None:
    """When search.enabled=False the subsystem is inert: no provider, no
    worker, no lexical backend, and the rest of the service must still be
    happy to boot.
    """
    app = FastAPI()
    with patch("cubebox.services.conversation_search.startup.config") as cfg:
        cfg.get.side_effect = lambda key, default=None: (
            False if key == "search.enabled" else default
        )
        await start_search_subsystem(app)

    assert app.state.embedding_provider is None
    assert app.state.embedding_worker is None
    assert app.state.embedding_worker_task is None
    assert app.state.lexical_backend is None

    # stop is a no-op when nothing was started
    await stop_search_subsystem(app)
