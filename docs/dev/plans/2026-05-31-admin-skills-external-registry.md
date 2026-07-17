# Admin Skills External Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add external registry skill discovery + install-to-org-catalog to the admin skills page, matching the workspace page's UX but targeting the org catalog (not workspace-private).

**Architecture:** Two new admin endpoints (`/admin/skills/discover`, `/admin/skills/discover/preview`, `/admin/skills/install-candidate`) handle admin-scoped discovery and install. `SkillsAdapterManager.build()` gains an optional `workspace_id` so `LocalCatalogAdapter` can run without one. Install writes to the org catalog (`workspace_id=None`) via `upsert()`, not `create_for_workspace()`. The frontend reuses `CandidateCard` + `CandidateDetailPanel` from the workspace page with a new `useAdminSkillsStore`.

**Tech Stack:** FastAPI, SQLModel, SQLAlchemy async, Next.js 14, Zustand, SWR, shadcn/ui, react-markdown, zod (none new).

**Worktree:** `/home/chris/cubeplex/.worktrees/feat/skillssh-source` (port 8043/3043)

---

## File Map

**Backend — modify:**
- `backend/cubeplex/skills/sources/local.py` — support `workspace_id=None` (show org install state)
- `backend/cubeplex/skills/sources/registry.py` — `build()` workspace_id optional
- `backend/cubeplex/skills/discovery.py` — `SkillInstallService` workspace_id optional; add `install_to_org_catalog()` path
- `backend/cubeplex/api/routes/v1/admin_skills.py` — add 3 new endpoints
- `backend/cubeplex/api/schemas/skill_discovery.py` — add `AdminInstallCandidateRequest`

**Frontend — create:**
- `frontend/packages/core/src/api/adminSkills.ts` — `adminDiscoverSkills`, `adminPreviewCandidate`, `adminInstallCandidate`
- `frontend/packages/core/src/stores/adminSkillsStore.ts` — Zustand store for admin discover/install
- `frontend/packages/web/components/admin/skills/AdminCandidateDetailPanel.tsx` — adapter of CandidateDetailPanel for admin install

**Frontend — modify:**
- `frontend/packages/core/src/index.ts` — export new API + store
- `frontend/packages/web/app/admin/skills/page.tsx` — add discover search + External section
- `frontend/packages/web/components/admin/skills/SkillsToolbar.tsx` — add search input + "External" pill
- `frontend/packages/web/components/admin/skills/SkillsList.tsx` — add External Sources section
- `frontend/packages/web/messages/en.json` — add i18n keys
- `frontend/packages/web/messages/zh.json` — add i18n keys

---

## Task 1: Backend — `LocalCatalogAdapter` org-mode + `SkillsAdapterManager` optional workspace

**Files:**
- Modify: `backend/cubeplex/skills/sources/local.py`
- Modify: `backend/cubeplex/skills/sources/registry.py`
- Test: `backend/tests/unit/test_local_catalog_adapter.py` (create)

- [ ] **Step 1: Write failing test**

```python
# backend/tests/unit/test_local_catalog_adapter.py
"""LocalCatalogAdapter in org-mode (workspace_id=None) shows org install state."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from cubeplex.skills.sources.local import LocalCatalogAdapter


@pytest.mark.asyncio
async def test_local_adapter_accepts_none_workspace_id():
    """build() with workspace_id=None must not raise."""
    session = MagicMock()
    catalog = MagicMock()
    # LocalCatalogAdapter should accept workspace_id=None
    adapter = LocalCatalogAdapter(
        session=session,
        catalog=catalog,
        org_id="org-1",
        workspace_id=None,
    )
    assert adapter is not None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/chris/cubeplex/.worktrees/feat/skillssh-source/backend
uv run pytest tests/unit/test_local_catalog_adapter.py -v
```
Expected: FAIL — `workspace_id=None` causes a type error.

- [ ] **Step 3: Update `LocalCatalogAdapter` to accept `workspace_id=None`**

In `backend/cubeplex/skills/sources/local.py`, change the `__init__` signature and `search` method:

```python
class LocalCatalogAdapter:
    kind: SourceKind = "local"

    def __init__(
        self,
        *,
        session: AsyncSession,
        catalog: SkillCatalogService,
        org_id: str,
        workspace_id: str | None,
    ) -> None:
        self._session = session
        self._catalog = catalog
        self._org_id = org_id
        self._workspace_id = workspace_id

    async def search(self, query: str, *, limit: int) -> list[SkillCandidate]:
        del query, limit
        visible = await SkillRepository(self._session).list_visible_for_org(self._org_id)
        tombstones = await OrgPreinstalledTombstoneRepository(self._session).list_for_org(
            self._org_id
        )
        tombstoned_ids = {t.skill_id for t in tombstones}

        if self._workspace_id is not None:
            # Workspace mode: show workspace-level install state
            enabled = await self._catalog.list_enabled_for_workspace(
                self._workspace_id, org_id=self._org_id
            )
            enabled_names = {r.name for r in enabled}
            install_state_fn = lambda s: "enabled" if s.name in enabled_names else "in_catalog"
        else:
            # Org mode (admin): show org-level install state
            from cubeplex.repositories.skill import OrgSkillInstallRepository
            installs = await OrgSkillInstallRepository(self._session).list_for_org(self._org_id)
            installed_ids = {i.skill_id for i in installs}
            install_state_fn = lambda s: "in_catalog" if s.id in installed_ids else "available"

        out: list[SkillCandidate] = []
        for s in visible:
            if s.id in tombstoned_ids:
                continue
            out.append(
                SkillCandidate(
                    candidate_id=encode_candidate_id("local", s.id),
                    name=s.name,
                    canonical_name=s.name,
                    description=s.description,
                    source_kind="local",
                    source_ref=s.id,
                    keywords=list(s.keywords),
                    version=s.current_version,
                    trust=TrustTier.official,
                    install_state=install_state_fn(s),
                    source_name="catalog",
                    repo=None,
                )
            )
        return out
```

- [ ] **Step 4: Update `SkillsAdapterManager.build()` — make workspace_id optional**

In `backend/cubeplex/skills/sources/registry.py`:

```python
@classmethod
async def build(
    cls,
    *,
    session: AsyncSession,
    catalog: SkillCatalogService,
    org_id: str,
    org_slug: str,
    workspace_id: str | None,
) -> SkillsAdapterManager:
    adapters: list[SkillRegistryAdapter] = [
        LocalCatalogAdapter(
            session=session,
            catalog=catalog,
            org_id=org_id,
            workspace_id=workspace_id,
        )
    ]
    # ... rest unchanged
```

- [ ] **Step 5: Run test + mypy**

```bash
cd backend
uv run pytest tests/unit/test_local_catalog_adapter.py -v
uv run mypy cubeplex/
```
Expected: test PASS, mypy 0 errors.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/skills/sources/local.py \
        backend/cubeplex/skills/sources/registry.py \
        backend/tests/unit/test_local_catalog_adapter.py
git commit -m "feat(skills): LocalCatalogAdapter supports org-mode (workspace_id=None)"
```

---

## Task 2: Backend — `SkillInstallService` org-catalog install path

**Files:**
- Modify: `backend/cubeplex/skills/discovery.py`

The key change: `workspace_id` becomes `str | None`. When `None`, use `upsert()` (org-wide) instead of `create_for_workspace()`.

- [ ] **Step 1: Update `SkillInstallService.__init__` signature**

In `backend/cubeplex/skills/discovery.py`, change:

```python
class SkillInstallService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        registry: SkillsAdapterManager,
        publisher: SkillPublishService,
        org_id: str,
        org_slug: str,
        workspace_id: str | None,  # None = org-wide catalog install
        actor_user_id: str,
    ) -> None:
        self._session = session
        self._registry = registry
        self._publisher = publisher
        self._org_id = org_id
        self._org_slug = org_slug
        self._workspace_id = workspace_id
        self._actor = actor_user_id
```

- [ ] **Step 2: Update `_install_local` to handle org-mode**

Replace the `OrgSkillInstallRepository(self._session).create_for_workspace(...)` call in `_install_local` with:

```python
if self._workspace_id is not None:
    await OrgSkillInstallRepository(self._session).create_for_workspace(
        org_id=self._org_id,
        workspace_id=self._workspace_id,
        skill_id=skill.id,
        installed_version=skill.current_version,
        installed_by_user_id=self._actor,
    )
else:
    await OrgSkillInstallRepository(self._session).upsert(
        org_id=self._org_id,
        skill_id=skill.id,
        installed_version=skill.current_version,
        installed_by_user_id=self._actor,
        auto_bind=False,
    )
```

- [ ] **Step 3: Update `_install_remote` VersionCollisionError branch similarly**

Both `create_for_workspace` calls in `_install_remote` (the collision path and the success path) become:

```python
# success path after sv = await self._publisher._publish_from_files(...)
if self._workspace_id is not None:
    await OrgSkillInstallRepository(self._session).create_for_workspace(
        org_id=self._org_id,
        workspace_id=self._workspace_id,
        skill_id=sv.skill_id,   # Note: use existing.id in collision path
        installed_version=install_version,
        installed_by_user_id=self._actor,
    )
else:
    await OrgSkillInstallRepository(self._session).upsert(
        org_id=self._org_id,
        skill_id=sv.skill_id,
        installed_version=install_version,
        installed_by_user_id=self._actor,
        auto_bind=False,
    )
```

Apply the same pattern in the VersionCollisionError branch (where `existing` is used instead of `sv`).

- [ ] **Step 4: Run mypy**

```bash
cd backend && uv run mypy cubeplex/
```
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/skills/discovery.py
git commit -m "feat(skills): SkillInstallService workspace_id=None installs to org catalog"
```

