# Artifacts Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a workspace-level "Artifacts" page (`/w/[wsId]/artifacts`) that lists the current user's accessible artifacts across conversations as a flat, filterable/searchable grid, with click-to-preview reusing the existing side `ArtifactPanel`, plus download / jump-to-source / delete actions.

**Architecture:** New workspace-scoped backend handler `ws_artifacts.py` (separate from the conversation-scoped handler; reuse only at the repository layer) exposing `GET /ws/{wsId}/artifacts` (visibility-filtered, type/name filters, paginated) and `DELETE /ws/{wsId}/artifacts/{id}` (DB rows + object-store cleanup). The frontend page mounts its own `ResizablePanelGroup` (grid + `ArtifactPanel`) rather than the chat `AppShell`, seeds the global `artifactStore` so the existing panel works, and assembles new modules (`ArtifactsToolbar`, `ArtifactLibraryCard`, `ArtifactsEmptyState`).

**Tech Stack:** FastAPI + SQLModel/SQLAlchemy + Postgres + S3-compatible object store (backend); Next.js 15 / React 19 + Zustand + `@cubebox/core` + next-intl + shadcn/ui (frontend); pytest (backend tests), Playwright (frontend E2E).

**Spec:** `docs/dev/specs/2026-06-20-artifacts-page-design.md`

> **Execution note (2026-06-20):** Task 11 (frontend Playwright E2E) was **dropped**.
> Investigation confirmed an `artifacts` row can only be created via a live agent
> run (`save_artifact` / `generate_image` are sandbox-backed LLM tools; there is no
> HTTP create endpoint or test seeder reachable from Playwright). With no seeded
> artifact, the only non-real-LLM frontend test would be an empty-state / nav
> presence check — which CLAUDE.md "Testing Principles" explicitly forbids as a
> standalone test, and which the existing `chat-skill-artifact-preview.spec.ts`
> precedent already pushes down to backend E2E. The list / delete / visibility
> invariants are owned by `backend/tests/e2e/test_ws_artifacts.py` (Task 3).

**Worktree note:** Work in this worktree. First run `cat .worktree.env` — backend is `127.0.0.1:8050`, frontend `:3050`, slot 50. Use `uv run` for backend, `pnpm` for frontend. Do not use ports 8000/3000.

**"Accessible artifact" definition:** an artifact whose `conversation_id` resolves through `ConversationRepository`'s scoped visibility (creator + topic/conversation participation). This reuses the existing conversation-access boundary; we do not add a creator-only filter.

---

## File Structure

**Backend (create):**
- _none_ — only modifications + one new route file.

**Backend (create route):**
- `backend/cubebox/api/routes/v1/ws_artifacts.py` — workspace-scoped list + delete handler.

**Backend (modify):**
- `backend/cubebox/repositories/conversation.py` — add `accessible_id_subquery()`.
- `backend/cubebox/repositories/artifact.py` — add `list_by_workspace()` + `delete_with_versions()`.
- `backend/cubebox/api/routes/v1/__init__.py` — export `ws_artifacts_router`.
- `backend/cubebox/api/app.py` — include `ws_artifacts_router`.

**Backend (tests):**
- `backend/tests/e2e/test_ws_artifacts.py` — list filtering + visibility isolation + delete cleanup + cross-user 404.

**Frontend core (create):**
- `frontend/packages/core/src/api/artifacts.ts` — `listWorkspaceArtifacts` + `deleteArtifact`.

**Frontend core (modify):**
- `frontend/packages/core/src/api/index.ts` — export `./artifacts`.

**Frontend web (create):**
- `frontend/packages/web/app/(app)/w/[wsId]/artifacts/page.tsx` — the page.
- `frontend/packages/web/components/artifacts/ArtifactsToolbar.tsx`
- `frontend/packages/web/components/artifacts/ArtifactLibraryCard.tsx`
- `frontend/packages/web/components/artifacts/ArtifactsEmptyState.tsx`

**Frontend web (modify):**
- `frontend/packages/web/components/layout/Sidebar.tsx` — add "Artifacts" nav entry.
- `frontend/packages/web/messages/en.json` + `messages/zh.json` — i18n keys.

**Frontend web (tests):**
- `frontend/packages/web/__tests__/e2e/artifacts/artifacts-page.spec.ts`

---

## Task 1: Repository — accessible-conversation subquery

**Files:**
- Modify: `backend/cubebox/repositories/conversation.py`

- [ ] **Step 1: Add `accessible_id_subquery()` to `ConversationRepository`**

Add this method to the `ConversationRepository` class (place it right after `_scoped_select`). It reuses the existing visibility WHERE clause and projects only the `id` column so it can be used inside an `IN (...)` filter:

```python
    def accessible_id_subquery(self) -> Any:
        """Subquery of conversation IDs the caller may access.

        Reuses the visibility WHERE from ``_scoped_select`` (creator +
        topic/conversation participation) and projects only ``id`` so it can
        feed an ``Artifact.conversation_id.in_(...)`` filter.
        """
        return self._scoped_select().with_only_columns(cast(Any, Conversation.id))
```

`cast` and `Any` are already imported in this file (used throughout `_scoped_select`). No new imports.

- [ ] **Step 2: Verify it type-checks**

Run: `cd backend && uv run mypy cubebox/repositories/conversation.py`
Expected: `Success: no issues found`.

- [ ] **Step 3: Commit**

