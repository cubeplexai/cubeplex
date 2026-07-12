# Office File Preview via One-Time Token — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable in-browser preview of Office files (docx, xlsx, pptx) via Microsoft Office Online Viewer, using a one-time-use public download token stored in Redis.

**Architecture:** Backend issues a time-limited one-time token (OTK) stored in Redis. A new unauthenticated public endpoint serves the file to Microsoft's servers using `GETDEL` for atomic one-time enforcement. Frontend detects Office file types and embeds the viewer in an iframe with a fallback download UI when the viewer fails.

**Tech Stack:** FastAPI, Redis (`GETDEL`), Python `secrets` module, React iframe, next-intl i18n

---

## File Structure

### Backend (new files)

| File | Responsibility |
|------|---------------|
| `backend/cubeplex/api/routes/v1/public_artifacts.py` | Public (no-auth) download endpoint: validate OTK, stream file |
| `backend/tests/e2e/test_otk_preview.py` | E2E tests for OTK issue + consume + one-time + expiry |

### Backend (modified files)

| File | Change |
|------|--------|
| `backend/cubeplex/api/routes/v1/artifacts.py` | Add `POST .../preview-token` endpoint |
| `backend/cubeplex/api/routes/v1/__init__.py` | Export `public_artifacts` |
| `backend/cubeplex/api/app.py` | Register `public_artifacts.router` |
| `backend/config.yaml` | Add `api.public_url: ""` config key |

### Frontend (modified files)

| File | Change |
|------|--------|
| `frontend/packages/core/src/api/conversations.ts` | Add `requestPreviewToken()` API function |
| `frontend/packages/web/components/panel/artifact/ArtifactPanel.tsx` | Route Office files to `OfficePreview` in `PreviewContent` |
| `frontend/packages/web/components/panel/artifact/OfficePreview.tsx` | **New** — iframe embed + timeout fallback |
| `frontend/packages/web/messages/en.json` | Add `panel.office.*` i18n keys |
| `frontend/packages/web/messages/zh.json` | Add `panel.office.*` i18n keys |

---

## Task 1: Add `api.public_url` config key

**Files:**
- Modify: `backend/config.yaml`

- [ ] **Step 1: Add the config key**

In `backend/config.yaml`, add `public_url: ""` under the existing `api:` section, after `reload: true`:

```yaml
  api:
    host: "0.0.0.0"
    port: 8000
    reload: true
    public_url: ""
```

This is overridable via `CUBEPLEX_API__PUBLIC_URL` (dynaconf convention).

- [ ] **Step 2: Commit**

```bash
git add backend/config.yaml
git commit -m "$(cat <<'EOF'
feat(config): add api.public_url for OTK download base URL
EOF
)"
```

---

## Task 2: Add the `POST preview-token` endpoint (authenticated)

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/artifacts.py`

- [ ] **Step 1: Add the preview-token endpoint**

Add these imports at the top of `backend/cubeplex/api/routes/v1/artifacts.py`:

```python
import secrets
from urllib.parse import quote

import orjson
from fastapi import Request

from cubeplex.cache import redis_dep, RedisHandle
from cubeplex.config import config
```

Then add this endpoint after the existing `download_artifact` function (after line 148, before the `preview_artifact_file` function):

```python
OFFICE_EXTENSIONS = frozenset({".docx", ".xlsx", ".pptx"})
OTK_TTL_SECONDS = 300


@router.post("/{artifact_id}/preview-token")
async def create_preview_token(
    conversation_id: str,
    artifact_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    rh: Annotated[RedisHandle, Depends(redis_dep)],
    version: int | None = Query(default=None),
) -> dict[str, str]:
    """Issue a one-time public download token for Office Online Viewer."""
    repo = ArtifactRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    artifact = await repo.get_by_id(artifact_id)
    if not artifact or artifact.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )

    target_version = version or artifact.version
    filename = artifact.entry_file or artifact.path.rsplit("/", 1)[-1]
    ext = filename[filename.rfind("."):].lower() if "." in filename else ""
    if ext not in OFFICE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Office preview not supported for extension '{ext}'",
        )

    nonce = secrets.token_hex(32)
    payload = orjson.dumps({
        "conversation_id": conversation_id,
        "artifact_id": artifact_id,
        "version": target_version,
        "filename": filename,
    })
    key = f"{rh.key_prefix}:otk:{nonce}"
    await rh.client.set(key, payload, ex=OTK_TTL_SECONDS)

    public_url = config.get("api.public_url", "")
    if public_url:
        base = str(public_url).rstrip("/")
    else:
        base = str(request.base_url).rstrip("/")

    download_url = f"{base}/api/v1/public/artifacts/dl/{nonce}/{filename}"
    viewer_url = (
        f"https://view.officeapps.live.com/op/embed.aspx?src={quote(download_url, safe='')}"
    )

    return {"download_url": download_url, "viewer_url": viewer_url}
