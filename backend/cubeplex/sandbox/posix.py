"""POSIX-guest shared sandbox mixin for container/VM sandbox backends."""

from __future__ import annotations

import base64
import re
import shlex
from datetime import timedelta

from loguru import logger

from cubeplex.sandbox.base import (
    BrowserEndpoint,
    Sandbox,
    SandboxCapabilityUnsupportedError,
    SandboxError,
)
from cubeplex.sandbox.browser_tab_follow import tab_follow_install_command
from cubeplex.sandbox.panel_token import (
    get_panel_base_url,
    get_panel_secret,
    sign_panel_token,
)

_TERMINAL_ENV_FILE = "/run/cubeplex/sandbox-env.sh"
_POSIX_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PosixSandboxMixin(Sandbox):
    """Shared POSIX guest helpers for container-backed sandboxes.

    Subclasses may set ``_run_user``, ``_run_uid``, and ``_run_gid`` to control
    guest privilege drop and workspace ownership.
    """

    _run_user: str | None = "cubeplex"
    _run_uid: int | None = 1000
    _run_gid: int | None = 1000
    _run_env: dict[str, str] | None = None

    def set_run_env(self, env: dict[str, str]) -> None:
        self._run_env = dict(env)

    async def _write_terminal_env(self) -> None:
        """Atomically publish run env to ``/run/cubeplex/sandbox-env.sh``.

        ``ttyd`` freezes its environment on start, so interactive shells opened
        via the terminal source this file via ``/etc/bash.bashrc``.
        """
        env = self._run_env or {}
        lines = [
            f"export {name}={shlex.quote(value)}"
            for name, value in sorted(env.items())
            if _POSIX_NAME_RE.fullmatch(name)
        ]
        script = ("\n".join(lines) + "\n") if lines else "\n"
        encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
        target = shlex.quote(_TERMINAL_ENV_FILE)
        cmd = (
            f'd=$(dirname {target}) && mkdir -p "$d" '
            f'&& tmp=$(mktemp "$d/.sandbox-env.XXXXXX") '
            f"&& trap 'rm -f \"$tmp\"' EXIT "
            f'&& printf %s {shlex.quote(encoded)} | base64 -d > "$tmp" '
            f'&& chmod 0644 "$tmp" '
            f'&& mv -f "$tmp" {target} '
            f"&& trap - EXIT"
        )
        res = await self.execute(cmd, as_root=True)
        if isinstance(res.exit_code, int) and res.exit_code != 0:
            raise SandboxError(
                f"failed to write terminal env (exit {res.exit_code}): {res.output.strip()}"
            )

    def _terminal_shell_command(self) -> str:
        """Build the interactive shell invocation passed to ttyd."""
        wd = shlex.quote(self.workdir)
        if self._run_uid is not None and self._run_gid is not None:
            uid = int(self._run_uid)
            gid = int(self._run_gid)
            inner = (
                f'export HOME="$(getent passwd {uid} | cut -d: -f6 || echo /root)" '
                f"&& cd {wd} && exec bash -i"
            )
            return f"setpriv --reuid={uid} --regid={gid} --init-groups sh -c {shlex.quote(inner)}"
        if self._run_user:
            user = shlex.quote(self._run_user)
            return f"runuser -u {user} -- bash -lc {shlex.quote(f'cd {wd} && exec bash -i')}"
        return f"bash -lc {shlex.quote(f'cd {wd} && exec bash -i')}"

    async def write_terminal_env(self) -> None:
        """Publish current run env for interactive shells."""
        await self._write_terminal_env()

    async def start_terminal(self) -> None:
        """Write terminal env and start ``ttyd`` on ``TERMINAL_PORT`` if needed."""
        if not self.capabilities.terminal:
            raise SandboxCapabilityUnsupportedError("terminal", self.provider_name)
        await self.write_terminal_env()
        wd = shlex.quote(self.workdir)
        cmd = (
            "start-stop-daemon --start --oknodo --background"
            " --make-pidfile --pidfile /tmp/ttyd.pid"
            f" --exec /usr/bin/ttyd -- -p {self.TERMINAL_PORT} -W -w {wd} "
            f"{self._terminal_shell_command()}"
            " && sleep 1"
        )
        res = await self.execute(cmd, timeout=30, as_root=True)
        if isinstance(res.exit_code, int) and res.exit_code != 0:
            raise SandboxError(
                f"failed to start sandbox terminal (exit {res.exit_code}): {res.output.strip()}"
            )

    async def install_browser_tab_follow(self) -> None:
        """Wrap ``agent-browser`` so the live view follows the agent's tab."""
        try:
            res = await self.execute(tab_follow_install_command(), timeout=15, as_root=True)
        except Exception as exc:
            logger.warning("browser tab-follow install failed: {}", exc)
            return
        if isinstance(res.exit_code, int) and res.exit_code != 0:
            logger.warning(
                "browser tab-follow install exited {}: {}",
                res.exit_code,
                res.output,
            )

    async def start_browser(self) -> None:
        """Install tab-follow and ensure the in-sandbox browser/live-view stack is up."""
        if not self.capabilities.browser:
            raise SandboxCapabilityUnsupportedError("browser", self.provider_name)
        await self.install_browser_tab_follow()
        res = await self.execute("/usr/local/bin/start-browser.sh", timeout=120, as_root=True)
        if isinstance(res.exit_code, int) and res.exit_code != 0:
            raise SandboxError(f"failed to start sandbox browser: {res.output}")

    async def ensure_workspace_owner(self) -> None:
        """Best-effort creation + non-recursive chown of the sandbox workdir."""
        if self._run_uid is not None and self._run_gid is not None:
            owner = f"{int(self._run_uid)}:{int(self._run_gid)}"
        elif self._run_user:
            owner = shlex.quote(self._run_user)
        else:
            return
        quoted = shlex.quote(self.workdir)
        try:
            res = await self.execute(
                f"mkdir -p {quoted} && chown {owner} {quoted} 2>/dev/null || true",
                timeout=30,
                as_root=True,
            )
        except Exception as exc:
            logger.warning(
                "workspace prepare for {} failed on sandbox {}: {}",
                self.workdir,
                self.id,
                exc,
            )
            return
        if isinstance(res.exit_code, int) and res.exit_code != 0:
            logger.warning(
                "workspace prepare for {} exited {} on sandbox {}: {}",
                self.workdir,
                res.exit_code,
                self.id,
                res.output,
            )

    def _panel_endpoint(self, port: int, *, expires_in: int = 3600) -> BrowserEndpoint:
        """Mint a header-free signed panel proxy endpoint for *port*."""
        base = get_panel_base_url()
        if not base:
            raise SandboxError(
                "Sandbox panel proxy requires a public URL "
                "(set api.public_url or frontend.base_url)"
            )
        token = sign_panel_token(
            sandbox_id=self.id,
            port=port,
            secret=get_panel_secret(),
            ttl=timedelta(seconds=expires_in),
            provider=self.provider_name,
        )
        return BrowserEndpoint(url=f"{base}/sandbox-panel/{token}/", headers={})

    async def get_browser_endpoint(self, *, expires_in: int = 3600) -> BrowserEndpoint:
        if not self.capabilities.browser:
            raise SandboxCapabilityUnsupportedError("browser", self.provider_name)
        return self._panel_endpoint(self.BROWSER_PORT, expires_in=expires_in)

    async def get_terminal_endpoint(self, *, expires_in: int = 3600) -> BrowserEndpoint:
        if not self.capabilities.terminal:
            raise SandboxCapabilityUnsupportedError("terminal", self.provider_name)
        return self._panel_endpoint(self.TERMINAL_PORT, expires_in=expires_in)
