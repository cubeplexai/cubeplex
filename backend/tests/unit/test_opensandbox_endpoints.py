"""Browser/terminal endpoint methods build a signed cubeplex panel-proxy URL."""

from __future__ import annotations

import pytest

from cubeplex.sandbox.base import SandboxError
from cubeplex.sandbox.opensandbox import OpenSandbox
from cubeplex.sandbox.panel_token import verify_panel_token


class _FakeSandbox:
    def __init__(self, sid: str) -> None:
        self.id = sid


@pytest.fixture(autouse=True)
def _panel_config():
    from cubeplex.config import config

    prev_url = config.get("api.public_url", "")
    prev_secret = config.get("auth.jwt_secret", "CHANGE_ME")
    config.set("api.public_url", "https://app.example.com")
    config.set("auth.jwt_secret", "unit-test-secret")
    yield
    config.set("api.public_url", prev_url)
    config.set("auth.jwt_secret", prev_secret)


class TestPanelEndpoints:
    async def test_browser_endpoint_is_signed_panel_url(self) -> None:
        sb = OpenSandbox(sandbox=_FakeSandbox("sbx_1"))  # type: ignore[arg-type]
        ep = await sb.get_browser_endpoint()
        assert ep.headers == {}
        assert ep.url.startswith("https://app.example.com/sandbox-panel/")
        assert ep.url.endswith("/")
        token = ep.url[len("https://app.example.com/sandbox-panel/") :].rstrip("/")
        claims = verify_panel_token(token, secret="unit-test-secret")
        assert claims.sandbox_id == "sbx_1"
        assert claims.port == OpenSandbox.BROWSER_PORT

    async def test_terminal_endpoint_port(self) -> None:
        sb = OpenSandbox(sandbox=_FakeSandbox("sbx_2"))  # type: ignore[arg-type]
        ep = await sb.get_terminal_endpoint()
        token = ep.url[len("https://app.example.com/sandbox-panel/") :].rstrip("/")
        claims = verify_panel_token(token, secret="unit-test-secret")
        assert claims.sandbox_id == "sbx_2"
        assert claims.port == OpenSandbox.TERMINAL_PORT

    async def test_missing_public_url_raises(self) -> None:
        from cubeplex.config import config

        config.set("api.public_url", "")
        sb = OpenSandbox(sandbox=_FakeSandbox("sbx_3"))  # type: ignore[arg-type]
        with pytest.raises(SandboxError, match="public URL"):
            await sb.get_browser_endpoint()
