# M6 · file_read 通用工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an agent-facing `file_read` tool that reads files from the sandbox and returns LLM-ready content (markdown / structured cells / unsupported sentinel / unchanged sentinel / error). Parser implementations live in a backend-side `cubeplex.parsers` plugin registry; the default Docling parser delegates to an external `docling-serve` HTTP service so heavy ML deps stay out of backend.

**Architecture:** New `cubeplex.parsers` package owns the `FileParser` Protocol + entry_points-based plugin registry + 3 default plugins (`TextParser`, `NotebookParser`, `DoclingParser`). `Sandbox` abstract base class gains a non-abstract `file_read(path, options)` method that downloads bytes + dispatches via the parser registry + applies conversation-scoped SHA-256 dedup. `SandboxMiddleware` registers a new `file_read` agent tool that calls `sandbox.file_read(...)`.

**Tech Stack:** Python 3.12, FastAPI, httpx (for docling-serve client), python-magic (libmagic wrapper), filetype (libmagic-free fallback), structlog, Pydantic, pytest, pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-04-22-file-read-tool-design.md`

---

## File Structure

### Create

```
backend/cubeplex/parsers/
├─ __init__.py                  # Re-exports
├─ schema.py                    # FileReadOutput union + ParseOptions (page_range + line_range)
├─ protocols.py                 # FileParser Protocol
├─ mime.py                      # libmagic + extension fallback + REJECT lists
├─ dedup.py                     # Redis-backed hash cache; key includes ParseOptions sig
├─ registry.py                  # ParserRegistry (discover + dispatch)
└─ plugins/
   ├─ __init__.py
   ├─ text.py                   # TextParser (UTF-8 + line_range slicing + truncation)
   ├─ notebook.py               # NotebookParser (Jupyter cells)
   └─ docling.py                # DoclingParser (HTTP to docling-serve)

backend/cubeplex/cache/
├─ __init__.py                  # exposes get_redis()
└─ redis.py                     # async Redis client factory (skip if exists)

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
backend/cubeplex/sandbox/base.py            # Add Sandbox.file_read non-abstract method
backend/cubeplex/middleware/sandbox.py      # Register file_read tool with description
backend/cubeplex/config.py                  # Add parsers.docling_serve + redis.url schemas
backend/config.yaml                        # Add parsers: + redis: sections
backend/config.development.yaml            # Add parsers: + redis: sections
backend/config.test.yaml                   # Add parsers: + redis: sections
backend/pyproject.toml                     # Deps (redis>=5.0, python-magic, filetype, fakeredis dev) + entry_points group
docker-compose.yml                         # Add docling-serve service (redis already present)
```

---

## Tasks

### Task 1: Create `cubeplex.parsers` package skeleton

**Files:**
- Create: `backend/cubeplex/parsers/__init__.py`
- Create: `backend/cubeplex/parsers/plugins/__init__.py`
- Create: `backend/tests/parsers/__init__.py`

- [ ] **Step 1: Create directories + empty __init__.py files**

```bash
mkdir -p backend/cubeplex/parsers/plugins
mkdir -p backend/tests/parsers
touch backend/cubeplex/parsers/plugins/__init__.py
touch backend/tests/parsers/__init__.py
```

- [ ] **Step 2: Write package docstring**

```python
# backend/cubeplex/parsers/__init__.py
"""File parser plugin registry shared by file_read tool and future filebox.

See docs/superpowers/specs/2026-04-22-file-read-tool-design.md.
"""
```

- [ ] **Step 3: Verify import**

Run: `cd backend && uv run python -c "import cubeplex.parsers"`
Expected: no error.

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/parsers/ backend/tests/parsers/
git commit -m "chore(parsers): create package skeleton for M6 file_read"
```

---

### Task 2: Define `FileReadOutput` discriminated union + supporting types

**Files:**
- Create: `backend/cubeplex/parsers/schema.py`
- Create: `backend/tests/parsers/test_schema.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/parsers/test_schema.py
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
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/parsers/test_schema.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement schema.py**

```python
# backend/cubeplex/parsers/schema.py
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
    page_range: str | None = None  # PDF/DOCX/PPTX 用；"1-5" or "3"
    line_range: str | None = None  # text/code/log 用；"100-200" or "42"
    language_hint: str | None = None
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/parsers/test_schema.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/parsers/schema.py backend/tests/parsers/test_schema.py
git commit -m "feat(parsers): FileReadOutput discriminated union + ParseOptions"
```

---

### Task 3: Define `FileParser` Protocol

**Files:**
- Create: `backend/cubeplex/parsers/protocols.py`
- Create: `backend/tests/parsers/test_protocols.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/parsers/test_protocols.py
from cubeplex.parsers.protocols import FileParser
from cubeplex.parsers.schema import ParseOptions, TextOutput


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
# backend/cubeplex/parsers/protocols.py
"""FileParser plugin Protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cubeplex.parsers.schema import FileReadOutput, ParseOptions


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
git add backend/cubeplex/parsers/protocols.py backend/tests/parsers/test_protocols.py
git commit -m "feat(parsers): FileParser runtime_checkable Protocol"
```

---

### Task 4: MIME sniff (no REJECT lists — see D22)

**Files:**
- Create: `backend/cubeplex/parsers/mime.py`
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
from cubeplex.parsers.mime import sniff_mime, sniff_mime_async


def test_sniff_mime_detects_pdf_from_bytes() -> None:
    pdf_magic = b"%PDF-1.4\n"
    mime = sniff_mime("/tmp/a.pdf", pdf_magic + b"x" * 100)
    assert mime == "application/pdf"


def test_sniff_mime_falls_back_to_extension() -> None:
    # ASCII content with .py extension
    mime = sniff_mime("/tmp/x.py", b"print('hi')\n")
    # libmagic detects as text/x-python or text/plain; either acceptable
    assert mime.startswith("text/") or mime == "application/x-python"


def test_sniff_mime_returns_octet_stream_for_unknown() -> None:
    # Random bytes with no recognizable magic and no useful extension
    mime = sniff_mime("/tmp/x", b"\x01\x02\x03\x04random")
    # libmagic may detect as text/plain or octet-stream; either is acceptable
    assert mime in {"application/octet-stream", "text/plain"}


@pytest.mark.asyncio
async def test_sniff_mime_async_returns_same_result() -> None:
    pdf_magic = b"%PDF-1.4\nstuff"
    mime = await sniff_mime_async("/tmp/a.pdf", pdf_magic)
    assert mime == "application/pdf"
```

(Add `import pytest` to the top of the file.)

- [ ] **Step 3: Run, verify fail**

Run: `cd backend && uv run pytest tests/parsers/test_mime.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement mime.py**

```python
# backend/cubeplex/parsers/mime.py
"""MIME sniffing helpers for file_read.

