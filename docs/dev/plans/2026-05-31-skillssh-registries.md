# Skill Registries — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename "source" → "registry/adapter" throughout, add a SkillsShAdapter for skills.sh discovery, and build the admin Skill Registries management page.

**Architecture:** Three independent phases: (A) rename existing types atomically keeping mypy green at each commit, (B) implement SkillsShAdapter with TDD, (C) build the frontend admin page following the admin/skills pattern.

**Tech Stack:** Python / FastAPI / SQLModel / Alembic / httpx — Next.js / React / SWR / Tailwind

---

## Phase A — Rename

> **Working directory for all Phase A and Phase B commands:** `backend/`
> Unless stated otherwise, run every shell command from the `backend/` directory:
> ```bash
> cd /home/chris/cubeplex/.worktrees/feat/skillssh-source/backend
> ```

### Task 1: DB migration — rename `skill_sources` → `skill_registries`

**Files:**
- Create: `backend/alembic/versions/<rev>_rename_skill_sources_to_skill_registries.py`

- [ ] **Step 1: Generate empty migration**

```bash
uv run alembic revision -m "rename skill_sources to skill_registries"
```

Copy the generated filename (e.g. `abc123_rename_skill_sources_to_skill_registries.py`).

- [ ] **Step 2: Write upgrade/downgrade**

Open the generated file and replace the empty `upgrade`/`downgrade` with:

```python
def upgrade() -> None:
    op.rename_table("skill_sources", "skill_registries")


def downgrade() -> None:
    op.rename_table("skill_registries", "skill_sources")
```

- [ ] **Step 3: Apply migration**

```bash
uv run alembic upgrade head
```

Expected: `Running upgrade ... -> <rev>, rename skill_sources to skill_registries`

- [ ] **Step 4: Verify**

```bash
uv run python -c "
import asyncio
from sqlalchemy import text
from cubeplex.db.engine import async_session_maker
async def main():
    async with async_session_maker() as s:
        r = await s.execute(text(\"SELECT tablename FROM pg_tables WHERE tablename='skill_registries'\"))
        print('found:', r.scalar())
asyncio.run(main())
"
```

Expected: `found: skill_registries`

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/
git commit -m "chore(db): rename skill_sources table to skill_registries"
```

---

### Task 2: Rename model + repository

**Files:**
- Rename: `backend/cubeplex/models/skill_source.py` → `skill_registry.py`
- Rename: `backend/cubeplex/repositories/skill_source.py` → `skill_registry.py`
- Modify: `backend/cubeplex/models/__init__.py`
- Modify: `backend/alembic/env.py`
- Modify: `backend/cubeplex/api/routes/v1/admin_skill_sources.py` (import only, class rename in Task 4)

- [ ] **Step 1: Rename + rewrite model file**

```bash
# from backend/
git mv cubeplex/models/skill_source.py cubeplex/models/skill_registry.py
```

Replace entire content of `cubeplex/models/skill_registry.py`:

```python
"""Registered remote skill registries (org-scoped admin config)."""

from typing import ClassVar

from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase


class SkillRegistry(CubeplexBase, table=True):
    """A remote registry an org admin registered for skill discovery.

    The built-in local catalog adapter is implicit (always present) and has no
    row here — only admin-configured registries are persisted.
    """

    _PREFIX: ClassVar[str] = "sksrc"
    __tablename__ = "skill_registries"

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    name: str = Field(max_length=128)
    kind: str = Field(max_length=16, default="remote")
    base_url: str = Field(max_length=512)
    repo: str | None = Field(default=None, max_length=256)
    trust_tier: str = Field(max_length=16, default="untrusted")
    enabled: bool = Field(default=True)
    created_by_user_id: str = Field(foreign_key="users.id", max_length=20)
```

- [ ] **Step 2: Rename + rewrite repository file**

```bash
# from backend/
git mv cubeplex/repositories/skill_source.py cubeplex/repositories/skill_registry.py
```

Replace entire content of `cubeplex/repositories/skill_registry.py`:

```python
"""Repository for org-configured skill registries."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import SkillRegistry


class SkillRegistryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        org_id: str,
        name: str,
        kind: str,
        base_url: str,
        repo: str | None,
        trust_tier: str,
        created_by_user_id: str,
    ) -> SkillRegistry:
        row = SkillRegistry(
            org_id=org_id,
            name=name,
            kind=kind,
            base_url=base_url,
            repo=repo,
            trust_tier=trust_tier,
            created_by_user_id=created_by_user_id,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def get(self, org_id: str, registry_id: str) -> SkillRegistry | None:
        row = await self.session.get(SkillRegistry, registry_id)
        if row is None or row.org_id != org_id:
            return None
        return row

    async def list_for_org(
        self, org_id: str, *, enabled_only: bool = False
    ) -> list[SkillRegistry]:
        stmt = select(SkillRegistry).where(SkillRegistry.org_id == org_id)  # type: ignore[arg-type]
        if enabled_only:
            stmt = stmt.where(SkillRegistry.enabled.is_(True))  # type: ignore[attr-defined]
        stmt = stmt.order_by(SkillRegistry.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def set_enabled(self, org_id: str, registry_id: str, enabled: bool) -> bool:
        row = await self.get(org_id, registry_id)
        if row is None:
            return False
        row.enabled = enabled
        await self.session.commit()
        return True

    async def set_trust_tier(self, org_id: str, registry_id: str, trust_tier: str) -> bool:
        row = await self.get(org_id, registry_id)
        if row is None:
            return False
        row.trust_tier = trust_tier
        await self.session.commit()
        return True

    async def delete(self, org_id: str, registry_id: str) -> bool:
        row = await self.get(org_id, registry_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.commit()
        return True
```

- [ ] **Step 3: Update `cubeplex/models/__init__.py`**

Find the line that exports `SkillSource` and replace with `SkillRegistry`:

```python
# Before:
from cubeplex.models.skill_source import SkillSource

# After:
from cubeplex.models.skill_registry import SkillRegistry
```

Also update the `__all__` list if it exists: replace `"SkillSource"` → `"SkillRegistry"`.

- [ ] **Step 4: Update `alembic/env.py`**

Find `from cubeplex.models.skill_source import SkillSource` (or the models import block) and replace with:

```python
from cubeplex.models.skill_registry import SkillRegistry  # noqa: F401
```

Or if the file imports from `cubeplex.models` directly, no change needed — the __init__.py update handles it.

- [ ] **Step 5: Update `admin_skill_sources.py` import**

In `cubeplex/api/routes/v1/admin_skill_sources.py`, replace:

```python
# Before:
from cubeplex.models import SkillSource
from cubeplex.repositories.skill_source import SkillSourceRepository

# After:
from cubeplex.models import SkillRegistry
from cubeplex.repositories.skill_registry import SkillRegistryRepository
```

Also replace all occurrences of `SkillSource(` with `SkillRegistry(` and `SkillSourceRepository(` with `SkillRegistryRepository(` in that file (do NOT rename the route or request/response classes yet — that's Task 4).

- [ ] **Step 6: Verify mypy passes**

```bash
cd backend && uv run mypy cubeplex/
```

Expected: `Success: no issues found in N source files`

- [ ] **Step 7: Run existing skill-source admin tests**

```bash
uv run pytest tests/e2e/test_skill_sources_admin.py -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add cubeplex/models/skill_registry.py cubeplex/models/__init__.py \
        cubeplex/repositories/skill_registry.py \
        cubeplex/api/routes/v1/admin_skill_sources.py \
        alembic/env.py
git commit -m "refactor(models): rename SkillSource→SkillRegistry, SkillSourceRepository→SkillRegistryRepository"
```

---

### Task 3: Rename Protocol + adapters + container

**Files:**
- Modify: `backend/cubeplex/skills/sources/base.py`
- Modify: `backend/cubeplex/skills/sources/local.py`
- Modify: `backend/cubeplex/skills/sources/remote.py`
- Modify: `backend/cubeplex/skills/sources/registry.py`
- Modify: `backend/cubeplex/skills/discovery.py`
- Modify: `backend/cubeplex/streams/run_manager.py`
- Modify: `backend/cubeplex/api/routes/v1/ws_skills.py`
- Modify: `backend/cubeplex/api/routes/v1/conversations.py`
- Modify: `backend/tests/e2e/conftest.py`
- Modify: `backend/tests/e2e/test_skill_discovery_remote.py`
- Modify: `backend/tests/unit/test_remote_registry_source.py` (rename file too)
- Modify: `backend/tests/unit/test_skill_discovery_ranking.py`

- [ ] **Step 1: Update `sources/base.py`**

Replace `class SkillSource(Protocol)` → `class SkillRegistryAdapter(Protocol)`:

```python
class SkillRegistryAdapter(Protocol):
    kind: SourceKind

    async def search(self, query: str, *, limit: int) -> list[SkillCandidate]: ...

    async def fetch(self, source_ref: str) -> dict[str, bytes]:
        """Return {rel_path: bytes} of the skill bundle for import."""
        ...
```

- [ ] **Step 2: Update `sources/local.py`**

Replace `class LocalCatalogSource` → `class LocalCatalogAdapter`. Update the import if `SkillSource` protocol is referenced:

```python
# Before: class LocalCatalogSource:
# After:
class LocalCatalogAdapter:
    kind: SourceKind = "local"
    # rest of class unchanged
```

- [ ] **Step 3: Update `sources/remote.py`**

Replace `class RemoteRegistrySource` → `class RemoteRegistryAdapter`:

```python
# Before: class RemoteRegistrySource:
# After:
class RemoteRegistryAdapter:
    kind: SourceKind = "remote"
    # rest of class unchanged
```

- [ ] **Step 4: Rewrite `sources/registry.py`**

```python
"""Assembles the live SkillRegistryAdapter set for an (org, workspace)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.repositories.skill_registry import SkillRegistryRepository
from cubeplex.skills.service import SkillCatalogService
from cubeplex.skills.sources.base import SkillRegistryAdapter, TrustTier
from cubeplex.skills.sources.local import LocalCatalogAdapter
from cubeplex.skills.sources.remote import RemoteRegistryAdapter


