# Sandbox Preview Panel

Turn the existing single-purpose sandbox browser button into a full sandbox
environment panel with three tabs: file browser, browser, and terminal.

## Context

The conversation page has a monitor icon that opens a Neko browser live view
in the right panel. This is useful but limited — users also need to browse
sandbox files, preview documents, and run shell commands, all without leaving
the conversation.

## What changes

### Panel system

The panel store gains a new view type `sandbox`. When active the right panel
occupies maximum width, leaving the conversation area at a fixed minimum
width (420px, tunable via a constant) instead of the current 50/50 split.
Other panel types (tool, artifact, attachment) keep their existing 50/50
behavior.

The monitor icon in AppShell calls `openSandbox()` instead of
`openBrowser()`. The old `type: 'browser'` view type is replaced by
`type: 'sandbox'`.

### SandboxPanel component

A tab container rendered when `view.type === 'sandbox'`. Three tabs:

**Files** — Left/right resizable split inside the tab.

- Left pane: a file tree rooted at `/workspace`. Directories expand on click,
  loading children lazily via `GET /sandbox/files?path=<dir>`. Each file entry
  has a download action.
- Right pane: file preview. Appears when a file is selected; the tree
  narrows but stays visible. Preview routing by file type:
  - Text/code files → syntax-highlighted read-only view (reuse the rendering
    logic from the existing CodePreview component).
  - `.docx`, `.xlsx`, `.pptx` → Office preview via Microsoft Office Online
    Viewer. Uses a proxy-download flow (see backend section) so no temp
    storage is needed.
  - `.html` → iframe render via blob URL (fetch content from
    `/sandbox/files/content`, create a `Blob`, set iframe `src` to
    `URL.createObjectURL`).
  - Anything else → file info card with name, size, modified time, and a
    download button.
  - Files over 1 MB skip inline preview and show the download-only card.

**Browser** — Reuses the existing `BrowserView` component (Neko iframe,
watch/takeover modes, keepalive). No changes to BrowserView itself.

**Terminal** — An iframe pointing at a ttyd instance running inside the
sandbox container. ttyd provides its own xterm.js frontend, so no custom
terminal rendering is needed. Keepalive pings follow the same pattern as
BrowserView.

### Backend API

New route file: `backend/cubebox/api/routes/v1/ws_sandbox.py`

Prefix: `/api/v1/ws/{workspace_id}/sandbox`

All endpoints require workspace membership (`require_member`).

#### `GET /sandbox/files`

Query params: `path` (default `/workspace`), `pattern` (default `*`).

Calls OpenSandbox SDK `filesystem.search(path, pattern)`. The SDK returns
all matching entries recursively, so the backend filters to only direct
children of `path` (entries whose parent directory equals `path`). Returns
a flat list:

```json
[
  { "path": "/workspace/src", "name": "src", "is_dir": true, "size": 0, "modified_at": "..." },
  { "path": "/workspace/README.md", "name": "README.md", "is_dir": false, "size": 1234, "modified_at": "..." }
]
```

Sorted: directories first, then alphabetical.

#### `GET /sandbox/files/content`

Query params: `path` (required).

Reads file content for inline preview. Uses SDK `filesystem.read_file()`.
Returns `{ content: string, mime_type: string }`.

Size limit: 1 MB. Files exceeding this return HTTP 413 with a message
directing the user to download instead.

Office files (`.docx`, `.xlsx`, `.pptx`) should not use this endpoint — they
go through the preview-token flow below.

#### `GET /sandbox/files/download`

Query params: `path` (required).

Streams the file via SDK `filesystem.read_bytes_stream()`. Returns a
`StreamingResponse` with `Content-Disposition: attachment; filename="..."`.
No size limit.

#### `POST /sandbox/files/preview-token`

Query params: `path` (required).

Generates a time-limited nonce (5 min TTL) and stores only metadata in Redis:
`{ sandbox_id, file_path, org_id, workspace_id, user_id }`. Returns:

```json
{
  "download_url": "https://<host>/api/v1/public/sandbox/dl/<nonce>/<filename>",
  "viewer_url": "https://view.officeapps.live.com/op/embed.aspx?src=<encoded_download_url>"
}
```

#### `GET /api/v1/public/sandbox/dl/{nonce}/{filename}`

Public (no auth). Looks up the nonce in Redis to get `sandbox_id` and
`file_path`. Reconnects to the sandbox via
`SandboxManager.get_by_id(sandbox_id)` (no user context needed — the nonce
itself is the authorization) and proxies the file content in real time via
`read_bytes_stream()`. The file never touches disk or object storage — it
streams straight from the sandbox through to the Microsoft viewer.

Added as a new route in `sandbox_share.py` alongside the existing artifact
nonce-download endpoint in `artifact_share.py`.

#### `GET /sandbox/terminal`

Starts ttyd inside the sandbox (idempotent — checks if already running):

```bash
start-stop-daemon --start --background --make-pidfile --pidfile /tmp/ttyd.pid \
  --exec /usr/bin/ttyd -- -p 7681 -W bash
```

Then calls `get_signed_endpoint(7681)` and returns `{ url: string }`.

### Sandbox image

Add `ttyd` to the Dockerfile (`apt-get install -y ttyd`). It is not started
at container boot — the backend starts it on demand via the terminal
endpoint.

No other image changes needed.

### Frontend file tree

The file tree is a new component (`SandboxFileTree`). It manages its own
expand/collapse state. Each directory node fetches children on first expand
via SWR (`/sandbox/files?path=<dir_path>`). The tree supports:

- Expand/collapse directories
- Click file → load preview in right pane
- Download button per file (triggers `/sandbox/files/download`)
- Visual indicators: file type icons (reuse `fileIcons.ts`), directory
  chevrons, loading spinners during fetch

### Frontend terminal view

`SandboxTerminalView` fetches the terminal URL via SWR from
`/sandbox/terminal`, then renders an iframe. The iframe embed pattern is
identical to BrowserView:

- `allow="fullscreen; clipboard-read; clipboard-write"`
- Keepalive ping every 30s to `/browser/keepalive` (same sandbox, same
  purpose)

### What is not included

- File editing / write-back to sandbox (read-only for now).
- File upload from local machine to sandbox.
- Multi-file selection or batch download.
- Image/video/PDF preview (can be added later by routing more mime types).
- Terminal session persistence across panel close/reopen (ttyd process stays
  alive in the container, but the terminal state resets if the iframe
  reconnects).
