"""OpenSandbox implementation of the Sandbox base class."""

import asyncio
import base64
import re
import shlex
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

# Sourced by /etc/bash.bashrc in the sandbox image — keep the two in sync.
# /run is container-local: writing under /workspace would strand placeholders on
# the user's PVC, where they outlive the sandbox that minted them.
_TERMINAL_ENV_FILE = "/run/cubeplex/sandbox-env.sh"

# Deliberately not imported from the service that validates env_name on write:
# this is the last gate before a name becomes shell code, so it re-checks
# independently and skips anything a pre-validation row might already carry.
_POSIX_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _is_timeout_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "timeout" in text or "timed out" in text


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

        async def _run() -> ExecuteResult:
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

        try:
            if timeout is not None:
                return await asyncio.wait_for(_run(), timeout=timeout)
            return await _run()
        except TimeoutError:
            return ExecuteResult(output="[timeout]", exit_code=-1)
        except SandboxError as exc:
            if timeout is not None and _is_timeout_error(exc):
                return ExecuteResult(output="[timeout]", exit_code=-1)
            raise

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        """Write files then chown by numeric uid so agent can edit them.

        The files API typically writes as root; without a follow-up chown the
        agent (uid 1000) cannot edit the uploaded path.
        """
        if not files:
            return
        with _as_sandbox_error():
            for path, content in files:
                await self._sandbox.files.write_file(path, content)
        await self._chown_paths([path for path, _ in files])

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

    async def _chown_paths(self, paths: list[str]) -> None:
        """Best-effort chown of paths to run_uid:run_gid (root only)."""
        if self._run_uid is None or not paths:
            return
        gid = self._run_gid if self._run_gid is not None else self._run_uid
        quoted = " ".join(shlex.quote(p) for p in paths)
        try:
            result = await self.execute(
                f"chown {self._run_uid}:{gid} {quoted} 2>/dev/null || true",
                timeout=60,
                as_root=True,
            )
        except Exception as exc:
            logger.warning("chown after upload failed on sandbox {}: {}", self.id, exc)
            return
        if result.exit_code not in (0, None):
            logger.warning(
                "chown after upload failed on sandbox {}: {}",
                self.id,
                result.output,
            )

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

    async def _write_terminal_env(self) -> None:
        """Drop the injected env where a terminal shell can source it.

        ttyd inherits its environment once, when it starts, and a process's
        environ cannot be rewritten afterwards — so a shell it forks can never be
        handed a later generation of placeholders. Writing the env to a file that
        the image's /etc/bash.bashrc sources means each new terminal reads the
        current one, with no ttyd restart and no session interrupted.

        Rewritten in full every time so a deleted entry disappears too.
        """
        lines = [
            f"export {name}={shlex.quote(value)}"
            for name, value in sorted(self._run_env.items())
            if _POSIX_NAME_RE.fullmatch(name)
        ]
        # base64 so neither a value nor a name can break out of the write command.
        payload = base64.b64encode(("\n".join(lines) + "\n").encode()).decode()
        directory = _TERMINAL_ENV_FILE.rsplit("/", 1)[0]
        # Publish by rename, never by truncating in place: `> file` empties it before
        # base64 writes a byte, and a shell starting in that window would source a
        # partial file — the same silent half-configured terminal this is meant to
        # prevent. mktemp keeps two concurrent writers off each other's scratch file,
        # and rename(2) is atomic, so a reader sees the old file or the new one.
        # The trap fires on the failure paths, where the && chain stops with the scratch
        # file still on disk; after a successful rename there is nothing left to remove.
        result = await self.execute(
            f"mkdir -p {directory}"
            f" && tmp=$(mktemp {directory}/.sandbox-env.XXXXXX)"
            " && trap 'rm -f \"$tmp\"' EXIT"
            f' && printf %s {shlex.quote(payload)} | base64 -d > "$tmp"'
            ' && chmod 0644 "$tmp"'
            f' && mv -f "$tmp" {_TERMINAL_ENV_FILE}',
            timeout=30,
            as_root=True,
        )
        if result.exit_code not in (0, None):
            raise SandboxError(f"failed to write sandbox terminal env: {result.output}")

    async def start_terminal(self) -> None:
        """Start ttyd; shell runs as the configured sandbox uid when possible."""
        # Before ttyd, so the first shell of a fresh sandbox already sees the env.
        await self._write_terminal_env()
        if self._run_uid is not None:
            gid = self._run_gid if self._run_gid is not None else self._run_uid
            shell = f"setpriv --reuid={self._run_uid} --regid={gid} --clear-groups -- bash"
        elif self._run_user:
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
        """Best-effort chown of the workdir mount point (non-recursive).

        Fresh PVC mounts are usually root-owned and mode 755, so uid 1000
        cannot create files until the mount point itself is chowned. Only the
        directory is fixed — not a recursive walk of existing content. Never
        raises: create must not fail solely because chown is unavailable.
        """
        if self._run_uid is None:
            return
        gid = self._run_gid if self._run_gid is not None else self._run_uid
        # Non-recursive: only the mount point. Agent-created files already land
        # as run_uid; uploads are chowned per-path in upload().
        try:
            result = await self.execute(
                f"chown {self._run_uid}:{gid} {shlex.quote(self._workdir)} 2>/dev/null || true",
                timeout=30,
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
