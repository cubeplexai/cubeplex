"""Unit tests for the sandbox browser live-view plumbing."""

from __future__ import annotations

from pathlib import Path

import pytest

from cubeplex.sandbox.base import BrowserEndpoint, ExecuteResult, Sandbox
from cubeplex.sandbox.local import LocalSandbox

# backend/tests/unit → repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SANDBOX_DOCKERFILE = _REPO_ROOT / "deploy" / "images" / "sandbox" / "Dockerfile"


def test_sandbox_image_enables_neko_implicit_hosting() -> None:
    """Take over must not require a second in-Neko mouse/control click (#423).

    Upstream Neko defaults to ``implicit_hosting: false``. The sandbox image must
    flip that so the first click after cubeplex "Take over" grants control, and
    must hide Neko's redundant mouse control chrome in the embed.
    """
    text = _SANDBOX_DOCKERFILE.read_text(encoding="utf-8")
    assert "NEKO_SESSION_IMPLICIT_HOSTING=true" in text
    assert "implicit_hosting: false/implicit_hosting: true" in text
    assert "fa-mouse-pointer" in text
    # Upstream clipboard panel has no close control; CubePlex injects × / Esc.
    assert "cubeplex-clipboard-close" in text
    # Keep Neko's overlay positioning. `position:relative` on `.clipboard`
    # shrinks the live-view desktop instead of floating over it.
    assert ".clipboard{position:relative}" not in text
    assert ".clipboard .cubeplex-clipboard-close{position:absolute" in text
    # Dockerfile sed is single-quoted; do not backslash-escape JS strings.
    assert 'document.querySelector("video")' in text
    assert r"querySelector(\"video\")" not in text


def test_start_browser_script_ensures_chromium_when_stack_already_up() -> None:
    """Closing the last Chromium tab leaves Neko up but CDP dead.

    ``start-browser.sh`` used to exit early on "supervisord pid ok", so refresh /
    browser skill saw "already running" while ``agent-browser connect 9222`` got
    connection refused. The script must heal Chromium and wait on CDP.
    """
    script = (_REPO_ROOT / "deploy" / "images" / "sandbox" / "neko" / "start-browser.sh").read_text(
        encoding="utf-8"
    )
    assert "ensure_chromium" in script
    assert "9222/json/version" in script
    assert "supervisorctl" in script and "restart chromium" in script
    # Must not exit before ensuring chromium on the already-running path.
    assert "neko stack already running" in script
    assert "ensure_chromium" in script.split("neko stack already running", 1)[1]


def test_start_browser_preserves_neko_json_env_without_sourcing_it() -> None:
    """TURN JSON must survive the non-root sudo re-exec unchanged.

    Shell-sourcing raw ``env`` output strips JSON's double quotes, making
    Neko reject its ICE-server configuration and leaving the live view blank.
    """
    script = (_REPO_ROOT / "deploy" / "images" / "sandbox" / "neko" / "start-browser.sh").read_text(
        encoding="utf-8"
    )
    assert 'export "$_neko_name=$_neko_value"' in script
    assert '. "$NEKO_ENV_FILE"' not in script


def test_launch_chrome_bounds_certutil_so_restart_cannot_wedge() -> None:
    """Unbounded ``certutil -N`` on an existing NSS DB spins and blocks chrome.

    Supervisord still reports chromium RUNNING (the launcher shell is alive) while
    CDP never comes up — same empty desktop / connection-refused symptom. The
    launcher must only init missing DBs, skip already-imported certs, and timeout
    every certutil call.
    """
    script = (_REPO_ROOT / "deploy" / "images" / "sandbox" / "neko" / "launch-chrome.sh").read_text(
        encoding="utf-8"
    )
    assert "timeout 5 certutil" in script
    assert "cert9.db" in script
    assert "cubeplex-egress-ca" in script


class _RecordingSandbox(Sandbox):
    """Minimal sandbox that records executed commands."""

    def __init__(self, exit_code: int = 0) -> None:
        self.commands: list[str] = []
        self._exit_code = exit_code

    @property
    def id(self) -> str:
        return "rec"

    @property
    def workdir(self) -> str:
        return "/workspace"

    async def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        envs: dict[str, str] | None = None,
        as_root: bool = False,
    ) -> ExecuteResult:
        del timeout, envs, as_root
        self.commands.append(command)
        return ExecuteResult(output="", exit_code=self._exit_code)

    async def upload(self, files: list[tuple[str, bytes]]) -> None: ...

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        return []

    async def close(self) -> None: ...


@pytest.mark.asyncio
async def test_start_browser_runs_launch_script() -> None:
    sb = _RecordingSandbox()
    await sb.start_browser()
    assert "/usr/local/bin/start-browser.sh" in sb.commands
    assert any("cubeplex-focus-browser-tab" in cmd for cmd in sb.commands)
    # Wrapper must land before Chromium so the first connect is already wrapped.
    assert sb.commands.index(
        next(cmd for cmd in sb.commands if "cubeplex-focus-browser-tab" in cmd)
    ) < sb.commands.index("/usr/local/bin/start-browser.sh")