```

- [ ] **Step 2: Verify lint passes**

Run from `backend/`:

```bash
cd /home/chris/cubeplex/.worktrees/feat/office-preview-token/backend && uv run ruff check cubeplex/api/routes/v1/artifacts.py
```

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/api/routes/v1/artifacts.py
git commit -m "$(cat <<'EOF'
feat(api): add POST preview-token endpoint for Office OTK
EOF
)"
```

---

## Task 3: Add the public download endpoint (no auth)

**Files:**
- Create: `backend/cubeplex/api/routes/v1/public_artifacts.py`

- [ ] **Step 1: Create the public artifacts router**

Create `backend/cubeplex/api/routes/v1/public_artifacts.py`:

```python
"""Public (unauthenticated) artifact download via one-time token."""

import mimetypes
from typing import Annotated

import orjson
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from loguru import logger

from cubeplex.cache import RedisHandle, redis_dep
from cubeplex.objectstore import get_objectstore_client

router = APIRouter(prefix="/public/artifacts", tags=["public-artifacts"])


@router.get("/dl/{token}/{filename}")
async def public_download(
    token: str,
    filename: str,
    rh: Annotated[RedisHandle, Depends(redis_dep)],
) -> Response:
    """Serve an artifact file using a one-time download token.

    Microsoft Office Online Viewer calls this URL exactly once to fetch the
    file. The token is atomically deleted on first use (GETDEL).
    """
    key = f"{rh.key_prefix}:otk:{token}"
    raw: bytes | str | None = await rh.client.getdel(key)  # type: ignore[assignment]
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found or already used",
        )

    payload = orjson.loads(raw if isinstance(raw, bytes) else raw.encode())
    stored_filename: str = payload["filename"]
    if filename != stored_filename:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Filename mismatch",
        )

    conversation_id: str = payload["conversation_id"]
    artifact_id: str = payload["artifact_id"]
    version: int = payload["version"]
    obj_key = f"artifacts/{conversation_id}/{artifact_id}/v{version}/{stored_filename}"

    try:
        store = get_objectstore_client()
        data, stored_content_type = await store.download_file(obj_key)
    except Exception as e:
        logger.error("OTK download failed for {}: {}", obj_key, e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found in storage",
        ) from None

    mime, _ = mimetypes.guess_type(stored_filename)
    media_type = mime or stored_content_type or "application/octet-stream"

    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{stored_filename}"'},
    )
```

- [ ] **Step 2: Verify lint passes**

```bash
cd /home/chris/cubeplex/.worktrees/feat/office-preview-token/backend && uv run ruff check cubeplex/api/routes/v1/public_artifacts.py
```

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/api/routes/v1/public_artifacts.py
git commit -m "$(cat <<'EOF'
feat(api): add public one-time artifact download endpoint
EOF
)"
```

---

## Task 4: Register the public router

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/__init__.py`
- Modify: `backend/cubeplex/api/app.py`

- [ ] **Step 1: Export from routes __init__**

In `backend/cubeplex/api/routes/v1/__init__.py`, add the import and export.

Add to imports (after the existing `from cubeplex.api.routes.v1.artifacts import router as artifacts_router` line):

```python
from cubeplex.api.routes.v1 import public_artifacts
```

Add `"public_artifacts"` to the `__all__` list.

- [ ] **Step 2: Register in app.py**

In `backend/cubeplex/api/app.py`, inside `create_app()`, add the router import and registration.