class SkillsAdapterManager:
    def __init__(self, adapters: list[SkillRegistryAdapter]) -> None:
        self._adapters = adapters

    @property
    def adapters(self) -> list[SkillRegistryAdapter]:
        return self._adapters

    def adapter_by_id(self, source_id: str) -> SkillRegistryAdapter | None:
        """Return the enabled remote adapter with this registry row id, or None."""
        for a in self._adapters:
            if a.kind == "remote" and getattr(a, "source_id", None) == source_id:
                return a
        return None

    @classmethod
    async def build(
        cls,
        *,
        session: AsyncSession,
        catalog: SkillCatalogService,
        org_id: str,
        org_slug: str,
        workspace_id: str,
    ) -> SkillsAdapterManager:
        adapters: list[SkillRegistryAdapter] = [
            LocalCatalogAdapter(
                session=session,
                catalog=catalog,
                org_id=org_id,
                workspace_id=workspace_id,
            )
        ]
        rows = await SkillRegistryRepository(session).list_for_org(
            org_id, enabled_only=True
        )
        for row in rows:
            adapters.append(
                RemoteRegistryAdapter(
                    source_id=row.id,
                    base_url=row.base_url,
                    trust_tier=TrustTier(row.trust_tier),
                    org_slug=org_slug,
                    source_name=row.name,
                    repo=row.repo,
                )
            )
        return cls(adapters)
```

- [ ] **Step 5: Update all remaining import sites**

For each file, replace old name → new name. Run this grep to see exact lines:

```bash
grep -rn "SkillSourceRegistry\|RemoteRegistrySource\|LocalCatalogSource\|SkillSource\b" \
  cubeplex/ tests/ --include="*.py" | grep -v '.venv'
# (run from backend/ — cubeplex/ and tests/ are subdirs of backend/)
```

Make replacements:
- `SkillSourceRegistry` → `SkillsAdapterManager`
- `RemoteRegistrySource` → `RemoteRegistryAdapter`
- `LocalCatalogSource` → `LocalCatalogAdapter`
- `SkillSource` (Protocol only, not the model) → `SkillRegistryAdapter`
- `remote_source_by_id` → `adapter_by_id`
- `registry.sources` → `registry.adapters` (if used in discovery.py)

Key files to check: `discovery.py`, `run_manager.py`, `ws_skills.py`, `conversations.py`, `tests/e2e/conftest.py`, `tests/e2e/test_skill_discovery_remote.py`, `tests/unit/test_skill_discovery_ranking.py`.

- [ ] **Step 6: Rename test file**

```bash
# from backend/
git mv tests/unit/test_remote_registry_source.py tests/unit/test_remote_registry_adapter.py
```

Update the import inside: `from cubeplex.skills.sources.remote import RemoteRegistrySource` → `RemoteRegistryAdapter`, update all usages.

- [ ] **Step 7: Verify mypy + tests**

```bash
uv run mypy cubeplex/ && uv run pytest tests/unit/ -v -x
```

Expected: mypy clean, all unit tests pass.

- [ ] **Step 8: Commit**

```bash
git add cubeplex/skills/sources/ cubeplex/skills/discovery.py \
        cubeplex/streams/run_manager.py \
        cubeplex/api/routes/v1/ws_skills.py \
        cubeplex/api/routes/v1/conversations.py \
        tests/
git commit -m "refactor(skills): rename source→registry/adapter throughout (SkillsAdapterManager, SkillRegistryAdapter, LocalCatalogAdapter, RemoteRegistryAdapter)"
```

---

### Task 4: Rename + update admin API route

**Files:**
- Rename: `backend/cubeplex/api/routes/v1/admin_skill_sources.py` → `admin_skill_registries.py`
- Modify: `backend/cubeplex/api/app.py`
- Modify: `backend/cubeplex/api/routes/v1/__init__.py`
- Rename: `backend/tests/e2e/test_skill_sources_admin.py` → `test_skill_registries_admin.py`

- [ ] **Step 1: Rename the route file**

```bash
# from backend/
git mv cubeplex/api/routes/v1/admin_skill_sources.py \
       cubeplex/api/routes/v1/admin_skill_registries.py
