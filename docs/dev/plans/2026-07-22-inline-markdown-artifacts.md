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
`useComposerDraft`. No new npm packages required for MVP textarea.

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
1. Authz + load conversation + artifact (existing helpers).
2. Reject non-markdown-eligible artifacts with 400.
3. If `expected_version` provided and mismatches → 409.
4. Enforce max length (config or constant ~2_000_000 chars/bytes).
5. Bump version via repository `update` path used by agent registration;
   create version row with same path/entry_file/mime.
6. `ObjectStoreClient.upload_file(key, content_bytes, content_type=...)`.
7. Best-effort sandbox (Unit 3); never fail the whole request solely because
   sandbox write failed.

**Tests** (`backend/tests/e2e/...` if hits DB/app; pure validation in unit):
- Happy path: version N→N+1, object exists, body returned.
- 409 on version mismatch.
- 400 on non-markdown artifact.
- Authz: non-member / wrong workspace.
- Soft-deleted conversation: same as other artifact routes.

---

## Unit 3: Backend — best-effort sandbox sync

**Files**: service from Unit 2; reuse conversation sandbox resolution used by
agent tools / lazy sandbox.

**Logic**:
1. If no active sandbox for conversation → reason `no_sandbox`.
2. Resolve write path: `path` if file-like; else `join(path, entry_file)` when
   directory + entry_file markdown.
3. Reject path escape outside workdir.
4. `sandbox.upload([(path, content.encode("utf-8"))])`.
5. Catch errors → `sandbox_synced: false` + reason; log warning.

**Tests**: mock sandbox missing; path missing parent; successful upload
called with expected path/bytes.

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

## Unit 5: Frontend — edit + save

**Files**:
- `MarkdownArtifactCard.tsx` — textarea, Save/Cancel, dirty state
- API client method on existing artifacts API module
- `artifactStore` (or equivalent) version metadata refresh after save
- i18n keys: edit, save, cancel, saving, sandbox partial warning

**Flow**: Edit → load raw text if not already held → Save PUT with
`expected_version` → on success swap to read mode with new content; toast
if `sandbox_synced === false`.

**Tests**: mock API success/409/error; dirty cancel restores previous body.

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
| 2 | 2 (store only), 5 | Edit + version without sandbox |
| 3 | 3 + toast wiring | Best-effort sandbox + partial status |
| 4 | 6 | Quote into composer |

---

## Out of scope for this plan

- Rich WYSIWYG markdown editor.
- Live collaborative editing.
- Changing agent `save_artifact` schema.
- Inline edit for non-md types.

## Risks

| Risk | Mitigation |
| --- | --- |
| Large md blocks bloat chat DOM | max-height + expand; later virtualize if needed |
| Race with agent `save_artifact` | `expected_version` 409; user reloads and retries |
| Sandbox dead → user thinks save failed | toast distinguishes history vs sandbox |
| Path is directory | disable Edit unless entry_file is md |