Add `public_artifacts` to the existing import block (around line 393-409):

```python
from cubeplex.api.routes.v1 import (
    ...
    public_artifacts,
    ...
)
```

Add registration after the `artifacts_router` line (after line 414):

```python
app.include_router(public_artifacts.router, prefix="/api/v1")
```

- [ ] **Step 3: Verify the app starts without errors**

```bash
cd /home/chris/cubeplex/.worktrees/feat/office-preview-token/backend && uv run python -c "from cubeplex.api.app import create_app; app = create_app(); print('OK:', [r.path for r in app.routes if 'public' in getattr(r, 'path', '')])"
```

Expected: prints a list containing `/api/v1/public/artifacts/dl/{token}/{filename}`.

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/api/routes/v1/__init__.py backend/cubeplex/api/app.py
git commit -m "$(cat <<'EOF'
feat(api): register public artifacts router
EOF
)"
```

---

## Task 5: E2E tests for OTK preview flow

**Files:**
- Create: `backend/tests/e2e/test_otk_preview.py`

The tests need a seeded artifact with an Office file in object storage. We reuse the same pattern as `_seed_skill_artifact` in `conftest.py` but with a `.docx` file.

- [ ] **Step 1: Write the E2E test file**

Create `backend/tests/e2e/test_otk_preview.py`:

```python
"""E2E tests for one-time-token (OTK) artifact preview."""

from collections.abc import AsyncIterator

import httpx
import orjson
import pytest
import pytest_asyncio
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.config import config as _cubeplex_config
from cubeplex.db.engine import _build_database_url
from cubeplex.models import Artifact, Conversation, Membership, Workspace
from cubeplex.objectstore import get_objectstore_client
from tests.e2e.conftest import (
    DEFAULT_WS_ID,
    _lifespan_context,
    _login_and_attach,
    _make_isolated_user,
)
from cubeplex.models import Role
from tests.e2e.helpers import csrf_cookie_name


DOCX_BYTES = (
    b"PK\x03\x04\x14\x00\x00\x00\x00\x00"
    b"\x00\x00!\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x13\x00\x00\x00[Content_Types].xml"
)


async def _seed_office_artifact(
    workspace_id: str,
    *,
    filename: str = "report.docx",
    file_bytes: bytes = DOCX_BYTES,
) -> tuple[str, str]:
    """Seed a conversation + artifact + upload file to object storage.

    Returns (artifact_id, conversation_id).
    """
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            ws = await session.get(Workspace, workspace_id)
            assert ws is not None
            org_id = ws.org_id

            stmt = sa_select(Membership).where(Membership.workspace_id == workspace_id)
            mem = (await session.execute(stmt)).scalars().first()
            assert mem is not None
            user_id = str(mem.user_id)

            conv = Conversation(
                org_id=org_id,
                workspace_id=workspace_id,
                creator_user_id=user_id,
                title="office preview test",
            )
            session.add(conv)
            await session.flush()

            artifact = Artifact(
                org_id=org_id,
                workspace_id=workspace_id,
                conversation_id=conv.id,
                name=filename,
                artifact_type="file",
                path=f"/workspace/{filename}",
                entry_file=filename,
                mime_type=(
                    "application/vnd.openxmlformats-officedocument"
                    ".wordprocessingml.document"
                ),
            )
            session.add(artifact)
            await session.flush()
            artifact_id = artifact.id
            conv_id = conv.id
            await session.commit()
    finally:
        await test_engine.dispose()

    store = get_objectstore_client()
    key = f"artifacts/{conv_id}/{artifact_id}/v1/{filename}"
    await store.upload_file(key, file_bytes)
    return artifact_id, conv_id


@pytest_asyncio.fixture
async def office_client() -> AsyncIterator[tuple[httpx.AsyncClient, str, str, str]]:
    """Yield (client, workspace_id, artifact_id, conversation_id)."""
    app, email, password, workspace_id = await _make_isolated_user(Role.MEMBER)
    artifact_id, conv_id = await _seed_office_artifact(workspace_id)
    app.state.deployment_mode = "multi_tenant"
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password)
            yield c, workspace_id, artifact_id, conv_id


