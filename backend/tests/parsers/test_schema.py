"""FileReadOutput discriminated union schema tests."""

import pytest
from pydantic import TypeAdapter, ValidationError

from cubeplex.parsers.schema import (
    ErrorOutput,
    FileReadOutput,
    NotebookCell,
    NotebookOutput,
    ParseOptions,
    TextOutput,
    UnchangedOutput,
    UnsupportedOutput,
)


def test_text_output_minimal() -> None:
    o = TextOutput(path="/tmp/a.txt", mime="text/plain", content="hi", size_bytes=2)
    assert o.kind == "text"
    assert o.truncated is False
    assert o.metadata == {}


def test_notebook_output_with_cells() -> None:
    o = NotebookOutput(
        path="/tmp/n.ipynb",
        cells=[NotebookCell(cell_type="code", source="print(1)", outputs=[{"text": "1"}])],
    )
    assert o.kind == "notebook"
    assert o.cells[0].cell_type == "code"


def test_unsupported_output() -> None:
    o = UnsupportedOutput(
        path="/tmp/v.mp4",
        mime="video/mp4",
        size_bytes=1024,
        reason="video file not supported",
    )
    assert o.kind == "unsupported"
    assert o.hint is None


def test_unchanged_output() -> None:
    o = UnchangedOutput(path="/tmp/a.txt")
    assert o.kind == "unchanged"


def test_error_output_default_not_retryable() -> None:
    o = ErrorOutput(path="/tmp/x", error="boom")
    assert o.retryable is False


def test_discriminated_union_parses_text() -> None:
    adapter = TypeAdapter(FileReadOutput)
    data = {"kind": "text", "path": "/p", "mime": "text/plain", "content": "x", "size_bytes": 1}
    result = adapter.validate_python(data)
    assert isinstance(result, TextOutput)


def test_discriminated_union_parses_unsupported() -> None:
    adapter = TypeAdapter(FileReadOutput)
    data = {
        "kind": "unsupported",
        "path": "/p",
        "mime": "video/mp4",
        "size_bytes": 1,
        "reason": "x",
    }
    result = adapter.validate_python(data)
    assert isinstance(result, UnsupportedOutput)


def test_discriminated_union_rejects_unknown_kind() -> None:
    adapter = TypeAdapter(FileReadOutput)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "weirdo", "path": "/p"})


def test_parse_options_default_empty() -> None:
    p = ParseOptions()
    assert p.page_range is None
    assert p.line_range is None
    assert p.language_hint is None


def test_parse_options_accepts_line_range() -> None:
    p = ParseOptions(line_range="100-200")
    assert p.line_range == "100-200"
    assert p.page_range is None


def test_parse_options_accepts_both_ranges() -> None:
    """Both range params can coexist; each plugin honors only what it cares about."""
    p = ParseOptions(page_range="1-5", line_range="100-200")
    assert p.page_range == "1-5"
    assert p.line_range == "100-200"
