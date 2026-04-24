"""Sandbox base class — async-first interface for code execution environments."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from cubebox.parsers import FileReadOutput, ParseOptions


@dataclass
class ExecuteResult:
    """Result of a shell command execution."""

    output: str
    exit_code: int | None = None


class Sandbox(ABC):
    """Async-first sandbox base class.

    Agent-facing: only `execute` is registered as a tool.
    Infrastructure-facing: `upload`/`download` for binary file transfer
    (used by API endpoints, SandboxManager, skills sync — NOT agent tools).
    """

    @property
    @abstractmethod
    def id(self) -> str:
        """Unique identifier for this sandbox instance."""
        ...

    @property
    @abstractmethod
    def workdir(self) -> str:
        """Working directory for command execution."""
        ...

    @abstractmethod
    async def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResult:
        """Execute a shell command. Returns combined stdout+stderr and exit code."""
        ...

    @abstractmethod
    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        """Upload files into the sandbox. Each tuple is (absolute_path, content)."""
        ...

    @abstractmethod
    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        """Download files from the sandbox. Returns list of (path, content) tuples."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release sandbox resources."""
        ...

    async def file_read(
        self,
        path: str,
        *,
        options: ParseOptions | None = None,
        conversation_id: UUID | None = None,
    ) -> FileReadOutput:
        """Read and parse a file at ``path`` inside the sandbox.

        Default impl: download bytes via ``self.download`` + dispatch through the
        parser registry. Subclasses may override if they can do parsing natively.
        """
        from cubebox.parsers import ParseOptions as _ParseOptions
        from cubebox.parsers import get_parser_registry

        return await get_parser_registry().dispatch(
            sandbox=self,
            path=path,
            options=options or _ParseOptions(),
            conversation_id=conversation_id,
        )
