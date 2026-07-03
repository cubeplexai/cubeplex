"""End-to-end preset switching: per-message model_key picks the right chain.

Two FauxProviders ("big" and "small") are registered. The default preset
points at "big"; an additional non-default preset points at "small".

* Without ``model_key`` → default ("big") is used; reply contains "big".
* With ``model_key="small"`` → the "small" preset is used; reply
  contains "small", and the conversation row persists ``model_key``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from cubepi.providers.faux import FauxProvider, faux_assistant_message, faux_text
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import cubebox.db as _cubebox_db
from cubebox.api.app import create_app
from cubebox.db.engine import _build_database_url, engine
from cubebox.db.session import get_session
from cubebox.models import Conversation
from cubebox.models.org_settings import MODEL_PRESETS_KEY, OrgSettings
from cubebox.models.provider import Model as DBModel
from cubebox.models.provider import Provider as DBProvider
from tests.e2e.conftest import (
    DEFAULT_ORG_ID,
    DEFAULT_TEST_EMAIL,
    DEFAULT_TEST_PASSWORD,
    DEFAULT_WS_ID,
    _ensure_default_user_and_membership,
    _lifespan_context,
    _login_and_attach,
)


def _make_test_app() -> Any:
    """Create a test app wired with NullPool DB + sandbox_factory=None."""
    url = _build_database_url()
    test_engine = create_async_engine(url, poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    _cubebox_db.async_session_maker = test_session_maker

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with test_session_maker() as session:
            yield session

    app = create_app(sandbox_factory=None)
    app.dependency_overrides[get_session] = override_get_session
    return app


async def _seed_switching_providers_and_presets() -> None:
    """Seed providers big/small (system, org_id=None) and two-preset OrgSettings."""
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            # Wipe any prior rows from a previous run (the test DB persists).
            await session.execute(
                delete(DBModel).where(
                    DBModel.provider_id.in_(  # type: ignore[attr-defined]
                        select(DBProvider.id).where(
                            DBProvider.slug.in_(("big", "small"))  # type: ignore[attr-defined]
                        )
                    )
                )
            )
            await session.execute(
                delete(DBProvider).where(
                    DBProvider.slug.in_(("big", "small"))  # type: ignore[attr-defined]
                )
            )
            # Delete BOTH org-level and system-level model_presets rows so
            # only our seeded preset survives.
            await session.execute(
                delete(OrgSettings).where(
                    OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
                )
            )
            await session.commit()

            for slug in ("big", "small"):
                provider = DBProvider(
                    org_id=None,
                    name=slug,
                    slug=slug,
                    provider_type="openai-completions",
                    base_url=f"https://{slug}.test/v1",
                    auth_type="api_key",
                    enabled=True,
                )
                session.add(provider)
                await session.flush()
                session.add(
                    DBModel(
                        org_id=None,
                        provider_id=provider.id,
                        model_id="m1",
                        display_name=f"{slug}-m1",
                        reasoning=False,
                        input_modalities=["text"],
                        cost_input=0.0,
                        cost_output=0.0,
                        cost_cache_read=0.0,
                        cost_cache_write=0.0,
                        context_window=128_000,
                        max_tokens=4096,
                        enabled=True,
                    )
                )

            # Two custom presets: "big" (default) and "small". Tiers are off;
            # routing is exercised purely via custom labels here.
            off = {"enabled": False, "primary": None, "fallbacks": []}
            session.add(
                OrgSettings(
                    org_id=DEFAULT_ORG_ID,
                    key=MODEL_PRESETS_KEY,
                    value={
                        "tiers": {
                            "lite": dict(off),
                            "flash": dict(off),
                            "pro": dict(off),
                            "max": dict(off),
                        },
                        "custom_presets": [
                            {
                                "label": "big",
                                "primary": "big/m1",
                                "fallbacks": [],
                                "description": "",
                            },
                            {
                                "label": "small",
                                "primary": "small/m1",
                                "fallbacks": [],
                                "description": "",
                            },
                        ],
                        "default_preset": "big",
                        "task_routing": {},
                    },
                )
            )
            await session.commit()
    finally:
        await test_engine.dispose()


@pytest_asyncio.fixture
async def switching_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """Client with FauxProviders patched in for slugs big / small."""
    await _ensure_default_user_and_membership()
    await _seed_switching_providers_and_presets()

    big = FauxProvider(provider_id="big")
    big.set_responses(
        [
            faux_assistant_message(
                [faux_text("answer from big")],
                stop_reason="stop",
            ),
            faux_assistant_message(
                [faux_text("answer from big again")],
                stop_reason="stop",
            ),
        ]
    )

    small = FauxProvider(provider_id="small")
    small.set_responses(
        [
            faux_assistant_message(
                [faux_text("answer from small")],
                stop_reason="stop",
            ),
            faux_assistant_message(
                [faux_text("answer from small again")],
                stop_reason="stop",
            ),
        ]
    )

    from cubebox.llm import builder as _builder

    real_build_provider = _builder.build_provider

    def _patched_build_provider(snap: Any, slug: str, **kw: Any) -> Any:
        if slug == "big":
            return big
        if slug == "small":
            return small
        return real_build_provider(snap, slug, **kw)

    monkeypatch.setattr("cubebox.llm.builder.build_provider", _patched_build_provider)

    app = _make_test_app()
    app.state.deployment_mode = "multi_tenant"

    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, DEFAULT_TEST_EMAIL, DEFAULT_TEST_PASSWORD)
            yield c

    await engine.dispose()


async def _stream_to_done(
    client: httpx.AsyncClient,
    ws_id: str,
    conv_id: str,
    body: dict[str, Any],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async with client.stream(
        "POST",
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        json=body,
        headers={"accept": "text/event-stream"},
    ) as resp:
        assert resp.status_code == 200, resp.text
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[len("data: ") :])
            events.append(payload)
            if payload.get("type") in {"done", "error"}:
                return events
    return events


async def _create_conversation(client: httpx.AsyncClient, ws_id: str, title: str) -> str:
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations",
        params={"title": title},
    )
    assert resp.status_code == 201, f"conversation creation failed: {resp.text}"
    conv_id: str = resp.json()["id"]
    return conv_id


def _collect_text(events: list[dict[str, Any]]) -> str:
    text_events = [e for e in events if e.get("type") == "text_delta"]
    return "".join((e.get("data") or {}).get("content", "") for e in text_events)


@pytest.mark.asyncio
async def test_default_preset_routes_to_big(
    switching_client: httpx.AsyncClient,
) -> None:
    """No preset_label → default 'big' preset is used."""
    client = switching_client
    ws_id = DEFAULT_WS_ID
    conv_id = await _create_conversation(client, ws_id, "switch-default")

    events = await _stream_to_done(client, ws_id, conv_id, {"content": "hi"})

    errors = [e for e in events if e.get("type") == "error"]
    assert not errors, f"unexpected error events: {errors!r}"

    text = _collect_text(events)
    assert "big" in text.lower(), (
        f"expected 'big' answer; got: {text!r}\nevent types: {[e.get('type') for e in events]!r}"
    )
    assert "small" not in text.lower(), f"unexpected 'small' in reply: {text!r}"


@pytest.mark.asyncio
async def test_explicit_preset_label_routes_to_small(
    switching_client: httpx.AsyncClient,
) -> None:
    """model_key='small' → small preset is used instead of default."""
    client = switching_client
    ws_id = DEFAULT_WS_ID
    conv_id = await _create_conversation(client, ws_id, "switch-small")

    events = await _stream_to_done(
        client,
        ws_id,
        conv_id,
        {
            "content": "hi",
            "model_key": "small",
            "reasoning": {"mode": "on", "effort": "high", "summary": "none"},
        },
    )

    errors = [e for e in events if e.get("type") == "error"]
    assert not errors, f"unexpected error events: {errors!r}"

    text = _collect_text(events)
    assert "small" in text.lower(), (
        f"expected 'small' answer; got: {text!r}\nevent types: {[e.get('type') for e in events]!r}"
    )
    assert "big" not in text.lower(), f"unexpected 'big' in reply: {text!r}"

    # The send path must persist the chosen model setting on the conversation
    # row so the frontend can restore it on reload.
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            row = (
                await session.execute(
                    select(Conversation).where(Conversation.id == conv_id)  # type: ignore[arg-type]
                )
            ).scalar_one()
            assert row.model_key == "small", row.model_key
            assert row.reasoning == {"mode": "on", "effort": "high", "summary": "none"}
    finally:
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_unknown_model_key_falls_back_to_default(
    switching_client: httpx.AsyncClient,
) -> None:
    """A model_key whose preset no longer exists (e.g. a custom preset deleted
    after a conversation stored it) falls back to the workspace default instead
    of 400, and the conversation's stored key is healed to null on send."""
    client = switching_client
    ws_id = DEFAULT_WS_ID
    conv_id = await _create_conversation(client, ws_id, "switch-ghost")

    events = await _stream_to_done(
        client,
        ws_id,
        conv_id,
        {
            "content": "hi",
            "model_key": "ghost",
            "reasoning": {"mode": "on", "effort": "high", "summary": "none"},
        },
    )

    errors = [e for e in events if e.get("type") == "error"]
    assert not errors, f"unexpected error events: {errors!r}"

    # The stale key resolves to the default 'big' preset, not a 400.
    text = _collect_text(events)
    assert "big" in text.lower(), f"expected default 'big' answer; got: {text!r}"

    # The conversation heals: the unknown key is persisted as null (default),
    # not the stale 'ghost', so the next send no longer carries a dead key.
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            row = (
                await session.execute(
                    select(Conversation).where(Conversation.id == conv_id)  # type: ignore[arg-type]
                )
            ).scalar_one()
            assert row.model_key is None, row.model_key
            assert row.reasoning == {"mode": "on", "effort": "high", "summary": "none"}
    finally:
        await test_engine.dispose()