```bash
git add backend/cubebox/repositories/conversation.py
git commit -m "feat(artifacts): add accessible_id_subquery to ConversationRepository"
```

---

## Task 2: Repository — workspace artifact list + delete

**Files:**
- Modify: `backend/cubebox/repositories/artifact.py`

- [ ] **Step 1: Add imports**

At the top of `backend/cubebox/repositories/artifact.py`, the current imports are:

```python
"""Artifact repository."""

from datetime import UTC, datetime

from cubebox.models import Artifact
from cubebox.models.artifact_version import ArtifactVersion
from cubebox.repositories.base import ScopedRepository
```

Replace with:

```python
"""Artifact repository."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import cast, delete, func, select

from cubebox.models import Artifact
from cubebox.models.artifact_version import ArtifactVersion
from cubebox.repositories.base import ScopedRepository
```

- [ ] **Step 2: Add `list_by_workspace` to `ArtifactRepository`**

Add this method to the `ArtifactRepository` class (after `list_by_conversation`):

```python
    async def list_by_workspace(
        self,
        *,
        accessible_conv_subq: Any,
        artifact_type: str | None = None,
        name_query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Artifact], int]:
        """List artifacts in the workspace restricted to accessible conversations.

        ``accessible_conv_subq`` is a single-column subquery of conversation
        IDs the caller may access (see
        ``ConversationRepository.accessible_id_subquery``). Optional filters:
        ``artifact_type`` (exact) and ``name_query`` (case-insensitive
        substring). Ordered newest-updated first. Returns ``(items, total)``.
        """
        stmt = self._scoped_select().where(
            cast(Any, Artifact.conversation_id).in_(accessible_conv_subq)
        )
        if artifact_type:
            stmt = stmt.where(Artifact.artifact_type == artifact_type)
        if name_query:
            stmt = stmt.where(cast(Any, Artifact.name).ilike(f"%{name_query}%"))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.session.execute(count_stmt)).scalar_one()

        page_stmt = (
            stmt.order_by(cast(Any, Artifact.updated_at).desc()).limit(limit).offset(offset)
        )
        result = await self.session.execute(page_stmt)
        return list(result.scalars().all()), total
```

- [ ] **Step 3: Add `delete_with_versions` to `ArtifactRepository`**

Add this method to the `ArtifactRepository` class (after `list_by_workspace`). It deletes the version rows and the artifact row within the workspace scope; object-store cleanup is done by the route (which already holds `conversation_id`):

```python
    async def delete_with_versions(self, artifact_id: str) -> bool:
        """Delete an artifact and its version rows. Returns False if not found."""
        artifact = await self.get(artifact_id)
        if artifact is None:
            return False
        await self.session.execute(
            delete(ArtifactVersion).where(
                ArtifactVersion.artifact_id == artifact_id,
                ArtifactVersion.org_id == self.org_id,
                ArtifactVersion.workspace_id == self.workspace_id,
            )
        )
        await self.session.delete(artifact)
        await self.session.commit()
        return True
```

- [ ] **Step 4: Verify it type-checks**

