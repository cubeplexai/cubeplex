"""OpenSandbox backend implementation for DeepAgents framework.

This module provides integration between OpenSandbox and the DeepAgents framework
by implementing the BaseSandbox protocol.
"""

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any

import opensandbox
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox


def _run_async_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run async coroutine in sync context by creating a new event loop in a thread.

    This is used when the backend is called from sync code but needs to run async operations.
    It always creates a new thread with its own event loop to avoid conflicts.

    Args:
        coro: Coroutine to run

    Returns:
        Result of the coroutine
    """
    result: T | None = None
    exception: Exception | None = None

    def run_in_thread() -> None:
        nonlocal result, exception
        try:
            result = asyncio.run(coro)
        except Exception as e:
            exception = e

    thread = threading.Thread(target=run_in_thread)
    thread.start()
    thread.join()

    if exception:
        raise exception
    return result  # type: ignore[return-value]


class OpenSandbox(BaseSandbox):
    """OpenSandbox implementation conforming to SandboxBackendProtocol.

    This implementation wraps an OpenSandbox instance and provides the standard
    DeepAgents sandbox interface. It inherits file operation methods from BaseSandbox
    and implements execute(), upload_files(), and download_files() using OpenSandbox's API.

    Example:
        ```python
        # Create OpenSandbox instance
        sandbox = await opensandbox.Sandbox.create("python:3.11")

        # Wrap with DeepAgents backend
        backend = OpenSandbox(sandbox=sandbox)

        # Use with DeepAgents tools
        result = backend.execute("python --version")
        print(result.output)
        ```
    """

    def __init__(self, *, sandbox: opensandbox.Sandbox) -> None:
        """Create a backend wrapping an existing OpenSandbox instance.

        Args:
            sandbox: An initialized OpenSandbox.Sandbox instance
        """
        self._sandbox = sandbox
        self._timeout: int = 30 * 60  # 30 minutes default timeout

    @property
    def id(self) -> str:
        """Return the OpenSandbox sandbox id."""
        return self._sandbox.id

    async def execute_async(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Execute a shell command inside the sandbox (async version).

        Args:
            command: Shell command string to execute
            timeout: Maximum time in seconds to wait for command completion (unused for now)

        Returns:
            ExecuteResponse with combined output, exit code, and truncation flag
        """
        # Run async command
        execution = await self._sandbox.commands.run(command)

        # Combine stdout and stderr into single output
        output_lines: list[str] = []

        # Add stdout messages
        for msg in execution.logs.stdout:
            output_lines.append(msg.text)

        # Add stderr messages
        for msg in execution.logs.stderr:
            output_lines.append(msg.text)

        combined_output = "".join(output_lines)

        # Get exit code from command status
        exit_code: int | None = None
        if execution.id:
            status = await self._sandbox.commands.get_command_status(execution.id)
            exit_code = status.exit_code

        return ExecuteResponse(
            output=combined_output,
            exit_code=exit_code,
            truncated=False,
        )

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Execute a shell command inside the sandbox (sync wrapper).

        Args:
            command: Shell command string to execute
            timeout: Maximum time in seconds to wait for command completion (unused for now)

        Returns:
            ExecuteResponse with combined output, exit code, and truncation flag
        """
        return _run_async_sync(self.execute_async(command, timeout=timeout))

    async def download_files_async(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files from the sandbox (async version).

        Args:
            paths: List of file paths to download

        Returns:
            List of FileDownloadResponse objects, one per input path
        """
        responses: list[FileDownloadResponse] = []

        for path in paths:
            # Validate path format
            if not path.startswith("/"):
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="invalid_path")
                )
                continue

            try:
                # Use OpenSandbox's file API to read file
                content_str = await self._sandbox.files.read_file(path)
                content_bytes = content_str.encode("utf-8")

                responses.append(FileDownloadResponse(path=path, content=content_bytes, error=None))
            except Exception:
                # File not found or other error
                responses.append(
                    FileDownloadResponse(
                        path=path,
                        content=None,
                        error="file_not_found",
                    )
                )

        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files from the sandbox (sync wrapper).

        Args:
            paths: List of file paths to download

        Returns:
            List of FileDownloadResponse objects, one per input path
        """
        return _run_async_sync(self.download_files_async(paths))

    async def upload_files_async(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload files into the sandbox (async version).

        Args:
            files: List of (path, content) tuples to upload

        Returns:
            List of FileUploadResponse objects, one per input file
        """
        responses: list[FileUploadResponse] = []

        for path, content in files:
            # Validate path format
            if not path.startswith("/"):
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
                continue

            try:
                # Decode bytes to string for OpenSandbox API
                content_str = content.decode("utf-8")

                # Use OpenSandbox's file API to write file
                await self._sandbox.files.write_file(path, content_str)

                responses.append(FileUploadResponse(path=path, error=None))
            except Exception:
                # Write failed
                responses.append(FileUploadResponse(path=path, error="permission_denied"))

        return responses

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload files into the sandbox (sync wrapper).

        Args:
            files: List of (path, content) tuples to upload

        Returns:
            List of FileUploadResponse objects, one per input file
        """
        return _run_async_sync(self.upload_files_async(files))