```

- [ ] **Step 2: Rewrite the route file**

Replace the entire content of `admin_skill_registries.py`:

```python
"""Org-admin management of skill registries (/admin/skill-registries)."""

from __future__ import annotations

import ipaddress
import socket
from typing import Annotated, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.context import RequestContext
from cubeplex.db import get_session
from cubeplex.mcp.dependencies import get_admin_request_context
from cubeplex.models import SkillRegistry
from cubeplex.repositories.skill_registry import SkillRegistryRepository

router = APIRouter(prefix="/admin/skill-registries", tags=["admin-skill-registries"])

_TRUST_TIERS = {"official", "community", "untrusted"}
_VALID_KINDS = {"remote", "skills-sh"}

_FORBIDDEN_HOSTNAMES = {
    "localhost", "ip6-localhost", "ip6-loopback",
    "metadata", "metadata.google.internal",
}
_FORBIDDEN_HOSTNAME_SUFFIXES = (".local", ".internal", ".localdomain")

_SKILLS_SH_BASE_URL = "https://skills.sh"


def _validate_registry_base_url(raw: str) -> None:
    # (copy entire function body from old admin_skill_sources.py unchanged)
    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="BAD_BASE_URL") from exc
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="BAD_BASE_URL")
    host = (parsed.hostname or "").lower()
    if not host:
        raise HTTPException(status_code=400, detail="BAD_BASE_URL")
    if host in _FORBIDDEN_HOSTNAMES:
        raise HTTPException(status_code=400, detail="BAD_BASE_URL")
    if any(host.endswith(suf) for suf in _FORBIDDEN_HOSTNAME_SUFFIXES):
        raise HTTPException(status_code=400, detail="BAD_BASE_URL")
    try:
        packed_v4 = socket.inet_aton(host)
    except OSError:
        packed_v4 = None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="BAD_BASE_URL") from exc
    if packed_v4 is not None:
        canonical_v4 = ipaddress.IPv4Address(packed_v4)
        if host != str(canonical_v4) or not canonical_v4.is_global:
            raise HTTPException(status_code=400, detail="BAD_BASE_URL")
        return
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if not ip.is_global:
        raise HTTPException(status_code=400, detail="BAD_BASE_URL")


class CreateSkillRegistryRequest(BaseModel):
    name: str
    kind: Literal["remote", "skills-sh"] = "remote"
    base_url: str = ""
    repo: str | None = None
    trust_tier: str = "untrusted"


class PatchSkillRegistryRequest(BaseModel):
    enabled: bool | None = None
    trust_tier: str | None = None


class SkillRegistryResponse(BaseModel):
    id: str
    name: str
    kind: str
    base_url: str
    repo: str | None
    trust_tier: str
    enabled: bool


def _to_response(row: SkillRegistry) -> SkillRegistryResponse:
    return SkillRegistryResponse(
        id=row.id,
        name=row.name,
        kind=row.kind,
        base_url=row.base_url,
        repo=row.repo,
        trust_tier=row.trust_tier,
        enabled=row.enabled,
    )


