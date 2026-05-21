"""OpenSandbox implementation of the Sandbox base class."""

import time

import opensandbox
from loguru import logger

from cubebox.sandbox.base import BrowserEndpoint, ExecuteResult, Sandbox


class OpenSandbox(Sandbox):
    """Sandbox backed by a remote OpenSandbox container."""

    def __init__(self, *, sandbox: opensandbox.Sandbox, workdir: str = "/workspace") -> None:
        self._sandbox = sandbox
        self._workdir = workdir

    @property
    def id(self) -> str:
        return self._sandbox.id

    @property
    def workdir(self) -> str:
        return self._workdir

    async def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResult:
        execution = await self._sandbox.commands.run(command)

        output_lines: list[str] = []
        for msg in execution.logs.stdout:
            output_lines.append(msg.text)
        for msg in execution.logs.stderr:
            output_lines.append(msg.text)
        output = "\n".join(output_lines) if output_lines else ""

        exit_code: int | None = None
        if execution.id:
            try:
                status = await self._sandbox.commands.get_command_status(execution.id)
                exit_code = status.exit_code
            except Exception as e:
                logger.warning("Could not get exit code for command: {}", e)

        return ExecuteResult(output=output, exit_code=exit_code)

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        for path, content in files:
            await self._sandbox.files.write_file(path, content)

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        result = []
        for path in paths:
            try:
                content = await self._sandbox.files.read_bytes(path)
            except Exception as exc:
                if "404" in str(exc):
                    raise FileNotFoundError(path) from exc
                raise
            result.append((path, content))
        return result

    async def get_browser_endpoint(self, *, expires_in: int = 3600) -> BrowserEndpoint:
        expires = int(time.time()) + expires_in
        endpoint = await self._sandbox.get_signed_endpoint(self.BROWSER_PORT, expires)
        url = endpoint.endpoint
        # OpenSandbox returns a scheme-less host/path; an iframe needs a full URL.
        if not url.startswith(("http://", "https://")):
            protocol = getattr(self._sandbox.connection_config, "protocol", "http")
            url = f"{protocol}://{url}"
        return BrowserEndpoint(url=url, headers=dict(endpoint.headers or {}))

    async def close(self) -> None:
        pass
