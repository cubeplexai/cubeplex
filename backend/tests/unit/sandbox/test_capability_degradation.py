"""Unit tests verifying graceful capability degradation and panel proxy routing."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cubeplex.api.routes import sandbox_panel
from cubeplex.api.routes.v1 import admin_sandbox_policy
from cubeplex.middleware.sandbox import SandboxMiddleware
from cubeplex.models.background_task import BackgroundTask
from cubeplex.models.sandbox_command import SandboxCommand
from cubeplex.prompts.sandbox import SANDBOX_PROMPT_TEMPLATE, build_sandbox_prompt
from cubeplex.sandbox.base import ExecuteResult, Sandbox, SandboxCapabilities
from cubeplex.sandbox.panel_token import sign_panel_token, verify_panel_token
from cubeplex.sandbox.provider import PanelUpstream, SandboxProvider
from cubeplex.sandbox.registry import ProviderRegistry
from cubeplex.services.background_task_query import TaskProjection
from cubeplex.services.sandbox_runtime_config import (
    FORBIDDEN_KEY_FRAGMENTS,
    load_agent_view,
    resolve_env_inventory,
)


class _StubSandbox(Sandbox):
    def __init__(self, caps: SandboxCapabilities, workdir: str = "/tmp/sbx") -> None:
        self._caps = caps
        self._workdir = workdir
        self.executed: list[str] = []

    @property
    def id(self) -> str:
        return "sbx-stub"

    @property
    def provider_name(self) -> str:
        return "stub"

    @property
    def capabilities(self) -> SandboxCapabilities:
        return self._caps

    @property
    def workdir(self) -> str:
        return self._workdir

    async def execute(self, command: str, *, timeout: int | None = None, envs=None, on_chunk=None):
        del timeout, envs, on_chunk
        self.executed.append(command)
        return ExecuteResult(output="ok\n", exit_code=0)

    async def write_file(self, path: str, content: str) -> None:
        del path, content

    async def read_file(self, path: str) -> str:
        del path
        return ""

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        del files

    async def download(self, path: str) -> bytes:
        del path
        return b""

    async def close(self) -> None:
        return None


def _forbidden_keys(obj: Any) -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if any(frag in kl for frag in FORBIDDEN_KEY_FRAGMENTS):
                hits.append(str(k))
            hits.extend(_forbidden_keys(v))
    elif isinstance(obj, list):
        for item in obj:
            hits.extend(_forbidden_keys(item))
    return hits


@pytest.mark.asyncio
async def test_background_false_mounts_foreground_execute_and_foreground_prompt() -> None:
    caps = SandboxCapabilities(background=False, persistent_volume=True)
    sandbox = _StubSandbox(caps, workdir="/work")
    mw = SandboxMiddleware(sandbox=sandbox, workspace_id="ws-1")

    tool_names = {t.name for t in mw.tools}
    assert tool_names == {"execute", "write", "edit", "read"}
    assert "monitor" not in tool_names
    assert "kill_execute" not in tool_names

    exec_tool = next(t for t in mw.tools if t.name == "execute")
    assert "background" not in exec_tool.parameters.model_fields
    res = await exec_tool.execute(
        "tc-fg", exec_tool.parameters(command="echo hi", description="Hi")
    )
    assert sandbox.executed == ["echo hi"]
    assert res.is_error is not True

    rendered = await mw.transform_system_prompt("base", ctx=object())
    assert "background=true" not in rendered
    assert "kill_execute" not in rendered
    assert "monitor" not in rendered
    assert "notify_on_complete" not in rendered
    assert "result_pending" not in rendered
    assert "/work" in rendered


def test_build_sandbox_prompt_ephemeral_vs_full_stability() -> None:
    full_caps = SandboxCapabilities(background=True, persistent_volume=True)
    assert build_sandbox_prompt("/work", full_caps) == SANDBOX_PROMPT_TEMPLATE.format(
        workdir="/work"
    )

    ephemeral_caps = SandboxCapabilities(background=True, persistent_volume=False)
    p1 = build_sandbox_prompt("/ephemeral", ephemeral_caps)
    p2 = build_sandbox_prompt("/ephemeral", ephemeral_caps)
    assert p1 == p2
    assert "/ephemeral" in p1
    assert "ephemeral" in p1.lower()
    assert "survives sandbox restarts" not in p1
    assert "shared across sessions" not in p1
    assert "remain available across conversations" not in p1


@pytest.mark.asyncio
async def test_egress_proxy_false_reports_skipped_provider_unsupported_without_leaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_rows = [
        SimpleNamespace(
            env_name="OPENAI_API_KEY",
            is_secret=True,
            scope="workspace",
            status="valid",
            hosts=["api.openai.com"],
            header_names=["Authorization"],
        ),
        SimpleNamespace(
            env_name="APP_MODE",
            is_secret=False,
            scope="org",
            status="valid",
            hosts=None,
            header_names=None,
        ),
    ]

    class _FakeEnvRepo:
        def __init__(self, session: object, *, org_id: str) -> None:
            del session, org_id

        async def list_for_resolution(self, *, workspace_id: str, user_id: str) -> list[Any]:
            del workspace_id, user_id
            return fake_rows

    class _FakePolicyRepo:
        def __init__(self, session: object, *, org_id: str) -> None:
            del session, org_id

        async def get(self) -> None:
            return None

    monkeypatch.setattr(
        "cubeplex.services.sandbox_runtime_config.SandboxEnvRepository", _FakeEnvRepo
    )
    monkeypatch.setattr(
        "cubeplex.services.sandbox_runtime_config.SandboxPolicyRepository",
        _FakePolicyRepo,
    )

    inventory = await resolve_env_inventory(
        object(),  # type: ignore[arg-type]
        org_id="org-1",
        workspace_id="ws-1",
        user_id="user-1",
        egress_proxy_supported=False,
    )
    by_name = {item.env_name: item for item in inventory}
    assert by_name["APP_MODE"].status == "valid"
    assert by_name["OPENAI_API_KEY"].status == "skipped_provider_unsupported"

    view = await load_agent_view(
        object(),  # type: ignore[arg-type]
        org_id="org-1",
        workspace_id="ws-1",
        user_id="user-1",
        default_image="local",
        egress_proxy_supported=False,
    )
    assert _forbidden_keys(view) == []
    env_map = {item["env_name"]: item for item in view["env"]}
    assert env_map["OPENAI_API_KEY"]["status"] == "skipped_provider_unsupported"
    assert env_map["APP_MODE"]["status"] == "valid"


@pytest.mark.asyncio
async def test_network_policy_false_emits_admin_policy_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = MagicMock(spec=SandboxProvider)
    provider.name = "local"
    provider.default_image = "local"
    provider.capabilities = SandboxCapabilities(network_policy=False)
    registry = ProviderRegistry({"local": provider}, default_name="local")

    class _Repo:
        def __init__(self, session: object, *, org_id: str) -> None:
            del session, org_id

        async def get(self) -> None:
            return None

    monkeypatch.setattr(admin_sandbox_policy, "get_provider_registry", lambda: registry)
    monkeypatch.setattr(admin_sandbox_policy, "SandboxPolicyRepository", _Repo)

    ctx = SimpleNamespace(org_id="org-1", user=SimpleNamespace(id="admin-1"))
    out = await admin_sandbox_policy.get_sandbox_policy(
        session=object(),  # type: ignore[arg-type]
        ctx=ctx,  # type: ignore[arg-type]
    )
    assert out.default_image == "local"
    assert out.network_default_action == "deny"
    assert len(out.warnings) == 1
    assert "local" in out.warnings[0]
    assert "does not enforce network egress policies" in out.warnings[0]

    provider.capabilities = SandboxCapabilities(network_policy=True)
    clean_out = await admin_sandbox_policy.get_sandbox_policy(
        session=object(),  # type: ignore[arg-type]
        ctx=ctx,  # type: ignore[arg-type]
    )
    assert clean_out.warnings == []


def test_task_projection_reconnect_and_cancel_use_provider_capabilities() -> None:
    task = BackgroundTask(
        id="task-1",
        org_id="org-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
        execution_generation=1,
        kind="command",
        state="running",
        summary="cmd",
    )
    local_cmd = SandboxCommand(
        id="scmd-1",
        org_id="org-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
        execution_generation=1,
        user_sandbox_id="usbx-1",
        provider="local",
        provider_ref="pid-1",
        sandbox_instance_id="local-1",
        command="sleep 10",
        description="sleep",
        status="running",
        log_path="/tmp/log",
    )
    local_proj = TaskProjection(
        task=task, command=local_cmd, has_pending_event=False, has_claimed_event=False
    )
    assert local_proj.remote_cancel_supported is False
    assert local_proj.reconnect_supported is False

    os_cmd = SandboxCommand(
        id="scmd-2",
        org_id="org-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
        execution_generation=1,
        user_sandbox_id="usbx-1",
        provider="opensandbox",
        provider_ref="pid-2",
        sandbox_instance_id="sbx-1",
        command="sleep 10",
        description="sleep",
        status="running",
        log_path="/workspace/log",
    )
    os_proj = TaskProjection(
        task=task, command=os_cmd, has_pending_event=False, has_claimed_event=False
    )
    assert os_proj.remote_cancel_supported is True
    assert os_proj.reconnect_supported is True

    unknown_cmd = SandboxCommand(
        id="scmd-3",
        org_id="org-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
        execution_generation=1,
        user_sandbox_id="usbx-1",
        provider="retired-provider",
        provider_ref="pid-3",
        sandbox_instance_id="sbx-3",
        command="sleep 10",
        description="sleep",
        status="running",
        log_path="/workspace/log",
    )
    unknown_proj = TaskProjection(
        task=task, command=unknown_cmd, has_pending_event=False, has_claimed_event=False
    )
    assert unknown_proj.remote_cancel_supported is False
    assert unknown_proj.reconnect_supported is False


@pytest.mark.asyncio
async def test_panel_token_and_ws_upstream_routing_without_duplicate_host_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "test-panel-secret"
    legacy_token = sign_panel_token(
        sandbox_id="sbx-default", port=7681, secret=secret, ttl=timedelta(minutes=5)
    )
    assert verify_panel_token(legacy_token, secret=secret).provider is None

    custom_token = sign_panel_token(
        sandbox_id="sbx-cp",
        port=8080,
        provider="custom",
        secret=secret,
        ttl=timedelta(minutes=5),
    )
    assert verify_panel_token(custom_token, secret=secret).provider == "custom"

    seen_calls: list[tuple[str, dict[str, Any]]] = []

    class _UpstreamProvider(SandboxProvider):
        def __init__(self, name: str) -> None:
            self._name = name

        @property
        def name(self) -> str:
            return self._name

        @property
        def capabilities(self) -> SandboxCapabilities:
            return SandboxCapabilities(terminal=True, browser=True)

        @property
        def default_workdir(self) -> str:
            return "/workspace"

        @property
        def default_image(self) -> str:
            return "stub:latest"

        async def create(self, spec, *, on_created=None):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        async def connect(
            self, sandbox_id, *, workdir=None, user_sandbox_id=None, run_identity=None
        ):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        async def inspect_state(self, sandbox_id):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        async def pause(self, sandbox_id):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        async def terminate(self, sandbox_id):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        async def renew_ttl(self, sandbox_id, ttl_seconds):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        def build_panel_upstream(
            self, *, sandbox_id: str, port: int, path: str, query: str
        ) -> PanelUpstream:
            qs = f"?{query}" if query else ""
            if self._name == "custom":
                return PanelUpstream(
                    http_url=f"https://gw.internal/{path}{qs}",
                    http_headers={"Host": f"{port}-{sandbox_id}.custom.local"},
                    ws_url=f"wss://{port}-{sandbox_id}.custom.local/{path}{qs}",
                    ws_headers={"Authorization": "Bearer gw-key"},
                    ws_connect_host="gw.internal",
                    ws_connect_port=9443,
                    ws_server_hostname="gw.internal",
                )
            return PanelUpstream(
                http_url=f"http://default.internal/{sandbox_id}/{port}/{path}{qs}",
                http_headers={"accept-encoding": "identity"},
                ws_url=f"ws://default.internal/{sandbox_id}/{port}/{path}{qs}",
                ws_headers={},
            )

    registry = ProviderRegistry(
        {"default": _UpstreamProvider("default"), "custom": _UpstreamProvider("custom")},
        default_name="default",
    )
    monkeypatch.setattr(sandbox_panel, "get_provider_registry", lambda: registry)
    monkeypatch.setattr(sandbox_panel, "get_panel_secret", lambda: secret)

    fake_ws = AsyncMock()
    fake_ws.scope = {"subprotocols": []}
    fake_ws.url = SimpleNamespace(query="token=1")
    fake_upstream = AsyncMock()
    fake_upstream.subprotocol = None

    async def _fake_connect(url: str, **kwargs: Any) -> AsyncMock:
        seen_calls.append((url, kwargs))
        return fake_upstream

    with (
        patch.object(sandbox_panel.websockets, "connect", side_effect=_fake_connect),
        patch.object(sandbox_panel, "_relay", AsyncMock()),
    ):
        await sandbox_panel.panel_ws(fake_ws, legacy_token, "tty")
        await sandbox_panel.panel_ws(fake_ws, custom_token, "ws")

    assert seen_calls[0][0] == "ws://default.internal/sbx-default/7681/tty?token=1"
    assert "additional_headers" not in seen_calls[0][1]

    custom_url, custom_kwargs = seen_calls[1]
    assert custom_url == "wss://8080-sbx-cp.custom.local/ws?token=1"
    assert custom_kwargs["host"] == "gw.internal"
    assert custom_kwargs["port"] == 9443
    assert custom_kwargs["server_hostname"] == "gw.internal"
    assert custom_kwargs["additional_headers"] == {"Authorization": "Bearer gw-key"}
    assert "Host" not in custom_kwargs["additional_headers"]
