"""Unit test: LazySandbox.download must not recreate the sandbox on failure.

A download failure is overwhelmingly a missing / unreadable path (e.g. the
agent referenced a skill file that isn't there). The previous behaviour nulled
and recreated the sandbox on ANY download error — which wiped /workspace AND
could not produce the file anyway, so a single bad path read destroyed the
agent's in-progress work and triggered recreate storms. The read error must
instead surface to the caller; a genuinely dead sandbox is recovered by the
next execute/upload call.
"""

from __future__ import annotations

import pytest

from cubeplex.sandbox.base import (
    ProcessHandle,
    ProcessSnapshot,
    Sandbox,
    SandboxError,
    SandboxInstanceGoneError,
)
from cubeplex.sandbox.lazy import LazySandbox
from cubeplex.sandbox.manager import SandboxAttachment


class _FakeSandbox:
    def __init__(self, sandbox_id: str) -> None:
        self.id = sandbox_id

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        raise FileNotFoundError(paths[0])

    async def poll(self, _handle: ProcessHandle) -> ProcessSnapshot:
        return ProcessSnapshot(status="running")

    async def observe(self, _handle: ProcessHandle) -> ProcessSnapshot:
        return ProcessSnapshot(status="killed", exit_code=-9)


class _CountingManager:
    """Counts get_or_create so the test can assert no recreate happened."""

    def __init__(self) -> None:
        self.create_count = 0
        self.renew_count = 0

    async def get_or_create(self, **_kwargs: object) -> SandboxAttachment:
        self.create_count += 1
        fake = _FakeSandbox(f"sbx-{self.create_count}")
        return SandboxAttachment(sandbox=fake, user_sandbox_id=f"uss-{self.create_count}")  # type: ignore[arg-type]

    async def touch(self, *_a: object, **_k: object) -> None:
        return None

    async def renew_lease(self, *_a: object, **_k: object) -> None:
        self.renew_count += 1


def _make_lazy(manager: _CountingManager) -> LazySandbox:
    return LazySandbox(
        manager=manager,  # type: ignore[arg-type]
        scope_type="user",
        scope_id="usr-1",
        user_id="usr-1",
        org_id="org-1",
        workspace_id="ws-1",
    )


@pytest.mark.asyncio
async def test_download_failure_does_not_recreate_sandbox() -> None:
    manager = _CountingManager()
    lazy = _make_lazy(manager)

    with pytest.raises(FileNotFoundError):
        await lazy.download(["/.skills/acme__x/1.0/missing.py"])

    # Exactly one create (the initial _ensure); the failed read must NOT have
    # triggered a recreate, and the live sandbox handle must be preserved.
    assert manager.create_count == 1
    assert lazy.initialized is True


@pytest.mark.asyncio
async def test_foreground_operation_uses_one_lease_for_many_polls() -> None:
    manager = _CountingManager()
    lazy = _make_lazy(manager)
    handle = ProcessHandle(command_id="scmd-test", provider_ref="provider-test")

    async def poll_many(backend: Sandbox) -> None:
        for _ in range(5):
            snapshot = await backend.poll(handle)
            assert snapshot.status == "running"

    await lazy.run_with_backend(poll_many)

    assert manager.renew_count == 1
    assert manager.create_count == 1
    assert (await lazy.observe(handle)).status == "killed"


@pytest.mark.asyncio
async def test_uncertain_foreground_start_failure_does_not_replay_operation() -> None:
    manager = _CountingManager()
    lazy = _make_lazy(manager)
    attempts: list[str] = []

    async def start_on_backend(backend: Sandbox) -> str:
        attempts.append(backend.id)
        raise SandboxError("transport failed before start receipt")

    with pytest.raises(SandboxError, match="transport failed"):
        await lazy.run_with_backend(start_on_backend)

    assert attempts == ["sbx-1"]
    assert manager.create_count == 1


@pytest.mark.asyncio
async def test_confirmed_instance_loss_recreates_only_for_next_operation() -> None:
    manager = _CountingManager()
    lazy = _make_lazy(manager)
    attempts: list[str] = []

    async def operation(backend: Sandbox) -> str:
        attempts.append(backend.id)
        if backend.id == "sbx-1":
            raise SandboxInstanceGoneError(backend.id)
        return backend.id

    with pytest.raises(SandboxInstanceGoneError):
        await lazy.run_with_backend(operation)

    assert attempts == ["sbx-1"]
    assert await lazy.run_with_backend(operation) == "sbx-2"
    assert attempts == ["sbx-1", "sbx-2"]
    assert manager.create_count == 2


@pytest.mark.asyncio
async def test_foreground_failure_after_start_does_not_replay_operation() -> None:
    manager = _CountingManager()
    lazy = _make_lazy(manager)
    attempts: list[str] = []

    async def poll_on_backend(backend: Sandbox) -> None:
        attempts.append(backend.id)
        raise RuntimeError("poll failed after command started")

    with pytest.raises(RuntimeError, match="poll failed"):
        await lazy.run_with_backend(poll_on_backend)

    assert attempts == ["sbx-1"]
    assert manager.create_count == 1