Run: `cd backend && uv run mypy cubebox/repositories/artifact.py`
Expected: `Success: no issues found`.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/repositories/artifact.py
git commit -m "feat(artifacts): add list_by_workspace and delete_with_versions"
```

---

## Task 3: Backend route — `ws_artifacts.py` (list + delete)

**Files:**
- Create: `backend/cubebox/api/routes/v1/ws_artifacts.py`
- Modify: `backend/cubebox/api/routes/v1/__init__.py`
- Modify: `backend/cubebox/api/app.py`
- Test: `backend/tests/e2e/test_ws_artifacts.py`

- [ ] **Step 1: Write the failing E2E test**

Create `backend/tests/e2e/test_ws_artifacts.py`. This seeds two conversations under the default test workspace owned by two different users, with one artifact each, and asserts the list endpoint returns only the caller's accessible artifact, that filters work, and that delete removes the row and is scoped. Mirror the seeding style of `tests/e2e/test_artifact_share_token.py` and the auth fixtures in `tests/e2e/conftest.py`.

```python
"""Integration tests for workspace-level artifacts list + delete."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.e2e.conftest import (
    DEFAULT_ORG_ID,
    DEFAULT_WS_ID,
    _build_database_url,
    authed_client,
)

pytestmark = pytest.mark.asyncio

# The authed_client fixture logs in as the default user; resolve that user's id
# from the DB so we can attribute one conversation to them and one to a stranger.
_STRANGER_ID = "usr-wsart-stranger"
_MY_CONV = "conv-wsart-mine"
_OTHER_CONV = "conv-wsart-other"
_MY_ART = "art-wsart-mine"
_OTHER_ART = "art-wsart-other"


@pytest_asyncio.fixture
async def _seed(authed_user_id: str) -> AsyncIterator[None]:
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as s:
            await s.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password, is_active,"
                    " is_superuser, is_verified, created_at, language)"
                    " VALUES (:id, :email, 'x', true, false, false, NOW(), 'en')"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {"id": _STRANGER_ID, "email": f"{_STRANGER_ID}@example.com"},
            )
            for conv_id, uid in ((_MY_CONV, authed_user_id), (_OTHER_CONV, _STRANGER_ID)):
                await s.execute(
                    text(
                        "INSERT INTO conversations (id, org_id, workspace_id,"
                        " creator_user_id, title, has_messages, created_at, updated_at)"
                        " VALUES (:id, :org, :ws, :uid, 'seed', true, NOW(), NOW())"
                        " ON CONFLICT (id) DO NOTHING"
                    ),
                    {"id": conv_id, "org": DEFAULT_ORG_ID, "ws": DEFAULT_WS_ID, "uid": uid},
                )
            for art_id, conv_id, atype, name in (
                (_MY_ART, _MY_CONV, "html", "My Report"),
                (_OTHER_ART, _OTHER_CONV, "code", "Stranger Script"),
            ):
                await s.execute(
                    text(
                        "INSERT INTO artifacts (id, org_id, workspace_id, conversation_id,"
                        " name, artifact_type, path, entry_file, mime_type, description,"
                        " version, created_at, updated_at)"
                        " VALUES (:id, :org, :ws, :conv, :name, :atype, '/x/f', 'f',"
                        " 'text/plain', NULL, 1, NOW(), NOW())"
                        " ON CONFLICT (id) DO NOTHING"
                    ),
                    {
                        "id": art_id,
                        "org": DEFAULT_ORG_ID,
                        "ws": DEFAULT_WS_ID,
                        "conv": conv_id,
                        "name": name,
                        "atype": atype,
                    },
                )
            await s.commit()
        yield
    finally:
        async with maker() as s:
            await s.execute(text("DELETE FROM artifacts WHERE id IN (:a, :b)"),
                            {"a": _MY_ART, "b": _OTHER_ART})
            await s.execute(text("DELETE FROM conversations WHERE id IN (:a, :b)"),
                            {"a": _MY_CONV, "b": _OTHER_CONV})
            await s.commit()
        await engine.dispose()


def test_list_returns_only_accessible(_seed: None, authed_client: TestClient) -> None:
    res = authed_client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/artifacts")
    assert res.status_code == 200
    ids = {a["id"] for a in res.json()["artifacts"]}
    assert _MY_ART in ids
    assert _OTHER_ART not in ids


def test_list_type_filter(_seed: None, authed_client: TestClient) -> None:
    res = authed_client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/artifacts?type=html")
    assert res.status_code == 200
    assert all(a["artifact_type"] == "html" for a in res.json()["artifacts"])


def test_list_name_search(_seed: None, authed_client: TestClient) -> None:
    res = authed_client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/artifacts?q=report")
    assert res.status_code == 200
    ids = {a["id"] for a in res.json()["artifacts"]}
    assert _MY_ART in ids


def test_delete_accessible_artifact(_seed: None, authed_client: TestClient) -> None:
    res = authed_client.delete(f"/api/v1/ws/{DEFAULT_WS_ID}/artifacts/{_MY_ART}")
    assert res.status_code == 204
    after = authed_client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/artifacts")
    assert _MY_ART not in {a["id"] for a in after.json()["artifacts"]}


def test_delete_inaccessible_artifact_404(_seed: None, authed_client: TestClient) -> None:
    res = authed_client.delete(f"/api/v1/ws/{DEFAULT_WS_ID}/artifacts/{_OTHER_ART}")
    assert res.status_code == 404
```

> NOTE: This test references two conftest helpers that may need to be confirmed/added: `authed_client` (a `TestClient` already logged-in as the default user) and `authed_user_id` (that user's id), plus `DEFAULT_ORG_ID` / `DEFAULT_WS_ID`. Before writing the route, open `backend/tests/e2e/conftest.py` and `tests/e2e/test_conversations.py` to confirm the exact fixture names used for an authenticated client and the logged-in user id; adapt the fixture names in this test to match what already exists (e.g. the existing client fixture name). Do NOT invent a new auth flow — reuse the established one.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/e2e/test_ws_artifacts.py -v`
Expected: FAIL — 404 on `/api/v1/ws/{ws}/artifacts` (route not registered yet).

- [ ] **Step 3: Create the route file**

Create `backend/cubebox/api/routes/v1/ws_artifacts.py`:

```python
"""Workspace-level artifacts API routes (list + delete).

Scope-isolated from the conversation-scoped handler in ``artifacts.py``:
this serves the workspace "artifact library" audience. Reuse lives in the
repository layer, not here.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.db import get_session
from cubebox.objectstore import get_objectstore_client
from cubebox.repositories import ArtifactRepository, ConversationRepository

router = APIRouter(prefix="/ws/{workspace_id}/artifacts", tags=["ws-artifacts"])


@router.get("")
async def list_workspace_artifacts(
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    type: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    """List the caller's accessible artifacts in the workspace."""
    conv_repo = ConversationRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id, user_id=ctx.user.id
    )
    art_repo = ArtifactRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    artifacts, total = await art_repo.list_by_workspace(
        accessible_conv_subq=conv_repo.accessible_id_subquery(),
        artifact_type=type,
        name_query=q,
        limit=limit,
        offset=offset,
    )
    return {"artifacts": [a.to_dict() for a in artifacts], "total": total}


