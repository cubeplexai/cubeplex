"""Reject new run starts with 503 while the process is draining.

This middleware is registered last in ``create_app`` so it runs as the
outermost wrapper on the request path: a draining server should refuse new
runs before doing CSRF or identity work.

Only the run-start surface is gated. SSE subscription, bootstrap, auth,
and health probes pass through unchanged so existing clients keep working
during drain.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Receive, Scope, Send

from cubeplex.lifecycle.drain import DrainState

_RETRY_AFTER_SECONDS = "5"
_BLOCKED_BODY = json.dumps(
    {"error": {"code": "draining", "message": "Server is draining; retry."}}
).encode()


def _is_run_start(scope: Scope) -> bool:
    """Match POST /api/v1/ws/{ws}/conversations/{cid}/messages exactly."""
    if scope.get("method") != "POST":
        return False
    path = scope.get("path", "")
    if not path.startswith("/api/v1/ws/"):
        return False
    segments = path.strip("/").split("/")
    # ['api', 'v1', 'ws', '{ws}', 'conversations', '{cid}', 'messages']
    return len(segments) == 7 and segments[4] == "conversations" and segments[6] == "messages"


class DrainMiddleware:
    def __init__(self, app: ASGIApp, *, drain_state: DrainState) -> None:
        self.app = app
        self._state = drain_state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._state.is_draining():
            await self.app(scope, receive, send)
            return

        if not _is_run_start(scope):
            await self.app(scope, receive, send)
            return

        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"retry-after", _RETRY_AFTER_SECONDS.encode()),
                    (b"connection", b"close"),
                    (b"content-type", b"application/json"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _BLOCKED_BODY})
