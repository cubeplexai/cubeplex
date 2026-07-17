# Sandbox Preview Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-purpose sandbox browser button with a full sandbox environment panel (Files / Browser / Terminal tabs).

**Architecture:** Extend the existing `ws_sandbox.py` backend with file-browsing, content, download, terminal, and Office preview-token endpoints. Frontend gets a new `SandboxPanel` component with three tab views, a file tree, and file preview. The panel store gains a `sandbox` view type that uses a wider default width.

**Tech Stack:** FastAPI, OpenSandbox SDK (filesystem.search, read_file, read_bytes_stream, get_signed_endpoint), React 19, SWR, react-resizable-panels, next-intl

**Spec:** `docs/dev/specs/2026-06-15-sandbox-preview-panel-design.md`

**Worktree:** `/home/chris/cubeplex/.worktrees/feat/2025-06-15-sandbox-preview`
**Ports:** backend 8077, frontend 3077

---

## File Map

### Sandbox image

| File | Action | Responsibility |
|------|--------|----------------|
| `misc/sandbox-image/Dockerfile` | Modify | Add `ttyd` package for web terminal |

### Backend — new/modified

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/cubeplex/api/routes/v1/ws_sandbox.py` | Modify | Add files, content, download, terminal, preview-token endpoints |
| `backend/cubeplex/api/routes/v1/sandbox_share.py` | Create | Public nonce-gated sandbox file download (proxy) |
| `backend/cubeplex/api/routes/v1/__init__.py` | Modify | Export `sandbox_share` |
| `backend/cubeplex/api/app.py` | Modify | Register `sandbox_share.router`, gate sandbox file endpoints behind `sandbox.enabled` |
| `backend/cubeplex/sandbox/base.py` | Modify | Add `TERMINAL_PORT`, `start_terminal()`, `get_terminal_endpoint()` |
| `backend/cubeplex/sandbox/opensandbox.py` | Modify | Implement `get_terminal_endpoint()` |

### Frontend — new/modified

| File | Action | Responsibility |
|------|--------|----------------|
| `frontend/packages/core/src/stores/panelStore.ts` | Modify | Add `type: 'sandbox'`, `openSandbox()` |
| `frontend/packages/web/components/layout/AppShell.tsx` | Modify | Route to SandboxPanel, wider panel sizing |
| `frontend/packages/web/components/panel/sandbox/SandboxPanel.tsx` | Create | Tab container (Files / Browser / Terminal) |
| `frontend/packages/web/components/panel/sandbox/SandboxFilesView.tsx` | Create | File tree + preview split |
| `frontend/packages/web/components/panel/sandbox/SandboxFileTree.tsx` | Create | Recursive file tree with lazy loading |
| `frontend/packages/web/components/panel/sandbox/SandboxFilePreview.tsx` | Create | File preview routing (code / office / html / fallback) |
| `frontend/packages/web/components/panel/sandbox/SandboxTerminalView.tsx` | Create | ttyd iframe + keepalive |
| `frontend/packages/web/hooks/useSandboxFiles.ts` | Create | SWR hook for file listing |
| `frontend/packages/web/hooks/useSandboxFileContent.ts` | Create | SWR hook for file content |
| `frontend/packages/web/hooks/useSandboxTerminal.ts` | Create | SWR hook for terminal URL |

---

## Task 0: Sandbox image — install ttyd

**Files:**
- Modify: `misc/sandbox-image/Dockerfile`

- [ ] **Step 1: Add ttyd to the Neko/browser apt-get layer**

In the Neko browser-takeover stack section (the `apt-get install` block around line 158), add `ttyd` to the package list:

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        xserver-xorg-core xserver-xorg-video-dummy x11-xserver-utils xauth \
        openbox pulseaudio dbus-x11 supervisor xclip \
        libgtk-3-0 libxtst6 libxcvt0 \
        libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 \
        gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav \
        libnss3-tools \
        ttyd \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
```

ttyd is not started at container boot — the backend starts it on demand via `pgrep -x ttyd || ttyd -p 7681 -W bash`.

- [ ] **Step 2: Commit**

```bash
git add misc/sandbox-image/Dockerfile
git commit -m "feat(sandbox-image): install ttyd for web terminal support"
```

Note: After merging, a new sandbox image must be built and pushed to the registry. The image tag in `config.sandbox.image` must be updated to point to the new image.

---

## Task 1: Backend — sandbox file listing endpoint

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/ws_sandbox.py`

- [ ] **Step 1: Add file listing endpoint to ws_sandbox.py**

Add the `/files` endpoint and its response model to the existing `ws_sandbox.py`:

```python
import mimetypes
import posixpath
from typing import Annotated

from fastapi import Depends, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel

from cubeplex.sandbox import SandboxError
from cubeplex.sandbox.manager import get_sandbox_manager


class SandboxFileEntry(BaseModel):
    path: str
    name: str
    is_dir: bool
    size: int
    modified_at: str


