"""Reference MCP-like HTTP server for MCP connector E2E tests."""

import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass

import pytest


@dataclass
class ReferenceMCPServer:
    base_url: str
    process: subprocess.Popen[bytes]


def _alloc_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _server_script(auth_mode: str, jwt_secret: str | None, static_token: str | None) -> str:
    return textwrap.dedent(
        f"""
        import json
        import sys

        import jwt
        import uvicorn
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response
        from starlette.routing import Route

        AUTH_MODE = {auth_mode!r}
        JWT_SECRET = {jwt_secret!r}
        STATIC_TOKEN = {static_token!r}

        TOOLS = [
            {{
                "name": "echo",
                "description": "echoes input",
                "inputSchema": {{
                    "type": "object",
                    "properties": {{"text": {{"type": "string"}}}},
                    "required": ["text"],
                }},
            }},
            {{
                "name": "ping",
                "description": "responds pong",
                "inputSchema": {{"type": "object", "properties": {{}}}},
            }},
        ]


        def _verify_auth(request: Request) -> tuple[bool, str]:
            if AUTH_MODE == "none":
                return True, ""
            header = request.headers.get("authorization", "")
            if not header.startswith("Bearer "):
                return False, "missing bearer"
            token = header[len("Bearer "):]
            if AUTH_MODE == "bearer-static":
                return token == STATIC_TOKEN, ""
            if AUTH_MODE == "bearer-jwt-verify":
                try:
                    claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
                except Exception as exc:
                    return False, f"jwt: {{exc}}"
                if claims.get("iss") != "cubeplex":
                    return False, f"bad issuer: {{claims.get('iss')}}"
                return True, json.dumps(claims)
            return False, f"unknown auth mode: {{AUTH_MODE}}"


        async def list_tools(request: Request) -> Response:
            ok, error = _verify_auth(request)
            if not ok:
                return Response(error, status_code=401)
            return JSONResponse({{"tools": TOOLS}})


        async def call_tool(request: Request) -> Response:
            ok, error = _verify_auth(request)
            if not ok:
                return Response(error, status_code=401)
            body = await request.json()
            name = body.get("name")
            args = body.get("arguments", {{}})
            if name == "echo":
                text = args.get("text", "")
                return JSONResponse({{"content": [{{"type": "text", "text": text}}]}})
            if name == "ping":
                return JSONResponse({{"content": [{{"type": "text", "text": "pong"}}]}})
            return JSONResponse({{"error": f"unknown tool {{name}}"}}, status_code=404)


        app = Starlette(routes=[
            Route("/mcp/tools/list", list_tools, methods=["GET", "POST"]),
            Route("/mcp/tools/call", call_tool, methods=["POST"]),
        ])

        if __name__ == "__main__":
            uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
        """
    )


def _wait_for_port(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("reference MCP server exited before accepting connections")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("reference MCP server failed to start")


@pytest.fixture
def reference_mcp_server() -> Iterator[Callable[..., AbstractContextManager[ReferenceMCPServer]]]:
    """Yield a factory that spawns a reference MCP-like server subprocess."""
    spawned: list[ReferenceMCPServer] = []

    @contextmanager
    def _spawn(
        auth_mode: str = "none",
        *,
        jwt_secret: str | None = None,
        static_token: str | None = None,
    ) -> Iterator[ReferenceMCPServer]:
        port = _alloc_port()
        script = _server_script(auth_mode, jwt_secret, static_token)
        temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
        try:
            temp_file.write(script)
            temp_file.flush()
            temp_file.close()
            process = subprocess.Popen(
                [sys.executable, temp_file.name, str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            server = ReferenceMCPServer(
                base_url=f"http://127.0.0.1:{port}",
                process=process,
            )
            spawned.append(server)
            _wait_for_port(port, process)
            yield server
        finally:
            if "process" in locals() and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
            os.unlink(temp_file.name)

    yield _spawn

    for server in spawned:
        if server.process.poll() is None:
            server.process.kill()