@router.post("", status_code=201, response_model=SkillRegistryResponse)
async def create_registry(
    body: CreateSkillRegistryRequest,
    *,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillRegistryResponse:
    if body.trust_tier not in _TRUST_TIERS:
        raise HTTPException(status_code=400, detail="BAD_TRUST_TIER")
    if body.kind not in _VALID_KINDS:
        raise HTTPException(status_code=400, detail="BAD_KIND")
    if body.kind == "skills-sh":
        base_url = _SKILLS_SH_BASE_URL
    else:
        base_url = body.base_url
        _validate_registry_base_url(base_url)
    row = await SkillRegistryRepository(session).create(
        org_id=ctx.org_id,
        name=body.name,
        kind=body.kind,
        base_url=base_url,
        repo=body.repo,
        trust_tier=body.trust_tier,
        created_by_user_id=ctx.user.id,
    )
    return _to_response(row)


@router.get("", response_model=list[SkillRegistryResponse])
async def list_registries(
    *,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[SkillRegistryResponse]:
    rows = await SkillRegistryRepository(session).list_for_org(ctx.org_id)
    return [_to_response(r) for r in rows]


@router.patch("/{registry_id}", response_model=SkillRegistryResponse)
async def patch_registry(
    registry_id: str,
    body: PatchSkillRegistryRequest,
    *,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillRegistryResponse:
    if body.trust_tier is not None and body.trust_tier not in _TRUST_TIERS:
        raise HTTPException(status_code=400, detail="BAD_TRUST_TIER")
    repo = SkillRegistryRepository(session)
    if body.enabled is not None:
        if not await repo.set_enabled(ctx.org_id, registry_id, body.enabled):
            raise HTTPException(status_code=404, detail="REGISTRY_NOT_FOUND")
    if body.trust_tier is not None:
        if not await repo.set_trust_tier(ctx.org_id, registry_id, body.trust_tier):
            raise HTTPException(status_code=404, detail="REGISTRY_NOT_FOUND")
    row = await repo.get(ctx.org_id, registry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="REGISTRY_NOT_FOUND")
    return _to_response(row)


@router.delete("/{registry_id}", status_code=204)
async def delete_registry(
    registry_id: str,
    *,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    deleted = await SkillRegistryRepository(session).delete(ctx.org_id, registry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="REGISTRY_NOT_FOUND")
```

- [ ] **Step 3: Update `api/app.py` and `routes/v1/__init__.py`**

In `app.py`, replace the old import and include:
```python
# Before:
from cubeplex.api.routes.v1.admin_skill_sources import router as admin_skill_sources_router
app.include_router(admin_skill_sources_router, ...)

# After:
from cubeplex.api.routes.v1.admin_skill_registries import router as admin_skill_registries_router
app.include_router(admin_skill_registries_router, ...)
```

Similarly in `routes/v1/__init__.py` if it re-exports the router.

- [ ] **Step 4: Rename test file + update**

```bash
# from backend/
git mv tests/e2e/test_skill_sources_admin.py tests/e2e/test_skill_registries_admin.py
```

Inside the file, update all imports from `admin_skill_sources` → `admin_skill_registries`, class names, and endpoint paths from `/admin/skill-sources` → `/admin/skill-registries`.

- [ ] **Step 5: Verify mypy + admin tests**

```bash
uv run mypy cubeplex/ && uv run pytest tests/e2e/test_skill_registries_admin.py -v
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add cubeplex/api/ tests/e2e/test_skill_registries_admin.py
git commit -m "refactor(api): rename admin/skill-sources→skill-registries; add kind, DELETE endpoint"
```

---

## Phase B — SkillsShAdapter

> **Working directory for all Phase B commands:** `backend/`
> ```bash
> cd /home/chris/cubeplex/.worktrees/feat/skillssh-source/backend
> ```

### Task 5: Config block

**Files:**
- Modify: `backend/config.yaml`

- [ ] **Step 1: Add registry config section**

In `config.yaml` under the `default:` block, after the existing `skills:` section, add:

```yaml
  registry:
    skills_sh:
      github_token: ""  # optional: raises GitHub API rate limit 60→5000 req/h
```

- [ ] **Step 2: Verify config loads**

```bash
uv run python -c "
from cubeplex.config import config
print(config.get('registry.skills_sh.github_token', 'NOT_SET'))
"
```

Expected: `` (empty string, not an error)

- [ ] **Step 3: Commit**

```bash
git add config.yaml
git commit -m "chore(config): add registry.skills_sh.github_token config block"
```

---

### Task 6: SkillsShAdapter — tests first

**Files:**
- Create: `backend/tests/unit/test_skills_sh_adapter.py`

- [ ] **Step 1: Write the test file**

```python
"""Unit tests for SkillsShAdapter using httpx.MockTransport."""

from __future__ import annotations

import httpx
import pytest

from cubeplex.skills.sources.base import TrustTier, decode_candidate_id
from cubeplex.skills.sources.skills_sh import SkillsShAdapter


def _make_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        # skills.sh search
        if "skills.sh/api/search" in url:
            return httpx.Response(
                200,
                json={
                    "skills": [
                        {
                            "name": "frontend-design",
                            "id": "frontend-design",
                            "source": "vercel-labs/skills",
                            "installs": 850,
                        }
                    ]
                },
            )

        # GitHub repo metadata (default_branch)
        if "api.github.com/repos/vercel-labs/skills" in url and "git/trees" not in url:
            return httpx.Response(200, json={"default_branch": "main"})

        # GitHub tree
        if "api.github.com/repos/vercel-labs/skills/git/trees/main" in url:
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {"path": "frontend-design/SKILL.md", "type": "blob"},
                        {"path": "frontend-design/references/guide.md", "type": "blob"},
                        {"path": "other-skill/SKILL.md", "type": "blob"},
                    ]
                },
            )

        # GitHub raw files
        if "raw.githubusercontent.com" in url:
            if url.endswith("SKILL.md"):
                return httpx.Response(
                    200,
                    text=(
                        "---\nname: frontend-design\n"
                        "description: Build UIs\nversion: 1.2.0\n---\n# Frontend\n"
                    ),
                )
            if url.endswith("references/guide.md"):
                return httpx.Response(200, text="# Guide\n")

        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def adapter() -> SkillsShAdapter:
    return SkillsShAdapter(
        source_id="sksrc-test-1",
        trust_tier=TrustTier.community,
        source_name="skills.sh",
        github_token=None,
        transport=_make_transport(),
    )


@pytest.mark.asyncio
async def test_search_returns_candidates(adapter: SkillsShAdapter) -> None:
    results = await adapter.search("frontend", limit=5)
    assert len(results) == 1
    c = results[0]
    assert c.name == "frontend-design"
    assert c.trust == TrustTier.community
    assert c.source_name == "skills.sh"
    assert c.install_count == 850


@pytest.mark.asyncio
async def test_search_encodes_branch_in_source_ref(adapter: SkillsShAdapter) -> None:
    results = await adapter.search("frontend", limit=5)
    kind, source_id, source_ref = decode_candidate_id(results[0].candidate_id)
    assert kind == "remote"
    assert source_id == "sksrc-test-1"
    # source_ref encodes branch resolved at search time
    assert source_ref == "vercel-labs/skills/main/frontend-design"


@pytest.mark.asyncio
async def test_search_returns_empty_on_api_error() -> None:
    def fail_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    bad_adapter = SkillsShAdapter(
        source_id="sksrc-x",
        trust_tier=TrustTier.untrusted,
        source_name="skills.sh",
        github_token=None,
        transport=httpx.MockTransport(fail_handler),
    )
    results = await bad_adapter.search("anything", limit=5)
    assert results == []


@pytest.mark.asyncio
async def test_fetch_downloads_skill_files(adapter: SkillsShAdapter) -> None:
    files = await adapter.fetch("vercel-labs/skills/main/frontend-design")
    assert "SKILL.md" in files
    assert b"Frontend" in files["SKILL.md"]
    assert "references/guide.md" in files
    # files from other skills must not appear
    assert not any("other-skill" in k for k in files)


@pytest.mark.asyncio
async def test_fetch_raises_on_missing_skill_md() -> None:
    def no_skill_md(request: httpx.Request) -> httpx.Response:
        if "git/trees" in str(request.url):
            return httpx.Response(200, json={"tree": []})
        return httpx.Response(200, json={"default_branch": "main"})

    bad = SkillsShAdapter(
        source_id="x",
        trust_tier=TrustTier.untrusted,
        source_name="s",
        github_token=None,
        transport=httpx.MockTransport(no_skill_md),
    )
    with pytest.raises(ValueError, match="SKILL.md"):
        await bad.fetch("owner/repo/main/slug")
```

- [ ] **Step 2: Run tests — expect ImportError (module doesn't exist yet)**

```bash
cd backend && uv run pytest tests/unit/test_skills_sh_adapter.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'cubeplex.skills.sources.skills_sh'`

- [ ] **Step 3: Commit the tests**

```bash
git add tests/unit/test_skills_sh_adapter.py
git commit -m "test(skills-sh): add SkillsShAdapter unit tests (red)"
```

---

### Task 7: SkillsShAdapter — implementation

**Files:**
- Create: `backend/cubeplex/skills/sources/skills_sh.py`

- [ ] **Step 1: Create the implementation**

```python
"""SkillsShAdapter — connects skill discovery to the skills.sh public registry."""

from __future__ import annotations

import json

import httpx

from cubeplex.skills.sources.base import (
    SkillCandidate,
    SkillRegistryAdapter,
    SourceKind,
    TrustTier,
    encode_candidate_id,
)

_SKILLS_SH_BASE = "https://skills.sh"
_GITHUB_API_BASE = "https://api.github.com"
_GITHUB_RAW_BASE = "https://raw.githubusercontent.com"

_RAW_FILE_MAX_BYTES = 10 * 1024 * 1024   # 10 MB
_BUNDLE_MAX_BYTES = 50 * 1024 * 1024     # 50 MB


class SkillsShAdapter:
    """Adapter that searches skills.sh and fetches skill files from GitHub."""

    kind: SourceKind = "remote"

    def __init__(
        self,
        *,
        source_id: str,
        trust_tier: TrustTier,
        source_name: str,
        github_token: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.source_id = source_id
        self._trust = trust_tier
        self._source_name = source_name
        self._github_token = github_token
        self._transport = transport

    def _skills_sh_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=_SKILLS_SH_BASE,
            transport=self._transport,
            timeout=15.0,
        )

    def _github_client(self) -> httpx.AsyncClient:
        headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
        if self._github_token:
            headers["Authorization"] = f"Bearer {self._github_token}"
        return httpx.AsyncClient(
            base_url=_GITHUB_API_BASE,
            headers=headers,
            transport=self._transport,
            timeout=15.0,
        )

    def _raw_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=_GITHUB_RAW_BASE,
            transport=self._transport,
            timeout=30.0,
        )

    async def _resolve_default_branch(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> str:
        resp = await client.get(f"/repos/{owner}/{repo}")
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("default_branch") or "main")

    async def search(self, query: str, *, limit: int) -> list[SkillCandidate]:
        try:
            return await self._search(query, limit=limit)
        except Exception:  # noqa: BLE001
            return []

    async def _search(self, query: str, *, limit: int) -> list[SkillCandidate]:
        async with self._skills_sh_client() as sh_client:
            resp = await sh_client.get(
                "/api/search", params={"q": query, "limit": limit}
            )
            if not resp.is_success:
                return []
            data = resp.json()

        skills = data.get("skills", [])
        if not isinstance(skills, list):
            return []

        # Resolve default branch once per unique owner/repo
        repos: dict[str, str] = {}
        async with self._github_client() as gh_client:
            for item in skills:
                source = item.get("source", "")
                if source and source not in repos:
                    try:
                        parts = source.split("/", 1)
                        if len(parts) == 2:
                            repos[source] = await self._resolve_default_branch(
                                gh_client, parts[0], parts[1]
                            )
                    except Exception:  # noqa: BLE001
                        repos[source] = "main"

        out: list[SkillCandidate] = []
        for item in skills:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("id") or item.get("name") or "")
            source = str(item.get("source") or "")
            if not slug or not source:
                continue
            branch = repos.get(source, "main")
            source_ref = f"{source}/{branch}/{slug}"
            out.append(
                SkillCandidate(
                    candidate_id=encode_candidate_id(
                        "remote", source_ref, source_id=self.source_id
                    ),
                    name=slug,
                    canonical_name=slug,
                    description=str(item.get("description") or ""),
                    source_kind="remote",
                    source_ref=source_ref,
                    trust=self._trust,
                    install_state="available",
                    install_count=item.get("installs"),
                    source_name=self._source_name,
                    repo=f"https://github.com/{source}",
                )
            )
        return out

    async def fetch(self, source_ref: str) -> dict[str, bytes]:
        parts = source_ref.split("/", 3)
        if len(parts) != 4:
            raise ValueError(f"invalid skills-sh source_ref: {source_ref!r}")
        owner, repo, branch, slug = parts

        async with self._github_client() as gh_client:
            tree_resp = await gh_client.get(
                f"/repos/{owner}/{repo}/git/trees/{branch}",
                params={"recursive": "1"},
            )
            tree_resp.raise_for_status()
            tree_data = tree_resp.json()

        entries = tree_data.get("tree", [])
        prefix = f"{slug}/"
        rel_paths = [
            e["path"][len(prefix):]
            for e in entries
            if isinstance(e, dict)
            and e.get("type") == "blob"
            and str(e.get("path", "")).startswith(prefix)
        ]

        files: dict[str, bytes] = {}
        bundle_total = 0

        async with self._raw_client() as raw_client:
            for rel in rel_paths:
                resp = await raw_client.get(f"/{owner}/{repo}/{branch}/{slug}/{rel}")
                resp.raise_for_status()
                content = resp.content
                if len(content) > _RAW_FILE_MAX_BYTES:
                    raise ValueError(
                        f"file {rel!r} exceeds {_RAW_FILE_MAX_BYTES} byte limit"
                    )
                bundle_total += len(content)
                if bundle_total > _BUNDLE_MAX_BYTES:
                    raise ValueError(
                        f"skill bundle exceeds {_BUNDLE_MAX_BYTES} byte limit"
                    )
                files[rel] = content

        if "SKILL.md" not in files:
            raise ValueError(f"skills-sh skill {slug!r} has no SKILL.md")
        return files
