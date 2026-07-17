# 多图 Artifact 预览与成果库封面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让"一组图片"作为单一 artifact 时可正常预览（轮播画廊）并在成果库卡片用首图当封面 + 数量角标；同时引导 agent 正确产出多图交付物。

**Architecture:** 新增后端 `GET /artifacts/{id}/files?filter=image` 接口（复用 `list_objects`，文件名升序、按扩展名过滤）；前端 `ImagePreview` 用启发式判定单图 vs 多图目录，多图时渲染新 `ImageCarousel` 组件（复用 `ImageViewer`）；成果库卡片复用同一启发式取首图封面 + `×N` 角标；prompt 引导 agent 编号文件名、不设 `entry_file`。

**Tech Stack:** FastAPI（后端路由）、SQLModel/aioboto3 rustfs（object store）、React 19 + Next.js + next-intl（前端）、vitest（前端单测）、Playwright（前端 e2e）、pytest（后端 e2e）。

## Global Constraints

- 后端类型注解全覆盖，mypy strict；行宽 100。
- 时间列 tz-aware；DB→API 用 `utc_isoformat()`。（本计划无新时间列）
- Scope-isolated：新接口走 workspace 路由 `/api/v1/ws/{ws}/conversations/{conv}/artifacts/...`，无 admin 对应。
- 依赖：`uv add`（后端）、`pnpm add`（前端）；本计划不新增依赖。
- 前端用 pnpm；`@cubeplex/core` 改动需 build 后 web 才能看到（本计划不改 core）。
- 文档随代码：本特性不改 user-facing 文档页（artifact 预览是既有行为的多图扩展，无新 route/enum/option 暴露给用户配置）；prompt 文案改动属内部。
- 图片扩展名集合 `IMAGE_EXTENSIONS = {png,jpg,jpeg,gif,webp,svg,bmp}`（小写，无点）。
- 临时脚本放 `backend/scripts/dev/`；spec/plan 已在 `docs/dev/`。

参考 spec：`docs/dev/specs/2026-06-29-multi-image-artifact-preview-design.md`

---

## File Structure

**后端：**
- Modify: `backend/cubeplex/api/routes/v1/artifacts.py` — 新增 `GET /{artifact_id}/files` 路由 + `IMAGE_EXTENSIONS` 常量 + 响应模型。
- Test: `backend/tests/e2e/test_artifact_files.py`（新建）— `/files` 契约 + RBAC + 过滤/排序。

**前端：**
- Modify: `frontend/packages/web/components/panel/artifact/previewUtils.ts` — 加 `IMAGE_EXTENSIONS` 常量 + `hasImageExt(filename)` 纯函数。
- Create: `frontend/packages/web/components/panel/artifact/ImageCarousel.tsx` — 多图轮播，复用 `ImageViewer`。
- Modify: `frontend/packages/web/components/panel/artifact/ImagePreview.tsx` — 启发式判定：单图直接渲染，多图目录拉 `/files` 渲染 `ImageCarousel`。
- Create: `frontend/packages/web/components/panel/artifact/useArtifactCover.ts` — hook：启发式取封面 URL + 数量（供卡片用）。
- Modify: `frontend/packages/web/components/artifacts/ArtifactLibraryCard.tsx` — 用 `useArtifactCover` 取首图封面 + `×N` 角标。
- Test: `frontend/packages/web/__tests__/components/previewUtils.test.ts`（新建，纯函数）。
- Test: `frontend/packages/web/__tests__/e2e/artifact-multi-image.spec.ts`（新建，Playwright）。

**Prompt：**
- Modify: `backend/cubeplex/prompts/artifacts.py` — `image` 条目加多图引导。

---

