"""HTTP transport that records outbound requests to JSON files for diff analysis.

Used by Phase 2 diagnostic tests to capture the exact request body that each
runtime (langgraph / cubepi) sends to the provider.  Kept in the repo permanently
as part of the diagnostic scaffold.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime
from typing import Any

import httpx


class CapturingAsyncTransport(httpx.AsyncHTTPTransport):
    """Wraps a real httpx transport; writes each request body to JSON files.

    File naming: ``<label>_<counter:03d>.json``
    e.g. ``anthropic_001.json``, ``openai_002.json``

    Secrets (Authorization, x-api-key headers) are redacted in the output.
    """

    REDACT_HEADERS = frozenset({"authorization", "x-api-key"})

    def __init__(self, capture_dir: pathlib.Path, label: str, **kw: Any) -> None:
        super().__init__(**kw)
        self._capture_dir = capture_dir
        self._capture_dir.mkdir(parents=True, exist_ok=True)
        self._label = label
        self._counter = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._counter += 1
        body_bytes = request.content or b""
        try:
            body: Any = json.loads(body_bytes.decode("utf-8")) if body_bytes else None
        except Exception:
            body = {"_raw_hex": body_bytes.hex()}

        record: dict[str, Any] = {
            "label": self._label,
            "counter": self._counter,
            "timestamp": datetime.now(UTC).isoformat(),
            "method": request.method,
            "url": str(request.url),
            "headers": {
                k: ("[REDACTED]" if k.lower() in self.REDACT_HEADERS else v)
                for k, v in request.headers.items()
            },
            "body": body,
        }
        fname = f"{self._label}_{self._counter:03d}.json"
        (self._capture_dir / fname).write_text(
            json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False)
        )
        return await super().handle_async_request(request)
