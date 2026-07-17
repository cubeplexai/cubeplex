# File Upload Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the polish pass on file upload defined in `docs/superpowers/specs/2026-05-04-file-upload-polish-design.md` — immediate upload on home page with cancel, animated progress chips, type-aware icons, attachments above the user bubble, dedicated `file_read` preview panel, and citation support for `file_read` results.

**Architecture:** Backend: extend the existing generic `CitationMiddleware` to wire `file_read` (no parallel pipeline). Frontend: extract a shared `lib/fileIcons.ts`, replace `AttachmentChip` with two new chip components, add a `'attachment'` panel-view variant alongside `tool` / `artifact`, add a `FileReadView` for the new tool content type. Conversation cleanup: a single `has_messages` boolean column flipped on the first message, with the list endpoint filtering on it.

**Tech Stack:** Python 3.13 + FastAPI + LangChain/LangGraph + Alembic + Pydantic v2 (backend); Next.js 16 + React 19 + Tailwind 4 + Zustand + Vitest + Playwright (frontend); pnpm workspace; react-icons/fa6 for glyphs.

---

## Branch & worktree

This plan is implemented on branch `feat/file-upload-polish` (already created from `main` at the repo root). The user explicitly does NOT want a worktree for this work. Run all backend commands from `backend/`, all frontend commands from `frontend/`. The test database, ports, and Redis prefix come from the main checkout's `backend/.env` + `backend/config.development.local.yaml` — no `.worktree.env` exists.

---

## Phase 1 — Backend foundation

### Task 1: `CitationConfig.discriminator` for `file_read` kinds

**Files:**
- Modify: `backend/cubeplex/middleware/citations/config.py`
- Test: `backend/tests/unit/test_citation_config.py`

`CitationConfig` needs to skip items whose `kind` field is not in the allowed set, so `file_read` results with `kind: "notebook" | "unsupported" | "unchanged" | "error"` produce zero citations.

- [ ] **Step 1.1 — Write the failing tests**

Append to `backend/tests/unit/test_citation_config.py`:

```python
class TestCitationDiscriminator:
    def _file_cfg(self) -> CitationConfig:
        return CitationConfig(
            source_type="file",
            content_field=None,
            discriminator_field="kind",
            discriminator_values=["text"],
            mapping={"snippet": "content", "path": "path"},
        )

    def test_discriminator_allows_matching_kind(self):
        cfg = self._file_cfg()
        items = cfg.extract_items(
            {"kind": "text", "path": "/a.md", "content": "hello"}
        )
        assert len(items) == 1
        assert items[0]["content"] == "hello"

    def test_discriminator_rejects_other_kind(self):
        cfg = self._file_cfg()
        assert cfg.extract_items({"kind": "notebook", "path": "/a.ipynb"}) == []
        assert cfg.extract_items({"kind": "unsupported", "path": "/a.bin"}) == []
        assert cfg.extract_items({"kind": "error", "path": "/a.md", "error": "boom"}) == []

    def test_discriminator_no_field_set_passes_through(self):
        # No discriminator_field → behaviour unchanged from before.
        cfg = CitationConfig(
            source_type="web",
            content_field=None,
            mapping={"snippet": "body"},
        )
        assert cfg.extract_items({"body": "x"}) == [{"body": "x"}]
```

- [ ] **Step 1.2 — Run the tests and watch them fail**

Run: `cd backend && uv run pytest tests/unit/test_citation_config.py::TestCitationDiscriminator -v`
Expected: FAIL — `discriminator_field` and `discriminator_values` are not yet model fields.

- [ ] **Step 1.3 — Add the fields and gate `extract_items`**

Edit `backend/cubeplex/middleware/citations/config.py`:

```python
class CitationConfig(BaseModel):
    source_type: str
    content_field: str | None
    mapping: dict[str, str]
    args_mapping: dict[str, str] | None = None
    discriminator_field: str | None = None
    discriminator_values: list[str] | None = None

    # ... existing extract_metadata / extract_text unchanged ...

    def extract_items(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        if self.discriminator_field:
            value = data.get(self.discriminator_field)
            if self.discriminator_values is not None and value not in self.discriminator_values:
                return []
        if self.content_field is None:
            return [data]
        items = data.get(self.content_field, [])
        if not isinstance(items, list):
            return [items] if items else []
        return items
```

- [ ] **Step 1.4 — Run the tests and watch them pass**

Run: `cd backend && uv run pytest tests/unit/test_citation_config.py -v`
Expected: all tests pass (existing + new).

- [ ] **Step 1.5 — Commit**

```bash
git add backend/cubeplex/middleware/citations/config.py backend/tests/unit/test_citation_config.py
git commit -m "feat(backend): add discriminator filter to CitationConfig"
```

---

### Task 2: `load_builtin_citation_configs`

**Files:**
- Modify: `backend/cubeplex/middleware/citations/config.py`
- Test: `backend/tests/unit/test_citation_config.py`

Built-in tools (`StructuredTool` instances) carry their citation config on the tool's `metadata` dict. We need a loader that mirrors `load_citation_configs` but reads from `tool.metadata['citation']`.

- [ ] **Step 2.1 — Write the failing test**

Append to `backend/tests/unit/test_citation_config.py`:

```python
from langchain_core.tools import StructuredTool

from cubeplex.middleware.citations.config import load_builtin_citation_configs


def _make_tool(name: str, metadata: dict | None) -> StructuredTool:
    async def _noop() -> str:
        return ""

    return StructuredTool.from_function(
        coroutine=_noop,
        name=name,
        description="t",
        metadata=metadata,
    )


class TestLoadBuiltinCitationConfigs:
    def test_reads_citation_from_tool_metadata(self):
        tool = _make_tool(
            "file_read",
            {
                "content_type": "file_read",
                "citation": {
                    "source_type": "file",
                    "content_field": None,
                    "mapping": {"snippet": "content"},
                },
            },
        )
        configs = load_builtin_citation_configs([tool])
        assert "file_read" in configs
        assert configs["file_read"].source_type == "file"

    def test_skips_tool_without_citation_metadata(self):
        tool = _make_tool("execute", {"content_type": "shell"})
        assert load_builtin_citation_configs([tool]) == {}

    def test_skips_tool_without_metadata(self):
        tool = _make_tool("calculator", None)
        assert load_builtin_citation_configs([tool]) == {}

    def test_empty_input_returns_empty(self):
        assert load_builtin_citation_configs([]) == {}
```

- [ ] **Step 2.2 — Run the tests and watch them fail**

Run: `cd backend && uv run pytest tests/unit/test_citation_config.py::TestLoadBuiltinCitationConfigs -v`
Expected: FAIL — `load_builtin_citation_configs` is undefined.

- [ ] **Step 2.3 — Implement the loader**

Append to `backend/cubeplex/middleware/citations/config.py`:

```python
def load_builtin_citation_configs(
    tools: list[Any],
) -> dict[str, CitationConfig]:
    """Build tool_name -> CitationConfig from each tool's metadata['citation']."""
    configs: dict[str, CitationConfig] = {}
    for t in tools:
        meta = getattr(t, "metadata", None)
        if not isinstance(meta, dict):
            continue
        citation = meta.get("citation")
        name = getattr(t, "name", None)
        if name and isinstance(citation, dict):
            configs[str(name)] = CitationConfig(**citation)
    return configs
```

- [ ] **Step 2.4 — Run the tests and watch them pass**

Run: `cd backend && uv run pytest tests/unit/test_citation_config.py -v`
Expected: all pass.

- [ ] **Step 2.5 — Commit**

```bash
git add backend/cubeplex/middleware/citations/config.py backend/tests/unit/test_citation_config.py
git commit -m "feat(backend): add load_builtin_citation_configs for tool metadata"
```

---

### Task 3: Attach citation metadata to `file_read` tool

**Files:**
- Modify: `backend/cubeplex/middleware/sandbox.py:190-211`
- Test: `backend/tests/unit/test_citation_config.py` (smoke check via the loader)

- [ ] **Step 3.1 — Update `_create_file_read_tool` metadata**

Edit `backend/cubeplex/middleware/sandbox.py` — change the `metadata=` argument on the `StructuredTool.from_function(...)` call inside `_create_file_read_tool` to:

```python
metadata={
    "content_type": "file_read",
    "citation": {
        "source_type": "file",
        "content_field": None,
        "discriminator_field": "kind",
        "discriminator_values": ["text"],
        "mapping": {
            "snippet": "content",
            "path": "path",
            "mime": "mime",
            "size_bytes": "size_bytes",
            "truncated": "truncated",
        },
        "args_mapping": {
            "page_range": "page_range",
            "line_range": "line_range",
        },
    },
},
```

- [ ] **Step 3.2 — Add an assertion that the tool's citation config round-trips**

Append to `backend/tests/unit/test_citation_config.py`:

```python
class TestFileReadToolCitationWiring:
    def test_file_read_tool_metadata_loadable(self):
        from unittest.mock import MagicMock

        from cubeplex.middleware.sandbox import _create_file_read_tool

        sandbox = MagicMock()
        tool = _create_file_read_tool(sandbox, conversation_id=None)
        configs = load_builtin_citation_configs([tool])
        assert "file_read" in configs
        cfg = configs["file_read"]
        assert cfg.source_type == "file"
        assert cfg.discriminator_field == "kind"
        assert cfg.discriminator_values == ["text"]
        # text result chunks via 'content'
        items = cfg.extract_items(
            {"kind": "text", "path": "/a.md", "content": "x" * 50}
        )
        assert len(items) == 1
        # error result is filtered
        assert cfg.extract_items({"kind": "error", "path": "/a.md", "error": "boom"}) == []
```

- [ ] **Step 3.3 — Run the test and watch it pass**

Run: `cd backend && uv run pytest tests/unit/test_citation_config.py::TestFileReadToolCitationWiring -v`
Expected: PASS.

- [ ] **Step 3.4 — Commit**

```bash
git add backend/cubeplex/middleware/sandbox.py backend/tests/unit/test_citation_config.py
git commit -m "feat(backend): attach citation config to file_read tool"
```

---

### Task 4: Wire built-in citation configs into the run manager

**Files:**
- Modify: `backend/cubeplex/streams/run_manager.py:591-617` (the citation config loading block)

- [ ] **Step 4.1 — Update the import to bring in the new loader**

In `backend/cubeplex/streams/run_manager.py`, change the inline import inside the run setup block:

```python
from cubeplex.middleware.citations import CitationConfig, load_citation_configs
from cubeplex.middleware.citations.config import load_builtin_citation_configs
```

- [ ] **Step 4.2 — Merge the configs**

Find the `all_citation_configs: dict[str, CitationConfig] = {}` block (~line 591) and the loop that calls `load_citation_configs(tool_defs)`. Right after that loop, before `CitationMiddleware(citation_configs=all_citation_configs, ...)` is constructed, add:

```python
# Built-in tools (e.g. file_read) carry their citation config on tool.metadata['citation'].
try:
    all_citation_configs.update(load_builtin_citation_configs(builtin_tools))
except Exception as exc:  # noqa: BLE001
    logger.debug("Failed to load builtin citation configs: {}", exc)
```