```

- [ ] **Step 2: Verify `SkillsShAdapter` satisfies the protocol**

```bash
uv run python -c "
from cubeplex.skills.sources.skills_sh import SkillsShAdapter
from cubeplex.skills.sources.base import SkillRegistryAdapter
# structural check
a: SkillRegistryAdapter = SkillsShAdapter(
    source_id='x', trust_tier='community',
    source_name='test', github_token=None
)
print('protocol check passed')
"
```

Expected: `protocol check passed`

- [ ] **Step 3: Run tests — expect all green**

```bash
uv run pytest tests/unit/test_skills_sh_adapter.py -v
```

Expected: 5 tests pass.

- [ ] **Step 4: Run mypy**

```bash
uv run mypy cubeplex/skills/sources/skills_sh.py
```

Expected: `Success`

- [ ] **Step 5: Commit**

```bash
git add cubeplex/skills/sources/skills_sh.py
git commit -m "feat(skills-sh): implement SkillsShAdapter (search + fetch via GitHub)"
```

---

### Task 8: Wire SkillsShAdapter into SkillsAdapterManager

**Files:**
- Modify: `backend/cubeplex/skills/sources/registry.py`

- [ ] **Step 1: Update `build()` in `SkillsAdapterManager`**

Add the import and the `skills-sh` branch in `build()`:

```python
from cubeplex.config import config as _config
from cubeplex.skills.sources.skills_sh import SkillsShAdapter

# inside build():
for row in rows:
    if row.kind == "skills-sh":
        adapters.append(
            SkillsShAdapter(
                source_id=row.id,
                trust_tier=TrustTier(row.trust_tier),
                source_name=row.name,
                github_token=_config.get("registry.skills_sh.github_token") or None,
            )
        )
    else:
        adapters.append(
            RemoteRegistryAdapter(
                source_id=row.id,
                base_url=row.base_url,
                trust_tier=TrustTier(row.trust_tier),
                org_slug=org_slug,
                source_name=row.name,
                repo=row.repo,
            )
        )
```

- [ ] **Step 2: Run e2e admin tests to confirm wiring doesn't break existing flow**

```bash
uv run pytest tests/e2e/test_skill_registries_admin.py tests/e2e/test_skill_discovery_local.py -v
```

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add cubeplex/skills/sources/registry.py
git commit -m "feat(skills-sh): wire SkillsShAdapter into SkillsAdapterManager.build()"
```

---

## Phase C — Admin Skill Registries Page

> **Working directory for all Phase C commands:** `frontend/`
> ```bash
> cd /home/chris/cubeplex/.worktrees/feat/skillssh-source/frontend
> ```
> Git commits in Phase C still run from the **worktree root** (one level up):
> ```bash
> cd /home/chris/cubeplex/.worktrees/feat/skillssh-source && git add ... && git commit ...
> ```

### Task 9: Frontend data hook

**Files:**
- Create: `frontend/packages/web/hooks/useAdminSkillRegistries.ts`

- [ ] **Step 1: Create the hook**

