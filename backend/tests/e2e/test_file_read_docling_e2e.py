"""E2E: agent-facing file_read path reaches a real docling-serve.

Runs only when ``DOCLING_URL`` is set and the endpoint answers its /health
probe. No mocks — if docling is unreachable the tests skip with a reason.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from redis.asyncio import Redis

from cubeplex.cache import reset_for_tests, set_redis
from cubeplex.config import config
from cubeplex.models.public_id import generate_public_id
from cubeplex.parsers import (
    ParseOptions,
    TextOutput,
    UnchangedOutput,
    get_parser_registry,
    reset_parser_registry_for_tests,
)

FIXTURE = Path(__file__).parent / "fixtures" / "hello.pdf"


@pytest.fixture
async def _bound_registry_and_redis() -> AsyncIterator[None]:
    """Configure a fresh parser registry + real Redis cache for each test.

    The autouse _flush_test_redis fixture in the e2e conftest clears the DB
    before every test, so we don't need per-fixture flushall here.
    """
    client = Redis.from_url(
        config.get("redis.url", "redis://127.0.0.1:6379/0"),
        decode_responses=True,
    )
    set_redis(client)
    reset_parser_registry_for_tests()
    try:
        yield
    finally:
        reset_parser_registry_for_tests()
        await client.aclose()
        reset_for_tests()


class _LocalSandbox:
    """Minimal sandbox stub — just surfaces the fixture bytes for the registry."""

    async def _download_one(self, path: str) -> bytes:  # noqa: D401
        return FIXTURE.read_bytes()


@pytest.mark.e2e
async def test_pdf_flows_through_real_docling(
    docling_url: str,
    monkeypatch: pytest.MonkeyPatch,
    _bound_registry_and_redis: None,
) -> None:
    """Read a small PDF via real docling-serve; assert markdown + parser metadata."""
    monkeypatch.setenv("CUBEPLEX_PARSERS__DOCLING_SERVE__BASE_URL", docling_url)
    config.reload()

    reg = get_parser_registry()
    await reg.discover()

    out = await reg.dispatch(
        sandbox=_LocalSandbox(),
        path=str(FIXTURE),
        options=ParseOptions(),
        conversation_id=generate_public_id("conv"),
    )
    assert isinstance(out, TextOutput), f"expected markdown, got {type(out).__name__}"
    assert cast(str, out.metadata.get("parser")) == "docling"
    assert len(out.content) > 0
    assert "hello" in out.content.lower()


@pytest.mark.e2e
async def test_unchanged_second_read_hits_dedup(
    docling_url: str,
    monkeypatch: pytest.MonkeyPatch,
    _bound_registry_and_redis: None,
) -> None:
    """Same conversation + same bytes + same options → second read is UnchangedOutput."""
    monkeypatch.setenv("CUBEPLEX_PARSERS__DOCLING_SERVE__BASE_URL", docling_url)
    config.reload()

    reg = get_parser_registry()
    await reg.discover()

    sandbox = _LocalSandbox()
    conv = generate_public_id("conv")
    first = await reg.dispatch(
        sandbox=sandbox,
        path=str(FIXTURE),
        options=ParseOptions(),
        conversation_id=conv,
    )
    assert isinstance(first, TextOutput)

    second = await reg.dispatch(
        sandbox=sandbox,
        path=str(FIXTURE),
        options=ParseOptions(),
        conversation_id=conv,
    )
    assert isinstance(second, UnchangedOutput)