`builtin_tools` is the list available at this point in `run_manager.py` — verify the surrounding code: look for the variable that holds the assembled tools (it's typically `tools` or `builtin_tools`; if the local name differs, adapt the call accordingly while keeping the merge intent the same).

- [ ] **Step 4.3 — Type-check and lint**

Run: `cd backend && make type-check && make lint`
Expected: all green.

- [ ] **Step 4.4 — Run the full unit suite**

Run: `cd backend && uv run pytest tests/unit -v`
Expected: all pass.

- [ ] **Step 4.5 — Commit**

```bash
git add backend/cubeplex/streams/run_manager.py
git commit -m "feat(backend): merge builtin citation configs into run manager"
```

---

### Task 5: `Conversation.has_messages` column + Alembic migration

**Files:**
- Modify: `backend/cubeplex/models/conversation.py`
- Create: `backend/alembic/versions/<auto>_conversation_has_messages.py`

- [ ] **Step 5.1 — Add the field on the model**

Edit `backend/cubeplex/models/conversation.py`:

```python
class Conversation(SQLModel, OrgScopedMixin, table=True):
    """Conversation model for storing chat sessions."""

    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_ws", "creator_user_id", "workspace_id"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    creator_user_id: str = Field(max_length=36)
    title: str = Field(max_length=255)
    has_messages: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

- [ ] **Step 5.2 — Generate the migration**

Run from `backend/`:

```bash
uv run alembic revision --autogenerate -m "conversation has_messages"
```

Open the generated file in `backend/alembic/versions/`. Review:
- It should add a `has_messages` column with `server_default=sa.false()` (or equivalent), `nullable=False`.
- It should add an index on `has_messages`.

If autogenerate produced `nullable=True`, edit the migration to use `server_default=sa.text("false"), nullable=False`. Existing rows then default to `false`, which is fine because they have no messages or had real activity already (we'll backfill in step 5.4).

- [ ] **Step 5.3 — Add a backfill in the migration**

In the same migration file, after the column add and before any index ops, append a backfill:

```python
op.execute(
    "UPDATE conversations SET has_messages = TRUE "
    "WHERE updated_at > created_at + INTERVAL '1 second'"
)
```

(This is a heuristic: any conversation whose `updated_at` has drifted from `created_at` already had real activity. New empty conversations created via the new home-page flow will keep `has_messages = false`.)

- [ ] **Step 5.4 — Apply the migration**

Run: `cd backend && uv run alembic upgrade head`
Expected: migration applies cleanly.

- [ ] **Step 5.5 — Commit**

```bash
git add backend/cubeplex/models/conversation.py backend/alembic/versions/
git commit -m "feat(backend): add Conversation.has_messages column"
```

---

### Task 6: Set `has_messages` on send and filter the list endpoint

**Files:**
- Modify: `backend/cubeplex/repositories/conversation.py`
- Modify: `backend/cubeplex/api/routes/v1/conversations.py:30-60` (the `update_timestamp_after_stream` helper)
- Test: `backend/tests/e2e/test_conversation_filter.py` (new)

- [ ] **Step 6.1 — Write the failing E2E test**

Create `backend/tests/e2e/test_conversation_filter.py`:

```python
"""E2E: empty conversations are hidden from the list endpoint."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.e2e


async def test_empty_conversation_not_listed(
    authed_client: AsyncClient, workspace_id: str
) -> None:
    # Create an empty conversation.
    create = await authed_client.post(
        f"/api/v1/ws/{workspace_id}/conversations?title=draft"
    )
    assert create.status_code == 201
    convo_id = create.json()["id"]

    listed = await authed_client.get(f"/api/v1/ws/{workspace_id}/conversations")
    assert listed.status_code == 200
    ids = [c["id"] for c in listed.json()["conversations"]]
    assert convo_id not in ids


async def test_conversation_listed_after_first_message(
    authed_client: AsyncClient, workspace_id: str, send_message
) -> None:
    create = await authed_client.post(
        f"/api/v1/ws/{workspace_id}/conversations?title=hi"
    )
    convo_id = create.json()["id"]
    await send_message(convo_id, "hello")

    listed = await authed_client.get(f"/api/v1/ws/{workspace_id}/conversations")
    ids = [c["id"] for c in listed.json()["conversations"]]
    assert convo_id in ids
```

(Adapt fixture names — `authed_client`, `workspace_id`, `send_message` — to whatever exists in `backend/tests/conftest.py`. Read `tests/conftest.py` first; if the helpers differ, mirror the patterns from `tests/e2e/test_attachment_lifecycle.py` for client + auth and from `tests/e2e/test_send_with_attachments.py` for message send. If no `send_message` fixture exists, inline the SSE POST call from those tests.)

- [ ] **Step 6.2 — Run it and watch it fail**

Run: `cd backend && uv run pytest tests/e2e/test_conversation_filter.py -v`
Expected: the empty-conversation case fails because the empty conversation IS still listed.

- [ ] **Step 6.3 — Add `mark_has_messages` on the repo**

Edit `backend/cubeplex/repositories/conversation.py`:

```python
    async def mark_has_messages(self, conversation_id: str) -> None:
        conv = await self.get(conversation_id)
        if conv and not conv.has_messages:
            conv.has_messages = True
            conv.updated_at = datetime.now(UTC)
            await self.session.commit()
```

Then change `list_all` to filter:

```python
    async def list_all(self, *, limit: int = 20, offset: int = 0) -> tuple[list[Conversation], int]:
        stmt = (
            self._scoped_select()
            .where(Conversation.has_messages.is_(True))  # type: ignore[union-attr]
            .order_by(desc(Conversation.updated_at))  # type: ignore[arg-type]
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        items = list(result.scalars().all())

        count_stmt = (
            select(func.count())
            .select_from(Conversation)
            .where(
                Conversation.workspace_id == self.workspace_id,  # type: ignore[arg-type]
                Conversation.creator_user_id == self.user_id,  # type: ignore[arg-type]
                Conversation.has_messages.is_(True),  # type: ignore[union-attr]
            )
        )
        total = (await self.session.execute(count_stmt)).scalar_one()
        return items, total
```

- [ ] **Step 6.4 — Flip the flag on first send**

In `backend/cubeplex/api/routes/v1/conversations.py`, find `update_timestamp_after_stream` (~line 30). Replace its body's `await save_conv_repo.update_timestamp(conversation_id)` line with:

```python
            await save_conv_repo.mark_has_messages(conversation_id)
```

`mark_has_messages` already updates `updated_at`, so the existing timestamp behaviour is preserved.

- [ ] **Step 6.5 — Run the E2E and watch it pass**

Run: `cd backend && uv run pytest tests/e2e/test_conversation_filter.py -v`
Expected: PASS.

- [ ] **Step 6.6 — Run full backend check**

Run: `cd backend && make check`
Expected: format / lint / mypy / pytest all green.

- [ ] **Step 6.7 — Commit**

```bash
git add backend/cubeplex/ backend/tests/e2e/test_conversation_filter.py
git commit -m "feat(backend): filter empty conversations from list endpoint"
```

---

## Phase 2 — Frontend foundation

### Task 7: Extract `lib/fileIcons.ts`

**Files:**
- Create: `frontend/packages/web/lib/fileIcons.ts`
- Create: `frontend/packages/web/__tests__/lib/fileIcons.test.ts`

- [ ] **Step 7.1 — Write the failing tests**

Create `frontend/packages/web/__tests__/lib/fileIcons.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { getFileVisual } from '@/lib/fileIcons'

describe('getFileVisual', () => {
  it('resolves PDF by extension', () => {
    const v = getFileVisual({ filename: 'report.pdf' })
    expect(v.family).toBe('pdf')
    expect(v.label).toBe('PDF')
    expect(v.bg).toBe('bg-rose-500')
  })

  it('resolves Word by extension', () => {
    expect(getFileVisual({ filename: 'doc.docx' }).family).toBe('word')
    expect(getFileVisual({ filename: 'doc.doc' }).family).toBe('word')
  })

  it('resolves Excel and CSV', () => {
    expect(getFileVisual({ filename: 'a.xlsx' }).family).toBe('excel')
    expect(getFileVisual({ filename: 'a.csv' }).family).toBe('csv')
  })

  it('resolves Markdown', () => {
    expect(getFileVisual({ filename: 'a.md' }).family).toBe('markdown')
    expect(getFileVisual({ filename: 'a.markdown' }).family).toBe('markdown')
  })

  it('resolves code by extension family', () => {
    expect(getFileVisual({ filename: 'x.ts' }).family).toBe('code')
    expect(getFileVisual({ filename: 'x.py' }).family).toBe('code')
    expect(getFileVisual({ filename: 'x.json' }).family).toBe('json')
  })

  it('falls back to mime when extension is unknown', () => {
    expect(getFileVisual({ filename: 'noext', mime_type: 'application/pdf' }).family).toBe('pdf')
    expect(getFileVisual({ filename: 'noext', mime_type: 'image/jpeg' }).family).toBe('image')
    expect(getFileVisual({ filename: 'noext', mime_type: 'video/mp4' }).family).toBe('video')
    expect(getFileVisual({ filename: 'noext', mime_type: 'audio/mpeg' }).family).toBe('audio')
    expect(getFileVisual({ filename: 'noext', mime_type: 'text/plain' }).family).toBe('text')
  })

  it('returns unknown for fully unknown input', () => {
    const v = getFileVisual({ filename: 'foo.xyz' })
    expect(v.family).toBe('unknown')
    expect(v.bg).toBe('bg-zinc-500')
  })

  it('handles empty input gracefully', () => {
    const v = getFileVisual({})
    expect(v.family).toBe('unknown')
  })
})
```

- [ ] **Step 7.2 — Run tests and watch them fail**

Run: `cd frontend && pnpm --filter web test -- fileIcons`
Expected: FAIL — module does not resolve.

- [ ] **Step 7.3 — Create `lib/fileIcons.ts`**

Create `frontend/packages/web/lib/fileIcons.ts`:

```ts
import type { IconType } from 'react-icons'
import {
  FaFile,
  FaFileLines,
  FaFileCode,
  FaFileCsv,
  FaFileExcel,
  FaFileImage,
  FaFileVideo,
  FaFileAudio,
  FaFileZipper,
  FaFilePdf,
  FaFileWord,
  FaFilePowerpoint,
} from 'react-icons/fa6'

export type FileFamily =
  | 'pdf'
  | 'word'
  | 'excel'
  | 'csv'
  | 'ppt'
  | 'markdown'
  | 'text'
  | 'code'
  | 'json'
  | 'image'
  | 'video'
  | 'audio'
  | 'archive'
  | 'unknown'

export interface FileVisual {
  family: FileFamily
  Icon: IconType
  label: string
  bg: string
  fg: string
}

const FAMILY_VISUALS: Record<FileFamily, Omit<FileVisual, 'family'>> = {
  pdf:      { Icon: FaFilePdf,        label: 'PDF',       bg: 'bg-rose-500',     fg: 'text-white' },
  word:     { Icon: FaFileWord,       label: 'Word',      bg: 'bg-blue-600',     fg: 'text-white' },
  excel:    { Icon: FaFileExcel,      label: 'Excel',     bg: 'bg-emerald-600',  fg: 'text-white' },
  csv:      { Icon: FaFileCsv,        label: 'CSV',       bg: 'bg-emerald-600',  fg: 'text-white' },
  ppt:      { Icon: FaFilePowerpoint, label: 'Slides',    bg: 'bg-orange-500',   fg: 'text-white' },
  markdown: { Icon: FaFileLines,      label: 'Markdown',  bg: 'bg-slate-500',    fg: 'text-white' },
  text:     { Icon: FaFileLines,      label: 'Text',      bg: 'bg-slate-500',    fg: 'text-white' },
  code:     { Icon: FaFileCode,       label: 'Code',      bg: 'bg-violet-600',   fg: 'text-white' },
  json:     { Icon: FaFileCode,       label: 'JSON',      bg: 'bg-violet-600',   fg: 'text-white' },
  image:    { Icon: FaFileImage,      label: 'Image',     bg: 'bg-pink-500',     fg: 'text-white' },
  video:    { Icon: FaFileVideo,      label: 'Video',     bg: 'bg-fuchsia-600',  fg: 'text-white' },
  audio:    { Icon: FaFileAudio,      label: 'Audio',     bg: 'bg-cyan-600',     fg: 'text-white' },
  archive:  { Icon: FaFileZipper,     label: 'Archive',   bg: 'bg-amber-600',    fg: 'text-white' },
  unknown:  { Icon: FaFile,           label: 'File',      bg: 'bg-zinc-500',     fg: 'text-white' },
}

const EXT_TO_FAMILY: Record<string, FileFamily> = {
  pdf: 'pdf',
  doc: 'word', docx: 'word', odt: 'word', rtf: 'word',
  xls: 'excel', xlsx: 'excel', ods: 'excel',
  csv: 'csv',
  ppt: 'ppt', pptx: 'ppt', odp: 'ppt', key: 'ppt',
  md: 'markdown', markdown: 'markdown', mdx: 'markdown',
  txt: 'text', log: 'text',
  json: 'json', jsonl: 'json',
  js: 'code', ts: 'code', jsx: 'code', tsx: 'code', mjs: 'code', cjs: 'code',
  py: 'code', rb: 'code', go: 'code', rs: 'code',
  java: 'code', kt: 'code', c: 'code', cpp: 'code', h: 'code', hpp: 'code',
  cs: 'code', swift: 'code', sh: 'code', bash: 'code', zsh: 'code',
  html: 'code', css: 'code', scss: 'code', less: 'code',
  sql: 'code', yaml: 'code', yml: 'code', toml: 'code', xml: 'code',
  vue: 'code', svelte: 'code',
  png: 'image', jpg: 'image', jpeg: 'image', gif: 'image',
  svg: 'image', webp: 'image', bmp: 'image', ico: 'image', tiff: 'image',
  mp4: 'video', webm: 'video', mov: 'video', avi: 'video', mkv: 'video',
  mp3: 'audio', wav: 'audio', ogg: 'audio', flac: 'audio', aac: 'audio',
  zip: 'archive', tar: 'archive', gz: 'archive', rar: 'archive', '7z': 'archive',
}

const MIME_TO_FAMILY: Record<string, FileFamily> = {
  'application/pdf': 'pdf',
  'application/msword': 'word',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'word',
  'application/vnd.ms-excel': 'excel',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'excel',
  'text/csv': 'csv',
  'application/vnd.ms-powerpoint': 'ppt',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'ppt',
  'text/markdown': 'markdown',
  'application/json': 'json',
  'application/x-ndjson': 'json',
  'application/zip': 'archive',
  'application/x-tar': 'archive',
  'application/gzip': 'archive',
  'application/x-7z-compressed': 'archive',
  'application/x-rar-compressed': 'archive',
}

const MIME_PREFIX_TO_FAMILY: [string, FileFamily][] = [
  ['image/', 'image'],
  ['video/', 'video'],
  ['audio/', 'audio'],
  ['text/', 'text'],
]

function getExt(filename: string | undefined): string {
  if (!filename) return ''
  const dot = filename.lastIndexOf('.')
  return dot > 0 ? filename.slice(dot + 1).toLowerCase() : ''
}

export function getFileFamily(input: { filename?: string; mime_type?: string }): FileFamily {
  const ext = getExt(input.filename)
  if (ext && EXT_TO_FAMILY[ext]) return EXT_TO_FAMILY[ext]
  const mime = input.mime_type
  if (mime && MIME_TO_FAMILY[mime]) return MIME_TO_FAMILY[mime]
  if (mime) {
    for (const [prefix, family] of MIME_PREFIX_TO_FAMILY) {
      if (mime.startsWith(prefix)) return family
    }
  }
  return 'unknown'
}

export function getFileVisual(input: { filename?: string; mime_type?: string }): FileVisual {
  const family = getFileFamily(input)
  return { family, ...FAMILY_VISUALS[family] }
}

export function getFileLabel(input: { filename?: string; mime_type?: string }): string {
  return getFileVisual(input).label
}
```

- [ ] **Step 7.4 — Run tests and watch them pass**

Run: `cd frontend && pnpm --filter web test -- fileIcons`
Expected: PASS.

- [ ] **Step 7.5 — Commit**

```bash
git add frontend/packages/web/lib/fileIcons.ts frontend/packages/web/__tests__/lib/fileIcons.test.ts
git commit -m "feat(frontend): add shared fileIcons library"
```

---

### Task 8: Refactor `artifactIcons.ts` as a wrapper over `fileIcons.ts`

**Files:**
- Modify: `frontend/packages/web/components/panel/artifact/artifactIcons.ts`

- [ ] **Step 8.1 — Replace the content with thin wrappers**

Edit `frontend/packages/web/components/panel/artifact/artifactIcons.ts`:

```ts
import type { Artifact } from '@cubeplex/core'
import type { IconType } from 'react-icons'
import { FaCode, FaDatabase, FaFile, FaFileLines, FaGlobe, FaImage } from 'react-icons/fa6'
import { getFileFamily, getFileVisual } from '@/lib/fileIcons'

const TYPE_ICONS: Record<string, IconType> = {
  website: FaGlobe,
  document: FaFileLines,
  code: FaCode,
  image: FaImage,
  data: FaDatabase,
  file: FaFile,
}

const TYPE_LABELS: Record<string, string> = {
  website: 'Website',
  document: 'Document',
  code: 'Code',
  image: 'Image',
  data: 'Data',
  file: 'File',
}

function artifactInput(artifact: Artifact): { filename?: string; mime_type?: string } {
  const filename = artifact.entry_file || artifact.path.split('/').pop() || ''
  return { filename, mime_type: artifact.mime_type ?? undefined }
}

export function getArtifactIcon(artifact: Artifact): IconType {
  const family = getFileFamily(artifactInput(artifact))
  if (family !== 'unknown') return getFileVisual(artifactInput(artifact)).Icon
  return TYPE_ICONS[artifact.artifact_type] ?? FaFile
}

export function getArtifactLabel(artifact: Artifact): string {
  const visual = getFileVisual(artifactInput(artifact))
  if (visual.family !== 'unknown') return visual.label
  return TYPE_LABELS[artifact.artifact_type] ?? 'File'
}
```

- [ ] **Step 8.2 — Type-check, run all tests**

Run: `cd frontend && pnpm type-check && pnpm --filter web test`
Expected: green; all existing artifact-icon usages still resolve.

- [ ] **Step 8.3 — Commit**

```bash
git add frontend/packages/web/components/panel/artifact/artifactIcons.ts
git commit -m "refactor(frontend): make artifactIcons a wrapper over fileIcons"
```

---

### Task 9: Extend `PanelContentType` and `mapContentType` for `file_read`

**Files:**
- Modify: `frontend/packages/core/src/types/events.ts:37-46`
- Modify: `frontend/packages/core/src/stores/panelStore.ts:6-24`

- [ ] **Step 9.1 — Add `'file_read'` to the union**

Edit `frontend/packages/core/src/types/events.ts`:

```ts
export type PanelContentType =
  | 'search'
  | 'code_execute'
  | 'web_fetch'
  | 'terminal'
  | 'write_file'
  | 'generic'
  | 'artifact'
  | 'skill'
  | 'file_read'
```

- [ ] **Step 9.2 — Route `file_read` in `mapContentType`**

Edit `frontend/packages/core/src/stores/panelStore.ts` — update `mapContentType`:

```ts
function mapContentType(toolName: string, backendContentType?: string): PanelContentType {
  if (toolName === 'load_skill') return 'skill'
  if (toolName === 'execute') return 'terminal'
  if (toolName === 'write_file') return 'write_file'
  if (toolName === 'code_execute' || toolName === 'python') return 'code_execute'
  if (toolName === 'file_read') return 'file_read'
  if (backendContentType === 'file_read') return 'file_read'

  if (backendContentType === 'json') {
    if (toolName === 'web_search' || toolName === 'search') return 'search'
    return 'generic'
  }
  if (backendContentType === 'text') {
    if (toolName === 'web_fetch' || toolName === 'fetch') return 'web_fetch'
    return 'generic'
  }

  if (toolName === 'web_search' || toolName === 'search') return 'search'
  if (toolName === 'web_fetch' || toolName === 'fetch') return 'web_fetch'
  return 'generic'
}
```

- [ ] **Step 9.3 — Build core, type-check both**

Run: `cd frontend && pnpm --filter @cubeplex/core build && pnpm type-check`
Expected: green.

- [ ] **Step 9.4 — Commit**

```bash
git add frontend/packages/core/
git commit -m "feat(frontend): add file_read to PanelContentType + mapContentType"
```

---

### Task 10: Extend `CitationMetadata`, add `'attachment'` panel view

**Files:**
- Modify: `frontend/packages/core/src/types/citation.ts`
- Modify: `frontend/packages/core/src/stores/panelStore.ts`

- [ ] **Step 10.1 — Add file fields to `CitationMetadata`**

Edit `frontend/packages/core/src/types/citation.ts`:

```ts
export interface CitationChunk {
  chunk_index: number
  content: string
}

export interface CitationMetadata {
  source_type: string
  // web fields
  url?: string
  title?: string
  domain?: string
  published_at?: string
  // file fields
  path?: string
  mime?: string
  size_bytes?: number
  truncated?: boolean
  page_range?: string
  line_range?: string
}

export interface CitationData {
  citation_id: number
  chunks: CitationChunk[]
  metadata: CitationMetadata
  tool_call_id: string
}
```

- [ ] **Step 10.2 — Add `'attachment'` view + `openAttachment` action**

Edit `frontend/packages/core/src/stores/panelStore.ts`:

```ts
export interface AttachmentPanelInfo {
  attachmentId: string
  filename: string
  downloadUrl: string
  mimeType: string
  sizeBytes: number
}

export type PanelView =
  | { type: 'closed' }
  | {
      type: 'tool'
      toolName: string
      toolArgs: Record<string, unknown>
      toolResult: string | null
      contentType: PanelContentType
      toolRef: ToolCallRef | null
      highlightText: string | null
      highlightKey: number
    }
  | {
      type: 'artifact'
      conversationId: string
      artifactId: string
    }
  | {
      type: 'attachment'
      info: AttachmentPanelInfo
    }

export interface PanelStore {
  view: PanelView

  openTool: (
    toolName: string,
    toolArgs: Record<string, unknown>,
    toolResult: string | null,
    contentType?: string,
    toolRef?: ToolCallRef,
    highlightText?: string,
  ) => void

  openArtifact: (conversationId: string, artifactId: string) => void

  openAttachment: (info: AttachmentPanelInfo) => void

  close: () => void
}
```

Inside the store body, add:

```ts
  openAttachment: (info) => set({ view: { type: 'attachment', info } }),
```

- [ ] **Step 10.3 — Build, type-check**

Run: `cd frontend && pnpm --filter @cubeplex/core build && pnpm type-check`
Expected: green.

- [ ] **Step 10.4 — Commit**

```bash
git add frontend/packages/core/
git commit -m "feat(frontend): extend CitationMetadata + add attachment panel view"
```

---

## Phase 3 — Upload mechanics

### Task 11: `AbortSignal` support in `uploadAttachment`

**Files:**
- Modify: `frontend/packages/core/src/api/attachments.ts`
- Test: `frontend/packages/core/__tests__/api/attachments.test.ts` (new)

- [ ] **Step 11.1 — Write a failing test**

Create `frontend/packages/core/__tests__/api/attachments.test.ts`:

```ts
import { describe, expect, it, vi } from 'vitest'
import { uploadAttachment } from '../../src/api/attachments'
import { createApiClient } from '../../src/api/client'

class FakeXHR {
  upload = { onprogress: null as ((e: ProgressEvent) => void) | null }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  onabort: (() => void) | null = null
  status = 0
  responseText = ''
  withCredentials = false
  open = vi.fn()
  setRequestHeader = vi.fn()
  send = vi.fn()
  abort = vi.fn(() => {
    this.onabort?.()
  })
}

describe('uploadAttachment', () => {
  it('aborts the request when the signal fires', async () => {
    const xhr = new FakeXHR()
    vi.stubGlobal('XMLHttpRequest', vi.fn(() => xhr))
    const client = createApiClient('')
    const file = new File(['x'], 'a.txt')
    const ac = new AbortController()

    const promise = uploadAttachment(client, 'c1', file, undefined, ac.signal)
    ac.abort()
    await expect(promise).rejects.toMatchObject({ name: 'AbortError' })
    expect(xhr.abort).toHaveBeenCalledTimes(1)
    vi.unstubAllGlobals()
  })

  it('reports progress', async () => {
    const xhr = new FakeXHR()
    vi.stubGlobal('XMLHttpRequest', vi.fn(() => xhr))
    const client = createApiClient('')
    const file = new File(['x'], 'a.txt')
    const onProgress = vi.fn()
    const ac = new AbortController()
    const promise = uploadAttachment(client, 'c1', file, onProgress, ac.signal)
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 } as ProgressEvent)
    expect(onProgress).toHaveBeenCalledWith(0.5)
    ac.abort()
    await expect(promise).rejects.toBeDefined()
    vi.unstubAllGlobals()
  })
})
```

- [ ] **Step 11.2 — Run it and watch it fail**

Run: `cd frontend && pnpm --filter @cubeplex/core test -- attachments`
Expected: FAIL — `uploadAttachment` does not accept a 5th argument.

- [ ] **Step 11.3 — Add `signal` to `uploadAttachment`**

Edit `frontend/packages/core/src/api/attachments.ts`:

```ts
export async function uploadAttachment(
  client: ApiClient,
  conversationId: string,
  file: File,
  onProgress?: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<AttachmentDto> {
  const url = client.resolvePath(base(conversationId))
  const fd = new FormData()
  fd.append('file', file)

  return new Promise<AttachmentDto>((resolve, reject) => {
    if (signal?.aborted) {
      const err = new Error('aborted') as Error & { name: string }
      err.name = 'AbortError'
      reject(err)
      return
    }
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    xhr.withCredentials = true
    const csrf = document.cookie
      .split('; ')
      .find((c) => c.startsWith('cubeplex_csrf='))
      ?.split('=')[1]
    if (csrf) xhr.setRequestHeader('X-CSRF-Token', decodeURIComponent(csrf))

    const abortHandler = () => xhr.abort()
    signal?.addEventListener('abort', abortHandler)
    const cleanup = () => signal?.removeEventListener('abort', abortHandler)

    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable && onProgress) onProgress(ev.loaded / ev.total)
    }
    xhr.onload = () => {
      cleanup()
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText))
        } catch (e) {
          reject(e instanceof Error ? e : new Error(String(e)))
        }
      } else {
        try {
          const body = JSON.parse(xhr.responseText)
          reject(new Error(body.message || body.detail || `HTTP ${xhr.status}`))
        } catch {
          reject(new Error(`HTTP ${xhr.status}`))
        }
      }
    }
    xhr.onerror = () => {
      cleanup()
      reject(new Error('Network error'))
    }
    xhr.onabort = () => {
      cleanup()
      const err = new Error('aborted') as Error & { name: string }
      err.name = 'AbortError'
      reject(err)
    }
    xhr.send(fd)
  })
}
```

- [ ] **Step 11.4 — Run tests and watch them pass**

Run: `cd frontend && pnpm --filter @cubeplex/core test -- attachments`
Expected: PASS.

- [ ] **Step 11.5 — Build core**

Run: `cd frontend && pnpm --filter @cubeplex/core build`
Expected: clean.

- [ ] **Step 11.6 — Commit**

```bash
git add frontend/packages/core/
git commit -m "feat(frontend): support AbortSignal in uploadAttachment"
```

---

### Task 12: `cancel(tempId)` action in `attachmentStore`

**Files:**
- Modify: `frontend/packages/core/src/stores/attachmentStore.ts`
- Modify: `frontend/packages/core/__tests__/stores/attachmentStore.test.ts` (extend)

- [ ] **Step 12.1 — Read existing test patterns**

Open `frontend/packages/core/__tests__/stores/attachmentStore.test.ts` so the new tests blend in (mock setup, helpers, `useAttachmentStore.setState({ ... })` resets, etc).

- [ ] **Step 12.2 — Write the failing tests**

Append to `frontend/packages/core/__tests__/stores/attachmentStore.test.ts`:

```ts
describe('cancel', () => {
  it('aborts the in-flight upload and removes the staging entry', async () => {
    // Arrange a long-running upload
    let _resolve: (v: AttachmentDto) => void = () => {}
    const pending = new Promise<AttachmentDto>((res) => (_resolve = res))
    const uploadSpy = vi.fn().mockImplementation(
      (_c, _id, _f, _onP, signal: AbortSignal) =>
        new Promise<AttachmentDto>((_res, rej) => {
          signal.addEventListener('abort', () => {
            const err = new Error('aborted') as Error & { name: string }
            err.name = 'AbortError'
            rej(err)
          })
        }),
    )
    vi.doMock('../../src/api/attachments', async (orig) => {
      const actual = (await orig()) as object
      return { ...actual, uploadAttachment: uploadSpy }
    })
    // re-import after mock is set
    const { useAttachmentStore: store } = await import('../../src/stores/attachmentStore')
    store.setState({ staging: {} })

    const file = new File(['x'], 'a.txt')
    const client = {} as never
    const uploadPromise = store.getState().upload(client, 'c1', [file])

    // tempId is created synchronously in upload(); read it from staging
    const list = store.getState().staging['c1'] ?? []
    expect(list.length).toBe(1)
    const tempId = list[0].tempId

    await store.getState().cancel('c1', tempId)
    await uploadPromise

    expect(store.getState().staging['c1'] ?? []).toEqual([])
    expect(uploadSpy).toHaveBeenCalled()
  })

  it('does NOT call deleteAttachment when canceling an in-flight upload', async () => {
    const deleteSpy = vi.fn()
    vi.doMock('../../src/api/attachments', async (orig) => {
      const actual = (await orig()) as object
      return {
        ...actual,
        deleteAttachment: deleteSpy,
        uploadAttachment: vi
          .fn()
          .mockImplementation(
            (_c, _id, _f, _onP, signal: AbortSignal) =>
              new Promise<AttachmentDto>((_res, rej) =>
                signal.addEventListener('abort', () => {
                  const err = new Error('aborted') as Error & { name: string }
                  err.name = 'AbortError'
                  rej(err)
                }),
              ),
          ),
      }
    })
    const { useAttachmentStore: store } = await import('../../src/stores/attachmentStore')
    store.setState({ staging: {} })
    const promise = store.getState().upload({} as never, 'c2', [new File(['x'], 'b.txt')])
    const tempId = store.getState().staging['c2'][0].tempId
    await store.getState().cancel('c2', tempId)
    await promise
    expect(deleteSpy).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 12.3 — Run tests and watch them fail**

Run: `cd frontend && pnpm --filter @cubeplex/core test -- attachmentStore`
Expected: FAIL — `cancel` is undefined.

- [ ] **Step 12.4 — Implement `cancel` and thread the signal through `upload`**

Edit `frontend/packages/core/src/stores/attachmentStore.ts`:

```ts
import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import type { AttachmentDto } from '../types/attachment'
import { deleteAttachment, listAttachments, uploadAttachment } from '../api/attachments'

export interface UploadingFile {
  tempId: string
  filename: string
  size: number
  progress: number
  status: 'uploading' | 'done' | 'error'
  serverFile?: AttachmentDto
  error?: string
}

interface AttachmentStoreState {
  staging: Record<string, UploadingFile[]>

  upload(client: ApiClient, convId: string, files: File[]): Promise<void>
  cancel(convId: string, tempId: string): Promise<void>
  remove(client: ApiClient, convId: string, tempId: string): Promise<void>
  clear(convId: string): void
  attachedIds(convId: string): string[]
  hydrate(client: ApiClient, convId: string): Promise<void>
}

const newTempId = (): string => `tmp_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`

// In-memory map outside of zustand state — abort controllers are not serializable
// and zustand's structural sharing fights with mutable controllers.
const abortControllers: Record<string, AbortController> = {}

export const useAttachmentStore = create<AttachmentStoreState>((set, get) => ({
  staging: {},

  async upload(client, convId, files) {
    const next: UploadingFile[] = files.map((f) => ({
      tempId: newTempId(),
      filename: f.name,
      size: f.size,
      progress: 0,
      status: 'uploading',
    }))
    for (const item of next) abortControllers[item.tempId] = new AbortController()
    set((s) => ({
      staging: {
        ...s.staging,
        [convId]: [...(s.staging[convId] || []), ...next],
      },
    }))

    await Promise.all(
      next.map(async (item, idx) => {
        const controller = abortControllers[item.tempId]
        try {
          const dto = await uploadAttachment(
            client,
            convId,
            files[idx],
            (p) => {
              set((s) => {
                const list = (s.staging[convId] || []).map((u) =>
                  u.tempId === item.tempId ? { ...u, progress: p } : u,
                )
                return { staging: { ...s.staging, [convId]: list } }
              })
            },
            controller?.signal,
          )
          set((s) => {
            const list = (s.staging[convId] || []).map((u) =>
              u.tempId === item.tempId
                ? { ...u, progress: 1, status: 'done' as const, serverFile: dto }
                : u,
            )
            return { staging: { ...s.staging, [convId]: list } }
          })
        } catch (err) {
          const aborted = (err as Error)?.name === 'AbortError'
          if (aborted) {
            // Cancel path: silently drop the entry (cancel() already removed it,
            // but make sure even if it raced).
            set((s) => {
              const list = (s.staging[convId] || []).filter((u) => u.tempId !== item.tempId)
              return { staging: { ...s.staging, [convId]: list } }
            })
          } else {
            set((s) => {
              const list = (s.staging[convId] || []).map((u) =>
                u.tempId === item.tempId
                  ? { ...u, status: 'error' as const, error: String(err) }
                  : u,
              )
              return { staging: { ...s.staging, [convId]: list } }
            })
          }
        } finally {
          delete abortControllers[item.tempId]
        }
      }),
    )
  },

  async cancel(convId, tempId) {
    const controller = abortControllers[tempId]
    if (controller) {
      controller.abort()
      delete abortControllers[tempId]
    }
    set((s) => {
      const list = (s.staging[convId] || []).filter((u) => u.tempId !== tempId)
      return { staging: { ...s.staging, [convId]: list } }
    })
  },

  async remove(client, convId, tempId) {
    const item = (get().staging[convId] || []).find((u) => u.tempId === tempId)
    if (item?.serverFile) {
      try {
        await deleteAttachment(client, convId, item.serverFile.id)
      } catch {
        // best-effort — orphan reaper will clean it up server-side
      }
    }
    set((s) => {
      const list = (s.staging[convId] || []).filter((u) => u.tempId !== tempId)
      return { staging: { ...s.staging, [convId]: list } }
    })
  },

  clear(convId) {
    set((s) => {
      const next = { ...s.staging }
      delete next[convId]
      return { staging: next }
    })
  },

  attachedIds(convId) {
    return (get().staging[convId] || [])
      .filter((u) => u.status === 'done' && u.serverFile)
      .map((u) => u.serverFile!.id)
  },

  async hydrate(client, convId) {
    let list: Awaited<ReturnType<typeof listAttachments>>
    try {
      list = await listAttachments(client, convId, 'pending')
    } catch {
      return
    }
    if (!list.attachments.length) return
    set((s) => ({
      staging: {
        ...s.staging,
        [convId]: list.attachments.map((a) => ({
          tempId: newTempId(),
          filename: a.filename,
          size: a.size_bytes,
          progress: 1,
          status: 'done' as const,
          serverFile: a,
        })),
      },
    }))
  },
}))
```

- [ ] **Step 12.5 — Run tests and watch them pass**

Run: `cd frontend && pnpm --filter @cubeplex/core test`
Expected: green (existing tests + new `cancel` tests).

- [ ] **Step 12.6 — Build core**

Run: `cd frontend && pnpm --filter @cubeplex/core build`

- [ ] **Step 12.7 — Commit**

```bash
git add frontend/packages/core/
git commit -m "feat(frontend): cancel action on attachmentStore aborts in-flight upload"
```

---

### Task 13: Home-page eager-create flow

**Files:**
- Modify: `frontend/packages/web/app/(app)/w/[wsId]/page.tsx`

The home page must:
1. On the first file selection (when `pendingFiles` is empty AND no `convId` exists), create a conversation and set it as active.
2. Pass that `convId` into `InputBar` so subsequent uploads use the existing per-conversation flow.
3. On submit, send the message and only then `router.push` to the conversation page.

The cleanest way is to lift the `convId` state into the home page itself and pass it as a prop to `InputBar`. We don't need a new prop — `InputBar` already accepts `conversationId` and uses it the same way as the conversation page.

- [ ] **Step 13.1 — Rewrite the home page**

Replace `frontend/packages/web/app/(app)/w/[wsId]/page.tsx` with:

```tsx
'use client'

import { use, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import {
  createApiClient,
  useAttachmentStore,
  useConversationStore,
  useMessageStore,
} from '@cubeplex/core'
import { InputBar } from '@/components/layout/InputBar'
import { Box } from 'lucide-react'

export default function WorkspaceHomePage({
  params,
}: {
  params: Promise<{ wsId: string }>
}): React.ReactElement {
  const t = useTranslations('home')
  const { wsId } = use(params)
  const router = useRouter()
  const { create: createConversation } = useConversationStore()
  const send = useMessageStore((s) => s.send)
  const [draftConvId, setDraftConvId] = useState<string | null>(null)

  const ensureConversation = useCallback(async (): Promise<string> => {
    if (draftConvId) return draftConvId
    const client = createApiClient('')
    client.setWorkspaceId(wsId)
    const convo = await createConversation(client, 'New chat')
    useConversationStore.setState({ activeId: convo.id })
    setDraftConvId(convo.id)
    return convo.id
  }, [draftConvId, wsId, createConversation])

  const handleSubmit = async (content: string): Promise<void> => {
    const client = createApiClient('')
    client.setWorkspaceId(wsId)
    try {
      const convId = await ensureConversation()

      const attachedIds = useAttachmentStore.getState().attachedIds(convId)
      if (!content.trim() && attachedIds.length === 0) return

      // Update the title to something more useful than "New chat".
      const title = content.trim() ? content.trim().slice(0, 30) : 'Files'
      await client.put(`/api/v1/conversations/${convId}/title`, { title }).catch(() => {})

      useAttachmentStore.getState().clear(convId)
      send(client, convId, content, attachedIds).catch((err) => {
        console.error('Failed to send message:', err)
      })
      router.push(`/w/${wsId}/conversations/${convId}`)
    } catch (err) {
      console.error('Failed to create conversation:', err)
    }
  }

  return (
    <div className="flex-1 flex flex-col items-center justify-center">
      <div className="text-center mb-8">
        <div className="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-primary/10 border border-primary/20 mb-5">
          <Box className="size-6 text-primary" strokeWidth={2} />
        </div>
        <h1 className="text-2xl font-semibold tracking-tight mb-1.5">cubeplex</h1>
        <p className="text-sm text-muted-foreground/70">{t('subtitle')}</p>
      </div>
      <div className="w-full max-w-2xl px-4">
        <InputBar
          conversationId={draftConvId ?? undefined}
          onCreateConversation={ensureConversation}
          onSubmit={handleSubmit}
        />
      </div>
    </div>
  )
}
```

- [ ] **Step 13.2 — Add `onCreateConversation` to `InputBar`**

Edit `frontend/packages/web/components/layout/InputBar.tsx` — change the props and the file selection handler so that when there's no `conversationId` and an `onCreateConversation` is provided, picking a file calls it first, then uploads to that new id:

Add to the props interface:

```ts
interface InputBarProps {
  conversationId?: string
  onSubmit?: (content: string, files: File[]) => void | Promise<void>
  onCreateConversation?: () => Promise<string>
  isLoading?: boolean
}
```

Replace `handleFiles`:

```ts
  const handleFiles = async (files: FileList | null): Promise<void> => {
    if (!files || !files.length) return
    const selectedFiles = Array.from(files)
    let convId = conversationId
    if (!convId && onCreateConversation) {
      try {
        convId = await onCreateConversation()
      } catch (err) {
        console.error('Failed to create conversation for upload:', err)
        return
      }
    }
    if (!convId) {
      // No conversation context and no creator provided — fall back to the
      // legacy local-pending behaviour (only used by callers that don't pass
      // onCreateConversation, e.g. tests).
      if (onSubmit) setPendingFiles((current) => [...current, ...selectedFiles])
      return
    }
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    await upload(client, convId, selectedFiles)
  }
```

Update the function signature accordingly:

```ts
export function InputBar({
  conversationId,
  onSubmit,
  onCreateConversation,
  isLoading = false,
}: InputBarProps): React.ReactElement {
```

The submit path remains unchanged for the existing-conversation case. For the new-conversation case, `attachedIds` now correctly reflects already-uploaded files (because we uploaded them to `draftConvId` immediately when selected); the home page's `handleSubmit` reads them via `useAttachmentStore.getState().attachedIds(convId)`.

- [ ] **Step 13.3 — Update existing tests**

`frontend/packages/web/__tests__/components/InputBar.test.tsx` and `frontend/packages/web/__tests__/components/WorkspaceHomePage.test.tsx` may be asserting against the old `pendingFiles` flow. Read them and adjust:
- `WorkspaceHomePage.test.tsx`: assertions about `onSubmit(content, files)` should change — files are now already uploaded by the time submit runs. Mock `useAttachmentStore` to return staged items.
- `InputBar.test.tsx`: tests of the `pendingFiles` UI (which ran in the no-`conversationId` no-`onCreateConversation` path) keep working because that branch is preserved.

Read the existing tests, adjust assertions where they're now describing wrong behaviour, and add a new test:

```tsx
it('creates a draft conversation on first file pick when onCreateConversation is provided', async () => {
  const create = vi.fn().mockResolvedValue('conv-1')
  const onSubmit = vi.fn()
  // ... render <InputBar onCreateConversation={create} onSubmit={onSubmit} />
  // ... fire change event on the hidden file input with one File
  expect(create).toHaveBeenCalledTimes(1)
})
```

- [ ] **Step 13.4 — Type-check**

Run: `cd frontend && pnpm type-check`
Expected: green.

- [ ] **Step 13.5 — Run unit tests**

Run: `cd frontend && pnpm --filter web test`
Expected: green.

- [ ] **Step 13.6 — Commit**

```bash
git add frontend/packages/web/app/\(app\)/w/\[wsId\]/page.tsx \
        frontend/packages/web/components/layout/InputBar.tsx \
        frontend/packages/web/__tests__/components/
git commit -m "feat(frontend): home page eagerly creates draft conversation on file pick"
```

---

## Phase 4 — Chip components

### Task 14: New `FileChip` (input-bar version)

**Files:**
- Create: `frontend/packages/web/components/chat/FileChip.tsx`

- [ ] **Step 14.1 — Implement `FileChip`**

Create `frontend/packages/web/components/chat/FileChip.tsx`:

```tsx
'use client'

import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import type { UploadingFile } from '@cubeplex/core'
import { getFileVisual } from '@/lib/fileIcons'
import { cn } from '@/lib/utils'

interface Props {
  item: UploadingFile
  thumbnailUrl?: string | null
  onCancel: () => void
}

export function FileChip({ item, thumbnailUrl, onCancel }: Props): React.ReactElement {
  const [mounted, setMounted] = useState(false)
  useEffect(() => {
    requestAnimationFrame(() => setMounted(true))
  }, [])

  const visual = getFileVisual({ filename: item.filename })
  const isUploading = item.status === 'uploading'
  const isError = item.status === 'error'
  const radius = 18
  const circumference = 2 * Math.PI * radius
  const offset = (1 - item.progress) * circumference

  return (
    <div
      className={cn(
        'group relative inline-flex items-center gap-2 rounded-lg border border-border bg-card pl-2 pr-2.5 py-1.5 text-xs transition-all duration-150 ease-out',
        mounted ? 'opacity-100 scale-100' : 'opacity-0 scale-[0.96]',
      )}
    >
      <div className={cn('relative size-10 shrink-0 rounded-md grid place-items-center', visual.bg)}>
        {thumbnailUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={thumbnailUrl} alt={item.filename} className="absolute inset-0 size-full rounded-md object-cover" />
        ) : (
          <visual.Icon className={cn('size-5', visual.fg)} />
        )}
        {(isUploading || isError) && (
          <svg className="absolute inset-0" viewBox="0 0 40 40" aria-hidden>
            <circle
              cx="20"
              cy="20"
              r={radius}
              fill="none"
              stroke="currentColor"
              className="text-white/25"
              strokeWidth="2"
            />
            <circle
              cx="20"
              cy="20"
              r={radius}
              fill="none"
              stroke="currentColor"
              className={isError ? 'text-rose-200' : 'text-white'}
              strokeWidth="2"
              strokeDasharray={circumference}
              strokeDashoffset={offset}
              strokeLinecap="round"
              transform="rotate(-90 20 20)"
              style={{ transition: 'stroke-dashoffset 200ms ease-out' }}
            />
          </svg>
        )}
      </div>
      <div className="flex flex-col leading-tight max-w-[160px]">
        <span className="truncate font-medium" title={item.filename}>
          {item.filename}
        </span>
        <span
          className={cn(
            'text-[10px] truncate',
            isError ? 'text-destructive' : 'text-muted-foreground',
          )}
        >
          {isError ? 'Upload failed' : visual.label}
        </span>
      </div>
      <button
        type="button"
        onClick={onCancel}
        className="absolute -right-1.5 -top-1.5 grid size-5 place-items-center rounded-full bg-foreground text-background hover:scale-110 transition-transform"
        aria-label={`Remove ${item.filename}`}
      >
        <X className="size-3" />
      </button>
    </div>
  )
}
```

- [ ] **Step 14.2 — Verify imports resolve**

Run: `cd frontend && pnpm type-check`
Expected: green.

- [ ] **Step 14.3 — Commit**

```bash
git add frontend/packages/web/components/chat/FileChip.tsx
git commit -m "feat(frontend): add FileChip with progress ring for input bar"
```

---

### Task 15: Wire `FileChip` into `AttachmentChips` and delete the old chip

**Files:**
- Modify: `frontend/packages/web/components/chat/AttachmentChips.tsx`
- Delete: `frontend/packages/web/components/chat/AttachmentChip.tsx`

- [ ] **Step 15.1 — Replace `AttachmentChips` body**

Edit `frontend/packages/web/components/chat/AttachmentChips.tsx`:

```tsx
'use client'

import { useAttachmentStore } from '@cubeplex/core'
import { FileChip } from './FileChip'

interface Props {
  conversationId: string
}

export function AttachmentChips({ conversationId }: Props) {
  const items = useAttachmentStore((s) => s.staging[conversationId] || [])
  const cancel = useAttachmentStore((s) => s.cancel)
  const remove = useAttachmentStore((s) => s.remove)
  const removeAttachment = (tempId: string) => {
    const item = items.find((u) => u.tempId === tempId)
    if (!item) return
    if (item.status === 'uploading') {
      void cancel(conversationId, tempId)
    } else {
      // For completed/error states we still call remove (which goes through
      // the server DELETE for done, no-op for error).
      const { createApiClient } = require('@cubeplex/core') as typeof import('@cubeplex/core')
      const client = createApiClient('')
      void remove(client, conversationId, tempId)
    }
  }

  if (items.length === 0) return null

  return (
    <div className="flex flex-wrap gap-2 pb-2">
      {items.map((item) => (
        <FileChip
          key={item.tempId}
          item={item}
          thumbnailUrl={item.serverFile?.thumbnail_url ?? null}
          onCancel={() => removeAttachment(item.tempId)}
        />
      ))}
    </div>
  )
}
```

The require trick avoids a circular import at module load. If the codebase uses ESM-only imports throughout, replace with a top-level `import { createApiClient } from '@cubeplex/core'` and pull `workspaceId` from `useWorkspaceContext()` for the DELETE call (mirror the previous pattern):

```tsx
import { createApiClient, useAttachmentStore } from '@cubeplex/core'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { FileChip } from './FileChip'

// inside the component:
const { workspaceId } = useWorkspaceContext()
const removeAttachment = async (tempId: string) => {
  const item = items.find((u) => u.tempId === tempId)
  if (!item) return
  if (item.status === 'uploading') {
    await cancel(conversationId, tempId)
  } else {
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    await remove(client, conversationId, tempId)
  }
}
```

(Use the second pattern; it matches the repo conventions and avoids `require`.)

- [ ] **Step 15.2 — Delete the old chip component**

```bash
rm frontend/packages/web/components/chat/AttachmentChip.tsx
```

Run a quick grep to make sure nothing else imports it:

```bash
grep -rn "AttachmentChip\b" frontend/packages | grep -v "AttachmentChips" | grep -v node_modules
```

Expected: no remaining references (other than `AttachmentChips`).

- [ ] **Step 15.3 — Type-check and run unit tests**

Run: `cd frontend && pnpm type-check && pnpm --filter web test`
Expected: green.

- [ ] **Step 15.4 — Commit**

```bash
git add frontend/packages/web/components/chat/AttachmentChips.tsx
git rm frontend/packages/web/components/chat/AttachmentChip.tsx
git commit -m "refactor(frontend): replace AttachmentChip with FileChip"
```

---

### Task 16: New `MessageFileChip` (sent-message version)

**Files:**
- Create: `frontend/packages/web/components/chat/MessageFileChip.tsx`

- [ ] **Step 16.1 — Implement `MessageFileChip`**

Create `frontend/packages/web/components/chat/MessageFileChip.tsx`:

```tsx
'use client'

import { usePanelStore } from '@cubeplex/core'
import { getFileVisual } from '@/lib/fileIcons'
import { cn } from '@/lib/utils'

export interface MessageFileChipProps {
  attachmentId: string
  filename: string
  mimeType: string
  sizeBytes: number
  downloadUrl: string
  onOpenImage?: (downloadUrl: string, filename: string) => void
}

const PANEL_FAMILIES = new Set([
  'pdf',
  'markdown',
  'text',
  'code',
  'json',
  'csv',
  'video',
  'audio',
])

export function MessageFileChip({
  attachmentId,
  filename,
  mimeType,
  sizeBytes,
  downloadUrl,
  onOpenImage,
}: MessageFileChipProps): React.ReactElement {
  const openAttachment = usePanelStore((s) => s.openAttachment)
  const visual = getFileVisual({ filename, mime_type: mimeType })

  const handleClick: React.MouseEventHandler = (e) => {
    if (visual.family === 'image' && onOpenImage) {
      e.preventDefault()
      onOpenImage(downloadUrl, filename)
      return
    }
    if (PANEL_FAMILIES.has(visual.family)) {
      e.preventDefault()
      openAttachment({ attachmentId, filename, downloadUrl, mimeType, sizeBytes })
      return
    }
    // word/excel/ppt/archive/unknown → let the <a> default download behavior run
  }

  return (
    <a
      href={downloadUrl}
      download
      onClick={handleClick}
      className="inline-flex items-center gap-2 rounded-lg border border-border bg-card px-2 py-1.5 text-[11px] hover:bg-muted/40 transition-colors"
    >
      <div className={cn('size-9 shrink-0 rounded-md grid place-items-center', visual.bg)}>
        <visual.Icon className={cn('size-4', visual.fg)} />
      </div>
      <div className="flex flex-col leading-tight max-w-[140px]">
        <span className="truncate font-medium" title={filename}>
          {filename}
        </span>
        <span className="text-[10px] text-muted-foreground truncate">{visual.label}</span>
      </div>
    </a>
  )
}
```

- [ ] **Step 16.2 — Type-check**

Run: `cd frontend && pnpm type-check`
Expected: green.

- [ ] **Step 16.3 — Commit**

```bash
git add frontend/packages/web/components/chat/MessageFileChip.tsx
git commit -m "feat(frontend): add MessageFileChip for sent messages"
```

---

### Task 17: Update `MessageAttachments` to use `MessageFileChip`

**Files:**
- Modify: `frontend/packages/web/components/chat/MessageAttachments.tsx`

- [ ] **Step 17.1 — Rewrite `MessageAttachments`**

Replace `frontend/packages/web/components/chat/MessageAttachments.tsx`:

```tsx
'use client'

import { useState, useMemo } from 'react'
import { createApiClient } from '@cubeplex/core'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { ImageLightbox } from './ImageLightbox'
import { MessageFileChip } from './MessageFileChip'

export interface MessageAttachmentDto {
  id: string
  filename: string
  kind: 'image' | 'document' | 'other'
  mime_type?: string
  size_bytes: number
  width?: number | null
  height?: number | null
  thumbnail_url?: string | null
  download_url: string
}

interface Props {
  attachments: MessageAttachmentDto[]
  conversationId: string
}

export function MessageAttachments({ attachments, conversationId }: Props) {
  const { workspaceId } = useWorkspaceContext()
  const [openSrc, setOpenSrc] = useState<{ src: string; alt: string } | null>(null)

  const resolved = useMemo(() => {
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    const baseApi = `/api/v1/conversations/${conversationId}/attachments`
    const fix = (url: string | null | undefined): string => {
      if (!url) return ''
      if (url.startsWith('./attachments/')) {
        const tail = url.slice('./attachments/'.length)
        return client.resolvePath(`${baseApi}/${tail}`)
      }
      return url
    }
    return attachments.map((a) => ({
      ...a,
      thumbnail_url: a.thumbnail_url ? fix(a.thumbnail_url) : null,
      download_url: fix(a.download_url),
    }))
  }, [attachments, conversationId, workspaceId])

  if (!resolved.length) return null

  return (
    <div
      className="flex flex-wrap gap-1.5 justify-end max-w-[72%] ml-auto mb-1.5"
      data-testid="message-attachments"
    >
      {resolved.map((a) => {
        if (a.kind === 'image' && a.thumbnail_url) {
          return (
            <button
              key={a.id}
              type="button"
              onClick={() => setOpenSrc({ src: a.download_url, alt: a.filename })}
              className="group relative overflow-hidden rounded-lg border border-border"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={a.thumbnail_url}
                alt={a.filename}
                className="size-24 object-cover transition group-hover:scale-105"
              />
              <span className="absolute bottom-0 left-0 right-0 truncate bg-background/80 px-1 py-0.5 text-[10px]">
                {a.filename}
              </span>
            </button>
          )
        }
        return (
          <MessageFileChip
            key={a.id}
            attachmentId={a.id}
            filename={a.filename}
            mimeType={a.mime_type ?? ''}
            sizeBytes={a.size_bytes}
            downloadUrl={a.download_url}
            onOpenImage={(src, alt) => setOpenSrc({ src, alt })}
          />
        )
      })}
      {openSrc && (
        <ImageLightbox src={openSrc.src} alt={openSrc.alt} onClose={() => setOpenSrc(null)} />
      )}
    </div>
  )
}
```

- [ ] **Step 17.2 — Type-check**

Run: `cd frontend && pnpm type-check`
Expected: green.

- [ ] **Step 17.3 — Run existing component test**

Run: `cd frontend && pnpm --filter web test -- MessageAttachments`
Expected: any failures here come from the layout changes; update the assertions in `MessageAttachments.test.tsx` to:
- still find `data-testid="message-attachments"`
- expect non-image attachments to render via `MessageFileChip` (assert filename + label, not the old `<a>` with `Download` icon).

- [ ] **Step 17.4 — Commit**

```bash
git add frontend/packages/web/components/chat/MessageAttachments.tsx \
        frontend/packages/web/__tests__/components/MessageAttachments.test.tsx
git commit -m "feat(frontend): MessageAttachments uses MessageFileChip + new layout"
```

---

### Task 18: `MessageList` — render attachments above the user bubble

**Files:**
- Modify: `frontend/packages/web/components/chat/MessageList.tsx:191-204`

- [ ] **Step 18.1 — Swap the order**

Edit `frontend/packages/web/components/chat/MessageList.tsx` — find the `msg.role === 'user'` branch and reorder:

```tsx
            {msg.role === 'user' && (
              <>
                {msg.attachments && msg.attachments.length > 0 && (
                  <MessageAttachments
                    attachments={msg.attachments}
                    conversationId={conversationId}
                  />
                )}
                <UserMessage content={msg.content ?? ''} />
              </>
            )}
```

(`MessageAttachments` already returns its own right-aligned wrap container; no outer `flex justify-end` wrapper is needed any more.)

- [ ] **Step 18.2 — Type-check + tests**

Run: `cd frontend && pnpm type-check && pnpm --filter web test`
Expected: green.

- [ ] **Step 18.3 — Commit**

```bash
git add frontend/packages/web/components/chat/MessageList.tsx
git commit -m "feat(frontend): render user message attachments above the bubble"
```

---

## Phase 5 — Panel views

### Task 19: `AttachmentPreviewView`

**Files:**
- Create: `frontend/packages/web/components/panel/AttachmentPreviewView.tsx`

- [ ] **Step 19.1 — Create the component**

Create `frontend/packages/web/components/panel/AttachmentPreviewView.tsx`:

```tsx
'use client'

import { useEffect, useState } from 'react'
import { Download, X } from 'lucide-react'
import type { AttachmentPanelInfo } from '@cubeplex/core'
import { usePanelStore } from '@cubeplex/core'
import { ScrollArea } from '@/components/ui/scroll-area'
import { MarkdownWithCitations } from '@/components/shared/MarkdownWithCitations'
import { PdfPreview } from '@/components/panel/artifact/PdfPreview'
import { getFileVisual } from '@/lib/fileIcons'
import { cn } from '@/lib/utils'

const TEXT_MAX_BYTES = 5 * 1024 * 1024

interface Props {
  info: AttachmentPanelInfo
}

function humanSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export function AttachmentPreviewView({ info }: Props): React.ReactElement {
  const close = usePanelStore((s) => s.close)
  const visual = getFileVisual({ filename: info.filename, mime_type: info.mimeType })

  return (
    <div className="flex h-full flex-col bg-background">
      <header className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <div className={cn('size-7 grid place-items-center rounded', visual.bg)}>
          <visual.Icon className={cn('size-3.5', visual.fg)} />
        </div>
        <div className="flex flex-col leading-tight min-w-0">
          <span className="truncate text-sm font-medium" title={info.filename}>
            {info.filename}
          </span>
          <span className="text-[10px] text-muted-foreground">
            {visual.label} · {humanSize(info.sizeBytes)}
          </span>
        </div>
        <a
          href={info.downloadUrl}
          download
          className="ml-auto grid size-7 place-items-center rounded hover:bg-muted"
          aria-label="Download"
        >
          <Download className="size-3.5" />
        </a>
        <button
          type="button"
          onClick={close}
          className="grid size-7 place-items-center rounded hover:bg-muted"
          aria-label="Close"
        >
          <X className="size-3.5" />
        </button>
      </header>
      <Body info={info} family={visual.family} />
    </div>
  )
}

function Body({ info, family }: { info: AttachmentPanelInfo; family: string }): React.ReactElement {
  if (family === 'pdf') {
    return <PdfPreview src={info.downloadUrl} />
  }
  if (family === 'video') {
    return (
      <div className="flex-1 grid place-items-center bg-black">
        {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
        <video src={info.downloadUrl} controls className="max-h-full max-w-full" />
      </div>
    )
  }
  if (family === 'audio') {
    return (
      <div className="flex-1 grid place-items-center p-8">
        <audio src={info.downloadUrl} controls />
      </div>
    )
  }
  if (info.sizeBytes > TEXT_MAX_BYTES) {
    return (
      <div className="flex-1 grid place-items-center p-8 text-center text-sm text-muted-foreground">
        File is too large for inline preview ({(info.sizeBytes / 1024 / 1024).toFixed(1)} MB).
        Please download to view.
      </div>
    )
  }
  return <TextBody info={info} family={family} />
}

function TextBody({
  info,
  family,
}: {
  info: AttachmentPanelInfo
  family: string
}): React.ReactElement {
  const [state, setState] = useState<
    | { kind: 'loading' }
    | { kind: 'error'; message: string }
    | { kind: 'ready'; text: string }
  >({ kind: 'loading' })

  useEffect(() => {
    const ac = new AbortController()
    setState({ kind: 'loading' })
    void (async () => {
      try {
        const res = await fetch(info.downloadUrl, {
          credentials: 'include',
          signal: ac.signal,
        })
        if (!res.ok) {
          setState({ kind: 'error', message: `HTTP ${res.status}` })
          return
        }
        const text = await res.text()
        if (!ac.signal.aborted) setState({ kind: 'ready', text })
      } catch (err) {
        if (!ac.signal.aborted) {
          setState({ kind: 'error', message: (err as Error).message ?? 'Failed to load' })
        }
      }
    })()
    return () => ac.abort()
  }, [info.downloadUrl])

  if (state.kind === 'loading') {
    return (
      <div className="flex-1 grid place-items-center p-8 text-sm text-muted-foreground">
        Loading…
      </div>
    )
  }
  if (state.kind === 'error') {
    return (
      <div className="flex-1 grid place-items-center p-8 text-sm text-destructive">
        Failed to load: {state.message}
      </div>
    )
  }
  return (
    <ScrollArea className="flex-1 p-4">
      {family === 'markdown' ? (
        <MarkdownWithCitations className="prose prose-sm dark:prose-invert max-w-none">
          {state.text}
        </MarkdownWithCitations>
      ) : family === 'csv' ? (
        <CsvTable text={state.text} />
      ) : family === 'json' ? (
        <pre className="font-mono text-sm whitespace-pre-wrap break-all">
          {prettyJson(state.text)}
        </pre>
      ) : (
        <pre className="font-mono text-sm whitespace-pre-wrap break-all">{state.text}</pre>
      )}
    </ScrollArea>
  )
}

function prettyJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

function CsvTable({ text }: { text: string }): React.ReactElement {
  const lines = text.split(/\r?\n/).filter(Boolean).slice(0, 1000)
  if (lines.length === 0) return <div className="p-4 text-sm text-muted-foreground">Empty</div>
  const rows = lines.map((line) => line.split(','))
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-xs">
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={i === 0 ? 'bg-muted font-medium' : ''}>
              {row.map((cell, j) => (
                <td key={j} className="border border-border px-2 py-1 align-top">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 19.2 — Type-check**

Run: `cd frontend && pnpm type-check`
Expected: green. If `PdfPreview`'s prop signature differs from `{ src }`, look at its actual signature in `components/panel/artifact/PdfPreview.tsx` and adapt.

- [ ] **Step 19.3 — Commit**

```bash
git add frontend/packages/web/components/panel/AttachmentPreviewView.tsx
git commit -m "feat(frontend): AttachmentPreviewView for chip-click previews"
```

---

### Task 20: `FileReadView`

**Files:**
- Create: `frontend/packages/web/components/panel/FileReadView.tsx`

- [ ] **Step 20.1 — Create the component**

Create `frontend/packages/web/components/panel/FileReadView.tsx`:

```tsx
'use client'

import { useEffect, useMemo, useRef } from 'react'
import { AlertTriangle, FileQuestion, Info } from 'lucide-react'
import { MarkdownWithCitations } from '@/components/shared/MarkdownWithCitations'
import { getFileVisual } from '@/lib/fileIcons'
import { cn } from '@/lib/utils'

interface Props {
  args: Record<string, unknown>
  result: string | null
  highlightText?: string | null
  highlightKey?: number
}

interface TextOutput {
  kind: 'text'
  path: string
  mime: string
  content: string
  size_bytes: number
  truncated?: boolean
  metadata?: Record<string, unknown>
}
interface NotebookCell {
  cell_type: 'code' | 'markdown' | 'raw'
  source: string
  outputs?: Array<Record<string, unknown>> | null
}
interface NotebookOutput {
  kind: 'notebook'
  path: string
  cells: NotebookCell[]
}
interface UnsupportedOutput {
  kind: 'unsupported'
  path: string
  mime: string
  size_bytes: number
  reason: string
  hint?: string
}
interface UnchangedOutput {
  kind: 'unchanged'
  path: string
}
interface ErrorOutput {
  kind: 'error'
  path: string
  error: string
  retryable?: boolean
}
type FileReadResult =
  | TextOutput
  | NotebookOutput
  | UnsupportedOutput
  | UnchangedOutput
  | ErrorOutput
  | { kind: string; path?: string }

function parseResult(raw: string | null): FileReadResult | null {
  if (!raw) return null
  try {
    return JSON.parse(raw) as FileReadResult
  } catch {
    return null
  }
}

function basename(path: string): string {
  const i = path.lastIndexOf('/')
  return i >= 0 ? path.slice(i + 1) : path
}

function humanSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export function FileReadView({
  args,
  result,
  highlightText,
  highlightKey,
}: Props): React.ReactElement {
  const parsed = useMemo(() => parseResult(result), [result])
  const path = parsed?.path ?? String(args.path ?? '')
  const visual = getFileVisual({ filename: basename(path) })

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <div className={cn('size-7 grid place-items-center rounded', visual.bg)}>
          <visual.Icon className={cn('size-3.5', visual.fg)} />
        </div>
        <div className="flex flex-col leading-tight min-w-0">
          <span className="truncate text-sm font-medium" title={path}>
            {basename(path) || '(untitled)'}
          </span>
          <span className="text-[10px] text-muted-foreground truncate" title={path}>
            {path}
          </span>
        </div>
      </header>
      <MetaStrip parsed={parsed} args={args} />
      <Body parsed={parsed} highlightText={highlightText} highlightKey={highlightKey} />
    </div>
  )
}

function MetaStrip({
  parsed,
  args,
}: {
  parsed: FileReadResult | null
  args: Record<string, unknown>
}): React.ReactElement | null {
  if (!parsed) return null
  const range = (args.page_range || args.line_range) as string | undefined
  const chips: React.ReactNode[] = []
  if (parsed.kind === 'text') {
    const t = parsed as TextOutput
    chips.push(<Chip key="mime">{t.mime}</Chip>)
    chips.push(<Chip key="size">{humanSize(t.size_bytes)}</Chip>)
    chips.push(<Chip key="chars">{t.content.length.toLocaleString()} chars</Chip>)
    if (t.truncated) {
      chips.push(
        <Chip key="trunc" tone="warn">
          <AlertTriangle className="size-3" /> Truncated
        </Chip>,
      )
    }
  } else if (parsed.kind === 'notebook') {
    const nb = parsed as NotebookOutput
    const code = nb.cells.filter((c) => c.cell_type === 'code').length
    const md = nb.cells.filter((c) => c.cell_type === 'markdown').length
    chips.push(<Chip key="cells">{nb.cells.length} cells</Chip>)
    chips.push(<Chip key="code">{code} code</Chip>)
    chips.push(<Chip key="md">{md} md</Chip>)
  } else if (parsed.kind === 'unsupported') {
    const u = parsed as UnsupportedOutput
    chips.push(<Chip key="mime">{u.mime}</Chip>)
    chips.push(<Chip key="size">{humanSize(u.size_bytes)}</Chip>)
  }
  if (range) chips.push(<Chip key="range">Range: {range}</Chip>)
  if (!chips.length) return null
  return <div className="flex flex-wrap gap-1.5 border-b border-border px-4 py-2">{chips}</div>
}

function Chip({
  children,
  tone = 'normal',
}: {
  children: React.ReactNode
  tone?: 'normal' | 'warn'
}): React.ReactElement {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px]',
        tone === 'warn'
          ? 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400'
          : 'border-border bg-muted text-muted-foreground',
      )}
    >
      {children}
    </span>
  )
}

function Body({
  parsed,
  highlightText,
  highlightKey,
}: {
  parsed: FileReadResult | null
  highlightText?: string | null
  highlightKey?: number
}): React.ReactElement {
  const bodyRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!highlightText || !bodyRef.current) return
    const el = bodyRef.current
    const text = el.textContent ?? ''
    const search = highlightText.slice(0, 50)
    if (text.includes(search)) {
      el.classList.add('ring-2', 'ring-primary/50')
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
    return () => {
      el.classList.remove('ring-2', 'ring-primary/50')
    }
  }, [highlightText, highlightKey])

  if (!parsed) {
    return <div className="flex-1 p-4 text-sm text-muted-foreground">No result.</div>
  }

  if (parsed.kind === 'text') {
    return (
      <div ref={bodyRef} className="flex-1 overflow-y-auto p-4">
        <MarkdownWithCitations className="prose prose-sm dark:prose-invert max-w-none">
          {(parsed as TextOutput).content}
        </MarkdownWithCitations>
      </div>
    )
  }
  if (parsed.kind === 'notebook') {
    const nb = parsed as NotebookOutput
    return (
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {nb.cells.map((cell, i) => (
          <div key={i} className="rounded-lg border border-border bg-card p-3">
            <div className="mb-2 text-[10px] uppercase tracking-wider text-muted-foreground">
              {cell.cell_type === 'code' ? `In [${i + 1}]` : cell.cell_type}
            </div>
            {cell.cell_type === 'markdown' ? (
              <MarkdownWithCitations className="prose prose-sm dark:prose-invert max-w-none">
                {cell.source}
              </MarkdownWithCitations>
            ) : (
              <pre className="font-mono text-xs whitespace-pre-wrap break-all">{cell.source}</pre>
            )}
            {cell.outputs && cell.outputs.length > 0 && (
              <div className="mt-2 border-t border-border pt-2">
                {cell.outputs.map((out, j) => (
                  <NotebookOutputBlock key={j} out={out} />
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    )
  }
  if (parsed.kind === 'unsupported') {
    const u = parsed as UnsupportedOutput
    return (
      <div className="flex-1 grid place-items-center p-8 text-center">
        <div className="space-y-3 max-w-sm">
          <FileQuestion className="mx-auto size-10 text-muted-foreground" />
          <h3 className="text-base font-medium">Unsupported format</h3>
          <p className="text-sm text-muted-foreground">{u.reason}</p>
          {u.hint && (
            <div className="flex items-start gap-2 rounded-md border border-blue-500/30 bg-blue-500/10 px-3 py-2 text-left text-xs text-blue-700 dark:text-blue-400">
              <Info className="size-3.5 shrink-0 mt-0.5" />
              <span>{u.hint}</span>
            </div>
          )}
        </div>
      </div>
    )
  }
  if (parsed.kind === 'unchanged') {
    return (
      <div className="flex-1 grid place-items-center p-8 text-sm text-muted-foreground">
        File unchanged since the previous read.
      </div>
    )
  }
  if (parsed.kind === 'error') {
    const e = parsed as ErrorOutput
    return (
      <div className="flex-1 p-4">
        <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {e.error}
          {e.retryable ? ' (retryable)' : ''}
        </div>
      </div>
    )
  }
  return <div className="flex-1 p-4 text-sm text-muted-foreground">Unknown result kind.</div>
}

function NotebookOutputBlock({ out }: { out: Record<string, unknown> }): React.ReactElement {
  // Stdout/stderr text
  const text = (out.text ?? '') as string
  if (typeof text === 'string' && text) {
    return <pre className="font-mono text-xs whitespace-pre-wrap break-all">{text}</pre>
  }
  const data = out.data as Record<string, unknown> | undefined
  if (data?.['image/png']) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={`data:image/png;base64,${data['image/png']}`}
        alt="output"
        className="max-w-full"
      />
    )
  }
  return (
    <pre className="font-mono text-xs whitespace-pre-wrap break-all text-muted-foreground">
      {JSON.stringify(out, null, 2)}
    </pre>
  )
}
```

- [ ] **Step 20.2 — Type-check**

Run: `cd frontend && pnpm type-check`
Expected: green.

- [ ] **Step 20.3 — Commit**

```bash
git add frontend/packages/web/components/panel/FileReadView.tsx
git commit -m "feat(frontend): FileReadView renders file_read tool results"
```

---

### Task 21: Wire `FileReadView` and `AttachmentPreviewView` into the panel

**Files:**
- Modify: `frontend/packages/web/components/panel/ToolDetailPanel.tsx`
- Modify: the right-panel container (find via grep on `panelStore.view` / `useToolDetail`)

- [ ] **Step 21.1 — Add `FileReadView` branch to `ToolDetailPanel`**

Edit `frontend/packages/web/components/panel/ToolDetailPanel.tsx`:

```tsx
import { FileReadView } from './FileReadView'
// ... after the existing branches inside <ScrollArea>:

{contentType === 'file_read' && (
  <FileReadView
    args={toolArgs}
    result={toolResult}
    highlightText={highlightText}
    highlightKey={highlightKey}
  />
)}
```

`FileReadView` already wraps its own scrollable body. The `<ScrollArea>` wrapper still wins ownership; that's fine — its children scroll naturally.

- [ ] **Step 21.2 — Find the panel container**

Run: `grep -rn "panelStore.view\|view\.type === 'tool'\|view\.type === 'artifact'" frontend/packages/web --include="*.tsx" | grep -v node_modules`

Open the file that switches on `panelStore.view.type`. The expected location is `frontend/packages/web/components/layout/AppShell.tsx` or a sibling panel-container. Inspect it to confirm.

- [ ] **Step 21.3 — Add the `'attachment'` branch**

In the panel container, alongside the existing `'tool'` and `'artifact'` branches, add:

```tsx
import { AttachmentPreviewView } from '@/components/panel/AttachmentPreviewView'
// ...
{view.type === 'attachment' && <AttachmentPreviewView info={view.info} />}
```

- [ ] **Step 21.4 — Type-check + tests**

Run: `cd frontend && pnpm type-check && pnpm --filter web test`
Expected: green.

- [ ] **Step 21.5 — Commit**

```bash
git add frontend/packages/web/components/panel/ToolDetailPanel.tsx \
        frontend/packages/web/components/layout/AppShell.tsx
git commit -m "feat(frontend): wire FileReadView and AttachmentPreviewView into panel"
```

(Adjust the paths to whichever container file actually held the view switch.)

---

## Phase 6 — Citation rendering for files

### Task 22: `CitationMarker` file-source render branch

**Files:**
- Modify: `frontend/packages/web/components/chat/CitationMarker.tsx`

The `CitationMarker.handleOpenPanel` logic is already correct (it routes via `tool_call_id` → `openTool` → `mapContentType` returns `'file_read'` → `FileReadView` renders). Only the popover content needs a new branch.

- [ ] **Step 22.1 — Branch on `source_type` in the popover**

Edit `frontend/packages/web/components/chat/CitationMarker.tsx`. Add an import for `getFileVisual` and refactor `CitationHoverContent` to dispatch:

```tsx
import { getFileVisual } from '@/lib/fileIcons'

// ... inside the file:

function CitationHoverContent({
  citation,
  chunkIndex,
  onOpenPanel,
}: {
  citation: CitationData
  chunkIndex: number
  onOpenPanel: () => void
}) {
  if (citation.metadata.source_type === 'file') {
    return (
      <FileSourceHoverContent
        citation={citation}
        chunkIndex={chunkIndex}
        onOpenPanel={onOpenPanel}
      />
    )
  }
  return (
    <WebSourceHoverContent
      citation={citation}
      chunkIndex={chunkIndex}
      onOpenPanel={onOpenPanel}
    />
  )
}
```

Rename the existing `CitationHoverContent` body to `WebSourceHoverContent` (no behaviour change). Then add `FileSourceHoverContent`:

```tsx
function basename(path?: string): string {
  if (!path) return ''
  const i = path.lastIndexOf('/')
  return i >= 0 ? path.slice(i + 1) : path
}

function FileSourceHoverContent({
  citation,
  chunkIndex,
  onOpenPanel,
}: {
  citation: CitationData
  chunkIndex: number
  onOpenPanel: () => void
}) {
  const { metadata, chunks } = citation
  const sortedChunks = [...chunks].sort((a, b) => a.chunk_index - b.chunk_index)
  const visual = getFileVisual({ filename: basename(metadata.path), mime_type: metadata.mime })
  const activeRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (activeRef.current && scrollRef.current) {
      const container = scrollRef.current
      const el = activeRef.current
      const top = el.offsetTop - container.offsetTop
      container.scrollTop = top - 8
    }
  }, [])

  const range = metadata.page_range
    ? `Pages ${metadata.page_range}`
    : metadata.line_range
      ? `Lines ${metadata.line_range}`
      : null

  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        onClick={onOpenPanel}
        className="flex items-center gap-2 text-left text-sm font-semibold text-foreground hover:text-primary transition-colors cursor-pointer"
      >
        <span className={`size-6 grid place-items-center rounded ${visual.bg}`}>
          <visual.Icon className={`size-3 ${visual.fg}`} />
        </span>
        <span className="truncate">{basename(metadata.path) || '(file)'}</span>
      </button>
      <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
        <span className="truncate" title={metadata.path}>
          {metadata.path}
        </span>
        {range && (
          <span className="rounded bg-muted px-1.5 py-0.5 font-medium">{range}</span>
        )}
        {metadata.truncated && (
          <span className="rounded bg-amber-500/10 px-1.5 py-0.5 font-medium text-amber-700 dark:text-amber-400">
            Truncated
          </span>
        )}
        <span className="ml-auto rounded bg-muted px-1.5 py-0.5 font-medium">file</span>
      </div>
      {sortedChunks.length > 0 && (
        <div ref={scrollRef} className="max-h-40 overflow-y-auto -mx-1 px-1">
          <div className="flex flex-col gap-0.5">
            {sortedChunks.map((c) => {
              const isActive = c.chunk_index === chunkIndex
              return (
                <div
                  key={c.chunk_index}
                  ref={isActive ? activeRef : undefined}
                  className={`text-xs leading-relaxed rounded px-2 py-1 ${
                    isActive
                      ? 'bg-primary/8 text-foreground border-l-2 border-primary'
                      : 'text-muted-foreground/50'
                  }`}
                >
                  <span className={isActive ? '' : 'line-clamp-2'}>{c.content}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 22.2 — Type-check**

Run: `cd frontend && pnpm type-check`
Expected: green.

- [ ] **Step 22.3 — Commit**

```bash
git add frontend/packages/web/components/chat/CitationMarker.tsx
git commit -m "feat(frontend): file-source render branch in CitationMarker"
```

---

## Phase 7 — Integration tests & polish

### Task 23: E2E test — upload, cancel, send

**Files:**
- Modify: `frontend/packages/web/__tests__/e2e/attachments.spec.ts`

- [ ] **Step 23.1 — Read existing attachment E2E**

Open `frontend/packages/web/__tests__/e2e/attachments.spec.ts` so the new tests follow its patterns (page setup, login fixture, file paths used).

- [ ] **Step 23.2 — Add the cancel test**

Append to the file (translate selectors and helpers to the existing fixtures):

```ts
test('cancels an in-flight upload from the home page', async ({ page, login }) => {
  await login()
  await page.goto('/w/<wsId>/')
  // pick a file
  const input = page.locator('input[type=file]')
  await input.setInputFiles('tests/e2e/fixtures/sample.pdf')
  // cancel chip should appear with the close button
  const chip = page.getByRole('button', { name: /Remove sample\.pdf/i })
  await chip.click()
  // chip removed
  await expect(page.locator('[data-testid="message-attachments"]')).toHaveCount(0)
  // sidebar should not list the empty draft conversation
  await expect(page.getByText(/New chat/)).toHaveCount(0)
})

test('uploads on the home page and sends with attachment above the bubble', async ({
  page,
  login,
}) => {
  await login()
  await page.goto('/w/<wsId>/')
  await page.locator('input[type=file]').setInputFiles('tests/e2e/fixtures/sample.pdf')
  // wait for upload to complete (label moves to PDF; cancel button still shown)
  await expect(page.getByText('PDF').first()).toBeVisible()
  await page.getByTestId('chat-input').fill('Hi')
  await page.getByTestId('send-button').click()
  // user message appears with attachments above
  const userMsg = page.getByText('Hi')
  const attach = page.getByTestId('message-attachments')
  await expect(attach).toBeVisible()
  // attachments are above the user message in DOM order — assert ordering
  const userBox = await userMsg.boundingBox()
  const attachBox = await attach.boundingBox()
  expect(userBox!.y).toBeGreaterThan(attachBox!.y)
})
```

(Replace `<wsId>` with whatever helper exists in the fixtures, e.g. `getDefaultWorkspace()`. The existing `attachments.spec.ts` already does this; mimic that pattern.)

- [ ] **Step 23.3 — Run E2E**

Run: `cd frontend && pnpm test:e2e -- attachments`
Expected: PASS.

- [ ] **Step 23.4 — Commit**

```bash
git add frontend/packages/web/__tests__/e2e/attachments.spec.ts
git commit -m "test(frontend): e2e for cancel and home-page upload flow"
```

---

### Task 24: Backend E2E — `file_read` emits citations

**Files:**
- Create: `backend/tests/e2e/test_file_read_citations.py`

- [ ] **Step 24.1 — Write the E2E**

Create `backend/tests/e2e/test_file_read_citations.py`:

```python
"""E2E: file_read tool emits citation events for kind=text results."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.e2e


async def test_file_read_text_emits_citation(
    authed_client: AsyncClient,
    workspace_id: str,
    sandboxed_run,  # fixture that runs an agent prompt to completion and yields events
) -> None:
    """When the agent runs file_read on a text file and the kind is 'text',
    a citation event with source_type=file appears in the SSE stream."""
    create = await authed_client.post(
        f"/api/v1/ws/{workspace_id}/conversations?title=cite"
    )
    convo_id = create.json()["id"]

    # The fixture should: upload a small markdown file, send a prompt that
    # explicitly asks the agent to read+quote it, and return the parsed event
    # list. If no such fixture exists, inline the SSE iteration loop here using
    # the same pattern as backend/tests/e2e/test_send_with_attachments.py.
    events = await sandboxed_run(
        convo_id,
        upload="The capital of France is **Paris**.",
        upload_filename="fact.md",
        prompt="Read fact.md and tell me where Paris is, citing the file.",
    )
    citation_events = [e for e in events if e["type"] == "citation"]
    assert citation_events, "no citation events received"
    file_citations = [
        e for e in citation_events if e["data"]["metadata"]["source_type"] == "file"
    ]
    assert file_citations, f"no file-source citations: {citation_events}"

    md = file_citations[0]["data"]["metadata"]
    assert md["path"].endswith("fact.md")
    chunks = file_citations[0]["data"]["chunks"]
    assert any("Paris" in c["content"] for c in chunks)


async def test_file_read_unsupported_emits_no_citation(
    authed_client: AsyncClient,
    workspace_id: str,
    sandboxed_run,
) -> None:
    """unsupported / unchanged / error kinds do NOT emit citations."""
    create = await authed_client.post(
        f"/api/v1/ws/{workspace_id}/conversations?title=cite-neg"
    )
    convo_id = create.json()["id"]
    events = await sandboxed_run(
        convo_id,
        upload=b"\x00\x01\x02BIN",  # binary blob with no parser
        upload_filename="opaque.bin",
        prompt="Read opaque.bin and describe it.",
    )
    citation_events = [e for e in events if e["type"] == "citation"]
    file_citations = [
        e for e in citation_events if e["data"]["metadata"]["source_type"] == "file"
    ]
    assert file_citations == []
```

(If `sandboxed_run` doesn't exist, pull the SSE-consumption helper from `tests/e2e/test_send_with_attachments.py` and adapt it. The point is: a small markdown file + a prompt that triggers `file_read` → assert the citation event shape.)

- [ ] **Step 24.2 — Run it**

Run: `cd backend && uv run pytest tests/e2e/test_file_read_citations.py -v -s`
Expected: PASS. If the agent doesn't reliably call `file_read`, tighten the prompt or pre-upload the file via the API.

- [ ] **Step 24.3 — Commit**

```bash
git add backend/tests/e2e/test_file_read_citations.py
git commit -m "test(backend): e2e for file_read citation emission"
```

---

### Task 25: Final integration sweep + push

**Files:** N/A (verification only)

- [ ] **Step 25.1 — Backend full check**

Run: `cd backend && make check`
Expected: format / lint / mypy / tests all green.

- [ ] **Step 25.2 — Frontend full check**

Run: `cd frontend && pnpm type-check && pnpm --filter @cubeplex/core test && pnpm --filter web test && pnpm --filter web lint`
Expected: green.

- [ ] **Step 25.3 — Manual smoke (dev server)**

Bring up backend and frontend dev servers. In the browser:

1. Open `/w/<wsId>/` (home page).
2. Drag a PDF → ring animates → sidebar still does not list this conversation.
3. Click the `×` mid-upload → chip vanishes; no DELETE in network.
4. Drag a fresh PDF → wait for completion → type "summarize this" → submit.
5. URL flips to `/w/<wsId>/conversations/<id>`; user message renders with the PDF chip ABOVE the bubble.
6. Click the chip → side panel opens, PDF.js renders the file.
7. Wait for the agent to call `file_read` and answer with `【N-M】` markers.
8. Hover a `【N-M】` marker → file-source popover (icon + filename + path + range).
9. Click the marker → side panel switches to `FileReadView` with the cited chunk highlighted.

If anything is off, fix inline before the final commit. Use existing tests as the source of truth for selector names.

- [ ] **Step 25.4 — Push the branch**

```bash
git push -u origin feat/file-upload-polish
```

- [ ] **Step 25.5 — (Optional) Open a PR**

If the user requests a PR, run `gh pr create` with a body that points at the design doc and lists the Phase summaries from this plan. Otherwise stop here.

---

## Self-review checklist

- All six items from the spec are covered: §1 (Tasks 5–6 backend; 11–13 frontend), §2 (Tasks 1–4, 9, 22), §3 (Tasks 7–8, 14, 15, 16, 17), §4 (Task 18), §5 (Tasks 9, 10, 19, 20, 21), §6 (Tasks 1–4, 10, 22).
- No "TODO", "TBD", or "implement later" placeholders in steps.
- Type signatures of `getFileVisual`, `uploadAttachment`, `useAttachmentStore.cancel`, and the new `panelStore.openAttachment` action are consistent across the files that reference them.
- Each task ends in a green test or check + a focused commit.
- Branch is `feat/file-upload-polish` (no worktree, per user request).