```typescript
'use client'

import { useState } from 'react'
import useSWR from 'swr'
import { csrfHeaders, jsonHeaders, readApiError } from '@/lib/csrf'

export interface SkillRegistryEntry {
  id: string
  name: string
  kind: string
  base_url: string
  repo: string | null
  trust_tier: string
  enabled: boolean
}

export interface CreateRegistryBody {
  name: string
  kind: 'remote' | 'skills-sh'
  base_url?: string
  repo?: string | null
  trust_tier: string
}

export interface PatchRegistryBody {
  enabled?: boolean
  trust_tier?: string
}

async function fetcher(url: string): Promise<SkillRegistryEntry[]> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`skill registries fetch failed: ${res.status}`)
  return res.json() as Promise<SkillRegistryEntry[]>
}

export function useAdminSkillRegistries() {
  const [mutating, setMutating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { data, isLoading, mutate } = useSWR<SkillRegistryEntry[]>(
    '/api/v1/admin/skill-registries',
    fetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  async function create(body: CreateRegistryBody): Promise<SkillRegistryEntry | null> {
    setMutating(true)
    setError(null)
    try {
      const res = await fetch('/api/v1/admin/skill-registries', {
        method: 'POST',
        credentials: 'include',
        headers: jsonHeaders(),
        body: JSON.stringify(body),
      })
      if (!res.ok) { setError(await readApiError(res)); return null }
      const created = (await res.json()) as SkillRegistryEntry
      await mutate()
      return created
    } finally {
      setMutating(false)
    }
  }

  async function patch(id: string, body: PatchRegistryBody): Promise<boolean> {
    setMutating(true)
    setError(null)
    try {
      const res = await fetch(`/api/v1/admin/skill-registries/${id}`, {
        method: 'PATCH',
        credentials: 'include',
        headers: jsonHeaders(),
        body: JSON.stringify(body),
      })
      if (!res.ok) { setError(await readApiError(res)); return false }
      await mutate()
      return true
    } finally {
      setMutating(false)
    }
  }

  async function remove(id: string): Promise<boolean> {
    setMutating(true)
    setError(null)
    try {
      const res = await fetch(`/api/v1/admin/skill-registries/${id}`, {
        method: 'DELETE',
        credentials: 'include',
        headers: csrfHeaders(),
      })
      if (!res.ok) { setError(await readApiError(res)); return false }
      await mutate()
      return true
    } finally {
      setMutating(false)
    }
  }

  return {
    registries: data ?? [],
    loading: isLoading,
    mutating,
    error,
    create,
    patch,
    remove,
    refresh: mutate,
  }
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd frontend && pnpm --filter web tsc --noEmit 2>&1 | grep 'error TS'
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/hooks/useAdminSkillRegistries.ts
git commit -m "feat(admin): add useAdminSkillRegistries hook"
```

---

### Task 10: RegistryCard + RegistryDetailPanel components

**Files:**
- Create: `frontend/packages/web/components/admin/skill-registries/RegistryCard.tsx`
- Create: `frontend/packages/web/components/admin/skill-registries/RegistryDetailPanel.tsx`

- [ ] **Step 1: Create `RegistryCard.tsx`**

```tsx
'use client'

import { Database, Globe, ShieldCheck, ShieldAlert, ShieldOff } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { SkillRegistryEntry } from '@/hooks/useAdminSkillRegistries'

function KindBadge({ kind }: { kind: string }) {
  return (
    <span className="rounded-full bg-muted/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
      {kind === 'skills-sh' ? 'skills.sh' : 'Custom'}
    </span>
  )
}

function TrustBadge({ tier }: { tier: string }) {
  if (tier === 'official') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
        <ShieldCheck className="size-3" />Official
      </span>
    )
  }
  if (tier === 'community') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-blue-500/10 px-1.5 py-0.5 text-[10px] font-medium text-blue-600 dark:text-blue-400">
        <ShieldAlert className="size-3" />Community
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
      <ShieldOff className="size-3" />Unvetted
    </span>
  )
}

interface RegistryCardProps {
  registry: SkillRegistryEntry
  active: boolean
  onClick: () => void
}

export function RegistryCard({ registry, active, onClick }: RegistryCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? 'true' : undefined}
      className={cn(
        'group flex w-full flex-col gap-1.5 rounded-lg border p-3 text-left transition-all',
        active
          ? 'border-primary/40 bg-primary/5 shadow-sm'
          : 'border-border/70 bg-card/40 hover:border-border hover:bg-accent/40',
        !registry.enabled && 'opacity-60',
      )}
    >
      <div className="flex items-center gap-2">
        <Database className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="truncate text-sm font-semibold">{registry.name}</span>
        <KindBadge kind={registry.kind} />
      </div>
      <div className="flex flex-wrap items-center gap-1 pt-0.5">
        <TrustBadge tier={registry.trust_tier} />
        {!registry.enabled && (
          <span className="text-[10px] text-muted-foreground/70">disabled</span>
        )}
      </div>
    </button>
  )
}
```

- [ ] **Step 2: Create `RegistryDetailPanel.tsx`**

```tsx
'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Database, ShieldCheck, ShieldAlert, ShieldOff, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { cn } from '@/lib/utils'
import type { SkillRegistryEntry, PatchRegistryBody } from '@/hooks/useAdminSkillRegistries'

const TRUST_TIERS = ['official', 'community', 'untrusted'] as const
type TrustTier = (typeof TRUST_TIERS)[number]

interface RegistryDetailPanelProps {
  registry: SkillRegistryEntry
  onPatch: (id: string, body: PatchRegistryBody) => Promise<boolean>
  onDelete: (id: string) => Promise<boolean>
  mutating: boolean
  error: string | null
}

export function RegistryDetailPanel({
  registry,
  onPatch,
  onDelete,
  mutating,
  error,
}: RegistryDetailPanelProps) {
  const t = useTranslations('adminSkillRegistries')
  const [deleting, setDeleting] = useState(false)

  async function handleDelete() {
    setDeleting(true)
    await onDelete(registry.id)
    setDeleting(false)
  }

  return (
    <div className="flex flex-1 flex-col gap-6 p-6">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <Database className="size-4 shrink-0 text-muted-foreground" />
            <h3 className="text-base font-semibold">{registry.name}</h3>
          </div>
          <span className="text-xs text-muted-foreground">
            {registry.kind === 'skills-sh' ? 'skills.sh' : registry.base_url}
          </span>
        </div>
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive">
              <Trash2 className="size-3.5" />
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t('deleteTitle')}</AlertDialogTitle>
              <AlertDialogDescription>{t('deleteDescription', { name: registry.name })}</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>{t('cancel')}</AlertDialogCancel>
              <AlertDialogAction
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                onClick={handleDelete}
                disabled={deleting}
              >
                {t('delete')}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          {error}
        </div>
      )}

      <dl className="flex flex-col gap-4">
        {/* Enabled toggle */}
        <div className="flex items-center justify-between">
          <dt className="text-sm font-medium">{t('enabled')}</dt>
          <dd>
            <button
              type="button"
              role="switch"
              aria-checked={registry.enabled}
              disabled={mutating}
              onClick={() => void onPatch(registry.id, { enabled: !registry.enabled })}
              className={cn(
                'relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus-visible:outline-none',
                registry.enabled ? 'bg-primary' : 'bg-muted',
                mutating && 'opacity-50 cursor-not-allowed',
              )}
            >
              <span
                className={cn(
                  'inline-block size-3.5 rounded-full bg-white shadow transition-transform',
                  registry.enabled ? 'translate-x-4' : 'translate-x-0.5',
                )}
              />
            </button>
          </dd>
        </div>

        {/* Trust tier */}
        <div className="flex items-center justify-between">
          <dt className="text-sm font-medium">{t('trustTier')}</dt>
          <dd>
            <div className="inline-flex items-center gap-0.5 rounded-lg border border-border bg-muted/30 p-0.5">
              {TRUST_TIERS.map((tier) => (
                <button
                  key={tier}
                  type="button"
                  disabled={mutating}
                  onClick={() => {
                    if (tier !== registry.trust_tier)
                      void onPatch(registry.id, { trust_tier: tier })
                  }}
                  className={cn(
                    'rounded-md px-2.5 py-1 text-xs font-medium transition-colors capitalize',
                    tier === registry.trust_tier
                      ? 'bg-background text-foreground shadow-sm'
                      : 'text-muted-foreground hover:text-foreground',
                    mutating && 'opacity-50 cursor-not-allowed',
                  )}
                >
                  {tier}
                </button>
              ))}
            </div>
          </dd>
        </div>

        {/* Base URL (custom registries only) */}
        {registry.kind !== 'skills-sh' && (
          <div className="flex items-start gap-3">
            <dt className="w-20 shrink-0 pt-0.5 text-sm font-medium">URL</dt>
            <dd className="truncate text-xs text-muted-foreground">{registry.base_url}</dd>
          </div>
        )}
      </dl>
    </div>
  )
}
```

