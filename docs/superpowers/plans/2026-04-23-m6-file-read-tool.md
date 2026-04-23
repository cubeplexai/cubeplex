# M6 · file_read 通用工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an agent-facing `file_read` tool that reads files from the sandbox and returns LLM-ready content (markdown / structured cells / unsupported sentinel / unchanged sentinel / error). Parser implementations live in a backend-side `cubebox.parsers` plugin registry; the default Docling parser delegates to an external `docling-serve` HTTP service so heavy ML deps stay out of backend.

**Architecture:** New `cubebox.parsers` package owns the `FileParser` Protocol + entry_points-based plugin registry + 3 default plugins (`TextParser`, `NotebookParser`, `DoclingParser`). `Sandbox` abstract base class gains a non-abstract `file_read(path, options)` method that downloads bytes + dispatches via the parser registry + applies conversation-scoped SHA-256 dedup. `SandboxMiddleware` registers a new `file_read` agent tool that calls `sandbox.file_read(...)`.

**Tech Stack:** Python 3.12, FastAPI, httpx (for docling-serve client), python-magic (libmagic wrapper), filetype (libmagic-free fallback), structlog, Pydantic, pytest, pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-04-22-file-read-tool-design.md`

---

## File Structure

### Create

```
backend/cubebox/parsers/
├─ __init__.py                  # Re-exports
├─ schema.py                    # FileReadOutput union + ParseOptions
├─ protocols.py                 # FileParser Protocol
├─ mime.py                      # libmagic + extension fallback + REJECT lists
├─ dedup.py                     # asyncio.to_thread hash cache
├─ registry.py                  # ParserRegistry (discover + dispatch)
└─ plugins/
   ├─ __init__.py
   ├─ text.py                   # TextParser (UTF-8 decode + truncation)
   ├─ notebook.py               # NotebookParser (Jupyter cells)
   └─ docling.py                # DoclingParser (HTTP to docling-serve)

backend/tests/parsers/
├─ __init__.py
├─ conftest.py                  # parser registry fixtures + mock docling
├─ test_schema.py
├─ test_mime.py
├─ test_dedup.py
├─ test_text_parser.py
├─ test_notebook_parser.py
├─ test_docling_parser.py       # httpx mock server
├─ test_registry.py
└─ test_sandbox_file_read.py    # integration with Sandbox.file_read
```

### Modify

```
backend/cubebox/sandbox/base.py            # Add Sandbox.file_read non-abstract method
backend/cubebox/middleware/sandbox.py      # Register file_read tool with description
backend/cubebox/config.py                  # Add parsers.docling_serve schema
backend/config.yaml                        # Add parsers: section
backend/config.development.yaml            # Add parsers: section
backend/config.test.yaml                   # Add parsers: section
backend/pyproject.toml                     # Deps + entry_points group
docker-compose.yml                         # Add docling-serve service
```

---

## Tasks

### Task 1: Create `cubebox.parsers` package skeleton

**Files:**
- Create: `backend/cubebox/parsers/__init__.py`
- Create: `backend/cubebox/parsers/plugins/__init__.py`
- Create: `backend/tests/parsers/__init__.py`

- [ ] **Step 1: Create directories + empty __init__.py files**

```bash
mkdir -p backend/cubebox/parsers/plugins
mkdir -p backend/tests/parsers
touch backend/cubebox/parsers/plugins/__init__.py
touch backend/tests/parsers/__init__.py
```

- [ ] **Step 2: Write package docstring**

```python
# backend/cubebox/parsers/__init__.py
"""File parser plugin registry shared by file_read tool and future filebox.

See docs/superpowers/specs/2026-04-22-file-read-tool-design.md.
"""
```

- [ ] **Step 3: Verify import**

Run: `cd backend && uv run python -c "import cubebox.parsers"`
Expected: no error.

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/parsers/ backend/tests/parsers/
git commit -m "chore(parsers): create package skeleton for M6 file_read"
```

---

### Task 2: Define `FileReadOutput` discriminated union + supporting types

**Files:**
- Create: `backend/cubebox/parsers/schema.py`
- Create: `backend/tests/parsers/test_schema.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/parsers/test_schema.py
"""FileReadOutput discriminated union schema tests."""

import pytest
from pydantic import TypeAdapter, ValidationError

from cubebox.parsers.schema import (
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
        path="/tmp/v.mp4", mime="video/mp4", size_bytes=1024,
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
    data = {"kind": "unsupported", "path": "/p", "mime": "video/mp4", "size_bytes": 1, "reason": "x"}
    result = adapter.validate_python(data)
    assert isinstance(result, UnsupportedOutput)


def test_discriminated_union_rejects_unknown_kind() -> None:
    adapter = TypeAdapter(FileReadOutput)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "weirdo", "path": "/p"})


def test_parse_options_default_empty() -> None:
    p = ParseOptions()
    assert p.page_range is None
    assert p.language_hint is None
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/parsers/test_schema.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement schema.py**

```python
# backend/cubebox/parsers/schema.py
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
    page_range: str | None = None  # "1-5" or "3"
    language_hint: str | None = None
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/parsers/test_schema.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/parsers/schema.py backend/tests/parsers/test_schema.py
git commit -m "feat(parsers): FileReadOutput discriminated union + ParseOptions"
```

---

### Task 3: Define `FileParser` Protocol

**Files:**
- Create: `backend/cubebox/parsers/protocols.py`
- Create: `backend/tests/parsers/test_protocols.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/parsers/test_protocols.py
from cubebox.parsers.protocols import FileParser
from cubebox.parsers.schema import ParseOptions, TextOutput


class _ConformingParser:
    mime_types = ["text/plain"]
    extensions = ["txt"]
    priority = 0

    async def parse(self, content, *, mime, options):  # type: ignore[no-untyped-def]
        return TextOutput(path="/tmp/x", mime=mime, content="x", size_bytes=len(content))


class _MissingParse:
    mime_types: list[str] = []
    extensions: list[str] = []
    priority = 0


def test_protocol_accepts_conforming() -> None:
    assert isinstance(_ConformingParser(), FileParser)


def test_protocol_rejects_missing_parse() -> None:
    assert not isinstance(_MissingParse(), FileParser)
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/parsers/test_protocols.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement protocols.py**

```python
# backend/cubebox/parsers/protocols.py
"""FileParser plugin Protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cubebox.parsers.schema import FileReadOutput, ParseOptions


@runtime_checkable
class FileParser(Protocol):
    """A plugin that parses file bytes for a specific format family.

    mime_types: list of MIME patterns ("application/pdf", "text/*")
    extensions: list of extensions without leading dot ("pdf", "docx")
    priority:   within-family tie-breaker; higher wins
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
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/parsers/test_protocols.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/parsers/protocols.py backend/tests/parsers/test_protocols.py
git commit -m "feat(parsers): FileParser runtime_checkable Protocol"
```

---

### Task 4: MIME sniff + REJECT lists

**Files:**
- Create: `backend/cubebox/parsers/mime.py`
- Create: `backend/tests/parsers/test_mime.py`
- Modify: `backend/pyproject.toml` (add `python-magic` + `filetype` deps)

- [ ] **Step 1: Add deps**

```bash
cd backend && uv add python-magic filetype
```

NOTE: `python-magic` requires libmagic shared library on the system. macOS: `brew install libmagic`. Debian: `apt install libmagic1`. Alpine: `apk add libmagic`. CI image: ensure libmagic is installed.

- [ ] **Step 2: Write failing tests**

