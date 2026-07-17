"""E2E HTTP contract tests for conversation attachments."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def _make_conversation(client: httpx.AsyncClient, ws_id: str) -> str:
    resp = await client.post(f"/api/v1/ws/{ws_id}/conversations", params={"title": "atta-test"})
    resp.raise_for_status()
    return resp.json()["id"]


def _atta_url(ws_id: str, conv_id: str, suffix: str = "") -> str:
    base = f"/api/v1/ws/{ws_id}/conversations/{conv_id}/attachments"
    return f"{base}{suffix}"


async def test_upload_png_returns_metadata(member_client_org_a, sample_png_bytes) -> None:
    client, ws_id = member_client_org_a
    conv_id = await _make_conversation(client, ws_id)
    files = {"file": ("chart.png", sample_png_bytes, "image/png")}
    resp = await client.post(_atta_url(ws_id, conv_id), files=files)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["filename"] == "chart.png"
    assert body["kind"] == "image"
    assert body["mime_type"] == "image/png"
    assert body["width"] == 100 and body["height"] == 100
    assert body["status"] == "pending"
    assert body["thumbnail_url"] and body["download_url"]


async def test_upload_pdf_no_dimensions(member_client_org_a, sample_pdf_bytes) -> None:
    client, ws_id = member_client_org_a
    conv_id = await _make_conversation(client, ws_id)
    files = {"file": ("spec.pdf", sample_pdf_bytes, "application/pdf")}
    resp = await client.post(_atta_url(ws_id, conv_id), files=files)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "document"
    assert body["width"] is None and body["height"] is None
    assert body["thumbnail_url"] is None


async def test_upload_rejects_disallowed_mime(member_client_org_a) -> None:
    client, ws_id = member_client_org_a
    conv_id = await _make_conversation(client, ws_id)
    files = {"file": ("evil.exe", b"MZ\x90\x00", "application/x-msdownload")}
    resp = await client.post(_atta_url(ws_id, conv_id), files=files)
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "INVALID_MIME_TYPE"


async def test_list_default_returns_all(member_client_org_a, sample_png_bytes) -> None:
    client, ws_id = member_client_org_a
    conv_id = await _make_conversation(client, ws_id)
    files = {"file": ("a.png", sample_png_bytes, "image/png")}
    await client.post(_atta_url(ws_id, conv_id), files=files)
    resp = await client.get(_atta_url(ws_id, conv_id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["attachments"][0]["status"] == "pending"


async def test_list_status_filter(member_client_org_a, sample_png_bytes) -> None:
    client, ws_id = member_client_org_a
    conv_id = await _make_conversation(client, ws_id)
    files = {"file": ("a.png", sample_png_bytes, "image/png")}
    await client.post(_atta_url(ws_id, conv_id), files=files)
    resp = await client.get(_atta_url(ws_id, conv_id), params={"status": "attached"})
    assert resp.json()["total"] == 0


async def test_delete_pending_succeeds(member_client_org_a, sample_png_bytes) -> None:
    client, ws_id = member_client_org_a
    conv_id = await _make_conversation(client, ws_id)
    files = {"file": ("a.png", sample_png_bytes, "image/png")}
    aid = (await client.post(_atta_url(ws_id, conv_id), files=files)).json()["id"]
    resp = await client.delete(_atta_url(ws_id, conv_id, f"/{aid}"))
    assert resp.status_code == 204
    listing = (await client.get(_atta_url(ws_id, conv_id))).json()
    assert listing["total"] == 0


async def test_thumbnail_for_image(member_client_org_a, sample_png_bytes) -> None:
    client, ws_id = member_client_org_a
    conv_id = await _make_conversation(client, ws_id)
    files = {"file": ("a.png", sample_png_bytes, "image/png")}
    aid = (await client.post(_atta_url(ws_id, conv_id), files=files)).json()["id"]
    resp = await client.get(_atta_url(ws_id, conv_id, f"/{aid}/thumbnail"))
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"
    assert len(resp.content) > 0


async def test_thumbnail_404_for_document(member_client_org_a, sample_pdf_bytes) -> None:
    client, ws_id = member_client_org_a
    conv_id = await _make_conversation(client, ws_id)
    files = {"file": ("spec.pdf", sample_pdf_bytes, "application/pdf")}
    aid = (await client.post(_atta_url(ws_id, conv_id), files=files)).json()["id"]
    resp = await client.get(_atta_url(ws_id, conv_id, f"/{aid}/thumbnail"))
    assert resp.status_code == 404


async def test_cross_workspace_returns_404(
    member_client_org_a, member_client_org_b, sample_png_bytes
) -> None:
    client_a, ws_a = member_client_org_a
    client_b, ws_b = member_client_org_b
    conv_a = await _make_conversation(client_a, ws_a)
    files = {"file": ("a.png", sample_png_bytes, "image/png")}
    aid = (await client_a.post(_atta_url(ws_a, conv_a), files=files)).json()["id"]
    # client_b uses its OWN workspace id but conv_a id from org A → expect 4xx
    resp = await client_b.get(_atta_url(ws_b, conv_a, f"/{aid}"))
    assert resp.status_code in (403, 404)


async def test_upload_filename_path_traversal_is_sanitized(
    member_client_org_a, sample_png_bytes
) -> None:
    """Multipart filename like ../../etc/passwd must NOT escape the upload prefix."""
    from sqlalchemy import select as sa_select

    from cubeplex.db.engine import async_session_maker
    from cubeplex.models import Attachment

    client, ws_id = member_client_org_a
    conv_id = await _make_conversation(client, ws_id)
    files = {"file": ("../../etc/passwd.png", sample_png_bytes, "image/png")}
    resp = await client.post(_atta_url(ws_id, conv_id), files=files)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Persisted name is the safe basename, not the traversal path
    assert body["filename"] == "passwd.png"
    aid = body["id"]

    # Sandbox path stays under the per-conversation prefix (no traversal)
    async with async_session_maker() as session:
        row = (
            await session.execute(sa_select(Attachment).where(Attachment.id == aid))
        ).scalar_one()
        assert row.sandbox_path == f"/workspace/uploads/{conv_id}/{aid}/passwd.png"
        assert "../" not in row.sandbox_path
        assert "../" not in row.object_key
        assert row.object_key.endswith(f"/{aid}/original/passwd.png")
