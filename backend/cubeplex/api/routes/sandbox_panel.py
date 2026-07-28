"""Token-authenticated reverse proxy for sandbox browser/terminal panels.

The panel URL embeds a short-lived signed token in its path; this router verifies
the token and forwards HTTP and WebSocket traffic to the cluster-internal
opensandbox-server server-proxy (`/sandboxes/{id}/proxy/{port}`), which reaches
the sandbox Pod (k8s) or container (docker). It is the single panel gateway for
both runtimes — see docs/dev/specs/2026-07-26-sandbox-panel-proxy-design.md.

The route is intentionally public (no membership dependency): an iframe navigation
and its WebSocket/asset sub-resources cannot attach auth headers or a session
cookie to the backend origin, so the signed token in the URL path *is* the
credential. opensandbox-server itself does not authenticate the proxy route, so it
must never be exposed publicly — this backend is the only public entry.
"""

from __future__ import annotations

import asyncio

import httpx
import websockets
from fastapi import APIRouter, Request, Response, WebSocket
from fastapi.responses import StreamingResponse
from loguru import logger
from starlette.background import BackgroundTask
from starlette.status import HTTP_403_FORBIDDEN
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed
from websockets.typing import Subprotocol

from cubeplex.sandbox.panel_token import get_panel_secret, verify_panel_token

router = APIRouter()

# Connection-level headers that must not be relayed across a proxy hop.
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "transfer-encoding",
        "upgrade",
        "te",
        "trailer",
        "proxy-authorization",
        "proxy-authenticate",
        "host",
        "content-length",
    }
)

_WS_POLICY_VIOLATION = 1008
_WS_INTERNAL_ERROR = 1011


def _server_host() -> str:
    """host:port of the internal opensandbox-server (no scheme)."""
    from cubeplex.config import config

    domain = str(config.get("sandbox.domain", "localhost:8090")).strip()
    return domain.split("://", 1)[-1].rstrip("/")


def _upstream_path(sandbox_id: str, port: int, full_path: str) -> str:
    return f"/sandboxes/{sandbox_id}/proxy/{port}/{full_path.lstrip('/')}"


@router.api_route(
    "/sandbox-panel/{token}/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def panel_http(token: str, request: Request, full_path: str = "") -> Response:
    try:
        claims = verify_panel_token(token, secret=get_panel_secret())
    except ValueError:
        return Response(status_code=HTTP_403_FORBIDDEN, content=b"invalid or expired panel token")

    target = f"http://{_server_host()}{_upstream_path(claims.sandbox_id, claims.port, full_path)}"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    # Force identity encoding to prevent opensandbox-server double-compression:
    # ttyd (libwebsockets) compresses when gzip is accepted, then opensandbox's nginx
    # compresses again, producing a corrupt/truncated response. httpx also adds its own
    # accept-encoding by default, so we must override explicitly here.
    fwd_headers["accept-encoding"] = "identity"
    body = await request.body()

    # trust_env=False: opensandbox-server is an internal service — forwarding must
    # never be routed through an ambient HTTP_PROXY/ALL_PROXY.
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None), trust_env=False)
    try:
        upstream = await client.send(
            client.build_request(request.method, target, headers=fwd_headers, content=body),
            stream=True,
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        logger.warning("panel http upstream unreachable ({}): {}", target, exc)
        return Response(status_code=502, content=b"sandbox panel upstream unavailable")

    if upstream.status_code >= 400:
        # Consume the error body so httpx can release the connection, then return
        # a plain error response instead of streaming raw JSON from opensandbox-server
        # (e.g. {"code":"KUBERNETES::SANDBOX_NOT_FOUND",...}) into the browser iframe.
        body = await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        return Response(status_code=upstream.status_code, content=body)

    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}

    async def _close() -> None:
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=resp_headers,
        background=BackgroundTask(_close),
    )


@router.websocket("/sandbox-panel/{token}/{full_path:path}")
async def panel_ws(websocket: WebSocket, token: str, full_path: str = "") -> None:
    try:
        claims = verify_panel_token(token, secret=get_panel_secret())
    except ValueError:
        await websocket.close(code=_WS_POLICY_VIOLATION)
        return

    target = f"ws://{_server_host()}{_upstream_path(claims.sandbox_id, claims.port, full_path)}"
    if websocket.url.query:
        target = f"{target}?{websocket.url.query}"
    subprotocols = [Subprotocol(s) for s in (websocket.scope.get("subprotocols") or [])]

    try:
        upstream = await websockets.connect(
            target,
            subprotocols=subprotocols or None,
            open_timeout=15,
            max_size=None,
        )
    except Exception as exc:  # connect/handshake failure
        logger.warning("panel ws upstream connect failed ({}): {}", target, exc)
        await websocket.close(code=_WS_INTERNAL_ERROR)
        return

    await websocket.accept(subprotocol=upstream.subprotocol)
    await _relay(websocket, upstream)


async def _relay(ws: WebSocket, upstream: ClientConnection) -> None:
    async def client_to_upstream() -> None:
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                if (text := msg.get("text")) is not None:
                    await upstream.send(text)
                elif (data := msg.get("bytes")) is not None:
                    await upstream.send(data)
        except (WebSocketDisconnect, ConnectionClosed):
            return

    async def upstream_to_client() -> None:
        try:
            async for message in upstream:
                if isinstance(message, str):
                    await ws.send_text(message)
                else:
                    await ws.send_bytes(message)
        except ConnectionClosed:
            return

    tasks = {
        asyncio.create_task(client_to_upstream()),
        asyncio.create_task(upstream_to_client()),
    }
    _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await upstream.close()
    try:
        await ws.close()
    except RuntimeError:
        # already closed by the disconnect that ended the relay
        pass
