"""OpenSandbox implementation of the Sandbox base class."""

import opensandbox
from loguru import logger

from cubebox.sandbox.base import ExecuteResult, Sandbox


class OpenSandbox(Sandbox):
    """Sandbox backed by a remote OpenSandbox container."""

    def __init__(self, *, sandbox: opensandbox.Sandbox) -> None:
        self._sandbox = sandbox

    @property
    def id(self) -> str:
        return self._sandbox.id

    async def execute(
        self, command: str, *, timeout: int | None = None
    ) -> ExecuteResult:
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
            content_str = await self._sandbox.files.read_file(path)
            result.append((path, content_str.encode("utf-8")))
        return result

    async def close(self) -> None:
        pass