@router.get("/files", response_model=list[SandboxFileEntry])
async def list_sandbox_files(
    ctx: Annotated[RequestContext, Depends(require_member)],
    path: str = Query(default="/workspace"),
    pattern: str = Query(default="*"),
) -> list[SandboxFileEntry]:
    """List direct children of a directory in the user's sandbox."""
    normalized = posixpath.normpath(path)
    if not normalized.startswith("/workspace"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="path outside workspace"
        )
    manager = get_sandbox_manager()
    try:
        sandbox = await manager.get_or_create(
            ctx.user.id, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        await manager.touch(sandbox.id, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
        raw = sandbox._sandbox  # noqa: SLF001 — direct SDK access for filesystem
        from opensandbox.models.filesystem import SearchEntry

        entries = await raw.files.search(SearchEntry(path=normalized, pattern=pattern))
    except SandboxError as exc:
        logger.warning("sandbox file listing failed for workspace {}: {}", ctx.workspace_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sandbox unavailable; please retry",
        ) from exc

    # Filter to direct children only (SDK search is recursive)
    children = [
        e for e in entries if posixpath.dirname(posixpath.normpath(e.path)) == normalized
    ]
    # Sort: directories first, then alphabetical by name
    children.sort(key=lambda e: ((e.mode & 0o40000) == 0, posixpath.basename(e.path).lower()))

    result: list[SandboxFileEntry] = []
    for e in children:
        name = posixpath.basename(e.path)
        if not name:
            continue
        result.append(
            SandboxFileEntry(
                path=e.path,
                name=name,
                is_dir=(e.mode & 0o40000) != 0,
                size=e.size,
                modified_at=utc_isoformat(e.modified_at),
            )
        )
    return result
```

- [ ] **Step 2: Verify endpoint manually**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-sandbox-preview/backend
uv run python main.py
# In another terminal, call the endpoint with curl (need auth cookie)
```

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/api/routes/v1/ws_sandbox.py
git commit -m "feat(sandbox): add file listing endpoint"
```

---

## Task 2: Backend — file content and download endpoints

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/ws_sandbox.py`

- [ ] **Step 1: Add file content endpoint**

```python
MAX_PREVIEW_BYTES = 1_048_576  # 1 MB


class SandboxFileContent(BaseModel):
    content: str
    mime_type: str


@router.get("/files/content", response_model=SandboxFileContent)
async def get_sandbox_file_content(
    ctx: Annotated[RequestContext, Depends(require_member)],
    path: str = Query(...),
) -> SandboxFileContent:
    """Read a text file from the sandbox for inline preview."""
    normalized = posixpath.normpath(path)
    if not normalized.startswith("/workspace"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="path outside workspace"
        )
    manager = get_sandbox_manager()
    try:
        sandbox = await manager.get_or_create(
            ctx.user.id, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        raw = sandbox._sandbox  # noqa: SLF001
        info_map = await raw.files.get_file_info([path])
        info = info_map.get(path)
        if info and info.size > MAX_PREVIEW_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="file too large for preview; use download instead",
            )
        content = await raw.files.read_file(path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="file not found"
        ) from None
    except SandboxError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sandbox unavailable",
        ) from exc

    mime, _ = mimetypes.guess_type(path)
    return SandboxFileContent(content=content, mime_type=mime or "text/plain")
```

- [ ] **Step 2: Add file download endpoint**

```python
from fastapi.responses import StreamingResponse


@router.get("/files/download")
async def download_sandbox_file(
    ctx: Annotated[RequestContext, Depends(require_member)],
    path: str = Query(...),
) -> StreamingResponse:
    """Stream a file from the sandbox as a download."""
    normalized = posixpath.normpath(path)
    if not normalized.startswith("/workspace"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="path outside workspace"
        )
    manager = get_sandbox_manager()
    try:
        sandbox = await manager.get_or_create(
            ctx.user.id, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        raw = sandbox._sandbox  # noqa: SLF001
        stream = await raw.files.read_bytes_stream(path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="file not found"
        ) from None
    except SandboxError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sandbox unavailable",
        ) from exc

    filename = posixpath.basename(path)
    mime, _ = mimetypes.guess_type(filename)
    return StreamingResponse(
        stream,
        media_type=mime or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/api/routes/v1/ws_sandbox.py
git commit -m "feat(sandbox): add file content and download endpoints"
```

---

## Task 3: Backend — terminal endpoint

**Files:**
- Modify: `backend/cubeplex/sandbox/base.py`
- Modify: `backend/cubeplex/sandbox/opensandbox.py`
- Modify: `backend/cubeplex/api/routes/v1/ws_sandbox.py`

- [ ] **Step 1: Add terminal support to Sandbox base class**

In `backend/cubeplex/sandbox/base.py`, add after `BROWSER_PORT`:

```python
TERMINAL_PORT = 7681

async def start_terminal(self) -> None:
    """Start the on-demand ttyd terminal inside the sandbox (idempotent)."""
    result = await self.execute(
        "start-stop-daemon --start --background"
        " --make-pidfile --pidfile /tmp/ttyd.pid"
        " --exec /usr/bin/ttyd -- -p 7681 -W bash"
        " && sleep 1",
        timeout=30,
    )
    if result.exit_code not in (0, None):
        raise RuntimeError(f"failed to start sandbox terminal: {result.output}")

async def get_terminal_endpoint(
    self, *, expires_in: int = 3600
) -> BrowserEndpoint:
    """Return a reachable endpoint for the ttyd terminal."""
    raise NotImplementedError(
        "terminal is not supported by this sandbox backend"
    )
```

- [ ] **Step 2: Implement get_terminal_endpoint in OpenSandbox**

In `backend/cubeplex/sandbox/opensandbox.py`, add after `get_browser_endpoint`:

```python
async def get_terminal_endpoint(
    self, *, expires_in: int = 3600
) -> BrowserEndpoint:
    with _as_sandbox_error():
        expires = int(time.time()) + expires_in
        endpoint = await self._sandbox.get_signed_endpoint(
            self.TERMINAL_PORT, expires
        )
        url = endpoint.endpoint
        if not url.startswith(("http://", "https://")):
            protocol = getattr(
                self._sandbox.connection_config, "protocol", "http"
            )
            url = f"{protocol}://{url}"
        if not url.endswith("/"):
            url += "/"
        headers = {
            k: v
            for k, v in (endpoint.headers or {}).items()
            if k.lower() not in self._BROWSER_IRRELEVANT_HEADERS
        }
        return BrowserEndpoint(url=url, headers=headers)
```

- [ ] **Step 3: Add terminal route to ws_sandbox.py**

```python
class SandboxTerminalResponse(BaseModel):
    url: str


@router.get("/terminal", response_model=SandboxTerminalResponse)
async def get_terminal(
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> SandboxTerminalResponse:
    """Start ttyd in the sandbox and return a signed URL."""
    manager = get_sandbox_manager()
    try:
        sandbox = await manager.get_or_create(
            ctx.user.id, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        await sandbox.start_terminal()
        await manager.touch(
            sandbox.id, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        await manager.renew_lease(
            sandbox.id, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        endpoint = await sandbox.get_terminal_endpoint()
    except SandboxError as exc:
        logger.warning(
            "terminal unavailable for workspace {}: {}",
            ctx.workspace_id, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sandbox unavailable; please retry",
        ) from exc
    if endpoint.headers:
        raise HTTPException(
            status_code=501,
            detail="terminal endpoint requires header auth; not yet supported",
        )
    return SandboxTerminalResponse(url=endpoint.url)
```

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/sandbox/base.py backend/cubeplex/sandbox/opensandbox.py \
      backend/cubeplex/api/routes/v1/ws_sandbox.py
git commit -m "feat(sandbox): add terminal endpoint with ttyd support"
```

---

## Task 4: Backend — Office preview-token + public proxy download

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/ws_sandbox.py`
- Create: `backend/cubeplex/api/routes/v1/sandbox_share.py`
- Modify: `backend/cubeplex/api/routes/v1/__init__.py`
- Modify: `backend/cubeplex/api/app.py`

- [ ] **Step 1: Add preview-token endpoint to ws_sandbox.py**

```python
import secrets
from urllib.parse import quote

import orjson
from fastapi import Request

from cubeplex.cache import RedisHandle, redis_dep

SANDBOX_OTK_TTL_SECONDS = 300  # 5 minutes
OFFICE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}


class SandboxPreviewTokenResponse(BaseModel):
    download_url: str
    viewer_url: str


@router.post("/files/preview-token", response_model=SandboxPreviewTokenResponse)
async def create_sandbox_preview_token(
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
    rh: Annotated[RedisHandle, Depends(redis_dep)],
    path: str = Query(...),
) -> SandboxPreviewTokenResponse:
    """Issue a one-time nonce for Office Online Viewer to fetch a sandbox file."""
    filename = posixpath.basename(path)
    ext = filename[filename.rfind("."):].lower() if "." in filename else ""
    if ext not in OFFICE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Office preview not supported for extension '{ext}'",
        )

    manager = get_sandbox_manager()
    try:
        sandbox = await manager.get_or_create(
            ctx.user.id, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
    except SandboxError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sandbox unavailable",
        ) from exc

    nonce = secrets.token_hex(32)
    payload = orjson.dumps({
        "sandbox_id": sandbox.id,
        "file_path": path,
        "org_id": ctx.org_id,
        "workspace_id": ctx.workspace_id,
        "user_id": str(ctx.user.id),
    })
    key = f"{rh.key_prefix}:sandbox_otk:{nonce}"
    await rh.client.set(key, payload, ex=SANDBOX_OTK_TTL_SECONDS)

    from cubeplex.config import config

    public_url = config.get("api.public_url", "")
    base = (
        str(public_url).rstrip("/")
        if public_url
        else str(request.base_url).rstrip("/")
    )
    download_url = f"{base}/api/v1/public/sandbox/dl/{nonce}/{filename}"
    viewer_url = (
        f"https://view.officeapps.live.com/op/embed.aspx"
        f"?src={quote(download_url, safe='')}"
    )
    return SandboxPreviewTokenResponse(
        download_url=download_url, viewer_url=viewer_url
    )
```

- [ ] **Step 2: Create sandbox_share.py with public proxy download**

Create `backend/cubeplex/api/routes/v1/sandbox_share.py`:

```python
"""Public sandbox file download — nonce-gated, no auth.

The nonce IS the auth. Tokens are bound to (sandbox_id, file_path) and
expire after 5 minutes (see ws_sandbox.create_sandbox_preview_token).
The endpoint proxies the file from the live sandbox in real time — no
temp storage.
"""

from __future__ import annotations

import mimetypes
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from loguru import logger
import orjson
from opensandbox.config import ConnectionConfig

from cubeplex.cache import RedisHandle, redis_dep
from cubeplex.sandbox.manager import get_sandbox_manager
from cubeplex.sandbox.opensandbox import OpenSandbox

router = APIRouter(prefix="/public/sandbox", tags=["sandbox-share"])


@router.get("/dl/{nonce}/{filename}")
async def sandbox_file_download(
    nonce: str,
    filename: str,
    rh: Annotated[RedisHandle, Depends(redis_dep)],
) -> StreamingResponse:
    """Proxy a sandbox file to Microsoft Office Online Viewer."""
    key = f"{rh.key_prefix}:sandbox_otk:{nonce}"
    raw = await rh.client.get(key)
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="download link expired",
        )
    payload = orjson.loads(raw)
    sandbox_id = str(payload["sandbox_id"])
    file_path = str(payload["file_path"])

    # Reconnect to the sandbox by ID. The manager's connection config
    # carries the API key and domain — we don't need user context.
    manager = get_sandbox_manager()
    conn_config = manager._build_connection_config()  # noqa: SLF001
    try:
        sandbox = await OpenSandbox.connect_or_resume(
            sandbox_id, conn_config=conn_config
        )
        stream = await sandbox._sandbox.files.read_bytes_stream(file_path)  # noqa: SLF001
    except Exception as exc:
        logger.warning("sandbox proxy download failed: {}", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sandbox unavailable",
        ) from exc

    mime, _ = mimetypes.guess_type(filename)
    return StreamingResponse(
        stream,
        media_type=mime or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
```

- [ ] **Step 3: Register sandbox_share in __init__.py and app.py**

In `backend/cubeplex/api/routes/v1/__init__.py`, add to the import block:

```python
from cubeplex.api.routes.v1 import (
    # ... existing imports ...
    sandbox_share,  # <-- add
)
```

And to `__all__`:

```python
"sandbox_share",
```

In `backend/cubeplex/api/app.py`, near the existing `artifact_share` registration (around line 544):

```python
from cubeplex.api.routes.v1 import sandbox_share
app.include_router(sandbox_share.router, prefix="/api/v1")
```

- [ ] **Step 4: Gate the new sandbox endpoints behind sandbox.enabled**

In `backend/cubeplex/api/app.py`, the existing `ws_sandbox.router` is mounted **unconditionally** (line 563) because it only has `/status`. Keep it unconditional — `SandboxStatusCard` on the workspace `/sandbox` page calls `/status` regardless of the `sandboxEnabled` flag, and the endpoint only touches `UserSandboxRepository` (a plain DB query, no SandboxManager needed).

Add the new `sandbox_share.router` inside the `sandbox.enabled` gate alongside `ws_browser.router` (around line 582):

```python
if _sandbox_config.get("sandbox.enabled", False):
    app.include_router(ws_browser.router, prefix="/api/v1")
    app.include_router(sandbox_share.router, prefix="/api/v1")
```

The new file/terminal/preview-token endpoints live on the same `ws_sandbox.router` as `/status`, so they will also be unconditionally mounted. This is safe — each of those endpoints calls `get_sandbox_manager().get_or_create(...)` which raises `SandboxError` → 503 when the sandbox runtime is not configured. The frontend only shows the panel when `sandboxEnabled` is true, so users won't hit these endpoints when sandbox is off.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/api/routes/v1/ws_sandbox.py \
      backend/cubeplex/api/routes/v1/sandbox_share.py \
      backend/cubeplex/api/routes/v1/__init__.py \
      backend/cubeplex/api/app.py
git commit -m "feat(sandbox): Office preview-token + public proxy download"
```

---

## Task 5: Frontend — panel store + AppShell changes

**Files:**
- Modify: `frontend/packages/core/src/stores/panelStore.ts`
- Modify: `frontend/packages/web/components/layout/AppShell.tsx`

- [ ] **Step 1: Add sandbox view type to panelStore**

In `frontend/packages/core/src/stores/panelStore.ts`:

Add `| { type: 'sandbox' }` to the `PanelView` union (after the `browser` line).

Add `openSandbox` to the `PanelStore` interface:

```typescript
openSandbox: () => void
```

Add the implementation in the `create` block:

```typescript
openSandbox: () => set({ view: { type: 'sandbox' } }),
```

Remove `openBrowser` from the interface and implementation (it's replaced by `openSandbox`; the browser is now a tab within the sandbox panel).

- [ ] **Step 2: Update AppShell to use sandbox panel**

In `frontend/packages/web/components/layout/AppShell.tsx`:

Add import at the top:

```typescript
import { SandboxPanel } from '@/components/panel/sandbox/SandboxPanel'
```

Change the monitor icon click handler from `openBrowser` to `openSandbox`:

```typescript
const openSandbox = usePanelStore((s) => s.openSandbox)
// ...
<button onClick={openSandbox} ...>
```

Add the sandbox panel to the `panelContent` routing (replace the `browser` case):

```typescript
const panelContent =
  view.type === 'artifact' ? (
    <ArtifactPanel />
  ) : view.type === 'attachment' ? (
    <AttachmentPreviewView info={view.info} />
  ) : view.type === 'sandbox' ? (
    <SandboxPanel workspaceId={workspaceId} />
  ) : view.type === 'skill-candidate' ? (
    <SkillCandidatePanel ... />
  ) : (
    <ToolDetailPanel />
  )
```

Remove the `BrowserView` import (no longer used directly in AppShell).

Change the panel sizing for sandbox view. The `ResizablePanel` for the main content needs a pixel-based minSize. `react-resizable-panels` uses percentages, so compute the percentage from the constant:

```typescript
const SANDBOX_CONVERSATION_MIN_PX = 420

// Inside the component, compute min percentage for conversation area
const isSandboxPanel = view.type === 'sandbox'
```

For the `ResizablePanelGroup`, use conditional sizing:

```typescript
<ResizablePanel
  defaultSize={panelOpen ? (isSandboxPanel ? 25 : 50) : 100}
  minSize={isSandboxPanel ? 20 : 30}
>
  {main}
</ResizablePanel>

{panelOpen && (
  <>
    <ResizableHandle withHandle />
    <ResizablePanel
      defaultSize={isSandboxPanel ? 75 : 50}
      minSize={25}
    >
      {panelContent}
    </ResizablePanel>
  </>
)}
```

The exact percentage for `defaultSize` approximates the 420px minimum at typical viewport widths. The `minSize: 20` (20% of viewport) ensures conversation doesn't go below ~380px on a 1920px display.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/core/src/stores/panelStore.ts \
      frontend/packages/web/components/layout/AppShell.tsx
git commit -m "feat(sandbox): panel store sandbox view + wider AppShell layout"
```

---

## Task 6: Frontend — SandboxPanel tab container

**Files:**
- Create: `frontend/packages/web/components/panel/sandbox/SandboxPanel.tsx`

- [ ] **Step 1: Create the SandboxPanel component**

Create `frontend/packages/web/components/panel/sandbox/SandboxPanel.tsx`:

```typescript
'use client'

import { useState } from 'react'
import { FolderOpen, Globe, TerminalSquare } from 'lucide-react'
import { usePanelStore } from '@cubeplex/core'

import { PanelHeader } from '@/components/panel/PanelHeader'
import { BrowserView } from '@/components/panel/BrowserView'
import { SandboxFilesView } from './SandboxFilesView'
import { SandboxTerminalView } from './SandboxTerminalView'
import { cn } from '@/lib/utils'

type SandboxTab = 'files' | 'browser' | 'terminal'

interface SandboxPanelProps {
  workspaceId: string | null
}

const TABS: { id: SandboxTab; label: string; Icon: typeof FolderOpen }[] = [
  { id: 'files', label: 'Files', Icon: FolderOpen },
  { id: 'browser', label: 'Browser', Icon: Globe },
  { id: 'terminal', label: 'Terminal', Icon: TerminalSquare },
]

export function SandboxPanel({ workspaceId }: SandboxPanelProps) {
  const [activeTab, setActiveTab] = useState<SandboxTab>('files')
  const close = usePanelStore((s) => s.close)

  if (!workspaceId) return null

  return (
    <div className="flex h-full w-full flex-col">
      <PanelHeader
        source={{ kind: 'plain', icon: null, title: 'Sandbox' }}
        onClose={close}
      />
      <div className="flex border-b border-border bg-card shrink-0">
        {TABS.map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => setActiveTab(id)}
            className={cn(
              'flex items-center gap-1.5 px-4 py-2 text-xs font-medium transition-colors',
              activeTab === id
                ? 'text-foreground border-b-2 border-primary'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <Icon className="size-3.5" />
            {label}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-hidden">
        {activeTab === 'files' && (
          <SandboxFilesView workspaceId={workspaceId} />
        )}
        {activeTab === 'browser' && (
          <BrowserView workspaceId={workspaceId} />
        )}
        {activeTab === 'terminal' && (
          <SandboxTerminalView workspaceId={workspaceId} />
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Create placeholder SandboxFilesView and SandboxTerminalView**

Create `frontend/packages/web/components/panel/sandbox/SandboxFilesView.tsx`:

```typescript
'use client'

interface SandboxFilesViewProps {
  workspaceId: string
}

export function SandboxFilesView({ workspaceId }: SandboxFilesViewProps) {
  return (
    <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
      File browser — coming next
    </div>
  )
}
```

Create `frontend/packages/web/components/panel/sandbox/SandboxTerminalView.tsx`:

```typescript
'use client'

interface SandboxTerminalViewProps {
  workspaceId: string
}

export function SandboxTerminalView({ workspaceId }: SandboxTerminalViewProps) {
  return (
    <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
      Terminal — coming next
    </div>
  )
}
```

- [ ] **Step 3: Verify tabs render in the browser**

Start the frontend dev server and confirm the sandbox panel opens with three tabs, and switching tabs works:

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-sandbox-preview/frontend
source ../.worktree.env && pnpm dev
```

Open `http://192.168.1.150:3077`, click the monitor icon, confirm tabs show.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/panel/sandbox/
git commit -m "feat(sandbox): SandboxPanel tab container with Files/Browser/Terminal"
```

---

## Task 7: Frontend — SWR hooks for sandbox API

**Files:**
- Create: `frontend/packages/web/hooks/useSandboxFiles.ts`
- Create: `frontend/packages/web/hooks/useSandboxFileContent.ts`
- Create: `frontend/packages/web/hooks/useSandboxTerminal.ts`

- [ ] **Step 1: Create useSandboxFiles hook**

Create `frontend/packages/web/hooks/useSandboxFiles.ts`:

```typescript
'use client'

import useSWR from 'swr'

export interface SandboxFileEntry {
  path: string
  name: string
  is_dir: boolean
  size: number
  modified_at: string
}

async function fetcher(url: string): Promise<SandboxFileEntry[]> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`sandbox files fetch failed: ${res.status}`)
  return res.json() as Promise<SandboxFileEntry[]>
}

