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


@pytest.mark.asyncio
async def test_stream_text_sends_sequence_and_delta() -> None:
    captured: dict[str, object] = {}

    def handler(req: Request) -> Response:
        captured["url"] = str(req.url)
        captured["body"] = req.read()
        return Response(200, json={"code": 0, "msg": "success"})

    client = _build_client(MockTransport(handler))
    await client.stream_text(
        card_id="AAQA",
        element_id="streaming_content",
        content="Hello",
        sequence=3,
    )
    body_text = (captured["body"] or b"").decode()
    assert "AAQA" in str(captured["url"])
    assert "streaming_content" in str(captured["url"])
    assert '"sequence": 3' in body_text or '"sequence":3' in body_text
    assert "Hello" in body_text


@pytest.mark.asyncio
async def test_stream_text_raises_ratelimit_on_230020() -> None:
    def handler(_: Request) -> Response:
        return Response(200, json={"code": 230020, "msg": "too fast"})

    client = _build_client(MockTransport(handler))
    from cubebox.im.feishu.cardkit_client import CardKitRateLimit

    with pytest.raises(CardKitRateLimit):
        await client.stream_text(
            card_id="AAQA",
            element_id="streaming_content",
            content="x",
            sequence=1,
        )


@pytest.mark.asyncio
async def test_patch_card_sends_full_json() -> None:
    captured: dict[str, object] = {}

    def handler(req: Request) -> Response:
        captured["body"] = req.read()
        return Response(200, json={"code": 0, "msg": "success"})

    client = _build_client(MockTransport(handler))
    await client.patch_card(
        card_id="AAQA",
        card_json={"schema": "2.0", "body": {"elements": []}},
        sequence=5,
    )
    text = (captured["body"] or b"").decode()
    assert '"sequence": 5' in text or '"sequence":5' in text
    assert "schema" in text


@pytest.mark.asyncio
async def test_finalize_retries_up_to_cap() -> None:
    calls = {"n": 0}

    def handler(_: Request) -> Response:
        calls["n"] += 1
        if calls["n"] < 4:
            return Response(500, json={"code": 99999, "msg": "boom"})
        return Response(200, json={"code": 0, "msg": "success"})

    from cubebox.im.feishu import cardkit_client as mod

    orig = mod._FINALIZE_RETRY_DELAYS
    mod._FINALIZE_RETRY_DELAYS = (0.0, 0.0, 0.0, 0.0, 0.0)  # type: ignore[misc]
    try:
        client = _build_client(MockTransport(handler))
        finalized = await client.finalize(
            card_id="AAQA",
            card_json={"schema": "2.0", "body": {"elements": []}},
            sequence=99,
        )
    finally:
        mod._FINALIZE_RETRY_DELAYS = orig  # type: ignore[misc]
    assert finalized is True
    assert calls["n"] == 4


@pytest.mark.asyncio
async def test_finalize_gives_up_after_max_attempts() -> None:
    def handler(_: Request) -> Response:
        return Response(500, json={"code": 99999, "msg": "down"})

    from cubebox.im.feishu import cardkit_client as mod

    orig = mod._FINALIZE_RETRY_DELAYS
    mod._FINALIZE_RETRY_DELAYS = (0.0, 0.0)  # type: ignore[misc]
    try:
        client = _build_client(MockTransport(handler))
        finalized = await client.finalize(
            card_id="AAQA",
            card_json={"schema": "2.0", "body": {"elements": []}},
            sequence=99,
        )
        assert finalized is False
    finally:
        mod._FINALIZE_RETRY_DELAYS = orig  # type: ignore[misc]
