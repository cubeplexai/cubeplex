"""Outbound native delivery of present_file blobs via IMArtifactDispatcher.

Guards the gap where present_file succeeded on the web but IM never sent.
"""

import tempfile
from pathlib import Path
from typing import Any

import pytest

from cubeplex.im import artifacts as artifacts_mod
from cubeplex.im.artifacts import IMArtifactDispatcher
from cubeplex.im.card_model import CardState

pytestmark = pytest.mark.asyncio


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(
        self, key: str, value: str, *, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


class _FakeConnector:
    def __init__(self, *, send_ok: bool = True, chat_ok: bool = True) -> None:
        self.send_ok = send_ok
        self.chat_ok = chat_ok
        self.send_file_calls: list[dict[str, Any]] = []
        self.send_image_calls: list[dict[str, Any]] = []
        self.chat_calls: list[str] = []

    async def send_file(self, *, local_path: str, filename: str, mime: str | None) -> bool:
        self.send_file_calls.append({"filename": filename, "mime": mime})
        return self.send_ok

    async def send_image(self, *, local_path: str, filename: str) -> bool:
        self.send_image_calls.append({"filename": filename})
        return self.send_ok

    async def upload_image(self, local_path: str) -> str | None:
        return None

    async def send_to_chat(self, chat_id: str, reply_to_id: str | None, text: str) -> str | None:
        self.chat_calls.append(text)
        return "msg-1" if self.chat_ok else None


async def _make_temp(_presented: dict[str, Any], *, size: int = 100, **_kwargs: Any) -> Path:
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
        tmp.write(b"x" * size)
        return Path(tmp.name)


def _dispatcher(connector: _FakeConnector, redis: _FakeRedis) -> IMArtifactDispatcher:
    return IMArtifactDispatcher(
        connector=connector,
        redis=redis,
        redis_key_prefix="t",
        public_base_url="https://example.test",
        org_id="org-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
        card_state=CardState(bot_name="CubePlex", run_id="run-1"),
        run_id="run-1",
        platform="feishu",
        chat_id="oc_chat",
        reply_to_id=None,
        supports_inline_image=True,
    )


def _presented(*, kind: str = "document", file_id: str = "pfile-1") -> dict[str, Any]:
    return {
        "id": file_id,
        "filename": "report.xlsx" if kind != "image" else "qr.png",
        "mime_type": "application/vnd.ms-excel" if kind != "image" else "image/png",
        "kind": kind,
        "size_bytes": 100,
        "caption": "Login QR" if kind == "image" else None,
        "conversation_id": "conv-1",
    }


async def test_presented_document_sent_as_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-image present_file is send_file'd at terminal, not mid-run.

    Bug guarded: if the tailer never calls deliver_terminal_presented, IM
    users get silence after a successful present_file.
    """
    monkeypatch.setattr(artifacts_mod, "download_presented_to_tempfile", _make_temp)
    conn = _FakeConnector(send_ok=True)
    disp = _dispatcher(conn, _FakeRedis())

    await disp.handle_presented(_presented(kind="document"))
    assert len(conn.send_file_calls) == 0

    await disp.deliver_terminal_presented()
    assert len(conn.send_file_calls) == 1
    assert conn.send_file_calls[0]["filename"] == "report.xlsx"
    assert conn.send_image_calls == []
    assert conn.chat_calls == []


async def test_presented_image_on_feishu_uses_send_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Feishu QR / screenshot must be a native image, not a file bubble."""
    monkeypatch.setattr(artifacts_mod, "download_presented_to_tempfile", _make_temp)
    conn = _FakeConnector(send_ok=True)
    disp = _dispatcher(conn, _FakeRedis())

    await disp.handle_presented(_presented(kind="image"))
    await disp.deliver_terminal_presented()
    assert len(conn.send_image_calls) == 1
    assert conn.send_file_calls == []


async def test_presented_image_without_inline_uses_send_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack/Discord have no send_image path at the dispatcher (flag off)."""
    monkeypatch.setattr(artifacts_mod, "download_presented_to_tempfile", _make_temp)
    conn = _FakeConnector(send_ok=True)
    disp = _dispatcher(conn, _FakeRedis())
    disp.supports_inline_image = False

    await disp.handle_presented(_presented(kind="image"))
    await disp.deliver_terminal_presented()
    assert len(conn.send_file_calls) == 1
    assert conn.send_image_calls == []


async def test_presented_delivery_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tailer replay must not double-send a presented file."""
    monkeypatch.setattr(artifacts_mod, "download_presented_to_tempfile", _make_temp)
    conn = _FakeConnector(send_ok=True)
    redis = _FakeRedis()
    disp = _dispatcher(conn, redis)
    await disp.handle_presented(_presented())

    await disp.deliver_terminal_presented()
    await disp.deliver_terminal_presented()
    assert len(conn.send_file_calls) == 1


async def test_presented_oversize_falls_back_to_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _big(_p: dict[str, Any], **_kwargs: Any) -> Path:
        return await _make_temp(_p, size=40 * 1024 * 1024)

    monkeypatch.setattr(artifacts_mod, "download_presented_to_tempfile", _big)
    conn = _FakeConnector(send_ok=True)
    disp = _dispatcher(conn, _FakeRedis())
    await disp.handle_presented(_presented())

    await disp.deliver_terminal_presented()
    assert conn.send_file_calls == []
    assert len(conn.chat_calls) == 1
    assert "report.xlsx" in conn.chat_calls[0]
    assert "/api/v1/public/artifacts/share/" not in conn.chat_calls[0]


async def test_presented_total_failure_releases_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native + text both fail → claim released so replay retries."""
    monkeypatch.setattr(artifacts_mod, "download_presented_to_tempfile", _make_temp)
    conn = _FakeConnector(send_ok=False, chat_ok=False)
    redis = _FakeRedis()
    disp = _dispatcher(conn, redis)
    await disp.handle_presented(_presented())

    claim_key = "t:im:presented_sent:run-1:pfile-1"
    await disp.deliver_terminal_presented()
    assert claim_key not in redis.store

    conn.send_ok = True
    await disp.deliver_terminal_presented()
    assert len(conn.send_file_calls) == 2
    assert claim_key in redis.store


async def test_artifact_document_still_uses_send_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """save_artifact outbound must not be swallowed by the present_file path."""

    async def _art_temp(_a: dict[str, Any], *, size: int = 100) -> Path:
        return await _make_temp(_a, size=size)

    monkeypatch.setattr(artifacts_mod, "download_artifact_to_tempfile", _art_temp)
    conn = _FakeConnector(send_ok=True)
    disp = _dispatcher(conn, _FakeRedis())
    await disp.handle(
        {
            "id": "art-1",
            "artifact_type": "document",
            "name": "out.xlsx",
            "version": 1,
            "entry_file": "out.xlsx",
            "conversation_id": "conv-1",
        }
    )
    await disp.deliver_terminal_files()
    await disp.deliver_terminal_presented()
    assert len(conn.send_file_calls) == 1
    assert conn.send_image_calls == []