export function useSandboxFiles(workspaceId: string | null, path: string) {
  const key =
    workspaceId
      ? `/api/v1/ws/${workspaceId}/sandbox/files?path=${encodeURIComponent(path)}`
      : null
  const { data, error, isLoading, mutate } = useSWR<SandboxFileEntry[]>(
    key,
    fetcher,
    { revalidateOnFocus: false },
  )
  return { files: data ?? [], error, loading: isLoading, refresh: mutate }
}
```

- [ ] **Step 2: Create useSandboxFileContent hook**

Create `frontend/packages/web/hooks/useSandboxFileContent.ts`:

```typescript
'use client'

import useSWR from 'swr'

interface SandboxFileContent {
  content: string
  mime_type: string
}

async function fetcher(url: string): Promise<SandboxFileContent> {
  const res = await fetch(url, { credentials: 'include' })
  if (res.status === 413) throw new Error('FILE_TOO_LARGE')
  if (!res.ok) throw new Error(`file content fetch failed: ${res.status}`)
  return res.json() as Promise<SandboxFileContent>
}

export function useSandboxFileContent(
  workspaceId: string | null,
  path: string | null,
) {
  const key =
    workspaceId && path
      ? `/api/v1/ws/${workspaceId}/sandbox/files/content?path=${encodeURIComponent(path)}`
      : null
  const { data, error, isLoading } = useSWR<SandboxFileContent>(key, fetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })
  return {
    content: data?.content ?? null,
    mimeType: data?.mime_type ?? null,
    error,
    loading: isLoading,
  }
}
```

- [ ] **Step 3: Create useSandboxTerminal hook**

Create `frontend/packages/web/hooks/useSandboxTerminal.ts`:

```typescript
'use client'

