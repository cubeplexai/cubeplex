"""OpenSandbox backend implementation for DeepAgents framework.

This module provides integration between OpenSandbox and the DeepAgents framework
by implementing the BaseSandbox protocol with native async support.
"""

import asyncio

import opensandbox
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox


class OpenSandbox(BaseSandbox):
    """OpenSandbox implementation conforming to SandboxBackendProtocol.

    This implementation wraps an OpenSandbox instance and provides the standard
    DeepAgents sandbox interface with native async support. It overrides aexecute()
    to use OpenSandbox's async API directly, avoiding event loop conflicts.

    Example:
        ```python
        # Create OpenSandbox instance
        sandbox = await opensandbox.Sandbox.create("python:3.11")

        # Wrap with DeepAgents backend
        backend = OpenSandbox(sandbox=sandbox)

        # Use with DeepAgents tools (sync)
        result = backend.execute("python --version")
        print(result.output)

        # Or use async version
        result = await backend.aexecute("python --version")
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
        # Use asyncer.syncify to handle event loop properly
        from asyncer import syncify

        sync_execute = syncify(self.aexecute, raise_sync_error=False)
        return sync_execute(command, timeout=timeout)

    async def als_info(self, path: str) -> list[dict[str, any]]:
        """List all files in a directory with metadata (async version).

        Args:
            path: Absolute path to the directory to list

        Returns:
            List of FileInfo dicts containing file metadata (only direct children)
        """
        try:
            import stat

            from opensandbox.models.filesystem import SearchEntry

            # Use search to list directory contents
            search_entry = SearchEntry(
                path=path,
                pattern="*",  # Match all files
                recursive=True,  # Need recursive to get all items
            )
            items = await self._sandbox.files.search(search_entry)

            # Filter to only direct children (one level deep)
            # Direct children have exactly one more path separator than the parent
            path_normalized = path.rstrip("/")
            parent_depth = path_normalized.count("/")

            file_infos = []
            seen_paths = set()

            for item in items:
                # Skip the directory itself
                if item.path == path or item.path == path_normalized:
                    continue

                # Get the direct child path (first level under parent)
                item_path = item.path
                if not item_path.startswith(path_normalized + "/"):
                    continue

                # Extract the direct child path
                relative_path = item_path[len(path_normalized) + 1:]  # Remove parent path + "/"
                first_component = relative_path.split("/")[0]
                direct_child_path = f"{path_normalized}/{first_component}"

                # Skip if we've already added this direct child
                if direct_child_path in seen_paths:
                    continue
                seen_paths.add(direct_child_path)

                # Check if it's a directory by seeing if there are items under it
                is_dir = "/" in relative_path  # If there's a slash, it's a directory

                file_info = {
                    "path": direct_child_path,
                    "is_dir": is_dir,
                }
                # Don't set size for directories
                if not is_dir and item.size is not None:
                    file_info["size"] = item.size
                file_infos.append(file_info)

            return file_infos
        except Exception as e:
            # Directory not found or other error
            from loguru import logger

            logger.warning("Failed to list directory {}: {}", path, e)
            return []

    def ls_info(self, path: str) -> list[dict[str, any]]:
        """List all files in a directory with metadata (sync wrapper).

        Args:
            path: Absolute path to the directory to list

        Returns:
            List of FileInfo dicts containing file metadata
        """
        # Use asyncer.syncify to handle event loop properly
        from asyncer import syncify

        sync_ls = syncify(self.als_info, raise_sync_error=False)
        return sync_ls(path)

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

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files from the sandbox (sync wrapper).

        Args:
            paths: List of file paths to download

        Returns:
            List of FileDownloadResponse objects, one per input path
        """
        # Use asyncer.syncify to handle event loop properly
        from asyncer import syncify

        sync_download = syncify(self.adownload_files, raise_sync_error=False)
        return sync_download(paths)

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
                # OpenSandbox's write_file supports bytes directly
                await self._sandbox.files.write_file(path, content)

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
        # Use asyncer.syncify to handle event loop properly
        from asyncer import syncify

        sync_upload = syncify(self.aupload_files, raise_sync_error=False)
        return sync_upload(files)

    async def sync_skills(self, files: list[tuple[str, bytes]]) -> None:
        """Sync skill files to the sandbox container.

        This method uploads skill files to the container and ensures parent directories
        are created. It's designed to be called after sandbox creation and before agent
        execution.

        Args:
            files: List of (container_path, content) tuples to upload.
                   Paths should be absolute (e.g., "/.skills/builtin/git-commit/SKILL.md")

        Example:
            ```python
            files = [
                ("/.skills/builtin/git-commit/SKILL.md", b"# Git Commit Skill..."),
                ("/.skills/builtin/code-review/SKILL.md", b"# Code Review..."),
            ]
            await sandbox.sync_skills(files)
            ```
        """
        if not files:
            return

        # Extract unique parent directories
        dirs = set()
        for path, _ in files:
            parent = path.rsplit("/", 1)[0]
            if parent:
                dirs.add(parent)

        # Create all parent directories
        if dirs:
            mkdir_cmd = "mkdir -p " + " ".join(f'"{d}"' for d in dirs)
            await self.aexecute(mkdir_cmd)

        # Upload all files
        responses = await self.aupload_files(files)

        # Log results
        failed = [r for r in responses if r.error]
        if failed:
            from loguru import logger

            logger.warning("Failed to sync {} skill files", len(failed))
            for resp in failed:
                logger.warning("  - {}: {}", resp.path, resp.error)