Note: there is intentionally NO hardcoded REJECT list. "Unsupported"
status is determined by whether any registered FileParser plugin claims
the MIME type. See spec D22 for rationale.
"""

from __future__ import annotations

import asyncio
import mimetypes

import filetype
import magic


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
```

- [ ] **Step 5: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/parsers/test_mime.py -v`
Expected: PASS (4 tests). If a test fails because libmagic isn't installed, install it on the host.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/parsers/mime.py backend/tests/parsers/test_mime.py backend/pyproject.toml backend/uv.lock
git commit -m "feat(parsers): MIME sniffing helpers (libmagic + filetype + ext fallback)"
```

---

### Task 5: Redis-backed dedup cache + async client setup

**Files:**
- Create: `backend/cubeplex/cache/__init__.py`
- Create: `backend/cubeplex/cache/redis.py` (skip if `cubeplex.cache` already exists; reuse existing client)
- Create: `backend/cubeplex/parsers/dedup.py`
- Create: `backend/tests/parsers/test_dedup.py`
- Modify: `backend/pyproject.toml` (add `redis>=5.0`)

**Note on prior art**: cubeplex already has Redis fully wired up (introduced by the recent streaming/resumable-runs feature):
- `redis>=5.2.0` is a direct dep in `backend/pyproject.toml` ✓
- `Redis.from_url(...)` is constructed in `backend/cubeplex/api/app.py:48-77` lifespan
- Stored in `_app.state.redis`; routes access via `raw_request.app.state.redis`
- Config: `streaming.redis_url` and `streaming.redis_key_prefix` in `config.yaml:191-194`
- `backend/cubeplex/streams/run_events.py` is the primary consumer

What's MISSING: a way for non-route code (like `dedup.py`, called from `Sandbox.file_read` deep in the parser dispatch) to reach the Redis client without dragging app context through. We add a tiny `cubeplex.cache` module with `get_redis()` / `set_redis()` that the lifespan registers after constructing the client. Dedup imports `from cubeplex.cache import get_redis`. Streaming code keeps using `app.state.redis` (don't touch).

Verify before starting:
```bash
grep -E '"redis>=' backend/pyproject.toml          # should print redis dep
grep -n 'streaming.redis_url' backend/config.yaml   # should print line ~192
grep -n 'app.state.redis' backend/cubeplex/api/app.py # should print lines around 64
```

- [ ] **Step 1: Add fakeredis dev dep (only new dep needed)**

```bash
cd backend && uv add --dev fakeredis
```

(`redis>=5.2.0` is already installed; do NOT re-add.)

- [ ] **Step 2: Create `cubeplex.cache` thin accessor module**

```python
# backend/cubeplex/cache/__init__.py
"""Module-level accessor for the shared async Redis client.

cubeplex already constructs a single async Redis client in app lifespan
(see api/app.py for the streaming feature). This module exposes that
SAME client to non-route code (parsers/dedup, future filebox indexer)
that doesn't have a Request handle.

Lifespan calls `set_redis(client)` after building the connection;
consumers call `get_redis()`. No second connection is opened.
"""

from __future__ import annotations

import redis.asyncio as redis_asyncio

_client: redis_asyncio.Redis | None = None


def set_redis(client: redis_asyncio.Redis) -> None:
    """Register the shared async Redis client (called by app lifespan)."""
    global _client
    _client = client


def get_redis() -> redis_asyncio.Redis:
    """Return the registered shared async Redis client.

    Raises RuntimeError if called before lifespan registered the client.
    """
    if _client is None:
        raise RuntimeError(
            "cubeplex.cache.get_redis() called before lifespan set the client. "
            "Either app startup is incomplete or test fixture didn't inject one."
        )
    return _client


def reset_for_tests() -> None:
    """Tests call this between cases to clear the registered client."""
    global _client
    _client = None


__all__ = ["get_redis", "reset_for_tests", "set_redis"]
```

- [ ] **Step 3: Wire lifespan to register the shared client**

In `backend/cubeplex/api/app.py`, find the line `_app.state.redis = redis_client` (around line 64). Immediately after it, add:

```python
        from cubeplex.cache import set_redis as _set_shared_redis
        _set_shared_redis(redis_client)
```

This shares the SAME client object via the module-level accessor. No second connection, no separate config.

- [ ] **Step 4: Smoke test**

```bash
cd backend && uv run python -c "from cubeplex.cache import get_redis, set_redis; print('cache module imports OK')"
```
Expected: `cache module imports OK`.

- [ ] **Step 4: Write failing tests for dedup.py (using fakeredis)**

```python
# backend/tests/parsers/test_dedup.py
from uuid import uuid4

import fakeredis.aioredis
import pytest

from cubeplex.parsers.dedup import check, hash_bytes, update
from cubeplex.parsers.schema import ParseOptions


@pytest.fixture
async def fake_redis():
    """Inject a fakeredis instance via cubeplex.cache.set_redis."""
    from cubeplex.cache import reset_for_tests, set_redis
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    set_redis(fake)
    yield fake
    await fake.flushall()
    reset_for_tests()


@pytest.mark.asyncio
async def test_hash_bytes_returns_sha256_hex() -> None:
    digest = await hash_bytes(b"hello")
    assert digest == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


@pytest.mark.asyncio
async def test_check_returns_false_when_empty(fake_redis) -> None:
    conv = uuid4()
    assert await check(conv, "/p", ParseOptions(), "abc") is False


@pytest.mark.asyncio
async def test_update_then_check_matches(fake_redis) -> None:
    conv = uuid4()
    await update(conv, "/p", ParseOptions(), "abc")
    assert await check(conv, "/p", ParseOptions(), "abc") is True
    assert await check(conv, "/p", ParseOptions(), "different") is False


@pytest.mark.asyncio
async def test_check_isolates_per_conversation(fake_redis) -> None:
    a, b = uuid4(), uuid4()
    await update(a, "/p", ParseOptions(), "abc")
    assert await check(b, "/p", ParseOptions(), "abc") is False


@pytest.mark.asyncio
async def test_check_isolates_per_page_range(fake_redis) -> None:
    """Different page_range = different cache slot."""
    conv = uuid4()
    await update(conv, "/p", ParseOptions(page_range="1-5"), "abc")
    assert await check(conv, "/p", ParseOptions(page_range="6-10"), "abc") is False
    assert await check(conv, "/p", ParseOptions(page_range="1-5"), "abc") is True


@pytest.mark.asyncio
async def test_check_isolates_per_line_range(fake_redis) -> None:
    """Different line_range = different cache slot."""
    conv = uuid4()
    await update(conv, "/p", ParseOptions(line_range="1-100"), "abc")
    assert await check(conv, "/p", ParseOptions(line_range="200-300"), "abc") is False


@pytest.mark.asyncio
async def test_ttl_set_on_update(fake_redis) -> None:
    """Update sets TTL so cache eventually expires."""
    conv = uuid4()
    await update(conv, "/p", ParseOptions(), "abc")
    # fakeredis exposes ttl
    keys = await fake_redis.keys("parsers:dedup:v1:*")
    assert len(keys) == 1
    ttl = await fake_redis.ttl(keys[0])
    assert ttl > 0  # TTL is set


@pytest.mark.asyncio
async def test_hash_bytes_offloads_to_thread() -> None:
    """Verifies async-safe for large inputs."""
    big = b"x" * (10 * 1024 * 1024)  # 10 MB
    digest = await hash_bytes(big)
    assert len(digest) == 64
```

- [ ] **Step 5: Run, verify fail**

Run: `cd backend && uv run pytest tests/parsers/test_dedup.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 6: Implement dedup.py**

```python
# backend/cubeplex/parsers/dedup.py
"""Redis-backed conversation-scoped SHA-256 file_state dedup cache.