class TestOTKPreviewToken:
    """Tests for POST preview-token endpoint."""

    async def test_issue_token_returns_urls(
        self, office_client: tuple[httpx.AsyncClient, str, str, str]
    ) -> None:
        client, ws_id, art_id, conv_id = office_client
        url = f"/api/v1/ws/{ws_id}/conversations/{conv_id}/artifacts/{art_id}/preview-token"
        r = await client.post(url)
        assert r.status_code == 200
        body = r.json()
        assert "download_url" in body
        assert "viewer_url" in body
        assert "view.officeapps.live.com" in body["viewer_url"]
        assert body["download_url"] in body["viewer_url"]

    async def test_token_not_supported_for_non_office(
        self, office_client: tuple[httpx.AsyncClient, str, str, str]
    ) -> None:
        """Seed a .txt artifact and verify preview-token rejects it."""
        client, ws_id, _, _ = office_client
        art_id, conv_id = await _seed_office_artifact(
            ws_id, filename="notes.txt", file_bytes=b"hello"
        )
        url = f"/api/v1/ws/{ws_id}/conversations/{conv_id}/artifacts/{art_id}/preview-token"
        r = await client.post(url)
        assert r.status_code == 400


class TestOTKPublicDownload:
    """Tests for GET /public/artifacts/dl/{token}/{filename}."""

    async def test_download_consumes_token(
        self, office_client: tuple[httpx.AsyncClient, str, str, str]
    ) -> None:
        client, ws_id, art_id, conv_id = office_client
        # Issue token
        token_url = (
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}"
            f"/artifacts/{art_id}/preview-token"
        )
        r = await client.post(token_url)
        assert r.status_code == 200
        download_url = r.json()["download_url"]
        # Extract relative path from absolute URL
        path = download_url.replace("http://test", "")

        # First GET — succeeds
        r1 = await client.get(path)
        assert r1.status_code == 200
        assert len(r1.content) > 0

        # Second GET — token consumed, 404
        r2 = await client.get(path)
        assert r2.status_code == 404

    async def test_invalid_token_returns_404(
        self, office_client: tuple[httpx.AsyncClient, str, str, str]
    ) -> None:
        client, *_ = office_client
        r = await client.get("/api/v1/public/artifacts/dl/bogus-token/report.docx")
        assert r.status_code == 404

    async def test_filename_mismatch_returns_404(
        self, office_client: tuple[httpx.AsyncClient, str, str, str]
    ) -> None:
        client, ws_id, art_id, conv_id = office_client
        token_url = (
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}"
            f"/artifacts/{art_id}/preview-token"
        )
        r = await client.post(token_url)
        assert r.status_code == 200
        download_url = r.json()["download_url"]
        path = download_url.replace("http://test", "")
        # Replace filename
        tampered = path.rsplit("/", 1)[0] + "/evil.docx"
        r2 = await client.get(tampered)
        assert r2.status_code == 404
```

- [ ] **Step 2: Run the tests**

```bash
cd /home/chris/cubeplex/.worktrees/feat/office-preview-token/backend && uv run pytest tests/e2e/test_otk_preview.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_otk_preview.py
git commit -m "$(cat <<'EOF'
test(e2e): add OTK preview token tests — issue, consume, one-time, filename mismatch
EOF
)"
```

---

## Task 6: Frontend — add `requestPreviewToken` API function

**Files:**
- Modify: `frontend/packages/core/src/api/conversations.ts`

- [ ] **Step 1: Add the API function**

Add this function at the end of `frontend/packages/core/src/api/conversations.ts`:

```typescript
export interface PreviewTokenResponse {
  download_url: string
  viewer_url: string
}

export async function requestPreviewToken(
  client: ApiClient,
  conversationId: string,
  artifactId: string,
  version?: number,
): Promise<PreviewTokenResponse> {
  const params = version != null ? `?version=${version}` : ''
  const url = `/api/v1/conversations/${conversationId}/artifacts/${artifactId}/preview-token${params}`
  const res = await client.post(url, {})
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<PreviewTokenResponse>
}
```

- [ ] **Step 2: Export from core index**

Check if `requestPreviewToken` is already auto-exported via the barrel in `frontend/packages/core/src/index.ts`. If `conversations.ts` functions are re-exported, no change needed. If not, add the export.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/core/src/api/conversations.ts
git commit -m "$(cat <<'EOF'
feat(core): add requestPreviewToken API function
EOF
)"
```

