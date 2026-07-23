# Inline markdown artifacts — implementation plan

Related: #396 · Spec: `docs/dev/specs/2026-07-22-inline-markdown-artifacts-design.md`

**Goal**: Inline markdown artifact cards in chat with edit→version save,
best-effort sandbox write-back, and quote-to-composer.

**Architecture**: Backend adds a content PUT that versions like agent
`save_artifact` but accepts browser text (object store first, sandbox second).
Frontend adds `MarkdownArtifactCard` parallel to `ImageArtifactCard`, wired
from `AssistantMessage` via a shared markdown-detection helper.

**Tech stack**: FastAPI + existing Artifact/Version repos + objectstore
`upload_file` + sandbox `upload`; React + `MarkdownWithCitations` +
`useComposerDraft` + **TipTap** (`@tiptap/react`, starter-kit class extensions,
`@tiptap/markdown` for bidirectional md). Add packages via `pnpm add` in
`packages/web`. Bare `<textarea>` and CodeMirror-as-primary are rejected;
primary UX is WYSIWYG for non-technical users; storage remains markdown.

---

## Unit 1: Markdown detection helper (frontend)

**Files**:
- `frontend/packages/core` or `packages/web/lib/artifactMarkdown.ts` (prefer
  shared pure helper colocated with artifact types if `Artifact` lives in core)

**What**: `isMarkdownArtifact(artifact): boolean` — true when type/mime/filename
match the design rules. Export `markdownFilename(artifact): string | null` for
entry target.

**Tests**: unit on filename/mime matrix (`.md`, `.markdown`, `.mdx`,
`text/markdown`, non-md document, image).

---

## Unit 2: Backend — content update service + route

**Files**:
- `backend/cubeplex/services/artifact_content.py` (new) — or extend
  `artifact_registration.py` with `register_artifact_from_bytes(...)`
- `backend/cubeplex/api/routes/v1/artifacts.py` — `PUT /{artifact_id}/content`
- `backend/cubeplex/api/schemas/artifacts.py` (new or existing) — request/response

**Request**:
```json
{ "content": "string", "expected_version": 3 }
```

**Response** (sketch):
```json
{
  "artifact": { "...to_dict..." },
  "sandbox_synced": false,
  "sandbox_sync_reason": "no_sandbox"
}
```

**Logic**:
1. Authz + load conversation + artifact; assert
   `artifact.conversation_id == conversation_id`.
2. Reject non-markdown-eligible artifacts with 400. **v1 default:** reject
   multi-file directory artifacts for content edit unless copy-prior-objects
   is implemented (see spec).
3. Enforce max length (config or constant ~2_000_000 chars/bytes).
4. **Consistency strategy (do not call `ArtifactRepository.update` then
   non-fatal upload):** upload object for next version key first **or** use
   pending/ready state; then **atomic CAS** bump
   (`WHERE version = expected_version`) + insert version row. On upload
   failure after DB commit, compensate or never commit current to missing
   object. Unique `(artifact_id, version)` recommended.
5. Best-effort sandbox (Unit 3); never fail the whole request solely because
   sandbox write failed.

**Tests** (`backend/tests/e2e/...` if hits DB/app; pure validation in unit):
- Happy path: version N→N+1, object exists, body returned.
- 409 on version mismatch; concurrent browser + agent save only one winner.
- Upload-fails-after-DB and DB-fails-after-upload orders (no missing current object).
- 400 on non-markdown / directory (if rejected).
- Authz: non-member / wrong workspace / **artifact from other conversation**.
- Soft-deleted conversation: same as other artifact routes.

---

## Unit 3: Backend — best-effort sandbox sync

**Files**: service from Unit 2; reuse conversation sandbox resolution used by
agent tools / lazy sandbox.

**Logic**:
1. If no active sandbox for conversation → reason `no_sandbox`.
2. Resolve write path: `path` if file-like; else `join(path, entry_file)` when
   directory + entry_file markdown (`entry_file` must be relative single-file).
3. Canonicalize; reject `..`, absolute entry, workdir escape, symlink escape
   when detectable → `path_escape` / `path_missing`.
4. `sandbox.upload([(path, content.encode("utf-8"))])`.
5. Catch errors → `sandbox_synced: false` + stable reason (`sandbox_error` for
   unknowns); log warning; never return raw exception strings.

**Tests**: mock sandbox missing; path missing parent; traversal/symlink cases;
successful upload called with expected path/bytes; e2e partial failure still
returns updated artifact + reason + UI toast path.

---

## Unit 4: Frontend — `MarkdownArtifactCard` (read-only)