Cache key: (conversation_id, path, options-signature). The options-signature
includes page_range + line_range so different range-slices land in different
cache slots and don't incorrectly return UnchangedOutput.

TTL: 6 hours of inactivity → auto-expire (Redis-managed; conversation has
no explicit "end" event in cubeplex).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from uuid import UUID

from cubeplex.cache import get_redis
from cubeplex.parsers.schema import ParseOptions

DEDUP_TTL_SECONDS = 6 * 3600
KEY_PREFIX = "parsers:dedup:v1:"


async def hash_bytes(data: bytes) -> str:
    """Compute SHA-256 hex; offload to thread (CPU-bound for large inputs)."""
    return await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest())


def _options_signature(options: ParseOptions) -> str:
    """JSON-serialize range params (sorted) so equivalent options → same key."""
    return json.dumps(
        {"page_range": options.page_range, "line_range": options.line_range},
        sort_keys=True,
    )


def _key(conversation_id: UUID, path: str, options: ParseOptions) -> str:
    return f"{KEY_PREFIX}{conversation_id}:{path}:{_options_signature(options)}"


async def check(
    conversation_id: UUID,
    path: str,
    options: ParseOptions,
    digest: str,
) -> bool:
    """True if digest matches Redis-cached value (→ caller emits UnchangedOutput)."""
    redis = get_redis()
    cached = await redis.get(_key(conversation_id, path, options))
    if cached is None:
        return False
    return cached == digest if isinstance(cached, str) else cached == digest.encode()


async def update(
    conversation_id: UUID,
    path: str,
    options: ParseOptions,
    digest: str,
) -> None:
    redis = get_redis()
    await redis.set(
        _key(conversation_id, path, options),
        digest,
        ex=DEDUP_TTL_SECONDS,
    )
```

- [ ] **Step 7: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/parsers/test_dedup.py -v`
Expected: PASS (8 tests).

- [ ] **Step 8: Commit**

```bash
git add backend/cubeplex/cache/ backend/cubeplex/parsers/dedup.py backend/tests/parsers/test_dedup.py backend/pyproject.toml backend/uv.lock backend/config*.yaml
git commit -m "feat(parsers): Redis-backed dedup cache with ParseOptions signature key"
```

---

### Task 6: `TextParser` plugin

**Files:**
- Create: `backend/cubeplex/parsers/plugins/text.py`
- Create: `backend/tests/parsers/test_text_parser.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/parsers/test_text_parser.py
import pytest

from cubeplex.parsers.plugins.text import TextParser
from cubeplex.parsers.protocols import FileParser
from cubeplex.parsers.schema import ParseOptions, TextOutput


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


@pytest.mark.asyncio
async def test_line_range_returns_specific_lines() -> None:
    """line_range='2-4' returns lines 2 through 4 (1-indexed, inclusive)."""
    p = TextParser()
    body = "\n".join(f"line{i}" for i in range(1, 11)).encode()
    out = await p.parse(body, mime="text/plain", options=ParseOptions(line_range="2-4"))
    assert out.content == "line2\nline3\nline4"
    assert out.metadata["lines_returned"] == "2-4"
    assert out.metadata["total_lines"] == 10


@pytest.mark.asyncio
async def test_line_range_single_line() -> None:
    p = TextParser()
    body = "\n".join(f"line{i}" for i in range(1, 6)).encode()
    out = await p.parse(body, mime="text/plain", options=ParseOptions(line_range="3"))
    assert out.content == "line3"
    assert out.metadata["lines_returned"] == "3-3"


@pytest.mark.asyncio
async def test_line_range_clamps_to_file_length() -> None:
    """Out-of-range end is clamped silently."""
    p = TextParser()
    body = "\n".join(f"line{i}" for i in range(1, 6)).encode()
    out = await p.parse(body, mime="text/plain", options=ParseOptions(line_range="3-100"))
    assert out.content == "line3\nline4\nline5"
    assert out.metadata["lines_returned"] == "3-5"


@pytest.mark.asyncio
async def test_line_range_open_end() -> None:
    """'3-' = from line 3 to end."""
    p = TextParser()
    body = "\n".join(f"line{i}" for i in range(1, 6)).encode()
    out = await p.parse(body, mime="text/plain", options=ParseOptions(line_range="3-"))
    assert out.content == "line3\nline4\nline5"
    assert out.metadata["lines_returned"] == "3-5"


@pytest.mark.asyncio
async def test_line_range_negative_returns_last_n() -> None:
    """'-3' = last 3 lines (tail-style)."""
    p = TextParser()
    body = "\n".join(f"line{i}" for i in range(1, 11)).encode()
    out = await p.parse(body, mime="text/plain", options=ParseOptions(line_range="-3"))
    assert out.content == "line8\nline9\nline10"
    assert out.metadata["lines_returned"] == "8-10"


@pytest.mark.asyncio
async def test_line_range_negative_more_than_file_returns_all() -> None:
    """'-100' on a 5-line file returns all 5 lines."""
    p = TextParser()
    body = "\n".join(f"line{i}" for i in range(1, 6)).encode()
    out = await p.parse(body, mime="text/plain", options=ParseOptions(line_range="-100"))
    assert out.metadata["lines_returned"] == "1-5"


@pytest.mark.asyncio
async def test_no_line_range_returns_all_with_truncation_hint() -> None:
    """Without line_range and content > 20K → truncated + hint to use line_range."""
    p = TextParser()
    body = ("line\n" * 5000).encode()  # 25k chars total
    out = await p.parse(body, mime="text/plain", options=ParseOptions())
    assert out.truncated is True
    assert "line_range" in out.metadata.get("hint", "")


@pytest.mark.asyncio
async def test_truncation_emits_next_line_to_read() -> None:
    """When truncated, metadata.next_line_to_read tells agent where to resume."""
    p = TextParser()
    # Each line is ~20 chars so 1500 lines ~= 30k chars (will truncate ~= line 1000)
    body = ("x" * 19 + "\n").join(f"" for _ in range(1500)).encode()
    out = await p.parse(body, mime="text/plain", options=ParseOptions())
    assert out.truncated is True
    assert "next_line_to_read" in out.metadata
    assert out.metadata["next_line_to_read"] > 1
    # next_line_to_read should equal end-of-returned-range + 1
    returned = out.metadata["lines_returned"]
    end = int(returned.split("-")[1])
    assert out.metadata["next_line_to_read"] == end + 1
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/parsers/test_text_parser.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement TextParser**

```python
# backend/cubeplex/parsers/plugins/text.py
"""TextParser: UTF-8 decode for code/config/text files."""

