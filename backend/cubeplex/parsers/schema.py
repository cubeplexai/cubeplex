"""Discriminated union of FileReadOutput kinds returned by file_read."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class TextOutput(BaseModel):
    kind: Literal["text"] = "text"
    path: str
    mime: str
    content: str
    size_bytes: int
    truncated: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class NotebookCell(BaseModel):
    cell_type: Literal["code", "markdown", "raw"]
    source: str
    outputs: list[dict[str, Any]] | None = None


class NotebookOutput(BaseModel):
    kind: Literal["notebook"] = "notebook"
    path: str
    cells: list[NotebookCell]
    metadata: dict[str, Any] = Field(default_factory=dict)


class UnsupportedOutput(BaseModel):
    kind: Literal["unsupported"] = "unsupported"
    path: str
    mime: str
    size_bytes: int
    reason: str
    hint: str | None = None


class UnchangedOutput(BaseModel):
    kind: Literal["unchanged"] = "unchanged"
    path: str


class ErrorOutput(BaseModel):
    kind: Literal["error"] = "error"
    path: str
    error: str
    retryable: bool = False


FileReadOutput = Annotated[
    TextOutput | NotebookOutput | UnsupportedOutput | UnchangedOutput | ErrorOutput,
    Field(discriminator="kind"),
]


class ParseOptions(BaseModel):
    page_range: str | None = None  # PDF/DOCX/PPTX use this; "1-5" or "3"
    line_range: str | None = None  # text/code/log use this; "100-200" or "42"
    language_hint: str | None = None