---

## Task 7: Frontend — add i18n strings for Office preview

**Files:**
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`

- [ ] **Step 1: Add English strings**

In `frontend/packages/web/messages/en.json`, add a new `"office"` object inside `"panel"`, at the same level as the existing `"fallback"` object:

```json
"office": {
  "loading": "Loading document preview…",
  "error": "Online preview is unavailable",
  "errorHint": "The file could not be loaded by the external preview service.",
  "download": "Download file",
  "retry": "Retry"
}
```

- [ ] **Step 2: Add Chinese strings**

In `frontend/packages/web/messages/zh.json`, add the same structure:

```json
"office": {
  "loading": "正在加载文档预览…",
  "error": "在线预览不可用",
  "errorHint": "外部预览服务无法加载此文件。",
  "download": "下载文件",
  "retry": "重试"
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/messages/en.json frontend/packages/web/messages/zh.json
git commit -m "$(cat <<'EOF'
feat(i18n): add Office preview strings (en + zh)
EOF
)"
```

---

## Task 8: Frontend — create `OfficePreview` component

**Files:**
- Create: `frontend/packages/web/components/panel/artifact/OfficePreview.tsx`

- [ ] **Step 1: Create the component**

Create `frontend/packages/web/components/panel/artifact/OfficePreview.tsx`:

```tsx
'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { Download, RefreshCw } from 'lucide-react'
import type { Artifact } from '@cubeplex/core'
import { createApiClient, requestPreviewToken } from '@cubeplex/core'
import { useTranslations } from 'next-intl'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { getArtifactIcon } from './artifactIcons'
import { buildDownloadUrl } from './previewUtils'
import { PreviewLoading } from './PreviewLoading'

interface OfficePreviewProps {
  artifact: Artifact
  version: number | null
  workspaceId: string
}

type ViewerState = 'loading' | 'ready' | 'error'

const LOAD_TIMEOUT_MS = 15_000

export function OfficePreview({ artifact, version, workspaceId }: OfficePreviewProps) {
  const t = useTranslations('panel.office')
  const [viewerUrl, setViewerUrl] = useState<string | null>(null)
  const [state, setState] = useState<ViewerState>('loading')
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const fetchToken = useCallback(async () => {
    setState('loading')
    setViewerUrl(null)
    try {
      const client = createApiClient('')
      if (workspaceId) client.setWorkspaceId(workspaceId)
      const res = await requestPreviewToken(
        client,
        artifact.conversation_id,
        artifact.id,
        version ?? artifact.version,
      )
      setViewerUrl(res.viewer_url)
    } catch {
      setState('error')
    }
  }, [artifact.conversation_id, artifact.id, artifact.version, version, workspaceId])

  useEffect(() => {
    void fetchToken()
  }, [fetchToken])

  useEffect(() => {
    if (!viewerUrl) return
    timerRef.current = setTimeout(() => {
      setState((prev) => (prev === 'loading' ? 'error' : prev))
    }, LOAD_TIMEOUT_MS)
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [viewerUrl])

  const handleLoad = () => {
    if (timerRef.current) clearTimeout(timerRef.current)
    setState('ready')
  }

  const handleError = () => {
    if (timerRef.current) clearTimeout(timerRef.current)
    setState('error')
  }

  if (state === 'error') {
    const Icon = getArtifactIcon(artifact)
    const downloadUrl = buildDownloadUrl(artifact, workspaceId, version)
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 p-8 text-center">
        <div className="flex size-16 items-center justify-center rounded-xl bg-muted">
          <Icon className="size-8 text-muted-foreground" />
        </div>
        <div>
          <h3 className="text-sm font-medium text-foreground">{artifact.name}</h3>
          <p className="mt-2 text-sm text-muted-foreground">{t('error')}</p>
          <p className="mt-1 text-xs text-muted-foreground/60">{t('errorHint')}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void fetchToken()}
            className="inline-flex items-center gap-2 rounded-md border border-border px-4 py-2
              text-sm font-medium text-foreground hover:bg-muted transition-colors"
          >
            <RefreshCw className="size-4" />
            {t('retry')}
          </button>
          <a
            href={downloadUrl}
            className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2
              text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            <Download className="size-4" />
            {t('download')}
          </a>
        </div>
      </div>
    )
  }

  return (
    <div className="relative w-full h-full">
      {state === 'loading' && (
        <div className="absolute inset-0 z-10">
          <PreviewLoading />
        </div>
      )}
      {viewerUrl && (
        <iframe
          ref={iframeRef}
          src={viewerUrl}
          className="w-full h-full border-0"
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
          onLoad={handleLoad}
          onError={handleError}
        />
      )}
    </div>
  )
}
```

- [ ] **Step 2: Verify types**

```bash
cd /home/chris/cubeplex/.worktrees/feat/office-preview-token/frontend && pnpm type-check
```

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/panel/artifact/OfficePreview.tsx
git commit -m "$(cat <<'EOF'
feat(ui): add OfficePreview component with iframe + fallback
EOF
)"
```

