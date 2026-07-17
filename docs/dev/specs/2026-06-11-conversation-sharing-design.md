# Conversation Sharing MVP

Share a conversation as a read-only public link. Each share is an immutable
snapshot — the same conversation can be shared multiple times, producing
independent links.

---

## Data Model

### New table: `conversation_shares`

| Column | Type | Notes |
|---|---|---|
| `id` | `str` (PK) | `shr-` prefixed public ID |
| `org_id` | FK → organizations | OrgScopedMixin |
| `workspace_id` | FK → workspaces | OrgScopedMixin |
| `conversation_id` | FK → conversations | Source conversation |
| `creator_user_id` | FK → users | Who created the share |
| `creator_display_name` | `str` | Frozen at share time |
| `title` | `str` | Frozen conversation title at share time |
| `snapshot` | JSONB | Materialized message list (filtered) |
| `artifacts_snapshot` | JSONB | Artifact metadata + file references |
| `is_active` | `bool` | Default `true`; set `false` to revoke |
| `created_at` | `timestamptz` | |
| `updated_at` | `timestamptz` | |

The snapshot is written once and never updated. Revoking a share sets
`is_active = false`; the snapshot data is retained for audit but the
public endpoint returns 404.

### Artifact file storage

When creating a share, copy each artifact's files to a share-scoped
object-store path: `shares/{shr_id}/artifacts/{artifact_id}/v{version}/{filename}`.
This decouples the share from the original conversation's storage — the
share remains accessible even if the source conversation is deleted.

### Public ID prefix

Add `PREFIX_SHR = "shr"` to `backend/cubeplex/models/public_id.py`.

---

## Snapshot Content Rules

### Included

- **User messages** — text content
- **Assistant messages** — text + thinking blocks
- **Tool calls** — name + arguments
- **Tool results** — content + is_error
- **Citations** — full citation data
- **Attachment metadata** — file name, MIME type, size (no file content,
  no download link)
- **Artifacts** — metadata + file content (copied to share-scoped storage
  for preview)

### Excluded

- **System prompts** — business configuration, never exposed
- **Synthetic messages** — framework-injected (`metadata.synthetic = true`),
  invisible to users
- **Attachment file content** — only metadata preserved; the visitor sees
  "user uploaded report.pdf (2.1 MB)" but cannot download it

---

## Backend API

### Workspace-scoped (authenticated)

**Create share**

```
POST /api/v1/ws/{ws}/conversations/{conversation_id}/shares
→ 201 { id, url, title, created_at }
```

Reads messages from cubepi checkpointer, applies filter rules, writes
snapshot + copies artifact files, returns the share record.

**List shares (for settings page)**

```
GET /api/v1/ws/{ws}/shares?limit=50&offset=0
→ 200 { items: [{ id, conversation_id, title, is_active, created_at }], total }
```

Returns all shares created by the current user in this workspace,
ordered by `created_at` descending.

**List shares for a conversation (for share panel)**

```
GET /api/v1/ws/{ws}/conversations/{conversation_id}/shares
→ 200 [{ id, url, is_active, created_at }]
```

**Revoke share**

```
PATCH /api/v1/ws/{ws}/shares/{shr_id}
Body: { "is_active": false }
→ 200 { id, is_active }
```

Sets `is_active = false`. The public endpoint returns 404 afterwards.
Revocation is permanent in MVP (no re-activation).

### Public (unauthenticated)

**Get shared conversation**

```
GET /api/v1/public/shares/{shr_id}
→ 200 {
    id, title, creator_display_name, created_at,
    messages: [...],
    artifacts: [{ id, name, artifact_type, mime_type, version }]
  }
→ 404 if not found or is_active = false
```

Single endpoint returns everything needed to render the page. Messages
are the pre-filtered snapshot from JSONB — no runtime filtering needed.

**Get shared artifact file (for preview)**

```
GET /api/v1/public/shares/{shr_id}/artifacts/{artifact_id}/v{version}/{filename}
→ 200 (file content with appropriate Content-Type)
→ 404 if share inactive or artifact not found
```

Serves the copied artifact file from share-scoped object storage.

---

## Frontend

### Share button + panel (conversation page)

Add a **Share** button to the conversation page header (right side, next
to existing controls).

Clicking opens a popover/dialog:
- List of existing shares for this conversation (link + date + status)
- Each active share has a **Copy link** button and a **Revoke** button
- **Create new share** button at the bottom → calls POST, appends result
  to list, auto-copies link

### Settings page — shared conversations tab

Add a **Shared conversations** tab to workspace settings.

Table columns: title, shared date, link (copyable), status (active/revoked),
revoke button.

Paginated, sorted by date descending.

### Guest view (`/share/[shrId]`)

A standalone page outside the authenticated app shell:

**Layout:**
- No sidebar, no workspace context, no auth requirement
- Top bar: cubeplex logo (left) + conversation title (center) +
  share metadata "Shared by {name} · {date}" (right)
- Main: read-only MessageList (reuse existing component with
  `readOnly` prop to hide InputBar, HITL cards, streaming state)
- Artifacts: clickable to open preview panel (reuse ArtifactPanel
  in read-only mode, fetching from public artifact endpoint)
- Attachment chips: show file name + size, no download action
- Bottom: subtle CTA banner "Powered by cubeplex"

**Route:** `/share/[shrId]/page.tsx` under a `(public)` route group
that skips auth layout.

**Data fetching:** Single `GET /api/v1/public/shares/{shr_id}` on mount.
No Zustand stores needed — local component state is sufficient for a
read-only view.

---

## What MVP Does NOT Include

- Workspace admin toggle to disable sharing
- Snapshot update / link reuse (each share = new link)
- Embed / iframe support
- Social media share buttons
- Link expiration / TTL
- Open Graph / social preview metadata (nice-to-have, not MVP)