@pytest.mark.asyncio
async def test_start_browser_raises_on_failure() -> None:
    from cubeplex.sandbox.base import SandboxError

    sb = _RecordingSandbox(exit_code=1)
    with pytest.raises(SandboxError, match="failed to start sandbox browser"):
        await sb.start_browser()


@pytest.mark.asyncio
async def test_base_get_browser_endpoint_not_supported() -> None:
    sb = _RecordingSandbox()
    with pytest.raises(NotImplementedError):
        await sb.get_browser_endpoint()


@pytest.mark.asyncio
async def test_local_sandbox_browser_endpoint_is_localhost() -> None:
    sb = LocalSandbox()
    ep = await sb.get_browser_endpoint()
    assert isinstance(ep, BrowserEndpoint)
    assert ep.url == "http://localhost:8080/"
    assert ep.headers == {}


@pytest.mark.asyncio
async def test_local_sandbox_start_browser_is_noop() -> None:
    # The in-image launch script is absent on a dev host; start must not raise.
    sb = LocalSandbox()
    await sb.start_browser()


@pytest.mark.asyncio
async def test_opensandbox_translates_provider_error_to_sandbox_error() -> None:
    """The driver must not leak opensandbox's own exception type to callers."""
    from opensandbox.exceptions.sandbox import SandboxInternalException

    from cubeplex.sandbox.base import SandboxError
    from cubeplex.sandbox.opensandbox import OpenSandbox

    class _FailingInner:
        id = "sb-1"

        async def pause(self) -> None:
            raise SandboxInternalException("Network connectivity error")

    sb = OpenSandbox(sandbox=_FailingInner())  # type: ignore[arg-type]
    with pytest.raises(SandboxError):
        await sb.pause()


@pytest.mark.asyncio
async def test_live_view_returns_503_when_sandbox_unavailable(monkeypatch) -> None:
    """A provider failure (e.g. create timeout) surfaces as 503, not a bare 500.

    The route depends only on the driver-agnostic ``SandboxError`` — never on a
    specific backend driver's exception types.
    """
    from types import SimpleNamespace

    from fastapi import HTTPException

    from cubeplex.api.routes.v1 import ws_browser
    from cubeplex.sandbox import SandboxError

    class _Manager:
        async def get_or_create(self, *args, **kwargs):
            raise SandboxError("sandbox provider timed out")

    monkeypatch.setattr(ws_browser, "get_sandbox_manager", lambda: _Manager())
    # Resolver short-circuits to (user, ctx.user.id, ctx.user.id) when
    # conversation_id is None, so we don't need a real session for that path.
    ctx = SimpleNamespace(user=SimpleNamespace(id="usr-1"), org_id="org-1", workspace_id="ws-1")

    with pytest.raises(HTTPException) as exc_info:
        await ws_browser.get_live_view(ctx, session=None, conversation_id=None)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_live_view_returns_503_when_start_browser_fails(monkeypatch) -> None:
    """Chromium heal failure must be retryable 503, not an unhandled 500.

    ``start_browser`` raises ``SandboxError`` when start-browser.sh exits non-zero
    (CDP never comes up). The route must map that the same way as provision
    failures — otherwise SWR/panel do not retry cleanly.
    """
    from types import SimpleNamespace

    from fastapi import HTTPException

    from cubeplex.api.routes.v1 import ws_browser
    from cubeplex.sandbox import SandboxError

    class _Sandbox:
        async def start_browser(self) -> None:
            raise SandboxError("failed to start sandbox browser: chromium failed")

    class _Manager:
        async def get_or_create(self, *args, **kwargs):
            return SimpleNamespace(sandbox=_Sandbox())

    monkeypatch.setattr(ws_browser, "get_sandbox_manager", lambda: _Manager())
    ctx = SimpleNamespace(user=SimpleNamespace(id="usr-1"), org_id="org-1", workspace_id="ws-1")

    with pytest.raises(HTTPException) as exc_info:
        await ws_browser.get_live_view(ctx, session=None, conversation_id=None)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_keepalive_returns_404_when_no_active_sandbox(monkeypatch) -> None:
    """Keepalive on a terminated/absent sandbox row returns 404, not a silent 204.

    ``touch_active`` returns False when the row is absent, deleted, or has
    ``sandbox_id=None`` (terminal). The route must surface that so the frontend
    closes the stale iframe instead of pinging forever against a sandbox that
    extended nothing.
    """
    from types import SimpleNamespace

    from fastapi import HTTPException

    from cubeplex.api.routes.v1 import ws_browser

    class _Manager:
        async def touch_active(self, *args, **kwargs) -> bool:
            return False  # terminated/deleted/absent row

    monkeypatch.setattr(ws_browser, "get_sandbox_manager", lambda: _Manager())
    ctx = SimpleNamespace(user=SimpleNamespace(id="usr-1"), org_id="org-1", workspace_id="ws-1")

    with pytest.raises(HTTPException) as exc_info:
        await ws_browser.keepalive(ctx, session=None, conversation_id=None)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 404
