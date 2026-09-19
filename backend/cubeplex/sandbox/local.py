"""Local sandbox using asyncio subprocesses — for dev/debug only."""

import asyncio
import inspect
import os
import signal
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from loguru import logger

from cubeplex.sandbox.base import (
    BrowserEndpoint,
    ExecuteResult,
    ProcessHandle,
    ProcessSnapshot,
    Sandbox,
)


class _LocalBgProc:
    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc
        self.killed = False
        self.pump_task: asyncio.Task[None] | None = None
        self._buf = bytearray()
        self._lock = asyncio.Lock()

    async def pump(self) -> None:
        assert self.proc.stdout is not None
        while True:
            data = await self.proc.stdout.read(4096)
            if not data:
                break
            async with self._lock:
                self._buf.extend(data)

    async def take(self) -> str:
        async with self._lock:
            if not self._buf:
                return ""
            text = bytes(self._buf).decode(errors="replace")
            self._buf.clear()
            return text


def _emit_chunk(on_chunk: Callable[[str], None] | None, text: str) -> None:
    if on_chunk is None or not text:
        return
    try:
        on_chunk(text)
    except Exception:
        logger.exception("LocalSandbox on_chunk failed")


class LocalSandbox(Sandbox):
    """Sandbox backed by local asyncio subprocesses.

    Not suitable for production. Use for development and testing.
    """

    def __init__(self, *, workdir: str | None = None) -> None:
        self._id = str(uuid.uuid4())
        self._workdir = workdir or os.getcwd()
        self._bg: dict[str, _LocalBgProc] = {}

    @property
    def id(self) -> str:
        return self._id

    @property
    def workdir(self) -> str:
        return self._workdir

    async def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        envs: dict[str, str] | None = None,
        as_root: bool = False,
        on_chunk: Callable[[str], None] | None = None,
    ) -> ExecuteResult:
        # envs/as_root accepted for interface compatibility but not applied:
        # LocalSandbox runs in the host process environment and is not used
        # in production.
        del envs, as_root
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self._workdir,
        )
        chunks: list[str] = []

        async def _pump() -> None:
            assert proc.stdout is not None
            while True:
                data = await proc.stdout.read(4096)
                if not data:
                    break
                text = data.decode(errors="replace")
                chunks.append(text)
                _emit_chunk(on_chunk, text)

        try:
            await asyncio.wait_for(_pump(), timeout=timeout)
            await proc.wait()
        except TimeoutError:
            proc.kill()
            try:
                await proc.wait()
            except Exception:
                pass
            return ExecuteResult(output="[timeout]", exit_code=-1)

        return ExecuteResult(
            output="".join(chunks),
            exit_code=proc.returncode,
        )

    def supports_background(self) -> bool:
        return True

    async def start(
        self,
        command: str,
        *,
        timeout: int | None = None,
        envs: dict[str, str] | None = None,
        as_root: bool = False,
        on_chunk: Callable[[str], None] | None = None,
        on_started: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> ProcessHandle:
        del timeout, envs, as_root, on_chunk
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self._workdir,
            start_new_session=True,
        )
        rec = _LocalBgProc(proc)
        ref = str(id(rec))
        self._bg[ref] = rec
        rec.pump_task = asyncio.create_task(rec.pump())
        if on_started is not None:
            maybe = on_started(ref)
            if inspect.isawaitable(maybe):
                await maybe
        return ProcessHandle(command_id="", provider_ref=ref)

    async def poll(self, handle: ProcessHandle) -> ProcessSnapshot:
        rec = self._bg.get(handle.provider_ref)
        if rec is None:
            return ProcessSnapshot(status="killed", new_output="")
        new_output = await rec.take()
        code = rec.proc.returncode
        if rec.killed and code is not None:
            self._bg.pop(handle.provider_ref, None)
            return ProcessSnapshot(status="killed", exit_code=code, new_output=new_output)
        if code is not None:
            self._bg.pop(handle.provider_ref, None)
            return ProcessSnapshot(status="exited", exit_code=code, new_output=new_output)
        return ProcessSnapshot(status="running", new_output=new_output)

    async def kill(self, handle: ProcessHandle) -> None:
        rec = self._bg.pop(handle.provider_ref, None)
        if rec is None:
            return
        rec.killed = True
        if rec.pump_task is not None and not rec.pump_task.done():
            rec.pump_task.cancel()
        pid = rec.proc.pid
        if pid is not None:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                try:
                    rec.proc.kill()
                except ProcessLookupError:
                    pass
        else:
            try:
                rec.proc.kill()
            except ProcessLookupError:
                pass
        try:
            await rec.proc.wait()
        except Exception:
            pass

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        for path, content in files:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        result = []
        for path in paths:
            result.append((path, Path(path).read_bytes()))
        return result

    async def start_browser(self) -> None:
        # Dev only: the in-image launch script isn't present on the host, so the
        # base implementation would raise. Assume a locally-running Neko (if any).
        return None

    async def get_browser_endpoint(self, *, expires_in: int = 3600) -> BrowserEndpoint:
        # Dev only: the local Neko stack (if running) is reachable on localhost.
        return BrowserEndpoint(url=f"http://localhost:{self.BROWSER_PORT}/")

    async def close(self) -> None:
        pass
