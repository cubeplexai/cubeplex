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
    """Sandbox backed by a remote OpenSandbox container.

    Agent shell commands run as the configured non-root user (``run_uid`` /
    ``run_gid``, default cubeplex 1000:1000) via OpenSandbox ``RunCommandOpts``.
    Infrastructure helpers (browser stack, workspace ownership fixups) call
    ``execute(..., as_root=True)`` so supervisord can still drop privileges and
    chown PVC contents.
    """

    def __init__(
        self,
        *,
        sandbox: opensandbox.Sandbox,
        workdir: str = "/workspace",
        run_uid: int | None = 1000,
        run_gid: int | None = 1000,
        run_user: str | None = "cubeplex",
    ) -> None:
        self._sandbox = sandbox
        self._workdir = workdir
        self._run_env: dict[str, str] = {}
        self._run_uid = run_uid
        self._run_gid = run_gid
        self._run_user = run_user

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
        as_root: bool = False,
    ) -> ExecuteResult:
        # Merge: run-level env (set by manager) is the base; per-call envs win.
        merged = {**self._run_env, **(envs or {})}
        uid: int | None = None if as_root else self._run_uid
        gid: int | None = None if as_root or uid is None else self._run_gid
        opts = RunCommandOpts(
            working_directory=self._workdir,
            envs=merged if merged else None,
            timeout=timedelta(seconds=timeout) if timeout is not None else None,
            uid=uid,
            gid=gid,
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
                await self._sandbox.files.write_file(
                    path,
                    content,
                    owner=self._run_user,
                    group=self._run_user,
                )

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

    async def start_browser(self) -> None:
        """Start Neko as root so supervisord can chown and drop to cubeplex."""
        result = await self.execute("/usr/local/bin/start-browser.sh", timeout=120, as_root=True)
        if result.exit_code not in (0, None):
            raise RuntimeError(f"failed to start sandbox browser: {result.output}")

    async def start_terminal(self) -> None:
        """Start ttyd; shell runs as the configured sandbox user when possible."""
        if self._run_user:
            # runuser keeps the interactive shell as cubeplex even if ttyd is
            # launched with root privileges for start-stop-daemon.
            shell = f"runuser -u {self._run_user} -- bash"
        else:
            shell = "bash"
        result = await self.execute(
            "start-stop-daemon --start --oknodo --background"
            " --make-pidfile --pidfile /tmp/ttyd.pid"
            f" --exec /usr/bin/ttyd -- -p 7681 -W -w /workspace {shell}"
            " && sleep 1",
            timeout=30,
            as_root=True,
        )
        if result.exit_code not in (0, None):
            raise SandboxError(f"failed to start sandbox terminal: {result.output}")

    async def ensure_workspace_owner(self) -> None:
        """Best-effort chown of the workdir to the sandbox user (root only).

        PVC mounts often arrive as root-owned; agent commands run as cubeplex
        and need write access. Safe no-op when run_user is unset. Never raises:
        provisioning must not fail solely because chown is unavailable.
        """
        if not self._run_user:
            return
        try:
            result = await self.execute(
                f"chown -R {self._run_user}:{self._run_user} {self._workdir} 2>/dev/null || true",
                timeout=60,
                as_root=True,
            )
        except Exception as exc:
            logger.warning(
                "workspace chown for {} failed on sandbox {}: {}",
                self._workdir,
                self.id,
                exc,
            )
            return
        if result.exit_code not in (0, None):
            logger.warning(
                "workspace chown for {} failed on sandbox {}: {}",
                self._workdir,
                self.id,
                result.output,
            )

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
        run_uid: int | None = 1000,
        run_gid: int | None = 1000,
        run_user: str | None = "cubeplex",
        **_: object,
    ) -> "OpenSandbox":
        with _as_sandbox_error():
            raw = await opensandbox.Sandbox.resume(
                sandbox_id,
                connection_config=conn_config,
                resume_timeout=timedelta(seconds=resume_timeout),
            )
        return cls(
            sandbox=raw,
            workdir=workdir,
            run_uid=run_uid,
            run_gid=run_gid,
            run_user=run_user,
        )
