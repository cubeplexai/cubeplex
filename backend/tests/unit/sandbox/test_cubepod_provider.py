"""Transport-level contract tests for CubePodProvider and CubePodSandbox."""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest
from cubepod import AsyncCubePod

import cubeplex.sandbox as sandbox_pkg
from cubeplex.config import config
from cubeplex.sandbox import providers as providers_pkg
from cubeplex.sandbox.base import (
    ExecuteResult,
    ProcessHandle,
    ProcessHandleNotFoundError,
    SandboxCapabilities,
    SandboxConflictError,
    SandboxError,
    SandboxFileTooLargeError,
    SandboxInstanceGoneError,
)
from cubeplex.sandbox.provider import ProviderInstanceState, RunIdentity, SandboxCreateSpec
from cubeplex.sandbox.providers.cubepod import CubePodProvider, CubePodSandbox
from cubeplex.sandbox.registry import get_provider_registry
from cubeplex.sandbox_policy.rules import SandboxNetworkPolicy, SandboxNetworkRule


def _json_response(
    status: int,
    payload: Any,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers)


def _spec(
    *,
    image: str = "cubeplex-ubuntu24-desktop",
    volume_key: str | None = "cubeplex-user-u1234567",
    network_policy: SandboxNetworkPolicy | None = None,
) -> SandboxCreateSpec:
    return SandboxCreateSpec(
        volume_key=volume_key,
        volume_mount_path="/workspace",
        storage="10Gi",
        image=image,
        workdir="/workspace",
        ttl_seconds=600,
        create_timeout_seconds=120,
        ready_timeout_seconds=5,
        request_timeout_seconds=30,
        run_identity=RunIdentity(user="cubeplex", uid=1000, gid=1000),
        env={"PLAIN_KEY": "plain"},
        resource_cpu="500m",
        resource_memory="512Mi",
        network_policy=network_policy,
        metadata={"org_id": "org_1"},
        user_sandbox_id="usbx_1",
    )


def _client_for(handler: Any) -> AsyncCubePod:
    transport = httpx.MockTransport(handler)
    return AsyncCubePod(
        base_url="http://cubepod-gateway:8080",
        api_key="cpod-secret",
        transport=transport,
        max_retries=0,
        trust_env=False,
    )


def test_cubepod_registry_and_export_boundaries() -> None:
    registry = get_provider_registry()
    provider = registry.get("cubepod")
    assert isinstance(provider, CubePodProvider)
    assert provider.name == "cubepod"
    assert provider.capabilities == SandboxCapabilities(
        background=True,
        reconnect=True,
        persistent_volume=True,
        pause=True,
        reset_rootfs=True,
        terminal=True,
        browser=True,
        egress_proxy=True,
        network_policy=True,
    )
    assert hasattr(providers_pkg, "CubePodProvider")
    assert hasattr(providers_pkg, "CubePodSandbox")
    assert not hasattr(sandbox_pkg, "CubePodProvider")
    assert not hasattr(sandbox_pkg, "CubePodSandbox")


def test_cubepod_validate_config_checks_required_and_placeholder_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CubePodProvider()

    def _set_cfg(*, base_url: Any, template: Any, api_key: Any = "") -> None:
        values = {
            "sandbox.cubepod.base_url": base_url,
            "sandbox.cubepod.default_template": template,
            "sandbox.cubepod.api_key": api_key,
        }
        monkeypatch.setattr(
            config,
            "get",
            lambda key, default=None: values.get(key, default),
        )

    _set_cfg(base_url="", template="cubeplex-ubuntu24-desktop")
    with pytest.raises(RuntimeError, match="CUBEPLEX_SANDBOX__CUBEPOD__BASE_URL"):
        provider.validate_config()

    _set_cfg(base_url="REPLACE_ME", template="cubeplex-ubuntu24-desktop")
    with pytest.raises(RuntimeError, match="placeholder"):
        provider.validate_config()

    _set_cfg(base_url="http://cubepod:8080", template="")
    with pytest.raises(RuntimeError, match="CUBEPLEX_SANDBOX__CUBEPOD__DEFAULT_TEMPLATE"):
        provider.validate_config()

    for bad_key in ("REPLACE_ME", "CHANGE_ME", "YOUR_API_KEY", "your-token-here", "TODO"):
        _set_cfg(
            base_url="http://cubepod:8080",
            template="cubeplex-ubuntu24-desktop",
            api_key=bad_key,
        )
        with pytest.raises(RuntimeError, match="CUBEPLEX_SANDBOX__CUBEPOD__API_KEY"):
            provider.validate_config()

    _set_cfg(base_url="http://cubepod:8080", template="cubeplex-custom:v1", api_key="")
    provider.validate_config()
    assert provider.default_image == "cubeplex-custom:v1"

    _set_cfg(
        base_url="http://cubepod:8080",
        template="cubeplex-custom:v1",
        api_key="real-api-token",
    )
    provider.validate_config()


