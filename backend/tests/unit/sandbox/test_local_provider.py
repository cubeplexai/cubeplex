"""Unit tests for Unit 2: LocalSandbox filesystem methods and LocalSandboxProvider."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from cubeplex.sandbox.base import (
    SandboxCapabilityUnsupportedError,
    SandboxFileTooLargeError,
    SandboxInstanceGoneError,
)
from cubeplex.sandbox.provider import ProviderInstanceState, RunIdentity, SandboxCreateSpec
from cubeplex.sandbox.providers.local import LocalSandbox, LocalSandboxProvider
from cubeplex.sandbox_policy.rules import SandboxNetworkPolicy


def _spec(workdir: str, volume_key: str | None = None) -> SandboxCreateSpec:
    return SandboxCreateSpec(
        volume_key=volume_key,
        volume_mount_path="/workspace",
        storage=None,
        image="local",
        workdir=workdir,
        ttl_seconds=600,
        create_timeout_seconds=10,
        ready_timeout_seconds=10,
        request_timeout_seconds=60,
        run_identity=RunIdentity(user=None, uid=None, gid=None),
        env={},
        resource_cpu="1",
        resource_memory="1Gi",
        network_policy=SandboxNetworkPolicy(default_action="allow"),
        user_sandbox_id="usbx_local",
    )


def test_local_provider_capabilities_and_defaults() -> None:
    provider = LocalSandboxProvider()
    assert provider.name == "local"
    assert provider.default_image == "local"
    assert asdict(provider.capabilities) == {
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


@pytest.mark.asyncio
async def test_local_sandbox_filesystem_methods(tmp_path: Path) -> None:
    (tmp_path / "dir").mkdir()
    target = tmp_path / "note.txt"
    target.write_text("hello world", encoding="utf-8")

    sb = LocalSandbox(workdir=str(tmp_path))
    assert sb.provider_name == "local"

    entries = await sb.list_dir(str(tmp_path))
    assert [(e.name, e.is_dir) for e in entries] == [("dir", True), ("note.txt", False)]

    st = await sb.stat_file("note.txt")
    assert st.size == 11
    assert st.modified_at.tzinfo is not None

    assert await sb.read_text(str(target), max_bytes=32) == "hello world"
    with pytest.raises(SandboxFileTooLargeError):
        await sb.read_text(str(target), max_bytes=4)

    chunks: list[bytes] = []
    async with sb.stream_file(str(target)) as stream:
        async for chunk in stream:
            chunks.append(chunk)
    assert b"".join(chunks) == b"hello world"

    with pytest.raises(FileNotFoundError):
        await sb.list_dir(str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError):
        await sb.stat_file(str(tmp_path / "missing.txt"))
    with pytest.raises(FileNotFoundError):
        await sb.read_text(str(tmp_path / "missing.txt"))
    with pytest.raises(FileNotFoundError):
        async with sb.stream_file(str(tmp_path / "missing.txt")):
            pass


@pytest.mark.asyncio
async def test_local_provider_lifecycle_and_unsupported_ops(tmp_path: Path) -> None:
    provider = LocalSandboxProvider(base_workdir=str(tmp_path))
    created_ids: list[str] = []

    async def _on_created(sid: str) -> None:
        created_ids.append(sid)

    sb = await provider.create(_spec(str(tmp_path), volume_key="vol-1"), on_created=_on_created)
    assert created_ids == [sb.id]
    assert sb.workdir == str(tmp_path / "vol-1")
    assert Path(sb.workdir).is_dir()
    assert await provider.inspect_state(sb.id) == ProviderInstanceState.RUNNING

    reconnected = await provider.connect(sb.id)
    assert reconnected.id == sb.id
    assert reconnected.workdir == sb.workdir

    for coro in (
        provider.pause(sb.id),
        provider.resume(sb.id),
        provider.reset_rootfs(sb.id),
    ):
        with pytest.raises(SandboxCapabilityUnsupportedError):
            await coro

    with pytest.raises(SandboxCapabilityUnsupportedError):
        provider.build_panel_upstream(sandbox_id=sb.id, port=8080, path="", query="")

    await provider.terminate(sb.id)
    await provider.terminate(sb.id)  # idempotent
    assert await provider.inspect_state(sb.id) is None

    with pytest.raises(SandboxInstanceGoneError):
        await provider.connect(sb.id)


@pytest.mark.asyncio
async def test_local_provider_connect_observer_raises_unsupported(
    tmp_path: Path,
) -> None:
    provider = LocalSandboxProvider(base_workdir=str(tmp_path))
    with pytest.raises(SandboxCapabilityUnsupportedError) as exc_info:
        await provider.connect_observer("sbx-local-1")
    assert exc_info.value.capability == "reconnect"
    assert exc_info.value.provider == "local"


@pytest.mark.asyncio
async def test_local_provider_resolve_workdir_does_not_double_nest(
    tmp_path: Path,
) -> None:
    provider = LocalSandboxProvider(base_workdir=str(tmp_path))
    resolved = provider.resolve_workdir(volume_key="vol-1")
    assert resolved == str(tmp_path / "vol-1")
    sb = await provider.create(_spec(resolved, volume_key="vol-1"))
    assert sb.workdir == resolved
    assert provider.resolve_workdir(volume_key=None) == str(tmp_path)
