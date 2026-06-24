"""Outbound native-file delivery via IMArtifactDispatcher.

Composes the dispatcher with a fake bound connector and a fake Redis (NX
semantics); mocks only the objectstore download. Asserts the routing,
idempotency, and share-link fallback the design promises.
"""

import tempfile
from pathlib import Path
from typing import Any

import pytest

from cubebox.im import artifacts as artifacts_mod
from cubebox.im.artifacts import IMArtifactDispatcher
from cubebox.im.card_model import CardState

pytestmark = pytest.mark.asyncio


class _FakeRedis:
    """Minimal SET NX EX implementation backed by a dict."""

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
        self.chat_calls: list[str] = []

    async def send_file(self, *, local_path: str, filename: str, mime: str | None) -> bool:
        self.send_file_calls.append({"filename": filename})
        return self.send_ok

    async def upload_image(self, local_path: str) -> str | None:
        return None

    async def send_to_chat(self, chat_id: str, reply_to_id: str | None, text: str) -> str | None:
        self.chat_calls.append(text)
        return "msg-1" if self.chat_ok else None


async def _make_temp(_conv: str, _artifact: dict[str, Any], *, size: int = 100) -> Path:
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
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
        card_state=CardState(bot_name="cubebox", run_id="run-1"),
        run_id="run-1",
        platform="feishu",
        chat_id="oc_chat",
        reply_to_id=None,
        supports_inline_image=True,
    )


def _artifact(atype: str, art_id: str = "art-1") -> dict[str, Any]:
    return {
        "id": art_id,
        "artifact_type": atype,
        "name": f"{art_id}.bin",
        "version": 1,
        "entry_file": "out.xlsx",
    }


async def test_file_artifact_sent_natively_and_not_share_linked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document artifact is captured (no in-card link) and sent at terminal.

    Bug guarded: if terminal dispatch drops file artifacts, users only get
    share-links and never the native file.
    """
    monkeypatch.setattr(artifacts_mod, "download_artifact_to_tempfile", _make_temp)
    conn = _FakeConnector(send_ok=True)
    disp = _dispatcher(conn, _FakeRedis())

    await disp.handle(_artifact("document"))
    # Captured for terminal delivery; no share-link minted mid-run.
    assert disp.card_state.artifacts[0].share_url is None
    assert len(conn.send_file_calls) == 0

    await disp.deliver_terminal_files()
    assert len(conn.send_file_calls) == 1
    assert conn.chat_calls == []  # native send succeeded → no fallback


async def test_terminal_delivery_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis SET NX gate: a replay sends the file exactly once.

    Bug guarded: a tailer restart replays the terminal event and double-sends.
    """
    monkeypatch.setattr(artifacts_mod, "download_artifact_to_tempfile", _make_temp)
    conn = _FakeConnector(send_ok=True)
    redis = _FakeRedis()
    disp = _dispatcher(conn, redis)
    await disp.handle(_artifact("document"))

    await disp.deliver_terminal_files()
    await disp.deliver_terminal_files()  # replay
    assert len(conn.send_file_calls) == 1


async def test_oversize_file_falls_back_to_share_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """Over the platform cap → no native send, a share-link message instead."""

    async def _big(_c: str, _a: dict[str, Any]) -> Path:
        return await _make_temp(_c, _a, size=40 * 1024 * 1024)  # > 30MB Feishu cap

    monkeypatch.setattr(artifacts_mod, "download_artifact_to_tempfile", _big)
    conn = _FakeConnector(send_ok=True)
    disp = _dispatcher(conn, _FakeRedis())
    await disp.handle(_artifact("document"))

    await disp.deliver_terminal_files()
    assert len(conn.send_file_calls) == 0
    # Link reaches the user via a standalone message (card is already finalized,
    # so the dispatcher does NOT mutate the card's share_url).
    assert len(conn.chat_calls) == 1
    assert "/api/v1/public/artifacts/share/" in conn.chat_calls[0]


async def test_failed_send_falls_back_to_share_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts_mod, "download_artifact_to_tempfile", _make_temp)
    conn = _FakeConnector(send_ok=False)  # upload fails
    disp = _dispatcher(conn, _FakeRedis())
    await disp.handle(_artifact("document"))

    await disp.deliver_terminal_files()
    assert len(conn.send_file_calls) == 1
    assert len(conn.chat_calls) == 1  # fell back


async def test_total_failure_releases_claim_so_replay_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native send AND fallback both fail → claim released → a later replay
    retries instead of the burned claim silently losing the artifact forever.
    """
    monkeypatch.setattr(artifacts_mod, "download_artifact_to_tempfile", _make_temp)
    conn = _FakeConnector(send_ok=False, chat_ok=False)  # both delivery paths fail
    redis = _FakeRedis()
    disp = _dispatcher(conn, redis)
    await disp.handle(_artifact("document"))

    claim_key = "t:im:artifact_sent:run-1:art-1"
    await disp.deliver_terminal_files()
    assert len(conn.send_file_calls) == 1  # attempted once, failed
    assert claim_key not in redis.store  # claim released for retry

    # Replay: now delivery succeeds; the released claim lets it retry.
    conn.send_ok = True
    await disp.deliver_terminal_files()
    assert len(conn.send_file_calls) == 2  # retried, not silently skipped
    assert claim_key in redis.store  # success keeps the claim


async def test_website_artifact_stays_share_link_never_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A website artifact must never be routed to send_file (undownloadable)."""
    monkeypatch.setattr(artifacts_mod, "download_artifact_to_tempfile", _make_temp)
    conn = _FakeConnector(send_ok=True)
    disp = _dispatcher(conn, _FakeRedis())

    await disp.handle(_artifact("website"))
    assert disp.card_state.artifacts[0].share_url is not None  # link minted in-card

    await disp.deliver_terminal_files()
    assert len(conn.send_file_calls) == 0


async def test_image_on_no_inline_platform_falls_back_to_share_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack/Discord (supports_inline_image=False) → image becomes a share-link,
    not an AttributeError or a wasted upload_image call."""
    monkeypatch.setattr(artifacts_mod, "download_artifact_to_tempfile", _make_temp)
    conn = _FakeConnector(send_ok=True)
    disp = _dispatcher(conn, _FakeRedis())
    disp.supports_inline_image = False

    await disp.handle(_artifact("image"))
    assert disp.card_state.artifacts[0].share_url is not None
    assert disp.card_state.artifacts[0].image_key is None
