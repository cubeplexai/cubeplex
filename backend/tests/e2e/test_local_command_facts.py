"""A local process result remains a fact after cancellation or another observation."""

import asyncio

import pytest

from cubeplex.sandbox.base import ProcessHandle, ProcessSnapshot, SandboxError
from cubeplex.sandbox.local import LocalSandbox


async def finished(sandbox: LocalSandbox, handle: ProcessHandle) -> ProcessSnapshot:
    async with asyncio.timeout(5):
        while True:
            snapshot = await sandbox.poll(handle)
            if snapshot.status != "running":
                return snapshot
            await asyncio.sleep(0.01)


async def test_natural_exit_survives_repeated_poll_and_stop() -> None:
    sandbox = LocalSandbox()
    handle = await sandbox.start("printf result; exit 7")
    try:
        first = await finished(sandbox, handle)
        await sandbox.kill(handle)
        again = await sandbox.poll(handle)
        assert (first.status, first.exit_code) == ("exited", 7)
        assert (again.status, again.exit_code) == ("exited", 7)
    finally:
        await sandbox.kill(handle)


async def test_kill_waits_for_real_exit_code_and_preserves_it() -> None:
    sandbox = LocalSandbox()
    handle = await sandbox.start("sleep 30")
    try:
        await sandbox.kill(handle)
        first = await sandbox.poll(handle)
        await sandbox.kill(handle)
        again = await sandbox.poll(handle)
        assert first.status == again.status == "killed"
        assert first.exit_code is not None and first.exit_code < 0
        assert again.exit_code == first.exit_code
    finally:
        await sandbox.kill(handle)


async def test_lost_local_process_reference_is_not_a_cancellation_proof() -> None:
    sandbox = LocalSandbox()
    with pytest.raises(SandboxError, match="not available"):
        await sandbox.poll(ProcessHandle(command_id="unknown", provider_ref="lost-reference"))
