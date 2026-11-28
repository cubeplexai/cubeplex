"""Minimal OpenAI-compatible SSE stub for local E2E runs.

Lets the ``@real-llm`` Playwright specs run without provider credentials: point
the backend's test-env provider at it and every completion replies with a fixed
string.

    python scripts/fake_openai_sse.py --port 8099 &
    CUBEPLEX_E2E_LLM_BASE_URL=http://127.0.0.1:8099/v1 \
    CUBEPLEX_E2E_REAL_LLM=true npx playwright test --grep @real-llm

Not for any deployed environment.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLY = "pong from the stub model"


def _chunk(delta: dict[str, str], finish: str | None = None) -> str:
    payload = {
        "id": "chatcmpl-stub",
        "object": "chat.completion.chunk",
        "created": int(datetime.now(UTC).timestamp()),
        "model": "stub",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)

        body = "".join(
            [
                _chunk({"role": "assistant", "content": ""}),
                _chunk({"content": REPLY}),
                _chunk({}, finish="stop"),
                "data: [DONE]\n\n",
            ]
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
