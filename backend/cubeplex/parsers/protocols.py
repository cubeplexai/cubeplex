"""FileParser plugin Protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cubeplex.parsers.schema import FileReadOutput, ParseOptions


@runtime_checkable
class FileParser(Protocol):
    """A plugin that parses file bytes for a specific format family.

    mime_types: list of MIME patterns ("application/pdf", "text/*")
    extensions: list of extensions without leading dot ("pdf", "docx")
    priority:   within-family tie-breaker; higher wins.
    """

    mime_types: list[str]
    extensions: list[str]
    priority: int

    async def parse(
        self,
        content: bytes,
        *,
        mime: str,
        options: ParseOptions,
    ) -> FileReadOutput: ...
