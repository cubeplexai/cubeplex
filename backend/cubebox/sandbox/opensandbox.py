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
    """Run async coroutine in sync context.

    This function handles two scenarios:
    1. If called from an async context (event loop running), it schedules the coroutine
       in a new thread with its own event loop to avoid conflicts.
    2. If called from a sync context (no event loop), it runs the coroutine directly.

    Args:
        coro: Coroutine to run

    Returns:
        Result of the coroutine
    """
    try:
        # Check if we're in an async context
        asyncio.get_running_loop()
        # We're in an async context, need to run in a separate thread
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
    except RuntimeError:
        # No event loop running, we can run directly
        return asyncio.run(coro)


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

    async def aexecute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Execute a shell command inside the sandbox (async version).

        This overrides the BaseSandbox.aexecute to use native async instead of
        wrapping the sync execute() method with asyncio.to_thread.

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

    async def execute_async(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Alias for aexecute for backward compatibility."""
        return await self.aexecute(command, timeout=timeout)

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Execute a shell command inside the sandbox (sync wrapper).

        Note: This method should not be called directly when already in an async context.
        The DeepAgents protocol will automatically use aexecute() instead.

        Args:
            command: Shell command string to execute
            timeout: Maximum time in seconds to wait for command completion (unused for now)

        Returns:
            ExecuteResponse with combined output, exit code, and truncation flag
        """
        # This should not be called from async context - DeepAgents uses aexecute() instead
        return _run_async_sync(self.aexecute(command, timeout=timeout))

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files from the sandbox (async version).

        This overrides BaseSandbox.adownload_files to use native async.

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

    async def download_files_async(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Alias for adownload_files for backward compatibility."""
        return await self.adownload_files(paths)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files from the sandbox (sync wrapper).

        Args:
            paths: List of file paths to download

        Returns:
            List of FileDownloadResponse objects, one per input path
        """
        return _run_async_sync(self.adownload_files(paths))

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload files into the sandbox (async version).

        This overrides BaseSandbox.aupload_files to use native async.

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

    async def upload_files_async(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Alias for aupload_files for backward compatibility."""
        return await self.aupload_files(files)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload files into the sandbox (sync wrapper).

        Args:
            files: List of (path, content) tuples to upload

        Returns:
            List of FileUploadResponse objects, one per input file
        """
        return _run_async_sync(self.aupload_files(files))

    # Override BaseSandbox methods to use native async instead of to_thread wrappers

    async def aread(self, file_path: str, offset: int = 0, limit: int = -1) -> str:
        """Read file content from sandbox (async, native implementation).

        This overrides BaseSandbox.aread to avoid the asyncio.to_thread wrapper
        that causes event loop conflicts.

        Args:
            file_path: Path to file in sandbox
            offset: Starting byte offset (not used by opensandbox, for protocol compatibility)
            limit: Maximum bytes to read (not used by opensandbox, for protocol compatibility)

        Returns:
            File content as string
        """
        # OpenSandbox doesn't support offset/limit, so we read the full file
        # If offset/limit are needed, we'd need to implement slicing here
        return await self._sandbox.files.read_file(file_path)

    async def awrite(self, file_path: str, content: str) -> Any:
        """Write content to file in sandbox (async, native implementation).

        This overrides BaseSandbox.awrite to avoid the asyncio.to_thread wrapper
        that causes event loop conflicts.

        Args:
            file_path: Path to file in sandbox
            content: Content to write

        Returns:
            Write result from BaseSandbox protocol
        """
        # Import here to avoid circular dependency
        from deepagents.backends.protocol import WriteResult

        await self._sandbox.files.write_file(file_path, content)
        return WriteResult(path=file_path)