### Task 1: 后端 — `/files` 接口

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/artifacts.py`
- Test: `backend/tests/e2e/test_artifact_files.py`

**Interfaces:**
- Consumes: `get_objectstore_client().list_objects(prefix)`（已存在，返回 `list[str]`）；`ArtifactRepository.get_by_id(artifact_id)`（已存在）；`_require_conversation(session, ctx, conversation_id)`（已存在于该文件）。
- Produces: `GET /ws/{workspace_id}/conversations/{conversation_id}/artifacts/{artifact_id}/files?version=&filter=` → `{ version: int, files: list[str] }`；模块级常量 `IMAGE_EXTENSIONS: frozenset[str]`。

- [ ] **Step 1: 写失败的 e2e 测试**

创建 `backend/tests/e2e/test_artifact_files.py`。该测试用真实 rustfs object store（conftest 已配 `127.0.0.1:9000` + rustfsadmin），直接调 `get_objectstore_client().upload_file()` 灌数据，再用 `TestClient` 打接口。

```python
"""E2E tests for the artifact file-list endpoint."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.db.engine import _build_database_url
from cubeplex.objectstore import get_objectstore_client
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID

pytestmark = pytest.mark.asyncio

_CONV = "conv-artfiles"
_ART = "art-artfiles"
_PREFIX = f"artifacts/{_CONV}/{_ART}/v1/"


@pytest_asyncio.fixture
async def _seed(client: TestClient) -> AsyncIterator[None]:
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    my_user_id = me.json()["id"]

    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as s:
            await s.execute(
                text(
                    "INSERT INTO conversations (id, org_id, workspace_id,"
                    " creator_user_id, title, has_messages, is_group_chat,"
                    " created_at, updated_at)"
                    " VALUES (:id, :org, :ws, :uid, 'seed', true, false, NOW(), NOW())"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {"id": _CONV, "org": DEFAULT_ORG_ID, "ws": DEFAULT_WS_ID, "uid": my_user_id},
            )
            await s.execute(
                text(
                    "INSERT INTO artifacts (id, org_id, workspace_id, conversation_id,"
                    " name, artifact_type, path, entry_file, mime_type, description,"
                    " version, created_at, updated_at)"
                    " VALUES (:id, :org, :ws, :conv, 'Charts', 'image',"
                    " '/workspace/charts', NULL, NULL, NULL, 1, NOW(), NOW())"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {
                    "id": _ART,
                    "org": DEFAULT_ORG_ID,
                    "ws": DEFAULT_WS_ID,
                    "conv": _CONV,
                },
            )
            await s.commit()

        store = get_objectstore_client()
        # Unsorted on purpose: 2_ before 1_ to prove the endpoint sorts.
        for name, data in (
            ("2_second.png", b"\x89PNG\r\n\x1a\n-second"),
            ("1_first.png", b"\x89PNG\r\n\x1a\n-first"),
            ("3_third.png", b"\x89PNG\r\n\x1a\n-third"),
            ("script.py", b"print(1)"),
        ):
            await store.upload_file(f"{_PREFIX}{name}", data)
        yield
    finally:
        store = get_objectstore_client()
        for name in ("2_second.png", "1_first.png", "3_third.png", "script.py"):
            try:
                await store.delete_object(f"{_PREFIX}{name}")
            except Exception:
                pass
        async with maker() as s:
            await s.execute(text("DELETE FROM artifacts WHERE id = :id"), {"id": _ART})
            await s.execute(text("DELETE FROM conversations WHERE id = :id"), {"id": _CONV})
            await s.commit()
        await engine.dispose()


def test_files_filter_image_sorted(_seed: None, client: TestClient) -> None:
    res = client.get(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{_CONV}/artifacts/{_ART}/files",
        params={"filter": "image"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["version"] == 1
    # Sorted ascending; non-image script.py excluded; prefix stripped.
    assert body["files"] == ["1_first.png", "2_second.png", "3_third.png"]


def test_files_no_filter_returns_all(_seed: None, client: TestClient) -> None:
    res = client.get(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{_CONV}/artifacts/{_ART}/files"
    )
    assert res.status_code == 200, res.text
    names = res.json()["files"]
    assert "script.py" in names
    assert names == sorted(names)


def test_files_missing_artifact_404(client: TestClient) -> None:
    res = client.get(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{_CONV}/artifacts/art-nonexistent/files"
    )
    assert res.status_code == 404


def test_files_cross_workspace_404(_seed: None, client: TestClient) -> None:
    # _seed is owned by DEFAULT_WS_ID; query a foreign workspace id.
    res = client.get(
        f"/api/v1/ws/ws-foreign/conversations/{_CONV}/artifacts/{_ART}/files",
        params={"filter": "image"},
    )
    assert res.status_code == 404
```

注：`delete_object` 若 `ObjectStoreClient` 无此方法，实现 Step 3 时确认；若没有，用 `list_objects(_PREFIX)` + 现有删除手段，或测试 cleanup 改用 `list_objects` 枚举后逐个删（见 Step 3 备注）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/e2e/test_artifact_files.py -v 2>&1 | tee tmp/artifact-files.log | tail -15`
Expected: FAIL — 404（路由不存在）或 405。

- [ ] **Step 3: 实现 `/files` 路由**

先确认 `ObjectStoreClient` 有无 `delete_object`：

Run: `cd backend && grep -n "def delete_object\|def delete" cubeplex/objectstore/client.py`
- 若有 → 上面 cleanup 直接可用。
- 若无 → 把测试 cleanup 改为：`keys = await store.list_objects(_PREFIX); for k in keys: ... ` 仍需一个删除方法。若完全没有删除方法，cleanup 用 `upload_file` 覆盖空字节不可行（list 仍返回）。此时最稳妥：在 `ObjectStoreClient` 加一个 `delete_object(key)` 方法（aioboto3 `delete_object(Bucket, Key)`），并在本任务一并实现 + 提交（它是个合理的通用缺失方法，cleanup 需要）。**先 grep 确认再决定。**

在 `backend/cubeplex/api/routes/v1/artifacts.py` 顶部 import 区下方（`router = APIRouter(...)` 之前）加常量：

```python
IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"})
```

并在 import 区加 `from pydantic import BaseModel`（若尚未导入）。

在 `download_artifact` 路由之后（`OFFICE_EXTENSIONS` 定义之前）加响应模型 + 路由：

```python
class ArtifactFilesOut(BaseModel):
    version: int
    files: list[str]


@router.get("/{artifact_id}/files", response_model=ArtifactFilesOut)
async def list_artifact_files(
    conversation_id: str,
    artifact_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    version: int | None = Query(default=None),
    filter: str | None = Query(default=None),
) -> ArtifactFilesOut:
    """List the files stored for an artifact version.

    ``filter=image`` restricts to image extensions (sorted ascending by
    filename); without ``filter`` all files are returned sorted. Used by the
    preview panel to render a multi-image directory as a carousel.
    """
    await _require_conversation(session, ctx, conversation_id)
    repo = ArtifactRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    artifact = await repo.get_by_id(artifact_id)
    if not artifact or artifact.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )

    target_version = version or artifact.version
    prefix = f"artifacts/{conversation_id}/{artifact_id}/v{target_version}/"

    try:
        store = get_objectstore_client()
        keys = await store.list_objects(prefix)
    except Exception as e:
        logger.error("Error listing artifact files: {}", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list artifact files",
        ) from None

    rel_files = [k[len(prefix):] for k in keys]
    if filter == "image":
        rel_files = [
            f
            for f in rel_files
            if "." in f and f.rsplit(".", 1)[-1].lower() in IMAGE_EXTENSIONS
        ]
    rel_files.sort()

    return ArtifactFilesOut(version=target_version, files=rel_files)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/e2e/test_artifact_files.py -v 2>&1 | tee tmp/artifact-files.log | tail -15`
Expected: 4 passed。

若 `test_files_cross_workspace_404` 不过（返回非 404），检查 `require_member` / `_require_conversation` 在跨 workspace 时的行为——`ArtifactRepository` 用 `(org_id, workspace_id)` scope，跨 ws 的 `get_by_id` 应返回 None → 404。若 conv 先于 artifact 校验且跨 ws 的 conv 也 404，同样满足。

- [ ] **Step 5: mypy + 提交**

Run: `cd backend && uv run mypy cubeplex/api/routes/v1/artifacts.py 2>&1 | tail -5`
Expected: no errors.

```bash
cd backend
git add tests/e2e/test_artifact_files.py cubeplex/api/routes/v1/artifacts.py
# 若加了 delete_object:
# git add cubeplex/objectstore/client.py
git commit -m "feat(artifacts): add GET /files endpoint for multi-image preview"
```

---

### Task 2: 前端 — `previewUtils` 图片扩展名启发式（纯函数）

**Files:**
- Modify: `frontend/packages/web/components/panel/artifact/previewUtils.ts`
- Test: `frontend/packages/web/__tests__/components/previewUtils.test.ts`

**Interfaces:**
- Produces: `IMAGE_EXTENSIONS: ReadonlySet<string>`；`hasImageExt(filename: string): boolean`。

- [ ] **Step 1: 写失败的纯函数测试**

创建 `frontend/packages/web/__tests__/components/previewUtils.test.ts`：

```typescript
import { describe, it, expect } from 'vitest'
import { hasImageExt } from '../../components/panel/artifact/previewUtils'

describe('hasImageExt', () => {
  it('true for image extensions (case-insensitive)', () => {
    expect(hasImageExt('1_镇街贷款金额.png')).toBe(true)
    expect(hasImageExt('chart.JPG')).toBe(true)
    expect(hasImageExt('a.svg')).toBe(true)
    expect(hasImageExt('a.webp')).toBe(true)
  })

  it('false for non-image extensions and extensionless names', () => {
    expect(hasImageExt('charts')).toBe(false)
    expect(hasImageExt('script.py')).toBe(false)
    expect(hasImageExt('data.csv')).toBe(false)
    expect(hasImageExt('')).toBe(false)
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && pnpm vitest run __tests__/components/previewUtils.test.ts 2>&1 | tail -15`
Expected: FAIL — `hasImageExt` 未导出。

- [ ] **Step 3: 实现常量与函数**

在 `frontend/packages/web/components/panel/artifact/previewUtils.ts` 末尾追加：

```typescript
export const IMAGE_EXTENSIONS = new Set([
  'png',
  'jpg',
  'jpeg',
  'gif',
  'webp',
  'svg',
  'bmp',
])

export function hasImageExt(filename: string): boolean {
  const dot = filename.lastIndexOf('.')
  if (dot < 0) return false
  return IMAGE_EXTENSIONS.has(filename.slice(dot + 1).toLowerCase())
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && pnpm vitest run __tests__/components/previewUtils.test.ts 2>&1 | tail -15`
Expected: PASS。

- [ ] **Step 5: lint + 提交**

Run: `cd frontend && pnpm lint 2>&1 | tail -5`
Expected: no errors.

```bash
cd frontend
git add packages/web/components/panel/artifact/previewUtils.ts packages/web/__tests__/components/previewUtils.test.ts
git commit -m "feat(artifact): add hasImageExt helper for image path detection"
```

---

### Task 3: 前端 — `ImageCarousel` 组件

**Files:**
- Create: `frontend/packages/web/components/panel/artifact/ImageCarousel.tsx`

**Interfaces:**
- Consumes: `ImageViewer`（`@/components/shared/previews`，props `{ url, alt }`）；`buildPreviewUrl(artifact, filePath, version, workspaceId)`（Task 既有）；`Artifact`（`@cubeplex/core`）。
- Produces: `ImageCarousel` 组件，props `{ artifact: Artifact; imageFiles: string[]; version: number | null; workspaceId: string }`。

- [ ] **Step 1: 创建组件**

创建 `frontend/packages/web/components/panel/artifact/ImageCarousel.tsx`：

```typescript
'use client'

import { useState, useCallback } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import type { Artifact } from '@cubeplex/core'
import { buildPreviewUrl } from './previewUtils'
import { ImageViewer } from '@/components/shared/previews'

interface ImageCarouselProps {
  artifact: Artifact
  imageFiles: string[]
  version: number | null
  workspaceId: string
}

export function ImageCarousel({
  artifact,
  imageFiles,
  version,
  workspaceId,
}: ImageCarouselProps): React.ReactElement {
  const [index, setIndex] = useState(0)
  const count = imageFiles.length

  const go = useCallback(
    (delta: number) => {
      setIndex((i) => Math.min(count - 1, Math.max(0, i + delta)))
    },
    [count],
  )

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'ArrowLeft') {
        e.preventDefault()
        go(-1)
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        go(1)
      }
    },
    [go],
  )

  const url = buildPreviewUrl(artifact, imageFiles[index], version, workspaceId)

  return (
    <div className="flex h-full flex-col" tabIndex={0} onKeyDown={onKeyDown}>
      <div className="relative flex-1 overflow-hidden">
        <ImageViewer url={url} alt={`${artifact.name} ${index + 1}/${count}`} />
        {count > 1 && (
          <>
            <button
              onClick={() => go(-1)}
              disabled={index === 0}
              aria-label="Previous image"
              className="absolute left-2 top-1/2 -translate-y-1/2 rounded-full bg-background/70
                p-1.5 text-foreground backdrop-blur-sm transition-colors hover:bg-background
                disabled:opacity-30"
            >
              <ChevronLeft className="size-5" />
            </button>
            <button
              onClick={() => go(1)}
              disabled={index === count - 1}
              aria-label="Next image"
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full bg-background/70
                p-1.5 text-foreground backdrop-blur-sm transition-colors hover:bg-background
                disabled:opacity-30"
            >
              <ChevronRight className="size-5" />
            </button>
            <div className="absolute bottom-2 left-1/2 -translate-x-1/2 rounded-full
              bg-background/70 px-2 py-0.5 text-xs tabular-nums text-muted-foreground
              backdrop-blur-sm">
              {index + 1} / {count}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: 类型检查 + lint**

Run: `cd frontend && pnpm tsc --noEmit 2>&1 | tail -10 && pnpm lint 2>&1 | tail -5`
Expected: no errors.

- [ ] **Step 3: 提交**

```bash
cd frontend
git add packages/web/components/panel/artifact/ImageCarousel.tsx
git commit -m "feat(artifact): add ImageCarousel for multi-image preview"
```

---

### Task 4: 前端 — `ImagePreview` 启发式集成

**Files:**
- Modify: `frontend/packages/web/components/panel/artifact/ImagePreview.tsx`

**Interfaces:**
- Consumes: `hasImageExt`（Task 2）；`ImageCarousel`（Task 3）；`buildPreviewUrl`（既有）；`FallbackPreview`（`./FallbackPreview`，既有）；`PreviewLoading`（`./PreviewLoading`，既有）。
- Produces: `ImagePreview` 仍导出同名组件，行为升级为启发式。

- [ ] **Step 1: 重写 `ImagePreview.tsx`**

完整替换 `frontend/packages/web/components/panel/artifact/ImagePreview.tsx`：

```typescript
'use client'

import { useState, useEffect } from 'react'
import type { Artifact } from '@cubeplex/core'
import { buildPreviewUrl, hasImageExt } from './previewUtils'
import { ImageViewer } from '@/components/shared/previews'
import { PreviewLoading } from './PreviewLoading'
import { FallbackPreview } from './FallbackPreview'
import { ImageCarousel } from './ImageCarousel'

interface ImagePreviewProps {
  artifact: Artifact
  version: number | null
  workspaceId: string
}

interface FilesResponse {
  version: number
  files: string[]
}

export function ImagePreview({
  artifact,
  version,
  workspaceId,
}: ImagePreviewProps): React.ReactElement {
  const filename = artifact.entry_file || artifact.path.split('/').pop() || ''

  // Heuristic: a path that already points at an image file → single image,
  // no list call. Otherwise (directory like /workspace/charts) fetch the
  // file list and render a carousel.
  if (hasImageExt(filename)) {
    const url = buildPreviewUrl(artifact, filename, version, workspaceId)
    return <ImageViewer url={url} alt={artifact.name} />
  }

  const v = version ?? artifact.version
  const [files, setFiles] = useState<string[] | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    let cancelled = false
    const url =
      `/api/v1/ws/${workspaceId}/conversations/${artifact.conversation_id}` +
      `/artifacts/${artifact.id}/files?filter=image&version=${v}`
    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status}`)
        return res.json() as Promise<FilesResponse>
      })
      .then((body) => {
        if (!cancelled) setFiles(body.files)
      })
      .catch(() => {
        if (!cancelled) setError(true)
      })
    return () => {
      cancelled = true
    }
  }, [artifact.id, artifact.conversation_id, v, workspaceId])

  if (error) {
    return <FallbackPreview artifact={artifact} version={version} workspaceId={workspaceId} />
  }
  if (files === null) {
    return <PreviewLoading />
  }
  if (files.length === 0) {
    return <FallbackPreview artifact={artifact} version={version} workspaceId={workspaceId} />
  }
  if (files.length === 1) {
    const url = buildPreviewUrl(artifact, files[0], version, workspaceId)
    return <ImageViewer url={url} alt={artifact.name} />
  }
  return (
    <ImageCarousel
      artifact={artifact}
      imageFiles={files}
      version={version}
      workspaceId={workspaceId}
    />
  )
}
```

- [ ] **Step 2: 确认 `FallbackPreview` props 形态**

Run: `cd frontend && grep -n "export function FallbackPreview\|interface FallbackPreviewProps" packages/web/components/panel/artifact/FallbackPreview.tsx`
Expected: 确认 props 为 `{ artifact, version, workspaceId }`（与其它 preview 一致）。若不一致，调整上面调用。

- [ ] **Step 3: 类型检查 + lint**

Run: `cd frontend && pnpm tsc --noEmit 2>&1 | tail -10 && pnpm lint 2>&1 | tail -5`
Expected: no errors.

- [ ] **Step 4: 提交**

```bash
cd frontend
git add packages/web/components/panel/artifact/ImagePreview.tsx
git commit -m "feat(artifact): ImagePreview renders carousel for multi-image dirs"
```

---

### Task 5: 前端 — `useArtifactCover` hook + 成果库卡片封面

**Files:**
- Create: `frontend/packages/web/components/panel/artifact/useArtifactCover.ts`
- Modify: `frontend/packages/web/components/artifacts/ArtifactLibraryCard.tsx`

**Interfaces:**
- Consumes: `hasImageExt`、`buildPreviewUrl`（Task 2 既有）；`Artifact`（`@cubeplex/core`）。
- Produces: `useArtifactCover(artifact, workspaceId)` → `{ coverUrl: string | null; count: number; loading: boolean }`。

- [ ] **Step 1: 创建 hook**

创建 `frontend/packages/web/components/panel/artifact/useArtifactCover.ts`：

```typescript
'use client'

import { useState, useEffect } from 'react'
import type { Artifact } from '@cubeplex/core'
import { buildPreviewUrl, hasImageExt } from './previewUtils'

interface FilesResponse {
  version: number
  files: string[]
}

interface CoverState {
  coverUrl: string | null
  count: number
  loading: boolean
}

export function useArtifactCover(
  artifact: Artifact,
  workspaceId: string,
): CoverState {
  const filename = artifact.entry_file || artifact.path.split('/').pop() || ''

  // Single image path → cover is that file, no list call, count 1.
  if (hasImageExt(filename)) {
    return {
      coverUrl: buildPreviewUrl(artifact, filename, null, workspaceId),
      count: 1,
      loading: false,
    }
  }

  // Directory path → fetch first image as cover.
  const [state, setState] = useState<CoverState>({
    coverUrl: null,
    count: 0,
    loading: true,
  })

  useEffect(() => {
    let cancelled = false
    const url =
      `/api/v1/ws/${workspaceId}/conversations/${artifact.conversation_id}` +
      `/artifacts/${artifact.id}/files?filter=image`
    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status}`)
        return res.json() as Promise<FilesResponse>
      })
      .then((body) => {
        if (cancelled) return
        const first = body.files[0]
        setState({
          coverUrl: first
            ? buildPreviewUrl(artifact, first, null, workspaceId)
            : null,
          count: body.files.length,
          loading: false,
        })
      })
      .catch(() => {
        if (!cancelled) setState({ coverUrl: null, count: 0, loading: false })
      })
    return () => {
      cancelled = true
    }
  }, [artifact.id, artifact.conversation_id, workspaceId])

  return state
}
```

- [ ] **Step 2: 改造 `ArtifactLibraryCard.tsx`**

在 `frontend/packages/web/components/artifacts/ArtifactLibraryCard.tsx`：

1) 顶部 import 加：
```typescript
import { useArtifactCover } from '@/components/panel/artifact/useArtifactCover'
```

2) 删除旧的 `thumbFailed`/`thumbFile`/`thumbUrl` 逻辑（第 45–52 行附近），替换为 hook + 派生：

把组件内（`const Icon = ...` 之后、`return` 之前）改为：
```typescript
  const cover = useArtifactCover(artifact, workspaceId)
  const [thumbFailed, setThumbFailed] = useState(false)
  const showImage = isImageArtifact(artifact) && !thumbFailed && cover.coverUrl !== null
  const showHtml = !showImage && isHtmlArtifact(artifact)
  const fallbackFile = isHtmlArtifact(artifact) ? 'index.html' : ''
  const thumbUrl =
    cover.coverUrl ??
    buildPreviewUrl(artifact, artifact.entry_file || fallbackFile, null, workspaceId)
```

注意：HTML artifact 仍走 `thumbFile = entry_file || 'index.html'` 的既有逻辑（不被 cover hook 影响，因为 `isHtmlArtifact` 时 `hasImageExt` 多半为 false 但 HTML 缩略图用 `ArtifactHtmlThumb`）。为避免回归，HTML 分支保持原 `thumbUrl` 计算。更稳妥的写法：

```typescript
  const cover = useArtifactCover(artifact, workspaceId)
  const [thumbFailed, setThumbFailed] = useState(false)
  const isHtml = isHtmlArtifact(artifact)
  const showImage = isImageArtifact(artifact) && !thumbFailed && cover.coverUrl !== null
  const showHtml = !showImage && isHtml
  const fallbackFile = isHtml ? 'index.html' : ''
  // HTML keeps its own thumb URL; image uses the cover hook result.
  const thumbUrl = isHtml
    ? buildPreviewUrl(artifact, artifact.entry_file || fallbackFile, null, workspaceId)
    : cover.coverUrl ?? buildPreviewUrl(artifact, '', null, workspaceId)
```

3) 在缩略图区域（`<img ... onError={() => setThumbFailed(true)} />`）下方、`DropdownMenu` 之前，加数量角标。在 `<div className="relative aspect-video ...">` 内最末尾（`</div>` 关闭前）追加：

```tsx
        {cover.count > 1 && !thumbFailed && (
          <span
            className="absolute bottom-2 right-2 rounded-full bg-background/80 px-1.5
              py-0.5 text-[10px] font-medium text-muted-foreground backdrop-blur-sm"
            data-testid="artifact-card-count"
          >
            ×{cover.count}
          </span>
        )}
```

4) `loading` 时（`cover.loading && showImage`）可显示 `PreviewLoading` 或保持 `bg-muted/40` 占位（当前 img 未渲染时容器本身就是灰底）。为简单起见，`cover.loading` 期间 `showImage` 为 false（因 `cover.coverUrl === null`）→ 自动落到图标兜底，加载完成后再切到图片。无需额外 loading UI。

- [ ] **Step 3: 类型检查 + lint**

Run: `cd frontend && pnpm tsc --noEmit 2>&1 | tail -10 && pnpm lint 2>&1 | tail -5`
Expected: no errors.

- [ ] **Step 4: 提交**

```bash
cd frontend
git add packages/web/components/panel/artifact/useArtifactCover.ts packages/web/components/artifacts/ArtifactLibraryCard.tsx
git commit -m "feat(artifacts): library card shows first-image cover + count badge for multi-image dirs"
```

---

### Task 6: Prompt 引导

**Files:**
- Modify: `backend/cubeplex/prompts/artifacts.py`

- [ ] **Step 1: 改 `image` 条目**

在 `backend/cubeplex/prompts/artifacts.py` 的 `ARTIFACT_PROMPT` 中，把：

```
- "image" — PNG, SVG, JPG images (e.g. matplotlib output)
```

改为：

```
- "image" — PNG, SVG, JPG images (e.g. matplotlib output). Point `path` at a single \
image file. If you produce multiple images as one deliverable, save them in a directory, \
number the filenames (`1_*.png`, `2_*.png`, …) so they preview in order, and leave \
`entry_file` unset — the preview renders them as a navigable gallery.
```

- [ ] **Step 2: 确认既有 prompt 测试不破**

Run: `cd backend && grep -rln "ARTIFACT_PROMPT\|artifacts.py" tests/ | head`
若有断言 prompt 内容的测试，更新之；否则跳过。跑相关单测：
Run: `cd backend && uv run pytest tests/unit/test_builtin_tools.py -v 2>&1 | tail -10`
Expected: PASS（或无相关测试）。

- [ ] **Step 3: 提交**

```bash
cd backend
git add cubeplex/prompts/artifacts.py
git commit -m "feat(artifact): guide agents on multi-image artifact authoring"
```

---

### Task 7: 前端 e2e — 多图预览 + 卡片封面

**Files:**
- Test: `frontend/packages/web/__tests__/e2e/artifact-multi-image.spec.ts`

**Interfaces:**
- Consumes: 既有 Playwright fixtures + 真实后端。需一个已存在的多图 image artifact。最稳妥：用 e2e 里既有的"发送消息让 agent 产图"流程不现实（依赖 LLM），改为**直接灌库 + 灌 object store** 的 setup（参考后端 e2e 的 seeding 思路），但前端 e2e 无法直接访问 DB/store。

**策略：** 前端 e2e 不易直接造多图 artifact。改用**前端组件交互测试（vitest + @testing-library）**覆盖画廊导航状态机（这是后端观测不到的客户端状态机，符合前端 e2e 的职责），用 mock 的 `/files` 响应驱动。Playwright 真实流程留给手动验收。

- [ ] **Step 1: 写 `ImageCarousel` 交互测试**

创建 `frontend/packages/web/__tests__/components/ImageCarousel.test.tsx`：

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import type { Artifact } from '@cubeplex/core'

const mockUrl = (file: string) => `/preview/${file}`
vi.mock('../../../components/panel/artifact/previewUtils', () => ({
  buildPreviewUrl: (_a: unknown, file: string) => mockUrl(file as string),
}))

// Stub ImageViewer to surface the url it received.
vi.mock('../../../components/shared/previews', () => ({
  ImageViewer: ({ url }: { url: string }) => (
    <img data-testid="carousel-img" src={url} alt="" />
  ),
}))

import { ImageCarousel } from '../../../components/panel/artifact/ImageCarousel'

const artifact = {
  id: 'art-1',
  conversation_id: 'conv-1',
  name: 'Charts',
  artifact_type: 'image' as const,
  path: '/workspace/charts',
  entry_file: null,
  mime_type: null,
  description: null,
  created_at: '2026-06-29T00:00:00Z',
  updated_at: '2026-06-29T00:00:00Z',
  version: 1,
} as unknown as Artifact

describe('ImageCarousel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows first image and counter 1/3, navigates with next/prev', () => {
    render(
      <ImageCarousel
        artifact={artifact}
        imageFiles={['1_a.png', '2_b.png', '3_c.png']}
        version={1}
        workspaceId="ws"
      />,
    )
    expect(screen.getByTestId('carousel-img')).toHaveAttribute('src', '/preview/1_a.png')
    expect(screen.getByText('1 / 3')).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('Next image'))
    expect(screen.getByTestId('carousel-img')).toHaveAttribute('src', '/preview/2_b.png')
    expect(screen.getByText('2 / 3')).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('Previous image'))
    expect(screen.getByTestId('carousel-img')).toHaveAttribute('src', '/preview/1_a.png')
  })

  it('disables prev at start and next at end', () => {
    render(
      <ImageCarousel
        artifact={artifact}
        imageFiles={['1_a.png', '2_b.png']}
        version={1}
        workspaceId="ws"
      />,
    )
    expect(screen.getByLabelText('Previous image')).toBeDisabled()
    fireEvent.click(screen.getByLabelText('Next image'))
    expect(screen.getByLabelText('Next image')).toBeDisabled()
  })

  it('hides nav chrome when single image', () => {
    render(
      <ImageCarousel
        artifact={artifact}
        imageFiles={['only.png']}
        version={1}
        workspaceId="ws"
      />,
    )
    expect(screen.getByTestId('carousel-img')).toHaveAttribute('src', '/preview/only.png')
    expect(screen.queryByLabelText('Next image')).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: 跑测试确认通过**

Run: `cd frontend && pnpm vitest run __tests__/components/ImageCarousel.test.tsx 2>&1 | tail -20`
Expected: 3 passed。

若 mock 路径不对（`previewUtils` 相对路径），用 `vi.mock('@/components/panel/artifact/previewUtils', ...)` 配合 vitest 的 `@` alias；先确认 `vitest.config.ts` 有 `@` alias：`grep -n "alias\|@" packages/web/vitest.config.ts`。

- [ ] **Step 3: 跑前端全套单测 + lint 兜底**

Run: `cd frontend && pnpm vitest run 2>&1 | tail -15 && pnpm lint 2>&1 | tail -5`
Expected: all pass。

- [ ] **Step 4: 提交**

```bash
cd frontend
git add packages/web/__tests__/components/ImageCarousel.test.tsx
git commit -m "test(artifact): cover ImageCarousel navigation state machine"
```

---

### Task 8: 收尾 — 后端全测 + 验收

- [ ] **Step 1: 后端 e2e + mypy 全扫**

Run: `cd backend && uv run pytest tests/e2e/test_artifact_files.py tests/e2e/test_ws_artifacts.py -v 2>&1 | tee tmp/artifact-sweep.log | tail -15`
Expected: all pass.

Run: `cd backend && uv run mypy cubeplex/api/routes/v1/artifacts.py cubeplex/prompts/artifacts.py 2>&1 | tail -5`
Expected: no errors.

- [ ] **Step 2: 手动验收清单**

启动后端 + 前端，对一个多图目录 artifact 验证：
1. 成果库卡片显示首图缩略图 + `×6` 角标。
2. 点开预览面板：显示轮播，`1/6` 计数器，左右箭头 + 键盘 ←/→ 翻页。
3. 单图 artifact 仍正常显示（无角标、无轮播 chrome）。
4. 非图片目录 artifact 仍走 `FallbackPreview`。
5. 下载多图 artifact 仍返回 tar（既有行为不回归）。

- [ ] **Step 3: 推送 + PR review 循环**

```bash
cd /home/chris/cubeplex
git push -u origin <branch>
```
按 `pr-codex-review-loop` skill 走 push → poll → fix → reply 循环。
