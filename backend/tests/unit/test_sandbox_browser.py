"""Unit tests for the sandbox browser live-view plumbing."""

from __future__ import annotations

import pytest

from cubeplex.sandbox.base import BrowserEndpoint, ExecuteResult, Sandbox
from cubeplex.sandbox.local import LocalSandbox


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

    async def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResult:
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
    assert sb.commands == ["/usr/local/bin/start-browser.sh"]


@pytest.mark.asyncio
async def test_start_browser_raises_on_failure() -> None:
    sb = _RecordingSandbox(exit_code=1)
    with pytest.raises(RuntimeError, match="failed to start sandbox browser"):
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
async def test_opensandbox_browser_endpoint_uses_signed_endpoint() -> None:
    """OpenSandbox.get_browser_endpoint signs port 8080 and maps the result."""
    from cubeplex.sandbox.opensandbox import OpenSandbox

    class _FakeSigned:
        # OpenSandbox returns a scheme-qualified host/path with no query string.
        endpoint = "https://signed.example/sandboxes/sb-1/proxy/8080"
        headers: dict[str, str] = {}

    class _FakeInner:
        id = "sb-1"

        def __init__(self) -> None:
            self.calls: list[tuple[int, int]] = []

        async def get_signed_endpoint(self, port: int, expires: int) -> _FakeSigned:
            self.calls.append((port, expires))
            return _FakeSigned()

    inner = _FakeInner()
    sb = OpenSandbox(sandbox=inner)  # type: ignore[arg-type]
    ep = await sb.get_browser_endpoint(expires_in=1800)

    # A trailing slash is appended so the Neko client's relative asset/WS paths
    # resolve under .../proxy/8080/ instead of dropping the port segment.
    assert ep.url == "https://signed.example/sandboxes/sb-1/proxy/8080/"
    assert ep.headers == {}
    assert len(inner.calls) == 1
    port, _expires = inner.calls[0]
    assert port == 8080


@pytest.mark.asyncio
async def test_opensandbox_translates_provider_error_to_sandbox_error() -> None:
    """The driver must not leak opensandbox's own exception type to callers."""
    from opensandbox.exceptions.sandbox import SandboxInternalException

    from cubeplex.sandbox.base import SandboxError
    from cubeplex.sandbox.opensandbox import OpenSandbox

    class _FailingInner:
        id = "sb-1"

        async def get_signed_endpoint(self, port: int, expires: int):
            raise SandboxInternalException("Network connectivity error")

    sb = OpenSandbox(sandbox=_FailingInner())  # type: ignore[arg-type]
    with pytest.raises(SandboxError):
        await sb.get_browser_endpoint()


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