**Files**:
- `frontend/packages/web/components/chat/MarkdownArtifactCard.tsx` (new)
- `AssistantMessage.tsx` — branch: image → Image; markdown → Markdown; else Artifact
- Reuse preview fetch pattern from `DocumentPreview` / `buildPreviewUrl`

**UX**: header (name, version, open panel), body max-height + expand,
loading/error states. Explicit **Edit** button present but may no-op until
Unit 5 if phased separately.

**Tests**: component test that markdown artifacts render body container;
non-markdown still uses ArtifactCard (existing AssistantMessage tests extended).

---

## Unit 5: Frontend — TipTap markdown editor + save

**Files**:
- `frontend/packages/web/components/editor/MarkdownRichEditor.tsx` (new) —
  reusable TipTap surface: markdown load/save via `@tiptap/markdown`, whitelist
  schema, toolbar, controlled dirty, imperative focus, dynamic client import
- `MarkdownArtifactCard.tsx` — edit chrome: toolbar + Save/Cancel + dirty;
  read mode still uses `MarkdownWithCitations`
- API client method on existing artifacts API module
- `artifactStore` (or equivalent) version metadata refresh after save
- i18n keys: edit, save, cancel, saving, sandbox partial warning
- deps: `pnpm add @tiptap/react @tiptap/starter-kit @tiptap/markdown` (+ link /
  table / task-list extensions as needed) in `packages/web`

**Editor bar (ship in this unit, not a follow-up):**
1. TipTap WYSIWYG for non-technical users (not bare textarea / not CM primary).
2. Load with `contentType: 'markdown'`; save with `getMarkdown()`.
3. Toolbar: bold / italic / heading / list / link / code.
4. Schema whitelist = markdown-expressible only; GFM on for tables/tasks.
5. `Cmd/Ctrl+S` save; `Esc` cancel when clean.
6. Canonical markdown on serialize is OK; fixture tests for semantic fidelity.

**Flow**: Edit → load raw text if not already held → editor mounts focused →
Save PUT with `expected_version` → on success swap to read mode with new
content; toast if `sandbox_synced === false`.

**Tests**: mock API success/409/error; dirty cancel restores previous body;
editor present in edit mode (not a raw textarea); unit fixtures for
parse→serialize semantic stability on agent-like markdown samples.

---

## Unit 6: Frontend — quote selection → composer

**Files**:
- `MarkdownArtifactCard.tsx` — selection listener + floating "Quote" control
- `useComposerDraft.setDraft` with quote + artifact id/name/version/path

**Tests**: unit/component — calling quote with selected text produces expected
markdown shape; empty selection hides control.

---

## Unit 7: Docs (user-facing, with implementation PR)

**Files**: `docs/site/docs/guides/conversations/artifacts.md` (or current
artifacts page) — note inline markdown edit + quote.

Spec/plan-only PR does **not** require site docs until code ships (per
issue: implementation deferred). Track as checkbox in implementation PR.

---

## Phased delivery checklist

| Phase | Units | Ship bar |
| --- | --- | --- |
| 1 | 1, 4 | Inline readable markdown in chat |
| 2 | 2 (store only), 5 | Real markdown editor + version save (no sandbox yet) |
| 3 | 3 + toast wiring | Best-effort sandbox + partial status |
| 4 | 6 | Quote into composer |

---

## Out of scope for this plan

- Collaborative multi-user editing / CRDT.
- Non-markdown HTML features (colors, arbitrary layout).
- Changing agent `save_artifact` schema.
- Inline edit for non-md types.
- Diff-against-previous-version UI (nice later; not blocking).
- CodeMirror source mode (optional later for power users).

## Risks

| Risk | Mitigation |
| --- | --- |
| Large md blocks bloat chat DOM | max-height + expand; later virtualize if needed |
| Bare textarea slips into MVP | Spec forbids it; component test asserts TipTap surface |
| TipTap md round-trip drift | Schema whitelist + GFM fixtures; accept canonical serialize |
| `@tiptap/markdown` early-release bugs | Pin version; fixture suite; fall back errors keep buffer |
| Bundle size / SSR | Dynamic import editor only in edit mode; client-only mount |
| Citations / custom syntax in md | Treat as plain text or extend tokenizer; don't drop content |
| Race with agent `save_artifact` | Atomic CAS on version + unique (artifact_id, version) |
| Version points at missing object | Upload/DB consistency strategy; test both failure orders |
| Directory multi-file version shrink | Reject edit or copy prior objects |
| Sandbox dead → user thinks save failed | toast distinguishes history vs sandbox |
| Path escape / symlink | Canonical path checks + tests |
| Path is directory | disable Edit unless entry_file is md |
