"""Sandbox panel reverse proxy: token gating + HTTP/WebSocket relay.

Builds a minimal app carrying only the panel router and forwards it at a
test-double upstream (a uvicorn server standing in for opensandbox-server's
server-proxy). No DB / real external systems -> integration.
"""

from __future__ import annotations

import socket
import threading
import time
from datetime import timedelta

import pytest
import uvicorn
from fastapi import FastAPI, Request, WebSocket
from starlette.testclient import TestClient

from cubeplex.sandbox.panel_token import sign_panel_token

_SECRET = "panel-proxy-test-secret"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def upstream_port():
    """A stand-in opensandbox-server server-proxy: echoes what it received."""
    up = FastAPI()

    @up.api_route("/sandboxes/{sid}/proxy/{port}/{path:path}", methods=["GET", "POST"])
    async def echo(sid: str, port: int, path: str, request: Request) -> dict:
        return {"sid": sid, "port": port, "path": path, "query": request.url.query}

    @up.websocket("/sandboxes/{sid}/proxy/{port}/{path:path}")
    async def ws_echo(websocket: WebSocket, sid: str, port: int, path: str) -> None:
        await websocket.accept()
        await websocket.send_text(f"hello:{sid}:{port}:{path}")
        try:
            while True:
                msg = await websocket.receive_text()
                await websocket.send_text(f"echo:{msg}")
        except Exception:
            return

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(up, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    assert server.started, "upstream test server failed to start"
    yield port
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def client(upstream_port: int, monkeypatch: pytest.MonkeyPatch):
    from cubeplex.config import config as cfg

    # sandbox.domain is a lazy `@format {env[CUBEPLEX_SANDBOX__DOMAIN]}` template,
    # so point it at the upstream via the env var (monkeypatch restores it).
    monkeypatch.setenv("CUBEPLEX_SANDBOX__DOMAIN", f"127.0.0.1:{upstream_port}")
    prev_url = cfg.get("api.public_url", "")
    prev_secret = cfg.get("auth.jwt_secret", "CHANGE_ME")
    cfg.set("api.public_url", "http://testserver")
    cfg.set("auth.jwt_secret", _SECRET)

    from cubeplex.api.routes import sandbox_panel

    app = FastAPI()
    app.include_router(sandbox_panel.router)
    with TestClient(app) as test_client:
        yield test_client

    cfg.set("api.public_url", prev_url)
    cfg.set("auth.jwt_secret", prev_secret)


def _token(sid: str = "sbx_1", port: int = 8080) -> str:
    return sign_panel_token(sandbox_id=sid, port=port, secret=_SECRET, ttl=timedelta(hours=1))


class TestPanelProxyHttp:
    def test_valid_token_proxies_to_pod(self, client: TestClient) -> None:
        r = client.get(f"/sandbox-panel/{_token('sbx_a', 7681)}/foo/bar?x=1")
        assert r.status_code == 200
        body = r.json()
        assert body == {"sid": "sbx_a", "port": 7681, "path": "foo/bar", "query": "x=1"}

    def test_root_path_proxies(self, client: TestClient) -> None:
        r = client.get(f"/sandbox-panel/{_token('sbx_b', 8080)}/")
        assert r.status_code == 200
        assert r.json()["path"] == ""

    def test_bad_token_forbidden(self, client: TestClient) -> None:
        r = client.get("/sandbox-panel/not-a-real-token/foo")
        assert r.status_code == 403

    def test_expired_token_forbidden(self, client: TestClient) -> None:
        expired = sign_panel_token(
            sandbox_id="sbx_x", port=8080, secret=_SECRET, ttl=timedelta(seconds=-5)
        )
        r = client.get(f"/sandbox-panel/{expired}/foo")
        assert r.status_code == 403


class TestPanelProxyWebSocket:
    def test_valid_token_relays_bidirectional(self, client: TestClient) -> None:
        with client.websocket_connect(f"/sandbox-panel/{_token('sbx_w', 7681)}/tty") as ws:
            assert ws.receive_text() == "hello:sbx_w:7681:tty"
            ws.send_text("ping")
            assert ws.receive_text() == "echo:ping"

    def test_bad_token_rejected(self, client: TestClient) -> None:
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/sandbox-panel/bad-token/tty"):
                pass