@router.delete("/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace_artifact(
    artifact_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> Response:
    """Delete an artifact (DB rows + object-store files) if the caller may access it."""
    art_repo = ArtifactRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    artifact = await art_repo.get_by_id(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

    conv_repo = ConversationRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id, user_id=ctx.user.id
    )
    if (await conv_repo.get_by_id(artifact.conversation_id)) is None:
        # Artifact exists in the workspace but its conversation is not visible
        # to the caller — treat as not found (don't leak existence).
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

    conversation_id = artifact.conversation_id
    await art_repo.delete_with_versions(artifact_id)

    prefix = f"artifacts/{conversation_id}/{artifact_id}/"
    try:
        store = get_objectstore_client()
        for key in await store.list_objects(prefix):
            await store.delete_file(key)
    except Exception as e:  # storage cleanup is best-effort; rows are already gone
        logger.error("Artifact {} storage cleanup failed: {}", artifact_id, e)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 4: Register the router export**

In `backend/cubebox/api/routes/v1/__init__.py`, add the import next to the other `from cubebox.api.routes.v1.X import router as Y` lines:

```python
from cubebox.api.routes.v1.ws_artifacts import router as ws_artifacts_router
```

And add `"ws_artifacts_router",` to the `__all__` list (near `"artifacts_router",`).

- [ ] **Step 5: Include the router in the app**

In `backend/cubebox/api/app.py`, find the line (~547):

```python
    app.include_router(artifacts_router, prefix="/api/v1")
```

Add immediately after it:

```python
    app.include_router(ws_artifacts_router, prefix="/api/v1")
```

Also add `ws_artifacts_router` to the import from `cubebox.api.routes.v1` at the top of `app.py` (find where `artifacts_router` is imported and add it alongside).

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/e2e/test_ws_artifacts.py -v`
Expected: PASS (all 5 tests). If `authed_client`/`authed_user_id` fixture names differ, fix the test to use the real names confirmed in Step 1's NOTE.

- [ ] **Step 7: Type-check + lint**

Run: `cd backend && uv run mypy cubebox/ && uv run ruff check cubebox/`
Expected: `Success` + `All checks passed!`.

- [ ] **Step 8: Commit**

```bash
git add backend/cubebox/api/routes/v1/ws_artifacts.py \
        backend/cubebox/api/routes/v1/__init__.py \
        backend/cubebox/api/app.py \
        backend/tests/e2e/test_ws_artifacts.py
git commit -m "feat(artifacts): workspace-level list + delete endpoints"
```

---

## Task 4: Core API — `listWorkspaceArtifacts` + `deleteArtifact`

**Files:**
- Create: `frontend/packages/core/src/api/artifacts.ts`
- Modify: `frontend/packages/core/src/api/index.ts`

Note: the API client rewrites `/api/v1/<path>` to `/api/v1/ws/<wsId>/<path>` when a workspaceId is set (see `client.ts` header comment). So these functions use the unscoped `/api/v1/artifacts` form.

- [ ] **Step 1: Create the core module**

Create `frontend/packages/core/src/api/artifacts.ts`:

```typescript
import { toApiError, type ApiClient } from './client'
import type { Artifact } from '../types'

export interface ListWorkspaceArtifactsParams {
  type?: string
  q?: string
  limit?: number
  offset?: number
}

export async function listWorkspaceArtifacts(
  client: ApiClient,
  params: ListWorkspaceArtifactsParams = {},
): Promise<{ artifacts: Artifact[]; total: number }> {
  const qs = new URLSearchParams()
  if (params.type) qs.set('type', params.type)
  if (params.q) qs.set('q', params.q)
  if (params.limit != null) qs.set('limit', String(params.limit))
  if (params.offset != null) qs.set('offset', String(params.offset))
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  const res = await client.get(`/api/v1/artifacts${suffix}`)
  if (!res.ok) throw await toApiError(res)
  const data = (await res.json()) as { artifacts?: Artifact[]; total?: number }
  return { artifacts: data.artifacts ?? [], total: data.total ?? 0 }
}

export async function deleteArtifact(client: ApiClient, artifactId: string): Promise<void> {
  const res = await client.del(`/api/v1/artifacts/${artifactId}`)
  if (!res.ok) throw await toApiError(res)
}
```

- [ ] **Step 2: Export from the API barrel**

In `frontend/packages/core/src/api/index.ts`, add after the `export * from './conversations'` line:

```typescript
export * from './artifacts'
```

- [ ] **Step 3: Build core**

Run: `cd frontend && pnpm --filter @cubebox/core build`
Expected: build succeeds (so `packages/web` can see the new exports).

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/core/src/api/artifacts.ts frontend/packages/core/src/api/index.ts
git commit -m "feat(artifacts): core API listWorkspaceArtifacts + deleteArtifact"
```

---

## Task 5: i18n keys

**Files:**
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`

- [ ] **Step 1: Add the sidebar label (en)**

In `frontend/packages/web/messages/en.json`, inside the `"sidebar"` object, add an `"artifacts"` key:

```json
    "artifacts": "Artifacts",
```

- [ ] **Step 2: Add the page namespace (en)**

In `frontend/packages/web/messages/en.json`, add a new top-level namespace `"artifactsPage"` (place it near `"chat"`):

```json
  "artifactsPage": {
    "title": "Artifacts",
    "subtitle": "Deliverables your agents produced across conversations.",
    "searchPlaceholder": "Search by name…",
    "filterAll": "All",
    "empty": "No artifacts yet",
    "emptyHint": "Artifacts your agents save will show up here.",
    "noResults": "No artifacts match your filters.",
    "openSource": "Open source conversation",
    "download": "Download",
    "delete": "Delete",
    "preview": "Preview",
    "deleteConfirmTitle": "Delete artifact?",
    "deleteConfirmBody": "This permanently removes \"{name}\" and its files. This cannot be undone.",
    "deleteConfirmCancel": "Cancel",
    "deleteConfirmAction": "Delete",
    "deleteFailed": "Failed to delete artifact",
    "loadFailed": "Failed to load artifacts"
  },
```

- [ ] **Step 3: Add the same keys (zh)**

In `frontend/packages/web/messages/zh.json`, add to the `"sidebar"` object:

```json
    "artifacts": "成果库",
```

And add the `"artifactsPage"` namespace:

```json
  "artifactsPage": {
    "title": "成果库",
    "subtitle": "你的智能体在各个会话里产生的成果。",
    "searchPlaceholder": "按名称搜索…",
    "filterAll": "全部",
    "empty": "暂无成果",
    "emptyHint": "智能体保存的成果会出现在这里。",
    "noResults": "没有符合筛选条件的成果。",
    "openSource": "打开来源会话",
    "download": "下载",
    "delete": "删除",
    "preview": "预览",
    "deleteConfirmTitle": "删除成果？",
    "deleteConfirmBody": "将永久删除「{name}」及其文件，且无法恢复。",
    "deleteConfirmCancel": "取消",
    "deleteConfirmAction": "删除",
    "deleteFailed": "删除成果失败",
    "loadFailed": "加载成果失败"
  },
```

- [ ] **Step 4: Verify i18n key parity**

Run: `cd frontend && pnpm --filter @cubebox/web lint` (the pre-commit i18n parity hook also checks this; ensure en/zh have identical key sets).
Expected: no i18n parity errors. If a standalone parity script exists (check `package.json` scripts for `i18n`), run it instead.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/messages/en.json frontend/packages/web/messages/zh.json
git commit -m "feat(artifacts): i18n keys for artifacts page + sidebar"
```

---

## Task 6: Sidebar nav entry

**Files:**
- Modify: `frontend/packages/web/components/layout/Sidebar.tsx`

- [ ] **Step 1: Widen the `labelKey` union**

In the `WorkspaceNavEntry` interface, add `'artifacts'` to the `labelKey` union:

```typescript
  labelKey: 'skills' | 'mcp' | 'artifacts' | 'scheduledTasks' | 'settings' | 'triggers'
```

- [ ] **Step 2: Import the icon**

Ensure `Package` is imported from `lucide-react` at the top of the file (add it to the existing `lucide-react` import if not already present):

```typescript
import { Package } from 'lucide-react'
```

(Confirm it isn't already imported — `lucide-react` imports are grouped; add `Package` into that group.)

- [ ] **Step 3: Add the prefix + active flag**

In `WorkspaceNav`, alongside the other `const xPrefix = ...` lines, add:

```typescript
  const artifactsPrefix = `/w/${wsId}/artifacts`
```

And alongside the other `const onX = ...` lines:

```typescript
  const onArtifacts = pathname?.startsWith(artifactsPrefix) ?? false
```

- [ ] **Step 4: Add the nav entry**

In the `entries: WorkspaceNavEntry[]` array, add this entry right after the `mcp` entry (before `scheduledTasks`):

```typescript
    {
      key: 'artifacts',
      labelKey: 'artifacts',
      icon: Package,
      href: artifactsPrefix,
      isActive: onArtifacts,
    },
```

- [ ] **Step 5: Type-check**

Run: `cd frontend && pnpm --filter @cubebox/web exec tsc --noEmit`
Expected: no type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/components/layout/Sidebar.tsx
git commit -m "feat(artifacts): add Artifacts nav entry to sidebar"
```

---

## Task 7: `ArtifactsToolbar` module

**Files:**
- Create: `frontend/packages/web/components/artifacts/ArtifactsToolbar.tsx`

- [ ] **Step 1: Create the toolbar component**

Create `frontend/packages/web/components/artifacts/ArtifactsToolbar.tsx`. It renders a search input and type-filter chips. The available types are passed in (derived by the page from the loaded list). `selectedType === null` means "All".

```typescript
'use client'

import { useTranslations } from 'next-intl'
import { Search } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ArtifactsToolbarProps {
  types: string[]
  selectedType: string | null
  onSelectType: (type: string | null) => void
  search: string
  onSearch: (value: string) => void
}

export function ArtifactsToolbar({
  types,
  selectedType,
  onSelectType,
  search,
  onSearch,
}: ArtifactsToolbarProps): React.ReactElement {
  const t = useTranslations('artifactsPage')

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
        <input
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder={t('searchPlaceholder')}
          className="h-8 w-56 rounded-md border border-border bg-background pl-8 pr-3 text-sm
            outline-none transition-colors focus:border-primary/40"
          data-testid="artifacts-search"
        />
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <Chip active={selectedType === null} onClick={() => onSelectType(null)}>
          {t('filterAll')}
        </Chip>
        {types.map((type) => (
          <Chip key={type} active={selectedType === type} onClick={() => onSelectType(type)}>
            {type}
          </Chip>
        ))}
      </div>
    </div>
  )
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}): React.ReactElement {
  return (
    <button
      onClick={onClick}
      className={cn(
        'rounded-full border px-2.5 py-1 text-xs capitalize transition-colors',
        active
          ? 'border-primary/40 bg-primary/10 text-foreground'
          : 'border-border text-muted-foreground hover:text-foreground hover:bg-accent',
      )}
    >
      {children}
    </button>
  )
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && pnpm --filter @cubebox/web exec tsc --noEmit`
Expected: no type errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/artifacts/ArtifactsToolbar.tsx
git commit -m "feat(artifacts): ArtifactsToolbar module"
```

---

## Task 8: `ArtifactLibraryCard` module

**Files:**
- Create: `frontend/packages/web/components/artifacts/ArtifactLibraryCard.tsx`

- [ ] **Step 1: Create the grid card component**

Create `frontend/packages/web/components/artifacts/ArtifactLibraryCard.tsx`. Clicking the card opens the preview panel; a dropdown menu offers download / open-source / delete. Reuses `getArtifactIcon`, `buildDownloadUrl`, and `usePanelStore.openArtifact`. Delete is delegated to the page via `onDelete` so the page owns the confirm dialog + list mutation.

```typescript
'use client'

import { useCallback } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { MoreVertical, Download, ExternalLink, Trash2 } from 'lucide-react'
import { usePanelStore } from '@cubebox/core'
import type { Artifact } from '@cubebox/core'
import { getArtifactIcon } from '@/components/panel/artifact/artifactIcons'
import { buildDownloadUrl } from '@/components/panel/artifact/previewUtils'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'

interface ArtifactLibraryCardProps {
  artifact: Artifact
  workspaceId: string
  onDelete: (artifact: Artifact) => void
}

export function ArtifactLibraryCard({
  artifact,
  workspaceId,
  onDelete,
}: ArtifactLibraryCardProps): React.ReactElement {
  const t = useTranslations('artifactsPage')
  const router = useRouter()
  const openArtifact = usePanelStore((s) => s.openArtifact)
  const Icon = getArtifactIcon(artifact)
  const conversationHref = `/w/${workspaceId}/conversations/${artifact.conversation_id}`

  const handlePreview = useCallback(() => {
    openArtifact(artifact.conversation_id, artifact.id)
  }, [openArtifact, artifact.conversation_id, artifact.id])

  return (
    <div
      onClick={handlePreview}
      className={cn(
        'group relative flex cursor-pointer flex-col gap-3 rounded-xl border border-border',
        'bg-card p-4 transition-all hover:border-primary/30 hover:shadow-sm',
      )}
      data-testid="artifact-card"
    >
      <div className="flex items-start justify-between">
        <div className="flex size-10 items-center justify-center rounded-lg bg-primary/10">
          {/* eslint-disable-next-line react-hooks/static-components -- Icon is a component reference from getArtifactIcon */}
          <Icon className="size-5 text-primary" />
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild onClick={(e) => e.stopPropagation()}>
            <button
              className="rounded-md p-1 text-muted-foreground opacity-0 transition-opacity
                hover:bg-muted hover:text-foreground group-hover:opacity-100"
              aria-label={t('preview')}
              data-testid="artifact-card-menu"
            >
              <MoreVertical className="size-4" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
            <DropdownMenuItem asChild>
              <a href={buildDownloadUrl(artifact, workspaceId)} download>
                <Download className="mr-2 size-4" />
                {t('download')}
              </a>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href={conversationHref}>
                <ExternalLink className="mr-2 size-4" />
                {t('openSource')}
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem
              className="text-destructive focus:text-destructive"
              onSelect={() => onDelete(artifact)}
              data-testid="artifact-card-delete"
            >
              <Trash2 className="mr-2 size-4" />
              {t('delete')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-foreground">{artifact.name}</span>
          {artifact.version > 1 && (
            <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              v{artifact.version}
            </span>
          )}
        </div>
        <div className="mt-0.5 flex items-center gap-1.5 text-xs capitalize text-muted-foreground">
          <span>{artifact.artifact_type}</span>
        </div>
        {artifact.description && (
          <p className="mt-1 line-clamp-2 text-xs text-muted-foreground/80">
            {artifact.description}
          </p>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Confirm `dropdown-menu` exists**

Run: `ls frontend/packages/web/components/ui/dropdown-menu.tsx`
Expected: file exists. If missing, add it with `cd frontend/packages/web && npx shadcn-ui@latest add dropdown-menu` (see `shadcn` skill) and commit separately.

- [ ] **Step 3: Type-check**

Run: `cd frontend && pnpm --filter @cubebox/web exec tsc --noEmit`
Expected: no type errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/artifacts/ArtifactLibraryCard.tsx
git commit -m "feat(artifacts): ArtifactLibraryCard module"
```

---

## Task 9: `ArtifactsEmptyState` module

**Files:**
- Create: `frontend/packages/web/components/artifacts/ArtifactsEmptyState.tsx`

- [ ] **Step 1: Create the empty-state component**

Create `frontend/packages/web/components/artifacts/ArtifactsEmptyState.tsx`:

```typescript
'use client'

import { useTranslations } from 'next-intl'
import { Package } from 'lucide-react'

export function ArtifactsEmptyState(): React.ReactElement {
  const t = useTranslations('artifactsPage')
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 py-24 text-center">
      <div className="flex size-14 items-center justify-center rounded-2xl bg-muted">
        <Package className="size-7 text-muted-foreground" />
      </div>
      <p className="text-sm font-medium text-foreground">{t('empty')}</p>
      <p className="max-w-xs text-xs text-muted-foreground">{t('emptyHint')}</p>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && pnpm --filter @cubebox/web exec tsc --noEmit`
Expected: no type errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/artifacts/ArtifactsEmptyState.tsx
git commit -m "feat(artifacts): ArtifactsEmptyState module"
```

---

## Task 10: The page — grid + side preview panel host

**Files:**
- Create: `frontend/packages/web/app/(app)/w/[wsId]/artifacts/page.tsx`

- [ ] **Step 1: Create the page**

Create `frontend/packages/web/app/(app)/w/[wsId]/artifacts/page.tsx`. It:
1. loads the workspace artifact list via `listWorkspaceArtifacts`,
2. seeds the global `artifactStore` so the reused `ArtifactPanel` can render on click,
3. renders a `ResizablePanelGroup`: left = toolbar + grid, right = `ArtifactPanel` when `view.type === 'artifact'`,
4. owns the delete confirm dialog + list mutation.

```typescript
'use client'

import { use, useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  createApiClient,
  listWorkspaceArtifacts,
  deleteArtifact,
  useArtifactStore,
  usePanelStore,
} from '@cubebox/core'
import type { Artifact } from '@cubebox/core'
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable'
import { ArtifactPanel } from '@/components/panel/artifact/ArtifactPanel'
import { ArtifactsToolbar } from '@/components/artifacts/ArtifactsToolbar'
import { ArtifactLibraryCard } from '@/components/artifacts/ArtifactLibraryCard'
import { ArtifactsEmptyState } from '@/components/artifacts/ArtifactsEmptyState'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { toast } from 'sonner'

interface PageProps {
  params: Promise<{ wsId: string }>
}

export default function WorkspaceArtifactsPage({ params }: PageProps): React.ReactElement {
  const { wsId } = use(params)
  const t = useTranslations('artifactsPage')

  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedType, setSelectedType] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [pendingDelete, setPendingDelete] = useState<Artifact | null>(null)

  const view = usePanelStore((s) => s.view)
  const closePanel = usePanelStore((s) => s.close)
  const seedArtifact = useArtifactStore((s) => s.addOrUpdate)

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    listWorkspaceArtifacts(client)
      .then(({ artifacts: list }) => {
        if (cancelled) return
        setArtifacts(list)
        // Seed the global store so ArtifactPanel can preview by (convId, id).
        for (const a of list) seedArtifact(a.conversation_id, a)
      })
      .catch(() => {
        if (!cancelled) toast.error(t('loadFailed'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [client, seedArtifact, t])

  const types = useMemo(
    () => Array.from(new Set(artifacts.map((a) => a.artifact_type))).sort(),
    [artifacts],
  )

  const filtered = useMemo(
    () =>
      artifacts.filter((a) => {
        if (selectedType && a.artifact_type !== selectedType) return false
        if (search && !a.name.toLowerCase().includes(search.toLowerCase())) return false
        return true
      }),
    [artifacts, selectedType, search],
  )

  const handleConfirmDelete = useCallback(async () => {
    if (!pendingDelete) return
    const target = pendingDelete
    setPendingDelete(null)
    try {
      await deleteArtifact(client, target.id)
      setArtifacts((prev) => prev.filter((a) => a.id !== target.id))
      if (view.type === 'artifact' && view.artifactId === target.id) closePanel()
    } catch {
      toast.error(t('deleteFailed'))
    }
  }, [pendingDelete, client, view, closePanel, t])

  const panelOpen = view.type === 'artifact'

  const grid = (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <h2 className="text-lg font-semibold tracking-tight">{t('title')}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('subtitle')}</p>
      </header>
      <div className="border-b border-border/70 px-6 py-3">
        <ArtifactsToolbar
          types={types}
          selectedType={selectedType}
          onSelectType={setSelectedType}
          search={search}
          onSearch={setSearch}
        />
      </div>
      <div className="flex-1 overflow-y-auto px-6 py-6">
        {!loading && artifacts.length === 0 ? (
          <ArtifactsEmptyState />
        ) : filtered.length === 0 && !loading ? (
          <p className="py-16 text-center text-sm text-muted-foreground">{t('noResults')}</p>
        ) : (
          <div
            className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
            data-testid="artifacts-grid"
          >
            {filtered.map((a) => (
              <ArtifactLibraryCard
                key={a.id}
                artifact={a}
                workspaceId={wsId}
                onDelete={setPendingDelete}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )

  return (
    <>
      <ResizablePanelGroup direction="horizontal" className="h-full">
        <ResizablePanel defaultSize={panelOpen ? 55 : 100} minSize={30}>
          {grid}
        </ResizablePanel>
        {panelOpen && (
          <>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize={45} minSize={25}>
              <ArtifactPanel />
            </ResizablePanel>
          </>
        )}
      </ResizablePanelGroup>

      <AlertDialog open={pendingDelete !== null} onOpenChange={(o) => !o && setPendingDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('deleteConfirmTitle')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('deleteConfirmBody', { name: pendingDelete?.name ?? '' })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('deleteConfirmCancel')}</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              data-testid="artifact-delete-confirm"
            >
              {t('deleteConfirmAction')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
```

- [ ] **Step 2: Confirm `alert-dialog` and `sonner` toast are available**

Run: `ls frontend/packages/web/components/ui/alert-dialog.tsx && grep -rn "from 'sonner'" frontend/packages/web/components | head -1`
Expected: alert-dialog file exists AND `sonner` is already used somewhere. If `alert-dialog` is missing, add via shadcn. If the toast import path differs (e.g. a local `@/components/ui/use-toast`), match the existing pattern used elsewhere in the web app and adjust the import + call.

- [ ] **Step 3: Reset panel state when leaving the page**

Add an effect to close any open artifact panel on unmount so it doesn't bleed into the chat view. Insert this effect after the load effect:

```typescript
  useEffect(() => {
    return () => {
      // Don't leave the global panel open when navigating away from this page.
      if (usePanelStore.getState().view.type === 'artifact') {
        usePanelStore.getState().close()
      }
    }
  }, [])
```

- [ ] **Step 4: Type-check + build web**

Run: `cd frontend && pnpm --filter @cubebox/web exec tsc --noEmit`
Expected: no type errors.

- [ ] **Step 5: Manual smoke (optional but recommended)**

With backend + frontend running on the worktree ports (8050 / 3050), log in, open `/w/<wsId>/artifacts`, confirm the grid loads and a card opens the side preview. (See worktree `pnpm dev` wrapper — do NOT bypass it, or PORT defaults to 3000.)

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/app/\(app\)/w/\[wsId\]/artifacts/page.tsx
git commit -m "feat(artifacts): workspace artifacts page with grid + side preview"
```

---

## Task 11: E2E test — artifacts page

**Files:**
- Test: `frontend/packages/web/__tests__/e2e/artifacts/artifacts-page.spec.ts`

This test must drive the real app. Creating an artifact requires an agent run that calls `save_artifact`, which is heavy/flaky for a UI E2E. Instead, seed an artifact + conversation directly via the backend test DB (the project already does direct SQL seeding in `backend/tests/e2e/test_artifact_share_token.py`). For the **frontend** E2E, prefer seeding through whatever HTTP/test affordance the other web E2E specs use; if none exists for artifacts, this spec focuses on UI behavior reachable without a live agent run and the backend E2E (Task 3) covers the data path.

- [ ] **Step 1: Confirm the seeding affordance**

Run: `ls frontend/packages/web/__tests__/e2e/ && sed -n '1,40p' frontend/packages/web/__tests__/e2e/skills/_helpers.ts`
Expected: review `_helpers.ts` for `registerAsAdmin` / API helpers. Determine whether the web E2E harness can seed an artifact (e.g. via a backend test endpoint or direct API). If artifacts can only be created via an agent run, keep this spec to: nav entry visible, page loads, empty state renders — and rely on Task 3's backend E2E for list/delete correctness. Document the chosen scope in a top-of-file comment.

- [ ] **Step 2: Write the spec**

Create `frontend/packages/web/__tests__/e2e/artifacts/artifacts-page.spec.ts`:

```typescript
import { test, expect } from '@playwright/test'
import { registerAsAdmin } from '../skills/_helpers'

/**
 * Artifacts page E2E. Artifact creation requires a live agent run
 * (save_artifact), so list/delete data correctness is covered by the backend
 * E2E suite (test_ws_artifacts.py). This spec verifies the page is reachable,
 * the nav entry works, and the empty state renders for a fresh workspace.
 */
test.describe('artifacts page', () => {
  test('nav entry opens the artifacts page', async ({ page }) => {
    await registerAsAdmin(page)

    // Navigate via the workspace sidebar "Artifacts" entry.
    await page.getByRole('link', { name: /artifacts/i }).first().click()
    await expect(page).toHaveURL(/\/w\/[^/]+\/artifacts/)

    // Header + empty state for a fresh workspace.
    await expect(page.getByRole('heading', { name: /artifacts/i })).toBeVisible()
    await expect(page.getByText(/no artifacts yet/i)).toBeVisible()
  })
})
```

- [ ] **Step 3: Run the E2E test**

Run (from the worktree, using its ports — the E2E harness reads `.worktree.env`):
`cd frontend && pnpm --filter @cubebox/web exec playwright test __tests__/e2e/artifacts/artifacts-page.spec.ts`
Expected: PASS. (First run may need `npx playwright install`.)

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/__tests__/e2e/artifacts/artifacts-page.spec.ts
git commit -m "test(artifacts): E2E for artifacts page nav + empty state"
```

---

## Task 12: Pre-PR verification sweep

**Files:** _none_ (verification only)

- [ ] **Step 1: Backend full check**

Run: `cd backend && uv run mypy cubebox/ && uv run ruff check cubebox/ && uv run pytest tests/e2e/test_ws_artifacts.py tests/unit/test_artifacts.py -v`
Expected: all green.

- [ ] **Step 2: Frontend full check**

Run: `cd frontend && pnpm --filter @cubebox/core build && pnpm --filter @cubebox/web exec tsc --noEmit && pnpm --filter @cubebox/web lint`
Expected: build + types + lint pass, including i18n key parity.

- [ ] **Step 3: Frontend E2E**

Run: `cd frontend && pnpm --filter @cubebox/web exec playwright test __tests__/e2e/artifacts/`
Expected: PASS.

- [ ] **Step 4: Review the diff**

Run: `git log --oneline origin/main..HEAD && git diff --stat origin/main..HEAD`
Confirm scope is exactly: backend repo methods + ws_artifacts route + registration + backend E2E; core API module + export; web page + 3 modules + sidebar entry + i18n + web E2E. No stray changes.

---

## Notes for the implementer

- **Scope isolation:** `ws_artifacts.py` is intentionally a separate handler from `artifacts.py`. Do not parameterize the conversation-scoped handler to also serve workspace scope.
- **Reuse boundary:** the page reuses `ArtifactPanel` by seeding the global `artifactStore` and driving `panelStore`. Do not fork `ArtifactPanel`.
- **No sharing this version:** do not add share-token UI/endpoints here; that's a deferred non-goal in the spec.
- **Datetimes:** the existing `Artifact.to_dict()` already uses `utc_isoformat()`; no datetime work needed.
- **No migration:** no schema change — `list_by_workspace`/`delete` use existing tables. Do not run alembic.