```python
# backend/tests/parsers/test_mime.py
from cubebox.parsers.mime import (
    REJECT_EXT,
    REJECT_MIME,
    is_rejected,
    sniff_mime,
)


def test_reject_ext_includes_video() -> None:
    assert "mp4" in REJECT_EXT
    assert "mov" in REJECT_EXT


def test_reject_ext_includes_audio() -> None:
    assert "mp3" in REJECT_EXT
    assert "wav" in REJECT_EXT


def test_reject_ext_includes_archives() -> None:
    assert "zip" in REJECT_EXT
    assert "tar" in REJECT_EXT


def test_reject_ext_includes_executables() -> None:
    assert "exe" in REJECT_EXT
    assert "so" in REJECT_EXT


def test_is_rejected_true_for_video_ext() -> None:
    assert is_rejected("/tmp/foo.mp4", "video/mp4") is True


def test_is_rejected_false_for_text() -> None:
    assert is_rejected("/tmp/foo.txt", "text/plain") is False


def test_sniff_mime_detects_pdf_from_bytes() -> None:
    pdf_magic = b"%PDF-1.4\n"
    mime = sniff_mime("/tmp/a.pdf", pdf_magic + b"x" * 100)
    assert mime == "application/pdf"


def test_sniff_mime_falls_back_to_extension() -> None:
    # ASCII content with .py extension
    mime = sniff_mime("/tmp/x.py", b"print('hi')\n")
    # libmagic detects as text/x-python or text/plain; either acceptable
    assert mime.startswith("text/") or mime == "application/x-python"
```

- [ ] **Step 3: Run, verify fail**

Run: `cd backend && uv run pytest tests/parsers/test_mime.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement mime.py**

```python
# backend/cubebox/parsers/mime.py
"""MIME sniffing + REJECT lists for file_read pre-screening."""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path

import filetype
import magic

REJECT_EXT: set[str] = {
    # video
    "mp4", "mov", "mkv", "webm", "avi", "flv", "wmv", "m4v",
    # audio
    "mp3", "wav", "m4a", "ogg", "flac", "opus", "aac", "wma",
    # binary / executable
    "exe", "so", "dll", "dylib", "o", "a", "bin", "com",
    # archive
    "zip", "tar", "gz", "bz2", "rar", "7z", "tgz", "xz", "zst",
}

REJECT_MIME: set[str] = {
    "video/mp4", "video/quicktime", "video/x-matroska", "video/webm",
    "video/x-msvideo", "video/x-flv", "video/x-ms-wmv",
    "audio/mpeg", "audio/wav", "audio/x-m4a", "audio/ogg", "audio/flac",
    "application/x-executable", "application/x-shared-library",
    "application/zip", "application/x-tar", "application/gzip",
    "application/x-bzip2", "application/x-rar-compressed", "application/x-7z-compressed",
    "application/x-xz", "application/zstd",
}


def sniff_mime(path: str, content: bytes) -> str:
    """Detect MIME via libmagic; fall back to filetype lib then extension.

    Synchronous on purpose — caller offloads to thread for very large files.
    """
    try:
        mime = magic.from_buffer(content[:8192], mime=True)
        if mime and mime != "application/octet-stream":
            return mime
    except Exception:
        pass

    kind = filetype.guess(content[:8192])
    if kind is not None:
        return kind.mime

    # Fall back to extension-based guess
    guessed, _ = mimetypes.guess_type(path)
    if guessed:
        return guessed
    return "application/octet-stream"


async def sniff_mime_async(path: str, content: bytes) -> str:
    return await asyncio.to_thread(sniff_mime, path, content)


def is_rejected(path: str, mime: str) -> bool:
    ext = Path(path).suffix.lstrip(".").lower()
    return ext in REJECT_EXT or mime in REJECT_MIME


def reject_reason(path: str, mime: str) -> tuple[str, str | None]:
    """Return (reason, hint) for a rejected file."""
    ext = Path(path).suffix.lstrip(".").lower()
    if ext in {"mp4", "mov", "mkv", "webm", "avi", "flv", "wmv", "m4v"} or mime.startswith("video/"):
        return ("video file not supported", "video content cannot be read as text")
    if ext in {"mp3", "wav", "m4a", "ogg", "flac", "opus", "aac", "wma"} or mime.startswith("audio/"):
        return ("audio file not supported", "audio content cannot be read as text")
    if ext in {"exe", "so", "dll", "dylib", "o", "a", "bin", "com"}:
        return ("binary executable not supported", "use shell tools to inspect metadata")
    if ext in {"zip", "tar", "gz", "bz2", "rar", "7z", "tgz", "xz", "zst"}:
        return (
            "archive file not supported",
            "extract first with execute(\"unzip <file>\") then file_read on contents",
        )
    return ("file type not supported", None)
```

- [ ] **Step 5: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/parsers/test_mime.py -v`
Expected: PASS (8 tests). If a test fails because libmagic isn't installed, install it on the host.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/parsers/mime.py backend/tests/parsers/test_mime.py backend/pyproject.toml backend/uv.lock
git commit -m "feat(parsers): MIME sniffing via libmagic + REJECT lists for unsupported types"
```

---

### Task 5: Async hash dedup cache

**Files:**
- Create: `backend/cubebox/parsers/dedup.py`
- Create: `backend/tests/parsers/test_dedup.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/parsers/test_dedup.py
from uuid import uuid4

import pytest

from cubebox.parsers.dedup import (
    check,
    forget_conversation,
    hash_bytes,
    update,
    _file_state,
)


@pytest.fixture(autouse=True)
def clear_state():
    _file_state.clear()
    yield
    _file_state.clear()


@pytest.mark.asyncio
async def test_hash_bytes_returns_sha256_hex() -> None:
    digest = await hash_bytes(b"hello")
    # SHA-256 of "hello" = 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824
    assert digest == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_check_returns_false_when_empty() -> None:
    conv = uuid4()
    assert check(conv, "/p", "abc") is False


def test_update_then_check_matches() -> None:
    conv = uuid4()
    update(conv, "/p", "abc")
    assert check(conv, "/p", "abc") is True
    assert check(conv, "/p", "different") is False


def test_check_isolates_per_conversation() -> None:
    a, b = uuid4(), uuid4()
    update(a, "/p", "abc")
    assert check(b, "/p", "abc") is False


def test_forget_conversation_clears_only_that_conv() -> None:
    a, b = uuid4(), uuid4()
    update(a, "/p", "abc")
    update(b, "/p", "abc")
    forget_conversation(a)
    assert check(a, "/p", "abc") is False
    assert check(b, "/p", "abc") is True


@pytest.mark.asyncio
async def test_hash_bytes_offloads_to_thread() -> None:
    """Verifies the call returns awaitable (i.e. async-safe for large inputs)."""
    big = b"x" * (10 * 1024 * 1024)  # 10 MB
    digest = await hash_bytes(big)
    assert len(digest) == 64
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/parsers/test_dedup.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement dedup.py**

```python
# backend/cubebox/parsers/dedup.py
"""Conversation-scoped SHA-256 file_state dedup cache.

v1: in-process dict. SaaS multi-replica needs session-sticky routing OR
swap to Redis-backed cache (TTL = conversation lifetime).
"""

from __future__ import annotations

import asyncio
import hashlib
from uuid import UUID

# Keyed by (conversation_id, path); value = SHA-256 hex digest.
_file_state: dict[tuple[UUID, str], str] = {}


async def hash_bytes(data: bytes) -> str:
    """Compute SHA-256 hex; offload to thread (CPU-bound for large inputs)."""
    return await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest())


def check(conversation_id: UUID, path: str, digest: str) -> bool:
    """True if digest matches cached value (→ caller emits UnchangedOutput)."""
    return _file_state.get((conversation_id, path)) == digest


def update(conversation_id: UUID, path: str, digest: str) -> None:
    _file_state[(conversation_id, path)] = digest


def forget_conversation(conversation_id: UUID) -> None:
    """Drop all keys for the given conversation. Hook from ConversationManager.close."""
    keys = [k for k in _file_state if k[0] == conversation_id]
    for k in keys:
        _file_state.pop(k, None)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/parsers/test_dedup.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/parsers/dedup.py backend/tests/parsers/test_dedup.py
git commit -m "feat(parsers): async SHA-256 file_state dedup cache (conversation-scoped)"
```

