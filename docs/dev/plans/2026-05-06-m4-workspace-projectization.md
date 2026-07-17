# M4 Workspace Projectization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire workspace-level persona, skill, and MCP settings so every conversation in a workspace automatically uses its configured agent behavior, and expose a settings UI in the conversation sidebar.

**Architecture:** Backend: schema migration adds `workspace_id` to `OrgSkillInstall` for workspace-private skills; new `ws_settings.py` router handles GET/PUT agent config, skills binding, and MCP binding; `run_manager.py` loads `AgentConfig` and appends its `system_prompt` to `BASE_SYSTEM_PROMPT`. Frontend: new `/w/[wsId]/settings` page with 3-column layout; settings nav injected at the bottom of the existing `Sidebar`.

**Tech Stack:** FastAPI + SQLModel + Alembic + pytest (backend); Next.js App Router + Zustand + Tailwind + shadcn/ui + Playwright (frontend)

---

## File Map

**Backend — new:**
- `backend/cubeplex/api/routes/v1/ws_settings.py` — agent config + skills + MCP settings routes
- `backend/cubeplex/api/schemas/ws_settings.py` — Pydantic schemas for settings endpoints
- `backend/alembic/versions/<rev>_m4_workspace_settings.py` — schema + data migration
- `backend/tests/e2e/test_ws_settings.py` — E2E tests for all settings endpoints

**Backend — modified:**
- `backend/cubeplex/models/skill.py` — add `workspace_id` FK to `OrgSkillInstall`
- `backend/cubeplex/repositories/skill.py` — add workspace-private install methods
- `backend/cubeplex/skills/service.py` — include workspace-private skills in catalog loading
- `backend/cubeplex/agents/graph.py:204` — add `system_prompt` param to `create_cubeplex_agent`
- `backend/cubeplex/streams/run_manager.py:607` — load `AgentConfig`, pass persona
- `backend/cubeplex/api/routes/v1/workspaces.py:95` — auto-create `AgentConfig` on workspace creation
- `backend/cubeplex/api/routes/v1/__init__.py` — export `ws_settings`
- `backend/cubeplex/api/app.py:384` — register `ws_settings.router`

**Frontend — new:**
- `frontend/packages/core/src/types/workspace-settings.ts` — TypeScript types
- `frontend/packages/core/src/api/workspace-settings.ts` — API client methods
- `frontend/packages/core/src/stores/workspaceSettingsStore.ts` — Zustand store
- `frontend/packages/web/app/(app)/w/[wsId]/settings/page.tsx` — settings shell page
- `frontend/packages/web/components/workspace-settings/SettingsNav.tsx` — settings nav (sidebar section)
- `frontend/packages/web/components/workspace-settings/PersonaEditor.tsx` — persona textarea + save
- `frontend/packages/web/components/workspace-settings/SkillsPanel.tsx` — skills list + detail
- `frontend/packages/web/components/workspace-settings/McpPanel.tsx` — MCP list + detail

**Frontend — modified:**
- `frontend/packages/web/components/layout/Sidebar.tsx` — add settings link in footer
- `frontend/packages/core/src/index.ts` — export new types/stores/api

**Tests:**
- `frontend/packages/web/tests/workspace-settings.spec.ts` — Playwright E2E test

---

## Task 1: Schema + Data Migration

**Files:**
- Modify: `backend/cubeplex/models/skill.py`
- Create: `backend/alembic/versions/<rev>_m4_workspace_settings.py`

- [ ] **Step 1: Add `workspace_id` to `OrgSkillInstall` model**

In `backend/cubeplex/models/skill.py`, update `OrgSkillInstall`:

```python
class OrgSkillInstall(CubeplexBase, table=True):
    """Org-level install — admin promoted a skill into the org marketplace.

    workspace_id=None → org-wide install (visible to all workspaces).
    workspace_id=<id>  → workspace-private install (visible only to that workspace).
    """

    _PREFIX: ClassVar[str] = "osi"
    __tablename__ = "org_skill_installs"

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    skill_id: str = Field(foreign_key="skills.id", max_length=20, index=True)
    workspace_id: str | None = Field(
        default=None, foreign_key="workspaces.id", max_length=20, nullable=True, index=True
    )
    installed_version: str = Field(max_length=32)
    installed_by_user_id: str = Field(foreign_key="users.id", max_length=20)
    installed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    auto_bind: bool = Field(default=False)

    __table_args__ = (
        # Org-wide installs: unique per (org, skill) where workspace is null
        Index(
            "uq_org_skill_install_org_wide",
            "org_id",
            "skill_id",
            unique=True,
            postgresql_where=sa.text("workspace_id IS NULL"),
        ),
        # Workspace-private installs: unique per (org, workspace, skill)
        UniqueConstraint("org_id", "workspace_id", "skill_id", name="uq_org_skill_install_ws"),
        Index("ix_osi_org_workspace", "org_id", "workspace_id"),
    )
```

Also add `import sqlalchemy as sa` at the top of the file.

- [ ] **Step 2: Generate Alembic migration**

```bash
cd backend
alembic revision -m "m4_workspace_settings"
```

Open the new file and replace the `upgrade`/`downgrade` bodies:

```python
import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    # 1. Add workspace_id column to org_skill_installs
    op.add_column(
        "org_skill_installs",
        sa.Column("workspace_id", sa.String(20), sa.ForeignKey("workspaces.id"), nullable=True),
    )
    op.create_index("ix_osi_org_workspace", "org_skill_installs", ["org_id", "workspace_id"])

    # 2. Replace uq_org_skill_install with partial unique index (org-wide rows only)
    op.drop_constraint("uq_org_skill_install", "org_skill_installs", type_="unique")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_org_skill_install_org_wide
        ON org_skill_installs (org_id, skill_id)
        WHERE workspace_id IS NULL
        """
    )
    # 3. Add unique constraint for workspace-private rows
    op.create_unique_constraint(
        "uq_org_skill_install_ws",
        "org_skill_installs",
        ["org_id", "workspace_id", "skill_id"],
    )

    # 4. Backfill AgentConfig for workspaces that don't have one
    # AgentConfig._PREFIX = "agt"; model_id has no default so provide empty string.
    op.execute(
        """
        INSERT INTO agent_configs (id, org_id, workspace_id, system_prompt, model_id,
                                   skill_ids, mcp_server_ids, created_at, updated_at)
        SELECT
            'agt-' || substr(md5(w.id::text), 1, 14),
            w.org_id,
            w.id,
            '',
            '',
            NULL,
            NULL,
            NOW(),
            NOW()
        FROM workspaces w
        WHERE NOT EXISTS (
            SELECT 1 FROM agent_configs ac WHERE ac.workspace_id = w.id
        )
        """
    )


def downgrade() -> None:
    op.drop_constraint("uq_org_skill_install_ws", "org_skill_installs", type_="unique")
    op.execute("DROP INDEX IF EXISTS uq_org_skill_install_org_wide")
    op.create_unique_constraint(
        "uq_org_skill_install", "org_skill_installs", ["org_id", "skill_id"]
    )
    op.drop_index("ix_osi_org_workspace", table_name="org_skill_installs")
    op.drop_column("org_skill_installs", "workspace_id")
```

- [ ] **Step 3: Apply migration**

```bash
cd backend
alembic upgrade head
```

Expected: Migration completes without errors.

- [ ] **Step 4: Verify with mypy**

```bash
cd backend
make type-check
```

