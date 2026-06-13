"""Tests for CardKitClient — HTTP layer with retry / backoff."""

from __future__ import annotations

import pytest
from httpx import MockTransport, Request, Response

from cubebox.im.feishu.cardkit_client import (
    CardKitClient,
    CardKitCreateError,
)


def _ok_create_response() -> dict[str, object]:
    return {"code": 0, "msg": "success", "data": {"card_id": "AAQA1234"}}


def _build_client(transport: MockTransport) -> CardKitClient:
    return CardKitClient(
        token_provider=lambda: "tenant_access_token_123",
        transport=transport,
    )


@pytest.mark.asyncio
async def test_create_entity_returns_card_id() -> None:
    def handler(_: Request) -> Response:
        return Response(200, json=_ok_create_response())

    client = _build_client(MockTransport(handler))
    card_id = await client.create_entity({"schema": "2.0", "body": {"elements": []}})
    assert card_id == "AAQA1234"


@pytest.mark.asyncio
async def test_create_entity_retries_on_5xx() -> None:
    calls = {"n": 0}

    def handler(_: Request) -> Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return Response(503, json={"code": 99999, "msg": "service unavailable"})
        return Response(200, json=_ok_create_response())

    client = _build_client(MockTransport(handler))
    # Bypass backoff sleep for the test by setting tiny delays.
    from cubebox.im.feishu import cardkit_client as mod

    orig = mod._CREATE_RETRY_DELAYS
    mod._CREATE_RETRY_DELAYS = (0.0, 0.0, 0.0)  # type: ignore[misc]
    try:
        card_id = await client.create_entity({"schema": "2.0", "body": {"elements": []}})
    finally:
        mod._CREATE_RETRY_DELAYS = orig  # type: ignore[misc]
    assert card_id == "AAQA1234"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_create_entity_raises_after_max_retries() -> None:
    def handler(_: Request) -> Response:
        return Response(500, json={"code": 99999, "msg": "boom"})

    client = _build_client(MockTransport(handler))
    from cubebox.im.feishu import cardkit_client as mod

    orig = mod._CREATE_RETRY_DELAYS
    mod._CREATE_RETRY_DELAYS = (0.0, 0.0, 0.0)  # type: ignore[misc]
    try:
        with pytest.raises(CardKitCreateError):
            await client.create_entity({"schema": "2.0", "body": {"elements": []}})
    finally:
        mod._CREATE_RETRY_DELAYS = orig  # type: ignore[misc]