---

### Task 6: `TextParser` plugin

**Files:**
- Create: `backend/cubebox/parsers/plugins/text.py`
- Create: `backend/tests/parsers/test_text_parser.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/parsers/test_text_parser.py
import pytest

from cubebox.parsers.plugins.text import TextParser
from cubebox.parsers.protocols import FileParser
from cubebox.parsers.schema import ParseOptions, TextOutput


def test_satisfies_protocol() -> None:
    assert isinstance(TextParser(), FileParser)


@pytest.mark.asyncio
async def test_decodes_utf8() -> None:
    p = TextParser()
    out = await p.parse(b"hello world", mime="text/plain", options=ParseOptions())
    assert isinstance(out, TextOutput)
    assert out.content == "hello world"
    assert out.size_bytes == 11
    assert out.truncated is False


@pytest.mark.asyncio
async def test_truncates_at_20k() -> None:
    p = TextParser()
    body = "x" * 30_000
    out = await p.parse(body.encode(), mime="text/plain", options=ParseOptions())
    assert out.truncated is True
    assert len(out.content) == 20_000
    assert out.metadata["total_chars"] == 30_000
    assert out.metadata["truncated_at_char"] == 20_000


@pytest.mark.asyncio
async def test_falls_back_to_latin1_on_decode_error() -> None:
    p = TextParser()
    # bytes that aren't valid UTF-8
    body = b"\xff\xfe hello"
    out = await p.parse(body, mime="text/plain", options=ParseOptions())
    assert isinstance(out, TextOutput)
    assert out.metadata.get("decode_fallback") == "latin-1"
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/parsers/test_text_parser.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement TextParser**

```python
# backend/cubebox/parsers/plugins/text.py
"""TextParser: UTF-8 decode for code/config/text files."""

from __future__ import annotations

from cubebox.parsers.schema import ParseOptions, TextOutput

MAX_CONTENT_CHARS = 20_000


class TextParser:
    mime_types = ["text/*"]
    extensions = [
        "txt", "md", "markdown", "rst", "org",
        "py", "pyi",
        "js", "ts", "jsx", "tsx", "mjs", "cjs",
        "json", "json5", "yaml", "yml", "toml", "ini", "conf", "env",
        "csv", "tsv",
        "html", "htm", "xhtml", "xml", "svg",
        "css", "scss", "sass", "less",
        "sh", "bash", "zsh", "fish",
        "sql", "graphql",
        "go", "rs", "java", "kt", "kts", "scala", "groovy",
        "c", "h", "cpp", "cc", "cxx", "hpp", "hxx",
        "rb", "php", "pl", "pm",
        "log", "lock", "properties",
    ]
    priority = 0

    async def parse(
        self,
        content: bytes,
        *,
        mime: str,
        options: ParseOptions,
    ) -> TextOutput:
        size = len(content)
        decode_fallback: str | None = None
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("latin-1", errors="replace")
            decode_fallback = "latin-1"

        truncated = False
        total_chars = len(text)
        metadata: dict[str, object] = {"parser": "text", "total_chars": total_chars}
        if decode_fallback:
            metadata["decode_fallback"] = decode_fallback
        if len(text) > MAX_CONTENT_CHARS:
            text = text[:MAX_CONTENT_CHARS]
            truncated = True
            metadata["truncated_at_char"] = MAX_CONTENT_CHARS

        return TextOutput(
            path="<set-by-caller>",
            mime=mime,
            content=text,
            size_bytes=size,
            truncated=truncated,
            metadata=metadata,
        )
```

NOTE: `path` is set to placeholder; the registry's `dispatch` overwrites with the actual path before returning. (Alternative: pass `path` into `parse`. Stick with current Protocol signature; registry overwrites.)

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/parsers/test_text_parser.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/parsers/plugins/text.py backend/tests/parsers/test_text_parser.py
git commit -m "feat(parsers): TextParser plugin (UTF-8 decode + 20K char truncation)"
```

---

### Task 7: `NotebookParser` plugin

**Files:**
- Create: `backend/cubebox/parsers/plugins/notebook.py`
- Create: `backend/tests/parsers/test_notebook_parser.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/parsers/test_notebook_parser.py
import json

import pytest

from cubebox.parsers.plugins.notebook import NotebookParser
from cubebox.parsers.protocols import FileParser
from cubebox.parsers.schema import NotebookOutput, ParseOptions


def test_satisfies_protocol() -> None:
    assert isinstance(NotebookParser(), FileParser)


@pytest.mark.asyncio
async def test_parses_simple_notebook() -> None:
    nb = {
        "cells": [
            {"cell_type": "markdown", "source": ["# Title\n", "intro\n"]},
            {
                "cell_type": "code",
                "source": "print('hi')",
                "outputs": [{"output_type": "stream", "text": "hi\n"}],
            },
        ]
    }
    p = NotebookParser()
    out = await p.parse(
        json.dumps(nb).encode(),
        mime="application/x-ipynb+json",
        options=ParseOptions(),
    )
    assert isinstance(out, NotebookOutput)
    assert len(out.cells) == 2
    assert out.cells[0].cell_type == "markdown"
    assert "Title" in out.cells[0].source
    assert out.cells[1].cell_type == "code"
    assert out.cells[1].outputs is not None


@pytest.mark.asyncio
async def test_truncates_when_exceeds_20k() -> None:
    nb = {
        "cells": [
            {"cell_type": "markdown", "source": "x" * 25_000},
            {"cell_type": "code", "source": "y", "outputs": []},
        ]
    }
    p = NotebookParser()
    out = await p.parse(
        json.dumps(nb).encode(),
        mime="application/x-ipynb+json",
        options=ParseOptions(),
    )
    # truncation happens at the cell level; first big cell included, second omitted
    assert out.metadata.get("truncated_cells", 0) >= 1
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/parsers/test_notebook_parser.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement NotebookParser**

```python
# backend/cubebox/parsers/plugins/notebook.py
"""NotebookParser: parse Jupyter .ipynb into structured cells."""

from __future__ import annotations

import json
from typing import Any

from cubebox.parsers.schema import NotebookCell, NotebookOutput, ParseOptions

MAX_CONTENT_CHARS = 20_000