---

## Task 3: Backend — three new admin endpoints

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/admin_skills.py`
- Modify: `backend/cubeplex/api/schemas/skill_discovery.py`

Add to `skill_discovery.py`:

```python
class AdminInstallCandidateRequest(BaseModel):
    candidate_id: str
```

Add to `admin_skills.py` (after existing imports, add the discovery imports):

```python
from cubeplex.api.schemas.skill_discovery import (
    AdminInstallCandidateRequest,
    CandidatePreviewResponse,
    InstallCandidateResponse,
    SkillCandidateResponse,
)
from cubeplex.repositories.skill_registry import SkillRegistryRepository
from cubeplex.skills.discovery import SkillDiscoveryService, SkillInstallError, SkillInstallService
from cubeplex.skills.service import SkillPublishService
from cubeplex.skills.sources.base import CandidateIdError, decode_candidate_id
from cubeplex.skills.sources.registry import SkillsAdapterManager
```

- [ ] **Step 1: Add `/admin/skills/discover` endpoint**

Append to `admin_skills.py` (before the `_visible` helper):

```python
@router.get("/discover", response_model=list[SkillCandidateResponse])
async def admin_discover_skills(
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str = Query(..., min_length=1),
    limit: int = Query(5, ge=1, le=20),
) -> list[SkillCandidateResponse]:
    org_id = await resolve_current_org_id(user, session)
    org = await OrganizationRepository(session).get(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")
    catalog = SkillCatalogService(session=session, cache=_cache())
    registry = await SkillsAdapterManager.build(
        session=session,
        catalog=catalog,
        org_id=org_id,
        org_slug=org.slug,
        workspace_id=None,  # org-mode: no workspace context
    )
    cands = await SkillDiscoveryService(registry).discover(q, limit=limit)
    registry_rows = await SkillRegistryRepository(session).list_for_org(org_id)
    registry_names: dict[str, str] = {r.id: r.name for r in registry_rows}
    return [
        SkillCandidateResponse(
            candidate_id=c.candidate_id,
            name=c.name,
            canonical_name=c.canonical_name,
            description=c.description,
            source_kind=c.source_kind,
            keywords=c.keywords,
            version=c.version,
            trust=c.trust.value,
            install_state=c.install_state,
            stars=c.stars,
            install_count=c.install_count,
            source_name=c.source_name,
            repo=c.repo,
            unvetted=(c.source_kind == "remote" and c.trust.value != "official"),
        )
        for c in cands
        if c.source_kind == "remote"  # admin discover: only remote candidates
    ]
```

- [ ] **Step 2: Add `/admin/skills/discover/preview` endpoint**

```python
@router.get("/discover/preview", response_model=CandidatePreviewResponse)
async def admin_preview_candidate(
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    candidate_id: str = Query(...),
) -> CandidatePreviewResponse:
    org_id = await resolve_current_org_id(user, session)
    org = await OrganizationRepository(session).get(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")
    try:
        kind, source_id, source_ref = decode_candidate_id(candidate_id)
    except CandidateIdError as e:
        raise HTTPException(status_code=400, detail="BAD_CANDIDATE_ID") from e
    if kind != "remote":
        raise HTTPException(status_code=400, detail="REMOTE_CANDIDATES_ONLY")
    catalog = SkillCatalogService(session=session, cache=_cache())
    registry = await SkillsAdapterManager.build(
        session=session,
        catalog=catalog,
        org_id=org_id,
        org_slug=org.slug,
        workspace_id=None,
    )
    remote = registry.adapter_by_id(source_id)
    if remote is None:
        raise HTTPException(status_code=404, detail="REGISTRY_NOT_FOUND")
    try:
        files = await remote.fetch(source_ref)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"FETCH_FAILED: {e}") from e
    skill_md = files.get("SKILL.md", b"").decode("utf-8", errors="replace")
    from cubeplex.skills.frontmatter import peek_skill_name
    name = peek_skill_name(skill_md) or source_ref.rsplit("/", 1)[-1]
    return CandidatePreviewResponse(
        candidate_id=candidate_id,
        name=name,
        canonical_name=name,
        content=skill_md,
    )
```

- [ ] **Step 3: Add `/admin/skills/install-candidate` endpoint**

```python
@router.post("/install-candidate", response_model=InstallCandidateResponse)
async def admin_install_candidate(
    body: AdminInstallCandidateRequest,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> InstallCandidateResponse:
    org_id = await resolve_current_org_id(user, session)
    org = await OrganizationRepository(session).get(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")
    catalog = SkillCatalogService(session=session, cache=_cache())
    registry = await SkillsAdapterManager.build(
        session=session,
        catalog=catalog,
        org_id=org_id,
        org_slug=org.slug,
        workspace_id=None,
    )
    publisher = SkillPublishService(session=session, cache=_cache())
    install_svc = SkillInstallService(
        session=session,
        registry=registry,
        publisher=publisher,
        org_id=org_id,
        org_slug=org.slug,
        workspace_id=None,  # install to org catalog, not workspace-private
        actor_user_id=user.id,
    )
    try:
        result = await install_svc.install(body.candidate_id)
    except SkillInstallError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return InstallCandidateResponse(
        canonical_name=result.canonical_name,
        skill_id=result.skill_id,
        installed_version=result.installed_version,
    )
```

- [ ] **Step 4: Run mypy**

```bash
cd backend && uv run mypy cubeplex/
```
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/api/routes/v1/admin_skills.py \
        backend/cubeplex/api/schemas/skill_discovery.py
git commit -m "feat(admin/skills): add discover, preview, and install-candidate endpoints"
```

---

## Task 4: Frontend — API functions + admin store

**Files:**
- Create: `frontend/packages/core/src/api/adminSkills.ts`
- Create: `frontend/packages/core/src/stores/adminSkillsStore.ts`
- Modify: `frontend/packages/core/src/index.ts`

- [ ] **Step 1: Create `adminSkills.ts`**

```typescript
// frontend/packages/core/src/api/adminSkills.ts
import type { ApiClient } from './client'
import type { SkillCandidateOut } from './skills'
import { toApiError } from './client'

export async function adminDiscoverSkills(
  q: string,
  limit = 5,
): Promise<SkillCandidateOut[]> {
  const params = new URLSearchParams({ q, limit: String(limit) })
  const res = await fetch(`/api/v1/admin/skills/discover?${params}`, {
    credentials: 'include',
  })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SkillCandidateOut[]
}

export async function adminPreviewCandidate(
  candidateId: string,
): Promise<{ content: string }> {
  const params = new URLSearchParams({ candidate_id: candidateId })
  const res = await fetch(`/api/v1/admin/skills/discover/preview?${params}`, {
    credentials: 'include',
  })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { content: string }
}

export async function adminInstallCandidate(
  candidateId: string,
): Promise<{ canonical_name: string; skill_id: string; installed_version: string }> {
  const res = await fetch('/api/v1/admin/skills/install-candidate', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ candidate_id: candidateId }),
  })
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<{ canonical_name: string; skill_id: string; installed_version: string }>
}
```

Note: `adminDiscoverSkills` uses `fetch` directly (no `ApiClient`) because the admin page doesn't have a workspace context for the ApiClient URL pattern.

- [ ] **Step 2: Create `adminSkillsStore.ts`**

```typescript
// frontend/packages/core/src/stores/adminSkillsStore.ts
import { create } from 'zustand'
import { adminDiscoverSkills, adminInstallCandidate } from '../api/adminSkills'
import type { SkillCandidateOut } from '../api/skills'

export interface AdminSkillsState {
  candidates: SkillCandidateOut[]
  query: string
  searching: boolean
  installing: Record<string, boolean>
  lastInstalled: string | null
  search: (q: string) => Promise<void>
  install: (candidateId: string) => Promise<void>
  reset: () => void
}

export const useAdminSkillsStore = create<AdminSkillsState>((set) => ({
  candidates: [],
  query: '',
  searching: false,
  installing: {},
  lastInstalled: null,

  async search(q) {
    set({ query: q, candidates: [], searching: true })
    try {
      const candidates = await adminDiscoverSkills(q)
      set({ candidates, searching: false })
    } catch {
      set({ candidates: [], searching: false })
    }
  },

  async install(candidateId) {
    set((s) => ({ installing: { ...s.installing, [candidateId]: true } }))
    try {
      const r = await adminInstallCandidate(candidateId)
      set((s) => ({
        lastInstalled: r.skill_id,
        installing: { ...s.installing, [candidateId]: false },
      }))
    } catch (e) {
      set((s) => ({ installing: { ...s.installing, [candidateId]: false } }))
      throw e
    }
  },

  reset: () =>
    set({ candidates: [], query: '', searching: false, installing: {}, lastInstalled: null }),
}))
```

- [ ] **Step 3: Export from `index.ts`**

Add to `frontend/packages/core/src/index.ts`:

```typescript
export { adminDiscoverSkills, adminPreviewCandidate, adminInstallCandidate } from './api/adminSkills'
export { useAdminSkillsStore } from './stores/adminSkillsStore'
export type { AdminSkillsState } from './stores/adminSkillsStore'
```

- [ ] **Step 4: Run lint + typecheck**

```bash
cd /home/chris/cubeplex/.worktrees/feat/skillssh-source/frontend
pnpm -r run lint
pnpm -r run typecheck
```
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/core/src/api/adminSkills.ts \
        frontend/packages/core/src/stores/adminSkillsStore.ts \
        frontend/packages/core/src/index.ts
git commit -m "feat(admin/skills): add adminSkills API + adminSkillsStore"
```

---

## Task 5: Frontend — `AdminCandidateDetailPanel`

**Files:**
- Create: `frontend/packages/web/components/admin/skills/AdminCandidateDetailPanel.tsx`

This is a thin wrapper around the existing `CandidateDetailPanel` logic, adapted for admin install (no wsId, calls `adminInstallCandidate`, shows "Add to Catalog" button).

- [ ] **Step 1: Create the component**

```tsx
// frontend/packages/web/components/admin/skills/AdminCandidateDetailPanel.tsx
'use client'

import { useState } from 'react'
import useSWR from 'swr'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { FileText, ShieldCheck, ShieldAlert, ShieldOff } from 'lucide-react'
import { useAdminSkillsStore, type SkillCandidateOut } from '@cubeplex/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn, proseClasses } from '@/lib/utils'

function TrustInfo({ trust }: { trust: SkillCandidateOut['trust'] }) {
  if (trust === 'official') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2 py-1 text-xs font-medium text-emerald-600 dark:text-emerald-400">
        <ShieldCheck className="size-3.5" />
        Official
      </span>
    )
  }
  if (trust === 'community') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-500/10 px-2 py-1 text-xs font-medium text-blue-600 dark:text-blue-400">
        <ShieldAlert className="size-3.5" />
        Community
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/10 px-2 py-1 text-xs font-medium text-amber-600 dark:text-amber-400">
      <ShieldOff className="size-3.5" />
      Unvetted
    </span>
  )
}

