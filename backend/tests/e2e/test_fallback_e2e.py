"""End-to-end fallback test using cubepi.FauxProvider chains.

The primary FauxProvider raises ``RateLimited`` on its first stream so
``FallbackBoundModel`` falls over to chain[1]; the backup FauxProvider
returns a normal AssistantMessage. We verify the full pipeline:

* a ``model_failover`` SSE event is emitted with the expected
  ``failed_ref`` / ``next_ref`` / ``reason``;
* the final reply text comes from the backup chain leg.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from cubepi.errors import RateLimited
from cubepi.providers.faux import FauxProvider, faux_assistant_message, faux_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import cubebox.db as _cubebox_db
from cubebox.api.app import create_app
from cubebox.db.engine import _build_database_url, engine
from cubebox.db.session import get_session
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


def _tiered_value(*, primary: str, fallbacks: list[str]) -> dict[str, Any]:
    """A valid tiered ModelPresetsConfig value: only `pro` enabled (default)."""
    off = {"enabled": False, "primary": None, "fallbacks": []}
    return {
        "tiers": {
            "lite": dict(off),
            "flash": dict(off),
            "pro": {"enabled": True, "primary": primary, "fallbacks": list(fallbacks)},
            "max": dict(off),
        },
        "custom_presets": [],
        "default_preset": "pro",
        "task_routing": {},
    }


def _make_fallback_test_app() -> Any:
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


async def _seed_fallback_providers_and_preset() -> None:
    """Insert system Provider+Model rows for primary/backup and an org preset.

    Org-level OrgSettings row overrides the system-seeded one so the
    default preset deterministically resolves to our 2-leg chain.
    """
    from sqlalchemy import delete, select

    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            # Wipe any prior rows from a previous run (the test DB persists).
            await session.execute(
                delete(DBModel).where(
                    DBModel.provider_id.in_(  # type: ignore[attr-defined]
                        select(DBProvider.id).where(
                            DBProvider.slug.in_(("primary", "backup"))  # type: ignore[attr-defined]
                        )
                    )
                )
            )
            await session.execute(
                delete(DBProvider).where(
                    DBProvider.slug.in_(("primary", "backup"))  # type: ignore[attr-defined]
                )
            )
            await session.execute(
                delete(OrgSettings).where(
                    OrgSettings.org_id == DEFAULT_ORG_ID,  # type: ignore[arg-type]
                    OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
                )
            )
            await session.commit()

            # System providers (org_id=None) for primary + backup.
            for slug in ("primary", "backup"):
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

            # Org-level preset row — overrides any system default; the `pro`
            # tier (default) resolves to chain [primary/m1, backup/m1].
            session.add(
                OrgSettings(
                    org_id=DEFAULT_ORG_ID,
                    key=MODEL_PRESETS_KEY,
                    value=_tiered_value(primary="primary/m1", fallbacks=["backup/m1"]),
                )
            )
            await session.commit()
    finally:
        await test_engine.dispose()


@pytest_asyncio.fixture
async def fallback_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """Client whose primary provider raises RateLimited; backup returns text."""
    await _ensure_default_user_and_membership()
    await _seed_fallback_providers_and_preset()

    primary = FauxProvider(provider_id="primary")

    def _raise_rate_limited(*args: Any, **kwargs: Any) -> Any:
        raise RateLimited("simulated 429", provider="primary", model="m1")

    primary.set_responses([_raise_rate_limited])

    backup = FauxProvider(provider_id="backup")
    backup.set_responses(
        [
            faux_assistant_message(
                [faux_text("answer from backup")],
                stop_reason="stop",
            ),
        ]
    )

    from cubebox.llm import builder as _builder

    real_build_provider = _builder.build_provider

    def _patched_build_provider(snap: Any, slug: str, **kw: Any) -> Any:
        if slug == "primary":
            return primary
        if slug == "backup":
            return backup
        return real_build_provider(snap, slug, **kw)

    monkeypatch.setattr("cubebox.llm.builder.build_provider", _patched_build_provider)

    app = _make_fallback_test_app()
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


@pytest.mark.asyncio
async def test_main_agent_fails_over(fallback_client: httpx.AsyncClient) -> None:
    """Primary RateLimited → backup serves; SSE shows model_failover event."""
    client = fallback_client
    ws_id = DEFAULT_WS_ID

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations",
        params={"title": "fallback-e2e"},
    )
    assert resp.status_code == 201, f"conversation creation failed: {resp.text}"
    conv_id = resp.json()["id"]

    events = await _stream_to_done(
        client,
        ws_id,
        conv_id,
        {"content": "Hello, please fail over."},
    )

    errors = [e for e in events if e.get("type") == "error"]
    assert not errors, f"unexpected error events: {errors!r}"

    failover_events = [e for e in events if e.get("type") == "model_failover"]
    assert failover_events, (
        f"expected a model_failover event; got types {[e.get('type') for e in events]!r}"
    )
    data = failover_events[0]["data"]
    assert data["failed_ref"] == "primary/m1", data
    assert data["next_ref"] == "backup/m1", data
    assert "simulated 429" in data["reason"], data

    text_events = [e for e in events if e.get("type") == "text_delta"]
    full_text = "".join((e.get("data") or {}).get("content", "") for e in text_events)
    assert "backup" in full_text.lower(), (
        f"expected backup answer; got: {full_text!r}\n"
        f"event types: {[e.get('type') for e in events]!r}"
    )
