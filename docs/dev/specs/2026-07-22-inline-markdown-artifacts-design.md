# Inline markdown artifacts: chat preview, edit, version save, quote

Related: #396

## Goal

Make markdown document artifacts first-class chat content, parallel to
image artifacts:

1. Render the body **inline** in the transcript (not only a compact chip).
2. Let the user **edit** that body and **save a new artifact version**.
3. On save, **best-effort** write the same bytes back to the recorded sandbox
   path when it is still valid.
4. Let the user **select a passage** and **quote it into the composer** with
   enough artifact context for the agent to revise via chat.

## Context

### What works today

| Surface | Behavior |
| --- | --- |
| Image artifacts | `ImageArtifactCard` — full inline preview; click opens right panel |
| Other artifacts (incl. md docs) | `ArtifactCard` chip → opens `ArtifactPanel` |
| Panel markdown | `DocumentPreview` loads object-store preview text; filenames matching `/\.(md\|markdown\|mdx)$/i` render via `MarkdownWithCitations` |
| Agent save | `save_artifact` / `register_artifact_from_sandbox` → DB version bump + object-store prefix `artifacts/{conv}/{id}/v{n}/…` |
| Path | Stored on `Artifact.path` and each `ArtifactVersion.path` — snapshot at registration; **not** a live sync guarantee |
| User content write API | **None** — artifact HTTP routes are read-oriented (list, get, versions, download, preview, share) |
| Composer inject | `useComposerDraft.setDraft` used by PromptCards; no selection→quote on artifacts |

Branch in `AssistantMessage`:

```text
if (artifact.artifact_type === 'image') → ImageArtifactCard
else → ArtifactCard
```

### Path reality (product answer)

| Question | Answer |
| --- | --- |
| Is sandbox path recorded? | **Yes** — `artifacts.path` + per-version `artifact_versions.path` |
| Is object store canonical for preview/download? | **Yes** |
| Can we always overwrite the sandbox file later? | **No** — sandbox recycle, path moves, dir artifacts, missing parent |
| What user save must do | Succeed on object-store version always; sandbox write is best-effort with explicit status |

## Non-goals

- Collaborative rich-text / multi-user CRDT.
- Inline edit for non-markdown types (PDF, office, websites, binary) in v1.
- Guaranteed sandbox rewrite when sandbox is dead or path missing.
- Changing the agent `save_artifact` tool contract beyond light awareness that
  user-created versions can appear (list already shows path + version).

## Product definition

### Which artifacts get the inline markdown card?

Any of:

- `artifact_type === 'document'` **and** filename (`entry_file` or basename of
  `path`) matches `md|markdown|mdx`, **or**
- `mime_type` is `text/markdown` or `text/x-markdown`.

Everything else keeps `ArtifactCard`. Images stay on `ImageArtifactCard`.

Directory artifacts: only enable inline edit when `entry_file` is a clear
markdown file (e.g. `README.md`). Otherwise keep the compact card (or
read-only inline without Edit if product later wants it — v1: no edit without
a clear single file target).

### Inline card UX

```text
┌─────────────────────────────────────────┐
│ 📄 title.md                    v3  ⋮    │  header: name, version, open panel, download
├─────────────────────────────────────────┤
│  # Rendered markdown…                   │  read mode (prose), max-h + fade/expand
│  …                                      │
│  [Edit]                                 │  explicit Edit; selection does not enter edit
└─────────────────────────────────────────┘
```

| Mode | Behavior |
| --- | --- |
| Read | Fetch preview text for current version; render with shared markdown renderer; selection enabled for quote |
| Edit | Monospace textarea (MVP); dirty state; **Save** / **Cancel** |
| Save | PUT new content → new version; show new body; toast on success or partial sandbox fail |
| Error | Keep edit buffer; error toast |

Click targets:

- **Edit button** (and optional double-click on body) → edit mode. Selection
  drag must **not** enter edit.
- **Header / open panel** → existing `ArtifactPanel`.
- **Quote control** → only when selection is non-empty (floating toolbar).

### Quote → composer

Insert via `useComposerDraft.setDraft` a stable, agent-friendly block:

```markdown
> <quoted passage>

Regarding artifact `art_…` (`title.md`, v3, path: `/workspace/...`):
```

