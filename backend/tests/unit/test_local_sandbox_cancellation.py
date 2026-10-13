"""Foreground process cancellation must stop the local sandbox command."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from cubeplex.sandbox.local import LocalSandbox


@pytest.mark.asyncio
async def test_cancelled_execute_kills_started_process(monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()
    killed = asyncio.Event()

    class FakeProcess:
        stdout: Any

        def __init__(self) -> None:
            self.stdout = self

        async def read(self, size: int) -> bytes:
            del size
            started.set()
            await asyncio.Event().wait()
            return b""

        def kill(self) -> None:
            killed.set()

        async def wait(self) -> int:
            return -9

    async def create_process(*args: Any, **kwargs: Any) -> FakeProcess:
        del args, kwargs
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", create_process)
    task = asyncio.create_task(LocalSandbox().execute("sleep 999"))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert killed.is_set()
