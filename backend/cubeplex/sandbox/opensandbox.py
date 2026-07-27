"""OpenSandbox implementation of the Sandbox base class."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

import opensandbox
from loguru import logger
from opensandbox.config import ConnectionConfig
from opensandbox.exceptions import SandboxException as _ProviderError
from opensandbox.models.execd import RunCommandOpts

from cubeplex.sandbox.base import BrowserEndpoint, ExecuteResult, Sandbox, SandboxError
from cubeplex.sandbox.panel_token import (
    get_panel_base_url,
    get_panel_secret,
    sign_panel_token,
)


@contextmanager
def _as_sandbox_error() -> Iterator[None]:
    """Translate the OpenSandbox provider's exceptions into a driver-agnostic
    SandboxError, so the opensandbox dependency never leaks past this driver."""
    try:
        yield
    except _ProviderError as exc:
        raise SandboxError(str(exc)) from exc


class OpenSandbox(Sandbox):
    """Sandbox backed by a remote OpenSandbox container."""

    def __init__(self, *, sandbox: opensandbox.Sandbox, workdir: str = "/workspace") -> None:
        self._sandbox = sandbox
        self._workdir = workdir
        self._run_env: dict[str, str] = {}

    @property
    def id(self) -> str:
        return self._sandbox.id

    @property
    def workdir(self) -> str:
        return self._workdir

    def set_run_env(self, env: dict[str, str]) -> None:
        """Replace the run-level env dict injected into every execute call."""
        self._run_env = env

    async def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        envs: dict[str, str] | None = None,
    ) -> ExecuteResult:
        # Merge: run-level env (set by manager) is the base; per-call envs win.
        merged = {**self._run_env, **(envs or {})}
        opts = RunCommandOpts(
            working_directory=self._workdir,
            envs=merged if merged else None,
            timeout=timedelta(seconds=timeout) if timeout is not None else None,
        )
        with _as_sandbox_error():
            execution = await self._sandbox.commands.run(command, opts=opts)

            output_lines: list[str] = []
            for msg in execution.logs.stdout:
                output_lines.append(msg.text)
            for msg in execution.logs.stderr:
                output_lines.append(msg.text)
            output = "\n".join(output_lines) if output_lines else ""

            exit_code: int | None = None
            if execution.id:
                try:
                    status = await self._sandbox.commands.get_command_status(execution.id)
                    exit_code = status.exit_code
                except Exception as e:
                    logger.warning("Could not get exit code for command: {}", e)

            return ExecuteResult(output=output, exit_code=exit_code)

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        with _as_sandbox_error():
            for path, content in files:
                await self._sandbox.files.write_file(path, content)

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        with _as_sandbox_error():
            result = []
            for path in paths:
                try:
                    content = await self._sandbox.files.read_bytes(path)
                except Exception as exc:
                    if "404" in str(exc):
                        raise FileNotFoundError(path) from exc
                    raise
                result.append((path, content))
            return result

    def _panel_endpoint(self, port: int, expires_in: int) -> BrowserEndpoint:
        """Build a cubeplex-signed panel-proxy URL for a sandbox port.

        The token lives in the URL path so the panel client's relative asset and
        WebSocket requests all carry the credential (an iframe/WS sub-resource
        can't attach auth headers). The cubeplex backend reverse-proxy route
        verifies it and forwards to the cluster-internal opensandbox-server;
        see docs/dev/specs/2026-07-26-sandbox-panel-proxy-design.md.
        """
        base = get_panel_base_url()
        if not base:
            raise SandboxError(
                "sandbox panel is unavailable: backend public URL "
                "(api.public_url) is not configured"
            )
        token = sign_panel_token(
            sandbox_id=self._sandbox.id,
            port=port,
            secret=get_panel_secret(),
            ttl=timedelta(seconds=expires_in),
        )
        # Trailing slash keeps the panel client's relative paths resolving under
        # the token prefix (drop it and they'd resolve one level up, losing the
        # token) — the same reason the previous signed-URL path appended one.
        return BrowserEndpoint(url=f"{base}/sandbox-panel/{token}/", headers={})

    async def get_browser_endpoint(self, *, expires_in: int = 3600) -> BrowserEndpoint:
        return self._panel_endpoint(self.BROWSER_PORT, expires_in)

    async def get_terminal_endpoint(self, *, expires_in: int = 3600) -> BrowserEndpoint:
        return self._panel_endpoint(self.TERMINAL_PORT, expires_in)

    async def close(self) -> None:
        pass

    def supports_pause(self) -> bool:
        return True

    async def pause(self) -> None:
        with _as_sandbox_error():
            await self._sandbox.pause()

    async def renew(self, timeout_seconds: int) -> None:
        with _as_sandbox_error():
            await self._sandbox.renew(timedelta(seconds=timeout_seconds))

    @classmethod
    async def connect_or_resume(  # type: ignore[override]
        cls,
        sandbox_id: str,
        *,
        conn_config: ConnectionConfig | None = None,
        resume_timeout: int = 30,
        workdir: str = "/workspace",
        **_: object,
    ) -> "OpenSandbox":
        with _as_sandbox_error():
            raw = await opensandbox.Sandbox.resume(
                sandbox_id,
                connection_config=conn_config,
                resume_timeout=timedelta(seconds=resume_timeout),
            )
        return cls(sandbox=raw, workdir=workdir)