class NotebookParser:
    mime_types = ["application/x-ipynb+json"]
    extensions = ["ipynb"]
    priority = 10

    async def parse(
        self,
        content: bytes,
        *,
        mime: str,
        options: ParseOptions,
    ) -> NotebookOutput:
        try:
            nb = json.loads(content)
        except json.JSONDecodeError as e:
            # Caller registry will catch + wrap as ErrorOutput; raise to propagate
            raise ValueError(f"invalid notebook JSON: {e}") from e

        all_cells = nb.get("cells", [])
        result_cells: list[NotebookCell] = []
        running_chars = 0
        truncated_cells = 0

        for raw in all_cells:
            cell_type = raw.get("cell_type", "raw")
            if cell_type not in ("code", "markdown", "raw"):
                cell_type = "raw"
            source = raw.get("source", "")
            if isinstance(source, list):
                source = "".join(source)

            outputs: list[dict[str, Any]] | None = None
            if cell_type == "code":
                outputs = []
                for o in raw.get("outputs", []):
                    if "text" in o:
                        text = o["text"]
                        if isinstance(text, list):
                            text = "".join(text)
                        outputs.append({"type": o.get("output_type", "stream"), "text": text})
                    else:
                        # Drop image base64 blobs; keep a marker only
                        outputs.append({"type": o.get("output_type", "unknown")})

            cell_chars = len(source) + sum(len(o.get("text", "")) for o in (outputs or []))
            if running_chars + cell_chars > MAX_CONTENT_CHARS:
                truncated_cells = len(all_cells) - len(result_cells)
                break
            running_chars += cell_chars
            result_cells.append(
                NotebookCell(cell_type=cell_type, source=source, outputs=outputs)
            )

        metadata: dict[str, Any] = {
            "parser": "notebook",
            "total_cells": len(all_cells),
        }
        if truncated_cells > 0:
            metadata["truncated_cells"] = truncated_cells

        return NotebookOutput(
            path="<set-by-caller>",
            cells=result_cells,
            metadata=metadata,
        )
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/parsers/test_notebook_parser.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/parsers/plugins/notebook.py backend/tests/parsers/test_notebook_parser.py
git commit -m "feat(parsers): NotebookParser preserves Jupyter cell structure"
```

---

### Task 8: `DoclingParser` — sync path + http client

**Files:**
- Create: `backend/cubebox/parsers/plugins/docling.py`
- Create: `backend/tests/parsers/test_docling_parser.py`

- [ ] **Step 1: Write failing test for sync path with httpx mock**

```python
# backend/tests/parsers/test_docling_parser.py
import base64
import json
from unittest.mock import patch

import httpx
import pytest

from cubebox.parsers.plugins.docling import DoclingParser
from cubebox.parsers.protocols import FileParser
from cubebox.parsers.schema import ErrorOutput, ParseOptions, TextOutput


def test_satisfies_protocol() -> None:
    assert isinstance(DoclingParser(base_url="http://test"), FileParser)


@pytest.mark.asyncio
async def test_sync_path_for_small_file() -> None:
    """File < async_threshold_mb hits sync /v1/convert/source endpoint."""

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/convert/source"
        body = json.loads(request.content)
        assert body["sources"][0]["kind"] == "file"
        return httpx.Response(
            200,
            json={"document": {"md_content": "# Parsed\n\nhello"}},
        )

    transport = httpx.MockTransport(handler)
    p = DoclingParser(
        base_url="http://docling",
        api_key=None,
        timeout_sync_seconds=30,
        timeout_async_minutes=10,
        async_threshold_mb=3,
        poll_interval_seconds=2,
        _transport=transport,
    )
    content = b"%PDF-1.4 stub"
    out = await p.parse(content, mime="application/pdf", options=ParseOptions())
    assert isinstance(out, TextOutput)
    assert "Parsed" in out.content
    assert out.metadata["parser"] == "docling"


@pytest.mark.asyncio
async def test_sync_path_returns_error_on_5xx() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal"})

    transport = httpx.MockTransport(handler)
    p = DoclingParser(
        base_url="http://docling",
        api_key=None,
        timeout_sync_seconds=30,
        timeout_async_minutes=10,
        async_threshold_mb=3,
        poll_interval_seconds=2,
        _transport=transport,
    )
    out = await p.parse(b"x" * 100, mime="application/pdf", options=ParseOptions())
    assert isinstance(out, ErrorOutput)
    assert out.retryable is True


@pytest.mark.asyncio
async def test_sync_path_truncates_long_content() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"document": {"md_content": "x" * 30_000}},
        )

    transport = httpx.MockTransport(handler)
    p = DoclingParser(
        base_url="http://docling",
        api_key=None,
        timeout_sync_seconds=30,
        timeout_async_minutes=10,
        async_threshold_mb=3,
        poll_interval_seconds=2,
        _transport=transport,
    )
    out = await p.parse(b"x", mime="application/pdf", options=ParseOptions())
    assert isinstance(out, TextOutput)
    assert out.truncated is True
    assert len(out.content) == 20_000
```

- [ ] **Step 2: Add httpx to deps if missing**

```bash
cd backend && uv add httpx
```

(Likely already installed, but ensure.)

- [ ] **Step 3: Run, verify fail**

Run: `cd backend && uv run pytest tests/parsers/test_docling_parser.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 4: Implement DoclingParser sync path**

```python
# backend/cubebox/parsers/plugins/docling.py
"""DoclingParser: HTTP client to docling-serve."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import httpx

from cubebox.parsers.schema import ErrorOutput, FileReadOutput, ParseOptions, TextOutput

MAX_CONTENT_CHARS = 20_000


class DoclingParser:
    mime_types = [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/epub+zip",
        "image/*",
    ]
    extensions = ["pdf", "docx", "pptx", "xlsx", "epub", "png", "jpg", "jpeg", "gif", "webp", "tiff", "bmp"]
    priority = 20

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        timeout_sync_seconds: int = 30,
        timeout_async_minutes: int = 10,
        async_threshold_mb: int = 3,
        poll_interval_seconds: int = 2,
        _transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_sync = timeout_sync_seconds
        self.timeout_async_seconds = timeout_async_minutes * 60
        self.async_threshold_bytes = async_threshold_mb * 1024 * 1024
        self.poll_interval = poll_interval_seconds
        self._transport = _transport

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-Api-Key"] = self.api_key
        return h

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            transport=self._transport,
        )

    async def parse(
        self,
        content: bytes,
        *,
        mime: str,
        options: ParseOptions,
    ) -> FileReadOutput:
        if len(content) < self.async_threshold_bytes:
            return await self._parse_sync(content, mime, options)
        return await self._parse_async(content, mime, options)

    async def _parse_sync(
        self,
        content: bytes,
        mime: str,
        options: ParseOptions,
    ) -> FileReadOutput:
        body = {
            "sources": [
                {
                    "kind": "file",
                    "filename": "input",
                    "base64": base64.b64encode(content).decode("ascii"),
                }
            ],
            "options": self._build_options(options),
        }
        try:
            async with self._client(timeout=self.timeout_sync) as client:
                resp = await client.post(
                    "/v1/convert/source",
                    json=body,
                    headers=self._headers(),
                )
                if resp.status_code >= 500:
                    return ErrorOutput(
                        path="<set-by-caller>",
                        error=f"docling-serve {resp.status_code}: {resp.text[:200]}",
                        retryable=True,
                    )
                if resp.status_code >= 400:
                    return ErrorOutput(
                        path="<set-by-caller>",
                        error=f"docling-serve {resp.status_code}: {resp.text[:200]}",
                        retryable=False,
                    )
                data = resp.json()
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            return ErrorOutput(
                path="<set-by-caller>",
                error=f"docling-serve unreachable: {e}",
                retryable=True,
            )
        return self._make_text_output(data, content, mime)

    async def _parse_async(
        self,
        content: bytes,
        mime: str,
        options: ParseOptions,
    ) -> FileReadOutput:
        # Implemented in Task 9
        return ErrorOutput(
            path="<set-by-caller>",
            error="async path not implemented",
            retryable=False,
        )

    def _build_options(self, options: ParseOptions) -> dict:
        opts: dict[str, object] = {}
        if options.page_range:
            opts["page_range"] = options.page_range
        if options.language_hint:
            opts["lang"] = options.language_hint
        return opts

    def _make_text_output(self, data: dict, content: bytes, mime: str) -> TextOutput:
        # docling-serve response shape: {"document": {"md_content": "..."}}
        md = (data.get("document") or {}).get("md_content", "")
        if not isinstance(md, str):
            md = str(md)

        truncated = False
        total = len(md)
        metadata: dict[str, object] = {"parser": "docling", "total_chars": total}
        if total > MAX_CONTENT_CHARS:
            md = md[:MAX_CONTENT_CHARS]
            truncated = True
            metadata["truncated_at_char"] = MAX_CONTENT_CHARS

        return TextOutput(
            path="<set-by-caller>",
            mime=mime,
            content=md,
            size_bytes=len(content),
            truncated=truncated,
            metadata=metadata,
        )
```

