"""Pure unit tests ensuring API routes always close request-scoped Sandbox handles."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import orjson
import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from cubeplex.api.routes.v1 import (
    background_tasks,
    sandbox_share,
    ws_browser,
    ws_sandbox,
)
from cubeplex.models.background_task import TERMINAL_TASK_STATES
from cubeplex.sandbox.base import (
    BrowserEndpoint,
    ExecuteResult,
    FileEntry,
    Sandbox,
    SandboxCapabilities,
    SandboxCapabilityUnsupportedError,
    SandboxError,
    SandboxFileTooLargeError,
)
from cubeplex.sandbox.manager import SandboxAttachment


class _SpySandbox(Sandbox):
    def __init__(
        self,
        *,
        workdir: str = "/workspace",
        list_error: Exception | None = None,
        read_error: Exception | None = None,
        stream_error: Exception | None = None,
        exec_error: Exception | None = None,
        terminal_error: Exception | None = None,
        browser_error: Exception | None = None,
        endpoint_headers: dict[str, str] | None = None,
    ) -> None:
        self._workdir = workdir
        self._list_error = list_error
        self._read_error = read_error
        self._stream_error = stream_error
        self._exec_error = exec_error
        self._terminal_error = terminal_error
        self._browser_error = browser_error
        self._endpoint_headers = endpoint_headers or {}
        self.close_calls = 0

    @property
    def id(self) -> str:
        return "sbx-spy-1"

    @property
    def provider_name(self) -> str:
        return "spy"

    @property
    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(terminal=True, browser=True)

    @property
    def workdir(self) -> str:
        return self._workdir

    async def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        provider_timeout: bool = False,
        envs: dict[str, str] | None = None,
        as_root: bool = False,
    ) -> ExecuteResult:
        del command, timeout, provider_timeout, envs, as_root
        if self._exec_error is not None:
            raise self._exec_error
        return ExecuteResult(output="ok", exit_code=0)

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        del files

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        del paths
        return []

    async def list_dir(self, path: str) -> list[FileEntry]:
        del path
        if self._list_error is not None:
            raise self._list_error
        return [
            FileEntry(
                path="/workspace/a.txt",
                name="a.txt",
                is_dir=False,
                size=3,
                modified_at=datetime.now(UTC),
            )
        ]

    async def read_text(self, path: str, *, max_bytes: int | None = None) -> str:
        del path, max_bytes
        if self._read_error is not None:
            raise self._read_error
        return "hello"

    @asynccontextmanager
    async def stream_file(self, path: str) -> AsyncIterator[AsyncIterator[bytes]]:
        del path
        if self._stream_error is not None:
            raise self._stream_error

        async def _iter() -> AsyncIterator[bytes]:
            yield b"chunk-1"

        yield _iter()

    async def start_terminal(self) -> None:
        if self._terminal_error is not None:
            raise self._terminal_error

    async def get_terminal_endpoint(self, *, expires_in: int = 3600) -> BrowserEndpoint:
        del expires_in
        return BrowserEndpoint(
            url="https://term.example/t",
            headers=self._endpoint_headers,
        )

    async def start_browser(self) -> None:
        if self._browser_error is not None:
            raise self._browser_error

    async def get_browser_endpoint(self, *, expires_in: int = 3600) -> BrowserEndpoint:
        del expires_in
        return BrowserEndpoint(
            url="https://neko.example/view",
            headers=self._endpoint_headers,
        )

    async def close(self) -> None:
        self.close_calls += 1


class _SpyManager:
    def __init__(
        self,
        sandbox: _SpySandbox,
        *,
        user_sandbox_id: str = "sbx_row_1",
        caps: SandboxCapabilities | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.user_sandbox_id = user_sandbox_id
        self.default_provider = SimpleNamespace(
            name="spy",
            capabilities=caps or SandboxCapabilities(terminal=True, browser=True),
            default_workdir=sandbox.workdir,
        )

    async def get_or_create(self, **_kwargs: object) -> SandboxAttachment:
        return SandboxAttachment(
            sandbox=self.sandbox,
            user_sandbox_id=self.user_sandbox_id,
        )

    async def ensure_running(self, _user_sandbox_id: str) -> SandboxAttachment:
        return SandboxAttachment(
            sandbox=self.sandbox,
            user_sandbox_id=self.user_sandbox_id,
        )

    async def touch(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def renew_lease(self, *_args: object, **_kwargs: object) -> None:
        return None


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(id="u-1"),
        org_id="org-1",
        workspace_id="ws-1",
    )


async def _consume_streaming(response: StreamingResponse) -> bytes:
    parts: list[bytes] = []
    async for chunk in response.body_iterator:
        parts.append(chunk if isinstance(chunk, bytes) else bytes(chunk))
    if response.background is not None:
        await response.background()
    return b"".join(parts)


@pytest.mark.asyncio
async def test_ws_sandbox_non_streaming_routes_close_on_success_and_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _ctx()

    # list_sandbox_files: normal, FileNotFoundError, path outside workdir
    sb = _SpySandbox()
    monkeypatch.setattr(ws_sandbox, "get_sandbox_manager", lambda: _SpyManager(sb))
    res = await ws_sandbox.list_sandbox_files(
        ctx,  # type: ignore[arg-type]
        session=None,  # type: ignore[arg-type]
        path="/workspace",
        conversation_id=None,
    )
    assert len(res) == 1
    assert sb.close_calls == 1

    sb_missing = _SpySandbox(list_error=FileNotFoundError("no dir"))
    monkeypatch.setattr(ws_sandbox, "get_sandbox_manager", lambda: _SpyManager(sb_missing))
    with pytest.raises(HTTPException) as exc:
        await ws_sandbox.list_sandbox_files(
            ctx,  # type: ignore[arg-type]
            session=None,  # type: ignore[arg-type]
            path="/workspace",
            conversation_id=None,
        )
    assert exc.value.status_code == 404
    assert sb_missing.close_calls == 1

    # get_sandbox_file_content: normal + FileTooLargeError + FileNotFoundError
    sb_read = _SpySandbox()
    monkeypatch.setattr(ws_sandbox, "get_sandbox_manager", lambda: _SpyManager(sb_read))
    content = await ws_sandbox.get_sandbox_file_content(
        ctx,  # type: ignore[arg-type]
        session=None,  # type: ignore[arg-type]
        path="/workspace/a.txt",
        conversation_id=None,
    )
    assert content.content == "hello"
    assert sb_read.close_calls == 1

    sb_big = _SpySandbox(read_error=SandboxFileTooLargeError("too large"))
    monkeypatch.setattr(ws_sandbox, "get_sandbox_manager", lambda: _SpyManager(sb_big))
    with pytest.raises(HTTPException) as exc:
        await ws_sandbox.get_sandbox_file_content(
            ctx,  # type: ignore[arg-type]
            session=None,  # type: ignore[arg-type]
            path="/workspace/a.txt",
            conversation_id=None,
        )
    assert exc.value.status_code == 413
    assert sb_big.close_calls == 1

    # exec_in_sandbox: normal + SandboxError
    sb_exec = _SpySandbox()
    monkeypatch.setattr(ws_sandbox, "get_sandbox_manager", lambda: _SpyManager(sb_exec))
    exec_res = await ws_sandbox.exec_in_sandbox(
        ws_sandbox.SandboxExecRequest(command="echo hi"),
        ctx,  # type: ignore[arg-type]
        session=None,  # type: ignore[arg-type]
        conversation_id=None,
    )
    assert exec_res.output == "ok"
    assert sb_exec.close_calls == 1

    sb_exec_err = _SpySandbox(exec_error=SandboxError("boom"))
    monkeypatch.setattr(ws_sandbox, "get_sandbox_manager", lambda: _SpyManager(sb_exec_err))
    with pytest.raises(HTTPException) as exc:
        await ws_sandbox.exec_in_sandbox(
            ws_sandbox.SandboxExecRequest(command="echo hi"),
            ctx,  # type: ignore[arg-type]
            session=None,  # type: ignore[arg-type]
            conversation_id=None,
        )
    assert exc.value.status_code == 503
    assert sb_exec_err.close_calls == 1

    # upload_sandbox_file: normal + post-attach validation failure
    sb_up = _SpySandbox()
    monkeypatch.setattr(ws_sandbox, "get_sandbox_manager", lambda: _SpyManager(sb_up))
    fake_file = SimpleNamespace(read=AsyncMock(return_value=b"payload"))
    up_res = await ws_sandbox.upload_sandbox_file(
        ctx,  # type: ignore[arg-type]
        session=None,  # type: ignore[arg-type]
        file=fake_file,  # type: ignore[arg-type]
        path="/workspace/up.txt",
        conversation_id=None,
    )
    assert up_res["ok"] is True
    assert sb_up.close_calls == 1

    # Post-attach sandbox.workdir narrower than pre-check provider.default_workdir
    sb_up_narrow = _SpySandbox(workdir="/workspace/sub")
    mgr_narrow = _SpyManager(sb_up_narrow)
    mgr_narrow.default_provider.default_workdir = "/workspace"
    monkeypatch.setattr(ws_sandbox, "get_sandbox_manager", lambda: mgr_narrow)
    with pytest.raises(HTTPException) as exc:
        await ws_sandbox.upload_sandbox_file(
            ctx,  # type: ignore[arg-type]
            session=None,  # type: ignore[arg-type]
            file=fake_file,  # type: ignore[arg-type]
            path="/workspace/other.txt",
            conversation_id=None,
        )
    assert exc.value.status_code == 400
    assert sb_up_narrow.close_calls == 1


@pytest.mark.asyncio
async def test_ws_sandbox_and_share_streaming_downloads_always_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _ctx()

    # ws_sandbox.download_sandbox_file: streaming success + FileNotFoundError
    sb_dl = _SpySandbox()
    monkeypatch.setattr(ws_sandbox, "get_sandbox_manager", lambda: _SpyManager(sb_dl))
    resp = await ws_sandbox.download_sandbox_file(
        ctx,  # type: ignore[arg-type]
        session=None,  # type: ignore[arg-type]
        path="/workspace/a.txt",
        conversation_id=None,
    )
    assert await _consume_streaming(resp) == b"chunk-1"
    assert sb_dl.close_calls >= 1

    sb_dl_missing = _SpySandbox(stream_error=FileNotFoundError("gone"))
    monkeypatch.setattr(ws_sandbox, "get_sandbox_manager", lambda: _SpyManager(sb_dl_missing))
    with pytest.raises(HTTPException) as exc:
        await ws_sandbox.download_sandbox_file(
            ctx,  # type: ignore[arg-type]
            session=None,  # type: ignore[arg-type]
            path="/workspace/missing.txt",
            conversation_id=None,
        )
    assert exc.value.status_code == 404
    assert sb_dl_missing.close_calls == 1

    # sandbox_share.sandbox_file_download: streaming success + FileNotFoundError
    token_payload = orjson.dumps(
        {"user_sandbox_id": "sbx_row_1", "file_path": "/workspace/report.docx"}
    )
    rh = SimpleNamespace(
        key_prefix="test",
        client=SimpleNamespace(get=AsyncMock(return_value=token_payload)),
    )

    sb_share = _SpySandbox()
    monkeypatch.setattr(sandbox_share, "get_sandbox_manager", lambda: _SpyManager(sb_share))
    share_resp = await sandbox_share.sandbox_file_download(
        "n1",
        "report.docx",
        rh,  # type: ignore[arg-type]
    )
    assert await _consume_streaming(share_resp) == b"chunk-1"
    assert sb_share.close_calls >= 1

    sb_share_err = _SpySandbox(stream_error=FileNotFoundError("gone"))
    monkeypatch.setattr(sandbox_share, "get_sandbox_manager", lambda: _SpyManager(sb_share_err))
    with pytest.raises(HTTPException) as exc:
        await sandbox_share.sandbox_file_download(
            "n1",
            "report.docx",
            rh,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 404
    assert sb_share_err.close_calls == 1


@pytest.mark.asyncio
async def test_terminal_and_browser_live_view_close_on_success_and_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _ctx()

    # get_terminal: normal + capability error + header error
    sb_term = _SpySandbox()
    monkeypatch.setattr(ws_sandbox, "get_sandbox_manager", lambda: _SpyManager(sb_term))
    term_res = await ws_sandbox.get_terminal(
        ctx,  # type: ignore[arg-type]
        session=None,  # type: ignore[arg-type]
        conversation_id=None,
    )
    assert term_res.url == "https://term.example/t"
    assert sb_term.close_calls == 1

    sb_term_hdr = _SpySandbox(endpoint_headers={"Authorization": "secret"})
    monkeypatch.setattr(ws_sandbox, "get_sandbox_manager", lambda: _SpyManager(sb_term_hdr))
    with pytest.raises(HTTPException) as exc:
        await ws_sandbox.get_terminal(
            ctx,  # type: ignore[arg-type]
            session=None,  # type: ignore[arg-type]
            conversation_id=None,
        )
    assert exc.value.status_code == 501
    assert sb_term_hdr.close_calls == 1

    sb_term_unsup = _SpySandbox(terminal_error=SandboxCapabilityUnsupportedError("terminal", "spy"))
    monkeypatch.setattr(ws_sandbox, "get_sandbox_manager", lambda: _SpyManager(sb_term_unsup))
    with pytest.raises(HTTPException) as exc:
        await ws_sandbox.get_terminal(
            ctx,  # type: ignore[arg-type]
            session=None,  # type: ignore[arg-type]
            conversation_id=None,
        )
    assert exc.value.status_code == 501
    assert sb_term_unsup.close_calls == 1

    # ws_browser.get_live_view: normal + capability error + header error
    sb_br = _SpySandbox()
    monkeypatch.setattr(ws_browser, "get_sandbox_manager", lambda: _SpyManager(sb_br))
    br_res = await ws_browser.get_live_view(
        ctx,  # type: ignore[arg-type]
        session=None,  # type: ignore[arg-type]
        conversation_id=None,
    )
    assert br_res.url.startswith("https://neko.example/view")
    assert sb_br.close_calls == 1

    sb_br_hdr = _SpySandbox(endpoint_headers={"Authorization": "secret"})
    monkeypatch.setattr(ws_browser, "get_sandbox_manager", lambda: _SpyManager(sb_br_hdr))
    with pytest.raises(HTTPException) as exc:
        await ws_browser.get_live_view(
            ctx,  # type: ignore[arg-type]
            session=None,  # type: ignore[arg-type]
            conversation_id=None,
        )
    assert exc.value.status_code == 501
    assert sb_br_hdr.close_calls == 1

    sb_br_unsup = _SpySandbox(browser_error=SandboxCapabilityUnsupportedError("browser", "spy"))
    monkeypatch.setattr(ws_browser, "get_sandbox_manager", lambda: _SpyManager(sb_br_unsup))
    with pytest.raises(HTTPException) as exc:
        await ws_browser.get_live_view(
            ctx,  # type: ignore[arg-type]
            session=None,  # type: ignore[arg-type]
            conversation_id=None,
        )
    assert exc.value.status_code == 501
    assert sb_br_unsup.close_calls == 1


@pytest.mark.asyncio
async def test_background_task_output_closes_sandbox_on_user_sandbox_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding 5: sandbox.close() runs even when user_sandbox_id mismatches record.id."""
    ctx = _ctx()
    sb = _SpySandbox()
    mgr = _SpyManager(sb, user_sandbox_id="sbx-different-row")
    monkeypatch.setattr(background_tasks, "get_sandbox_manager", lambda: mgr)
    monkeypatch.setattr(background_tasks, "_require_conversation", AsyncMock(return_value=None))
    item = SimpleNamespace(
        task=SimpleNamespace(state=next(iter(TERMINAL_TASK_STATES)), result_readiness="ready")
    )
    cmd = SimpleNamespace(
        id="cmd-1",
        user_sandbox_id="sbx-expected-row",
        log_path="/workspace/out.log",
    )
    monkeypatch.setattr(
        background_tasks,
        "_query_service",
        lambda *_a, **_k: SimpleNamespace(get_task=AsyncMock(return_value=item)),
    )
    monkeypatch.setattr(background_tasks, "_require_task_output", lambda _it: (item, cmd))
    monkeypatch.setattr(
        background_tasks.UserSandboxRepository,
        "get_by_id",
        AsyncMock(return_value=SimpleNamespace(id="sbx-expected-row", deleted_at=None)),
    )

    with pytest.raises(HTTPException) as exc:
        await background_tasks.get_background_task_output(
            workspace_id="ws-1",
            conversation_id="conv-1",
            task_id="task-1",
            session=None,  # type: ignore[arg-type]
            ctx=ctx,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 503
    assert sb.close_calls == 1


@pytest.mark.asyncio
async def test_run_manager_release_run_sandbox_closes_lazy_and_direct_handles() -> None:
    """RunManager cleanup closes initialized LazySandbox and handles uninitialized."""
    from cubeplex.sandbox.lazy import LazySandbox
    from cubeplex.streams.run_manager import RunManager

    # 1. Initialized LazySandbox releases lease and closes underlying Sandbox.
    sb_init = _SpySandbox()
    mgr_init = SimpleNamespace(
        release=AsyncMock(),
        default_provider=SimpleNamespace(name="spy", capabilities=SandboxCapabilities()),
    )
    lazy_init = LazySandbox(
        manager=mgr_init,  # type: ignore[arg-type]
        scope_type="user",
        scope_id="u-1",
        user_id="u-1",
        org_id="org-1",
        workspace_id="ws-1",
        workdir="/workspace",
    )
    lazy_init._sandbox = sb_init
    await RunManager._release_run_sandbox(
        lazy_init,
        sandbox_manager=None,
        org_id="org-1",
        workspace_id="ws-1",
    )
    mgr_init.release.assert_awaited_once_with(
        sb_init.id,
        org_id="org-1",
        workspace_id="ws-1",
    )
    assert sb_init.close_calls == 1

    # 2. Uninitialized LazySandbox skips release and closes cleanly as a no-op.
    mgr_idle = SimpleNamespace(
        release=AsyncMock(),
        default_provider=SimpleNamespace(name="spy", capabilities=SandboxCapabilities()),
    )
    lazy_idle = LazySandbox(
        manager=mgr_idle,  # type: ignore[arg-type]
        scope_type="user",
        scope_id="u-1",
        user_id="u-1",
        org_id="org-1",
        workspace_id="ws-1",
        workdir="/workspace",
    )
    await RunManager._release_run_sandbox(
        lazy_idle,
        sandbox_manager=None,
        org_id="org-1",
        workspace_id="ws-1",
    )
    mgr_idle.release.assert_not_called()

    # 3. Direct non-lazy Sandbox releases through sandbox_manager and closes handle.
    sb_direct = _SpySandbox()
    mgr_direct = SimpleNamespace(release=AsyncMock())
    await RunManager._release_run_sandbox(
        sb_direct,
        sandbox_manager=mgr_direct,
        org_id="org-1",
        workspace_id="ws-1",
    )
    mgr_direct.release.assert_awaited_once_with(
        sb_direct.id,
        org_id="org-1",
        workspace_id="ws-1",
    )
    assert sb_direct.close_calls == 1


@pytest.mark.asyncio
async def test_command_coordinator_sandbox_from_row_closes_on_mismatch_and_touch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sandbox_from_row closes attached handle on sandbox mismatch or touch failure."""
    from cubeplex.sandbox import command_coordinator

    us_row = SimpleNamespace(
        id="us-1",
        status="running",
        sandbox_id="sbx-expected",
        scope_type="user",
        scope_id="u-1",
        user_id="u-1",
        org_id="org-1",
        workspace_id="ws-1",
    )
    cmd_row = SimpleNamespace(id="cmd-1", user_sandbox_id="us-1")
    session = SimpleNamespace(get=AsyncMock(return_value=us_row))

    # Case 1: attached sandbox id replaced since command started -> closed before None.
    class _IdSpySandbox(_SpySandbox):
        def __init__(self, sandbox_id: str) -> None:
            super().__init__()
            self._custom_id = sandbox_id

        @property
        def id(self) -> str:
            return self._custom_id

    replaced = _IdSpySandbox("sbx-replaced")
    mgr_mismatch = _SpyManager(replaced)
    monkeypatch.setattr("cubeplex.sandbox.manager.get_sandbox_manager", lambda: mgr_mismatch)
    monkeypatch.setattr(command_coordinator, "_terminalize", AsyncMock(return_value=True))
    res_mismatch = await command_coordinator.sandbox_from_row(
        cmd_row,  # type: ignore[arg-type]
        session,  # type: ignore[arg-type]
    )
    assert res_mismatch is None
    assert replaced.close_calls == 1

    # Case 2: manager.touch raises -> handle closed before returning None.
    attached_err = _IdSpySandbox("sbx-expected")
    mgr_err = _SpyManager(attached_err)
    monkeypatch.setattr(mgr_err, "touch", AsyncMock(side_effect=RuntimeError("touch failed")))
    monkeypatch.setattr("cubeplex.sandbox.manager.get_sandbox_manager", lambda: mgr_err)
    res_err = await command_coordinator.sandbox_from_row(
        cmd_row,  # type: ignore[arg-type]
        session,  # type: ignore[arg-type]
    )
    assert res_err is None
    assert attached_err.close_calls == 1


@pytest.mark.asyncio
async def test_kill_stranded_commands_closes_attached_sandbox_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_kill_stranded_commands closes sandbox_from_row handles when done."""
    import importlib
    from contextlib import asynccontextmanager

    from cubeplex.sandbox import command_coordinator
    from cubeplex.streams import recovery

    db_engine = importlib.import_module("cubeplex.db.engine")

    sb = _SpySandbox()

    @asynccontextmanager
    async def _fake_session() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace()

    async def _fake_kill_run(
        _session: object,
        _run_id: str,
        *,
        get_sandbox: Any,
    ) -> list[str]:
        got = await get_sandbox(SimpleNamespace(id="cmd-1"))
        assert got is sb
        return ["cmd-1"]

    monkeypatch.setattr(recovery, "_stop_managed_stranded_runs", AsyncMock())
    monkeypatch.setattr(db_engine, "async_session_maker", _fake_session)
    monkeypatch.setattr(command_coordinator, "sandbox_from_row", AsyncMock(return_value=sb))
    monkeypatch.setattr(command_coordinator, "kill_run_commands", _fake_kill_run)

    await recovery._kill_stranded_commands(["run-1"])
    assert sb.close_calls == 1
