"""E2E tests for one-time-token (OTK) artifact preview."""

from collections.abc import AsyncIterator
from urllib.parse import quote

import httpx
import pytest_asyncio
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.db.engine import _build_database_url
from cubeplex.models import Artifact, Conversation, Membership, Role, Workspace
from cubeplex.objectstore import get_objectstore_client
from tests.e2e.conftest import (
    _lifespan_context,
    _login_and_attach,
    _make_isolated_user,
)

DOCX_BYTES = (
    b"PK\x03\x04\x14\x00\x00\x00\x00\x00"
    b"\x00\x00!\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x13\x00\x00\x00[Content_Types].xml"
)


async def _seed_office_artifact(
    workspace_id: str,
    *,
    filename: str = "report.docx",
    file_bytes: bytes = DOCX_BYTES,
) -> tuple[str, str]:
    """Seed a conversation + artifact + upload file to object storage.

    Returns (artifact_id, conversation_id).
    """
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            ws = await session.get(Workspace, workspace_id)
            assert ws is not None
            org_id = ws.org_id

            stmt = sa_select(Membership).where(Membership.workspace_id == workspace_id)
            mem = (await session.execute(stmt)).scalars().first()
            assert mem is not None
            user_id = str(mem.user_id)

            conv = Conversation(
                org_id=org_id,
                workspace_id=workspace_id,
                creator_user_id=user_id,
                title="office preview test",
            )
            session.add(conv)
            await session.flush()

            artifact = Artifact(
                org_id=org_id,
                workspace_id=workspace_id,
                conversation_id=conv.id,
                name=filename,
                artifact_type="file",
                path=f"/workspace/{filename}",
                entry_file=filename,
                mime_type=(
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
            )
            session.add(artifact)
            await session.flush()
            artifact_id = artifact.id
            conv_id = conv.id
            await session.commit()
    finally:
        await test_engine.dispose()

    store = get_objectstore_client()
    key = f"artifacts/{conv_id}/{artifact_id}/v1/{filename}"
    await store.upload_file(key, file_bytes)
    return artifact_id, conv_id


@pytest_asyncio.fixture
async def office_client() -> AsyncIterator[tuple[httpx.AsyncClient, str, str, str]]:
    """Yield (client, workspace_id, artifact_id, conversation_id)."""
    app, email, password, workspace_id = await _make_isolated_user(Role.MEMBER)
    artifact_id, conv_id = await _seed_office_artifact(workspace_id)
    app.state.deployment_mode = "multi_tenant"
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password)
            yield c, workspace_id, artifact_id, conv_id


class TestOTKPreviewToken:
    """Tests for POST preview-token endpoint."""

    async def test_issue_token_returns_urls(
        self, office_client: tuple[httpx.AsyncClient, str, str, str]
    ) -> None:
        client, ws_id, art_id, conv_id = office_client
        url = f"/api/v1/ws/{ws_id}/conversations/{conv_id}/artifacts/{art_id}/preview-token"
        r = await client.post(url)
        assert r.status_code == 200
        body = r.json()
        assert "download_url" in body
        assert "viewer_url" in body
        assert "view.officeapps.live.com" in body["viewer_url"]
        # viewer_url contains download_url percent-encoded as the ?src= query param
        assert quote(body["download_url"], safe="") in body["viewer_url"]

    async def test_token_not_supported_for_non_office(
        self, office_client: tuple[httpx.AsyncClient, str, str, str]
    ) -> None:
        """Seed a .txt artifact and verify preview-token rejects it."""
        client, ws_id, _, _ = office_client
        art_id, conv_id = await _seed_office_artifact(
            ws_id, filename="notes.txt", file_bytes=b"hello"
        )
        url = f"/api/v1/ws/{ws_id}/conversations/{conv_id}/artifacts/{art_id}/preview-token"
        r = await client.post(url)
        assert r.status_code == 400


class TestOTKPublicDownload:
    """Tests for GET /public/artifacts/dl/{token}/{filename}."""

    async def test_download_allows_repeated_fetches_within_ttl(
        self, office_client: tuple[httpx.AsyncClient, str, str, str]
    ) -> None:
        """Office Online Viewer pulls the URL more than once (probe +
        conversion nodes), so the token must stay valid for its full TTL."""
        client, ws_id, art_id, conv_id = office_client
        # Issue token
        token_url = f"/api/v1/ws/{ws_id}/conversations/{conv_id}/artifacts/{art_id}/preview-token"
        r = await client.post(token_url)
        assert r.status_code == 200
        download_url = r.json()["download_url"]
        # Extract relative path from absolute URL
        path = download_url.replace("http://test", "")

        # First GET — succeeds
        r1 = await client.get(path)
        assert r1.status_code == 200
        assert len(r1.content) > 0

        # Second GET — token still valid within TTL, same bytes
        r2 = await client.get(path)
        assert r2.status_code == 200
        assert r2.content == r1.content

    async def test_invalid_token_returns_404(
        self, office_client: tuple[httpx.AsyncClient, str, str, str]
    ) -> None:
        client, *_ = office_client
        r = await client.get("/api/v1/public/artifacts/dl/bogus-token/report.docx")
        assert r.status_code == 404

    async def test_filename_mismatch_returns_404(
        self, office_client: tuple[httpx.AsyncClient, str, str, str]
    ) -> None:
        client, ws_id, art_id, conv_id = office_client
        token_url = f"/api/v1/ws/{ws_id}/conversations/{conv_id}/artifacts/{art_id}/preview-token"
        r = await client.post(token_url)
        assert r.status_code == 200
        download_url = r.json()["download_url"]
        path = download_url.replace("http://test", "")
        # Replace filename
        tampered = path.rsplit("/", 1)[0] + "/evil.docx"
        r2 = await client.get(tampered)
        assert r2.status_code == 404