- [ ] **Step 5: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/parsers/test_docling_parser.py -v`
Expected: 3 tests PASS, async-path tests not yet present.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/parsers/plugins/docling.py backend/tests/parsers/test_docling_parser.py
git commit -m "feat(parsers): DoclingParser sync HTTP path + httpx mock tests"
```

---

### Task 9: `DoclingParser` async path + polling

**Files:**
- Modify: `backend/cubebox/parsers/plugins/docling.py`
- Modify: `backend/tests/parsers/test_docling_parser.py`

- [ ] **Step 1: Append failing async-path tests**

Append to `backend/tests/parsers/test_docling_parser.py`:

```python
@pytest.mark.asyncio
async def test_async_path_for_large_file() -> None:
    """File ≥ async_threshold_mb hits async submit + poll."""
    poll_count = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1alpha/convert/source/async":
            return httpx.Response(202, json={"task_id": "tk_123"})
        if request.url.path == "/v1alpha/convert/tasks/tk_123":
            poll_count["n"] += 1
            if poll_count["n"] < 2:
                return httpx.Response(200, json={"status": "STARTED"})
            return httpx.Response(
                200,
                json={
                    "status": "COMPLETED",
                    "result": {"document": {"md_content": "# Big\n\ndata"}},
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    p = DoclingParser(
        base_url="http://docling",
        api_key=None,
        timeout_sync_seconds=30,
        timeout_async_minutes=10,
        async_threshold_mb=1,
        poll_interval_seconds=0,  # tests should not actually sleep
        _transport=transport,
    )
    big = b"x" * (2 * 1024 * 1024)  # 2 MB > 1 MB threshold
    out = await p.parse(big, mime="application/pdf", options=ParseOptions())
    assert isinstance(out, TextOutput)
    assert "Big" in out.content


@pytest.mark.asyncio
async def test_async_path_task_failed_returns_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1alpha/convert/source/async":
            return httpx.Response(202, json={"task_id": "tk_x"})
        return httpx.Response(
            200,
            json={"status": "FAILED", "error": "corrupt input"},
        )

    transport = httpx.MockTransport(handler)
    p = DoclingParser(
        base_url="http://docling",
        api_key=None,
        timeout_sync_seconds=30,
        timeout_async_minutes=10,
        async_threshold_mb=1,
        poll_interval_seconds=0,
        _transport=transport,
    )
    big = b"x" * (2 * 1024 * 1024)
    out = await p.parse(big, mime="application/pdf", options=ParseOptions())
    assert isinstance(out, ErrorOutput)
    assert out.retryable is False
    assert "corrupt" in out.error
```

- [ ] **Step 2: Run, verify fail (async path returns "not implemented")**

Run: `cd backend && uv run pytest tests/parsers/test_docling_parser.py -v`
Expected: 2 new tests FAIL.

- [ ] **Step 3: Replace `_parse_async` body in `docling.py`**

Replace the `_parse_async` method:

```python
    async def _parse_async(
        self,
        content: bytes,
        mime: str,
        options: ParseOptions,
    ) -> FileReadOutput:
        body = {
            "sources": [
                {
                    "kind": "file",
                    "filename": "input",
                    "base64": base64.b64encode(content).decode("ascii"),
                }
            ],
            "options": self._build_options(options),
        }
        try:
            async with self._client(timeout=self.timeout_async_seconds) as client:
                # Submit
                resp = await client.post(
                    "/v1alpha/convert/source/async",
                    json=body,
                    headers=self._headers(),
                )
                if resp.status_code >= 400:
                    return ErrorOutput(
                        path="<set-by-caller>",
                        error=f"docling-serve submit {resp.status_code}: {resp.text[:200]}",
                        retryable=resp.status_code >= 500,
                    )
                task_id = resp.json().get("task_id")
                if not task_id:
                    return ErrorOutput(
                        path="<set-by-caller>",
                        error="docling-serve async submit returned no task_id",
                        retryable=False,
                    )

                # Poll
                deadline = asyncio.get_event_loop().time() + self.timeout_async_seconds
                while asyncio.get_event_loop().time() < deadline:
                    poll = await client.get(
                        f"/v1alpha/convert/tasks/{task_id}",
                        headers=self._headers(),
                    )
                    if poll.status_code >= 500:
                        await asyncio.sleep(self.poll_interval)
                        continue
                    if poll.status_code >= 400:
                        return ErrorOutput(
                            path="<set-by-caller>",
                            error=f"docling-serve poll {poll.status_code}: {poll.text[:200]}",
                            retryable=False,
                        )
                    data = poll.json()
                    status = data.get("status")
                    if status == "COMPLETED":
                        result = data.get("result", {})
                        return self._make_text_output(result, content, mime)
                    if status == "FAILED":
                        return ErrorOutput(
                            path="<set-by-caller>",
                            error=f"docling-serve task FAILED: {data.get('error', 'no detail')}",
                            retryable=False,
                        )
                    await asyncio.sleep(self.poll_interval)

                return ErrorOutput(
                    path="<set-by-caller>",
                    error="docling-serve async timeout",
                    retryable=True,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            return ErrorOutput(
                path="<set-by-caller>",
                error=f"docling-serve unreachable: {e}",
                retryable=True,
            )
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/parsers/test_docling_parser.py -v`
Expected: PASS (5 tests total).

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/parsers/plugins/docling.py backend/tests/parsers/test_docling_parser.py
git commit -m "feat(parsers): DoclingParser async submit + poll path with COMPLETED/FAILED handling"
```

---

### Task 10: `ParserRegistry` (discover + dispatch + REJECT precheck)

**Files:**
- Create: `backend/cubebox/parsers/registry.py`
- Modify: `backend/cubebox/parsers/__init__.py`
- Create: `backend/tests/parsers/test_registry.py`
- Modify: `backend/pyproject.toml` (add entry_points group)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/parsers/test_registry.py
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cubebox.parsers.registry import ParserRegistry, get_parser_registry, reset_parser_registry_for_tests
from cubebox.parsers.schema import (
    ErrorOutput,
    NotebookOutput,
    ParseOptions,
    TextOutput,
    UnchangedOutput,
    UnsupportedOutput,
)


@pytest.fixture(autouse=True)
def fresh_registry():
    reset_parser_registry_for_tests()
    yield
    reset_parser_registry_for_tests()


@pytest.mark.asyncio
async def test_resolve_picks_text_for_python_file() -> None:
    reg = ParserRegistry()
    await reg.discover()
    parser = reg.resolve(mime="text/x-python", ext="py")
    assert parser is not None
    assert "text" in type(parser).__name__.lower()


@pytest.mark.asyncio
async def test_resolve_picks_docling_for_pdf() -> None:
    reg = ParserRegistry()
    await reg.discover()
    parser = reg.resolve(mime="application/pdf", ext="pdf")
    assert parser is not None
    assert "docling" in type(parser).__name__.lower()


@pytest.mark.asyncio
async def test_resolve_picks_notebook_for_ipynb() -> None:
    reg = ParserRegistry()
    await reg.discover()
    parser = reg.resolve(mime="application/x-ipynb+json", ext="ipynb")
    assert parser is not None
    assert "notebook" in type(parser).__name__.lower()


@pytest.mark.asyncio
async def test_dispatch_rejects_video() -> None:
    sandbox = MagicMock()
    sandbox._download_one = AsyncMock(return_value=b"\x00" * 100)
    reg = ParserRegistry()
    await reg.discover()
    out = await reg.dispatch(
        sandbox=sandbox,
        path="/tmp/movie.mp4",
        options=ParseOptions(),
        conversation_id=uuid4(),
    )
    assert isinstance(out, UnsupportedOutput)
    assert "video" in out.reason


@pytest.mark.asyncio
async def test_dispatch_rejects_oversize() -> None:
    sandbox = MagicMock()
    sandbox._download_one = AsyncMock(return_value=b"\x00" * (101 * 1024 * 1024))
    reg = ParserRegistry()
    await reg.discover()
    out = await reg.dispatch(
        sandbox=sandbox,
        path="/tmp/big.txt",
        options=ParseOptions(),
        conversation_id=uuid4(),
    )
    assert isinstance(out, UnsupportedOutput)
    assert "100" in out.reason or "large" in out.reason.lower()


@pytest.mark.asyncio
async def test_dispatch_returns_unchanged_on_second_read() -> None:
    sandbox = MagicMock()
    sandbox._download_one = AsyncMock(return_value=b"hello world")
    reg = ParserRegistry()
    await reg.discover()
    conv = uuid4()

    first = await reg.dispatch(
        sandbox=sandbox, path="/tmp/a.txt", options=ParseOptions(), conversation_id=conv
    )
    assert isinstance(first, TextOutput)

    second = await reg.dispatch(
        sandbox=sandbox, path="/tmp/a.txt", options=ParseOptions(), conversation_id=conv
    )
    assert isinstance(second, UnchangedOutput)


@pytest.mark.asyncio
async def test_dispatch_overwrites_path_in_output() -> None:
    sandbox = MagicMock()
    sandbox._download_one = AsyncMock(return_value=b"x = 1")
    reg = ParserRegistry()
    await reg.discover()
    out = await reg.dispatch(
        sandbox=sandbox, path="/tmp/script.py", options=ParseOptions(), conversation_id=None
    )
    assert isinstance(out, TextOutput)
    assert out.path == "/tmp/script.py"


@pytest.mark.asyncio
async def test_dispatch_wraps_parser_exceptions_as_error() -> None:
    sandbox = MagicMock()
    sandbox._download_one = AsyncMock(return_value=b"not valid json {")
    reg = ParserRegistry()
    await reg.discover()
    out = await reg.dispatch(
        sandbox=sandbox, path="/tmp/x.ipynb", options=ParseOptions(), conversation_id=None
    )
    assert isinstance(out, ErrorOutput)
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/parsers/test_registry.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Add entry_points to pyproject.toml**

Edit `backend/pyproject.toml` to add (under `[project.entry-points]` section, create if absent):

```toml
[project.entry-points."cubebox.parsers"]
text = "cubebox.parsers.plugins.text:TextParser"
notebook = "cubebox.parsers.plugins.notebook:NotebookParser"
docling = "cubebox.parsers.plugins.docling:DoclingParser"
```

- [ ] **Step 4: Implement registry.py**

```python
# backend/cubebox/parsers/registry.py
"""ParserRegistry: discover plugins via entry_points + dispatch by MIME."""

