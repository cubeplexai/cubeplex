"""Unit tests for Unit 2: OpenSandboxProvider, data-plane extensions, and ProviderRegistry."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opensandbox.exceptions import SandboxApiException
from opensandbox.models.filesystem import EntryInfo

from cubeplex.sandbox.base import (
    SandboxCapabilities,
    SandboxCapabilityUnsupportedError,
    SandboxError,
    SandboxFileTooLargeError,
    SandboxInstanceGoneError,
)
from cubeplex.sandbox.provider import (
    ProviderInstanceState,
    RunIdentity,
    SandboxCreateSpec,
    SandboxProvider,
    resolve_default_image,
)
from cubeplex.sandbox.providers.opensandbox import OpenSandbox, OpenSandboxProvider
from cubeplex.sandbox.registry import (
    ProviderRegistry,
    UnknownSandboxProviderError,
    get_provider_registry,
    reset_provider_registry,
)
from cubeplex.sandbox_policy.rules import SandboxNetworkPolicy, SandboxNetworkRule


def _create_spec(**overrides: object) -> SandboxCreateSpec:
    defaults: dict[str, object] = {
        "volume_key": "cubeplex-user-ws-u1",
        "volume_mount_path": "/workspace",
        "storage": "10Gi",
        "image": "cubeplex/sandbox:v1",
        "workdir": "/workspace",
        "ttl_seconds": 1800,
        "create_timeout_seconds": 300,
        "ready_timeout_seconds": 120,
        "request_timeout_seconds": 1860,
        "run_identity": RunIdentity(user="cubeplex", uid=1000, gid=1000),
        "env": {"FOO": "bar"},
        "resource_cpu": "2",
        "resource_memory": "4Gi",
        "network_policy": SandboxNetworkPolicy(
            default_action="deny",
            egress=(SandboxNetworkRule(action="allow", target="api.github.com"),),
        ),
        "metadata": {"cubeplex_user": "u1"},
        "user_sandbox_id": "usbx_1",
    }
    defaults.update(overrides)
    return SandboxCreateSpec(**defaults)  # type: ignore[arg-type]


def test_opensandbox_provider_capabilities_and_dynamic_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.config import config

    provider = OpenSandboxProvider()
    assert provider.name == "opensandbox"
    caps = provider.capabilities
    assert asdict(caps) == {
        "background": True,
        "reconnect": True,
        "persistent_volume": True,
        "pause": True,
        "reset_rootfs": False,
        "terminal": True,
        "browser": True,
        "egress_proxy": True,
        "network_policy": True,
    }

    monkeypatch.setattr(config, "get", lambda k, d=None: "img:one" if k == "sandbox.image" else d)
    assert provider.default_image == "img:one"
    monkeypatch.setattr(config, "get", lambda k, d=None: "img:two" if k == "sandbox.image" else d)
    assert provider.default_image == "img:two"


@pytest.mark.asyncio
async def test_opensandbox_close_and_filesystem_primitives() -> None:
    raw = MagicMock()
    raw.id = "osbx-1"
    raw.close = AsyncMock()
    now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    raw.files.list_directory = AsyncMock(
        return_value=[
            EntryInfo(
                path="/workspace/src",
                type="directory",
                mode=755,
                owner="cubeplex",
                group="cubeplex",
                size=0,
                modified_at=now,
                created_at=now,
            ),
            EntryInfo(
                path="/workspace/hello.txt",
                type="file",
                mode=644,
                owner="cubeplex",
                group="cubeplex",
                size=5,
                modified_at=now,
                created_at=now,
            ),
        ]
    )
    raw.files.get_file_info = AsyncMock(
        return_value={
            "/workspace/hello.txt": EntryInfo(
                path="/workspace/hello.txt",
                type="file",
                mode=644,
                owner="cubeplex",
                group="cubeplex",
                size=5,
                modified_at=now,
                created_at=now,
            )
        }
    )
    raw.files.read_file = AsyncMock(return_value="hello")

    stream_closed = False

    async def _chunk_gen():
        nonlocal stream_closed
        try:
            yield b"hel"
            yield b"lo"
        finally:
            stream_closed = True

    raw.files.read_bytes_stream = AsyncMock(side_effect=lambda _path: _chunk_gen())

    sb = OpenSandbox(sandbox=raw)
    entries = await sb.list_dir("/workspace")
    assert [e.name for e in entries] == ["src", "hello.txt"]
    assert entries[0].is_dir is True
    assert entries[1].size == 5

    st = await sb.stat_file("/workspace/hello.txt")
    assert st.path == "/workspace/hello.txt"

    assert await sb.read_text("/workspace/hello.txt", max_bytes=10) == "hello"

    with pytest.raises(SandboxFileTooLargeError):
        await sb.read_text("/workspace/hello.txt", max_bytes=2)

    chunks: list[bytes] = []
    async with sb.stream_file("/workspace/hello.txt") as stream:
        async for chunk in stream:
            chunks.append(chunk)
    assert chunks == [b"hel", b"lo"]
    assert stream_closed is True

    await sb.close()
    raw.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_opensandbox_filesystem_404_raises_file_not_found() -> None:
    raw = MagicMock()
    raw.id = "osbx-404"
    err_404 = SandboxApiException("missing", status_code=404)
    raw.files.list_directory = AsyncMock(side_effect=err_404)
    raw.files.get_file_info = AsyncMock(return_value={})
    raw.files.read_file = AsyncMock(side_effect=err_404)
    raw.files.read_bytes_stream = AsyncMock(side_effect=err_404)

    sb = OpenSandbox(sandbox=raw)
    with pytest.raises(FileNotFoundError):
        await sb.list_dir("/workspace/missing")
    with pytest.raises(FileNotFoundError):
        await sb.stat_file("/workspace/missing")
    with pytest.raises(FileNotFoundError):
        await sb.read_text("/workspace/missing")
    with pytest.raises(FileNotFoundError):
        async with sb.stream_file("/workspace/missing"):
            pass


@pytest.mark.asyncio
async def test_opensandbox_provider_create_orders_on_created_closes_raw_and_rebinds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.config import config

    monkeypatch.setattr(
        config,
        "get",
        lambda k, d=None: {
            "sandbox.domain": "os.internal:8090",
            "sandbox.api_key": "k-1",
            "sandbox.use_server_proxy": True,
            "sandbox.secure_access": False,
        }.get(k, d),
    )

    events: list[str] = []
    captured: dict[str, object] = {}

    raw_created = MagicMock()
    raw_created.id = "osbx-new"

    async def _check_ready(ready_timeout, polling_interval):  # noqa: ANN001
        del ready_timeout, polling_interval
        events.append("check_ready")

    async def _close_raw():
        events.append("close_raw")

    raw_created.check_ready = _check_ready
    raw_created.close = _close_raw

    raw_rebound = MagicMock()
    raw_rebound.id = "osbx-new"

    async def _fake_create(image, **kwargs):  # noqa: ANN001
        events.append("create")
        captured["image"] = image
        captured["create_kwargs"] = kwargs
        return raw_created

    async def _fake_connect(sandbox_id, **kwargs):  # noqa: ANN001
        events.append("connect")
        captured["connect_id"] = sandbox_id
        captured["connect_kwargs"] = kwargs
        return raw_rebound

    async def _on_created(sid: str) -> None:
        events.append(f"on_created:{sid}")

    with (
        patch("opensandbox.Sandbox.create", side_effect=_fake_create),
        patch("opensandbox.Sandbox.connect", side_effect=_fake_connect),
    ):
        provider = OpenSandboxProvider()
        sb = await provider.create(_create_spec(), on_created=_on_created)

    assert events == ["create", "on_created:osbx-new", "check_ready", "close_raw", "connect"]
    assert isinstance(sb, OpenSandbox)
    assert sb.id == "osbx-new"
    assert sb.user_sandbox_id == "usbx_1"

    ckwargs = captured["create_kwargs"]
    assert ckwargs["skip_health_check"] is True  # type: ignore[index]
    assert ckwargs["volumes"][0].pvc.claim_name == "cubeplex-user-ws-u1"  # type: ignore[index]
    net_pol = ckwargs["network_policy"]  # type: ignore[index]
    assert net_pol.default_action == "deny"
    assert [(r.action, r.target) for r in net_pol.egress] == [("allow", "api.github.com")]
    c_conn = ckwargs["connection_config"]  # type: ignore[index]
    assert c_conn.request_timeout.total_seconds() == 300

    rebind_kwargs = captured["connect_kwargs"]
    assert rebind_kwargs["skip_health_check"] is True  # type: ignore[index]
    r_conn = rebind_kwargs["connection_config"]  # type: ignore[index]
    assert r_conn.request_timeout.total_seconds() == 1860


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_state", "expected"),
    [
        ("Paused", ProviderInstanceState.PAUSED),
        ("Running", ProviderInstanceState.RUNNING),
        ("Failed", ProviderInstanceState.FAILED),
        ("Terminated", ProviderInstanceState.TERMINATED),
        ("Succeed", ProviderInstanceState.TERMINATED),
        ("Pending", ProviderInstanceState.TRANSITIONAL),
    ],
)
async def test_opensandbox_provider_inspect_state_maps_and_closes_client(
    raw_state: str, expected: ProviderInstanceState
) -> None:
    mgr_client = MagicMock()
    mgr_client.get_sandbox_info = AsyncMock(
        return_value=SimpleNamespace(status=SimpleNamespace(state=raw_state))
    )
    mgr_client.close = AsyncMock()

    with patch("opensandbox.SandboxManager.create", new=AsyncMock(return_value=mgr_client)):
        provider = OpenSandboxProvider()
        state = await provider.inspect_state("osbx-1")

    assert state == expected
    mgr_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_opensandbox_provider_inspect_404_and_terminate_404_and_panel_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.config import config

    monkeypatch.setattr(
        config,
        "get",
        lambda k, d=None: "os-server.local:8090" if k == "sandbox.domain" else d,
    )

    mgr_client = MagicMock()
    err_404 = SandboxApiException("not found", status_code=404)
    mgr_client.get_sandbox_info = AsyncMock(side_effect=err_404)
    mgr_client.kill_sandbox = AsyncMock(side_effect=err_404)
    mgr_client.close = AsyncMock()

    with patch("opensandbox.SandboxManager.create", new=AsyncMock(return_value=mgr_client)):
        provider = OpenSandboxProvider()
        assert await provider.inspect_state("osbx-missing") is None
        await provider.terminate("osbx-missing")
        with pytest.raises(SandboxCapabilityUnsupportedError) as exc_info:
            await provider.reset_rootfs("osbx-1")
        assert exc_info.value.capability == "reset_rootfs"

    assert mgr_client.close.await_count == 2

    up = provider.build_panel_upstream(sandbox_id="sbx-9", port=7681, path="ws", query="a=1")
    assert up.http_url == "http://os-server.local:8090/sandboxes/sbx-9/proxy/7681/ws?a=1"
    assert up.ws_url == "ws://os-server.local:8090/sandboxes/sbx-9/proxy/7681/ws?a=1"
    assert up.http_headers == {"accept-encoding": "identity"}
    assert up.ws_headers == {}


@pytest.mark.asyncio
async def test_provider_registry_resolution_and_aclose(monkeypatch: pytest.MonkeyPatch) -> None:
    from cubeplex.config import config

    reset_provider_registry()
    try:
        reg = get_provider_registry()
        assert reg.get(None).name == "opensandbox"
        assert reg.get("local").name == "local"

        monkeypatch.setattr(
            config, "get", lambda k, d=None: "local" if k == "sandbox.provider" else d
        )
        assert reg.default().name == "local"
        assert reg.get(None).name == "local"

        with pytest.raises(UnknownSandboxProviderError, match="missing-provider"):
            reg.get("missing-provider")

        fake_provider = MagicMock(spec=SandboxProvider)
        fake_provider.name = "custom"
        fake_provider.aclose = AsyncMock()
        custom_reg = ProviderRegistry({"custom": fake_provider}, default_name="custom")
        await custom_reg.aclose()
        fake_provider.aclose.assert_awaited_once()
    finally:
        reset_provider_registry()


@pytest.mark.asyncio
async def test_opensandbox_provider_connect_404_translates_to_instance_gone() -> None:
    with patch(
        "opensandbox.Sandbox.connect",
        side_effect=SandboxApiException("gone", status_code=404),
    ):
        provider = OpenSandboxProvider()
        with pytest.raises(SandboxInstanceGoneError):
            await provider.connect("sbx-gone")


@pytest.mark.asyncio
async def test_opensandbox_observer_404_and_inspect_503_are_not_instance_gone() -> None:
    provider = OpenSandboxProvider()
    with patch(
        "opensandbox.Sandbox.connect",
        side_effect=SandboxApiException("not found", status_code=404),
    ):
        with pytest.raises(SandboxError) as exc_info:
            await provider.connect_observer("sbx-edge-404")
        assert not isinstance(exc_info.value, SandboxInstanceGoneError)

    mgr_client = MagicMock()
    mgr_client.get_sandbox_info = AsyncMock(
        side_effect=SandboxApiException("not found", status_code=503)
    )
    mgr_client.close = AsyncMock()
    with patch("opensandbox.SandboxManager.create", new=AsyncMock(return_value=mgr_client)):
        with pytest.raises(SandboxError) as exc_info:
            await provider.inspect_state("sbx-503")
        assert not isinstance(exc_info.value, SandboxInstanceGoneError)
    mgr_client.close.assert_awaited_once()

    raw = MagicMock()
    raw.id = "osbx-empty-info"
    raw.files.get_file_info = AsyncMock(return_value={})
    raw.files.read_file = AsyncMock(return_value="body")
    sb = OpenSandbox(sandbox=raw)
    assert await sb.read_text("/workspace/log.txt", max_bytes=1024) == "body"
    raw.files.read_file.assert_awaited_once_with("/workspace/log.txt")


@pytest.mark.asyncio
async def test_base_provider_connect_observer_requires_reconnect_capability() -> None:
    class _NoReconnectProvider(SandboxProvider):
        @property
        def name(self) -> str:
            return "noreconnect"

        @property
        def capabilities(self) -> SandboxCapabilities:
            return SandboxCapabilities(reconnect=False)

        @property
        def default_workdir(self) -> str:
            return "/work"

        @property
        def default_image(self) -> str | None:
            return None

        async def create(  # type: ignore[override]
            self, spec: SandboxCreateSpec, *, on_created=None
        ):
            raise NotImplementedError

        async def connect(  # type: ignore[override]
            self, sandbox_id: str, **_kwargs: object
        ):
            raise AssertionError("connect must not be called when reconnect=False")

        async def inspect_state(self, sandbox_id: str) -> ProviderInstanceState | None:
            return None

        async def terminate(self, sandbox_id: str) -> None:
            return None

    provider = _NoReconnectProvider()
    with pytest.raises(SandboxCapabilityUnsupportedError) as exc_info:
        await provider.connect_observer("sbx-1")
    assert exc_info.value.capability == "reconnect"
    assert exc_info.value.provider == "noreconnect"


def test_sandbox_package_does_not_reexport_opensandbox_and_default_image_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cubeplex.sandbox as sandbox_pkg
    from cubeplex.config import config

    assert "OpenSandbox" not in sandbox_pkg.__all__
    assert not hasattr(sandbox_pkg, "OpenSandbox")

    monkeypatch.setattr(config, "get", lambda k, d=None: "cfg-img:1" if k == "sandbox.image" else d)
    assert resolve_default_image(None) == "cfg-img:1"
    monkeypatch.setattr(config, "get", lambda k, d=None: "" if k == "sandbox.image" else d)
    assert resolve_default_image(None) == "ubuntu:22.04"
    monkeypatch.setattr(config, "get", lambda k, d=None: "img:x" if k == "sandbox.image" else d)
    assert resolve_default_image(OpenSandboxProvider()) == "img:x"


@pytest.mark.asyncio
async def test_internal_egress_policy_resolution_uses_provider_default_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.api.routes import internal_egress
    from cubeplex.config import config

    monkeypatch.setattr(
        config,
        "get",
        lambda k, d=None: "local" if k == "sandbox.provider" else d,
    )
    reset_provider_registry()
    seen: list[str] = []

    class _Resolver:
        def __init__(self, repo: object, *, default_image: str) -> None:
            del repo
            seen.append(default_image)

        async def resolve(self) -> SimpleNamespace:
            return SimpleNamespace(egress_proxy="http://proxy.internal:3128")

    monkeypatch.setattr(internal_egress, "SandboxPolicyRepository", lambda *_a, **_k: object())
    monkeypatch.setattr(internal_egress, "SandboxPolicyResolver", _Resolver)
    try:
        proxy = await internal_egress._resolve_egress_proxy_for_org(
            object(),  # type: ignore[arg-type]
            "org-1",
        )
        assert proxy == "http://proxy.internal:3128"
        assert seen == ["local"]
    finally:
        reset_provider_registry()