from __future__ import annotations

from cubeplex.parsers.schema import ParseOptions, TextOutput

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

        # Split into lines preserving line endings
        all_lines = text.splitlines(keepends=True)
        total_lines = len(all_lines)

        # Apply line_range slice (1-indexed inclusive); ignore page_range.
        start_idx, end_idx = self._parse_line_range(options.line_range, total_lines)
        sliced_lines = all_lines[start_idx:end_idx]
        sliced = "".join(sliced_lines)

        truncated = False
        last_line_returned = end_idx  # 1-indexed end (inclusive)
        metadata: dict[str, object] = {
            "parser": "text",
            "total_lines": total_lines,
        }
        if decode_fallback:
            metadata["decode_fallback"] = decode_fallback

        if len(sliced) > MAX_CONTENT_CHARS:
            # Truncate by chars, then back-compute how many full lines fit
            sliced = sliced[:MAX_CONTENT_CHARS]
            truncated = True
            # Count complete lines (those ending in \n) within the truncated text
            lines_kept = sliced.count("\n")
            # Edge case: if no newline in truncated text but we kept content, count as 1 partial line
            if lines_kept == 0 and sliced:
                lines_kept = 1
            last_line_returned = start_idx + lines_kept   # 1-indexed last fully-included line
            metadata["truncated_at_char"] = MAX_CONTENT_CHARS
            metadata["next_line_to_read"] = last_line_returned + 1
            if options.line_range is None:
                metadata["hint"] = "content truncated; use line_range to navigate"

        metadata["lines_returned"] = f"{start_idx + 1}-{last_line_returned}"

        return TextOutput(
            path="<set-by-caller>",
            mime=mime,
            content=sliced,
            size_bytes=size,
            truncated=truncated,
            metadata=metadata,
        )

    @staticmethod
    def _parse_line_range(spec: str | None, total_lines: int) -> tuple[int, int]:
        """Parse line_range syntax → (start_index_0based, end_index_exclusive).

        Supported syntaxes (1-indexed input):
          "42"       → line 42 only
          "100-200"  → lines 100 through 200
          "100-"     → from line 100 to end (sed-style)
          "-50"      → last 50 lines (tail-style)

        Returns (0, total_lines) on None or invalid input. End clamped to total_lines.
        """
        if not spec:
            return 0, total_lines
        try:
            if spec.startswith("-"):
                # "-50" → last N lines
                n = int(spec[1:])
                if n <= 0:
                    return 0, total_lines
                start = max(total_lines - n, 0)
                return start, total_lines
            if spec.endswith("-"):
                # "100-" → from N to end
                start = max(int(spec[:-1]), 1) - 1
                start = min(start, total_lines)
                return start, total_lines
            if "-" in spec:
                # "100-200" → range
                a, b = spec.split("-", 1)
                start = max(int(a), 1) - 1
                end = min(int(b), total_lines)
                return start, max(end, start)
            # "42" → single line
            n = max(int(spec), 1) - 1
            return n, min(n + 1, total_lines)
        except (ValueError, TypeError):
            return 0, total_lines
```

NOTE: `path` is set to placeholder; the registry's `dispatch` overwrites with the actual path before returning. (Alternative: pass `path` into `parse`. Stick with current Protocol signature; registry overwrites.)

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/parsers/test_text_parser.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/parsers/plugins/text.py backend/tests/parsers/test_text_parser.py
git commit -m "feat(parsers): TextParser with full line_range syntax + next_line_to_read on truncation"
```

---

### Task 7: `NotebookParser` plugin

**Files:**
- Create: `backend/cubeplex/parsers/plugins/notebook.py`
- Create: `backend/tests/parsers/test_notebook_parser.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/parsers/test_notebook_parser.py
import json

import pytest

from cubeplex.parsers.plugins.notebook import NotebookParser
from cubeplex.parsers.protocols import FileParser
from cubeplex.parsers.schema import NotebookOutput, ParseOptions


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
async def test_truncates_when_exceeds_20k_with_next_cell_index() -> None:
    nb = {
        "cells": [
            {"cell_type": "markdown", "source": "x" * 25_000},
            {"cell_type": "code", "source": "y", "outputs": []},
            {"cell_type": "code", "source": "z", "outputs": []},
        ]
    }
    p = NotebookParser()
    out = await p.parse(
        json.dumps(nb).encode(),
        mime="application/x-ipynb+json",
        options=ParseOptions(),
    )
    # First big cell included, second + third omitted
    assert out.metadata["truncated_cells"] == 2
    assert out.metadata["cells_returned"] == 1
    assert out.metadata["next_cell_index"] == 2  # 1-indexed
    assert out.metadata["total_cells"] == 3
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/parsers/test_notebook_parser.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement NotebookParser**

```python
# backend/cubeplex/parsers/plugins/notebook.py
"""NotebookParser: parse Jupyter .ipynb into structured cells."""

from __future__ import annotations

import json
from typing import Any

from cubeplex.parsers.schema import NotebookCell, NotebookOutput, ParseOptions

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
            "cells_returned": len(result_cells),
        }
        if truncated_cells > 0:
            metadata["truncated_cells"] = truncated_cells
            metadata["next_cell_index"] = len(result_cells) + 1  # 1-indexed

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
git add backend/cubeplex/parsers/plugins/notebook.py backend/tests/parsers/test_notebook_parser.py
git commit -m "feat(parsers): NotebookParser preserves Jupyter cell structure"
```

---

### Task 8: `DoclingParser` — sync path + http client

**Files:**
- Create: `backend/cubeplex/parsers/plugins/docling.py`
- Create: `backend/tests/parsers/test_docling_parser.py`

- [ ] **Step 1: Write failing test for sync path with httpx mock**

```python
# backend/tests/parsers/test_docling_parser.py
import base64
import json
from unittest.mock import patch

import httpx
import pytest

from cubeplex.parsers.plugins.docling import DoclingParser
from cubeplex.parsers.protocols import FileParser
from cubeplex.parsers.schema import ErrorOutput, ParseOptions, TextOutput


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
    # No page markers in this fake response → hint instead of next_page_to_read
    assert "hint" in out.metadata
    assert "page_range" in out.metadata["hint"]