import useSWR from 'swr'

interface SandboxTerminal {
  url: string
}

async function fetcher(url: string): Promise<SandboxTerminal> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`terminal fetch failed: ${res.status}`)
  return res.json() as Promise<SandboxTerminal>
}

export function useSandboxTerminal(
  workspaceId: string | null,
  enabled = true,
) {
  const key =
    workspaceId && enabled
      ? `/api/v1/ws/${workspaceId}/sandbox/terminal`
      : null
  const { data, error, isLoading, mutate } = useSWR<SandboxTerminal>(
    key,
    fetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
  return {
    url: data?.url ?? null,
    loading: isLoading,
    error: error as Error | undefined,
    refresh: mutate,
  }
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/hooks/useSandboxFiles.ts \
      frontend/packages/web/hooks/useSandboxFileContent.ts \
      frontend/packages/web/hooks/useSandboxTerminal.ts
git commit -m "feat(sandbox): SWR hooks for files, content, and terminal"
```

---

## Task 8: Frontend — SandboxTerminalView

**Files:**
- Modify: `frontend/packages/web/components/panel/sandbox/SandboxTerminalView.tsx`

- [ ] **Step 1: Implement SandboxTerminalView with iframe + keepalive**

Replace the placeholder in `SandboxTerminalView.tsx`:

```typescript
'use client'

import { useEffect } from 'react'
import { RefreshCw } from 'lucide-react'

import { useSandboxTerminal } from '@/hooks/useSandboxTerminal'
import { csrfHeaders } from '@/lib/csrf'

const KEEPALIVE_MS = 30_000

interface SandboxTerminalViewProps {
  workspaceId: string
}

export function SandboxTerminalView({ workspaceId }: SandboxTerminalViewProps) {
  const { url, loading, error, refresh } = useSandboxTerminal(workspaceId)

  useEffect(() => {
    if (!url) return
    const ping = () => {
      void fetch(`/api/v1/ws/${workspaceId}/browser/keepalive`, {
        method: 'POST',
        credentials: 'include',
        headers: csrfHeaders(),
      }).catch(() => {})
    }
    const id = setInterval(ping, KEEPALIVE_MS)
    return () => clearInterval(id)
  }, [workspaceId, url])

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Starting terminal…
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-sm">
        <p className="text-destructive">
          Could not start terminal. {error.message}
        </p>
        <button
          type="button"
          onClick={() => refresh()}
          className="inline-flex items-center gap-1.5 rounded border border-border px-3 py-1.5
            text-xs font-medium hover:bg-muted transition-colors"
        >
          <RefreshCw className="size-3" />
          Retry
        </button>
      </div>
    )
  }

  if (!url) return null

  return (
    <iframe
      title="Sandbox terminal"
      src={url}
      className="h-full w-full border-0"
      allow="fullscreen; clipboard-read; clipboard-write"
    />
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/packages/web/components/panel/sandbox/SandboxTerminalView.tsx
git commit -m "feat(sandbox): terminal view with ttyd iframe"
```

---

## Task 9: Frontend — SandboxFileTree

**Files:**
- Create: `frontend/packages/web/components/panel/sandbox/SandboxFileTree.tsx`

- [ ] **Step 1: Create the recursive file tree component**

Create `frontend/packages/web/components/panel/sandbox/SandboxFileTree.tsx`:

```typescript
'use client'

import { useState, useCallback } from 'react'
import { ChevronRight, Download, Loader2, FolderOpen, Folder } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useSandboxFiles, type SandboxFileEntry } from '@/hooks/useSandboxFiles'
import { getFileVisual } from '@/lib/fileIcons'

interface SandboxFileTreeProps {
  workspaceId: string
  onSelectFile: (entry: SandboxFileEntry) => void
  selectedPath: string | null
}

export function SandboxFileTree({
  workspaceId,
  onSelectFile,
  selectedPath,
}: SandboxFileTreeProps) {
  return (
    <div className="h-full overflow-auto py-1">
      <TreeDirectory
        workspaceId={workspaceId}
        path="/workspace"
        depth={0}
        defaultOpen
        onSelectFile={onSelectFile}
        selectedPath={selectedPath}
      />
    </div>
  )
}

function TreeDirectory({
  workspaceId,
  path,
  depth,
  defaultOpen = false,
  onSelectFile,
  selectedPath,
}: {
  workspaceId: string
  path: string
  depth: number
  defaultOpen?: boolean
  onSelectFile: (entry: SandboxFileEntry) => void
  selectedPath: string | null
}) {
  const [open, setOpen] = useState(defaultOpen)
  const { files, loading } = useSandboxFiles(
    open ? workspaceId : null,
    path,
  )

  return (
    <>
      {depth > 0 && (
        <TreeRow
          name={path.split('/').pop() || path}
          isDir
          depth={depth}
          open={open}
          onClick={() => setOpen((v) => !v)}
          selected={false}
          workspaceId={workspaceId}
          path={path}
        />
      )}
      {open && loading && (
        <div
          className="flex items-center gap-1.5 py-1 text-xs text-muted-foreground"
          style={{ paddingLeft: (depth + 1) * 16 + 8 }}
        >
          <Loader2 className="size-3 animate-spin" />
          Loading…
        </div>
      )}
      {open &&
        files.map((entry) =>
          entry.is_dir ? (
            <TreeDirectory
              key={entry.path}
              workspaceId={workspaceId}
              path={entry.path}
              depth={depth + 1}
              onSelectFile={onSelectFile}
              selectedPath={selectedPath}
            />
          ) : (
            <TreeRow
              key={entry.path}
              name={entry.name}
              isDir={false}
              depth={depth + 1}
              selected={selectedPath === entry.path}
              onClick={() => onSelectFile(entry)}
              workspaceId={workspaceId}
              path={entry.path}
            />
          ),
        )}
    </>
  )
}

function TreeRow({
  name,
  isDir,
  depth,
  open,
  selected,
  onClick,
  workspaceId,
  path,
}: {
  name: string
  isDir: boolean
  depth: number
  open?: boolean
  selected: boolean
  onClick: () => void
  workspaceId: string
  path: string
}) {
  const visual = isDir ? null : getFileVisual({ filename: name })
  const FileIcon = visual?.Icon

  const handleDownload = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation()
      const url = `/api/v1/ws/${workspaceId}/sandbox/files/download?path=${encodeURIComponent(path)}`
      window.open(url, '_blank')
    },
    [workspaceId, path],
  )

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'group flex w-full items-center gap-1 py-1 pr-2 text-xs hover:bg-muted/50',
        'transition-colors text-left',
        selected && 'bg-muted',
      )}
      style={{ paddingLeft: depth * 16 + 8 }}
    >
      {isDir ? (
        <>
          <ChevronRight
            className={cn(
              'size-3 shrink-0 text-muted-foreground transition-transform',
              open && 'rotate-90',
            )}
          />
          {open ? (
            <FolderOpen className="size-3.5 shrink-0 text-amber-500" />
          ) : (
            <Folder className="size-3.5 shrink-0 text-amber-500" />
          )}
        </>
      ) : (
        <>
          <span className="size-3 shrink-0" />
          {FileIcon ? (
            <FileIcon className="size-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <span className="size-3.5 shrink-0" />
          )}
        </>
      )}
      <span className="flex-1 truncate">{name}</span>
      {!isDir && (
        <span
          role="button"
          tabIndex={-1}
          onClick={handleDownload}
          className="hidden group-hover:block p-0.5 rounded hover:bg-accent"
          title="Download"
        >
          <Download className="size-3 text-muted-foreground" />
        </span>
      )}
    </button>
  )
}
```

`getFileVisual` is from `@/lib/fileIcons.ts` — takes `{ filename }` and returns `{ Icon, label, bg, fg, family }`.

- [ ] **Step 2: Commit**

```bash
git add frontend/packages/web/components/panel/sandbox/SandboxFileTree.tsx
git commit -m "feat(sandbox): file tree component with lazy loading"
```

---

## Task 10: Frontend — SandboxFilePreview

**Files:**
- Create: `frontend/packages/web/components/panel/sandbox/SandboxFilePreview.tsx`

- [ ] **Step 1: Create the file preview component**

Create `frontend/packages/web/components/panel/sandbox/SandboxFilePreview.tsx`:

```typescript
'use client'

