"""E2E tests for the artifact file-list endpoint."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.db.engine import _build_database_url
from cubebox.objectstore import get_objectstore_client
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID

pytestmark = pytest.mark.asyncio

_CONV = "conv-artfiles"
_ART = "art-artfiles"
_PREFIX = f"artifacts/{_CONV}/{_ART}/v1/"


@pytest_asyncio.fixture
async def _seed(client: TestClient) -> AsyncIterator[None]:
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    my_user_id = me.json()["id"]

    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as s:
            await s.execute(
                text(
                    "INSERT INTO conversations (id, org_id, workspace_id,"
                    " creator_user_id, title, has_messages, is_group_chat,"
                    " created_at, updated_at)"
                    " VALUES (:id, :org, :ws, :uid, 'seed', true, false, NOW(), NOW())"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {"id": _CONV, "org": DEFAULT_ORG_ID, "ws": DEFAULT_WS_ID, "uid": my_user_id},
            )
            await s.execute(
                text(
                    "INSERT INTO artifacts (id, org_id, workspace_id, conversation_id,"
                    " name, artifact_type, path, entry_file, mime_type, description,"
                    " version, created_at, updated_at)"
                    " VALUES (:id, :org, :ws, :conv, 'Charts', 'image',"
                    " '/workspace/charts', NULL, NULL, NULL, 1, NOW(), NOW())"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {
                    "id": _ART,
                    "org": DEFAULT_ORG_ID,
                    "ws": DEFAULT_WS_ID,
                    "conv": _CONV,
                },
            )
            await s.commit()

        store = get_objectstore_client()
        # Unsorted on purpose: 2_ before 1_ to prove the endpoint sorts.
        for name, data in (
            ("2_second.png", b"\x89PNG\r\n\x1a\n-second"),
            ("1_first.png", b"\x89PNG\r\n\x1a\n-first"),
            ("3_third.png", b"\x89PNG\r\n\x1a\n-third"),
            ("script.py", b"print(1)"),
        ):
            await store.upload_file(f"{_PREFIX}{name}", data)
        yield
    finally:
        store = get_objectstore_client()
        for name in ("2_second.png", "1_first.png", "3_third.png", "script.py"):
            try:
                await store.delete_file(f"{_PREFIX}{name}")
            except Exception:
                pass
        async with maker() as s:
            await s.execute(text("DELETE FROM artifacts WHERE id = :id"), {"id": _ART})
            await s.execute(text("DELETE FROM conversations WHERE id = :id"), {"id": _CONV})
            await s.commit()
        await engine.dispose()


def test_files_filter_image_sorted(_seed: None, client: TestClient) -> None:
    res = client.get(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{_CONV}/artifacts/{_ART}/files",
        params={"filter": "image"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["version"] == 1
    # Sorted ascending; non-image script.py excluded; prefix stripped.
    assert body["files"] == ["1_first.png", "2_second.png", "3_third.png"]


def test_files_no_filter_returns_all(_seed: None, client: TestClient) -> None:
    res = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{_CONV}/artifacts/{_ART}/files")
    assert res.status_code == 200, res.text
    names = res.json()["files"]
    assert "script.py" in names
    assert names == sorted(names)


def test_files_missing_artifact_404(client: TestClient) -> None:
    res = client.get(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{_CONV}/artifacts/art-nonexistent/files"
    )
    assert res.status_code == 404


def test_files_cross_workspace_404(_seed: None, client: TestClient) -> None:
    # _seed is owned by DEFAULT_WS_ID; query a foreign workspace id.
    res = client.get(
        f"/api/v1/ws/ws-foreign/conversations/{_CONV}/artifacts/{_ART}/files",
        params={"filter": "image"},
    )
    assert res.status_code == 404