Include **artifact id, name, version, and path when present** so the agent can
call `save_artifact` with the right id after chat-driven edits.

### Save pipeline (backend)

```http
PUT /api/v1/ws/{ws}/conversations/{conv}/artifacts/{id}/content
Content-Type: application/json

{ "content": "...", "expected_version": 3 }
```

Server steps:

1. Authz same as other conversation artifact routes (`require_member` +
   conversation soft-delete rules).
2. Load artifact; reject if not markdown-eligible; optional optimistic
   concurrency: if `expected_version` ≠ current → 409.
3. Size limit (e.g. 1–2 MB UTF-8 text).
4. Resolve target filename from `entry_file` or basename(`path`).
5. **Object store (required for success):** write bytes under
   `artifacts/{conv}/{id}/v{new}/…` via `upload_file` (or equivalent); create
   `ArtifactVersion` row; bump `Artifact.version`; keep path/mime unless
   intentionally updated.
6. **Sandbox sync (best-effort):**
   - Resolve conversation active sandbox (same rules as agent tools).
   - Missing sandbox → `sandbox_synced: false`, reason `no_sandbox`.
   - Empty path → `no_path`.
   - Path is a directory without usable `entry_file` → `path_is_directory`.
   - Parent missing / path escape outside workdir → `path_missing` /
     `path_escape`.
   - Else write via sandbox `upload([(abs_path, bytes)])` (prefer file API over
     shell heredoc).
7. Response includes updated artifact metadata + `sandbox_synced` + optional
   `sandbox_sync_reason`.
8. Do **not** delete prior version objects.

### Agent awareness

Artifacts middleware already injects path + version. After user save, the next
turn should see the bumped version so the agent does not clobber blindly.
No change to `save_artifact` schema required for v1.

## Approaches considered

| Option | Notes |
| --- | --- |
| A. Panel-only edit | Less chat friction relief; rejected as primary UX |
| B. Inline read + edit + version API + best-effort sandbox + quote | **Chosen** — matches image-class deliverables |
| C. Always require live sandbox on save | Too brittle; object store is the durable history |

## Phasing

| Phase | Deliverable |
| --- | --- |
| **1** | Inline read-only markdown card (detect + fetch + render + open panel) |
| **2** | Edit + save new version (object store + DB); no sandbox write yet |
| **3** | Best-effort sandbox path sync + status toast |
| **4** | Selection → quote into composer |

Implementation may ship 1–2 together if small; keep phases testable independently.

## Acceptance criteria

1. Markdown document artifacts render **inline** with readable rendered content.
2. User can Edit → change text → **Save** → version increments; preview/download
   serve new content.
3. Prior versions remain listable in the panel version popover.
4. When sandbox + path are valid, file at `path` (or `path/entry_file`) updates;
   when not, save still succeeds with explicit partial status.
5. Selecting text offers **quote into composer** with selection + artifact
   context.
6. Non-markdown documents/images unchanged.
7. Authz and conversation soft-delete rules match existing artifact routes.
8. E2E covers happy path save; unit/e2e cover concurrency, sandbox missing,
   path missing, and authz.

## Open questions (resolved for v1 unless product overrides)

1. **Enter edit:** explicit Edit button (+ optional double-click); not single-click body.
2. **Concurrency:** `expected_version` → 409 on mismatch.
3. **Directory artifacts:** edit only when `entry_file` is markdown.
4. **Editor:** monospace textarea MVP; richer editor later.
5. **Max size:** 2 MB UTF-8 text default.

## Related code

- `frontend/.../chat/ImageArtifactCard.tsx`, `ArtifactCard.tsx`, `AssistantMessage.tsx`
- `frontend/.../panel/artifact/DocumentPreview.tsx`
- `frontend/.../hooks/useComposerDraft.ts`
- `backend/cubeplex/api/routes/v1/artifacts.py`
- `backend/cubeplex/services/artifact_registration.py`
- `backend/cubeplex/models/artifact.py`, `artifact_version.py`
- `backend/cubeplex/objectstore/client.py` (`upload_file`, `upload_from_sandbox`)
- `backend/cubeplex/sandbox/base.py` (`upload`)
- `backend/cubeplex/prompts/artifacts.py`, `middleware/artifacts.py`
- Expand preview: #395
