"""Integration tests for workspace-level artifacts list + delete."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.db.engine import _build_database_url
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID

pytestmark = pytest.mark.asyncio

_STRANGER_ID = "usr-wsart-stranger"
_MY_CONV = "conv-wsart-mine"
_OTHER_CONV = "conv-wsart-other"
_MY_ART = "art-wsart-mine"
_OTHER_ART = "art-wsart-other"


@pytest_asyncio.fixture
async def _seed(client: TestClient) -> AsyncIterator[None]:
    """Seed two conversations + artifacts: one owned by the logged-in default
    user, one owned by a stranger. Depends on ``client`` so the default user
    row exists; the caller's id comes from ``/auth/me``.
    """
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    my_user_id = me.json()["id"]

    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as s:
            await s.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password, is_active,"
                    " is_superuser, is_verified, created_at, language)"
                    " VALUES (:id, :email, 'x', true, false, false, NOW(), 'en')"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {"id": _STRANGER_ID, "email": f"{_STRANGER_ID}@example.com"},
            )
            for conv_id, uid in ((_MY_CONV, my_user_id), (_OTHER_CONV, _STRANGER_ID)):
                await s.execute(
                    text(
                        "INSERT INTO conversations (id, org_id, workspace_id,"
                        " creator_user_id, title, has_messages, is_group_chat,"
                        " created_at, updated_at)"
                        " VALUES (:id, :org, :ws, :uid, 'seed', true, false, NOW(), NOW())"
                        " ON CONFLICT (id) DO NOTHING"
                    ),
                    {"id": conv_id, "org": DEFAULT_ORG_ID, "ws": DEFAULT_WS_ID, "uid": uid},
                )
            for art_id, conv_id, atype, name in (
                (_MY_ART, _MY_CONV, "html", "My Report"),
                (_OTHER_ART, _OTHER_CONV, "code", "Stranger Script"),
            ):
                await s.execute(
                    text(
                        "INSERT INTO artifacts (id, org_id, workspace_id, conversation_id,"
                        " name, artifact_type, path, entry_file, mime_type, description,"
                        " version, created_at, updated_at)"
                        " VALUES (:id, :org, :ws, :conv, :name, :atype, '/x/f', 'f',"
                        " 'text/plain', NULL, 1, NOW(), NOW())"
                        " ON CONFLICT (id) DO NOTHING"
                    ),
                    {
                        "id": art_id,
                        "org": DEFAULT_ORG_ID,
                        "ws": DEFAULT_WS_ID,
                        "conv": conv_id,
                        "name": name,
                        "atype": atype,
                    },
                )
            await s.commit()
        yield
    finally:
        async with maker() as s:
            await s.execute(
                text("DELETE FROM artifacts WHERE id IN (:a, :b)"), {"a": _MY_ART, "b": _OTHER_ART}
            )
            await s.execute(
                text("DELETE FROM conversations WHERE id IN (:a, :b)"),
                {"a": _MY_CONV, "b": _OTHER_CONV},
            )
            await s.execute(text("DELETE FROM users WHERE id = :id"), {"id": _STRANGER_ID})
            await s.commit()
        await engine.dispose()


def test_list_returns_only_accessible(_seed: None, client: TestClient) -> None:
    res = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/artifacts")
    assert res.status_code == 200
    ids = {a["id"] for a in res.json()["artifacts"]}
    assert _MY_ART in ids
    assert _OTHER_ART not in ids


def test_list_type_filter(_seed: None, client: TestClient) -> None:
    res = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/artifacts?type=html")
    assert res.status_code == 200
    assert all(a["artifact_type"] == "html" for a in res.json()["artifacts"])


def test_list_name_search(_seed: None, client: TestClient) -> None:
    res = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/artifacts?q=report")
    assert res.status_code == 200
    assert _MY_ART in {a["id"] for a in res.json()["artifacts"]}


def test_delete_accessible_artifact(_seed: None, client: TestClient) -> None:
    res = client.delete(f"/api/v1/ws/{DEFAULT_WS_ID}/artifacts/{_MY_ART}")
    assert res.status_code == 204
    after = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/artifacts")
    assert _MY_ART not in {a["id"] for a in after.json()["artifacts"]}


def test_delete_inaccessible_artifact_404(_seed: None, client: TestClient) -> None:
    res = client.delete(f"/api/v1/ws/{DEFAULT_WS_ID}/artifacts/{_OTHER_ART}")
    assert res.status_code == 404