import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { Download, FileText } from 'lucide-react'
import { csrfHeaders } from '@/lib/csrf'
import { useSandboxFileContent } from '@/hooks/useSandboxFileContent'
import type { SandboxFileEntry } from '@/hooks/useSandboxFiles'
import { PreviewLoading } from '@/components/panel/artifact/PreviewLoading'

const OFFICE_EXTENSIONS = new Set(['.docx', '.xlsx', '.pptx'])
const TEXT_EXTENSIONS = new Set([
  '.txt', '.md', '.py', '.js', '.ts', '.tsx', '.jsx', '.json', '.yaml',
  '.yml', '.toml', '.cfg', '.ini', '.sh', '.bash', '.zsh', '.css',
  '.scss', '.less', '.sql', '.rs', '.go', '.java', '.c', '.cpp', '.h',
  '.rb', '.php', '.swift', '.kt', '.r', '.lua', '.pl', '.ex', '.exs',
  '.vue', '.svelte', '.xml', '.csv', '.log', '.env', '.gitignore',
  '.dockerfile', '.makefile',
])

function getExtension(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot).toLowerCase() : ''
}

function isTextFile(name: string): boolean {
  const ext = getExtension(name)
  if (TEXT_EXTENSIONS.has(ext)) return true
  if (ext === '.html') return false // HTML gets iframe preview
  if (!ext) return true // extensionless files assumed text
  return false
}