---

## Task 9: Frontend — wire `OfficePreview` into `ArtifactPanel`

**Files:**
- Modify: `frontend/packages/web/components/panel/artifact/ArtifactPanel.tsx`

- [ ] **Step 1: Add the import and routing logic**

In `ArtifactPanel.tsx`, add the import for `OfficePreview` alongside the other preview imports (around line 19):

```typescript
import { OfficePreview } from './OfficePreview'
```

Add an Office file detection helper after the existing `isPdf` function (after line 32):

```typescript
const OFFICE_EXTENSIONS = new Set(['.docx', '.xlsx', '.pptx'])

function isOfficeFile(artifact: Artifact): boolean {
  const filename = artifact.entry_file || artifact.path.split('/').pop() || ''
  const ext = filename.slice(filename.lastIndexOf('.')).toLowerCase()
  return OFFICE_EXTENSIONS.has(ext)
}
```

In the `PreviewContent` function, add Office file check **before** the `switch` statement (after the PDF check on line 186, before line 188):

```typescript
  if (isOfficeFile(artifact)) {
    return <OfficePreview artifact={artifact} version={version} workspaceId={workspaceId} />
  }
```

- [ ] **Step 2: Verify types**

```bash
cd /home/chris/cubeplex/.worktrees/feat/office-preview-token/frontend && pnpm type-check
```

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/panel/artifact/ArtifactPanel.tsx
git commit -m "$(cat <<'EOF'
feat(ui): route Office files to OfficePreview in ArtifactPanel
EOF
)"
```

---

## Task 10: Manual verification + full test sweep

- [ ] **Step 1: Run backend lint + type-check**

```bash
cd /home/chris/cubeplex/.worktrees/feat/office-preview-token/backend && make format && make lint && make type-check
```

Fix any issues found.

- [ ] **Step 2: Run backend E2E tests**

```bash
cd /home/chris/cubeplex/.worktrees/feat/office-preview-token/backend && uv run pytest tests/e2e/test_otk_preview.py -v
```

All 5 tests must pass.

- [ ] **Step 3: Run frontend type-check + build**

```bash
cd /home/chris/cubeplex/.worktrees/feat/office-preview-token/frontend && pnpm type-check && pnpm build
```

- [ ] **Step 4: Start dev servers and test in browser**

Start backend and frontend:

```bash
cd /home/chris/cubeplex/.worktrees/feat/office-preview-token/backend && python main.py &
cd /home/chris/cubeplex/.worktrees/feat/office-preview-token/frontend && pnpm dev &
```

Note: this worktree uses port **8037** (backend) and **3037** (frontend). Open `http://localhost:3037` in a browser.

Test the golden path:
1. Create a conversation, ask the agent to generate an Office file
2. Click the artifact card — verify `OfficePreview` shows the loading state
3. Since this is localhost, Office Online Viewer will fail — verify the **fallback UI** appears with the correct copy and a working download button
4. Click "Download file" — verify the file downloads via the authenticated endpoint

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "$(cat <<'EOF'
fix: address lint/type issues from full test sweep
EOF
)"
```
