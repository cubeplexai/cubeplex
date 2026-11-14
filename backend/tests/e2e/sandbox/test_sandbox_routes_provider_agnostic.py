"""E2E tests exercising provider-agnostic workspace/admin sandbox routes.

Runs real FastAPI routes + real PostgreSQL repositories + real ``SandboxManager``
configured with ``LocalSandboxProvider`` (no internal route helpers mocked).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.db.engine import _build_database_url
from cubeplex.models.user_sandbox import UserSandbox
from cubeplex.sandbox.manager import build_sandbox_pvc_name, get_sandbox_manager
from cubeplex.sandbox.providers.local import LocalSandboxProvider
from cubeplex.sandbox.registry import ProviderRegistry


@pytest.fixture
def local_provider_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> LocalSandboxProvider:
    provider = LocalSandboxProvider(base_workdir=str(tmp_path))
    registry = ProviderRegistry({"local": provider}, default_name="local")
    manager = get_sandbox_manager()
    monkeypatch.setattr(manager, "_registry", registry)
    monkeypatch.setattr(
        "cubeplex.api.routes.v1.admin_sandbox_policy.get_provider_registry",
        lambda: registry,
    )
    return provider


async def _sandbox_rows(ws_id: str) -> list[UserSandbox]:
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            stmt = select(UserSandbox).where(UserSandbox.workspace_id == ws_id)  # type: ignore[arg-type]
            return list((await session.execute(stmt)).scalars().all())
    finally:
        await engine.dispose()


async def test_local_provider_status_capability_gating_files_and_share(
    admin_client_with_user_id: tuple[httpx.AsyncClient, str, str],
    local_provider_env: LocalSandboxProvider,
) -> None:
    client, ws_id, user_id = admin_client_with_user_id

    # 1. Status before any sandbox exists exposes provider & capabilities.
    status_resp = await client.get(f"/api/v1/ws/{ws_id}/sandbox/status")
    assert status_resp.status_code == 200, status_resp.text
    status_body = status_resp.json()
    assert status_body["status"] == "absent"
    assert status_body["provider"] == "local"
    assert status_body["capabilities"] == {
        "background": True,
        "reconnect": False,
        "persistent_volume": True,
        "pause": False,
        "reset_rootfs": False,
        "terminal": False,
        "browser": False,
        "egress_proxy": False,
        "network_policy": False,
    }

    # Topic sandbox status mirrors capabilities cleanly.
    topic_resp = await client.post(
        f"/api/v1/ws/{ws_id}/topics",
        json={"title": "Provider status topic"},
    )
    assert topic_resp.status_code in (200, 201), topic_resp.text
    topic_id = topic_resp.json()["topic"]["id"]
    topic_status = await client.get(f"/api/v1/ws/{ws_id}/topics/{topic_id}/sandbox")
    assert topic_status.status_code == 200, topic_status.text
    assert topic_status.json()["provider"] == "local"
    assert topic_status.json()["capabilities"] == status_body["capabilities"]

    # 2. Unsupported terminal & browser fail fast with 501 BEFORE provisioning.
    term_resp = await client.get(f"/api/v1/ws/{ws_id}/sandbox/terminal")
    assert term_resp.status_code == 501, term_resp.text
    browser_resp = await client.get(f"/api/v1/ws/{ws_id}/browser/live-view")
    assert browser_resp.status_code == 501, browser_resp.text
    assert await _sandbox_rows(ws_id) == []

    # 3. Provision a local sandbox via upload, then exercise list/read/download/413/404.
    volume_key = build_sandbox_pvc_name(
        get_sandbox_manager()._volume_pvc_prefix,
        ws_id,
        "user",
        user_id,
    )
    expected_root = Path(local_provider_env.default_workdir) / volume_key
    hello_path = expected_root / "notes" / "hello.txt"

    up_resp = await client.post(
        f"/api/v1/ws/{ws_id}/sandbox/files/upload",
        params={"path": str(hello_path)},
        files={"file": ("hello.txt", b"hello provider world", "text/plain")},
    )
    assert up_resp.status_code == 200, up_resp.text
    assert up_resp.json()["path"] == str(hello_path)

    rows = await _sandbox_rows(ws_id)
    assert len(rows) == 1
    assert rows[0].provider == "local"

    list_resp = await client.get(f"/api/v1/ws/{ws_id}/sandbox/files")
    assert list_resp.status_code == 200, list_resp.text
    assert [entry["name"] for entry in list_resp.json()] == ["notes"]

    notes_resp = await client.get(
        f"/api/v1/ws/{ws_id}/sandbox/files",
        params={"path": str(expected_root / "notes")},
    )
    assert notes_resp.status_code == 200, notes_resp.text
    assert [entry["name"] for entry in notes_resp.json()] == ["hello.txt"]

    content_resp = await client.get(
        f"/api/v1/ws/{ws_id}/sandbox/files/content",
        params={"path": str(hello_path)},
    )
    assert content_resp.status_code == 200, content_resp.text
    assert content_resp.json()["content"] == "hello provider world"

    dl_resp = await client.get(
        f"/api/v1/ws/{ws_id}/sandbox/files/download",
        params={"path": str(hello_path)},
    )
    assert dl_resp.status_code == 200, dl_resp.text
    assert dl_resp.content == b"hello provider world"

    # Outside workdir -> 400; missing -> 404; >1MB inline preview -> 413.
    bad_path_resp = await client.get(
        f"/api/v1/ws/{ws_id}/sandbox/files/content",
        params={"path": "/etc/passwd"},
    )
    assert bad_path_resp.status_code == 400

    missing_resp = await client.get(
        f"/api/v1/ws/{ws_id}/sandbox/files/content",
        params={"path": str(expected_root / "missing.txt")},
    )
    assert missing_resp.status_code == 404

    big_path = expected_root / "big.txt"
    big_bytes = b"a" * 1_100_000
    up_big = await client.post(
        f"/api/v1/ws/{ws_id}/sandbox/files/upload",
        params={"path": str(big_path)},
        files={"file": ("big.txt", big_bytes, "text/plain")},
    )
    assert up_big.status_code == 200, up_big.text
    too_large_resp = await client.get(
        f"/api/v1/ws/{ws_id}/sandbox/files/content",
        params={"path": str(big_path)},
    )
    assert too_large_resp.status_code == 413

    # 4. Preview-token + public share download stream and map missing file to 404.
    docx_path = expected_root / "report.docx"
    up_doc = await client.post(
        f"/api/v1/ws/{ws_id}/sandbox/files/upload",
        params={"path": str(docx_path)},
        files={"file": ("report.docx", b"docx-binary-content", "application/octet-stream")},
    )
    assert up_doc.status_code == 200, up_doc.text

    otk_resp = await client.post(
        f"/api/v1/ws/{ws_id}/sandbox/files/preview-token",
        params={"path": str(docx_path)},
    )
    assert otk_resp.status_code == 200, otk_resp.text
    dl_path = httpx.URL(otk_resp.json()["download_url"]).path
    public_dl = await client.get(dl_path)
    assert public_dl.status_code == 200, public_dl.text
    assert public_dl.content == b"docx-binary-content"

    docx_path.unlink()
    missing_dl = await client.get(dl_path)
    assert missing_dl.status_code == 404

    # 5. Admin sandbox policy surfaces warning when network_policy=False.
    policy_put = await client.put(
        "/api/v1/admin/sandbox-policy",
        json={
            "default_image": "local",
            "network_default_action": "deny",
            "network_rules": [{"action": "deny", "target": "example.com"}],
        },
    )
    assert policy_put.status_code == 200, policy_put.text
    assert any("does not enforce" in w for w in policy_put.json()["warnings"])
