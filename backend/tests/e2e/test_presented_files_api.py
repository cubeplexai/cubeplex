"""E2E: present_file storage + content API."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest

from cubeplex.db.engine import async_session_maker
from cubeplex.models import Workspace
from cubeplex.repositories.presented_file import PresentedFileRepository
from cubeplex.services.presented_files import PresentedFilePathError, PresentedFileService

pytestmark = pytest.mark.asyncio


async def _org_for_ws(ws_id: str) -> str:
    async with async_session_maker() as session:
        ws = await session.get(Workspace, ws_id)
        assert ws is not None
        return ws.org_id


@dataclass
class _ExecResult:
    output: str
    exit_code: int | None = 0


@dataclass
class _FakeSandbox:
    files: dict[str, bytes] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return "fake"

    @property
    def workdir(self) -> str:
        return "/workspace"

    async def execute(self, command: str, **kwargs: object) -> _ExecResult:
        """Satisfy present_file's pre-download size/realpath check."""
        del kwargs
        # Match the realpath/stat script: last existing path under /workspace in files.
        for path, content in self.files.items():
            if path in command or path.rsplit("/", 1)[-1] in command:
                if not path.startswith("/workspace"):
                    return _ExecResult(output="ESCAPE", exit_code=3)
                return _ExecResult(output=str(len(content)), exit_code=0)
        # Fallback: if command mentions a known absolute path key
        for path, content in self.files.items():
            if f"'{path}'" in command or f'"{path}"' in command or path in command:
                return _ExecResult(output=str(len(content)), exit_code=0)
        return _ExecResult(output="NOT_FOUND", exit_code=2)

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        del files

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        out: list[tuple[str, bytes]] = []
        for p in paths:
            if p not in self.files:
                raise FileNotFoundError(p)
            out.append((p, self.files[p]))
        return out

    async def close(self) -> None:
        return None


async def _make_conversation(client: httpx.AsyncClient, ws_id: str) -> str:
    resp = await client.post(f"/api/v1/ws/{ws_id}/conversations", params={"title": "present-test"})
    resp.raise_for_status()
    return resp.json()["id"]


async def test_present_then_download_content(
    member_client_org_a: tuple[httpx.AsyncClient, str],
    sample_png_bytes: bytes,
) -> None:
    client, ws_id = member_client_org_a
    conv_id = await _make_conversation(client, ws_id)
    path = "/workspace/tmp/lark-auth-qr.png"
    sandbox = _FakeSandbox(files={path: sample_png_bytes})

    org_id = await _org_for_ws(ws_id)
    async with async_session_maker() as session:
        repo = PresentedFileRepository(session, org_id=org_id, workspace_id=ws_id)
        service = PresentedFileService(repo=repo)
        presented = await service.present_from_sandbox(
            sandbox,  # type: ignore[arg-type]
            conversation_id=conv_id,
            path=path,
            caption="QR",
        )
        file_id = presented.id
        assert presented.kind == "image"
        assert presented.caption == "QR"

    content_url = f"/api/v1/ws/{ws_id}/conversations/{conv_id}/presented-files/{file_id}/content"
    resp = await client.get(content_url)
    assert resp.status_code == 200, resp.text
    assert resp.content == sample_png_bytes
    assert "image/png" in resp.headers.get("content-type", "")

    thumb = await client.get(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/presented-files/{file_id}/thumbnail"
    )
    assert thumb.status_code == 200, thumb.text
    assert thumb.headers.get("content-type") == "image/webp"
    assert len(thumb.content) > 0


async def test_present_content_404_for_unknown_id(
    member_client_org_a: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = member_client_org_a
    conv_id = await _make_conversation(client, ws_id)
    resp = await client.get(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/presented-files/pfile-doesnotexist00/content"
    )
    assert resp.status_code == 404


async def test_present_rejects_path_outside_workspace(
    member_client_org_a: tuple[httpx.AsyncClient, str],
    sample_png_bytes: bytes,
) -> None:
    client, ws_id = member_client_org_a
    conv_id = await _make_conversation(client, ws_id)
    sandbox = _FakeSandbox(files={"/etc/passwd": sample_png_bytes})
    org_id = await _org_for_ws(ws_id)

    async with async_session_maker() as session:
        repo = PresentedFileRepository(session, org_id=org_id, workspace_id=ws_id)
        service = PresentedFileService(repo=repo)
        with pytest.raises(PresentedFilePathError):
            await service.present_from_sandbox(
                sandbox,  # type: ignore[arg-type]
                conversation_id=conv_id,
                path="/etc/passwd",
            )