from __future__ import annotations

import importlib.metadata
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from cubebox.parsers import dedup
from cubebox.parsers.mime import (
    is_rejected,
    reject_reason,
    sniff_mime_async,
)
from cubebox.parsers.protocols import FileParser
from cubebox.parsers.schema import (
    ErrorOutput,
    FileReadOutput,
    ParseOptions,
    UnchangedOutput,
    UnsupportedOutput,
)

logger = logging.getLogger(__name__)

GROUP = "cubebox.parsers"
MAX_FILE_BYTES = 100 * 1024 * 1024


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: list[FileParser] = []

    async def discover(self) -> None:
        """Load all FileParser plugins from entry_points."""
        for ep in importlib.metadata.entry_points(group=GROUP):
            cls = ep.load()
            instance = cls() if isinstance(cls, type) else cls
            if not isinstance(instance, FileParser):
                raise RuntimeError(
                    f"entry_point {ep.value} does not satisfy FileParser Protocol"
                )
            self._parsers.append(instance)
            logger.info("registered FileParser: %s (priority=%d)", ep.name, instance.priority)

        # DoclingParser may need config-injected base_url etc.
        # If a DoclingParser was loaded with default args, swap in a config-bound one.
        from cubebox.config import config  # lazy import to avoid cycles
        from cubebox.parsers.plugins.docling import DoclingParser

        for i, p in enumerate(self._parsers):
            if isinstance(p, DoclingParser):
                self._parsers[i] = DoclingParser(
                    base_url=config.get("parsers.docling_serve.base_url", "http://docling-serve:5001"),
                    api_key=config.get("parsers.docling_serve.api_key") or None,
                    timeout_sync_seconds=int(config.get("parsers.docling_serve.timeout_sync_seconds", 30)),
                    timeout_async_minutes=int(config.get("parsers.docling_serve.timeout_async_minutes", 10)),
                    async_threshold_mb=int(config.get("parsers.docling_serve.async_threshold_mb", 3)),
                    poll_interval_seconds=int(config.get("parsers.docling_serve.poll_interval_seconds", 2)),
                )

    def resolve(self, *, mime: str, ext: str) -> FileParser | None:
        """Pick the best parser for a (mime, ext) pair."""
        candidates: list[tuple[int, FileParser]] = []
        for p in self._parsers:
            score = self._match_score(p, mime, ext)
            if score > 0:
                candidates.append((score + p.priority, p))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    @staticmethod
    def _match_score(parser: FileParser, mime: str, ext: str) -> int:
        for pattern in parser.mime_types:
            if pattern == mime:
                return 100  # exact MIME match
            if pattern.endswith("/*") and mime.startswith(pattern[:-1]):
                return 50  # MIME wildcard
        if ext in parser.extensions:
            return 25  # extension fallback
        return 0

    async def dispatch(
        self,
        sandbox: Any,
        path: str,
        options: ParseOptions,
        conversation_id: UUID | None,
    ) -> FileReadOutput:
        # 1. download
        files = await sandbox.download([path]) if hasattr(sandbox, "download") else None
        if files is None:
            content = await sandbox._download_one(path)
        else:
            content = files[0][1]
        size = len(content)

        # 2. size precheck
        if size > MAX_FILE_BYTES:
            return UnsupportedOutput(
                path=path, mime="application/octet-stream", size_bytes=size,
                reason="file too large (100MB limit)",
                hint="try reading specific pages with page_range",
            )

        # 3. MIME sniff
        mime = await sniff_mime_async(path, content)

        # 4. REJECT list
        if is_rejected(path, mime):
            reason, hint = reject_reason(path, mime)
            return UnsupportedOutput(
                path=path, mime=mime, size_bytes=size, reason=reason, hint=hint,
            )

        # 5. dedup check
        if conversation_id is not None:
            digest = await dedup.hash_bytes(content)
            if dedup.check(conversation_id, path, digest):
                return UnchangedOutput(path=path)
            dedup.update(conversation_id, path, digest)

        # 6. resolve plugin & parse
        ext = Path(path).suffix.lstrip(".").lower()
        parser = self.resolve(mime=mime, ext=ext)
        if parser is None:
            return UnsupportedOutput(
                path=path, mime=mime, size_bytes=size,
                reason="no parser matched",
            )
        try:
            out = await parser.parse(content, mime=mime, options=options)
        except Exception as exc:
            logger.exception("parser %s failed on %s", type(parser).__name__, path)
            return ErrorOutput(path=path, error=str(exc), retryable=False)

        # Overwrite the placeholder path; preserve all other fields
        out_dict = out.model_dump()
        out_dict["path"] = path
        return type(out).model_validate(out_dict)


