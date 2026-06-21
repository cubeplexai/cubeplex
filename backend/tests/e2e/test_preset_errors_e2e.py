"""HTTP error matrix for preset / thinking on the chat endpoint.

These tests verify that LLMConfigError subclasses surface as HTTP errors
(not as mid-stream SSE error events) when ``send_message`` validates the
preset synchronously before scheduling the background run.

Two error paths (an unknown ``model_key`` is NOT an error — it falls back to
the workspace default; see ``test_unknown_model_key_falls_back_to_default`` in
test_preset_switching_e2e.py):

* ``broken_preset`` (400) — default preset's chain references a
  non-existent provider/model.
* ``no_default_preset`` (500) — no ``model_presets`` row at all
  (neither org-level nor system-level).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import cubebox.db as _cubebox_db
from cubebox.api.app import create_app
from cubebox.db.engine import _build_database_url, engine
from cubebox.db.session import get_session
from cubebox.models.conversation import Conversation
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


def _tiered_value(*, primary: str, fallbacks: list[str] | None = None) -> dict[str, Any]:
    """A valid tiered ModelPresetsConfig value: only `pro` enabled (default)."""
    off = {"enabled": False, "primary": None, "fallbacks": []}
    return {
        "tiers": {
            "lite": dict(off),
            "flash": dict(off),
            "pro": {"enabled": True, "primary": primary, "fallbacks": list(fallbacks or [])},
            "max": dict(off),
        },
        "custom_presets": [],
        "default_preset": "pro",
        "task_routing": {},
    }


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


async def _wipe_preset_seed_rows() -> None:
    """Remove any provider+preset rows seeded by prior tests / bootstrap."""
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            from sqlalchemy import select

            await session.execute(
                delete(DBModel).where(
                    DBModel.provider_id.in_(  # type: ignore[attr-defined]
                        select(DBProvider.id).where(
                            DBProvider.slug.in_(("alpha", "ghost"))  # type: ignore[attr-defined]
                        )
                    )
                )
            )
            await session.execute(
                delete(DBProvider).where(
                    DBProvider.slug.in_(("alpha", "ghost"))  # type: ignore[attr-defined]
                )
            )
            # Delete BOTH org-level and system-level model_presets rows.
            await session.execute(
                delete(OrgSettings).where(
                    OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
                )
            )
            await session.commit()
    finally:
        await test_engine.dispose()


async def _seed_broken_default_preset() -> None:
    """Default preset whose chain references a non-existent provider/model."""
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            session.add(
                OrgSettings(
                    org_id=DEFAULT_ORG_ID,
                    key=MODEL_PRESETS_KEY,
                    value=_tiered_value(primary="ghost/x"),
                )
            )
            await session.commit()
    finally:
        await test_engine.dispose()


@pytest_asyncio.fixture
async def preset_error_client() -> AsyncIterator[httpx.AsyncClient]:
    """Authenticated client; each test seeds its own preset / provider state."""
    await _ensure_default_user_and_membership()

    app = _make_test_app()
    app.state.deployment_mode = "multi_tenant"

    async with _lifespan_context(app):
        # Wipe AFTER lifespan startup so the seeder's freshly-committed
        # model_presets / system-provider rows are cleared. Each test then
        # seeds its own state (or, for no_default_preset, seeds nothing).
        await _wipe_preset_seed_rows()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, DEFAULT_TEST_EMAIL, DEFAULT_TEST_PASSWORD)
            yield c

    await engine.dispose()


async def _create_conversation(client: httpx.AsyncClient, ws_id: str, title: str) -> str:
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations",
        params={"title": title},
    )
    assert resp.status_code == 201, f"conversation creation failed: {resp.text}"
    conv_id: str = resp.json()["id"]
    return conv_id


async def _read_conversation_state(conv_id: str) -> tuple[bool, Any]:
    """Return (has_messages, updated_at) straight from the DB.

    Used to assert that a preset-validation failure leaves the conversation
    row untouched — no orphan ``has_messages=True`` / bumped ``updated_at``
    for a turn that never ran. ``updated_at`` is returned as the raw
    SQLAlchemy datetime so callers can compare equality without ISO-format
    rounding surprises.
    """
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            row = await session.get(Conversation, conv_id)
            assert row is not None, f"conversation {conv_id} missing from DB"
            return bool(row.has_messages), row.updated_at
    finally:
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_broken_preset_400_lists_refs(preset_error_client: httpx.AsyncClient) -> None:
    """Default preset references a missing provider/model → 400 broken_preset."""
    await _seed_broken_default_preset()

    client = preset_error_client
    ws_id = DEFAULT_WS_ID
    conv_id = await _create_conversation(client, ws_id, "broken-preset")
    before_has_messages, before_updated_at = await _read_conversation_state(conv_id)

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        json={"content": "hello"},
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body.get("error_code") == "broken_preset", body
    # The missing ref "ghost/x" should appear somewhere in the response
    # (either the message or the details field).
    haystack = f"{body.get('message', '')} {body.get('details', '')}"
    assert "ghost/x" in haystack, body

    after_has_messages, after_updated_at = await _read_conversation_state(conv_id)
    assert after_has_messages == before_has_messages, (before_has_messages, after_has_messages)
    assert after_updated_at == before_updated_at, (before_updated_at, after_updated_at)


@pytest.mark.asyncio
async def test_no_default_preset_500(preset_error_client: httpx.AsyncClient) -> None:
    """No model_presets row at all → 500 no_default_preset."""
    # Fixture already wiped all model_presets rows; no seeding here.

    client = preset_error_client
    ws_id = DEFAULT_WS_ID
    conv_id = await _create_conversation(client, ws_id, "no-default-preset")
    before_has_messages, before_updated_at = await _read_conversation_state(conv_id)

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        json={"content": "hello"},
    )
    assert resp.status_code == 500, resp.text
    body = resp.json()
    assert body.get("error_code") == "no_default_preset", body

    after_has_messages, after_updated_at = await _read_conversation_state(conv_id)
    assert after_has_messages == before_has_messages, (before_has_messages, after_has_messages)
    assert after_updated_at == before_updated_at, (before_updated_at, after_updated_at)


@pytest.mark.asyncio
async def test_unknown_key_with_no_default_still_500_before_mutation(
    preset_error_client: httpx.AsyncClient,
) -> None:
    """An unknown model_key falls back to the default — but if there is no
    default either, it must STILL fail synchronously (500 no_default_preset)
    before any mutation, not slip into the stream after the row was changed."""
    # Fixture already wiped all model_presets rows; no seeding here.
    client = preset_error_client
    ws_id = DEFAULT_WS_ID
    conv_id = await _create_conversation(client, ws_id, "unknown-no-default")
    before_has_messages, before_updated_at = await _read_conversation_state(conv_id)

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        json={"content": "hello", "model_key": "ghost"},
    )
    assert resp.status_code == 500, resp.text
    assert resp.json().get("error_code") == "no_default_preset", resp.text

    after_has_messages, after_updated_at = await _read_conversation_state(conv_id)
    assert after_has_messages == before_has_messages, (before_has_messages, after_has_messages)
    assert after_updated_at == before_updated_at, (before_updated_at, after_updated_at)