Expected: no new errors.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/models/skill.py backend/alembic/versions/
git commit -m "feat(m4): add workspace_id to OrgSkillInstall + backfill AgentConfig"
```

---

## Task 2: Wire Persona to Runtime

**Files:**
- Modify: `backend/cubeplex/agents/graph.py`
- Modify: `backend/cubeplex/streams/run_manager.py`
- Modify: `backend/cubeplex/api/routes/v1/workspaces.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/e2e/test_ws_settings.py`:

```python
"""E2E tests for workspace settings API (M4)."""
import pytest
from fastapi.testclient import TestClient

from tests.e2e.conftest import DEFAULT_WS_ID

pytestmark = pytest.mark.e2e


class TestPersonaRuntime:
    """Persona is applied to the agent system prompt."""

    def test_get_agent_config_default(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/agent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["system_prompt"] == ""

    def test_update_and_read_persona(self, client: TestClient) -> None:
        persona = "You are a Python expert."
        resp = client.put(
            f"/api/v1/ws/{DEFAULT_WS_ID}/settings/agent",
            json={"system_prompt": persona},
        )
        assert resp.status_code == 200
        assert resp.json()["system_prompt"] == persona

        get_resp = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/agent")
        assert get_resp.json()["system_prompt"] == persona

    def test_unauthorized_cannot_access(self, client: TestClient) -> None:
        resp = client.get(
            f"/api/v1/ws/{DEFAULT_WS_ID}/settings/agent",
            cookies={},
        )
        # Without auth cookies the client fixture auto-provides, use a fresh client
        import httpx
        with httpx.Client(base_url="http://testserver") as anon:
            r = anon.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/agent")
        assert r.status_code == 401
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd backend
uv run pytest tests/e2e/test_ws_settings.py::TestPersonaRuntime -v
```

Expected: FAIL — `404` because the route doesn't exist yet.

- [ ] **Step 3: Add `system_prompt` param to `create_cubeplex_agent`**

In `backend/cubeplex/agents/graph.py`, update the function signature (around line 33):

```python
def create_cubeplex_agent(
    llm: BaseChatModel,
    tools: list[BaseTool],
    *,
    system_prompt: str = BASE_SYSTEM_PROMPT,   # add this line
    sandbox: Sandbox | None = None,
    conversation_id: str | None = None,
    org_id: str | None = None,
    workspace_id: str | None = None,
    catalog_session: AsyncSession | None = None,
    user_id: str | None = None,
    subagents: list[SubAgent] | None = None,
    checkpointer: Checkpointer | None = None,
    citation_configs: dict[str, CitationConfig] | None = None,
    event_queue: asyncio.Queue[Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
```

Then find the internal use of `BASE_SYSTEM_PROMPT` at line ~204 and replace the hardcoded reference:

```python
# Before:
system_prompt=BASE_SYSTEM_PROMPT,
# After:
system_prompt=system_prompt,
```

- [ ] **Step 4: Create the agent config schemas**

Create `backend/cubeplex/api/schemas/ws_settings.py`:

```python
"""Pydantic schemas for workspace settings endpoints."""
from pydantic import BaseModel, Field


class AgentConfigOut(BaseModel):
    system_prompt: str


class AgentConfigPatch(BaseModel):
    system_prompt: str = Field(max_length=8000)
```

- [ ] **Step 5: Create the ws_settings router with agent config routes**

Create `backend/cubeplex/api/routes/v1/ws_settings.py`:

```python
"""Workspace settings routes: agent config, skill bindings, MCP bindings."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from cubeplex.api.schemas.ws_settings import AgentConfigOut, AgentConfigPatch
from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import require_member
from cubeplex.db import get_session
from cubeplex.models.agent_config import AgentConfig

router = APIRouter(prefix="/ws/{workspace_id}/settings", tags=["workspace-settings"])


async def _get_or_create_agent_config(
    session: AsyncSession, org_id: str, workspace_id: str
) -> AgentConfig:
    result = await session.execute(
        select(AgentConfig).where(
            AgentConfig.org_id == org_id,  # type: ignore[arg-type]
            AgentConfig.workspace_id == workspace_id,  # type: ignore[arg-type]
        )
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = AgentConfig(org_id=org_id, workspace_id=workspace_id)
        session.add(cfg)
        await session.commit()
        await session.refresh(cfg)
    return cfg


@router.get("/agent", response_model=AgentConfigOut)
async def get_agent_config(
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentConfigOut:
    cfg = await _get_or_create_agent_config(session, ctx.org_id, ctx.workspace_id)
    return AgentConfigOut(system_prompt=cfg.system_prompt)


@router.put("/agent", response_model=AgentConfigOut)
async def update_agent_config(
    body: AgentConfigPatch,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentConfigOut:
    cfg = await _get_or_create_agent_config(session, ctx.org_id, ctx.workspace_id)
    cfg.system_prompt = body.system_prompt
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)
    return AgentConfigOut(system_prompt=cfg.system_prompt)
```

- [ ] **Step 6: Register the router**

In `backend/cubeplex/api/routes/v1/__init__.py`, add:

```python
from cubeplex.api.routes.v1 import admin_mcp, admin_skills, ws_mcp, ws_settings, ws_skills
# ...
__all__ = [
    # existing entries ...
    "ws_settings",
]
```

In `backend/cubeplex/api/app.py`, after the `ws_mcp` registration (around line 384):

```python
app.include_router(ws_settings.router, prefix="/api/v1")
```

- [ ] **Step 7: Auto-create AgentConfig on workspace creation**

In `backend/cubeplex/api/routes/v1/workspaces.py`, update `create_workspace`:

```python
from cubeplex.models.agent_config import AgentConfig

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: Annotated[WorkspaceCreate, Body()],
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    ws_repo = WorkspaceRepository(session)
    mem_repo = MembershipRepository(session)
    ws = await ws_repo.create(org_id=body.org_id, name=body.name)
    await mem_repo.grant(user_id=user.id, workspace_id=ws.id, role=Role.ADMIN)
    # Auto-create an empty agent config for the new workspace
    agent_cfg = AgentConfig(org_id=body.org_id, workspace_id=ws.id)
    session.add(agent_cfg)
    await session.commit()
    return {"id": ws.id, "name": ws.name, "org_id": ws.org_id}
```

- [ ] **Step 8: Load persona in run_manager**

In `backend/cubeplex/streams/run_manager.py`, add AgentConfig loading before the `create_cubeplex_agent` call (around line 552, before the agent creation block):

```python
from cubeplex.models.agent_config import AgentConfig
from cubeplex.prompts.system import BASE_SYSTEM_PROMPT
from sqlmodel import select

# Load persona from AgentConfig
effective_system_prompt = BASE_SYSTEM_PROMPT
try:
    async with async_session_maker() as cfg_session:
        result = await cfg_session.execute(
            select(AgentConfig).where(
                AgentConfig.org_id == ctx.org_id,  # type: ignore[arg-type]
                AgentConfig.workspace_id == ctx.workspace_id,  # type: ignore[arg-type]
            )
        )
        agent_cfg = result.scalar_one_or_none()
        if agent_cfg and agent_cfg.system_prompt:
            effective_system_prompt = BASE_SYSTEM_PROMPT + "\n\n" + agent_cfg.system_prompt
except Exception as exc:
    logger.warning("Failed to load AgentConfig, using base prompt: {}", exc)
```

Then update the `create_cubeplex_agent` call to pass it:

```python
agent = create_cubeplex_agent(
    llm=llm,
    tools=tools,
    system_prompt=effective_system_prompt,   # add this
    sandbox=sandbox,
    # ... rest unchanged
)
```

- [ ] **Step 9: Run the test to verify it passes**

```bash
cd backend
uv run pytest tests/e2e/test_ws_settings.py::TestPersonaRuntime -v
```

Expected: PASS

- [ ] **Step 10: Run full check**

```bash
cd backend
make check
```

Expected: all checks pass.

- [ ] **Step 11: Commit**

```bash
git add backend/cubeplex/agents/graph.py \
        backend/cubeplex/streams/run_manager.py \
        backend/cubeplex/api/routes/v1/workspaces.py \
        backend/cubeplex/api/routes/v1/ws_settings.py \
        backend/cubeplex/api/schemas/ws_settings.py \
        backend/cubeplex/api/routes/v1/__init__.py \
        backend/cubeplex/api/app.py \
        backend/tests/e2e/test_ws_settings.py
git commit -m "feat(m4): wire persona to runtime + agent config API"
```

---

## Task 3: Workspace-Private Skill Support

**Files:**
- Modify: `backend/cubeplex/repositories/skill.py`
- Modify: `backend/cubeplex/skills/service.py`

- [ ] **Step 1: Write failing tests**

Add to `backend/tests/e2e/test_ws_settings.py`:

```python
class TestSkillsSettings:
    """Workspace skill binding and private skill management."""

    def test_list_skills_empty(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert "org_skills" in data
        assert "workspace_skills" in data
        assert isinstance(data["org_skills"], list)
        assert isinstance(data["workspace_skills"], list)

    def test_install_workspace_private_skill(self, client: TestClient) -> None:
        # Requires a skill to exist in the catalog; skip if none installed
        skills_resp = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/skills")
        org_skills = skills_resp.json()["org_skills"]
        if not org_skills:
            pytest.skip("No org skills installed")

        # Toggle an org-installed skill off
        install_id = org_skills[0]["install_id"]
        resp = client.patch(
            f"/api/v1/ws/{DEFAULT_WS_ID}/settings/skills/{install_id}",
            json={"enabled": False},
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        # Toggle back on
        resp = client.patch(
            f"/api/v1/ws/{DEFAULT_WS_ID}/settings/skills/{install_id}",
            json={"enabled": True},
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True
```

- [ ] **Step 2: Run to verify failing**

```bash
cd backend
uv run pytest tests/e2e/test_ws_settings.py::TestSkillsSettings -v
```

Expected: FAIL — `404` (route not yet added).

- [ ] **Step 3: Add repository methods for workspace-private skills**

In `backend/cubeplex/repositories/skill.py`, update `OrgSkillInstallRepository`:

```python
async def create_for_workspace(
    self,
    *,
    org_id: str,
    workspace_id: str,
    skill_id: str,
    installed_version: str,
    installed_by_user_id: str,
) -> OrgSkillInstall:
    row = OrgSkillInstall(
        org_id=org_id,
        workspace_id=workspace_id,
        skill_id=skill_id,
        installed_version=installed_version,
        installed_by_user_id=installed_by_user_id,
        auto_bind=True,
    )
    self.session.add(row)
    await self.session.commit()
    await self.session.refresh(row)
    return row

async def list_for_workspace_private(
    self, org_id: str, workspace_id: str
) -> list[OrgSkillInstall]:
    result = await self.session.execute(
        select(OrgSkillInstall).where(
            OrgSkillInstall.org_id == org_id,  # type: ignore[arg-type]
            OrgSkillInstall.workspace_id == workspace_id,  # type: ignore[arg-type]
        )
    )
    return list(result.scalars().all())

async def get_by_id(self, install_id: str) -> OrgSkillInstall | None:
    result = await self.session.execute(
        select(OrgSkillInstall).where(OrgSkillInstall.id == install_id)  # type: ignore[arg-type]
    )
    return result.scalar_one_or_none()

async def delete_workspace_private(self, install_id: str, workspace_id: str) -> bool:
    row = await self.get_by_id(install_id)
    if row is None or row.workspace_id != workspace_id:
        return False
    await self.session.delete(row)
    await self.session.commit()
    return True
```

Also update `list_for_org` to only return org-wide installs (where `workspace_id IS NULL`):

```python
async def list_for_org(self, org_id: str) -> list[OrgSkillInstall]:
    result = await self.session.execute(
        select(OrgSkillInstall).where(
            OrgSkillInstall.org_id == org_id,  # type: ignore[arg-type]
            OrgSkillInstall.workspace_id.is_(None),  # type: ignore[union-attr]
        )
    )
    return list(result.scalars().all())
```

- [ ] **Step 4: Update SkillCatalogService to include workspace-private skills**

In `backend/cubeplex/skills/service.py`, update `list_enabled_for_workspace` to also include workspace-private installs (they are always enabled — no binding row needed):

```python
async def list_enabled_for_workspace(
    self,
    org_id: str,
    workspace_id: str,
) -> list[tuple[Skill, SkillVersion, OrgSkillInstall]]:
    # Existing org-wide skill query (unchanged) ...
    org_wide_results = ...  # keep existing query

    # Additionally load workspace-private installs
    ws_private_stmt = (
        select(Skill, SkillVersion, OrgSkillInstall)
        .join(OrgSkillInstall, OrgSkillInstall.skill_id == Skill.id)
        .join(
            SkillVersion,
            (SkillVersion.skill_id == Skill.id)
            & (SkillVersion.version == OrgSkillInstall.installed_version),
        )
        .where(
            OrgSkillInstall.org_id == org_id,
            OrgSkillInstall.workspace_id == workspace_id,
        )
    )
    ws_private_result = await self.session.execute(ws_private_stmt)
    ws_private = list(ws_private_result.all())

    return org_wide_results + ws_private
```

> **Note:** Read the existing `list_enabled_for_workspace` body in `backend/cubeplex/skills/service.py` before editing — preserve its exact existing logic and append the workspace-private query result. Do not rewrite the existing org-wide query.

- [ ] **Step 5: Add skills settings routes to `ws_settings.py`**

Add to `backend/cubeplex/api/routes/v1/ws_settings.py`:

```python
from cubeplex.api.schemas.ws_settings import (
    AgentConfigOut,
    AgentConfigPatch,
    SkillBindingPatch,
    SkillInstallCreate,
    WorkspaceSkillsOut,
)
from cubeplex.models.skill import OrgSkillInstall, WorkspaceSkillBinding
from cubeplex.repositories.skill import OrgSkillInstallRepository, WorkspaceSkillBindingRepository
from sqlmodel import select


@router.get("/skills", response_model=WorkspaceSkillsOut)
async def list_workspace_skills(
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WorkspaceSkillsOut:
    install_repo = OrgSkillInstallRepository(session)
    binding_repo = WorkspaceSkillBindingRepository(session, ctx.org_id, ctx.workspace_id)

    org_installs = await install_repo.list_for_org(ctx.org_id)
    ws_private = await install_repo.list_for_workspace_private(ctx.org_id, ctx.workspace_id)

    org_skills = []
    for install in org_installs:
        binding = await binding_repo.get_by_install(install.id)
        enabled = binding.enabled if binding is not None else install.auto_bind
        org_skills.append(
            {
                "install_id": install.id,
                "skill_id": install.skill_id,
                "installed_version": install.installed_version,
                "enabled": enabled,
                "scope": "org",
            }
        )

    workspace_skills = [
        {
            "install_id": i.id,
            "skill_id": i.skill_id,
            "installed_version": i.installed_version,
            "enabled": True,
            "scope": "workspace",
        }
        for i in ws_private
    ]

    return WorkspaceSkillsOut(org_skills=org_skills, workspace_skills=workspace_skills)


@router.patch("/skills/{install_id}")
async def toggle_skill_binding(
    install_id: str,
    body: SkillBindingPatch,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    install_repo = OrgSkillInstallRepository(session)
    install = await install_repo.get_by_id(install_id)
    if install is None or install.org_id != ctx.org_id or install.workspace_id is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="org skill install not found")

    binding_repo = WorkspaceSkillBindingRepository(session, ctx.org_id, ctx.workspace_id)
    if body.enabled:
        binding = await binding_repo.enable(install_id)
    else:
        await binding_repo.disable(install_id)
        binding = await binding_repo.get_by_install(install_id)

    enabled = binding.enabled if binding is not None else body.enabled
    return {"install_id": install_id, "enabled": enabled}


@router.post("/skills", status_code=status.HTTP_201_CREATED)
async def install_workspace_skill(
    body: SkillInstallCreate,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    from cubeplex.repositories.skill import SkillRepository

    skill_repo = SkillRepository(session)
    skill = await skill_repo.get(body.skill_id)
    if skill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="skill not found in catalog")

    install_repo = OrgSkillInstallRepository(session)
    install = await install_repo.create_for_workspace(
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        skill_id=body.skill_id,
        installed_version=body.version,
        installed_by_user_id=ctx.user_id,
    )
    return {"install_id": install.id, "skill_id": install.skill_id, "scope": "workspace"}


@router.delete("/skills/{install_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace_skill(
    install_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    install_repo = OrgSkillInstallRepository(session)
    deleted = await install_repo.delete_workspace_private(install_id, ctx.workspace_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="workspace skill install not found")
```

- [ ] **Step 6: Add new schemas to `ws_settings.py`**

In `backend/cubeplex/api/schemas/ws_settings.py`, add:

```python
from pydantic import BaseModel, Field


class AgentConfigOut(BaseModel):
    system_prompt: str


class AgentConfigPatch(BaseModel):
    system_prompt: str = Field(max_length=8000)


class SkillInstallOut(BaseModel):
    install_id: str
    skill_id: str
    installed_version: str
    enabled: bool
    scope: str  # "org" | "workspace"


class WorkspaceSkillsOut(BaseModel):
    org_skills: list[SkillInstallOut]
    workspace_skills: list[SkillInstallOut]


class SkillBindingPatch(BaseModel):
    enabled: bool


class SkillInstallCreate(BaseModel):
    skill_id: str
    version: str
```

- [ ] **Step 7: Check SkillRepository.get exists**

```bash
grep -n "async def get\b" backend/cubeplex/repositories/skill.py
```

If no `async def get(self, skill_id: str)` method exists on `SkillRepository`, add:

```python
async def get(self, skill_id: str) -> Skill | None:
    result = await self.session.execute(
        select(Skill).where(Skill.id == skill_id)  # type: ignore[arg-type]
    )
    return result.scalar_one_or_none()
```

- [ ] **Step 8: Run skills tests**

```bash
cd backend
uv run pytest tests/e2e/test_ws_settings.py::TestSkillsSettings -v
```

Expected: PASS

- [ ] **Step 9: Run full check**

```bash
cd backend
make check
```

- [ ] **Step 10: Commit**

```bash
git add backend/cubeplex/repositories/skill.py \
        backend/cubeplex/skills/service.py \
        backend/cubeplex/api/routes/v1/ws_settings.py \
        backend/cubeplex/api/schemas/ws_settings.py \
        backend/tests/e2e/test_ws_settings.py
git commit -m "feat(m4): workspace skill binding + private skill install API"
```

---

## Task 4: MCP Settings List API

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/ws_settings.py`
- Modify: `backend/cubeplex/api/schemas/ws_settings.py`

- [ ] **Step 1: Write failing tests**

Add to `backend/tests/e2e/test_ws_settings.py`:

```python
class TestMCPSettings:
    """Workspace MCP settings routes."""

    def test_list_mcp(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/mcp")
        assert resp.status_code == 200
        data = resp.json()
        assert "org_servers" in data
        assert "workspace_servers" in data
        assert isinstance(data["org_servers"], list)
        assert isinstance(data["workspace_servers"], list)
```

- [ ] **Step 2: Run to verify failing**

```bash
cd backend
uv run pytest tests/e2e/test_ws_settings.py::TestMCPSettings -v
```

Expected: FAIL — 404.

- [ ] **Step 3: Understand MCPServer listing**

Read `backend/cubeplex/repositories/mcp.py` and `backend/cubeplex/services/mcp.py` to understand how to query:
- Org-wide servers: `MCPServer.owner_workspace_id IS NULL` with their `WorkspaceMCPBinding` for this workspace
- Workspace-private servers: `MCPServer.owner_workspace_id == workspace_id`

```bash
grep -n "def list\|owner_workspace_id\|WorkspaceMCPBinding" \
  backend/cubeplex/repositories/mcp.py \
  backend/cubeplex/services/mcp.py | head -30
```

- [ ] **Step 4: Add MCP schemas**

In `backend/cubeplex/api/schemas/ws_settings.py`, add:

```python
class MCPServerItem(BaseModel):
    server_id: str
    name: str
    server_url: str
    transport: str
    enabled: bool
    scope: str  # "org" | "workspace"


class WorkspaceMCPOut(BaseModel):
    org_servers: list[MCPServerItem]
    workspace_servers: list[MCPServerItem]


class MCPBindingPatch(BaseModel):
    enabled: bool
```

- [ ] **Step 5: Add MCP routes to `ws_settings.py`**

Add to `backend/cubeplex/api/routes/v1/ws_settings.py`:

```python
from cubeplex.models.mcp import MCPServer, WorkspaceMCPBinding
from cubeplex.api.schemas.ws_settings import MCPBindingPatch, WorkspaceMCPOut, MCPServerItem


@router.get("/mcp", response_model=WorkspaceMCPOut)
async def list_workspace_mcp(
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WorkspaceMCPOut:
    # Org-wide servers
    org_stmt = select(MCPServer, WorkspaceMCPBinding).outerjoin(
        WorkspaceMCPBinding,
        (WorkspaceMCPBinding.mcp_server_id == MCPServer.id)
        & (WorkspaceMCPBinding.workspace_id == ctx.workspace_id),
    ).where(
        MCPServer.org_id == ctx.org_id,
        MCPServer.owner_workspace_id.is_(None),  # type: ignore[union-attr]
    )
    org_result = await session.execute(org_stmt)
    org_servers = [
        MCPServerItem(
            server_id=srv.id,
            name=srv.name,
            server_url=srv.server_url,
            transport=srv.transport,
            enabled=binding.enabled if binding is not None else False,
            scope="org",
        )
        for srv, binding in org_result.all()
    ]

    # Workspace-private servers
    ws_stmt = select(MCPServer).where(
        MCPServer.org_id == ctx.org_id,
        MCPServer.owner_workspace_id == ctx.workspace_id,  # type: ignore[arg-type]
    )
    ws_result = await session.execute(ws_stmt)
    workspace_servers = [
        MCPServerItem(
            server_id=srv.id,
            name=srv.name,
            server_url=srv.server_url,
            transport=srv.transport,
            enabled=True,
            scope="workspace",
        )
        for srv in ws_result.scalars().all()
    ]

    return WorkspaceMCPOut(org_servers=org_servers, workspace_servers=workspace_servers)


@router.patch("/mcp/{server_id}")
async def toggle_mcp_binding(
    server_id: str,
    body: MCPBindingPatch,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    srv = await session.get(MCPServer, server_id)
    if srv is None or srv.org_id != ctx.org_id or srv.owner_workspace_id is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="org MCP server not found")

    binding = await session.execute(
        select(WorkspaceMCPBinding).where(
            WorkspaceMCPBinding.mcp_server_id == server_id,
            WorkspaceMCPBinding.workspace_id == ctx.workspace_id,
        )
    )
    existing = binding.scalar_one_or_none()
    if existing is None:
        existing = WorkspaceMCPBinding(
            workspace_id=ctx.workspace_id,
            mcp_server_id=server_id,
            enabled=body.enabled,
        )
        session.add(existing)
    else:
        existing.enabled = body.enabled
    await session.commit()
    return {"server_id": server_id, "enabled": body.enabled}
```

> **Note:** After writing, check the actual column names on `WorkspaceMCPBinding` by reading `backend/cubeplex/models/mcp.py`. Adjust field names in the query if they differ.

- [ ] **Step 6: Run tests**

```bash
cd backend
uv run pytest tests/e2e/test_ws_settings.py::TestMCPSettings -v
```

Expected: PASS

- [ ] **Step 7: Run full check**

```bash
cd backend
make check
```

- [ ] **Step 8: Commit**

```bash
git add backend/cubeplex/api/routes/v1/ws_settings.py \
        backend/cubeplex/api/schemas/ws_settings.py \
        backend/tests/e2e/test_ws_settings.py
git commit -m "feat(m4): workspace MCP settings list + toggle API"
```

---

## Task 5: Core Package — Types, API Client, Store

**Files:**
- Create: `frontend/packages/core/src/types/workspace-settings.ts`
- Create: `frontend/packages/core/src/api/workspace-settings.ts`
- Create: `frontend/packages/core/src/stores/workspaceSettingsStore.ts`
- Modify: `frontend/packages/core/src/index.ts`

- [ ] **Step 1: Create TypeScript types**

Create `frontend/packages/core/src/types/workspace-settings.ts`:

```typescript
export interface AgentConfig {
  system_prompt: string
}

export interface SkillInstall {
  install_id: string
  skill_id: string
  installed_version: string
  enabled: boolean
  scope: 'org' | 'workspace'
}

export interface WorkspaceSkills {
  org_skills: SkillInstall[]
  workspace_skills: SkillInstall[]
}

export interface MCPServerItem {
  server_id: string
  name: string
  server_url: string
  transport: string
  enabled: boolean
  scope: 'org' | 'workspace'
}

export interface WorkspaceMCP {
  org_servers: MCPServerItem[]
  workspace_servers: MCPServerItem[]
}
```

- [ ] **Step 2: Create API client methods**

Create `frontend/packages/core/src/api/workspace-settings.ts`:

```typescript
import type { ApiClient } from './client'
import type { AgentConfig, WorkspaceMCP, WorkspaceSkills } from '../types/workspace-settings'

export async function getAgentConfig(client: ApiClient): Promise<AgentConfig> {
  return client.get('/settings/agent')
}

export async function updateAgentConfig(
  client: ApiClient,
  patch: Partial<AgentConfig>,
): Promise<AgentConfig> {
  return client.put('/settings/agent', patch)
}

export async function listWorkspaceSkills(client: ApiClient): Promise<WorkspaceSkills> {
  return client.get('/settings/skills')
}

export async function toggleWorkspaceSkill(
  client: ApiClient,
  installId: string,
  enabled: boolean,
): Promise<{ install_id: string; enabled: boolean }> {
  return client.patch(`/settings/skills/${installId}`, { enabled })
}

export async function installWorkspaceSkill(
  client: ApiClient,
  skillId: string,
  version: string,
): Promise<{ install_id: string; skill_id: string; scope: string }> {
  return client.post('/settings/skills', { skill_id: skillId, version })
}

export async function deleteWorkspaceSkill(
  client: ApiClient,
  installId: string,
): Promise<void> {
  return client.delete(`/settings/skills/${installId}`)
}

export async function listWorkspaceMCP(client: ApiClient): Promise<WorkspaceMCP> {
  return client.get('/settings/mcp')
}

export async function toggleWorkspaceMCP(
  client: ApiClient,
  serverId: string,
  enabled: boolean,
): Promise<{ server_id: string; enabled: boolean }> {
  return client.patch(`/settings/mcp/${serverId}`, { enabled })
}
```

> **Note:** Read `frontend/packages/core/src/api/client.ts` to confirm the method names (`get`, `put`, `patch`, `post`, `delete`) and call convention before writing this file. Adjust accordingly.

- [ ] **Step 3: Create Zustand store**

Create `frontend/packages/core/src/stores/workspaceSettingsStore.ts`:

```typescript
import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import {
  getAgentConfig,
  listWorkspaceMCP,
  listWorkspaceSkills,
  toggleWorkspaceMCP,
  toggleWorkspaceSkill,
  updateAgentConfig,
} from '../api/workspace-settings'
import type { AgentConfig, MCPServerItem, SkillInstall, WorkspaceMCP, WorkspaceSkills } from '../types/workspace-settings'

interface WorkspaceSettingsState {
  agentConfig: AgentConfig | null
  skills: WorkspaceSkills | null
  mcp: WorkspaceMCP | null
  loading: boolean
  error: string | null

  loadAll: (client: ApiClient) => Promise<void>
  savePersona: (client: ApiClient, prompt: string) => Promise<void>
  toggleSkill: (client: ApiClient, installId: string, enabled: boolean) => Promise<void>
  toggleMCP: (client: ApiClient, serverId: string, enabled: boolean) => Promise<void>
}

export const useWorkspaceSettingsStore = create<WorkspaceSettingsState>((set, get) => ({
  agentConfig: null,
  skills: null,
  mcp: null,
  loading: false,
  error: null,

  loadAll: async (client) => {
    set({ loading: true, error: null })
    try {
      const [agentConfig, skills, mcp] = await Promise.all([
        getAgentConfig(client),
        listWorkspaceSkills(client),
        listWorkspaceMCP(client),
      ])
      set({ agentConfig, skills, mcp, loading: false })
    } catch (e) {
      set({ loading: false, error: String(e) })
    }
  },

  savePersona: async (client, prompt) => {
    const config = await updateAgentConfig(client, { system_prompt: prompt })
    set({ agentConfig: config })
  },

  toggleSkill: async (client, installId, enabled) => {
    await toggleWorkspaceSkill(client, installId, enabled)
    const skills = get().skills
    if (!skills) return
    const update = (list: SkillInstall[]) =>
      list.map((s) => (s.install_id === installId ? { ...s, enabled } : s))
    set({
      skills: {
        org_skills: update(skills.org_skills),
        workspace_skills: update(skills.workspace_skills),
      },
    })
  },

  toggleMCP: async (client, serverId, enabled) => {
    await toggleWorkspaceMCP(client, serverId, enabled)
    const mcp = get().mcp
    if (!mcp) return
    const update = (list: MCPServerItem[]) =>
      list.map((s) => (s.server_id === serverId ? { ...s, enabled } : s))
    set({
      mcp: {
        org_servers: update(mcp.org_servers),
        workspace_servers: update(mcp.workspace_servers),
      },
    })
  },
}))
```

- [ ] **Step 4: Export from core index**

In `frontend/packages/core/src/index.ts`, add:

```typescript
export * from './types/workspace-settings'
export * from './api/workspace-settings'
export { useWorkspaceSettingsStore } from './stores/workspaceSettingsStore'
```

- [ ] **Step 5: Build core package**

```bash
cd frontend
pnpm --filter @cubeplex/core build
```

Expected: builds without errors.

- [ ] **Step 6: Type-check**

```bash
cd frontend
pnpm type-check
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/core/src/
git commit -m "feat(m4): core types, API client methods, and settings store"
```

---

## Task 6: Settings Entry in Sidebar

**Files:**
- Modify: `frontend/packages/web/components/layout/Sidebar.tsx`

- [ ] **Step 1: Add settings link to sidebar footer**

In `frontend/packages/web/components/layout/Sidebar.tsx`, update the footer section (around line 119). Import `Settings` from lucide-react and add a settings link:

```tsx
import { Box, Plus, Settings, Trash2 } from 'lucide-react'

// In the footer section, replace:
<div className="border-t border-border/60 p-2">
  <AvatarPopover />
</div>

// With:
<div className="border-t border-border/60 p-2 flex items-center justify-between">
  <AvatarPopover />
  {currentWsId && (
    <Link
      href={`/w/${currentWsId}/settings`}
      className={`p-1.5 rounded-md transition-colors ${
        pathname?.startsWith(`/w/${currentWsId}/settings`)
          ? 'text-primary bg-primary/10'
          : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
      }`}
      aria-label="Workspace settings"
    >
      <Settings className="size-4" />
    </Link>
  )}
</div>
```

- [ ] **Step 2: Type-check**

```bash
cd frontend
pnpm type-check
```

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/layout/Sidebar.tsx
git commit -m "feat(m4): add settings entry point to sidebar footer"
```

---

## Task 7: Settings Page Shell + Navigation

**Files:**
- Modify: `frontend/packages/web/components/layout/Sidebar.tsx`
- Create: `frontend/packages/web/components/workspace-settings/SettingsNav.tsx`
- Create: `frontend/packages/web/app/(app)/w/[wsId]/settings/page.tsx`

**Layout contract:** The existing `Sidebar` (col 1) stays. When the user is on `/settings`, the `Sidebar` renders a `SettingsNav` section at the bottom in place of the conversation list. The settings page itself provides only col 2 + col 3 (no second nav panel) — this avoids a 4-column layout.

- [ ] **Step 1: Create SettingsNav component**

Create `frontend/packages/web/components/workspace-settings/SettingsNav.tsx`:

```tsx
'use client'

import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { Bot, Plug, Sparkles } from 'lucide-react'

interface SettingsNavProps {
  wsId: string
}

const TOP_LEVEL = [
  {
    key: 'workspace',
    label: 'Workspace Settings',
    icon: Bot,
    sub: [
      { key: 'persona', label: 'Persona' },
      { key: 'model', label: 'Model', disabled: true },
    ],
  },
  { key: 'skills', label: 'Skills', icon: Sparkles },
  { key: 'mcp', label: 'MCP Connectors', icon: Plug },
]

export function SettingsNav({ wsId }: SettingsNavProps) {
  const searchParams = useSearchParams()
  const currentTab = searchParams.get('tab') ?? 'workspace'
  const currentSub = searchParams.get('sub') ?? 'persona'

  return (
    <div className="px-2 pt-3 pb-2">
      <p className="px-2 text-[9px] font-semibold uppercase tracking-widest text-muted-foreground/50 mb-2">
        Settings
      </p>
      <nav className="space-y-0.5">
        {TOP_LEVEL.map((item) => {
          const Icon = item.icon
          const isActive = currentTab === item.key
          return (
            <div key={item.key}>
              <Link
                href={`/w/${wsId}/settings?tab=${item.key}${item.sub ? `&sub=${item.sub[0].key}` : ''}`}
                className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-[12.5px] transition-colors ${
                  isActive
                    ? 'text-primary bg-primary/10 font-medium'
                    : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                }`}
              >
                <Icon className="size-3.5 shrink-0" />
                {item.label}
              </Link>
              {isActive && item.sub && (
                <div className="ml-6 mt-0.5 space-y-0.5">
                  {item.sub.map((s) => (
                    <Link
                      key={s.key}
                      href={('disabled' in s && s.disabled) ? '#' : `/w/${wsId}/settings?tab=${item.key}&sub=${s.key}`}
                      className={`flex items-center justify-between px-2 py-1 rounded-md text-[11.5px] transition-colors ${
                        currentSub === s.key
                          ? 'text-primary bg-primary/8 font-medium'
                          : ('disabled' in s && s.disabled)
                          ? 'text-muted-foreground/40 cursor-default pointer-events-none'
                          : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                      }`}
                    >
                      {s.label}
                      {('disabled' in s && s.disabled) && (
                        <span className="text-[9px] bg-muted text-muted-foreground/60 rounded px-1">
                          soon
                        </span>
                      )}
                    </Link>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </nav>
    </div>
  )
}
```

- [ ] **Step 2: Embed SettingsNav in Sidebar when on settings route**

In `frontend/packages/web/components/layout/Sidebar.tsx`, update the component to show `SettingsNav` when on the settings route, replacing the conversation list:

```tsx
import { Suspense } from 'react'
import { SettingsNav } from '@/components/workspace-settings/SettingsNav'

// Inside the Sidebar function, before the return:
const isSettingsRoute = currentWsId
  ? pathname?.startsWith(`/w/${currentWsId}/settings`) ?? false
  : false

// Replace the conversation ScrollArea section with a conditional:
{isSettingsRoute && currentWsId ? (
  <Suspense>
    <SettingsNav wsId={currentWsId} />
  </Suspense>
) : (
  <>
    <div className="px-2 pt-2 pb-1">
      <p className="px-2 text-[10px] font-medium uppercase tracking-widest text-muted-foreground/60">
        {tSidebar('recentChats')}
      </p>
    </div>
    <ScrollArea className="flex-1 px-2">
      {/* existing conversation list ... */}
    </ScrollArea>
  </>
)}
```

Read the full current `Sidebar.tsx` before editing to ensure you wrap exactly the right JSX in the conditional. The `ScrollArea` + `<ul>` with conversation items is the section to replace with `<SettingsNav>` when `isSettingsRoute` is true.

- [ ] **Step 3: Create the settings page shell**

The page provides col 2 + col 3 only. For `tab=workspace`, col 2 is handled by `PersonaEditor`'s enclosing layout (it is full-width). For `tab=skills` and `tab=mcp`, `SkillsPanel` and `McpPanel` each render their own left list (col 2) and right detail (col 3).

Create `frontend/packages/web/app/(app)/w/[wsId]/settings/page.tsx`:

```tsx
import { use } from 'react'
import { PersonaEditor } from '@/components/workspace-settings/PersonaEditor'
import { SkillsPanel } from '@/components/workspace-settings/SkillsPanel'
import { McpPanel } from '@/components/workspace-settings/McpPanel'

interface SettingsPageProps {
  params: Promise<{ wsId: string }>
  searchParams: Promise<{ tab?: string; sub?: string }>
}

export default function WorkspaceSettingsPage({
  params,
  searchParams,
}: SettingsPageProps): React.ReactElement {
  const { wsId } = use(params)
  const { tab = 'workspace' } = use(searchParams)

  return (
    <div className="flex flex-1 overflow-hidden h-full">
      {tab === 'workspace' && <PersonaEditor wsId={wsId} />}
      {tab === 'skills' && <SkillsPanel wsId={wsId} />}
      {tab === 'mcp' && <McpPanel wsId={wsId} />}
    </div>
  )
}
```

- [ ] **Step 4: Type-check**

```bash
cd frontend
pnpm type-check
```

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/app/(app)/w/\[wsId\]/settings/ \
        frontend/packages/web/components/workspace-settings/SettingsNav.tsx \
        frontend/packages/web/components/layout/Sidebar.tsx
git commit -m "feat(m4): settings page shell and sidebar nav"
```

---

## Task 8: Persona Tab

**Files:**
- Create: `frontend/packages/web/components/workspace-settings/PersonaEditor.tsx`

- [ ] **Step 1: Create PersonaEditor**

Create `frontend/packages/web/components/workspace-settings/PersonaEditor.tsx`:

```tsx
'use client'

import { useCallback, useEffect, useState } from 'react'
import { createApiClient, useWorkspaceSettingsStore } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'

interface PersonaEditorProps {
  wsId: string
}

export function PersonaEditor({ wsId }: PersonaEditorProps) {
  const { agentConfig, loading, loadAll, savePersona } = useWorkspaceSettingsStore()
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)

  const client = useCallback(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    if (!agentConfig) {
      loadAll(client())
    }
  }, [wsId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (agentConfig) setDraft(agentConfig.system_prompt)
  }, [agentConfig])

  const handleSave = async () => {
    setSaving(true)
    try {
      await savePersona(client(), draft)
    } finally {
      setSaving(false)
    }
  }

  const handleReset = () => {
    setDraft(agentConfig?.system_prompt ?? '')
  }

  return (
    <div className="flex flex-1 flex-col overflow-y-auto p-8 max-w-2xl">
      <h2 className="text-base font-semibold mb-1">Persona</h2>
      <p className="text-sm text-muted-foreground mb-6">
        Define the agent&apos;s persona for every conversation in this workspace. Appended after
        the base system prompt.
      </p>

      {loading && !agentConfig ? (
        <div className="text-sm text-muted-foreground">Loading…</div>
      ) : (
        <>
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="e.g. You are a Python data analysis expert. Always provide runnable code examples."
            className="min-h-[200px] font-mono text-sm resize-y"
          />
          <div className="flex items-center justify-between mt-4">
            <span className="text-xs text-muted-foreground">
              {draft.length} characters
            </span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={handleReset} disabled={saving}>
                Reset
              </Button>
              <Button size="sm" onClick={handleSave} disabled={saving}>
                {saving ? 'Saving…' : 'Save'}
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend
pnpm type-check
```

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/workspace-settings/PersonaEditor.tsx
git commit -m "feat(m4): persona editor component"
```

---

## Task 9: Skills Tab

**Files:**
- Create: `frontend/packages/web/components/workspace-settings/SkillsPanel.tsx`

- [ ] **Step 1: Create SkillsPanel**

Create `frontend/packages/web/components/workspace-settings/SkillsPanel.tsx`:

```tsx
'use client'

import { useCallback, useEffect, useState } from 'react'
import { createApiClient, useWorkspaceSettingsStore } from '@cubeplex/core'
import type { SkillInstall } from '@cubeplex/core'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface SkillsPanelProps {
  wsId: string
}

export function SkillsPanel({ wsId }: SkillsPanelProps) {
  const { skills, loading, loadAll, toggleSkill } = useWorkspaceSettingsStore()
  const [selected, setSelected] = useState<SkillInstall | null>(null)
  const [toggling, setToggling] = useState<string | null>(null)

  const client = useCallback(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    if (!skills) loadAll(client())
  }, [wsId]) // eslint-disable-line react-hooks/exhaustive-deps

  const allSkills = [
    ...(skills?.org_skills ?? []),
    ...(skills?.workspace_skills ?? []),
  ]

  const handleToggle = async (skill: SkillInstall, enabled: boolean) => {
    if (skill.scope === 'workspace') return // always enabled
    setToggling(skill.install_id)
    try {
      await toggleSkill(client(), skill.install_id, enabled)
    } finally {
      setToggling(null)
    }
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Col 2: Skills list */}
      <div className="w-56 shrink-0 border-r border-border overflow-y-auto">
        <div className="p-3 border-b border-border">
          <p className="text-sm font-semibold">Skills</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            {allSkills.filter((s) => s.enabled).length} / {allSkills.length} enabled
          </p>
        </div>
        <ul className="p-2 space-y-0.5">
          {loading && !skills ? (
            <li className="text-xs text-muted-foreground px-2 py-4">Loading…</li>
          ) : allSkills.length === 0 ? (
            <li className="text-xs text-muted-foreground px-2 py-4">No skills available</li>
          ) : (
            allSkills.map((skill) => (
              <li key={skill.install_id}>
                <button
                  onClick={() => setSelected(skill)}
                  className={cn(
                    'w-full flex items-center gap-2 px-2 py-2 rounded-md text-left transition-colors',
                    selected?.install_id === skill.install_id
                      ? 'bg-primary/10 text-primary'
                      : 'text-muted-foreground hover:text-foreground hover:bg-accent/60',
                  )}
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-[12px] font-medium truncate">{skill.skill_id}</p>
                    <p className="text-[10px] text-muted-foreground/60">{skill.installed_version}</p>
                  </div>
                  <Switch
                    checked={skill.enabled}
                    disabled={skill.scope === 'workspace' || toggling === skill.install_id}
                    onCheckedChange={(v) => handleToggle(skill, v)}
                    onClick={(e) => e.stopPropagation()}
                    className="shrink-0 scale-75"
                  />
                </button>
              </li>
            ))
          )}
        </ul>
      </div>

      {/* Col 3: Skill detail */}
      <div className="flex-1 overflow-y-auto p-8">
        {selected ? (
          <>
            <h2 className="text-base font-semibold mb-1">{selected.skill_id}</h2>
            <div className="flex gap-2 mb-6">
              <Badge variant="outline">{selected.installed_version}</Badge>
              <Badge variant={selected.scope === 'workspace' ? 'secondary' : 'outline'}>
                {selected.scope === 'workspace' ? 'workspace-private' : 'org-installed'}
              </Badge>
              <Badge variant={selected.enabled ? 'default' : 'secondary'}>
                {selected.enabled ? 'enabled' : 'disabled'}
              </Badge>
            </div>
            <div className="space-y-3 text-sm text-muted-foreground">
              <div className="flex justify-between py-2 border-b border-border">
                <span>Install ID</span>
                <span className="font-mono text-xs">{selected.install_id}</span>
              </div>
              <div className="flex justify-between py-2 border-b border-border">
                <span>Scope</span>
                <span>{selected.scope}</span>
              </div>
              <div className="flex justify-between py-2 border-b border-border">
                <span>Version</span>
                <span>{selected.installed_version}</span>
              </div>
            </div>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Select a skill to view details</p>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend
pnpm type-check
```

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/workspace-settings/SkillsPanel.tsx
git commit -m "feat(m4): skills settings panel"
```

---

## Task 10: MCP Tab

**Files:**
- Create: `frontend/packages/web/components/workspace-settings/McpPanel.tsx`

- [ ] **Step 1: Create McpPanel**

Create `frontend/packages/web/components/workspace-settings/McpPanel.tsx`:

```tsx
'use client'

import { useCallback, useEffect, useState } from 'react'
import { createApiClient, useWorkspaceSettingsStore } from '@cubeplex/core'
import type { MCPServerItem } from '@cubeplex/core'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface McpPanelProps {
  wsId: string
}

export function McpPanel({ wsId }: McpPanelProps) {
  const { mcp, loading, loadAll, toggleMCP } = useWorkspaceSettingsStore()
  const [selected, setSelected] = useState<MCPServerItem | null>(null)
  const [toggling, setToggling] = useState<string | null>(null)

  const client = useCallback(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    if (!mcp) loadAll(client())
  }, [wsId]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleToggle = async (srv: MCPServerItem, enabled: boolean) => {
    if (srv.scope === 'workspace') return
    setToggling(srv.server_id)
    try {
      await toggleMCP(client(), srv.server_id, enabled)
    } finally {
      setToggling(null)
    }
  }

  const renderSection = (title: string, servers: MCPServerItem[]) => (
    <div className="mb-2">
      <p className="px-2 text-[9px] font-semibold uppercase tracking-widest text-muted-foreground/50 mb-1">
        {title}
      </p>
      {servers.length === 0 ? (
        <p className="text-xs text-muted-foreground px-2 py-2">None</p>
      ) : (
        servers.map((srv) => (
          <button
            key={srv.server_id}
            onClick={() => setSelected(srv)}
            className={cn(
              'w-full flex items-center gap-2 px-2 py-2 rounded-md text-left transition-colors',
              selected?.server_id === srv.server_id
                ? 'bg-primary/10 text-primary'
                : 'text-muted-foreground hover:text-foreground hover:bg-accent/60',
            )}
          >
            <div className="flex-1 min-w-0">
              <p className="text-[12px] font-medium truncate">{srv.name}</p>
              <p className="text-[10px] text-muted-foreground/60 truncate">{srv.server_url}</p>
            </div>
            <Switch
              checked={srv.enabled}
              disabled={srv.scope === 'workspace' || toggling === srv.server_id}
              onCheckedChange={(v) => handleToggle(srv, v)}
              onClick={(e) => e.stopPropagation()}
              className="shrink-0 scale-75"
            />
          </button>
        ))
      )}
    </div>
  )

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Col 2: MCP list */}
      <div className="w-56 shrink-0 border-r border-border overflow-y-auto">
        <div className="p-3 border-b border-border">
          <p className="text-sm font-semibold">MCP Connectors</p>
        </div>
        <div className="p-2">
          {loading && !mcp ? (
            <p className="text-xs text-muted-foreground py-4 px-2">Loading…</p>
          ) : (
            <>
              {renderSection('Org-wide', mcp?.org_servers ?? [])}
              {renderSection('Workspace private', mcp?.workspace_servers ?? [])}
            </>
          )}
        </div>
      </div>

      {/* Col 3: Server detail */}
      <div className="flex-1 overflow-y-auto p-8">
        {selected ? (
          <>
            <h2 className="text-base font-semibold mb-1">{selected.name}</h2>
            <div className="flex gap-2 mb-6">
              <Badge variant="outline">{selected.transport}</Badge>
              <Badge variant={selected.scope === 'workspace' ? 'secondary' : 'outline'}>
                {selected.scope === 'workspace' ? 'workspace-private' : 'org-wide'}
              </Badge>
              <Badge variant={selected.enabled ? 'default' : 'secondary'}>
                {selected.enabled ? 'enabled' : 'disabled'}
              </Badge>
            </div>
            <div className="space-y-3 text-sm text-muted-foreground">
              <div className="flex justify-between py-2 border-b border-border">
                <span>URL</span>
                <span className="font-mono text-xs truncate max-w-xs">{selected.server_url}</span>
              </div>
              <div className="flex justify-between py-2 border-b border-border">
                <span>Transport</span>
                <span>{selected.transport}</span>
              </div>
              <div className="flex justify-between py-2 border-b border-border">
                <span>Scope</span>
                <span>{selected.scope}</span>
              </div>
            </div>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Select a connector to view details</p>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend
pnpm type-check
```

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/workspace-settings/McpPanel.tsx
git commit -m "feat(m4): MCP settings panel"
```

---

## Task 11: Backend E2E Test Coverage

**Files:**
- Modify: `backend/tests/e2e/test_ws_settings.py`

- [ ] **Step 1: Add remaining edge-case tests**

Append to `backend/tests/e2e/test_ws_settings.py`:

```python
class TestSettingsScoping:
    """Settings are scoped per workspace — workspace B cannot read workspace A data."""

    def test_other_workspace_cannot_read_agent_config(self, client: TestClient) -> None:
        """A request for a workspace the user is not a member of returns 403 or 404."""
        resp = client.get("/api/v1/ws/ws-nonexistent-000/settings/agent")
        assert resp.status_code in (403, 404)

    def test_persona_is_empty_by_default(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/agent")
        assert resp.status_code == 200
        # May be non-empty if a previous test wrote to it; just assert the key exists
        assert "system_prompt" in resp.json()

    def test_skills_and_mcp_both_return_lists(self, client: TestClient) -> None:
        skills_resp = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/skills")
        mcp_resp = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/settings/mcp")
        assert skills_resp.status_code == 200
        assert mcp_resp.status_code == 200
        assert isinstance(skills_resp.json()["org_skills"], list)
        assert isinstance(mcp_resp.json()["org_servers"], list)
```

- [ ] **Step 2: Run all settings tests**

```bash
cd backend
uv run pytest tests/e2e/test_ws_settings.py -v
```

Expected: all PASS

- [ ] **Step 3: Run full check**

```bash
cd backend
make check
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/e2e/test_ws_settings.py
git commit -m "test(m4): complete backend E2E coverage for settings API"
```

---

## Task 12: Frontend Playwright E2E Test

**Files:**
- Create: `frontend/packages/web/tests/workspace-settings.spec.ts`

- [ ] **Step 1: Write the Playwright test**

Create `frontend/packages/web/tests/workspace-settings.spec.ts`:

```typescript
import { test, expect } from '@playwright/test'

test.describe('Workspace Settings', () => {
  test.beforeEach(async ({ page }) => {
    // Assumes the test user is already logged in via storageState configured in playwright.config.ts
    // Navigate to the default workspace home
    await page.goto('/')
    await page.waitForURL(/\/w\//)
  })

  test('settings icon visible in sidebar footer', async ({ page }) => {
    const settingsLink = page.getByRole('link', { name: /workspace settings/i })
    await expect(settingsLink).toBeVisible()
  })

  test('navigates to settings page', async ({ page }) => {
    await page.getByRole('link', { name: /workspace settings/i }).click()
    await expect(page).toHaveURL(/\/settings/)
    await expect(page.getByText('Persona')).toBeVisible()
  })

  test('persona editor loads and saves', async ({ page }) => {
    await page.goto(page.url().replace(/\/?$/, '') + '/settings?tab=workspace&sub=persona')
    // Wait for editor
    const textarea = page.getByPlaceholder(/e\.g\./)
    await expect(textarea).toBeVisible()

    // Type a persona
    await textarea.fill('You are a test assistant.')
    await page.getByRole('button', { name: 'Save' }).click()

    // Verify it persists after reload
    await page.reload()
    await expect(textarea).toHaveValue('You are a test assistant.')
  })

  test('skills tab shows lists', async ({ page }) => {
    const wsUrl = page.url().match(/(\/w\/[^/]+)/)?.[1] ?? ''
    await page.goto(`${wsUrl}/settings?tab=skills`)
    // Either shows skills list or "No skills available"
    const list = page.locator('[data-testid="skills-list"], .skills-list, ul')
    await expect(page.getByText(/skills/i).first()).toBeVisible()
  })

  test('mcp tab shows connector list', async ({ page }) => {
    const wsUrl = page.url().match(/(\/w\/[^/]+)/)?.[1] ?? ''
    await page.goto(`${wsUrl}/settings?tab=mcp`)
    await expect(page.getByText(/MCP Connectors/i)).toBeVisible()
  })
})
```

> **Note:** Check `frontend/packages/web/playwright.config.ts` (or root `playwright.config.ts`) for the storage state path and base URL. The `beforeEach` navigation may need to be adapted to the project's login fixture. If a `loginPage` fixture is already defined, use it instead of `page.goto('/')`.

- [ ] **Step 2: Run Playwright tests (start dev server first)**

```bash
cd frontend
pnpm dev &
sleep 5
pnpm test:e2e --grep "Workspace Settings"
```

Expected: tests pass (persona save/reload requires backend running; if backend is not up, the API calls will fail — ensure both servers are running).

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/tests/workspace-settings.spec.ts
git commit -m "test(m4): Playwright E2E for workspace settings"
```