_registry: ParserRegistry | None = None


def get_parser_registry() -> ParserRegistry:
    global _registry
    if _registry is None:
        _registry = ParserRegistry()
    return _registry


def reset_parser_registry_for_tests() -> None:
    global _registry
    _registry = None
```

- [ ] **Step 5: Re-export from `__init__.py`**

Replace `backend/cubebox/parsers/__init__.py`:

```python
"""File parser plugin registry shared by file_read tool and future filebox."""

from cubebox.parsers.protocols import FileParser
from cubebox.parsers.registry import (
    ParserRegistry,
    get_parser_registry,
    reset_parser_registry_for_tests,
)
from cubebox.parsers.schema import (
    ErrorOutput,
    FileReadOutput,
    NotebookCell,
    NotebookOutput,
    ParseOptions,
    TextOutput,
    UnchangedOutput,
    UnsupportedOutput,
)

__all__ = [
    "ErrorOutput",
    "FileParser",
    "FileReadOutput",
    "NotebookCell",
    "NotebookOutput",
    "ParseOptions",
    "ParserRegistry",
    "TextOutput",
    "UnchangedOutput",
    "UnsupportedOutput",
    "get_parser_registry",
    "reset_parser_registry_for_tests",
]
```

- [ ] **Step 6: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/parsers/ -v`
Expected: All parser tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/cubebox/parsers/registry.py backend/cubebox/parsers/__init__.py backend/tests/parsers/test_registry.py backend/pyproject.toml backend/uv.lock
git commit -m "feat(parsers): ParserRegistry discover + dispatch with REJECT/dedup precheck"
```

---

### Task 11: `Sandbox.file_read` non-abstract method

**Files:**
- Modify: `backend/cubebox/sandbox/base.py`
- Create: `backend/tests/parsers/test_sandbox_file_read.py`

- [ ] **Step 1: Write failing integration test**

```python
# backend/tests/parsers/test_sandbox_file_read.py
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cubebox.parsers.schema import ParseOptions, TextOutput
from cubebox.sandbox.base import Sandbox


class _FakeSandbox(Sandbox):
    @property
    def id(self) -> str:
        return "fake"

    @property
    def workdir(self) -> str:
        return "/work"

    async def execute(self, command, *, timeout=None):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def upload(self, files):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def download(self, paths):  # type: ignore[no-untyped-def]
        return [(paths[0], b"hello world")]

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_file_read_default_impl_dispatches_via_registry() -> None:
    s = _FakeSandbox()
    out = await s.file_read("/tmp/a.txt", conversation_id=uuid4())
    assert isinstance(out, TextOutput)
    assert out.content == "hello world"
    assert out.path == "/tmp/a.txt"


@pytest.mark.asyncio
async def test_file_read_passes_options() -> None:
    s = _FakeSandbox()
    out = await s.file_read(
        "/tmp/a.txt",
        options=ParseOptions(page_range="1-3"),
        conversation_id=None,
    )
    assert isinstance(out, TextOutput)
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/parsers/test_sandbox_file_read.py -v`
Expected: FAIL with `AttributeError: ... has no attribute 'file_read'`.

- [ ] **Step 3: Add `Sandbox.file_read` non-abstract default**

Edit `backend/cubebox/sandbox/base.py`. After existing class methods:

```python
    async def file_read(
        self,
        path: str,
        *,
        options: "ParseOptions | None" = None,
        conversation_id: "UUID | None" = None,
    ) -> "FileReadOutput":
        """Read and parse a file at <path> inside the sandbox.

        Default impl: download bytes via self.download + dispatch via
        cubebox.parsers registry. Subclasses may override (future Sandbox
        implementations with native parsing may call their own API).
        """
        from cubebox.parsers import ParseOptions, get_parser_registry

        return await get_parser_registry().dispatch(
            sandbox=self,
            path=path,
            options=options or ParseOptions(),
            conversation_id=conversation_id,
        )
```

Add type-only imports at the top:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID
    from cubebox.parsers import FileReadOutput, ParseOptions
```

NOTE: registry must be `discover()`-ed before `file_read` is called. App startup will do this in Task 14.

- [ ] **Step 4: Bootstrap registry in test conftest**

Add to `backend/tests/parsers/conftest.py` (create if missing):

```python
import pytest

from cubebox.parsers import get_parser_registry, reset_parser_registry_for_tests


@pytest.fixture(autouse=True)
def _bind_parser_registry():
    reset_parser_registry_for_tests()
    import asyncio
    asyncio.get_event_loop().run_until_complete(get_parser_registry().discover())
```

(Adjust to your test event loop pattern; if pytest-asyncio mode is "auto", use an `@pytest.fixture(autouse=True)` async fixture instead.)

- [ ] **Step 5: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/parsers/test_sandbox_file_read.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/sandbox/base.py backend/tests/parsers/test_sandbox_file_read.py backend/tests/parsers/conftest.py
git commit -m "feat(sandbox): add Sandbox.file_read non-abstract method dispatching to parser registry"
```

---

### Task 12: Register `file_read` agent tool in SandboxMiddleware

**Files:**
- Modify: `backend/cubebox/middleware/sandbox.py`
- Create: `backend/tests/middleware/test_sandbox_file_read_tool.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/middleware/test_sandbox_file_read_tool.py
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cubebox.middleware.sandbox import _create_file_read_tool
from cubebox.parsers.schema import TextOutput


@pytest.mark.asyncio
async def test_file_read_tool_calls_sandbox() -> None:
    sandbox = MagicMock()
    sandbox.file_read = AsyncMock(
        return_value=TextOutput(
            path="/tmp/a.txt", mime="text/plain",
            content="hi", size_bytes=2, metadata={},
        )
    )
    tool = _create_file_read_tool(sandbox, conversation_id=uuid4())
    assert tool.name == "file_read"
    result = await tool.coroutine(path="/tmp/a.txt")  # type: ignore[union-attr]
    assert "hi" in str(result)


def test_file_read_tool_description_mentions_use_cases() -> None:
    sandbox = MagicMock()
    tool = _create_file_read_tool(sandbox, conversation_id=None)
    desc = tool.description.lower()
    # The description must mention what to use it for + what NOT to
    assert "pdf" in desc
    assert "video" in desc or "audio" in desc
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/middleware/test_sandbox_file_read_tool.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `_create_file_read_tool` in `middleware/sandbox.py`**

Append to `backend/cubebox/middleware/sandbox.py`:

