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

- Collaborative rich-text / multi-user CRDT / live multi-user cursors.
- Full WYSIWYG document model that rewrites markdown on every keystroke
  (Notion-style). Source markdown remains the edit surface.
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
Read:
┌─────────────────────────────────────────┐
│ 📄 title.md                    v3  ⋮    │  header: name, version, open panel, download
├─────────────────────────────────────────┤
│  # Rendered markdown…                   │  read mode (prose), max-h + fade/expand
│  …                                      │
│  [Edit]                                 │  explicit Edit; selection does not enter edit
└─────────────────────────────────────────┘

Edit:
┌─────────────────────────────────────────┐
│ 📄 title.md                    v3  ⋮    │
├─────────────────────────────────────────┤
│ [B I H • # `]     Write | Preview       │  format toolbar + mode toggle
├─────────────────────────────────────────┤
│  1  # Heading…                          │  CodeMirror markdown source
│  2  paragraph with **bold**…            │  (or live Preview via same renderer)
│  …                                      │
│  [Cancel]                    [Save]     │  dirty state; Cmd/Ctrl+S saves
└─────────────────────────────────────────┘
```

| Mode | Behavior |
| --- | --- |
| Read | Fetch preview text for current version; render with shared markdown renderer; selection enabled for quote |
| Edit | **Real markdown source editor** (not a bare `<textarea>`) — see Editor below; dirty state; **Save** / **Cancel** |
| Save | PUT new content → new version; show new body; toast on success or partial sandbox fail |
| Error | Keep edit buffer; error toast |

Click targets:

- **Edit button** (and optional double-click on body) → edit mode. Selection
  drag must **not** enter edit.
- **Header / open panel** → existing `ArtifactPanel`.
- **Quote control** → only when selection is non-empty (floating toolbar).

### Editor (v1 requirement — not deferred)

Editing is the primary value of this feature. Shipping a plain monospace
`<textarea>` is **not acceptable** for v1.

| Requirement | Detail |
| --- | --- |
| Surface | CodeMirror 6 markdown source editor (`@codemirror/lang-markdown` or thin React wrapper such as `@uiw/react-codemirror`) |
| Highlighting | Markdown syntax highlighting; theme-aware (match app light/dark) |
| Layout | Soft wrap for prose; min height ≈ read card; grow with content up to a max then scroll |
| Write / Preview | Toggle (or split when wide enough): **Write** = source editor; **Preview** = same `MarkdownWithCitations` / prose renderer as read mode so save-what-you-see matches chat |
| Format helpers | Lightweight toolbar that wraps/inserts common markdown: bold, italic, heading, list, link, inline/fenced code — pure source transforms, not a separate document model |
| Keys | `Cmd/Ctrl+S` → save; `Esc` → cancel when clean, confirm-or-stay when dirty (product may soften to toast + stay) |
| Focus | Enter edit focuses the editor; leave edit restores sensible focus (Edit button or card) |
| Bytes | Editor edits the raw markdown string; save sends exact UTF-8 text — no silent reformat / prettier on save |

**Rejected alternatives:**

| Option | Why not for v1 |
| --- | --- |
| Bare `<textarea>` | Core UX of the feature; looks unfinished; no highlight/toolbar/keys |
| Full WYSIWYG (TipTap/ProseMirror Notion-style) | Mangles agent markdown; heavy model; out of scope for collaborative rich-text non-goal |
| Monaco full IDE | Overkill weight for chat-inline cards; CodeMirror is enough |

Extract a reusable `MarkdownSourceEditor` component so the side panel can
adopt the same editor later without a second implementation.

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
   conversation soft-delete rules). **Invariant:** resolve artifact by id
   **and** require `artifact.conversation_id == path conversation_id` (cross-
   conversation IDOR test mandatory for this mutating route).
2. Load artifact; reject if not markdown-eligible; **atomic** optimistic
   concurrency: compare-and-swap on `version` (conditional
   `UPDATE … WHERE version = :expected` or row lock + recheck) — not
   read-then-increment. On mismatch → 409. Unique constraint on
   `(artifact_id, version)` recommended.
3. Size limit (e.g. 1–2 MB UTF-8 text).
4. Resolve target filename from `entry_file` or basename(`path`).
5. **Object store + DB consistency (required for success):** do **not** reuse
   agent registration's “upload non-fatal after version bump” pattern.
   Define one strategy and test both failure orders:
   - **Preferred:** upload object for `v{n+1}` first under the version key;
     then CAS-bump DB + insert `ArtifactVersion` in one transaction; on DB
     failure delete or GC the orphan object (or mark key provisional).
   - **Alternative:** DB row in `pending_upload` then upload then mark
     `ready` — only if product wants durable incomplete versions.
   Never leave current version pointing at a missing object.
6. **Directory artifacts:** existing multi-file registration uploads every
   file under the directory into the version prefix. User edit of a single
   markdown entry must either (a) **copy prior version objects** then
   overwrite the edited entry key, (b) mark the new version as
   **single-file-only** with explicit product semantics, or (c) **reject**
   directory artifacts for edit in v1. Choose (c) unless copy is cheap —
   document the choice; add a multi-file regression test.
7. **Sandbox sync (best-effort):**
   - Resolve conversation active sandbox (same rules as agent tools).
   - Missing sandbox → `sandbox_synced: false`, reason `no_sandbox`.
   - Empty path → `no_path`.
   - Path is a directory without usable `entry_file` → `path_is_directory`.
   - **Path safety:** resolve with provider-aware normalization; reject
     absolute `entry_file`, `..` segments, path escape outside workdir,
     symlink escape when detectable; require `entry_file` to be a relative
     single-file path. Reasons: `path_missing` / `path_escape`.
   - Else write via sandbox `upload([(abs_path, bytes)])` (prefer file API over
     shell heredoc). Unknown exceptions → `sandbox_error` (stable code; no
     raw exception text to client).
8. Response includes updated artifact metadata + `sandbox_synced` + optional
   `sandbox_sync_reason`.
9. Do **not** delete prior version objects.

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
| D. Bare `<textarea>` for edit MVP | **Rejected** — edit is the core feature; ship CodeMirror source + Preview in v1 |
| E. Full WYSIWYG (TipTap/ProseMirror) | Deferred / out of scope — source markdown stays canonical for agents |

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
3. Edit mode uses a **real markdown source editor** (CodeMirror 6 + highlight +
   Write/Preview + basic format toolbar) — **not** a bare `<textarea>`.
4. Prior versions remain listable in the panel version popover.
5. When sandbox + path are valid, file at `path` (or `path/entry_file`) updates;
   when not, save still succeeds with explicit partial status.
6. Selecting text offers **quote into composer** with selection + artifact
   context.
7. Non-markdown documents/images unchanged.
8. Authz and conversation soft-delete rules match existing artifact routes.
9. E2E covers happy path save; unit/e2e cover concurrency, sandbox missing,
   path missing, and authz.

## Open questions (resolved for v1 unless product overrides)

1. **Enter edit:** explicit Edit button (+ optional double-click); not single-click body.
2. **Concurrency:** `expected_version` → 409 on mismatch.
3. **Directory artifacts:** edit only when `entry_file` is markdown.
4. **Editor:** CodeMirror 6 markdown source + Write/Preview + format toolbar
   in v1 (no bare textarea; no full WYSIWYG).
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