- [ ] **Step 3: TypeScript check**

```bash
cd frontend && pnpm --filter web tsc --noEmit 2>&1 | grep 'error TS'
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/admin/skill-registries/
git commit -m "feat(admin): add RegistryCard and RegistryDetailPanel components"
```

---

### Task 11: AddRegistryForm + page + nav + i18n

**Files:**
- Create: `frontend/packages/web/components/admin/skill-registries/AddRegistryForm.tsx`
- Create: `frontend/packages/web/app/admin/skill-registries/page.tsx`
- Modify: `frontend/packages/web/components/admin/AdminSubNav.tsx`
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`

- [ ] **Step 1: Create `AddRegistryForm.tsx`**

```tsx
'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { CreateRegistryBody } from '@/hooks/useAdminSkillRegistries'

const TRUST_TIERS = ['official', 'community', 'untrusted'] as const

interface AddRegistryFormProps {
  onSubmit: (body: CreateRegistryBody) => Promise<boolean>
  onCancel: () => void
  mutating: boolean
  error: string | null
}

export function AddRegistryForm({ onSubmit, onCancel, mutating, error }: AddRegistryFormProps) {
  const t = useTranslations('adminSkillRegistries')
  const [kind, setKind] = useState<'skills-sh' | 'remote'>('skills-sh')
  const [name, setName] = useState(kind === 'skills-sh' ? 'skills.sh' : '')
  const [baseUrl, setBaseUrl] = useState('')
  const [trustTier, setTrustTier] = useState<string>('community')

  function handleKindChange(next: 'skills-sh' | 'remote') {
    setKind(next)
    setName(next === 'skills-sh' ? 'skills.sh' : '')
    setTrustTier(next === 'skills-sh' ? 'community' : 'untrusted')
    setBaseUrl('')
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const body: CreateRegistryBody = {
      name: name.trim(),
      kind,
      trust_tier: trustTier,
      ...(kind === 'remote' ? { base_url: baseUrl.trim() } : {}),
    }
    await onSubmit(body)
  }

  return (
    <form onSubmit={(e) => void handleSubmit(e)} className="flex flex-1 flex-col gap-6 p-6">
      <h3 className="text-base font-semibold">{t('addTitle')}</h3>

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          {error}
        </div>
      )}

      {/* Kind selector */}
      <div className="flex flex-col gap-2">
        <label className="text-sm font-medium">{t('kind')}</label>
        <div className="inline-flex items-center gap-0.5 rounded-lg border border-border bg-muted/30 p-0.5 self-start">
          {(['skills-sh', 'remote'] as const).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => handleKindChange(k)}
              className={cn(
                'rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
                k === kind
                  ? 'bg-background text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {k === 'skills-sh' ? 'skills.sh' : t('customRegistry')}
            </button>
          ))}
        </div>
      </div>

      {/* Name */}
      <div className="flex flex-col gap-2">
        <label className="text-sm font-medium">{t('name')}</label>
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t('namePlaceholder')}
          required
          className="max-w-sm"
        />
      </div>

      {/* Base URL (custom only) */}
      {kind === 'remote' && (
        <div className="flex flex-col gap-2">
          <label className="text-sm font-medium">{t('registryUrl')}</label>
          <Input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://registry.example.com"
            required
            type="url"
            className="max-w-sm"
          />
          <p className="text-xs text-muted-foreground">{t('registryUrlHint')}</p>
        </div>
      )}

      {/* Trust tier */}
      <div className="flex flex-col gap-2">
        <label className="text-sm font-medium">{t('trustTier')}</label>
        <div className="inline-flex items-center gap-0.5 rounded-lg border border-border bg-muted/30 p-0.5 self-start">
          {TRUST_TIERS.map((tier) => (
            <button
              key={tier}
              type="button"
              onClick={() => setTrustTier(tier)}
              className={cn(
                'rounded-md px-2.5 py-1 text-xs font-medium transition-colors capitalize',
                tier === trustTier
                  ? 'bg-background text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {tier}
            </button>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-2 pt-2">
        <Button type="submit" size="sm" disabled={mutating || !name.trim()}>
          {mutating ? t('adding') : t('add')}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          {t('cancel')}
        </Button>
      </div>
    </form>
  )
}
```

- [ ] **Step 2: Create `app/admin/skill-registries/page.tsx`**

```tsx
'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { RegistryCard } from '@/components/admin/skill-registries/RegistryCard'
import { RegistryDetailPanel } from '@/components/admin/skill-registries/RegistryDetailPanel'
import { AddRegistryForm } from '@/components/admin/skill-registries/AddRegistryForm'
import { useAdminSkillRegistries } from '@/hooks/useAdminSkillRegistries'

export default function SkillRegistriesPage() {
  const t = useTranslations('adminSkillRegistries')
  const { registries, loading, mutating, error, create, patch, remove } =
    useAdminSkillRegistries()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)

  useEffect(() => {
    document.title = t('pageTitle')
  }, [t])

  const selected = registries.find((r) => r.id === selectedId) ?? null

  async function handleCreate(body: Parameters<typeof create>[0]) {
    const created = await create(body)
    if (created) {
      setAdding(false)
      setSelectedId(created.id)
    }
    return !!created
  }

  async function handleDelete(id: string) {
    const ok = await remove(id)
    if (ok && selectedId === id) setSelectedId(null)
  }

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <h2 className="text-lg font-semibold tracking-tight">{t('title')}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('subtitle')}</p>
      </header>

      <div className="flex items-center gap-2 border-b border-border/70 px-4 py-3">
        <Button
          size="sm"
          onClick={() => { setAdding(true); setSelectedId(null) }}
        >
          <Plus className="size-3.5" />
          {t('addRegistry')}
        </Button>
      </div>

      <div className="flex flex-1 overflow-hidden">
        <aside
          aria-label={t('listAria')}
          className="w-[300px] shrink-0 overflow-y-auto border-r border-border/70 bg-card/20"
        >
          {loading ? (
            <p className="px-4 py-6 text-center text-xs text-muted-foreground">{t('loading')}</p>
          ) : registries.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
              <p className="text-sm text-muted-foreground">{t('empty')}</p>
              <p className="text-xs text-muted-foreground/70">{t('emptyHint')}</p>
            </div>
          ) : (
            <ul className="flex flex-col gap-1.5 p-3">
              {registries.map((r) => (
                <li key={r.id}>
                  <RegistryCard
                    registry={r}
                    active={r.id === selectedId && !adding}
                    onClick={() => { setSelectedId(r.id); setAdding(false) }}
                  />
                </li>
              ))}
            </ul>
          )}
        </aside>

        <section className="flex flex-1 overflow-y-auto">
          {adding ? (
            <AddRegistryForm
              onSubmit={handleCreate}
              onCancel={() => setAdding(false)}
              mutating={mutating}
              error={error}
            />
          ) : selected ? (
            <RegistryDetailPanel
              registry={selected}
              onPatch={patch}
              onDelete={handleDelete}
              mutating={mutating}
              error={error}
            />
          ) : (
            <div className="flex flex-1 items-center justify-center p-8 text-sm text-muted-foreground">
              {t('selectOrAdd')}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Add nav entry to `AdminSubNav.tsx`**

Find the nav array and add the entry after the `skills` entry:

```tsx
// Add import at top:
import { Database } from 'lucide-react'

// In the navItems array, after the skills entry:
{ href: '/admin/skills', label: t('skills'), icon: Sparkles },
{ href: '/admin/skill-registries', label: t('skillRegistries'), icon: Database },
```

- [ ] **Step 4: Add i18n keys to `en.json`**

Find the `"admin"` object and add inside it:

```json
"skillRegistries": "Skill Registries",
```

Add a new top-level `"adminSkillRegistries"` object:

```json
"adminSkillRegistries": {
  "pageTitle": "Skill Registries",
  "title": "Skill Registries",
  "subtitle": "Manage external skill registries for this organisation",
  "addRegistry": "Add Registry",
  "listAria": "skill registries list",
  "loading": "Loading…",
  "empty": "No registries configured",
  "emptyHint": "Add a registry to enable skill discovery from external sources",
  "selectOrAdd": "Select a registry or add a new one",
  "addTitle": "Add Registry",
  "kind": "Kind",
  "customRegistry": "Custom Registry",
  "name": "Name",
  "namePlaceholder": "My Registry",
  "registryUrl": "Registry URL",
  "registryUrlHint": "Must be a public HTTPS endpoint implementing the registry search API",
  "trustTier": "Trust Tier",
  "adding": "Adding…",
  "add": "Add",
  "cancel": "Cancel",
  "enabled": "Enabled",
  "deleteTitle": "Delete Registry",
  "deleteDescription": "Remove \"{name}\" from this org. Existing installed skills are not affected.",
  "delete": "Delete"
}
```

- [ ] **Step 5: Add i18n keys to `zh.json`**

Same structure under `"admin"` and `"adminSkillRegistries"`:

```json
"skillRegistries": "技能仓库",
```

```json
"adminSkillRegistries": {
  "pageTitle": "技能仓库",
  "title": "技能仓库",
  "subtitle": "管理此组织的外部 Skill 仓库",
  "addRegistry": "添加仓库",
  "listAria": "技能仓库列表",
  "loading": "加载中…",
  "empty": "暂无已配置的仓库",
  "emptyHint": "添加仓库以启用外部 Skill 发现功能",
  "selectOrAdd": "选择一个仓库或添加新仓库",
  "addTitle": "添加仓库",
  "kind": "类型",
  "customRegistry": "自定义仓库",
  "name": "名称",
  "namePlaceholder": "我的仓库",
  "registryUrl": "仓库地址",
  "registryUrlHint": "必须是实现了注册表搜索 API 的公共 HTTPS 端点",
  "trustTier": "信任级别",
  "adding": "添加中…",
  "add": "添加",
  "cancel": "取消",
  "enabled": "已启用",
  "deleteTitle": "删除仓库",
  "deleteDescription": "从此组织移除「{name}」。已安装的 Skill 不受影响。",
  "delete": "删除"
}
```

- [ ] **Step 6: TypeScript + i18n parity check**

```bash
cd frontend && pnpm --filter web tsc --noEmit 2>&1 | grep 'error TS'
```

Expected: no errors.

- [ ] **Step 7: Prettier**

```bash
pnpm prettier --write \
  "packages/web/app/admin/skill-registries/page.tsx" \
  "packages/web/components/admin/skill-registries/*.tsx" \
  "packages/web/hooks/useAdminSkillRegistries.ts" \
  "packages/web/components/admin/AdminSubNav.tsx" \
  "packages/web/messages/en.json" \
  "packages/web/messages/zh.json"
```

- [ ] **Step 8: Full frontend lint**

```bash
pnpm --filter web eslint --max-warnings=0 . 2>&1 | grep -v '^$' | head -20
```

Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add frontend/packages/web/
git commit -m "feat(admin): add Skill Registries admin page (list, add, enable/disable, delete)"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] Rename: SkillSource(Protocol)→SkillRegistryAdapter, model→SkillRegistry, repo→SkillRegistryRepository, container→SkillsAdapterManager, adapters renamed
- [x] DB migration: skill_sources→skill_registries
- [x] admin route renamed to /admin/skill-registries
- [x] DELETE endpoint added to repository + route
- [x] Config: registry.skills_sh.github_token
- [x] SkillsShAdapter: search (with branch resolution at search time), fetch (split/3)
- [x] SkillsShAdapter: kind="remote" for adapter_by_id() compatibility
- [x] SkillsShAdapter: transport param for testing
- [x] Admin page: master-detail, add form with kind selector, enable toggle, trust tier, delete confirm
- [x] AdminSubNav: correct file, correct icon import
- [x] Auth: direct fetch + credentials:include + CSRF headers from lib/csrf

**No placeholders:** checked — all steps contain exact code.

**Type consistency:** `SkillRegistryAdapter` defined in Task 3, used in Task 7/8. `SkillRegistry` (model) defined in Task 2, used in Tasks 3-4. Hook types defined in Task 9, used in Tasks 10-11.
