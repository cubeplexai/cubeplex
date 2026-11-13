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
    CubePodControlClient,
    CubePodSandbox,
    FileTooLargeError,
    normalize_cubepod_base_url,
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
) -> CubePodSandbox:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    client = CubePodControlClient(
        domain=domain,
        api_key=api_key,
        transport=transport,
    )
    return CubePodSandbox(
        sandbox_id=sandbox_id,
        client=client,
        owns_client=True,
        run_uid=run_uid,
        run_gid=run_gid,
    )


def test_normalize_cubepod_base_url() -> None:
    assert normalize_cubepod_base_url("gw.local:8080") == "http://gw.local:8080"
    assert normalize_cubepod_base_url("https://gw.local/") == "https://gw.local"
    assert normalize_cubepod_base_url("http://gw.local:8080/") == "http://gw.local:8080"


async def test_client_auth_header_and_error_translation() -> None:
    seen_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(request.headers))
        if request.url.path == "/sandboxes/cpod-gone":
            return httpx.Response(404, json={"error": "not found"})
        if request.url.path == "/sandboxes/cpod-term":
            return httpx.Response(200, json={"sandboxID": "cpod-term", "state": "Stopped"})
        if request.url.path == "/sandboxes/cpod-err":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(500, text="boom")

    client = CubePodControlClient(
        domain="gw.local:8080",
        api_key="tok-123",
        transport=httpx.MockTransport(handler),
    )
    assert client.client._trust_env is False  # noqa: SLF001

    with pytest.raises(SandboxInstanceGoneError):
        await client.get_sandbox("cpod-gone")
    assert seen_headers[0]["authorization"] == "Bearer tok-123"

    with pytest.raises(SandboxInstanceGoneError):
        await client.get_sandbox("cpod-term")

    with pytest.raises(SandboxError, match="connection refused"):
        await client.get_sandbox("cpod-err")

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

    async def slow_request(*_args: object, **_kwargs: object) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(200, json={"output": "late", "exitCode": 0})

    sb = _make_mock_sandbox(lambda _req: httpx.Response(200))
    sb._client.request = slow_request  # type: ignore[method-assign]
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
            return httpx.Response(200, json={"sandboxID": "cpod-1", "state": "Running"})
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
            return httpx.Response(200, json={"state": "Paused"})
        if request.url.path == "/sandboxes/cpod-1/timeout":
            return httpx.Response(200, json={"timeoutSeconds": 600})
        if request.url.path == "/sandboxes/cpod-1/lease":
            return httpx.Response(200, json={"leaseSeconds": 120})
        if request.url.path == "/sandboxes/cpod-1/connect":
            return httpx.Response(200, json={"sandboxID": "cpod-1", "state": "Running"})
        if request.url.path == "/sandboxes/cpod-1/reset-rootfs":
            return httpx.Response(200, json={"rootfsGeneration": 2})
        if request.url.path == "/sandboxes/cpod-1/fork":
            return httpx.Response(200, json={"sandboxID": "cpod-fork-2", "state": "Running"})
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
                            "name": "notes.txt",
                            "path": "/workspace/notes.txt",
                            "is_dir": False,
                            "size": 5,
                            "modified_at": "2026-11-13T00:00:00Z",
                        }
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
            return httpx.Response(200, json={"sandboxID": "cpod-new", "state": "Running"})
        if request.method == "GET" and request.url.path == "/sandboxes/cpod-new":
            return httpx.Response(200, json={"sandboxID": "cpod-new", "state": "Running"})
        if request.method == "POST" and request.url.path == "/sandboxes/cpod-new/reset-rootfs":
            return httpx.Response(200, json={"rootfsGeneration": 2})
        if request.method == "POST" and request.url.path == "/sandboxes/cpod-new/fork":
            return httpx.Response(200, json={"sandboxID": "cpod-fork", "state": "Running"})
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

    mgr._build_cubepod_client = lambda *, request_timeout=None: CubePodControlClient(  # type: ignore[method-assign]
        domain="http://cubepod-gw:8080",
        api_key="gw-key",
        timeout=float(request_timeout or 60),
        transport=transport,
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
        assert seen_create_payloads[0]["warmPoolRef"] == "pool-ubuntu"
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
        await mgr._kill_record(
            AsyncMock(),
            repo,
            SimpleNamespace(**{**record.__dict__, "sandbox_id": "cpod-new"}),  # type: ignore[arg-type]
            mgr._build_connection_config(),
        )
        repo.mark_terminated.assert_awaited()