```python
from uuid import UUID

from cubebox.parsers import ParseOptions
from pydantic import BaseModel, Field as PField


class _FileReadArgs(BaseModel):
    path: str = PField(description="Absolute path inside the sandbox to the file to read.")
    page_range: str | None = PField(
        default=None,
        description=(
            "Optional 1-indexed page range, e.g. '1-5' or '3'. "
            "Only honored for paginated formats (PDF, DOCX, PPTX)."
        ),
    )


_FILE_READ_DESCRIPTION = """\
Read a file from the sandbox workspace and return its content in a form
you can reason about. Use this whenever you need to inspect user uploads,
agent-generated artifacts, or any file inside the sandbox — not shell
output, not network resources.

USE THIS TOOL FOR:
- Text / source code (.txt .md .py .js .ts .json .yaml .toml .csv .html
  .css .go .rs .java .cpp etc.) — returns raw UTF-8 text.
- Documents (.pdf .docx .pptx .xlsx .epub) — returns markdown
  preserving headings, tables, lists.
- Notebooks (.ipynb) — returns structured cells.
- Images (.png .jpg .webp .tiff) — returns OCR'd text content.

DO NOT USE THIS TOOL FOR:
- Video / Audio — returns kind="unsupported".
- Executables / Binaries — returns kind="unsupported".
- Archives (.zip .tar .gz) — extract first via execute("unzip ..."),
  then file_read on extracted files.
- Remote URLs — file_read only reads sandbox paths.
- Quick line peeks — execute("sed -n '42p' <file>") is faster.

RETURN FORMAT (discriminated by `kind`):
- "text"        : {content, mime, size_bytes, truncated, metadata}
- "notebook"    : {cells: [{cell_type, source, outputs}, ...]}
- "unsupported" : {reason, hint, mime, size_bytes}
- "unchanged"   : file unchanged since previous file_read in this session
- "error"       : {error, retryable}

LIMITS:
- Files > 100 MB are refused with kind="unsupported".
- Content longer than 20,000 characters is truncated (truncated=True).
  Use `page_range` for narrower slices.
- Large files (>3 MB) trigger async parsing; up to 10 minutes.
"""


def _create_file_read_tool(sandbox: Sandbox, conversation_id: UUID | None) -> BaseTool:
    """Build the file_read tool backed by a sandbox + conversation."""

    async def _file_read(path: str, page_range: str | None = None):
        result = await sandbox.file_read(
            path,
            options=ParseOptions(page_range=page_range),
            conversation_id=conversation_id,
        )
        # Return the dict; LangChain will JSON-serialize for the LLM.
        return result.model_dump()

    return StructuredTool.from_function(
        coroutine=_file_read,
        name="file_read",
        description=_FILE_READ_DESCRIPTION,
        args_schema=_FileReadArgs,
        metadata={"content_type": "file_read"},
    )
```

Then in the SandboxMiddleware class where tools are assembled, add `_create_file_read_tool(sandbox, conversation_id=...)` to the tool list.

NOTE: The exact `conversation_id` source depends on existing middleware plumbing. If `SandboxMiddleware` already receives it via context, pass through; otherwise default to None for v1 (dedup will simply not engage).

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/middleware/test_sandbox_file_read_tool.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/middleware/sandbox.py backend/tests/middleware/test_sandbox_file_read_tool.py
git commit -m "feat(sandbox): register file_read agent tool in SandboxMiddleware"
```

---

### Task 13: Backend `parsers.docling_serve` config schema + YAML

**Files:**
- Modify: `backend/cubebox/config.py`
- Modify: `backend/config.yaml`
- Modify: `backend/config.development.yaml`
- Modify: `backend/config.test.yaml`

- [ ] **Step 1: Add config schema (if pydantic-based)**

In `backend/cubebox/config.py`:

```python
class _DoclingServeConfig(BaseModel):
    base_url: str = "http://docling-serve:5001"
    api_key: str | None = None
    timeout_sync_seconds: int = 30
    timeout_async_minutes: int = 10
    async_threshold_mb: int = 3
    poll_interval_seconds: int = 2


class ParsersConfig(BaseModel):
    docling_serve: _DoclingServeConfig = _DoclingServeConfig()


# Add `parsers: ParsersConfig = ParsersConfig()` to top-level Settings
```

(If using dynaconf, just rely on the YAML defaults — no schema class needed.)

- [ ] **Step 2: Append to YAML configs**

To `backend/config.yaml`, `backend/config.development.yaml`, `backend/config.test.yaml`:

```yaml
parsers:
  docling_serve:
    base_url: http://docling-serve:5001
    api_key: ${DOCLING_SERVE_API_KEY:-}
    timeout_sync_seconds: 30
    timeout_async_minutes: 10
    async_threshold_mb: 3
    poll_interval_seconds: 2
```

For test config, use a localhost URL that won't be hit during tests (mocked):

```yaml
parsers:
  docling_serve:
    base_url: http://localhost-test-no-hit
```

- [ ] **Step 3: Verify config loads**

Run: `cd backend && uv run python -c "from cubebox.config import config; print(config.get('parsers.docling_serve.base_url'))"`
Expected: prints `http://docling-serve:5001`.

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/config.py backend/config.yaml backend/config.development.yaml backend/config.test.yaml
git commit -m "feat(parsers): add parsers.docling_serve config schema with sensible defaults"
```

---

### Task 14: App startup parser registry discover + docling-serve in docker-compose

**Files:**
- Modify: `backend/cubebox/api/app.py`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Wire registry discover in lifespan**

In `backend/cubebox/api/app.py` lifespan async context manager, add:

```python
from cubebox.parsers import get_parser_registry

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing setup ...
    await get_parser_registry().discover()
    # ... rest of lifespan ...
```

- [ ] **Step 2: Add docling-serve service to docker-compose.yml**

If `docker-compose.yml` exists at repo root, append:

```yaml
  docling-serve:
    image: quay.io/docling-project/docling-serve-cpu
    environment:
      DOCLING_SERVE_API_KEY: ${DOCLING_SERVE_API_KEY:-}
    ports:
      - "5001:5001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5001/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped
```

If no `docker-compose.yml`, skip this step (deployment will follow per-environment patterns).

- [ ] **Step 3: Smoke test — server starts and discovers parsers**

```bash
cd backend && uv run python main.py &
sleep 5
curl -s http://localhost:8000/health || true
kill %1
```

Expected: server starts without exception. `cubebox.parsers` log line shows 3 plugins registered.

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/api/app.py docker-compose.yml
git commit -m "feat(api): discover parser plugins at app startup; add docling-serve compose service"
```

---

### Task 15: Final integration + check

**Files:**
- Run all tests + smoke

- [ ] **Step 1: Full backend test sweep**

```bash
cd backend && uv run pytest tests/ -v
```

Expected: ALL pass.

- [ ] **Step 2: Run lint + type-check**

```bash
cd backend && make check
```

Expected: All green.

- [ ] **Step 3: Manual smoke against real docling-serve (optional)**

If you have docling-serve running locally:

```bash
docker run -d -p 5001:5001 quay.io/docling-project/docling-serve-cpu
cd backend && uv run python main.py &
sleep 5
# Trigger an agent conversation that uses file_read on a small PDF
kill %1
docker stop $(docker ps -q --filter ancestor=quay.io/docling-project/docling-serve-cpu)
```

- [ ] **Step 4: Verify clean git status**

```bash
git status
```

Expected: clean. M6 implementation complete.

---

## Self-Review Notes (planner ran)

- ✅ Spec coverage:
  - FileReadOutput discriminated union (5 kinds) → Task 2
  - FileParser Protocol → Task 3
  - MIME sniff + REJECT → Task 4
  - dedup → Task 5
  - 3 default plugins → Tasks 6, 7, 8, 9
  - ParserRegistry → Task 10
  - Sandbox.file_read → Task 11
  - file_read agent tool registration → Task 12
  - Config schema → Task 13
  - Docker compose + startup discover → Task 14
- ✅ Tool description matches spec §6 verbatim
- ✅ All limit numbers consistent (20K chars, 100MB file, 3MB async threshold, 30s sync, 10min async)
- ✅ async-safe hashing via `asyncio.to_thread` in dedup.py
- ⚠ Docling-serve API request shape (`FileSourceRequest`, exact field names) is best-effort per public docs; implementer should verify against running docling-serve `/docs` page and adjust if needed.
- ⚠ Pytest-asyncio test event loop pattern in `conftest.py` may need adjustment depending on `pyproject.toml` asyncio_mode setting; current cubebox uses pytest-asyncio — confirm style matches existing tests.