interface SandboxFilePreviewProps {
  entry: SandboxFileEntry
  workspaceId: string
}

export function SandboxFilePreview({
  entry,
  workspaceId,
}: SandboxFilePreviewProps) {
  const ext = getExtension(entry.name)

  if (OFFICE_EXTENSIONS.has(ext)) {
    return <OfficeFilePreview entry={entry} workspaceId={workspaceId} />
  }
  if (ext === '.html') {
    return <HtmlFilePreview entry={entry} workspaceId={workspaceId} />
  }
  if (isTextFile(entry.name)) {
    return <TextFilePreview entry={entry} workspaceId={workspaceId} />
  }
  return <FallbackPreview entry={entry} workspaceId={workspaceId} />
}

function TextFilePreview({
  entry,
  workspaceId,
}: {
  entry: SandboxFileEntry
  workspaceId: string
}) {
  const { content, error, loading } = useSandboxFileContent(
    workspaceId,
    entry.path,
  )

  if (loading) return <PreviewLoading />
  if (error?.message === 'FILE_TOO_LARGE') {
    return <FallbackPreview entry={entry} workspaceId={workspaceId} />
  }
  if (error) {
    return (
      <div className="p-4 text-sm text-destructive">
        Failed to load: {error.message}
      </div>
    )
  }

  return (
    <div className="h-full overflow-auto">
      <pre className="p-4 text-xs leading-relaxed font-mono text-foreground whitespace-pre-wrap break-words">
        {content}
      </pre>
    </div>
  )
}