@pytest.mark.asyncio
async def test_sync_path_extracts_last_page_from_markers() -> None:
    """When docling output contains <!-- page N --> markers, metadata exposes them."""
    md = "intro\n<!-- page 1 -->\n" + "filler\n" * 1000 + "<!-- page 7 -->\nmore content\n" + "filler\n" * 3000 + "<!-- page 12 -->\nlate stuff"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"document": {"md_content": md}})

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
    # Last page marker found within the truncated content
    assert "last_page_returned" in out.metadata
    assert "next_page_to_read" in out.metadata
    assert out.metadata["next_page_to_read"] == out.metadata["last_page_returned"] + 1
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
# backend/cubeplex/parsers/plugins/docling.py
"""DoclingParser: HTTP client to docling-serve."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import httpx

from cubeplex.parsers.schema import ErrorOutput, FileReadOutput, ParseOptions, TextOutput

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
            truncated_md = md[:MAX_CONTENT_CHARS]
            truncated = True
            metadata["truncated_at_char"] = MAX_CONTENT_CHARS

            # Best-effort: try to extract last page number visible in truncated md.
            # Docling can emit page markers like "<!-- page 5 -->" or h1/h2 with page meta.
            # We try common patterns; if none match, the agent gets just truncated_at_char.
            last_page = self._extract_last_page(truncated_md)
            if last_page is not None:
                metadata["last_page_returned"] = last_page
                metadata["next_page_to_read"] = last_page + 1
            else:
                metadata["hint"] = "use page_range to read later sections"
            md = truncated_md

        return TextOutput(
            path="<set-by-caller>",
            mime=mime,
            content=md,
            size_bytes=len(content),
            truncated=truncated,
            metadata=metadata,
        )

    @staticmethod
    def _extract_last_page(md: str) -> int | None:
        """Best-effort scan for the last page marker in docling markdown.

        Patterns checked (last occurrence wins):
          - HTML comments:  <!-- page N -->
          - Heading line:   ## Page N
          - PageBreak:      <!-- PageBreak: N -->

        Returns None if no marker found (caller falls back to char-only truncation info).
        """
        import re
        patterns = [
            re.compile(r"<!--\s*page[:\s]+(\d+)\s*-->", re.IGNORECASE),
            re.compile(r"<!--\s*PageBreak[:\s]+(\d+)\s*-->", re.IGNORECASE),
            re.compile(r"^#+\s+Page\s+(\d+)\s*$", re.MULTILINE | re.IGNORECASE),
        ]
        last_page: int | None = None
        for pat in patterns:
            for m in pat.finditer(md):
                try:
                    last_page = int(m.group(1))
                except (ValueError, IndexError):
                    pass
        return last_page
```

- [ ] **Step 5: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/parsers/test_docling_parser.py -v`
Expected: 3 tests PASS, async-path tests not yet present.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/parsers/plugins/docling.py backend/tests/parsers/test_docling_parser.py
git commit -m "feat(parsers): DoclingParser sync HTTP path + httpx mock tests"
```

---

### Task 9: `DoclingParser` async path + polling

**Files:**
- Modify: `backend/cubeplex/parsers/plugins/docling.py`
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
git add backend/cubeplex/parsers/plugins/docling.py backend/tests/parsers/test_docling_parser.py
git commit -m "feat(parsers): DoclingParser async submit + poll path with COMPLETED/FAILED handling"
```

---

### Task 10: `ParserRegistry` (discover + dispatch + REJECT precheck)

**Files:**
- Create: `backend/cubeplex/parsers/registry.py`
- Modify: `backend/cubeplex/parsers/__init__.py`
- Create: `backend/tests/parsers/test_registry.py`
- Modify: `backend/pyproject.toml` (add entry_points group)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/parsers/test_registry.py
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cubeplex.parsers.registry import ParserRegistry, get_parser_registry, reset_parser_registry_for_tests
from cubeplex.parsers.schema import (
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
async def test_dispatch_unsupported_when_no_plugin_matches() -> None:
    """No plugin claims video/* → returns unsupported with format-aware hint."""
    import fakeredis.aioredis
    from cubeplex.cache import set_redis
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    set_redis(fake)

    sandbox = MagicMock()
    # Plausible MP4 magic so libmagic detects video/mp4
    sandbox._download_one = AsyncMock(
        return_value=b"\x00\x00\x00\x20ftypisom" + b"\x00" * 200
    )
    reg = ParserRegistry()
    await reg.discover()
    out = await reg.dispatch(
        sandbox=sandbox, path="/tmp/movie.mp4",
        options=ParseOptions(), conversation_id=uuid4(),
    )
    assert isinstance(out, UnsupportedOutput)
    assert "no parser registered" in out.reason
    # Hint mentions video transcription
    assert out.hint is not None
    assert "video" in out.hint.lower()


@pytest.mark.asyncio
async def test_dispatch_unsupported_archive_hint() -> None:
    """Archives suggest extract-then-read flow."""
    import fakeredis.aioredis
    from cubeplex.cache import set_redis
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    set_redis(fake)

    sandbox = MagicMock()
    sandbox._download_one = AsyncMock(
        return_value=b"PK\x03\x04" + b"\x00" * 200  # ZIP magic
    )
    reg = ParserRegistry()
    await reg.discover()
    out = await reg.dispatch(
        sandbox=sandbox, path="/tmp/data.zip",
        options=ParseOptions(), conversation_id=uuid4(),
    )
    assert isinstance(out, UnsupportedOutput)
    assert out.hint is not None
    assert "extract" in out.hint.lower() or "unzip" in out.hint.lower()


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
    """Same content + same options + same conv → second call returns UnchangedOutput."""
    import fakeredis.aioredis
    from cubeplex.cache import set_redis
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    set_redis(fake)

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
async def test_dispatch_different_line_range_does_not_unchanged() -> None:
    """Different line_range = different cache key = re-parses."""
    import fakeredis.aioredis
    from cubeplex.cache import set_redis
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    set_redis(fake)

    sandbox = MagicMock()
    body = "\n".join(f"line{i}" for i in range(1, 21)).encode()
    sandbox._download_one = AsyncMock(return_value=body)
    reg = ParserRegistry()
    await reg.discover()
    conv = uuid4()

    first = await reg.dispatch(
        sandbox=sandbox, path="/tmp/log.txt",
        options=ParseOptions(line_range="1-5"), conversation_id=conv,
    )
    assert isinstance(first, TextOutput)

    second = await reg.dispatch(
        sandbox=sandbox, path="/tmp/log.txt",
        options=ParseOptions(line_range="10-15"), conversation_id=conv,
    )
    # Different line_range → different cache slot → re-parses, returns TextOutput, not Unchanged
    assert isinstance(second, TextOutput)
    assert "line10" in second.content


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
    # Parse-format error: not retryable (re-parsing same bytes won't help).
    assert out.retryable is False


@pytest.mark.asyncio
async def test_dispatch_marks_transient_errors_retryable() -> None:
    """httpx.TransportError / timeout → retryable=True so agents can retry."""
    import httpx

    class FlakyParser:
        name = "flaky"
        priority = 50
        mime_types = ("application/pdf",)
        extensions = ("pdf",)

        async def parse(self, content, *, mime, options):
            raise httpx.ConnectError("connection refused")

    sandbox = MagicMock()
    sandbox._download_one = AsyncMock(return_value=b"%PDF-1.4\n...")
    reg = ParserRegistry()
    reg._parsers = [FlakyParser()]  # type: ignore[list-item]
    out = await reg.dispatch(
        sandbox=sandbox, path="/tmp/x.pdf", options=ParseOptions(), conversation_id=None
    )
    assert isinstance(out, ErrorOutput)
    assert out.retryable is True


@pytest.mark.asyncio
async def test_dispatch_does_not_cache_unsupported_so_retry_works() -> None:
    """Unsupported result must NOT update dedup; user can install a plugin and retry."""
    import fakeredis.aioredis
    from cubeplex.cache import set_redis
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    set_redis(fake)

    sandbox = MagicMock()
    sandbox._download_one = AsyncMock(return_value=b"\x00\x01binary")
    reg = ParserRegistry()
    reg._parsers = []  # no plugins → unsupported
    conv = uuid4()

    first = await reg.dispatch(
        sandbox=sandbox, path="/tmp/x.bin",
        options=ParseOptions(), conversation_id=conv,
    )
    assert isinstance(first, UnsupportedOutput)

    # Install a plugin and retry — must NOT short-circuit as UnchangedOutput.
    class BinParser:
        name = "bin"
        priority = 10
        mime_types = ("application/octet-stream",)
        extensions = ("bin",)

        async def parse(self, content, *, mime, options):
            return TextOutput(path="", content="parsed", line_count=1, truncated=False)

    reg._parsers = [BinParser()]  # type: ignore[list-item]
    second = await reg.dispatch(
        sandbox=sandbox, path="/tmp/x.bin",
        options=ParseOptions(), conversation_id=conv,
    )
    assert isinstance(second, TextOutput), "unsupported must not be cached"


@pytest.mark.asyncio
async def test_dispatch_does_not_cache_error_so_retry_works() -> None:
    """ErrorOutput must NOT update dedup; transient failures should be retryable end-to-end."""
    import fakeredis.aioredis
    import httpx
    from cubeplex.cache import set_redis
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    set_redis(fake)

    call_count = 0

    class RecoveringParser:
        name = "recovering"
        priority = 50
        mime_types = ("application/pdf",)
        extensions = ("pdf",)

        async def parse(self, content, *, mime, options):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("transient outage")
            return TextOutput(path="", content="ok", line_count=1, truncated=False)

    sandbox = MagicMock()
    sandbox._download_one = AsyncMock(return_value=b"%PDF-1.4\n...")
    reg = ParserRegistry()
    reg._parsers = [RecoveringParser()]  # type: ignore[list-item]
    conv = uuid4()

    first = await reg.dispatch(
        sandbox=sandbox, path="/tmp/x.pdf",
        options=ParseOptions(), conversation_id=conv,
    )
    assert isinstance(first, ErrorOutput)
    assert first.retryable is True

    second = await reg.dispatch(
        sandbox=sandbox, path="/tmp/x.pdf",
        options=ParseOptions(), conversation_id=conv,
    )
    assert isinstance(second, TextOutput), "error must not be cached"
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/parsers/test_registry.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Add entry_points to pyproject.toml**

Edit `backend/pyproject.toml` to add (under `[project.entry-points]` section, create if absent):

```toml
[project.entry-points."cubeplex.parsers"]
text = "cubeplex.parsers.plugins.text:TextParser"
notebook = "cubeplex.parsers.plugins.notebook:NotebookParser"
docling = "cubeplex.parsers.plugins.docling:DoclingParser"
```

- [ ] **Step 4: Implement registry.py**

```python
# backend/cubeplex/parsers/registry.py
"""ParserRegistry: discover plugins via entry_points + dispatch by MIME."""

from __future__ import annotations

import asyncio
import importlib.metadata
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from cubeplex.parsers import dedup
from cubeplex.parsers.mime import sniff_mime_async
from cubeplex.parsers.protocols import FileParser
from cubeplex.parsers.schema import (
    ErrorOutput,
    FileReadOutput,
    ParseOptions,
    UnchangedOutput,
    UnsupportedOutput,
)

logger = logging.getLogger(__name__)

GROUP = "cubeplex.parsers"
MAX_FILE_BYTES = 100 * 1024 * 1024


def _is_retryable_exception(exc: BaseException) -> bool:
    """Classify parser exceptions: transient faults are retryable.

    Agents should retry network/timeout errors; parse-format errors should not
    be retried since the file content itself is the problem.
    """
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, httpx.TransportError):  # covers ConnectError, ReadTimeout, etc.
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    return False


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
        from cubeplex.config import config  # lazy import to avoid cycles
        from cubeplex.parsers.plugins.docling import DoclingParser

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

        # 2. size precheck (backend resource protection, format-agnostic)
        if size > MAX_FILE_BYTES:
            return UnsupportedOutput(
                path=path, mime="application/octet-stream", size_bytes=size,
                reason="file too large (100MB limit)",
                hint="try reading specific pages with page_range or specific lines with line_range",
            )

        # 3. MIME sniff
        mime = await sniff_mime_async(path, content)

        # 4. dedup check only (write is deferred until after a successful parse so
        # transient failures are not cached as "already read").
        digest: str | None = None
        if conversation_id is not None:
            try:
                digest = await dedup.hash_bytes(content)
                if await dedup.check(conversation_id, path, options, digest):
                    return UnchangedOutput(path=path)
            except Exception as exc:
                # Redis unreachable → fall through (cache miss treatment)
                logger.warning("dedup cache unavailable, proceeding without: %s", exc)
                digest = None

        # 5. resolve plugin
        ext = Path(path).suffix.lstrip(".").lower()
        parser = self.resolve(mime=mime, ext=ext)
        if parser is None:
            # No plugin claims this MIME. CE doesn't maintain a hardcoded
            # REJECT list (see spec D22) — extensibility means future plugins
            # can claim ANY format. Return unsupported with format-aware hint.
            # Do NOT update dedup: user may install a plugin and retry.
            return UnsupportedOutput(
                path=path, mime=mime, size_bytes=size,
                reason=f"no parser registered for mime={mime}",
                hint=self._unsupported_hint(mime, ext),
            )

        # 6. parse
        try:
            out = await parser.parse(content, mime=mime, options=options)
        except Exception as exc:
            logger.exception("parser %s failed on %s", type(parser).__name__, path)
            # Do NOT update dedup on failure — allow retry. Classify transient
            # failures (network/timeout) as retryable so agents can recover.
            return ErrorOutput(
                path=path, error=str(exc), retryable=_is_retryable_exception(exc)
            )

        # 7. dedup update only after a successful parse result
        if conversation_id is not None and digest is not None:
            try:
                await dedup.update(conversation_id, path, options, digest)
            except Exception as exc:
                # Non-fatal: result is already computed; we just lose dedup benefit.
                logger.warning("dedup update failed, continuing: %s", exc)

        # Overwrite the placeholder path; preserve all other fields
        out_dict = out.model_dump()
        out_dict["path"] = path
        return type(out).model_validate(out_dict)

    @staticmethod
    def _unsupported_hint(mime: str, ext: str) -> str | None:
        """Format-family-aware hint for the no-parser-matched case."""
        if mime.startswith("video/"):
            return "video transcription requires a parser plugin (none installed)"
        if mime.startswith("audio/"):
            return "audio transcription requires a parser plugin (none installed)"
        archive_mimes = {
            "application/zip", "application/x-tar", "application/gzip",
            "application/x-bzip2", "application/x-7z-compressed",
            "application/x-rar-compressed", "application/x-xz",
        }
        archive_exts = {"zip", "tar", "gz", "bz2", "rar", "7z", "tgz", "xz"}
        if mime in archive_mimes or ext in archive_exts:
            return 'extract first via execute("unzip <file>") then file_read on contents'
        if ext in {"exe", "so", "dll", "dylib", "bin"}:
            return "binary executable; install a metadata parser plugin if needed"
        return None


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

Replace `backend/cubeplex/parsers/__init__.py`:

```python
"""File parser plugin registry shared by file_read tool and future filebox."""

from cubeplex.parsers.protocols import FileParser
from cubeplex.parsers.registry import (
    ParserRegistry,
    get_parser_registry,
    reset_parser_registry_for_tests,
)
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
git add backend/cubeplex/parsers/registry.py backend/cubeplex/parsers/__init__.py backend/tests/parsers/test_registry.py backend/pyproject.toml backend/uv.lock
git commit -m "feat(parsers): ParserRegistry discover + dispatch with REJECT/dedup precheck"
```

---

### Task 11: `Sandbox.file_read` non-abstract method

**Files:**
- Modify: `backend/cubeplex/sandbox/base.py`
- Create: `backend/tests/parsers/test_sandbox_file_read.py`

- [ ] **Step 1: Write failing integration test**

```python
# backend/tests/parsers/test_sandbox_file_read.py
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cubeplex.parsers.schema import ParseOptions, TextOutput
from cubeplex.sandbox.base import Sandbox


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

Edit `backend/cubeplex/sandbox/base.py`. After existing class methods:

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
        cubeplex.parsers registry. Subclasses may override (future Sandbox
        implementations with native parsing may call their own API).
        """
        from cubeplex.parsers import ParseOptions, get_parser_registry

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
    from cubeplex.parsers import FileReadOutput, ParseOptions
```

NOTE: registry must be `discover()`-ed before `file_read` is called. App startup will do this in Task 14.

- [ ] **Step 4: Bootstrap registry in test conftest**

Add to `backend/tests/parsers/conftest.py` (create if missing):

```python
import pytest

from cubeplex.parsers import get_parser_registry, reset_parser_registry_for_tests


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
git add backend/cubeplex/sandbox/base.py backend/tests/parsers/test_sandbox_file_read.py backend/tests/parsers/conftest.py
git commit -m "feat(sandbox): add Sandbox.file_read non-abstract method dispatching to parser registry"
```

---

### Task 12: Register `file_read` agent tool in SandboxMiddleware

**Files:**
- Modify: `backend/cubeplex/middleware/sandbox.py`
- Create: `backend/tests/middleware/test_sandbox_file_read_tool.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/middleware/test_sandbox_file_read_tool.py
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cubeplex.middleware.sandbox import _create_file_read_tool
from cubeplex.parsers.schema import TextOutput


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

Append to `backend/cubeplex/middleware/sandbox.py`:

```python
from uuid import UUID

from cubeplex.parsers import ParseOptions
from pydantic import BaseModel, Field as PField


class _FileReadArgs(BaseModel):
    path: str = PField(description="Absolute path inside the sandbox to the file to read.")
    page_range: str | None = PField(
        default=None,
        description=(
            "Optional 1-indexed page range, e.g. '1-5' or '3'. "
            "Paginated documents only: PDF / DOCX / PPTX."
        ),
    )
    line_range: str | None = PField(
        default=None,
        description=(
            "Optional 1-indexed line range, e.g. '100-200' or '42'. "
            "Text / code / log files only. Lets you navigate large text "
            "files (e.g. 100k-line logs) by line number."
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

WHEN OTHER TOOLS ARE BETTER:
- Remote URLs — file_read only reads sandbox paths. Use a web-fetch
  tool for URLs.
- Grep / search — for pattern-find, execute("grep -n 'pattern' <file>")
  is more direct than file_read + scan.
- Tiny known-offset peeks — execute("sed -n '42p' <file>") skips
  parser overhead.

HOW UNSUPPORTED FORMATS BEHAVE:
- The tool returns kind="unsupported" with a `hint` when no parser
  plugin handles the file's MIME type. Common cases — video, audio,
  archives, binary executables — fall here in the default deployment.
- The `hint` field tells you what alternative to try (e.g., for
  archives: extract first via execute("unzip <file>") then file_read
  on extracted files).
- If you see kind="unsupported", surface the hint to the user; don't
  retry file_read on the same path.

RETURN FORMAT (discriminated by `kind`):
- "text"        : {content, mime, size_bytes, truncated, metadata}
- "notebook"    : {cells: [{cell_type, source, outputs}, ...]}
- "unsupported" : {reason, hint, mime, size_bytes}
- "unchanged"   : file unchanged since previous file_read in this session
- "error"       : {error, retryable}

PARAMETERS:
- path (required)         — absolute sandbox path
- page_range (optional)   — paginated documents only: PDF/DOCX/PPTX
- line_range (optional)   — text/code/log files only

RANGE SYNTAX (page_range and line_range share these 4 forms):
  "42"      — single line/page (item 42)
  "100-200" — range from 100 to 200 inclusive
  "100-"    — from 100 to end of file (sed '100,$' style)
  "-50"     — last 50 lines/pages (tail -50 style)

HOW TO CONTINUE READING WHEN truncated=true:
- text/code/log: read metadata.next_line_to_read and call
  file_read(path, line_range=f"{N}-") to continue from there.
- PDF/DOCX/PPTX (best-effort): read metadata.next_page_to_read
  and call file_read(path, page_range=f"{N}-"). If the field
  is absent (parser couldn't map char-offset back to page),
  fall back to ranges you guess or ask the user.
- notebook: metadata.next_cell_index is informational only —
  v1 has no cell_range param. The first batch is what you get.

LIMITS:
- Files > 100 MB are refused with kind="unsupported".
- Content longer than 20,000 characters is truncated. See
  "HOW TO CONTINUE READING" above.
- Large files (>3 MB) trigger async parsing; up to 10 minutes.
"""


def _create_file_read_tool(sandbox: Sandbox, conversation_id: UUID | None) -> BaseTool:
    """Build the file_read tool backed by a sandbox + conversation."""

    async def _file_read(
        path: str,
        page_range: str | None = None,
        line_range: str | None = None,
    ):
        result = await sandbox.file_read(
            path,
            options=ParseOptions(page_range=page_range, line_range=line_range),
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
git add backend/cubeplex/middleware/sandbox.py backend/tests/middleware/test_sandbox_file_read_tool.py
git commit -m "feat(sandbox): register file_read agent tool in SandboxMiddleware"
```

---

### Task 13: Backend `parsers.docling_serve` YAML config

**Files:**
- Modify: `backend/config.yaml`
- Modify: `backend/config.development.yaml`
- Modify: `backend/config.test.yaml`

**Note**: cubeplex uses **dynaconf**, not pydantic Settings (see `backend/cubeplex/config.py`). There is no top-level `Settings` class; configuration is accessed via `config.get("parsers.docling_serve.base_url", default)`. No schema class needs to be added to `config.py` — YAML alone is the source of truth for defaults; env vars (`CUBEPLEX_PARSERS__DOCLING_SERVE__BASE_URL`) override at runtime.

- [ ] **Step 1: Append to YAML configs**

Append to `backend/config.yaml` and `backend/config.development.yaml`:

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

For `backend/config.test.yaml`, use a localhost URL that won't be hit during tests (httpx mock transport intercepts):

```yaml
parsers:
  docling_serve:
    base_url: http://localhost-test-no-hit
    timeout_sync_seconds: 30
    timeout_async_minutes: 10
    async_threshold_mb: 3
    poll_interval_seconds: 2
```

- [ ] **Step 2: Verify config loads**

```bash
cd backend && uv run python -c "from cubeplex.config import config; print(config.get('parsers.docling_serve.base_url'))"
```

Expected: prints `http://docling-serve:5001` (or the development.yaml override).

- [ ] **Step 3: Commit**

```bash
git add backend/config.yaml backend/config.development.yaml backend/config.test.yaml
git commit -m "feat(parsers): add parsers.docling_serve YAML config (dynaconf-driven)"
```

---

### Task 14: App startup parser registry discover + docling-serve in docker-compose

**Files:**
- Modify: `backend/cubeplex/api/app.py`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Wire registry discover in lifespan**

In `backend/cubeplex/api/app.py` lifespan async context manager, add:

```python
from cubeplex.parsers import get_parser_registry

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

Expected: server starts without exception. `cubeplex.parsers` log line shows 3 plugins registered.

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/api/app.py docker-compose.yml
git commit -m "feat(api): discover parser plugins at app startup; add docling-serve compose service"
```

---

### Task 15: E2E test — real docling parse end-to-end

Per CLAUDE.md project rule "Focus on E2E tests" and spec D18: CI does **not** bundle docling-serve, but e2e exercises the real parse path against an externally hosted docling-serve. Mocks are forbidden here — if docling is unreachable the test must skip-with-warning, not fall back.

**Files:**
- Create: `backend/tests/e2e/test_file_read_docling_e2e.py`
- Modify: `backend/tests/e2e/conftest.py` (add `docling_url` session fixture)

- [ ] **Step 1: Add `docling_url` session fixture**

Append to `backend/tests/e2e/conftest.py`:

```python
import os
import httpx
import pytest


@pytest.fixture(scope="session")
def docling_url() -> str:
    """Return reachable DOCLING_URL or skip. Never mocks."""
    url = os.environ.get("DOCLING_URL")
    if not url:
        pytest.skip("DOCLING_URL not set — external docling-serve required for e2e")
    try:
        resp = httpx.get(f"{url.rstrip('/')}/health", timeout=5)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — any probe failure → skip
        pytest.skip(f"docling-serve at {url} unreachable: {exc}")
    return url
```

- [ ] **Step 2: Write e2e that exercises the real parser**

```python
# backend/tests/e2e/test_file_read_docling_e2e.py
"""E2E: agent calls file_read on a PDF → real docling-serve parses → markdown returns."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from cubeplex.config import config
from cubeplex.parsers import ParseOptions, TextOutput, get_parser_registry

FIXTURE = Path(__file__).parent / "fixtures" / "hello.pdf"  # small (<100 KB) fixture PDF


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_pdf_flows_through_real_docling(docling_url: str, monkeypatch) -> None:
    """Read a small PDF via the real docling-serve endpoint; assert markdown + parser metadata."""
    monkeypatch.setenv("CUBEPLEX_PARSERS__DOCLING_SERVE__BASE_URL", docling_url)
    config.reload()

    from cubeplex.parsers.registry import reset_parser_registry_for_tests
    reset_parser_registry_for_tests()
    reg = get_parser_registry()
    await reg.discover()

    class _LocalSandbox:
        async def _download_one(self, path: str) -> bytes:
            return FIXTURE.read_bytes()

    out = await reg.dispatch(
        sandbox=_LocalSandbox(),
        path=str(FIXTURE),
        options=ParseOptions(),
        conversation_id=uuid4(),
    )
    assert isinstance(out, TextOutput), f"expected markdown text output, got {type(out).__name__}"
    assert out.metadata.get("parser") == "docling"
    assert len(out.content) > 0
    assert "hello" in out.content.lower()  # fixture contains the word "hello"


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_unchanged_second_read_hits_dedup(docling_url: str, monkeypatch) -> None:
    """Same conversation + same bytes + same ParseOptions → second read returns UnchangedOutput."""
    from cubeplex.parsers import UnchangedOutput

    monkeypatch.setenv("CUBEPLEX_PARSERS__DOCLING_SERVE__BASE_URL", docling_url)
    config.reload()

    from cubeplex.parsers.registry import reset_parser_registry_for_tests
    reset_parser_registry_for_tests()
    reg = get_parser_registry()
    await reg.discover()

    class _LocalSandbox:
        async def _download_one(self, path: str) -> bytes:
            return FIXTURE.read_bytes()

    sandbox = _LocalSandbox()
    conv = uuid4()
    first = await reg.dispatch(
        sandbox=sandbox, path=str(FIXTURE),
        options=ParseOptions(), conversation_id=conv,
    )
    assert isinstance(first, TextOutput)

    second = await reg.dispatch(
        sandbox=sandbox, path=str(FIXTURE),
        options=ParseOptions(), conversation_id=conv,
    )
    assert isinstance(second, UnchangedOutput)
```

- [ ] **Step 3: Commit fixture + test**

Add a small hand-crafted PDF at `backend/tests/e2e/fixtures/hello.pdf` (≤ 10 KB). Ensure it renders the literal word "hello" so the assertion above is robust.

```bash
git add backend/tests/e2e/test_file_read_docling_e2e.py backend/tests/e2e/conftest.py backend/tests/e2e/fixtures/hello.pdf
git commit -m "test(parsers): e2e file_read against real docling-serve"
```

- [ ] **Step 4: Run e2e locally against docling**

```bash
DOCLING_URL=http://localhost:5001 cd backend && uv run pytest tests/e2e/test_file_read_docling_e2e.py -v
```

Expected: 2 passed. (If DOCLING_URL is unset the tests skip — that is intended in developer environments but **not** acceptable in CI where the secret must be configured.)

---

### Task 16: Final integration + check

**Files:**
- Run all tests + smoke

- [ ] **Step 1: Full backend test sweep**

```bash
cd backend && uv run pytest tests/ -v
```

Expected: ALL pass (docling e2e skips with warning if `DOCLING_URL` is absent; must pass when set).

- [ ] **Step 2: Run lint + type-check**

```bash
cd backend && make check
```

Expected: All green.

- [ ] **Step 3: Verify clean git status**

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
- ⚠ Pytest-asyncio test event loop pattern in `conftest.py` may need adjustment depending on `pyproject.toml` asyncio_mode setting; current cubeplex uses pytest-asyncio — confirm style matches existing tests.
