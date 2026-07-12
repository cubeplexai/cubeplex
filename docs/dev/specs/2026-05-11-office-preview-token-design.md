# Office File Preview via One-Time Token

Allow artifact preview of Office files (docx, xlsx, pptx) by embedding
Microsoft Office Online Viewer in an iframe. A one-time-use, unsigned
public download endpoint serves the file to Microsoft's servers without
requiring cubeplex authentication.

## Problem

Artifact preview currently works for HTML/website artifacts (served via
the authenticated `preview_artifact_file` endpoint). Office files
(Word, Excel, PowerPoint) have no in-browser preview — users can only
download them. Microsoft's free Office Online Viewer
(`view.officeapps.live.com/op/embed.aspx?src=<url>`) can render these
files, but requires a publicly accessible, unauthenticated URL pointing
to the file.

## Solution

1. **Backend** issues a one-time download token (OTK) for a specific
   artifact + version, stored in Redis with a 5-minute TTL.
2. A **public endpoint** (no auth) validates the token, atomically
   deletes it (one-time), and streams the file from object storage.
3. **Frontend** requests the OTK, constructs the Office Online Viewer
   embed URL, and renders it in an iframe inside the existing artifact
   preview panel.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Token storage | Redis with 5-min TTL | Fast, atomic GETDEL, auto-cleanup of unused tokens |
| Token format | Random 32-byte hex nonce | No signing needed — Redis is the source of truth, not the token payload |
| One-time enforcement | `GETDEL` atomic operation | No race condition window for replay |
| Public endpoint path | `/api/v1/public/artifacts/dl/{token}/{filename}` | `{filename}` provides the file extension Office Online needs to detect type |
| Supported formats | `.docx`, `.xlsx`, `.pptx` | The three modern Office XML formats that Office Online Viewer supports |
| File size | No artificial limit | Office Online has no hard limit for Word/PPT viewing (60s download timeout only); Excel caps at 25MB |

## Data flow

```
User clicks "Preview" on an Office artifact
  → Frontend: POST /api/v1/ws/{wsId}/conversations/{cid}/artifacts/{aid}/preview-token
    (authenticated, returns { url, viewer_url })
  → Frontend: renders <iframe src="{viewer_url}"> in preview panel
  → Microsoft servers: GET /api/v1/public/artifacts/dl/{token}/report.docx
    → Backend: GETDEL token from Redis → stream file from object storage → token gone
  → Microsoft renders the document in the iframe
```

## Configuration

New config key `api.public_url`:

```yaml
api:
  public_url: ""  # e.g. "https://app.cubeplex.com"
```

- Overridable via `CUBEPLEX_API__PUBLIC_URL`.
- When set, OTK download URLs use this as the base:
  `{public_url}/api/v1/public/artifacts/dl/{nonce}/report.docx`
- When empty (default), the backend derives the base from the incoming
  request's `Host` header + scheme (via `request.base_url`). This
  works for local dev behind tunnels (e.g. ngrok) and for simple
  single-origin deployments where the API is served from the same
  domain as the frontend.

## Backend changes

### 1. New endpoint: issue OTK (authenticated)

Add to `cubeplex/api/routes/v1/artifacts.py`:

```
POST /ws/{workspace_id}/conversations/{conversation_id}/artifacts/{artifact_id}/preview-token
```

- Requires `require_member` (existing auth dependency).
- Query param: `version` (optional, defaults to latest).
- Generates a 32-byte random hex nonce.
- Stores in Redis: `SET otk:{nonce} {json_payload} EX 300`
  - Payload: `{"conversation_id": "...", "artifact_id": "...", "version": 3, "filename": "report.docx"}`
- Resolves base URL: `config.api.public_url` if set, otherwise
  `str(request.base_url).rstrip("/")` from the incoming request.
- Returns:
  ```json
  {
    "download_url": "{base}/api/v1/public/artifacts/dl/{nonce}/report.docx",
    "viewer_url": "https://view.officeapps.live.com/op/embed.aspx?src={encoded_download_url}"
  }
  ```

### 2. New endpoint: public file download (no auth)

New router file `cubeplex/api/routes/v1/public_artifacts.py`:

```
GET /public/artifacts/dl/{token}/{filename}
```

- **No authentication** — no `require_member`, no CSRF check.
- Validates `token` via `GETDEL otk:{token}` from Redis.
  - If None → 404.