function HtmlFilePreview({
  entry,
  workspaceId,
}: {
  entry: SandboxFileEntry
  workspaceId: string
}) {
  const { content, error, loading } = useSandboxFileContent(
    workspaceId,
    entry.path,
  )
  const blobUrl = useMemo(() => {
    if (!content) return null
    const blob = new Blob([content], { type: 'text/html' })
    return URL.createObjectURL(blob)
  }, [content])

  useEffect(() => {
    return () => {
      if (blobUrl) URL.revokeObjectURL(blobUrl)
    }
  }, [blobUrl])

  if (loading) return <PreviewLoading />
  if (error) {
    return (
      <div className="p-4 text-sm text-destructive">
        Failed to load: {error.message}
      </div>
    )
  }
  if (!blobUrl) return null

  return (
    <iframe
      title={`Preview: ${entry.name}`}
      src={blobUrl}
      className="h-full w-full border-0"
      sandbox="allow-scripts"
    />
  )
}

const OFFICE_LOAD_TIMEOUT_MS = 15_000

function OfficeFilePreview({
  entry,
  workspaceId,
}: {
  entry: SandboxFileEntry
  workspaceId: string
}) {
  const [viewerUrl, setViewerUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [timedOut, setTimedOut] = useState(false)
  const loadCountRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setTimeout>>()

  useEffect(() => {
    let cancelled = false
    const fetchToken = async () => {
      try {
        const res = await fetch(
          `/api/v1/ws/${workspaceId}/sandbox/files/preview-token?path=${encodeURIComponent(entry.path)}`,
          { method: 'POST', credentials: 'include', headers: csrfHeaders() },
        )
        if (!res.ok) throw new Error(`${res.status}`)
        const data = (await res.json()) as { viewer_url: string }
        if (!cancelled) setViewerUrl(data.viewer_url)
      } catch (e) {
        if (!cancelled) setError((e as Error).message)
      }
    }
    void fetchToken()
    return () => {
      cancelled = true
    }
  }, [workspaceId, entry.path])

  useEffect(() => {
    if (!viewerUrl) return
    setTimedOut(false)
    loadCountRef.current = 0
    timerRef.current = setTimeout(() => setTimedOut(true), OFFICE_LOAD_TIMEOUT_MS)
    return () => clearTimeout(timerRef.current)
  }, [viewerUrl])

  const handleIframeLoad = useCallback(() => {
    loadCountRef.current += 1
    if (loadCountRef.current > 1) {
      clearTimeout(timerRef.current)
    }
  }, [])

  const handleRetry = useCallback(() => {
    setTimedOut(false)
    setViewerUrl(null)
    setError(null)
  }, [])

  if (error) {
    return <FallbackPreview entry={entry} workspaceId={workspaceId} />
  }
  if (!viewerUrl) return <PreviewLoading />

  if (timedOut) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-8 text-center">
        <p className="text-sm text-muted-foreground">
          Office preview timed out.
        </p>
        <button
          onClick={handleRetry}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          Retry
        </button>
      </div>
    )
  }

  return (
    <iframe
      src={viewerUrl}
      className="h-full w-full border-0"
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
      onLoad={handleIframeLoad}
    />
  )
}

