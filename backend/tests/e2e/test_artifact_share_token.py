"""Integration tests for the public artifact share-token + preview page (Task 11)."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from cubeplex.config import config as _cubeplex_config
from cubeplex.services.artifact_share import (
    SHARE_TTL_SECONDS,
    mint_share_token,
    resolve_share_token,
)
from tests.e2e.conftest import _build_database_url

pytestmark = pytest.mark.asyncio


_ORG_ID = "org-arts1"
_WS_ID = "ws-arts1"
_USER_ID = "usr-arts1"
_CONV_ID = "conv-arts1"
_ART_ID = "art-arts1"


@pytest_asyncio.fixture
async def _redis() -> AsyncIterator[Redis]:
    client: Redis = Redis.from_url(
        _cubeplex_config.get("redis.url", "redis://127.0.0.1:6379/0"),
        decode_responses=False,
    )
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def _seeded_artifact() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            await session.execute(
                text(
                    "INSERT INTO organizations (id, name, slug, created_at)"
                    " VALUES (:id, :id, :id, NOW()) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": _ORG_ID},
            )
            await session.execute(
                text(
                    "INSERT INTO workspaces (id, org_id, name, created_at)"
                    " VALUES (:id, :org, :id, NOW()) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": _WS_ID, "org": _ORG_ID},
            )
            await session.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password, is_active,"
                    " is_superuser, is_verified, created_at, language)"
                    " VALUES (:id, :email, 'x', true, false, false, NOW(), 'en')"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {"id": _USER_ID, "email": f"{_USER_ID}@example.com"},
            )
            await session.execute(
                text(
                    "INSERT INTO conversations (id, org_id, workspace_id, creator_user_id,"
                    " title, has_messages, is_group_chat, reasoning, attributes,"
                    " created_at, updated_at)"
                    " VALUES (:id, :org, :ws, :uid, 'share-test', true, false,"
                    " '{}'::jsonb, '{}'::jsonb, NOW(), NOW())"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {"id": _CONV_ID, "org": _ORG_ID, "ws": _WS_ID, "uid": _USER_ID},
            )
            await session.execute(
                text(
                    "INSERT INTO artifacts (id, org_id, workspace_id, conversation_id,"
                    " name, artifact_type, path, entry_file, mime_type, description,"
                    " version, created_at, updated_at)"
                    " VALUES (:id, :org, :ws, :conv, 'report', 'document',"
                    " '/x/report.md', 'report.md', 'text/markdown', NULL, 1,"
                    " NOW(), NOW()) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": _ART_ID, "org": _ORG_ID, "ws": _WS_ID, "conv": _CONV_ID},
            )
            await session.commit()
        try:
            yield maker
        finally:
            async with maker() as session:
                await session.execute(text("DELETE FROM artifacts WHERE id = :id"), {"id": _ART_ID})
                await session.execute(
                    text("DELETE FROM conversations WHERE id = :id"), {"id": _CONV_ID}
                )
                await session.commit()
    finally:
        await engine.dispose()


async def test_mint_and_resolve_roundtrip(
    _redis: Redis, _seeded_artifact: async_sessionmaker[AsyncSession]
) -> None:
    """The service-level mint must write a key that resolve can read back."""
    key_prefix = "share-test-prefix"
    nonce = await mint_share_token(
        redis=_redis,
        key_prefix=key_prefix,
        org_id=_ORG_ID,
        workspace_id=_WS_ID,
        conversation_id=_CONV_ID,
        artifact_id=_ART_ID,
        version=1,
        ttl_seconds=60,
    )
    assert nonce and len(nonce) == 64

    payload = await resolve_share_token(redis=_redis, key_prefix=key_prefix, nonce=nonce)
    assert payload is not None
    assert payload["org_id"] == _ORG_ID
    assert payload["workspace_id"] == _WS_ID
    assert payload["conversation_id"] == _CONV_ID
    assert payload["artifact_id"] == _ART_ID
    assert payload["version"] == 1

    await _redis.delete(f"{key_prefix}:share:{nonce}")


async def test_resolve_returns_none_for_unknown_nonce(_redis: Redis) -> None:
    out = await resolve_share_token(
        redis=_redis, key_prefix="share-test-prefix", nonce="does-not-exist"
    )
    assert out is None


async def test_default_ttl_constant_is_seven_days() -> None:
    assert SHARE_TTL_SECONDS == 60 * 60 * 24 * 7
