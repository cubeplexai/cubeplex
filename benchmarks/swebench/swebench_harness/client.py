"""Cubebox HTTP client + SSE parser.

Single Bearer token authenticates every call (see `feat/2026-06-23-api-key`,
PR #270). The SSE format is documented in the harness-benchmarks design
doc: each line is either `id: …` or `data: <json>`, and the event type
lives inside the JSON `type` field (no `event:` SSE header).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests


@dataclass(slots=True)
class CubeboxConfig:
    base_url: str
    token: str
    workspace_id: str

    @property
    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


class CubeboxAPIError(RuntimeError):
    """Raised when cubebox returns a non-2xx response."""

    def __init__(self, status: int, body: str, *, method: str, url: str) -> None:
        super().__init__(f"{method} {url} -> {status}: {body[:300]}")
        self.status = status
        self.body = body


class CubeboxClient:
    def __init__(self, cfg: CubeboxConfig, *, request_timeout: float = 60.0) -> None:
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update(cfg.auth_headers)
        self.request_timeout = request_timeout

    # ------------------------------------------------------------------
    # Account / sanity

    def whoami(self) -> dict[str, Any]:
        r = self.session.get(
            f"{self.cfg.base_url}/api/v1/auth/me", timeout=self.request_timeout
        )
        self._raise_for_status(r, method="GET", url="/api/v1/auth/me")
        return r.json()

    # ------------------------------------------------------------------
    # Conversation lifecycle

    def create_conversation(self, *, title: str) -> str:
        url = f"/api/v1/ws/{self.cfg.workspace_id}/conversations"
        r = self.session.post(
            self.cfg.base_url + url,
            json={"title": title},
            timeout=self.request_timeout,
        )
        self._raise_for_status(r, method="POST", url=url)
        return str(r.json()["id"])

    def delete_conversation(self, conversation_id: str) -> None:
        url = f"/api/v1/ws/{self.cfg.workspace_id}/conversations/{conversation_id}"
        r = self.session.delete(self.cfg.base_url + url, timeout=self.request_timeout)
        # 204 or 404 are both acceptable — cleanup is best-effort.
        if r.status_code not in (204, 404):
            self._raise_for_status(r, method="DELETE", url=url)

    # ------------------------------------------------------------------
    # Streaming a turn

    def send_message_sse(
        self,
        conversation_id: str,
        *,
        content: str,
        model_key: str | None = None,
        thinking: str = "off",
        stream_idle_timeout: float = 600.0,
    ) -> Iterator[dict[str, Any]]:
        """POST a user message, yield parsed SSE events until `done` or `error`.

        ``stream_idle_timeout`` is passed to requests as its ``timeout``
        when streaming, which behaves as an idle/read-timeout: if no
        bytes arrive on the socket for this many seconds, requests raises
        ``ReadTimeout``. The caller (runner.py) catches that and records
        the task as a failure, so a stalled model upstream can't wedge
        a whole sweep. Default 10 minutes is well above the longest
        legitimate quiet period (a `pip install` resolving deps).
        """
        url = f"/api/v1/ws/{self.cfg.workspace_id}/conversations/{conversation_id}/messages"
        body: dict[str, Any] = {"content": content, "thinking": thinking}
        if model_key is not None:
            body["model_key"] = model_key
        with self.session.post(
            self.cfg.base_url + url,
            json=body,
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=stream_idle_timeout,
        ) as r:
            if r.status_code >= 400:
                # Drain body so we can surface a useful error.
                body_text = r.text
                raise CubeboxAPIError(r.status_code, body_text, method="POST", url=url)
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if not line.startswith("data: "):
                    # SSE `id:` lines are opaque to us; skip.
                    continue
                payload = line[len("data: ") :]
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                yield event
                evt_type = event.get("type")
                if evt_type in ("done", "error"):
                    return

    # ------------------------------------------------------------------
    # Sandbox file I/O

    def download_file(
        self, *, path: str, conversation_id: str
    ) -> bytes:
        """Pull a file from the sandbox by absolute path.

        Returns the raw bytes. Raises `CubeboxAPIError(status=404)` if the
        file does not exist (typical "agent never wrote patch.diff" case).
        """
        url = (
            f"/api/v1/ws/{self.cfg.workspace_id}/sandbox/files/download"
            f"?path={quote(path, safe='')}"
            f"&conversation_id={quote(conversation_id, safe='')}"
        )
        r = self.session.get(self.cfg.base_url + url, timeout=self.request_timeout)
        self._raise_for_status(r, method="GET", url=url)
        return r.content

    def list_files(self, *, path: str, conversation_id: str) -> list[dict[str, Any]]:
        url = (
            f"/api/v1/ws/{self.cfg.workspace_id}/sandbox/files"
            f"?path={quote(path, safe='')}"
            f"&conversation_id={quote(conversation_id, safe='')}"
        )
        r = self.session.get(self.cfg.base_url + url, timeout=self.request_timeout)
        self._raise_for_status(r, method="GET", url=url)
        data = r.json()
        # Defensive — endpoint shape may evolve.
        if isinstance(data, list):
            return data
        return list(data.get("entries", []))

    # ------------------------------------------------------------------
    # Helpers

    @staticmethod
    def _raise_for_status(r: requests.Response, *, method: str, url: str) -> None:
        if r.status_code >= 400:
            raise CubeboxAPIError(r.status_code, r.text, method=method, url=url)
