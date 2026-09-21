"""Execution evidence from one immutable sandbox instance, without lifecycle writes."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from cubeplex.sandbox.base import ProcessHandle, ProcessSnapshot, Sandbox, SandboxError


@dataclass(frozen=True)
class CommandObservation:
    snapshot: ProcessSnapshot | None
    error: str | None = None


class CommandAdapter:
    def __init__(self, sandbox: Sandbox, *, sandbox_instance_id: str) -> None:
        self.sandbox = sandbox
        if not sandbox_instance_id or sandbox.id != sandbox_instance_id:
            raise SandboxError("command attachment does not match its original sandbox instance")

    async def observe_and_stop(
        self,
        handle: ProcessHandle,
        *,
        stop_requested: bool,
        check_owner: Callable[[], Awaitable[None]],
    ) -> CommandObservation:
        snapshot: ProcessSnapshot | None = None
        errors: list[str] = []
        await check_owner()
        try:
            async with asyncio.timeout(10):
                snapshot = await self.sandbox.observe(handle)
        except (SandboxError, TimeoutError) as exc:
            errors.append(f"observe: {exc}")
        if not stop_requested or (snapshot is not None and snapshot.status != "running"):
            return CommandObservation(snapshot=snapshot, error="; ".join(errors) or None)

        await check_owner()
        try:
            async with asyncio.timeout(10):
                await self.sandbox.kill(handle)
        except (SandboxError, TimeoutError) as exc:
            errors.append(f"cancel: {exc}")
        # Neither an interrupt receipt nor its failure proves the process exited.
        await check_owner()
        try:
            async with asyncio.timeout(10):
                snapshot = await self.sandbox.observe(handle)
        except (SandboxError, TimeoutError) as exc:
            snapshot = None
            errors.append(f"observe after cancel: {exc}")
        return CommandObservation(snapshot=snapshot, error="; ".join(errors) or None)
