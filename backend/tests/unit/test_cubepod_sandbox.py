"""Pure in-process unit tests for the native CubePod sandbox driver and wiring."""

from __future__ import annotations

import io
import json
import tarfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from cubepod import AsyncCubePod
from fastapi import HTTPException

from cubeplex.api.app import validate_sandbox_config
from cubeplex.api.routes.sandbox_panel import (
    _build_panel_http_upstream,
    _build_panel_ws_upstream,
)
from cubeplex.api.routes.v1.ws_sandbox import (
    download_sandbox_file,
    get_sandbox_file_content,
    list_sandbox_files,
    reset_sandbox_rootfs,
)
from cubeplex.sandbox.base import (
    ExecuteResult,
    ProcessHandle,
    ProcessHandleNotFoundError,
    SandboxError,
    SandboxInstanceGoneError,
)
from cubeplex.sandbox.cubepod import (
    CubePodSandbox,
    FileTooLargeError,
    get_cubepod_sandbox,
)
from cubeplex.sandbox.lazy import LazySandbox, _sync_skills
from cubeplex.sandbox.manager import SandboxManager
from cubeplex.services.sandbox_policy import EffectivePolicy
from cubeplex.skills.sync_manifest import MANIFEST_PATH


def _make_mock_sandbox(
    handler: object,
    *,
    sandbox_id: str = "cpod-1",
    domain: str = "gw.internal:8080",
    api_key: str | None = "secret-key",
    run_uid: int | None = 1000,
    run_gid: int | None = 1000,
    max_retries: int = 0,
) -> CubePodSandbox:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    client = AsyncCubePod(
        base_url=domain,
        api_key=api_key,
        transport=transport,
        max_retries=max_retries,
    )
    return CubePodSandbox(
        sandbox_id=sandbox_id,
        client=client,
        owns_client=True,
        run_uid=run_uid,
        run_gid=run_gid,
    )


async def test_sdk_client_auth_header_and_error_translation() -> None:
    seen_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(request.headers))
        if request.url.path == "/sandboxes/cpod-gone":
            return httpx.Response(404, json={"error": "not found"})
        if request.url.path == "/sandboxes/cpod-term":
            return httpx.Response(
                200,
                json={"sandboxID": "cpod-term", "templateID": "ubuntu", "state": "Stopped"},
            )
        if request.url.path == "/sandboxes/cpod-err":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(500, text="boom")

    client = AsyncCubePod(
        base_url="gw.local:8080",
        api_key="tok-123",
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    assert str(client._client.base_url) == "http://gw.local:8080"  # noqa: SLF001
    assert client._client._trust_env is False  # noqa: SLF001

    with pytest.raises(SandboxInstanceGoneError):
        await get_cubepod_sandbox(client, "cpod-gone")
    assert seen_headers[0]["authorization"] == "Bearer tok-123"

    with pytest.raises(SandboxInstanceGoneError):
        await get_cubepod_sandbox(client, "cpod-term")

    with pytest.raises(SandboxError, match="connection refused"):
        await get_cubepod_sandbox(client, "cpod-err")

    await client.close()


async def test_execute_foreground_envs_and_privilege_separation() -> None:
    captured_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payloads.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"id": "cmd-1", "output": "ok\n", "exitCode": 0})

    sb = _make_mock_sandbox(handler, run_uid=1000, run_gid=1000)
    sb.set_run_env({"API_KEY": "cpodref_abc", "BASE": "1"})

    res = await sb.execute("echo hi", envs={"API_KEY": "override", "EXTRA": "2"})
    assert res == ExecuteResult(output="ok\n", exit_code=0)
    assert captured_payloads[0]["uid"] == 1000
    assert captured_payloads[0]["gid"] == 1000
    assert captured_payloads[0]["envs"] == {"API_KEY": "override", "BASE": "1", "EXTRA": "2"}

    await sb.execute("id", as_root=True)
    assert captured_payloads[1]["uid"] == 0
    assert captured_payloads[1]["gid"] == 0
    await sb.close()

    sb_no_uid = _make_mock_sandbox(handler, run_uid=None, run_gid=None)
    await sb_no_uid.execute("id")
    assert "uid" not in captured_payloads[2]
    assert "gid" not in captured_payloads[2]
    await sb_no_uid.close()


async def test_execute_streaming_on_chunk_and_callback_isolation() -> None:
    poll_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        if request.method == "POST" and request.url.path.endswith("/commands/run"):
            return httpx.Response(200, json={"id": "cmd-stream", "running": True, "cursor": 0})
        if request.method == "GET" and request.url.path.endswith("/commands/cmd-stream/logs"):
            poll_count += 1
            if poll_count == 1:
                return httpx.Response(
                    200,
                    json={"id": "cmd-stream", "content": "part1 ", "cursor": 6, "running": True},
                )
            return httpx.Response(
                200,
                json={
                    "id": "cmd-stream",
                    "content": "part2",
                    "cursor": 11,
                    "running": False,
                    "exitCode": 0,
                },
            )
        return httpx.Response(404)

    sb = _make_mock_sandbox(handler)
    chunks: list[str] = []

    def flaky_cb(text: str) -> None:
        chunks.append(text)
        if text == "part1 ":
            raise RuntimeError("callback error ignored")

    res = await sb.execute("run", on_chunk=flaky_cb)
    assert res == ExecuteResult(output="part1 part2", exit_code=0)
    assert chunks == ["part1 ", "part2"]
    await sb.close()


async def test_execute_timeout_returns_timeout_result() -> None:
    import asyncio

    async def slow_execute(*_args: object, **_kwargs: object) -> object:
        await asyncio.sleep(0.2)
        return SimpleNamespace(output="late", exit_code=0)

    sb = _make_mock_sandbox(lambda _req: httpx.Response(200))
    sb._client.execute = slow_execute  # type: ignore[method-assign]
    res = await sb.execute("sleep 10", timeout=0)
    assert res == ExecuteResult(output="[timeout]", exit_code=-1)
    await sb.close()


async def test_background_commands_and_404_distinction() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sandboxes/cpod-1/commands/run":
            return httpx.Response(200, json={"id": "cmd-bg", "running": True, "cursor": 0})
        if request.url.path == "/sandboxes/cpod-1/commands/cmd-bg/status":
            return httpx.Response(200, json={"id": "cmd-bg", "running": True})
        if request.url.path == "/sandboxes/cpod-1/commands/cmd-bg/logs":
            return httpx.Response(
                200,
                json={
                    "id": "cmd-bg",
                    "content": "hello",
                    "cursor": 5,
                    "running": False,
                    "exitCode": 0,
                },
            )
        if request.url.path == "/sandboxes/cpod-1/commands/cmd-bg/kill":
            return httpx.Response(200, json={"status": "killed"})
        if request.url.path == "/sandboxes/cpod-1/commands/missing/status":
            return httpx.Response(404, json={"error": "command not found"})
        if request.url.path == "/sandboxes/cpod-1":
            return httpx.Response(
                200,
                json={"sandboxID": "cpod-1", "templateID": "ubuntu", "state": "Running"},
            )
        return httpx.Response(404)

    sb = _make_mock_sandbox(handler)
    assert sb.supports_background() is True
    assert sb.supports_background_reconnect() is True

    started_ids: list[str] = []

    async def on_started(cmd_id: str) -> None:
        started_ids.append(cmd_id)

    handle = await sb.start("echo hi", timeout=30, on_started=on_started)
    assert started_ids == ["cmd-bg"]
    assert handle.provider_ref == "cmd-bg"

    snap_obs = await sb.observe(handle)
    assert snap_obs.status == "running"

    out = await sb.read_output(handle)
    assert out.new_output == "hello"
    assert out.log_cursor == "5"

    snap_poll = await sb.poll(handle)
    assert snap_poll.status == "exited"
    assert snap_poll.exit_code == 0

    await sb.kill(handle)

    with pytest.raises(ProcessHandleNotFoundError):
        await sb.observe(ProcessHandle(command_id="missing", provider_ref="missing"))
    await sb.close()


