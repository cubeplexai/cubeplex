"""Sandbox base class — async-first interface for code execution environments."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


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
