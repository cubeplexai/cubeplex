"""Local sandbox using asyncio subprocesses — for dev/debug only."""

import asyncio
import os
import uuid
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from cubeplex.sandbox.base import BrowserEndpoint, ExecuteResult, Sandbox


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
