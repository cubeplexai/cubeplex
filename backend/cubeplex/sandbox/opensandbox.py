"""OpenSandbox implementation of the Sandbox base class."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

import opensandbox
from loguru import logger
from opensandbox.config import ConnectionConfig
from opensandbox.exceptions import SandboxException as _ProviderError
from opensandbox.models.execd import RunCommandOpts

from cubeplex.sandbox.base import BrowserEndpoint, ExecuteResult, Sandbox, SandboxError


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

    # OpenSandbox infrastructure headers that are irrelevant for browser/Neko access.
    # The signed URL embeds the gateway auth; these headers are only meaningful for
    # server-to-server calls (egress proxy, secure-access token) and cannot be sent
    # by an iframe anyway. Strip them so they don't trigger the 501 safeguard in
    # ws_browser.get_live_view.
    _BROWSER_IRRELEVANT_HEADERS: frozenset[str] = frozenset(
        h.lower() for h in ("OPENSANDBOX-EGRESS-AUTH", "OpenSandbox-Secure-Access")
    )

    async def get_browser_endpoint(self, *, expires_in: int = 3600) -> BrowserEndpoint:
        with _as_sandbox_error():
            expires = int(time.time()) + expires_in
            endpoint = await self._sandbox.get_signed_endpoint(self.BROWSER_PORT, expires)
            url = endpoint.endpoint
            # OpenSandbox returns a scheme-less host/path; an iframe needs a full URL.
            if not url.startswith(("http://", "https://")):
                protocol = getattr(self._sandbox.connection_config, "protocol", "http")
                url = f"{protocol}://{url}"
            # A trailing slash after the .../proxy/<port> path is REQUIRED: the Neko
            # client uses relative asset/WS paths, so without it they resolve against
            # .../proxy/ (dropping the port) and the proxy returns 401 — the client JS
            # never loads and only the static login shell shows.
            if not url.endswith("/"):
                url += "/"
            headers = {
                k: v
                for k, v in (endpoint.headers or {}).items()
                if k.lower() not in self._BROWSER_IRRELEVANT_HEADERS
            }
            return BrowserEndpoint(url=url, headers=headers)

    async def get_terminal_endpoint(self, *, expires_in: int = 3600) -> BrowserEndpoint:
        with _as_sandbox_error():
            expires = int(time.time()) + expires_in
            endpoint = await self._sandbox.get_signed_endpoint(self.TERMINAL_PORT, expires)
            url = endpoint.endpoint
            if not url.startswith(("http://", "https://")):
                protocol = getattr(
                    self._sandbox.connection_config,
                    "protocol",
                    "http",
                )
                url = f"{protocol}://{url}"
            if not url.endswith("/"):
                url += "/"
            headers = {
                k: v
                for k, v in (endpoint.headers or {}).items()
                if k.lower() not in self._BROWSER_IRRELEVANT_HEADERS
            }
            return BrowserEndpoint(url=url, headers=headers)

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