- Parses the stored JSON payload to get `conversation_id`, `artifact_id`, `version`.
- Validates that `filename` matches the stored filename (prevents URL
  manipulation).
- Downloads from object storage using the same logic as existing
  `download_artifact` (single-file path).
- Returns file with correct `Content-Type` and
  `Content-Disposition: inline` (not attachment — Office Online needs
  to read the body, not trigger a browser download).
- **No `Cache-Control`** — the URL is one-time, caching is meaningless.

### 3. Router registration

In `cubeplex/api/app.py`, register the public router:

```python
from cubeplex.api.routes.v1 import public_artifacts
app.include_router(public_artifacts.router, prefix="/api/v1")
```

### 4. CSRF middleware exemption

The public download endpoint must not require CSRF tokens. The existing
`CSRFMiddleware` skips CSRF for GET requests, so no change needed.

### 5. Redis key prefix

OTK keys use the existing `redis_key_prefix` from app state to avoid
collisions across environments/worktrees:

```
{prefix}:otk:{nonce}
```

## Frontend changes

### 1. Detect Office file type

In the artifact preview panel, check `artifact.mime_type` or filename
extension to determine if the file is an Office document:

```typescript
const OFFICE_EXTENSIONS = new Set(['.docx', '.xlsx', '.pptx']);
const isOfficeFile = (name: string) =>
  OFFICE_EXTENSIONS.has(name.slice(name.lastIndexOf('.')).toLowerCase());
```

### 2. Request preview token

When an Office artifact is selected for preview, call the new
`preview-token` endpoint via `ApiClient`:

```typescript
const { viewer_url } = await apiClient.post(
  `/conversations/${cid}/artifacts/${aid}/preview-token`,
  { version }
);
```

### 3. Render Office Online Viewer iframe with fallback

Replace the existing "no preview available" state for Office files with
an iframe pointing to Office Online Viewer. If loading fails (the
iframe shows an error or times out), fall back to a download prompt.

**Loading state machine:**

```
idle → loading (iframe src set)
     → success (iframe loaded, Office renders content)
     → error   (timeout or iframe error detected)
```

- Set a **15-second timeout** after the iframe `src` is set. If the
  iframe hasn't fired a successful `load` event by then, assume
  Office Online cannot reach the file and switch to the fallback.
- The iframe `onerror` event also triggers the fallback immediately.

**Fallback UI:**

When Office Online preview fails, show an inline card:

```
┌─────────────────────────────────────────┐
│  📄 report.docx                         │
│                                         │
│  在线预览不可用                           │
│  (外部预览服务无法加载此文件)              │
│                                         │
│  [ 下载文件 ]                            │
└─────────────────────────────────────────┘
```

- The download button uses the existing authenticated
  `download_artifact` endpoint (not the OTK URL).
- This naturally handles local dev environments where
  `localhost` is unreachable by Microsoft's servers.

## Security considerations

- **One-time use**: `GETDEL` is atomic — even concurrent requests cannot
  both succeed. After one fetch, the token is gone.
- **TTL cleanup**: Unused tokens expire after 5 minutes automatically.
- **No secrets in URL**: The nonce is random, not a signed JWT — there's
  no secret material to leak. Redis is the authority.
- **Filename validation**: The public endpoint checks that the URL
  filename matches the stored payload, preventing path traversal or
  artifact ID guessing.
- **Scope limitation**: The token grants access to exactly one file of
  one version of one artifact. No enumeration possible.
- **Production only**: Office Online Viewer requires a public URL. In
  local dev (localhost), this feature is non-functional. Frontend
  should detect this and fall back to download-only.

## Out of scope

- Client-side rendering of Office files (docx-preview, SheetJS) — may
  be added later as a localhost fallback.
- WOPI protocol integration — far more complex, not needed for
  read-only preview.
- Old Office formats (.doc, .xls, .ppt) — not supported by Office
  Online Viewer.
- Edit capability — view only.

## Testing

- **E2E**: Issue a preview token, then GET the public download URL.
  Verify the file is returned correctly and a second GET returns 404.
- **E2E**: Verify token expires after 5 minutes (or use a shorter TTL
  in test config).
- **E2E**: Verify invalid/missing tokens return 404.
- **E2E**: Verify filename mismatch returns 404.