async def test_cubepod_pooled_client_survives_sandbox_close_and_closes_on_provider_aclose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CubePodProvider()
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: {
            "sandbox.cubepod.base_url": "http://cubepod-gateway:8080",
            "sandbox.cubepod.api_key": "",
            "sandbox.cubepod.default_template": "cubeplex-ubuntu24-desktop",
        }.get(key, default),
    )
    client_one = await provider._get_client()
    client_two = await provider._get_client()
    assert client_one is client_two

    sandbox = CubePodSandbox(client=client_one, sandbox_id="cpod-1", workdir="/workspace")
    await sandbox.close()
    assert not client_one._client.is_closed

    await provider.aclose()
    assert client_one._client.is_closed


async def test_cubepod_create_uses_sandbox_id_on_created_before_poll_and_network_policy() -> None:
    events: list[str] = []
    create_bodies: list[dict[str, Any]] = []
    polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        if request.method == "POST" and request.url.path == "/sandboxes":
            body = json.loads(request.content.decode("utf-8"))
            create_bodies.append(body)
            events.append("post_create")
            # Intentionally omit legacy "id" to lock sandboxID -> sandbox_id usage.
            return _json_response(
                201,
                {
                    "sandboxID": "cpod-999",
                    "templateID": "cubeplex-ubuntu24-desktop",
                    "state": "Creating",
                },
            )
        if request.method == "GET" and request.url.path == "/sandboxes/cpod-999":
            polls += 1
            events.append(f"poll_{polls}")
            state = "Creating" if polls < 2 else "Running"
            return _json_response(
                200,
                {
                    "sandboxID": "cpod-999",
                    "templateID": "cubeplex-ubuntu24-desktop",
                    "state": state,
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    provider = CubePodProvider(client=_client_for(handler), poll_interval=0.0)
    policy = SandboxNetworkPolicy(
        default_action="deny",
        egress=(
            SandboxNetworkRule(action="allow", target="api.github.com"),
            SandboxNetworkRule(action="deny", target="169.254.169.254"),
        ),
    )

    async def on_created(sandbox_id: str) -> None:
        events.append(f"on_created:{sandbox_id}")

    sandbox = await provider.create(
        _spec(network_policy=policy),
        on_created=on_created,
    )
    assert sandbox.id == "cpod-999"
    assert events == ["post_create", "on_created:cpod-999", "poll_1", "poll_2"]
    assert create_bodies[0]["templateID"] == "cubeplex-ubuntu24-desktop"
    assert create_bodies[0]["extension"]["volumeKey"] == "cubeplex-user-u1234567"
    assert create_bodies[0]["extension"]["networkPolicy"] == {
        "defaultAction": "deny",
        "egress": [
            {"action": "allow", "target": "api.github.com"},
            {"action": "deny", "target": "169.254.169.254"},
        ],
    }


async def test_cubepod_connect_never_auto_resumes_and_resume_uses_connect() -> None:
    calls: list[tuple[str, str]] = []
    state = "Paused"
    resuming_polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal state, resuming_polls
        calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path == "/sandboxes/cpod-1":
            if state == "Resuming":
                resuming_polls += 1
                if resuming_polls >= 2:
                    state = "Running"
            return _json_response(
                200,
                {
                    "sandboxID": "cpod-1",
                    "templateID": "cubeplex-ubuntu24-desktop",
                    "state": state,
                },
            )
        if request.method == "GET" and request.url.path == "/sandboxes/cpod-missing":
            return _json_response(404, {"error": "not_found"})
        if request.method == "POST" and request.url.path == "/sandboxes/cpod-1/connect":
            state = "Resuming"
            return _json_response(
                200,
                {
                    "sandboxID": "cpod-1",
                    "templateID": "cubeplex-ubuntu24-desktop",
                    "state": "Resuming",
                },
            )
        if request.method == "POST" and request.url.path == "/sandboxes/cpod-1/pause":
            state = "Paused"
            return _json_response(
                200,
                {
                    "sandboxID": "cpod-1",
                    "templateID": "cubeplex-ubuntu24-desktop",
                    "state": "Paused",
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    provider = CubePodProvider(client=_client_for(handler), poll_interval=0.0)

    # connect() uses GET and rejects Paused without calling POST /connect.
    with pytest.raises(SandboxError, match="Paused"):
        await provider.connect("cpod-1")
    with pytest.raises(SandboxError, match="Paused"):
        await provider.connect_observer("cpod-1")
    assert ("POST", "/sandboxes/cpod-1/connect") not in calls

    # 404 connect -> SandboxInstanceGoneError; 404 connect_observer -> SandboxError (not gone).
    with pytest.raises(SandboxInstanceGoneError):
        await provider.connect("cpod-missing")
    with pytest.raises(SandboxError) as observer_exc:
        await provider.connect_observer("cpod-missing")
    assert not isinstance(observer_exc.value, SandboxInstanceGoneError)

    # resume() calls POST /connect and polls GET until Running.
    resumed = await provider.resume("cpod-1")
    assert resumed.id == "cpod-1"
    assert ("POST", "/sandboxes/cpod-1/connect") in calls

    # connect() & connect_observer() succeed once Running without calling POST /connect.
    calls.clear()
    attached = await provider.connect("cpod-1")
    observed = await provider.connect_observer("cpod-1")
    assert attached.id == "cpod-1"
    assert observed.id == "cpod-1"
    assert calls == [("GET", "/sandboxes/cpod-1"), ("GET", "/sandboxes/cpod-1")]

    await provider.pause("cpod-1")
    assert ("POST", "/sandboxes/cpod-1/pause") in calls


async def test_cubepod_inspect_state_terminate_renew_and_reset_rootfs() -> None:
    states_by_id = {
        "cpod-run": ("Running", ProviderInstanceState.RUNNING),
        "cpod-pause": ("Paused", ProviderInstanceState.PAUSED),
        "cpod-create": ("Creating", ProviderInstanceState.TRANSITIONAL),
        "cpod-pausing": ("Pausing", ProviderInstanceState.TRANSITIONAL),
        "cpod-resuming": ("Resuming", ProviderInstanceState.TRANSITIONAL),
        "cpod-fail": ("Failed", ProviderInstanceState.FAILED),
        "cpod-stop": ("Stopped", ProviderInstanceState.TERMINATED),
        "cpod-kill": ("Killed", ProviderInstanceState.TERMINATED),
        "cpod-succ": ("Succeed", ProviderInstanceState.TERMINATED),
        "cpod-term": ("Terminated", ProviderInstanceState.TERMINATED),
    }
    delete_params: list[dict[str, str]] = []
    renew_payloads: list[dict[str, Any]] = []
    reset_polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal reset_polls
        if request.method == "GET" and request.url.path.startswith("/sandboxes/"):
            sid = request.url.path.split("/")[-1]
            if sid == "cpod-404":
                return _json_response(404, {"error": "not_found"})
            if sid == "cpod-reset":
                reset_polls += 1
                st = "Creating" if reset_polls < 2 else "Running"
                return _json_response(
                    200,
                    {
                        "sandboxID": sid,
                        "templateID": "cubeplex-ubuntu24-desktop",
                        "state": st,
                    },
                )
            raw_state = states_by_id[sid][0]
            return _json_response(
                200,
                {
                    "sandboxID": sid,
                    "templateID": "cubeplex-ubuntu24-desktop",
                    "state": raw_state,
                },
            )
        if request.method == "DELETE" and request.url.path.startswith("/sandboxes/"):
            sid = request.url.path.split("/")[-1]
            delete_params.append(dict(request.url.params))
            if sid == "cpod-404":
                return _json_response(404, {"error": "not_found"})
            return _json_response(200, {"deleted": True})
        if request.method == "POST" and request.url.path == "/sandboxes/cpod-run/timeout":
            renew_payloads.append(json.loads(request.content.decode("utf-8")))
            return _json_response(200, {"status": "ok"})
        if request.method == "POST" and request.url.path == "/sandboxes/cpod-reset/reset-rootfs":
            return _json_response(
                200,
                {
                    "sandboxID": "cpod-reset",
                    "templateID": "cubeplex-ubuntu24-desktop",
                    "state": "Creating",
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    provider = CubePodProvider(client=_client_for(handler), poll_interval=0.0)
    for sid, (_raw, expected) in states_by_id.items():
        assert await provider.inspect_state(sid) == expected
    assert await provider.inspect_state("cpod-404") is None

    await provider.terminate("cpod-run")
    await provider.terminate("cpod-404")  # 404 is idempotent
    assert delete_params[0] == {}

    await provider.renew_ttl("cpod-run", 900)
    assert renew_payloads == [{"timeoutSeconds": 900}]

    await provider.reset_rootfs("cpod-reset")
    assert reset_polls == 2


async def test_cubepod_commands_uid_cursor_and_gone_detection() -> None:
    exec_payloads: list[dict[str, Any]] = []
    sandbox_state = "Running"
    cmd_exists = True

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sandbox_state, cmd_exists
        if request.method == "POST" and request.url.path == "/sandboxes/cpod-1/commands/run":
            body = json.loads(request.content.decode("utf-8"))
            exec_payloads.append(body)
            if body.get("command") == "sleep 99":
                raise httpx.ReadTimeout("timed out", request=request)
            if body.get("background"):
                return _json_response(200, {"id": "cmd-7", "pid": 42, "running": True})
            return _json_response(
                200,
                {"id": "cmd-1", "exitCode": 0, "output": "ok\n", "running": False},
            )
        if (
            request.method == "GET"
            and request.url.path == "/sandboxes/cpod-1/commands/cmd-7/status"
        ):
            if not cmd_exists:
                return _json_response(404, {"error": "not_found"})
            return _json_response(
                200,
                {"id": "cmd-7", "running": True, "exitCode": None, "cursor": 5},
            )
        if request.method == "GET" and request.url.path == "/sandboxes/cpod-1/commands/cmd-7/logs":
            if not cmd_exists:
                return _json_response(404, {"error": "not_found"})
            cursor = int(request.url.params.get("cursor", "0"))
            if cursor == 0:
                return _json_response(
                    200,
                    {"id": "cmd-7", "content": "hello", "cursor": 5, "running": True},
                )
            return _json_response(
                200,
                {"id": "cmd-7", "content": " world", "cursor": 11, "running": False, "exitCode": 0},
            )
        if request.method == "POST" and request.url.path == "/sandboxes/cpod-1/commands/cmd-7/kill":
            return _json_response(200, {"killed": True})
        if request.method == "GET" and request.url.path == "/sandboxes/cpod-1":
            return _json_response(
                200,
                {
                    "sandboxID": "cpod-1",
                    "templateID": "cubeplex-ubuntu24-desktop",
                    "state": sandbox_state,
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _client_for(handler)
    sandbox = CubePodSandbox(client=client, sandbox_id="cpod-1", workdir="/workspace")
    sandbox.set_run_env({"FROM_VAULT": "cpodref_abc"})

    chunks: list[str] = []
    res = await sandbox.execute("id", envs={"LOCAL": "1"}, on_chunk=chunks.append)
    assert res == ExecuteResult(output="ok\n", exit_code=0)
    assert chunks == ["ok\n"]
    assert exec_payloads[0]["uid"] == 1000
    assert exec_payloads[0]["gid"] == 1000
    assert exec_payloads[0]["envs"] == {"FROM_VAULT": "cpodref_abc", "LOCAL": "1"}

    await sandbox.execute("whoami", as_root=True)
    assert exec_payloads[1]["uid"] == 0
    assert exec_payloads[1]["gid"] == 0

    timed_out = await sandbox.execute("sleep 99", timeout=1)
    assert timed_out == ExecuteResult(output="[timeout]", exit_code=-1)

    started_ids: list[str] = []
    handle = await sandbox.start("tail -f app.log", on_started=started_ids.append)
    assert started_ids == ["cmd-7"]
    assert handle.provider_ref == "cmd-7"

    snap_obs = await sandbox.observe(handle)
    assert snap_obs.status == "running"
    assert snap_obs.log_cursor == "5"

    snap1 = await sandbox.poll(handle)
    assert snap1.status == "running"
    assert snap1.new_output == "hello"
    assert snap1.log_cursor == "5"
    assert handle.log_cursor is None

    # Unacknowledged poll/read_output must retry from cursor=0 until acknowledge_output().
    retry_poll = await sandbox.poll(handle)
    assert retry_poll.new_output == "hello"
    assert retry_poll.log_cursor == "5"
    retry_read = await sandbox.read_output(
        ProcessHandle(command_id="c1", provider_ref="cmd-7", log_cursor=None)
    )
    assert retry_read.new_output == "hello"
    assert retry_read.log_cursor == "5"

    await sandbox.acknowledge_output(handle, snap1.log_cursor)
    assert handle.log_cursor == "5"

    snap2 = await sandbox.poll(handle)
    assert snap2.status == "exited"
    assert snap2.exit_code == 0
    assert snap2.new_output == " world"
    assert snap2.log_cursor == "11"

    # Driver-local cursor advanced on acknowledge_output() even if caller omits log_cursor.
    out_from_local = await sandbox.read_output(
        ProcessHandle(command_id="c1", provider_ref="cmd-7", log_cursor=None)
    )
    assert out_from_local.new_output == " world"
    assert out_from_local.log_cursor == "11"

    # Cross-worker observer passing explicit log_cursor respects caller cursor.
    out = await sandbox.read_output(
        ProcessHandle(command_id="c1", provider_ref="cmd-7", log_cursor="5")
    )
    assert out.new_output == " world"
    assert out.log_cursor == "11"

    await sandbox.kill(handle)

    cmd_exists = False
    with pytest.raises(ProcessHandleNotFoundError):
        await sandbox.observe(handle)
    with pytest.raises(ProcessHandleNotFoundError):
        await sandbox.poll(handle)

    sandbox_state = "Terminated"
    with pytest.raises(SandboxInstanceGoneError):
        await sandbox.poll(handle)


async def test_cubepod_filesystem_404_413_streaming_and_single_rtt_archive_sync() -> None:
    requests: list[tuple[str, str, dict[str, str], bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        requests.append((request.method, request.url.path, params, request.content))
        if request.method == "PUT" and request.url.path == "/sandboxes/cpod-1/files":
            return _json_response(200, {"written": True})
        if request.method == "GET" and request.url.path == "/sandboxes/cpod-1/files":
            path = params.get("path", "")
            if path == "/workspace/missing.txt":
                return _json_response(404, {"error": "not_found"})
            if path == "/workspace/big.txt":
                return httpx.Response(200, content=b"0123456789ABCDEF")
            return httpx.Response(200, content=b"hello bytes")
        if request.method == "GET" and request.url.path == "/sandboxes/cpod-1/files/list":
            path = params.get("path", "")
            if path == "/workspace/nope":
                return _json_response(404, {"error": "not_found"})
            return _json_response(
                200,
                {
                    "entries": [
                        {
                            "name": "note.txt",
                            "path": "/workspace/note.txt",
                            "is_dir": False,
                            "size": 11,
                            "mode": "0644",
                            "modified_at": "2026-11-14T08:00:00Z",
                        }
                    ]
                },
            )
        if request.method == "POST" and request.url.path == "/sandboxes/cpod-1/files/archive-sync":
            return _json_response(200, {"status": "ok"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    sandbox = CubePodSandbox(client=_client_for(handler), sandbox_id="cpod-1", workdir="/workspace")

    # upload sets uid/gid directly without a follow-up chown execute command.
    await sandbox.upload([("/workspace/note.txt", b"hello bytes")])
    assert requests[-1][0] == "PUT"
    assert requests[-1][2]["uid"] == "1000"
    assert requests[-1][2]["gid"] == "1000"
    assert not any("/commands/run" in r[1] for r in requests)

    assert await sandbox.download(["/workspace/note.txt"]) == [
        ("/workspace/note.txt", b"hello bytes")
    ]
    with pytest.raises(FileNotFoundError):
        await sandbox.download(["/workspace/missing.txt"])

    entries = await sandbox.list_dir("/workspace")
    assert len(entries) == 1
    assert entries[0].modified_at == datetime(2026, 11, 14, 8, 0, 0, tzinfo=UTC)
    assert (await sandbox.stat_file("/workspace/note.txt")).size == 11
    with pytest.raises(FileNotFoundError):
        await sandbox.stat_file("/workspace/absent.txt")

    assert await sandbox.read_text("/workspace/note.txt", max_bytes=32) == "hello bytes"
    with pytest.raises(SandboxFileTooLargeError):
        await sandbox.read_text("/workspace/big.txt", max_bytes=8)
    with pytest.raises(FileNotFoundError):
        await sandbox.read_text("/workspace/missing.txt")

    # stream_file raises FileNotFoundError BEFORE yielding any bytes.
    entered_body = False
    with pytest.raises(FileNotFoundError):
        async with sandbox.stream_file("/workspace/missing.txt") as stream:
            entered_body = True
            async for _chunk in stream:
                pass
    assert not entered_body

    async with sandbox.stream_file("/workspace/note.txt") as stream:
        collected = b"".join([chunk async for chunk in stream])
    assert collected == b"hello bytes"

    # Single-RTT skill sync (update + uninstall-only + empty no-op).
    requests.clear()
    await sandbox.sync_archive(None, dest="/workspace/.skills")
    assert requests == []

    tar_payload = base64.b64decode("H4sIAAAAAAAAAwAA")  # dummy bytes forwarded verbatim
    await sandbox.sync_archive(
        tar_payload,
        dest="/workspace/.skills",
        repush_names=["skill_a"],
        remove_paths=["skill_b"],
    )
    assert len(requests) == 1
    assert requests[0][1] == "/sandboxes/cpod-1/files/archive-sync"
    assert requests[0][2]["dest"] == "/workspace/.skills"
    assert requests[0][2]["remove_paths"] == "skill_a,skill_b"
    assert requests[0][3] == tar_payload

    requests.clear()
    await sandbox.sync_archive(
        b"",
        dest="/workspace/.skills",
        remove_paths=["skill_c"],
    )
    assert len(requests) == 1
    assert requests[0][2]["remove_paths"] == "skill_c"
    assert requests[0][3] == b""


def test_cubepod_build_panel_upstream_http_and_ws_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CubePodProvider()

    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: {
            "sandbox.cubepod.base_url": "http://cubepod-gateway:8080/",
            "sandbox.cubepod.api_key": "tok-123",
        }.get(key, default),
    )
    http_up = provider.build_panel_upstream(
        sandbox_id="cpod-abc",
        port=8080,
        path="/vnc/index.html",
        query="autoconnect=1",
    )
    assert http_up.http_url == "http://cubepod-gateway:8080/vnc/index.html?autoconnect=1"
    assert http_up.http_headers == {
        "Host": "8080-cpod-abc.cubepod.local",
        "accept-encoding": "identity",
        "Authorization": "Bearer tok-123",
    }
    assert http_up.ws_url == "ws://8080-cpod-abc.cubepod.local/vnc/index.html?autoconnect=1"
    assert "Host" not in http_up.ws_headers
    assert "host" not in http_up.ws_headers
    assert http_up.ws_headers == {"Authorization": "Bearer tok-123"}
    assert http_up.ws_connect_host == "cubepod-gateway"
    assert http_up.ws_connect_port == 8080
    assert http_up.ws_server_hostname is None

    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: {
            "sandbox.cubepod.base_url": "https://pods.example.com:8443",
            "sandbox.cubepod.api_key": "",
        }.get(key, default),
    )
    tls_up = provider.build_panel_upstream(
        sandbox_id="cpod-abc",
        port=7681,
        path="",
        query="",
    )
    assert tls_up.http_url == "https://pods.example.com:8443/"
    assert tls_up.http_headers == {
        "Host": "7681-cpod-abc.cubepod.local",
        "accept-encoding": "identity",
    }
    assert tls_up.ws_url == "wss://7681-cpod-abc.cubepod.local/"
    assert tls_up.ws_headers == {}
    assert tls_up.ws_connect_host == "pods.example.com"
    assert tls_up.ws_connect_port == 8443
    assert tls_up.ws_server_hostname == "pods.example.com"

    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: {
            "sandbox.cubepod.base_url": "https://ingress.example.com/cubepod/",
            "sandbox.cubepod.api_key": "",
        }.get(key, default),
    )
    prefixed_up = provider.build_panel_upstream(
        sandbox_id="cpod-abc",
        port=8080,
        path="/vnc/index.html",
        query="autoconnect=1",
    )
    assert prefixed_up.http_url == (
        "https://ingress.example.com/cubepod/vnc/index.html?autoconnect=1"
    )
    assert prefixed_up.ws_url == (
        "wss://8080-cpod-abc.cubepod.local/cubepod/vnc/index.html?autoconnect=1"
    )
    prefixed_root = provider.build_panel_upstream(
        sandbox_id="cpod-abc",
        port=7681,
        path="",
        query="",
    )
    assert prefixed_root.http_url == "https://ingress.example.com/cubepod/"
    assert prefixed_root.ws_url == "wss://7681-cpod-abc.cubepod.local/cubepod/"


async def test_cubepod_conflict_error_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/sandboxes/cpod-1/pause":
            return _json_response(409, {"error": "conflict"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    provider = CubePodProvider(client=_client_for(handler))
    with pytest.raises(SandboxConflictError):
        await provider.pause("cpod-1")


async def test_cubepod_execute_timeout_and_on_chunk_contract() -> None:
    mode = "ok"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/sandboxes/cpod-1/commands/run":
            if mode == "read_timeout":
                raise httpx.ReadTimeout("read timed out", request=request)
            if mode == "connect_timeout":
                raise httpx.ConnectTimeout("connect timed out", request=request)
            if mode == "pool_timeout":
                raise httpx.PoolTimeout("pool exhausted", request=request)
            return _json_response(
                200,
                {"id": "cmd-1", "exitCode": 0, "output": "ok\n", "running": False},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    sandbox = CubePodSandbox(
        client=_client_for(handler),
        sandbox_id="cpod-1",
        workdir="/workspace",
    )

    # Read timeout with timeout=None must raise SandboxError instead of ExecuteResult("[timeout]").
    mode = "read_timeout"
    with pytest.raises(SandboxError, match="read timed out"):
        await sandbox.execute("long-task", timeout=None)

    # Connect/Pool timeouts must always raise SandboxError even when timeout is set.
    mode = "connect_timeout"
    with pytest.raises(SandboxError, match="connect timed out"):
        await sandbox.execute("short-task", timeout=5)

    mode = "pool_timeout"
    with pytest.raises(SandboxError, match="pool exhausted"):
        await sandbox.execute("short-task", timeout=5)

    # Read timeout with bounded timeout maps to [timeout] / -1.
    mode = "read_timeout"
    timed_out = await sandbox.execute("sleep 99", timeout=5)
    assert timed_out == ExecuteResult(output="[timeout]", exit_code=-1)

    # Callback errors inside on_chunk must be caught and never fail execute().
    mode = "ok"

    def _boom(_chunk: str) -> None:
        raise RuntimeError("callback failure")

    ok = await sandbox.execute("echo ok", on_chunk=_boom)
    assert ok == ExecuteResult(output="ok\n", exit_code=0)


def test_cubepod_provider_loop_bound_client_pool_and_request_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _KeepAliveHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            sid = self.path.rsplit("/", 1)[-1]
            body = json.dumps(
                {
                    "sandboxID": sid,
                    "templateID": "cubeplex-ubuntu24-desktop",
                    "state": "Running",
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), _KeepAliveHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        orig_get = config.get
        api_key = "k1"

        def _fake_get(key: str, default: Any = None, *args: Any, **kwargs: Any) -> Any:
            overrides: dict[str, Any] = {
                "sandbox.cubepod.base_url": f"http://127.0.0.1:{port}",
                "sandbox.cubepod.api_key": api_key,
                "sandbox.request_timeout": 1860,
            }
            if key in overrides:
                return overrides[key]
            return orig_get(key, default, *args, **kwargs)

        monkeypatch.setattr(config, "get", _fake_get)
        provider = CubePodProvider()

        async def _probe_loop(count: int) -> tuple[list[ProviderInstanceState], list[AsyncCubePod]]:
            states = await asyncio.gather(
                *(provider.inspect_state(f"cpod-{i}") for i in range(count))
            )
            clients = await asyncio.gather(*(provider._get_client() for _ in range(count)))
            return list(states), list(clients)

        states1, clients1 = asyncio.run(_probe_loop(4))
        assert states1 == [ProviderInstanceState.RUNNING] * 4
        assert len({id(c) for c in clients1}) == 1
        assert clients1[0]._client.timeout == httpx.Timeout(1860.0)

        async def _probe_second_loop() -> tuple[list[ProviderInstanceState], AsyncCubePod]:
            states = await asyncio.gather(
                *(provider.inspect_state(f"cpod-b-{i}") for i in range(3))
            )
            c_before = await provider._get_client()
            nonlocal api_key
            api_key = "k2"
            c_after = await provider._get_client()
            assert c_after is not c_before
            await provider.aclose()
            return list(states), c_before

        states2, client_loop2 = asyncio.run(_probe_second_loop())
        assert states2 == [ProviderInstanceState.RUNNING] * 3
        assert client_loop2 is not clients1[0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