async function previewFetcher(url: string): Promise<{ content: string }> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`fetch failed: ${res.status}`)
  return res.json() as Promise<{ content: string }>
}

function stripFrontmatter(content: string): string {
  return content.replace(/^---\s*\n[\s\S]*?\n---\s*(\n|$)/, '')
}

interface AdminCandidateDetailPanelProps {
  candidate: SkillCandidateOut
  onInstalled: () => void
}

export function AdminCandidateDetailPanel({ candidate, onInstalled }: AdminCandidateDetailPanelProps) {
  const install = useAdminSkillsStore((s) => s.install)
  const installing = useAdminSkillsStore((s) => s.installing[candidate.candidate_id] ?? false)
  const [installError, setInstallError] = useState<string | null>(null)

  const isInCatalog = candidate.install_state === 'in_catalog'

  const { data: preview, isLoading } = useSWR<{ content: string }>(
    `/api/v1/admin/skills/discover/preview?candidate_id=${candidate.candidate_id}`,
    previewFetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  async function handleInstall() {
    setInstallError(null)
    try {
      await install(candidate.candidate_id)
      onInstalled()
    } catch (e) {
      setInstallError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="flex w-full flex-col gap-4 overflow-y-auto p-6">
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-xl font-semibold tracking-tight">{candidate.name}</h3>
          {candidate.version && (
            <Badge variant="outline" className="font-mono">
              v{candidate.version}
            </Badge>
          )}
          <Badge variant="secondary">{candidate.source_name}</Badge>
          <TrustInfo trust={candidate.trust} />
          <div className="ml-auto flex flex-col items-end gap-1.5">
            <Button
              size="sm"
              disabled={installing || isInCatalog}
              onClick={() => void handleInstall()}
            >
              {isInCatalog ? 'In Catalog' : installing ? 'Adding…' : 'Add to Catalog'}
            </Button>
            {installError && (
              <p className="max-w-48 text-right text-[11px] leading-tight text-destructive">
                {installError}
              </p>
            )}
          </div>
        </div>
        {candidate.description && (
          <p className="text-sm leading-relaxed text-muted-foreground">{candidate.description}</p>
        )}
        {candidate.keywords.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {candidate.keywords.map((kw) => (
              <Badge key={kw} variant="outline" className="text-[11px]">
                {kw}
              </Badge>
            ))}
          </div>
        )}
      </header>

      <dl className="flex flex-col gap-3 border-b border-border pb-4">
        {candidate.install_count !== null && (
          <div className="flex items-center gap-3">
            <dt className="min-w-24 text-xs font-medium text-muted-foreground">Installs</dt>
            <dd className="text-sm">{candidate.install_count.toLocaleString()}</dd>
          </div>
        )}
        {candidate.repo && (
          <div className="flex items-center gap-3">
            <dt className="min-w-24 text-xs font-medium text-muted-foreground">Repo</dt>
            <dd className="truncate text-xs text-muted-foreground">{candidate.repo}</dd>
          </div>
        )}
      </dl>

      <Tabs defaultValue="overview" className="flex-1 flex-col">
        <TabsList variant="line" className="w-full justify-start border-b border-border/60 pb-0">
          <TabsTrigger value="overview">
            <FileText className="size-3.5" />
            Overview
          </TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="mt-4">
          {isLoading && <p className="text-xs text-muted-foreground">Loading SKILL.md…</p>}
          {preview && (
            <div className="rounded-lg border border-border/70 bg-card/40 px-4 py-3">
              <div className={cn(proseClasses)}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {stripFrontmatter(preview.content)}
                </ReactMarkdown>
              </div>
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}
```

- [ ] **Step 2: Run lint**

```bash
cd /home/chris/cubeplex/.worktrees/feat/skillssh-source/frontend
pnpm -r run lint
```
Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/admin/skills/AdminCandidateDetailPanel.tsx
git commit -m "feat(admin/skills): AdminCandidateDetailPanel with Add-to-Catalog install"
```

---

## Task 6: Frontend — toolbar, list, page wiring

**Files:**
- Modify: `frontend/packages/web/components/admin/skills/SkillsToolbar.tsx`
- Modify: `frontend/packages/web/components/admin/skills/SkillsList.tsx`
- Modify: `frontend/packages/web/app/admin/skills/page.tsx`
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`

- [ ] **Step 1: Add i18n keys to `en.json`**

Inside the `"adminSkills"` object, add:

```json
"sourceExternal": "External",
"externalSources": "External Sources",
"externalSearchPlaceholder": "Search external registries…",
"noExternalResults": "No results",
"noExternalResultsHint": "Try a different keyword"
```

Add the same keys to `zh.json`:

```json
"sourceExternal": "外部",
"externalSources": "外部来源",
"externalSearchPlaceholder": "搜索外部注册表…",
"noExternalResults": "无结果",
"noExternalResultsHint": "换一个关键词试试"
```

- [ ] **Step 2: Update `SkillsToolbar.tsx`**

Add `externalOnly` mode and `onExternalSearch` prop:

```tsx
// frontend/packages/web/components/admin/skills/SkillsToolbar.tsx
'use client'

import { useRef } from 'react'
import { Search, Upload } from 'lucide-react'
import { useTranslations } from 'next-intl'
import type { SkillFilters, SkillSource } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

// PillGroup component stays the same (keep existing code)

interface SkillsToolbarProps {
  filters: SkillFilters
  onFiltersChange: (next: SkillFilters) => void
  onUploadClick: () => void
  onExternalSearch: (q: string) => void
}

export function SkillsToolbar({ filters, onFiltersChange, onUploadClick, onExternalSearch }: SkillsToolbarProps) {
  const t = useTranslations('adminSkills')
  const externalInputRef = useRef<HTMLInputElement>(null)
  const externalOnly = filters.externalOnly ?? false

  const SOURCE_OPTIONS: { value: SkillSource | 'all' | 'external'; label: string }[] = [
    { value: 'all', label: t('sourceAll') },
    { value: 'preinstalled', label: t('sourcePreinstalled') },
    { value: 'uploaded', label: t('sourceUploaded') },
    { value: 'external', label: t('sourceExternal') },
  ]

  const INSTALLED_OPTIONS: { value: 'all' | 'installed' | 'uninstalled'; label: string }[] = [
    { value: 'all', label: t('statusAll') },
    { value: 'installed', label: t('statusInstalled') },
    { value: 'uninstalled', label: t('statusUninstalled') },
  ]

  const sourceValue = externalOnly ? 'external' : (filters.source ?? 'all')
  const installedValue: 'all' | 'installed' | 'uninstalled' =
    filters.installed === true ? 'installed' : filters.installed === false ? 'uninstalled' : 'all'

  function handleSourceChange(next: SkillSource | 'all' | 'external') {
    if (next === 'external') {
      onFiltersChange({ externalOnly: true })
    } else {
      onFiltersChange({ ...filters, externalOnly: false, source: next === 'all' ? undefined : next as SkillSource })
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border/70 px-4 py-3">
      {externalOnly ? (
        <div className="relative min-w-[180px] flex-1">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/70" />
          <Input
            ref={externalInputRef}
            type="search"
            placeholder={t('externalSearchPlaceholder')}
            defaultValue=""
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                onExternalSearch((e.currentTarget as HTMLInputElement).value)
              }
            }}
            className="pl-7"
          />
        </div>
      ) : (
        <div className="relative min-w-[180px] flex-1">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/70" />
          <Input
            type="search"
            placeholder={t('searchPlaceholder')}
            value={filters.q ?? ''}
            onChange={(e) => onFiltersChange({ ...filters, q: e.target.value || undefined })}
            className="pl-7"
            aria-label={t('searchAriaLabel')}
          />
        </div>
      )}

      <PillGroup
        ariaLabel={t('filterBySource')}
        options={SOURCE_OPTIONS}
        value={sourceValue}
        onChange={handleSourceChange}
      />

      {!externalOnly && (
        <PillGroup
          ariaLabel={t('filterByStatus')}
          options={INSTALLED_OPTIONS}
          value={installedValue}
          onChange={(next) =>
            onFiltersChange({
              ...filters,
              installed: next === 'all' ? undefined : next === 'installed',
            })
          }
        />
      )}

      {!externalOnly && (
        <Button size="sm" onClick={onUploadClick} className="ml-auto">
          <Upload className="size-3.5" />
          {t('uploadButton')}
        </Button>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Update `SkillFilters` type in `@cubeplex/core`**

In `frontend/packages/core/src/types/skills.ts`, add `externalOnly` to `SkillFilters`:

```typescript
export interface SkillFilters {
  source?: SkillSource
  installed?: boolean
  q?: string
  tag?: string
  externalOnly?: boolean
}
```

- [ ] **Step 4: Update `SkillsList.tsx` — add External Sources section**

```tsx
// frontend/packages/web/components/admin/skills/SkillsList.tsx
'use client'

import { useTranslations } from 'next-intl'
import type { SkillCandidateOut, SkillSummary } from '@cubeplex/core'
import { SkillCard } from './SkillCard'
import { CandidateCard } from '@/components/skills/CandidateCard'

interface SkillsListProps {
  skills: SkillSummary[]
  loading: boolean
  error: Error | undefined
  selectedId: string | null
  onSelect: (id: string) => void
  // External sources
  candidates: SkillCandidateOut[]
  searching: boolean
  externalOnly: boolean
  selectedCandidateId: string | null
  onSelectCandidate: (id: string) => void
}

export function SkillsList({
  skills, loading, error, selectedId, onSelect,
  candidates, searching, externalOnly, selectedCandidateId, onSelectCandidate,
}: SkillsListProps) {
  const t = useTranslations('adminSkills')

  if (externalOnly) {
    if (searching) {
      return (
        <div className="flex flex-col gap-1.5 p-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-[76px] animate-pulse rounded-lg border border-border/50 bg-muted/30" />
          ))}
        </div>
      )
    }
    if (candidates.length === 0) {
      return (
        <div className="flex flex-col items-center justify-center gap-1 px-6 py-8 text-center">
          <p className="text-sm text-muted-foreground">{t('noExternalResults')}</p>
          <p className="text-xs text-muted-foreground/70">{t('noExternalResultsHint')}</p>
        </div>
      )
    }
    return (
      <ul className="flex flex-col gap-1.5 p-3">
        {candidates.map((c) => (
          <li key={c.candidate_id}>
            <CandidateCard
              candidate={c}
              active={c.candidate_id === selectedCandidateId}
              onClick={() => onSelectCandidate(c.candidate_id)}
            />
          </li>
        ))}
      </ul>
    )
  }

  // Normal catalog list
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
        {t('loading')}
      </div>
    )
  }
  if (error) {
    return (
      <div className="m-3 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
        {t('loadFailed', { message: error.message })}
      </div>
    )
  }

  return (
    <div className="flex flex-col">
      {skills.length === 0 ? (
        <div className="flex h-full flex-col items-center justify-center gap-1 px-6 text-center">
          <p className="text-sm text-muted-foreground">{t('noSkills')}</p>
          <p className="text-xs text-muted-foreground/70">{t('noSkillsHint')}</p>
        </div>
      ) : (
        <ul data-testid="skills-list" className="flex flex-col gap-1.5 p-3">
          {skills.map((skill) => (
            <li key={skill.id}>
              <SkillCard
                skill={skill}
                active={skill.id === selectedId}
                onClick={() => onSelect(skill.id)}
              />
            </li>
          ))}
        </ul>
      )}

      {(candidates.length > 0 || searching) && (
        <>
          <div className="flex items-center gap-2 px-4 py-2">
            <span className="text-xs font-medium text-muted-foreground">{t('externalSources')}</span>
            {searching && (
              <div className="flex gap-1">
                <div className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground" />
                <div className="animation-delay-200 h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground" />
                <div className="animation-delay-400 h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground" />
              </div>
            )}
            <div className="flex-1 border-t border-border/50" />
          </div>
          {searching && candidates.length === 0 ? (
            <div className="flex flex-col gap-1.5 px-3 pb-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-[76px] animate-pulse rounded-lg border border-border/50 bg-muted/30" />
              ))}
            </div>
          ) : (
            <ul className="flex flex-col gap-1.5 px-3 pb-3">
              {candidates.map((c) => (
                <li key={c.candidate_id}>
                  <CandidateCard
                    candidate={c}
                    active={c.candidate_id === selectedCandidateId}
                    onClick={() => onSelectCandidate(c.candidate_id)}
                  />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
```

- [ ] **Step 5: Update `page.tsx` to wire everything together**

```tsx
// frontend/packages/web/app/admin/skills/page.tsx
'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import type { SkillFilters } from '@cubeplex/core'
import { useAdminSkillsStore } from '@cubeplex/core'
import { SkillsToolbar } from '@/components/admin/skills/SkillsToolbar'
import { SkillsList } from '@/components/admin/skills/SkillsList'
import { SkillDetailPanel } from '@/components/admin/skills/SkillDetailPanel'
import { AdminCandidateDetailPanel } from '@/components/admin/skills/AdminCandidateDetailPanel'
import { UploadSkillModal } from '@/components/admin/skills/UploadSkillModal'
import { useAdminSkills } from '@/hooks/useAdminSkills'

type Selection =
  | { kind: 'skill'; id: string }
  | { kind: 'candidate'; candidateId: string }

export default function SkillsPage() {
  const t = useTranslations('admin')
  const [filters, setFilters] = useState<SkillFilters>({})
  const [selection, setSelection] = useState<Selection | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  const { skills, loading, error, refresh } = useAdminSkills(
    filters.externalOnly ? {} : filters
  )
  const search = useAdminSkillsStore((s) => s.search)
  const candidates = useAdminSkillsStore((s) => s.candidates)
  const searching = useAdminSkillsStore((s) => s.searching)
  const lastInstalled = useAdminSkillsStore((s) => s.lastInstalled)

  useEffect(() => {
    document.title = 'Skills'
  }, [])

  useEffect(() => {
    if (lastInstalled) void refresh()
  }, [lastInstalled]) // eslint-disable-line react-hooks/exhaustive-deps

  const selectedSkill =
    selection?.kind === 'skill' ? (skills.find((s) => s.id === selection.id) ?? null) : null
  const selectedCandidate =
    selection?.kind === 'candidate'
      ? (candidates.find((c) => c.candidate_id === selection.candidateId) ?? null)
      : null

  const externalOnly = filters.externalOnly ?? false

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <h2 className="text-lg font-semibold tracking-tight">{t('skills')}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('skillsSubtitle')}</p>
      </header>

      <SkillsToolbar
        filters={filters}
        onFiltersChange={(next) => {
          setFilters(next)
          setSelection(null)
        }}
        onUploadClick={() => setUploadOpen(true)}
        onExternalSearch={(q) => void search(q)}
      />

      <div className="flex flex-1 overflow-hidden">
        <aside
          aria-label="skills-list"
          className="w-[360px] shrink-0 overflow-y-auto border-r border-border/70 bg-card/20"
        >
          <SkillsList
            skills={skills}
            loading={loading}
            error={error}
            selectedId={selection?.kind === 'skill' ? selection.id : null}
            onSelect={(id) => setSelection({ kind: 'skill', id })}
            candidates={candidates}
            searching={searching}
            externalOnly={externalOnly}
            selectedCandidateId={selection?.kind === 'candidate' ? selection.candidateId : null}
            onSelectCandidate={(id) => setSelection({ kind: 'candidate', candidateId: id })}
          />
        </aside>

        <section className="flex flex-1 overflow-y-auto">
          {selectedCandidate ? (
            <AdminCandidateDetailPanel
              candidate={selectedCandidate}
              onInstalled={() => void refresh()}
            />
          ) : (
            <SkillDetailPanel
              skillId={selection?.kind === 'skill' ? selection.id : null}
              onActionDone={() => void refresh()}
            />
          )}
        </section>
      </div>

      <UploadSkillModal
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onUploaded={() => void refresh()}
      />
    </div>
  )
}
```

- [ ] **Step 6: Run lint + full pre-commit check**

```bash
cd /home/chris/cubeplex/.worktrees/feat/skillssh-source/frontend
pnpm -r run lint
cd ..
git add -A
# run pre-commit manually:
cd backend && uv run mypy cubeplex/
```
Expected: 0 errors in both.

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/web/components/admin/skills/SkillsToolbar.tsx \
        frontend/packages/web/components/admin/skills/SkillsList.tsx \
        frontend/packages/web/app/admin/skills/page.tsx \
        frontend/packages/web/messages/en.json \
        frontend/packages/web/messages/zh.json \
        frontend/packages/core/src/types/skills.ts
git commit -m "feat(admin/skills): wire external registry search + install into admin skills page"
```

---

## Self-Review

**Spec coverage:**
- ✅ Admin discovers external skills via `/admin/skills/discover`
- ✅ Admin previews SKILL.md via `/admin/skills/discover/preview`
- ✅ Admin installs to org catalog (not workspace-private) via `/admin/skills/install-candidate`
- ✅ Install lands in catalog first — no auto-enable (auto_bind=False)
- ✅ Separate admin routes (not parameterized ws routes)
- ✅ Trust tier enforcement inherited from `_install_remote`
- ✅ `imported_from_registry_id` + `imported_from_source_ref` recorded on install
- ✅ Admin page shows External tab + search + candidates list + detail panel

**Placeholder scan:** No TBDs, no "handle edge cases", all code provided.

**Type consistency:**
- `useAdminSkillsStore` — `search(q: string)` called in page.tsx ✅
- `AdminCandidateDetailPanel` props: `candidate: SkillCandidateOut, onInstalled: () => void` — matched in page.tsx ✅
- `SkillsList` new props all wired in page.tsx ✅
- `SkillsToolbar.onExternalSearch: (q: string) => void` — called in page.tsx ✅
- Backend: `workspace_id=None` threaded through `build()`, `SkillInstallService`, and both `_install_local`/`_install_remote` paths ✅