async def test_files_zero_chown_archive_sync_and_streaming() -> None:
    calls: list[tuple[str, str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, dict(request.url.params)))
        if request.method == "PUT" and request.url.path == "/sandboxes/cpod-1/files":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "GET" and request.url.path == "/sandboxes/cpod-1/files":
            if request.url.params.get("path") == "/workspace/missing.txt":
                return httpx.Response(404, text="not found")
            return httpx.Response(200, content=b"hello world")
        if request.method == "POST" and request.url.path == "/sandboxes/cpod-1/files/archive-sync":
            assert request.headers["content-type"] == "application/gzip"
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "GET" and request.url.path == "/sandboxes/cpod-1/files/list":
            return httpx.Response(
                200,
                json={
                    "entries": [
                        {"name": "a.py", "path": "/workspace/a.py", "is_dir": False, "size": 11}
                    ]
                },
            )
        return httpx.Response(404)

    sb = _make_mock_sandbox(handler)
    await sb.upload([("/workspace/a.txt", b"abc")])
    assert calls[0] == (
        "PUT",
        "/sandboxes/cpod-1/files",
        {"path": "/workspace/a.txt", "mode": "0644", "uid": "1000", "gid": "1000"},
    )

    assert await sb.download(["/workspace/a.txt"]) == [("/workspace/a.txt", b"hello world")]
    with pytest.raises(FileNotFoundError):
        await sb.download(["/workspace/missing.txt"])

    assert sb.supports_archive_sync() is True
    await sb.archive_sync(b"gz", dest="/workspace/.skills", remove_paths=["old-a", "old-b"])
    assert calls[-1][2]["remove_paths"] == "old-a,old-b"

    entries = await sb.list_directory("/workspace")
    assert len(entries) == 1 and entries[0].name == "a.py"

    assert await sb.read_text_file("/workspace/a.txt", max_bytes=100) == "hello world"
    with pytest.raises(FileTooLargeError):
        await sb.read_text_file("/workspace/a.txt", max_bytes=5)

    stream = await sb.stream_file_bytes("/workspace/a.txt")
    collected = b"".join([chunk async for chunk in stream])
    assert collected == b"hello world"
    await sb.close()