function FallbackPreview({
  entry,
  workspaceId,
}: {
  entry: SandboxFileEntry
  workspaceId: string
}) {
  const downloadUrl = `/api/v1/ws/${workspaceId}/sandbox/files/download?path=${encodeURIComponent(entry.path)}`
  const sizeLabel =
    entry.size < 1024
      ? `${entry.size} B`
      : entry.size < 1_048_576
        ? `${(entry.size / 1024).toFixed(1)} KB`
        : `${(entry.size / 1_048_576).toFixed(1)} MB`

  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 p-8 text-center">
      <div className="flex size-16 items-center justify-center rounded-xl bg-muted">
        <FileText className="size-8 text-muted-foreground" />
      </div>
      <div>
        <h3 className="text-sm font-medium text-foreground">{entry.name}</h3>
        <p className="mt-1 text-xs text-muted-foreground">{sizeLabel}</p>
      </div>
      <a
        href={downloadUrl}
        className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2
          text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
      >
        <Download className="size-4" />
        Download
      </a>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/packages/web/components/panel/sandbox/SandboxFilePreview.tsx
git commit -m "feat(sandbox): file preview with text/html/office/fallback routing"
```

---

## Task 11: Frontend — SandboxFilesView (tree + preview split)

**Files:**
- Modify: `frontend/packages/web/components/panel/sandbox/SandboxFilesView.tsx`

- [ ] **Step 1: Implement the split layout**

Replace the placeholder in `SandboxFilesView.tsx`:

```typescript
'use client'

import { useState } from 'react'
import {
  ResizablePanelGroup,
  ResizablePanel,
  ResizableHandle,
} from '@/components/ui/resizable'
import { SandboxFileTree } from './SandboxFileTree'
import { SandboxFilePreview } from './SandboxFilePreview'
import type { SandboxFileEntry } from '@/hooks/useSandboxFiles'

interface SandboxFilesViewProps {
  workspaceId: string
}

export function SandboxFilesView({ workspaceId }: SandboxFilesViewProps) {
  const [selectedFile, setSelectedFile] = useState<SandboxFileEntry | null>(
    null,
  )

  return (
    <ResizablePanelGroup direction="horizontal" className="h-full">
      <ResizablePanel
        defaultSize={selectedFile ? 30 : 100}
        minSize={20}
      >
        <SandboxFileTree
          workspaceId={workspaceId}
          onSelectFile={setSelectedFile}
          selectedPath={selectedFile?.path ?? null}
        />
      </ResizablePanel>
      {selectedFile && (
        <>
          <ResizableHandle withHandle />
          <ResizablePanel defaultSize={70} minSize={30}>
            <SandboxFilePreview
              key={selectedFile.path}
              entry={selectedFile}
              workspaceId={workspaceId}
            />
          </ResizablePanel>
        </>
      )}
    </ResizablePanelGroup>
  )
}
```

- [ ] **Step 2: Verify the full flow in the browser**

Start both backend and frontend. Open the sandbox panel → Files tab. Confirm:
- File tree loads `/workspace` contents
- Directories expand on click
- Clicking a file shows preview in the right pane
- Download button works
- Tab switching between Files/Browser/Terminal works

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/panel/sandbox/SandboxFilesView.tsx
git commit -m "feat(sandbox): files view with tree + preview resizable split"
```

---

## Task 12: Frontend — verify no remaining openBrowser references

- [ ] **Step 1: Search for any remaining openBrowser references**

```bash
grep -rn "openBrowser" frontend/
```

Task 5 already renamed `openBrowser` → `openSandbox` in `panelStore.ts` and `AppShell.tsx` (the only two call sites). This step confirms no references were missed. If the grep returns results, update them and commit. If empty, this task is done — no commit needed.

---

## Task 13: Type-check and lint

- [ ] **Step 1: Run TypeScript type-check**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-sandbox-preview/frontend
pnpm tsc --noEmit
```

Fix any type errors.

- [ ] **Step 2: Run mypy on backend**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-sandbox-preview/backend
uv run mypy cubeplex/api/routes/v1/ws_sandbox.py cubeplex/api/routes/v1/sandbox_share.py \
    cubeplex/sandbox/base.py cubeplex/sandbox/opensandbox.py
```

Fix any type errors.

- [ ] **Step 3: Run eslint**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-sandbox-preview/frontend
pnpm lint
```

Fix any lint errors.

- [ ] **Step 4: Commit fixes**

```bash
git add -u
git commit -m "fix: type-check and lint fixes for sandbox preview"
```
