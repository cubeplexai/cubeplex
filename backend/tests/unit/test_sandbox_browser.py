"""Unit tests for the sandbox browser live-view plumbing."""

from __future__ import annotations

import pytest

from cubebox.sandbox.base import BrowserEndpoint, ExecuteResult, Sandbox
from cubebox.sandbox.local import LocalSandbox


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
    from cubebox.sandbox.opensandbox import OpenSandbox

    class _FakeSigned:
        endpoint = "https://signed.example/neko?token=abc"
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

    assert ep.url == "https://signed.example/neko?token=abc"
    assert ep.headers == {}
    assert len(inner.calls) == 1
    port, _expires = inner.calls[0]
    assert port == 8080