async def test_lifecycle_reset_rootfs_fork_and_browser_terminal() -> None:
    commands_run: list[tuple[str, int | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sandboxes/cpod-1/pause":
            return httpx.Response(
                200,
                json={"sandboxID": "cpod-1", "templateID": "ubuntu", "state": "Paused"},
            )
        if request.url.path == "/sandboxes/cpod-1/timeout":
            return httpx.Response(200, json={"timeoutSeconds": 600})
        if request.url.path == "/sandboxes/cpod-1/lease":
            return httpx.Response(200, json={"leaseSeconds": 120})
        if request.url.path == "/sandboxes/cpod-1/connect":
            return httpx.Response(
                200,
                json={"sandboxID": "cpod-1", "templateID": "ubuntu", "state": "Running"},
            )
        if request.url.path == "/sandboxes/cpod-1/reset-rootfs":
            return httpx.Response(
                200,
                json={
                    "sandboxID": "cpod-1",
                    "templateID": "ubuntu",
                    "state": "Running",
                    "rootfsGeneration": 2,
                },
            )
        if request.url.path == "/sandboxes/cpod-1/fork":
            return httpx.Response(
                200,
                json={"sandboxID": "cpod-fork-2", "templateID": "ubuntu", "state": "Running"},
            )
        if request.url.path == "/sandboxes/cpod-1/commands/run":
            body = json.loads(request.content.decode())
            commands_run.append((body["command"], body.get("uid")))
            if "(echo >/dev/tcp/127.0.0.1/8080)" in body["command"]:
                return httpx.Response(200, json={"output": "", "exitCode": 1})
            return httpx.Response(200, json={"output": "", "exitCode": 0})
        return httpx.Response(404)

    sb = _make_mock_sandbox(handler)
    assert sb.supports_pause() is True
    assert sb.supports_reset_rootfs() is True
    assert sb.supports_fork() is True

    await sb.pause()
    await sb.renew(600)
    await sb.acquire_lease(120)
    await sb.reset_rootfs()
    await sb.ensure_workspace_owner()

    child = await sb.fork(volume_key="vol-child")
    assert child.id == "cpod-fork-2"

    resumed = await CubePodSandbox.connect_or_resume("cpod-1", client=sb._client)  # noqa: SLF001
    assert resumed.id == "cpod-1"

    with (
        patch(
            "cubeplex.sandbox.cubepod.get_panel_base_url", return_value="https://api.example.com"
        ),
        patch("cubeplex.sandbox.cubepod.get_panel_secret", return_value="panel-secret"),
    ):
        await sb.start_browser()
        browser_ep = await sb.get_browser_endpoint()
        assert browser_ep.url.startswith("https://api.example.com/sandbox-panel/")
        assert sb._browser_port == 6080  # noqa: SLF001

        sb.set_run_env({"SECRET": "cpodref_xyz"})
        await sb.start_terminal()
        term_ep = await sb.get_terminal_endpoint()
        assert term_ep.url.startswith("https://api.example.com/sandbox-panel/")

    await sb.close()


async def test_sync_skills_archive_sync_fast_path_and_empty_guard() -> None:
    skill = SimpleNamespace(
        name="demo",
        version="1.0.0",
        skill_version_id="sv-1",
        content_hash="sha256:abc",
        storage_prefix="skills/demo/1.0.0",
    )
    catalog = AsyncMock()
    catalog.list_enabled_for_workspace.return_value = [skill]
    catalog.list_files_for_sandbox_sync.return_value = [("SKILL.md", b"# Demo\n")]

    sandbox = AsyncMock()
    sandbox.supports_archive_sync = MagicMock(return_value=True)
    sandbox.download.side_effect = FileNotFoundError(MANIFEST_PATH)

    res = await _sync_skills(
        catalog=catalog,
        workspace_id="ws-1",
        org_id="org-1",
        sandbox=sandbox,
    )
    assert res.status == "success"
    sandbox.archive_sync.assert_awaited_once()
    sandbox.execute.assert_not_called()
    archive_bytes = sandbox.archive_sync.call_args.args[0]
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
        assert "demo/1.0.0/SKILL.md" in tf.getnames()
    sandbox.upload.assert_awaited_once()
    assert sandbox.upload.call_args.args[0][0][0] == MANIFEST_PATH

    # EmptyCollectFiles guard must fire BEFORE archive_sync or manifest write
    catalog.list_files_for_sandbox_sync.return_value = []
    sandbox.archive_sync.reset_mock()
    sandbox.upload.reset_mock()
    res_empty = await _sync_skills(
        catalog=catalog,
        workspace_id="ws-1",
        org_id="org-1",
        sandbox=sandbox,
    )
    assert res_empty.status == "failed"
    assert res_empty.error_type == "EmptyCollectFiles"
    sandbox.archive_sync.assert_not_awaited()
    sandbox.upload.assert_not_awaited()

    # LazySandbox delegates capability methods
    lazy = LazySandbox(
        manager=MagicMock(),
        scope_type="user",
        scope_id="u1",
        user_id="u1",
        org_id="o1",
        workspace_id="w1",
    )
    assert lazy.supports_archive_sync() is False
    assert lazy.supports_reset_rootfs() is False
    assert lazy.supports_fork() is False


def test_validate_sandbox_config_provider_validation() -> None:
    values = {
        "sandbox.enabled": True,
        "sandbox.domain": "gw.local:8080",
        "sandbox.image": "cubeplex-ubuntu24-desktop",
        "sandbox.api_key": "real-key",
        "sandbox.provider": "cubepod",
    }
    with (
        patch("cubeplex.config.config") as mock_cfg,
        patch("cubeplex.config.get_command_default_timeout_seconds", return_value=3600),
    ):
        mock_cfg.get.side_effect = lambda k, default=None: values.get(k, default)
        validate_sandbox_config()

        values["sandbox.provider"] = "invalid-provider"
        with pytest.raises(RuntimeError, match="CUBEPLEX_SANDBOX__PROVIDER"):
            validate_sandbox_config()


def test_sandbox_panel_cubepod_host_routing() -> None:
    values = {
        "sandbox.provider": "cubepod",
        "sandbox.domain": "http://cubepod-gw.internal:8080",
        "sandbox.api_key": "gw-token",
    }
    with patch("cubeplex.config.config") as mock_cfg:
        mock_cfg.get.side_effect = lambda k, default=None: values.get(k, default)
        http_url, http_headers = _build_panel_http_upstream(
            sandbox_id="cpod-99",
            port=6080,
            full_path="vnc.html",
            query="autoconnect=1",
            headers={"accept-encoding": "identity"},
        )
        assert http_url == "http://cubepod-gw.internal:8080/vnc.html?autoconnect=1"
        assert http_headers["host"] == "6080-cpod-99.cubepod.local"
        assert http_headers["authorization"] == "Bearer gw-token"

        ws_url, ws_headers = _build_panel_ws_upstream(
            sandbox_id="cpod-99",
            port=7681,
            full_path="ws",
            query="",
        )
        assert ws_url == "ws://cubepod-gw.internal:8080/ws"
        assert ws_headers == {
            "Host": "7681-cpod-99.cubepod.local",
            "Authorization": "Bearer gw-token",
        }


async def test_ws_sandbox_cubepod_file_ops_and_reset_rootfs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sandboxes/cpod-1/files/list":
            return httpx.Response(
                200,
                json={
                    "entries": [
                        {
                            "name": ".hidden",
                            "path": "/workspace/.hidden",
                            "is_dir": False,
                            "size": 1,
                        },
                        {
                            "name": "notes.txt",
                            "path": "/workspace/notes.txt",
                            "is_dir": False,
                            "size": 5,
                            "modified_at": "2026-11-13T00:00:00Z",
                        },
                    ]
                },
            )
        if request.url.path == "/sandboxes/cpod-1/files":
            return httpx.Response(200, content=b"hello")
        return httpx.Response(404)

    sb = _make_mock_sandbox(handler)
    manager = MagicMock()
    manager.get_or_create = AsyncMock(
        return_value=SimpleNamespace(sandbox=sb, user_sandbox_id="sbx-1")
    )
    manager.touch = AsyncMock()
    manager.reset_user_sandbox_rootfs = AsyncMock()

    ctx = SimpleNamespace(org_id="org-1", workspace_id="ws-1", user=SimpleNamespace(id="u-1"))
    session = AsyncMock()

    with (
        patch("cubeplex.api.routes.v1.ws_sandbox.get_sandbox_manager", return_value=manager),
        patch(
            "cubeplex.api.routes.v1.ws_sandbox._resolve_sandbox_scope",
            AsyncMock(return_value=("user", "u-1", "u-1")),
        ),
    ):
        listed = await list_sandbox_files(ctx=ctx, session=session, path="/workspace")
        assert len(listed) == 1 and listed[0].name == "notes.txt"

        content = await get_sandbox_file_content(
            ctx=ctx, session=session, path="/workspace/notes.txt"
        )
        assert content.content == "hello"

        dl = await download_sandbox_file(ctx=ctx, session=session, path="/workspace/notes.txt")
        assert dl.headers["content-disposition"].startswith("attachment;")

        repo_inst = MagicMock()
        repo_inst.get_active_by_scope = AsyncMock(
            return_value=SimpleNamespace(id="sbx-1", provider="cubepod")
        )
        with patch(
            "cubeplex.api.routes.v1.ws_sandbox.UserSandboxRepository", return_value=repo_inst
        ):
            await reset_sandbox_rootfs(ctx=ctx, session=session)
        manager.reset_user_sandbox_rootfs.assert_awaited_once_with("sbx-1")

        repo_inst.get_active_by_scope = AsyncMock(
            return_value=SimpleNamespace(id="sbx-1", provider="opensandbox")
        )
        with (
            patch(
                "cubeplex.api.routes.v1.ws_sandbox.UserSandboxRepository", return_value=repo_inst
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            await reset_sandbox_rootfs(ctx=ctx, session=session)
        assert exc_info.value.status_code == 501

    await sb.close()


async def test_sandbox_manager_cubepod_lifecycle_egress_reset_and_fork() -> None:
    seen_create_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/sandboxes":
            seen_create_payloads.append(json.loads(request.content.decode()))
            return httpx.Response(
                200,
                json={
                    "sandboxID": "cpod-new",
                    "templateID": "cubeplex-ubuntu24-desktop",
                    "state": "Running",
                },
            )
        if request.method == "GET" and request.url.path == "/sandboxes/cpod-new":
            return httpx.Response(
                200,
                json={
                    "sandboxID": "cpod-new",
                    "templateID": "cubeplex-ubuntu24-desktop",
                    "state": "Running",
                },
            )
        if request.method == "POST" and request.url.path == "/sandboxes/cpod-new/reset-rootfs":
            return httpx.Response(
                200,
                json={
                    "sandboxID": "cpod-new",
                    "templateID": "cubeplex-ubuntu24-desktop",
                    "state": "Running",
                    "rootfsGeneration": 2,
                },
            )
        if request.method == "POST" and request.url.path == "/sandboxes/cpod-new/fork":
            return httpx.Response(
                200,
                json={
                    "sandboxID": "cpod-fork",
                    "templateID": "cubeplex-ubuntu24-desktop",
                    "state": "Running",
                },
            )
        if request.method == "POST" and request.url.path == "/sandboxes/cpod-new/timeout":
            return httpx.Response(200, json={"timeoutSeconds": 1800})
        if request.method == "DELETE" and request.url.path == "/sandboxes/cpod-new":
            return httpx.Response(200, json={"status": "deleted"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    cfg_values = {
        "sandbox.provider": "cubepod",
        "sandbox.warm_pool_ref": "pool-ubuntu",
        "sandbox.domain": "http://cubepod-gw:8080",
        "sandbox.image": "cubeplex-ubuntu24-desktop",
        "sandbox.api_key": "gw-key",
        "sandbox.egress_exchange_host": "egress.internal:8443",
        "sandbox.volume.enabled": True,
        "sandbox.volume.pvc_prefix": "cubeplex-user",
        "sandbox.ttl": 1800,
    }

    @asynccontextmanager
    async def fake_session_ctx():
        yield AsyncMock()

    session_factory = MagicMock(side_effect=fake_session_ctx)

    with patch("cubeplex.sandbox.manager.config") as mock_cfg:
        mock_cfg.get.side_effect = lambda k, default=None: cfg_values.get(k, default)
        mgr = SandboxManager(session_factory=session_factory, encryption_backend=MagicMock())

    mgr._build_cubepod_client = lambda *, request_timeout=None: AsyncCubePod(  # type: ignore[method-assign]
        base_url="http://cubepod-gw:8080",
        api_key="gw-key",
        timeout=float(request_timeout or 60),
        transport=transport,
        max_retries=0,
    )

    policy = EffectivePolicy(
        default_image="cubeplex-ubuntu24-desktop",
        network_rules=[{"action": "allow", "target": "api.openai.com"}],
        network_default_action="deny",
        egress_proxy="http://proxy.internal:3128",
        command_rules=[],
        resource_cpu="2",
        resource_memory="4Gi",
        storage="10Gi",
    )
    record = SimpleNamespace(
        id="sbx-row-1",
        org_id="org-1",
        workspace_id="ws-1",
        user_id="user-1",
        scope_type="user",
        scope_id="user-1",
        sandbox_id=None,
        status="provisioning",
        image="cubeplex-ubuntu24-desktop",
        provider="cubepod",
        deleted_at=None,
        cleanup_action=None,
        last_activity_at=datetime.now(UTC) - timedelta(seconds=10),
    )

    repo = MagicMock()
    repo.set_sandbox_id = AsyncMock()
    repo.promote_to_running = AsyncMock()
    repo.update_activity = AsyncMock()
    repo.mark_terminated = AsyncMock()
    repo.delete_record = AsyncMock()
    repo.reserve = AsyncMock(
        return_value=SimpleNamespace(
            id="sbx-row-2",
            org_id="org-1",
            workspace_id="ws-1",
            user_id="user-1",
            scope_type="topic",
            scope_id="topic-1",
            image="cubeplex-ubuntu24-desktop",
        )
    )

    fake_resolved = [
        SimpleNamespace(
            id="ev-1",
            env_name="OPENAI_API_KEY",
            is_secret=True,
            value="",
            credential_id="cred-1",
            hosts=["api.openai.com"],
            header_names=["Authorization"],
        )
    ]

    with (
        patch("cubeplex.sandbox.manager.UserSandboxRepository", return_value=repo) as repo_cls,
        patch("cubeplex.sandbox.manager.SandboxEnvResolver") as env_res_cls,
        patch("cubeplex.sandbox.manager.EgressRefRepository") as ref_repo_cls,
    ):
        repo_cls.get_by_id_system = AsyncMock(
            return_value=SimpleNamespace(
                **{**record.__dict__, "sandbox_id": "cpod-new", "status": "running"}
            )
        )
        env_res_cls.return_value.resolve = AsyncMock(return_value=fake_resolved)
        ref_repo_cls.return_value.add = AsyncMock()
        ref_repo_cls.return_value.extend_for_hashes = AsyncMock(return_value=0)
        ref_repo_cls.return_value.revoke_for_sandbox = AsyncMock()

        backend = await mgr._provision_cubepod_container(
            AsyncMock(),
            record,  # type: ignore[arg-type]
            policy=policy,
            user_id="user-1",
        )
        assert backend.id == "cpod-new"
        assert "OPENAI_API_KEY" in backend._run_env  # noqa: SLF001
        assert backend._run_env["OPENAI_API_KEY"]  # noqa: SLF001
        assert seen_create_payloads[0]["extension"]["warmPoolRef"] == "pool-ubuntu"  # type: ignore[index]
        assert seen_create_payloads[0]["timeoutSeconds"] == 1800
        assert seen_create_payloads[0]["networkPolicy"] == {
            "defaultAction": "deny",
            "egress": [
                {"action": "allow", "target": "api.openai.com"},
                {"action": "allow", "target": "egress.internal:8443"},
            ],
            "egressProxy": "http://proxy.internal:3128",
        }
        await backend.close()

        await mgr.reset_user_sandbox_rootfs("sbx-row-1")
        forked = await mgr.fork_user_sandbox(
            source_user_sandbox_id="sbx-row-1",
            target_scope_type="topic",
            target_scope_id="topic-1",
            user_id="user-1",
        )
        assert forked.sandbox.id == "cpod-fork"
        assert forked.user_sandbox_id == "sbx-row-2"
        await forked.sandbox.close()

        await mgr._renew_provider_ttl("cpod-new", provider="cubepod")
        with patch(
            "cubeplex.sandbox.command_coordinator.kill_sandbox_commands",
            AsyncMock(),
        ):
            await mgr._kill_record(
                AsyncMock(),
                repo,
                SimpleNamespace(**{**record.__dict__, "sandbox_id": "cpod-new"}),  # type: ignore[arg-type]
                mgr._build_connection_config(),
            )
        repo.mark_terminated.assert_awaited()


async def test_cubepod_error_branches_streaming_browser_and_terminal() -> None:
    import asyncio
    from pathlib import PurePosixPath

    from cubeplex.sandbox.base import SandboxConflictError
    from cubeplex.sandbox.cubepod import _parse_modified_at, connect_cubepod_sandbox

    # _parse_modified_at variants
    naive_dt = datetime(2026, 11, 13, 12, 0, 0)
    assert _parse_modified_at(naive_dt) == datetime(2026, 11, 13, 12, 0, 0, tzinfo=UTC)
    assert _parse_modified_at("not-a-timestamp") is None

    sandbox_alive = True
    sandbox_status_code = 200
    cmd_killed_code = -9

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sandboxes/cpod-1" and request.method == "GET":
            if sandbox_status_code != 200:
                return httpx.Response(sandbox_status_code, text="gateway down")
            state = "Running" if sandbox_alive else "Terminated"
            return httpx.Response(
                200,
                json={"sandboxID": "cpod-1", "templateID": "ubuntu", "state": state},
            )
        if request.url.path == "/sandboxes/cpod-1/connect":
            return httpx.Response(
                200,
                json={"sandboxID": "cpod-1", "templateID": "ubuntu", "state": "Stopped"},
            )
        if request.url.path == "/sandboxes/cpod-1/commands/gone-sb/status":
            return httpx.Response(404, text="Sandbox not found")
        if request.url.path == "/sandboxes/cpod-1/commands/cmd-term/status":
            return httpx.Response(404, text="command not found")
        if request.url.path == "/sandboxes/cpod-1/commands/cmd-killed/logs":
            return httpx.Response(
                200,
                json={
                    "id": "cmd-killed",
                    "content": "bye",
                    "cursor": 3,
                    "running": False,
                    "exitCode": cmd_killed_code,
                },
            )
        if request.url.path == "/sandboxes/cpod-1/commands/run":
            body = json.loads(request.content.decode())
            if body.get("background") and body["command"] == "fast-bg":
                return httpx.Response(
                    200,
                    json={
                        "id": "cmd-fast",
                        "output": "instant",
                        "running": False,
                        "exitCode": 0,
                        "cursor": 7,
                    },
                )
            if body.get("background") and body["command"] == "slow-stream":
                return httpx.Response(200, json={"id": "cmd-slow", "running": True, "cursor": 0})
            return httpx.Response(500, text="exec boom")
        if request.url.path == "/sandboxes/cpod-1/commands/cmd-slow/logs":
            return httpx.Response(200, json={"id": "cmd-slow", "content": "", "running": True})
        if request.url.path == "/sandboxes/cpod-1/commands/cmd-slow/kill":
            return httpx.Response(200, json={"status": "killed"})
        if (
            request.url.path == "/sandboxes/cpod-1/files"
            and request.url.params.get("path") == "/500"
        ):
            return httpx.Response(500, text="stream error")
        return httpx.Response(404, text="not found")

    sb = _make_mock_sandbox(handler)
    assert sb.workdir == "/workspace"
    sb._invoke_on_chunk(lambda _t: None, "")  # noqa: SLF001

    # 500 -> SandboxError
    with pytest.raises(SandboxError, match="CubePod API error 500"):
        await sb.execute("boom")

    # 500 on stream_file_bytes triggers ResponseNotRead fallback in _response_error_detail
    with pytest.raises(SandboxError, match="CubePod API error 500"):
        await sb.stream_file_bytes("/500")

    # Mid-stream HTTPError inside stream_file_bytes iterator
    async def broken_stream(_sid: str, _path: str) -> object:
        async def _gen():
            yield b"first"
            raise httpx.ReadError("socket reset")

        return _gen()

    orig_stream_file = sb._client.stream_file
    sb._client.stream_file = broken_stream  # type: ignore[method-assign]
    it = await sb.stream_file_bytes("/any")
    assert await anext(it) == b"first"
    with pytest.raises(SandboxError, match="socket reset"):
        await anext(it)
    sb._client.stream_file = orig_stream_file  # type: ignore[method-assign]

    # 404 with "sandbox not found" -> SandboxInstanceGoneError
    with pytest.raises(SandboxInstanceGoneError):
        await sb.observe(ProcessHandle(command_id="gone-sb", provider_ref="gone-sb"))

    # 404 command + sandbox terminal -> SandboxInstanceGoneError
    sandbox_alive = False
    with pytest.raises(SandboxInstanceGoneError):
        await sb.observe(ProcessHandle(command_id="cmd-term", provider_ref="cmd-term"))

    # 404 command + sandbox GET 404 -> SandboxInstanceGoneError
    sandbox_status_code = 404
    with pytest.raises(SandboxInstanceGoneError):
        await sb.observe(ProcessHandle(command_id="cmd-term", provider_ref="cmd-term"))

    # 404 command + sandbox GET 500 -> SandboxError
    sandbox_status_code = 500
    with pytest.raises(SandboxError, match="CubePod API error 500"):
        await sb.observe(ProcessHandle(command_id="cmd-term", provider_ref="cmd-term"))
    sandbox_status_code = 200
    sandbox_alive = True

    # _snapshot_status killed (-9) and acknowledge_output
    h_kill = ProcessHandle(command_id="cmd-killed", provider_ref="cmd-killed", log_cursor="0")
    snap_killed = await sb.poll(h_kill)
    assert snap_killed.status == "killed"
    await sb.acknowledge_output(h_kill, "3")
    assert h_kill.log_cursor == "3"

    # Streaming execute fast-complete (initial output + running=False)
    chunks_fast: list[str] = []
    res_fast = await sb.execute("fast-bg", on_chunk=chunks_fast.append)
    assert res_fast == ExecuteResult(output="instant", exit_code=0)
    assert chunks_fast == ["instant"]

    # Start with immediate output triggers on_chunk
    chunks_start: list[str] = []
    started_h = await sb.start("fast-bg", on_chunk=chunks_start.append)
    assert started_h.command_id == "cmd-fast"
    assert chunks_start == ["instant"]

    # Streaming execute timeout kills background command
    async def slow_poll(*_args: object, **_kwargs: object) -> object:
        await asyncio.sleep(0.2)
        return SimpleNamespace(content="", cursor=0, running=True, exit_code=None)

    orig_poll = sb._client.poll_command
    sb._client.poll_command = slow_poll  # type: ignore[method-assign]
    res_timeout = await sb.execute("slow-stream", timeout=0, on_chunk=lambda _t: None)
    assert res_timeout == ExecuteResult(output="[timeout]", exit_code=-1)
    sb._client.poll_command = orig_poll  # type: ignore[method-assign]

    # connect_cubepod_sandbox on terminal state -> SandboxInstanceGoneError
    with pytest.raises(SandboxInstanceGoneError):
        await connect_cubepod_sandbox(sb._client, "cpod-1")  # noqa: SLF001

    # connect_or_resume domain required & cleanup on error
    with pytest.raises(SandboxError, match="domain is required"):
        await CubePodSandbox.connect_or_resume("cpod-1")
    with pytest.raises(SandboxInstanceGoneError):
        await CubePodSandbox.connect_or_resume(
            "cpod-1",
            domain="gw.local:8080",
            transport=httpx.MockTransport(handler),
        )

    # fork missing sandbox_id
    orig_fork = sb._client.fork
    sb._client.fork = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(sandbox_id="")
    )
    with pytest.raises(SandboxError, match="missing sandboxID"):
        await sb.fork()
    sb._client.fork = orig_fork  # type: ignore[method-assign]

    # Browser & terminal failure + fallback branches
    sb.install_browser_tab_follow = AsyncMock()  # type: ignore[method-assign]
    exec_mock = AsyncMock(
        side_effect=[
            ExecuteResult(output="", exit_code=0),  # test -x cubepod-desktop-ctl
            ExecuteResult(output="boom", exit_code=1),  # cubepod-desktop-ctl start fails
        ]
    )
    sb.execute = exec_mock  # type: ignore[method-assign]
    with pytest.raises(SandboxError, match="failed to start sandbox browser"):
        await sb.start_browser()

    # Fallback to /usr/local/bin/start-browser.sh (success + failure)
    sb.execute = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            ExecuteResult(output="", exit_code=1),  # no cubepod-desktop-ctl
            ExecuteResult(output="", exit_code=0),  # start-browser.sh ok
            ExecuteResult(output="", exit_code=1),  # no cubepod-desktop-ctl
            ExecuteResult(output="bad", exit_code=1),  # start-browser.sh fails
        ]
    )
    await sb.start_browser()
    assert sb._browser_port == sb.BROWSER_PORT  # noqa: SLF001
    with pytest.raises(SandboxError, match="failed to start sandbox browser"):
        await sb.start_browser()

    # _panel_endpoint unconfigured URL
    with patch("cubeplex.sandbox.cubepod.get_panel_base_url", return_value=""):
        with pytest.raises(SandboxError, match="api.public_url"):
            await sb.get_browser_endpoint()

    # _write_terminal_env failure + start_terminal user/bare-bash/failure branches
    sb.execute = AsyncMock(return_value=ExecuteResult(output="err", exit_code=1))  # type: ignore[method-assign]
    with pytest.raises(SandboxError, match="failed to write sandbox terminal env"):
        await sb.start_terminal()

    sb_user = _make_mock_sandbox(handler, run_uid=None, run_gid=None)
    sb_user.execute = AsyncMock(return_value=ExecuteResult(output="", exit_code=0))  # type: ignore[method-assign]
    await sb_user.start_terminal()
    assert "runuser -u cubeplex" in sb_user.execute.call_args_list[1].args[0]

    sb_bare = CubePodSandbox(
        sandbox_id="cpod-1",
        client=sb._client,  # noqa: SLF001
        owns_client=False,
        run_uid=None,
        run_gid=None,
        run_user=None,
    )
    sb_bare.execute = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            ExecuteResult(output="", exit_code=0),
            ExecuteResult(output="ttyd failed", exit_code=1),
        ]
    )
    with pytest.raises(SandboxError, match="failed to start sandbox terminal"):
        await sb_bare.start_terminal()
    await sb_user.close()
    await sb.close()
    del PurePosixPath, SandboxConflictError


async def test_sandbox_manager_cubepod_connect_resume_pause_reconcile_and_errors() -> None:
    from cubeplex.sandbox.base import SandboxConflictError

    state_map: dict[str, tuple[int, dict[str, object]]] = {
        "cpod-run": (200, {"sandboxID": "cpod-run", "templateID": "ubuntu", "state": "Running"}),
        "cpod-paused": (
            200,
            {"sandboxID": "cpod-paused", "templateID": "ubuntu", "state": "Paused"},
        ),
        "cpod-gone": (404, {"error": "not found"}),
        "cpod-term": (
            200,
            {"sandboxID": "cpod-term", "templateID": "ubuntu", "state": "Terminated"},
        ),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "sandboxes":
            sid = parts[1]
            if len(parts) == 2 and request.method == "GET":
                code, body = state_map.get(sid, (404, {"error": "not found"}))
                return httpx.Response(code, json=body)
            if len(parts) == 3 and parts[2] == "connect":
                if sid == "cpod-fail-connect":
                    return httpx.Response(500, text="connect fail")
                return httpx.Response(
                    200, json={"sandboxID": sid, "templateID": "ubuntu", "state": "Running"}
                )
            if len(parts) == 3 and parts[2] == "pause":
                if sid == "cpod-pause-fail":
                    return httpx.Response(500, text="pause fail")
                return httpx.Response(
                    200, json={"sandboxID": sid, "templateID": "ubuntu", "state": "Paused"}
                )
            if len(parts) == 2 and request.method == "DELETE":
                if sid == "cpod-kill-404":
                    return httpx.Response(404, text="not found")
                if sid == "cpod-kill-500":
                    return httpx.Response(500, text="delete fail")
                return httpx.Response(200, json={"status": "deleted"})
            if len(parts) == 3 and parts[2] == "timeout":
                return httpx.Response(500, text="timeout fail")
            if len(parts) == 3 and parts[2] == "fork":
                return httpx.Response(500, text="fork fail")
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    cfg_values = {
        "sandbox.provider": "cubepod",
        "sandbox.domain": "http://cubepod-gw:8080",
        "sandbox.image": "cubeplex-ubuntu24-desktop",
        "sandbox.api_key": "gw-key",
        "sandbox.egress_exchange_host": "egress.internal:8443",
        "sandbox.pause_on_idle": True,
        "sandbox.ttl": 1800,
    }

    @asynccontextmanager
    async def fake_session_ctx():
        yield AsyncMock()

    session_factory = MagicMock(side_effect=fake_session_ctx)
    with patch("cubeplex.sandbox.manager.config") as mock_cfg:
        mock_cfg.get.side_effect = lambda k, default=None: cfg_values.get(k, default)
        mgr = SandboxManager(session_factory=session_factory, encryption_backend=MagicMock())

    mgr._build_cubepod_client = lambda *, request_timeout=None: AsyncCubePod(  # type: ignore[method-assign]
        base_url="http://cubepod-gw:8080",
        api_key="gw-key",
        timeout=float(request_timeout or 60),
        transport=transport,
        max_retries=0,
    )

    repo = MagicMock()
    repo.update_activity = AsyncMock()
    repo.mark_terminated = AsyncMock()
    repo.mark_paused = AsyncMock()
    repo.mark_running = AsyncMock(return_value=True)
    repo.mark_resuming = AsyncMock(return_value=True)
    repo.mark_kill_pending = AsyncMock()
    repo.touch_provider_check = AsyncMock()
    repo.claim_pausing = AsyncMock(return_value=True)
    repo.delete_record = AsyncMock()
    repo.reserve = AsyncMock(
        return_value=SimpleNamespace(
            id="sbx-fork-row",
            org_id="org-1",
            workspace_id="ws-1",
            user_id="u-1",
            scope_type="topic",
            scope_id="t-1",
            image="ubuntu",
        )
    )

    def _rec(
        sid: str | None, *, status: str = "running", provider: str = "cubepod"
    ) -> SimpleNamespace:
        return SimpleNamespace(
            id="row-1",
            org_id="org-1",
            workspace_id="ws-1",
            user_id="u-1",
            scope_type="user",
            scope_id="u-1",
            sandbox_id=sid,
            status=status,
            image="ubuntu",
            provider=provider,
            deleted_at=None,
            cleanup_action=None,
            last_activity_at=datetime.now(UTC),
        )

    fake_resolved: list[object] = []

    with (
        patch("cubeplex.sandbox.manager.UserSandboxRepository", return_value=repo) as repo_cls,
        patch("cubeplex.sandbox.manager.SandboxEnvResolver") as env_res_cls,
        patch("cubeplex.sandbox.manager.EgressRefRepository") as ref_repo_cls,
        patch("cubeplex.sandbox.command_coordinator.kill_sandbox_commands", AsyncMock()),
    ):
        env_res_cls.return_value.resolve = AsyncMock(return_value=fake_resolved)
        ref_repo_cls.return_value.add = AsyncMock()
        ref_repo_cls.return_value.extend_for_hashes = AsyncMock(return_value=0)
        ref_repo_cls.return_value.revoke_for_sandbox = AsyncMock()

        # _connect_existing healthy & unhealthy
        conn_cfg = mgr._build_connection_config()
        sb = await mgr._connect_existing(
            AsyncMock(),
            repo,
            _rec("cpod-run"),
            conn_config=conn_cfg,
            user_id="u-1",  # type: ignore[arg-type]
        )
        assert sb.id == "cpod-run"
        await sb.close()

        with pytest.raises(SandboxError, match="unhealthy"):
            await mgr._connect_existing(
                AsyncMock(),
                repo,
                _rec("cpod-term"),
                conn_config=conn_cfg,
                user_id="u-1",  # type: ignore[arg-type]
            )
        repo.mark_terminated.assert_awaited()

        # connect_command_instance (running, terminated, paused)
        cmd_session = AsyncMock()
        cmd_session.execute = AsyncMock(
            return_value=SimpleNamespace(
                scalar_one_or_none=lambda: SimpleNamespace(
                    sandbox_instance_id="cpod-run", provider="cubepod"
                )
            )
        )

        @asynccontextmanager
        async def cmd_sess_ctx():
            yield cmd_session

        mgr._session_factory = MagicMock(side_effect=cmd_sess_ctx)
        async with mgr.connect_command_instance(
            command_id="c-1", org_id="org-1", workspace_id="ws-1"
        ) as obs_sb:
            assert obs_sb.id == "cpod-run"

        cmd_session.execute = AsyncMock(
            return_value=SimpleNamespace(
                scalar_one_or_none=lambda: SimpleNamespace(
                    sandbox_instance_id="cpod-term", provider="cubepod"
                )
            )
        )
        with pytest.raises(SandboxInstanceGoneError):
            async with mgr.connect_command_instance(
                command_id="c-1", org_id="org-1", workspace_id="ws-1"
            ):
                pass

        cmd_session.execute = AsyncMock(
            return_value=SimpleNamespace(
                scalar_one_or_none=lambda: SimpleNamespace(
                    sandbox_instance_id="cpod-paused", provider="cubepod"
                )
            )
        )
        with pytest.raises(SandboxError, match="Paused"):
            async with mgr.connect_command_instance(
                command_id="c-1", org_id="org-1", workspace_id="ws-1"
            ):
                pass

        mgr._session_factory = session_factory

        # _resume_record & _await_resumed_by_winner cubepod
        with patch.object(
            CubePodSandbox,
            "connect_or_resume",
            AsyncMock(
                return_value=CubePodSandbox(
                    sandbox_id="cpod-paused",
                    client=mgr._build_cubepod_client(),
                    owns_client=True,
                )
            ),
        ):
            resumed = await mgr._resume_record(
                AsyncMock(),
                repo,
                _rec("cpod-paused", status="paused"),  # type: ignore[arg-type]
                conn_cfg,
                org_id="org-1",
                workspace_id="ws-1",
                user_id="u-1",
            )
            assert resumed is not None and resumed.id == "cpod-paused"
            await resumed.close()

        mgr._await_stable_status = AsyncMock(  # type: ignore[method-assign]
            return_value=_rec("cpod-run", status="running")
        )
        winner_sb = await mgr._await_resumed_by_winner(
            "row-1", conn_cfg, org_id="org-1", workspace_id="ws-1", user_id="u-1"
        )
        assert winner_sb is not None and winner_sb.id == "cpod-run"
        await winner_sb.close()

        with pytest.raises(SandboxError):
            mgr._await_stable_status = AsyncMock(  # type: ignore[method-assign]
                return_value=_rec("cpod-fail-connect", status="running")
            )
            await mgr._await_resumed_by_winner(
                "row-1", conn_cfg, org_id="org-1", workspace_id="ws-1", user_id="u-1"
            )

        # pause_idle cubepod success + fallback to kill
        repo_cls.list_idle_to_pause_system = AsyncMock(
            return_value=[_rec("cpod-run"), _rec("cpod-pause-fail")]
        )
        await mgr.pause_idle()
        repo.mark_terminated.assert_awaited()

        # reconcile_transients cubepod: Paused, Running, 404 gone
        repo_cls.list_transient_for_reconcile_system = AsyncMock(
            return_value=[
                _rec("cpod-paused", status="pausing"),
                _rec("cpod-run", status="resuming"),
                _rec("cpod-gone", status="pausing"),
            ]
        )
        await mgr.reconcile_transients(claim_timeout=0)
        repo.mark_paused.assert_awaited()
        repo.mark_running.assert_awaited()

        # _kill_record 404 (already gone) + 500 (mark_kill_pending)
        await mgr._kill_record(AsyncMock(), repo, _rec("cpod-kill-404"), conn_cfg)  # type: ignore[arg-type]
        await mgr._kill_record(AsyncMock(), repo, _rec("cpod-kill-500"), conn_cfg)  # type: ignore[arg-type]
        repo.mark_kill_pending.assert_awaited_once()

        # _renew_provider_ttl failure is non-fatal
        await mgr._renew_provider_ttl("cpod-run", provider="cubepod")

        # reset_user_sandbox_rootfs & fork_user_sandbox error branches
        repo_cls.get_by_id_system = AsyncMock(return_value=None)
        with pytest.raises(SandboxError, match="not found"):
            await mgr.reset_user_sandbox_rootfs("missing")
        with pytest.raises(SandboxError, match="not found"):
            await mgr.fork_user_sandbox(
                source_user_sandbox_id="missing",
                target_scope_type="topic",
                target_scope_id="t",
                user_id="u-1",
            )

        repo_cls.get_by_id_system = AsyncMock(return_value=_rec("cpod-run", status="terminated"))
        with pytest.raises(SandboxConflictError):
            await mgr.reset_user_sandbox_rootfs("row-1")

        repo_cls.get_by_id_system = AsyncMock(
            return_value=_rec("cpod-run", status="running", provider="opensandbox")
        )
        with pytest.raises(SandboxError, match="only supported by cubepod"):
            await mgr.reset_user_sandbox_rootfs("row-1")
        with pytest.raises(SandboxError, match="only supported by cubepod"):
            await mgr.fork_user_sandbox(
                source_user_sandbox_id="row-1",
                target_scope_type="topic",
                target_scope_id="t",
                user_id="u-1",
            )

        repo_cls.get_by_id_system = AsyncMock(return_value=_rec("cpod-run", status="running"))
        with pytest.raises(SandboxError, match="CubePod API error 500"):
            await mgr.fork_user_sandbox(
                source_user_sandbox_id="row-1",
                target_scope_type="topic",
                target_scope_id="t",
                user_id="u-1",
            )
        repo.delete_record.assert_awaited()

        # _provision_cubepod_container failure paths + fork/winner egress failure
        from cubeplex.repositories.user_sandbox import SandboxCleanupRequestedError

        policy = EffectivePolicy(
            default_image="ubuntu",
            network_rules=[],
            network_default_action="allow",
            egress_proxy=None,
            command_rules=[],
            resource_cpu="1",
            resource_memory="1Gi",
            storage=None,
        )

        # Pre-create failure releases provisioning row
        repo.delete_record.reset_mock()
        with patch.object(
            mgr, "_build_cubepod_client", side_effect=RuntimeError("boom before create")
        ):
            with pytest.raises(RuntimeError, match="boom before create"):
                await mgr._provision_cubepod_container(
                    AsyncMock(),
                    _rec(None, status="provisioning"),  # type: ignore[arg-type]
                    policy=policy,
                    user_id="u-1",
                )
        repo.delete_record.assert_awaited_once()

        def ok_handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and request.url.path == "/sandboxes":
                return httpx.Response(
                    200,
                    json={"sandboxID": "cpod-created", "templateID": "ubuntu", "state": "Running"},
                )
            if request.method == "POST" and request.url.path == "/sandboxes/cpod-run/fork":
                return httpx.Response(
                    200,
                    json={"sandboxID": "cpod-fork-ok", "templateID": "ubuntu", "state": "Running"},
                )
            if request.method == "POST" and request.url.path == "/sandboxes/cpod-run/connect":
                return httpx.Response(
                    200,
                    json={"sandboxID": "cpod-run", "templateID": "ubuntu", "state": "Running"},
                )
            return httpx.Response(200, json={})

        mgr._build_cubepod_client = lambda *, request_timeout=None: AsyncCubePod(  # type: ignore[method-assign]
            base_url="http://cubepod-gw:8080",
            api_key="gw-key",
            timeout=float(request_timeout or 60),
            transport=httpx.MockTransport(ok_handler),
            max_retries=0,
        )

        repo.set_sandbox_id = AsyncMock(side_effect=SandboxCleanupRequestedError("cleanup"))
        with pytest.raises(SandboxConflictError):
            await mgr._provision_cubepod_container(
                AsyncMock(),
                _rec(None, status="provisioning"),  # type: ignore[arg-type]
                policy=policy,
                user_id="u-1",
            )

        repo.set_sandbox_id = AsyncMock()
        repo.promote_to_running = AsyncMock()
        repo.mark_terminated.reset_mock()
        with patch.object(mgr, "_apply_egress", AsyncMock(side_effect=RuntimeError("egress fail"))):
            with pytest.raises(RuntimeError, match="egress fail"):
                await mgr._provision_cubepod_container(
                    AsyncMock(),
                    _rec(None, status="provisioning"),  # type: ignore[arg-type]
                    policy=policy,
                    user_id="u-1",
                )
            repo.mark_terminated.assert_awaited()

            with pytest.raises(RuntimeError, match="egress fail"):
                await mgr.fork_user_sandbox(
                    source_user_sandbox_id="row-1",
                    target_scope_type="topic",
                    target_scope_id="t",
                    user_id="u-1",
                )

            mgr._await_stable_status = AsyncMock(  # type: ignore[method-assign]
                return_value=_rec("cpod-run", status="running")
            )
            with pytest.raises(RuntimeError, match="egress fail"):
                await mgr._await_resumed_by_winner(
                    "row-1", conn_cfg, org_id="org-1", workspace_id="ws-1", user_id="u-1"
                )


async def test_routes_and_lazy_cubepod_coverage() -> None:
    from starlette.responses import StreamingResponse

    from cubeplex.api.routes.v1.background_tasks import get_background_task_output

    def handler(request: httpx.Request) -> httpx.Response:
        path_param = request.url.params.get("path", "")
        if path_param == "/workspace/ok.log":
            return httpx.Response(200, content=b"line1\nline2\n")
        if path_param == "/workspace/big.log":
            return httpx.Response(200, content=b"x" * 200)
        if path_param == "/workspace/missing.log":
            return httpx.Response(404, text="not found")
        return httpx.Response(500, text="server err")

    sb = _make_mock_sandbox(handler)
    ctx = SimpleNamespace(org_id="org-1", workspace_id="ws-1", user=SimpleNamespace(id="u-1"))
    session = AsyncMock()

    # ws_sandbox error branches for CubePodSandbox
    manager = MagicMock()
    manager.get_or_create = AsyncMock(
        return_value=SimpleNamespace(sandbox=sb, user_sandbox_id="sbx-1")
    )
    manager.touch = AsyncMock()
    manager.reset_user_sandbox_rootfs = AsyncMock(side_effect=SandboxError("reset failed"))

    with (
        patch("cubeplex.api.routes.v1.ws_sandbox.get_sandbox_manager", return_value=manager),
        patch(
            "cubeplex.api.routes.v1.ws_sandbox._resolve_sandbox_scope",
            AsyncMock(return_value=("user", "u-1", "u-1")),
        ),
    ):
        with (
            patch("cubeplex.api.routes.v1.ws_sandbox.MAX_PREVIEW_BYTES", 50),
            pytest.raises(HTTPException) as exc_413,
        ):
            await get_sandbox_file_content(ctx=ctx, session=session, path="/workspace/big.log")
        assert exc_413.value.status_code == 413

        with pytest.raises(HTTPException) as exc_404:
            await get_sandbox_file_content(ctx=ctx, session=session, path="/workspace/missing.log")
        assert exc_404.value.status_code == 404

        with pytest.raises(HTTPException) as exc_dl_404:
            await download_sandbox_file(ctx=ctx, session=session, path="/workspace/missing.log")
        assert exc_dl_404.value.status_code == 404

        repo_inst = MagicMock()
        repo_inst.get_active_by_scope = AsyncMock(return_value=None)
        with (
            patch(
                "cubeplex.api.routes.v1.ws_sandbox.UserSandboxRepository", return_value=repo_inst
            ),
            pytest.raises(HTTPException) as exc_reset_404,
        ):
            await reset_sandbox_rootfs(ctx=ctx, session=session)
        assert exc_reset_404.value.status_code == 404

        repo_inst.get_active_by_scope = AsyncMock(
            return_value=SimpleNamespace(id="sbx-1", provider="cubepod")
        )
        with (
            patch(
                "cubeplex.api.routes.v1.ws_sandbox.UserSandboxRepository", return_value=repo_inst
            ),
            pytest.raises(HTTPException) as exc_reset_503,
        ):
            await reset_sandbox_rootfs(ctx=ctx, session=session)
        assert exc_reset_503.value.status_code == 503

    # background_tasks.py CubePodSandbox preview, 413, streaming download, 404
    @asynccontextmanager
    async def connect_ctx(**_kwargs: object):
        fresh = _make_mock_sandbox(handler)
        try:
            yield fresh
        finally:
            await fresh.close()

    bg_mgr = MagicMock()
    bg_mgr.connect_command_instance = connect_ctx
    bg_mgr.ensure_running = AsyncMock(
        side_effect=lambda _id: SimpleNamespace(
            sandbox=_make_mock_sandbox(handler), user_sandbox_id="sbx-1"
        )
    )

    def _task_item(log_path: str, *, terminal: bool = False) -> SimpleNamespace:
        return SimpleNamespace(
            task=SimpleNamespace(
                state="succeeded" if terminal else "running",
                result_readiness="ready",
                result_unavailable_reason=None,
            ),
            command=SimpleNamespace(id="cmd-1", user_sandbox_id="sbx-1", log_path=log_path),
        )

    query_svc = MagicMock()
    with (
        patch("cubeplex.api.routes.v1.background_tasks._require_conversation", AsyncMock()),
        patch("cubeplex.api.routes.v1.background_tasks._query_service", return_value=query_svc),
        patch("cubeplex.api.routes.v1.background_tasks.get_sandbox_manager", return_value=bg_mgr),
        patch("cubeplex.api.routes.v1.background_tasks.UserSandboxRepository") as bg_repo_cls,
    ):
        bg_repo_cls.return_value.get_by_id = AsyncMock(
            return_value=SimpleNamespace(id="sbx-1", deleted_at=None)
        )
        query_svc.get_task = AsyncMock(return_value=_task_item("/workspace/ok.log", terminal=True))
        preview = await get_background_task_output(
            workspace_id="ws-1",
            conversation_id="conv-1",
            task_id="t-1",
            session=session,
            ctx=ctx,
            download=False,
        )
        assert preview == {"content": "line1\nline2\n", "mime_type": "text/plain"}

        query_svc.get_task = AsyncMock(return_value=_task_item("/workspace/ok.log"))
        dl_resp = await get_background_task_output(
            workspace_id="ws-1",
            conversation_id="conv-1",
            task_id="t-1",
            session=session,
            ctx=ctx,
            download=True,
        )
        assert isinstance(dl_resp, StreamingResponse)
        body = b"".join([chunk async for chunk in dl_resp.body_iterator])  # type: ignore[union-attr]
        assert body == b"line1\nline2\n"

        with (
            patch.object(
                CubePodSandbox,
                "read_text_file",
                AsyncMock(side_effect=FileTooLargeError("too big")),
            ),
            pytest.raises(HTTPException) as exc_bg_413,
        ):
            await get_background_task_output(
                workspace_id="ws-1",
                conversation_id="conv-1",
                task_id="t-1",
                session=session,
                ctx=ctx,
                download=False,
            )
        assert exc_bg_413.value.status_code == 413

    # LazySandbox capability delegation when initialized
    delegate = AsyncMock()
    delegate.supports_archive_sync = MagicMock(return_value=True)
    delegate.supports_reset_rootfs = MagicMock(return_value=True)
    delegate.supports_fork = MagicMock(return_value=True)
    delegate.fork = AsyncMock(return_value=SimpleNamespace(id="child-1"))
    lazy = LazySandbox(
        manager=MagicMock(),
        scope_type="user",
        scope_id="u1",
        user_id="u1",
        org_id="o1",
        workspace_id="w1",
    )
    lazy._sandbox = delegate  # noqa: SLF001
    assert lazy.supports_archive_sync() is True
    assert lazy.supports_reset_rootfs() is True
    assert lazy.supports_fork() is True
    await lazy.archive_sync(b"gz", dest="/workspace/.skills", remove_paths=["old"])
    await lazy.reset_rootfs()
    forked = await lazy.fork(volume_key="vk")
    assert forked.id == "child-1"
    delegate.archive_sync.assert_awaited_once()
    delegate.reset_rootfs.assert_awaited_once()

    # sandbox_panel.py opensandbox fallback branch
    panel_cfg = {"sandbox.provider": "opensandbox", "sandbox.domain": "opensandbox.internal:8080"}
    with patch("cubeplex.config.config") as mock_cfg:
        mock_cfg.get.side_effect = lambda k, default=None: panel_cfg.get(k, default)
        http_url, _ = _build_panel_http_upstream(
            sandbox_id="os-1", port=8080, full_path="index.html", query="", headers={}
        )
        assert http_url == "http://opensandbox.internal:8080/sandboxes/os-1/proxy/8080/index.html"
        ws_url, extra = _build_panel_ws_upstream(
            sandbox_id="os-1", port=7681, full_path="ws", query="x=1"
        )
        assert ws_url == "ws://opensandbox.internal:8080/sandboxes/os-1/proxy/7681/ws?x=1"
        assert extra is None

    await sb.close()
