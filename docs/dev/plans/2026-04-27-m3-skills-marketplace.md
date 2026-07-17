# M3 Skills Marketplace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a per-deployment skills marketplace with 3-tier scope (deployment-global preinstalled pool → org marketplace → workspace bindings), turning today's filesystem-only "builtin skills" into a catalog-driven system. Members publish via drag-drop or via the `skill-creator` skill that produces a "skill artifact" the user can one-click publish.

**Architecture:** 5 new MySQL tables + 1 column on `Organization` (`slug`); object storage for skill files (no zip blob retained); FastAPI lifespan seeder with Redis named-lock for multi-replica safety; `SkillsMiddleware` and `load_skill` refactored to read from a `SkillCatalogService` instead of the filesystem; `LazySandbox._ensure()` transparently syncs workspace-enabled skill files into `/.skills/<name>/<version>/` so the agent never has to think about file sync; admin Skills tab replaces today's "Coming Soon" placeholder.

**Tech Stack:** FastAPI · SQLModel + Alembic · MySQL (aiomysql) · Redis (asyncio) · Next.js 16 · React 19 · Tailwind 4 · shadcn/ui · pytest-asyncio · Playwright.

**Spec:** `docs/superpowers/specs/2026-04-26-skills-marketplace-design.md`

---

## File Structure

### Backend new
- `backend/cubeplex/skills/__init__.py` — package
- `backend/cubeplex/skills/frontmatter.py` — `SkillFrontmatter` dataclass + `parse_skill_md()`
- `backend/cubeplex/skills/cache.py` — local extraction cache keyed by `skill_version_id`
- `backend/cubeplex/skills/seeder.py` — preinstalled-skills seeder + Redis lock
- `backend/cubeplex/skills/service.py` — `SkillCatalogService` + `SkillPublishService`
- `backend/cubeplex/skills/storage_paths.py` — single source of truth for object-storage layout
- `backend/cubeplex/models/skill.py` — 5 SQLModel classes
- `backend/cubeplex/repositories/skill.py` — repos for the 5 tables
- `backend/cubeplex/api/routes/v1/admin_skills.py`
- `backend/cubeplex/api/routes/v1/ws_skills.py`
- `backend/cubeplex/api/schemas/skill.py` — pydantic response models
- `backend/alembic/versions/<rev>_m3_skills_marketplace.py`
- `backend/scripts/dev/auto_install_preinstalled_for_existing_orgs.py`
- `backend/tests/e2e/test_skills_marketplace.py`
- `backend/tests/e2e/test_skills_artifact_flow.py` (Batch 2)
- `backend/tests/unit/test_skill_frontmatter.py`
- `backend/tests/fixtures/skill_frontmatter/*.json`

### Backend modify
- `backend/cubeplex/models/organization.py` — add `slug` column
- `backend/cubeplex/auth/users.py` — `UserManager.on_after_register` slugify
- `backend/cubeplex/middleware/skills.py` — refactor to catalog-driven
- `backend/cubeplex/middleware/artifacts.py` — note `artifact_type="skill"` (Batch 2)
- `backend/cubeplex/tools/builtin/load_skill.py` — refactor to catalog
- `backend/cubeplex/agents/graph.py` — drop `skills` param, inject service
- `backend/cubeplex/sandbox/base.py` — add `has_synced` / `mark_synced` to `Sandbox` ABC
- `backend/cubeplex/sandbox/lazy.py` — sync hook in `_ensure()`
- `backend/cubeplex/sandbox/manager.py` — drop `SkillLoader` call
- `backend/cubeplex/api/app.py` — register seeder + new routers
- `backend/config.yaml` — drop `sandbox.skills.builtin_dir`

### Backend delete
- `backend/cubeplex/sandbox/skills.py`

### Backend rename
- `backend/skills/builtin/` → `backend/skills/preinstalled/` (with SKILL.md path edits)

### Frontend new
- `frontend/packages/web/components/admin/skills/SkillsToolbar.tsx`
- `frontend/packages/web/components/admin/skills/SkillsList.tsx`
- `frontend/packages/web/components/admin/skills/SkillCard.tsx`
- `frontend/packages/web/components/admin/skills/SkillDetailPanel.tsx`
- `frontend/packages/web/components/admin/skills/OrgInstallActions.tsx`
- `frontend/packages/web/components/admin/skills/WorkspaceBindingsTable.tsx`
- `frontend/packages/web/components/admin/skills/UploadSkillModal.tsx`
- `frontend/packages/web/hooks/useAdminSkills.ts`
- `frontend/packages/web/hooks/useAdminSkill.ts`
- `frontend/packages/web/hooks/useWorkspaceSkills.ts`
- `frontend/packages/core/src/types/skills.ts`
- `frontend/packages/web/__tests__/e2e/skills/admin-skills-list.spec.ts`
- `frontend/packages/web/__tests__/e2e/skills/admin-skills-install.spec.ts`
- `frontend/packages/web/__tests__/e2e/skills/admin-skills-upload.spec.ts`
- `frontend/packages/web/__tests__/e2e/skills/admin-workspace-toggle.spec.ts`
- `frontend/packages/web/__tests__/e2e/skills/chat-skill-artifact-preview.spec.ts` (Batch 2)

### Frontend modify
- `frontend/packages/web/app/admin/skills/page.tsx` — replace `<ComingSoonCard>`
- `frontend/packages/web/components/panel/SkillView.tsx` — fetch from API
- `frontend/packages/web/components/panel/artifact/...` — register skill preview (Batch 2)

### Batch 2 only
- `backend/skills/preinstalled/skill-creator/SKILL.md`
- `frontend/packages/web/components/panel/artifact/SkillArtifactPreview.tsx`

---

## Conventions used in this plan

- **CWD for all backend commands:** `backend/` unless stated otherwise. Frontend commands run from `frontend/`.
- **Test invocation:** `uv run pytest <path> -v`. E2E tests under `tests/e2e/` get the `e2e` marker auto-applied via `conftest.py` (per `backend/CLAUDE.md`).
- **Type discipline:** all functions get type annotations; mypy strict.
- **Line length:** 100.
- **Commits per task:** one commit per task (or per logical sub-step where noted). Use `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` only when commits are agent-authored.
- **Branch:** `m3-skills-marketplace` (already set up in worktree).

---

# Batch 1 — Marketplace Foundation

## Task 0: `Organization.slug` column + bootstrap

**Files:**
- Modify: `backend/cubeplex/models/organization.py`
- Modify: `backend/cubeplex/auth/users.py`
- Create: `backend/alembic/versions/<rev>_add_org_slug.py`
- Test: `backend/tests/e2e/test_register_bootstrap.py` (extend existing)
- Test: `backend/tests/unit/test_org_slugify.py` (new, for the helper)

- [ ] **Step 1: Write failing unit test for slug helper**

Create `backend/tests/unit/test_org_slugify.py`:

```python
"""Unit tests for the org-name → slug helper."""

import pytest

from cubeplex.auth.users import _slugify_org_name


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Acme Inc", "acme-inc"),
        ("Foo's Org", "foo-s-org"),
        ("  Multiple   Spaces  ", "multiple-spaces"),
        ("UPPER CASE", "upper-case"),
        ("Unicode 公司", "unicode"),               # non-ASCII stripped
        ("---leading-dashes---", "leading-dashes"),
        ("a" * 50, "a" * 31),                       # truncated to 31
    ],
)
def test_slugify_org_name(name: str, expected: str) -> None:
    assert _slugify_org_name(name) == expected


def test_slugify_empty_falls_back() -> None:
    assert _slugify_org_name("") == "org"
    assert _slugify_org_name("公司") == "org"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_org_slugify.py -v
```

Expected: FAIL — `ImportError: cannot import name '_slugify_org_name' from 'cubeplex.auth.users'`.

- [ ] **Step 3: Add the slug helper to `users.py`**

Open `backend/cubeplex/auth/users.py` and add (after the existing imports, before `UserManager`):

```python
import re

_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_SLUG_DEDUP = re.compile(r"-{2,}")
_SLUG_MAX = 31


def _slugify_org_name(name: str) -> str:
    """Convert an org name into a URL-safe slug (max 31 chars).

    - lowercases, strips non-ASCII letters/digits, replaces runs with '-'
    - trims leading/trailing dashes
    - falls back to 'org' if the input slugifies to empty
    """
    lowered = name.strip().lower()
    raw = _SLUG_RE.sub("-", lowered)
    deduped = _SLUG_DEDUP.sub("-", raw).strip("-")
    if not deduped:
        return "org"
    return deduped[:_SLUG_MAX].rstrip("-")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_org_slugify.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Add `slug` column to Organization model**

Open `backend/cubeplex/models/organization.py` and replace the file with:

```python
"""Organization model — top-level tenant container."""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class Organization(SQLModel, table=True):
    __tablename__ = "organizations"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    name: str = Field(max_length=255)
    slug: str = Field(max_length=32, unique=True, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

- [ ] **Step 6: Generate Alembic migration with autogenerate**

```bash
uv run alembic revision --autogenerate -m "add_org_slug"
```

This creates `backend/alembic/versions/<rev>_add_org_slug.py`. Open it and replace the auto-generated body so we can backfill before adding NOT NULL + UNIQUE:

```python
def upgrade() -> None:
    # 1. Add nullable slug column
    op.add_column(
        "organizations",
        sa.Column("slug", sa.String(length=32), nullable=True),
    )

    # 2. Backfill slugs for existing rows
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, name FROM organizations")).fetchall()
    used: set[str] = set()
    for row in rows:
        base = _slugify(row.name)
        candidate = base
        n = 2
        while candidate in used:
            candidate = f"{base}-{n}"[:32]
            n += 1
        used.add(candidate)
        bind.execute(
            sa.text("UPDATE organizations SET slug = :slug WHERE id = :id"),
            {"slug": candidate, "id": row.id},
        )

    # 3. Enforce NOT NULL + UNIQUE
    op.alter_column("organizations", "slug", existing_type=sa.String(length=32), nullable=False)
    op.create_unique_constraint("uq_organizations_slug", "organizations", ["slug"])
    op.create_index("ix_organizations_slug", "organizations", ["slug"])


def downgrade() -> None:
    op.drop_index("ix_organizations_slug", table_name="organizations")
    op.drop_constraint("uq_organizations_slug", "organizations", type_="unique")
    op.drop_column("organizations", "slug")


# Local copy of the slugify helper so the migration is hermetic.
import re

_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_SLUG_DEDUP = re.compile(r"-{2,}")


def _slugify(name: str) -> str:
    lowered = name.strip().lower()
    raw = _SLUG_RE.sub("-", lowered)
    deduped = _SLUG_DEDUP.sub("-", raw).strip("-")
    if not deduped:
        return "org"
    return deduped[:31].rstrip("-")
```

- [ ] **Step 7: Apply migration locally and confirm**

```bash
uv run alembic upgrade head
uv run python -c "from cubeplex.db.engine import engine; import asyncio; from sqlalchemy import text; \
  conn=asyncio.run(engine.connect().__aenter__()); \
  print(asyncio.run(conn.execute(text('DESCRIBE organizations'))).fetchall())"
```

Expected: row for `slug` with type `varchar(32)`, NOT NULL.

(If you don't have a local DB, skip this step — CI will catch issues.)

- [ ] **Step 8: Wire slugify into `UserManager.on_after_register`**

Open `backend/cubeplex/auth/users.py`. Locate the `on_after_register` method (where it creates a personal Org). Find the line that constructs `Organization(name=...)`. Replace the org-creation block with:

```python
async def _allocate_org_slug(session: AsyncSession, base: str) -> str:
    """Pick a slug not already taken; append -2, -3, ... if needed."""
    candidate = base
    n = 2
    while True:
        existing = await session.execute(
            sa.select(Organization).where(Organization.slug == candidate)
        )
        if existing.scalar_one_or_none() is None:
            return candidate
        candidate = f"{base}-{n}"[:32].rstrip("-")
        n += 1
```

And inside `on_after_register`, replace:
```python
org = Organization(name=org_name)
```
with:
```python
slug = await _allocate_org_slug(session, _slugify_org_name(org_name))
org = Organization(name=org_name, slug=slug)
```

You'll also need to import `sqlalchemy as sa` and `Organization` if not already imported, plus add `_allocate_org_slug` near `_slugify_org_name`.

- [ ] **Step 9: Extend register-bootstrap E2E to assert slug is set**

Open `backend/tests/e2e/test_register_bootstrap.py`. Add a new test:

```python
@pytest.mark.asyncio
async def test_register_creates_org_with_slug(
    base_url: str,
) -> None:
    async with httpx.AsyncClient(base_url=base_url) as client:
        email = f"slugcheck-{secrets.token_hex(4)}@example.com"
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "test-password-12345"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # The register response gives default_workspace_id; pull org via /me-style flow.
        login = await client.post(
            "/api/v1/auth/login",
            data={"username": email, "password": "test-password-12345"},
        )
        assert login.status_code == 204
        # Hit a request-context endpoint to verify org exists with a slug.
        # Use the workspaces list to find the personal org.
        ws_resp = await client.get("/api/v1/workspaces")
        assert ws_resp.status_code == 200
        workspaces = ws_resp.json()
        assert len(workspaces) >= 1
        # Org slug surfaced via the workspace's org reference (or via a future /admin/me).
        # For this test, query DB directly via the test fixture session to confirm slug is non-empty.
        # NOTE: if /workspaces doesn't return org_slug, add it in a later task — for now,
        # assert the bootstrap created an org row with a slug column populated.
```

(The strict assertion form depends on what the existing test fixtures expose; if `/workspaces` doesn't include the org slug yet, defer the strict assert to Task 8 where the admin/skills endpoints will exercise it.)

- [ ] **Step 10: Run the full backend test suite locally to confirm no regressions**

```bash
uv run pytest tests/unit/ tests/e2e/test_register_bootstrap.py -v
```

Expected: all pass.

- [ ] **Step 11: Commit**

```bash
git add backend/cubeplex/models/organization.py \
        backend/cubeplex/auth/users.py \
        backend/alembic/versions/*_add_org_slug.py \
        backend/tests/unit/test_org_slugify.py \
        backend/tests/e2e/test_register_bootstrap.py
git commit -m "feat(m3): add Organization.slug column with auto-slugify on register"
```

---

## Task 1: Skill data model — 5 SQLModel classes

**Files:**
- Create: `backend/cubeplex/models/skill.py`
- Modify: `backend/cubeplex/models/__init__.py` (export new classes)

- [ ] **Step 1: Write the model file**

Create `backend/cubeplex/models/skill.py`:

```python
"""Skill catalog models — see docs/superpowers/specs/2026-04-26-skills-marketplace-design.md § 3."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7

from cubeplex.models.mixins import OrgScopedMixin


class Skill(SQLModel, table=True):
    """Global catalog row.

    source='preinstalled' → owner_org_id=NULL; name is bare slug.
    source='uploaded'     → owner_org_id=<publisher org>; name is '<org-slug>:<skill-slug>'.
    """

    __tablename__ = "skills"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    name: str = Field(max_length=128)
    source: str = Field(max_length=16)  # "preinstalled" | "uploaded"
    owner_org_id: str | None = Field(default=None, max_length=36, index=True)  # refs organizations.id
    current_version: str = Field(max_length=32)
    description: str = Field(max_length=1024)
    keywords: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("name", name="uq_skill_name"),
        Index("ix_skill_source_owner", "source", "owner_org_id"),
    )


class SkillVersion(SQLModel, table=True):
    """Immutable version row. New versions append; old rows never modified."""

    __tablename__ = "skill_versions"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    skill_id: str = Field(max_length=36, index=True)  # refs skills.id
    version: str = Field(max_length=32)
    description: str = Field(max_length=1024)
    keywords: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    raw_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    storage_prefix: str = Field(max_length=512)
    entry_file: str = Field(max_length=128, default="SKILL.md")
    uploaded_by_user_id: str | None = Field(default=None, max_length=36)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (UniqueConstraint("skill_id", "version", name="uq_skill_version"),)


class OrgSkillInstall(SQLModel, table=True):
    """Org-level install — admin promoted a skill into the org marketplace."""

    __tablename__ = "org_skill_installs"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)  # refs organizations.id
    skill_id: str = Field(max_length=36, index=True)  # refs skills.id
    installed_version: str = Field(max_length=32)
    installed_by_user_id: str = Field(max_length=36)  # refs users.id
    installed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (UniqueConstraint("org_id", "skill_id", name="uq_org_skill_install"),)


class WorkspaceSkillBinding(SQLModel, OrgScopedMixin, table=True):
    """Workspace-level enablement of an org-installed skill."""

    __tablename__ = "workspace_skill_bindings"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_skill_install_id: str = Field(max_length=36, index=True)  # refs org_skill_installs.id
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "org_skill_install_id", name="uq_workspace_skill_binding"
        ),
        Index("ix_wsb_org_ws", "org_id", "workspace_id"),
    )


class OrgPreinstalledTombstone(SQLModel, table=True):
    """Records that an org admin uninstalled a preinstalled skill; blocks reseed-restore."""

    __tablename__ = "org_preinstalled_tombstones"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)  # refs organizations.id
    skill_id: str = Field(max_length=36, index=True)  # refs skills.id
    hidden_by_user_id: str = Field(max_length=36)  # refs users.id
    hidden_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("org_id", "skill_id", name="uq_org_preinstalled_tombstone"),
    )
```

- [ ] **Step 2: Re-export from `models/__init__.py`**

Open `backend/cubeplex/models/__init__.py` and add to the existing exports:

```python
from cubeplex.models.skill import (
    OrgPreinstalledTombstone,
    OrgSkillInstall,
    Skill,
    SkillVersion,
    WorkspaceSkillBinding,
)

# add to __all__:
__all__ = [
    # ... existing entries ...
    "Skill",
    "SkillVersion",
    "OrgSkillInstall",
    "WorkspaceSkillBinding",
    "OrgPreinstalledTombstone",
]
```

- [ ] **Step 3: Generate Alembic migration**

```bash
uv run alembic revision --autogenerate -m "m3_skills_marketplace"
```

This creates `backend/alembic/versions/<rev>_m3_skills_marketplace.py`. Open it and verify it contains `op.create_table("skills", ...)`, `op.create_table("skill_versions", ...)`, etc. for all 5 tables. Trim any `sa.ForeignKeyConstraint(...)` lines (per D19, no DB FKs); keep all `sa.Column`, `sa.PrimaryKeyConstraint`, and `sa.UniqueConstraint` lines, and ensure indexes are present.

- [ ] **Step 4: Run mypy + apply migration**

```bash
uv run mypy cubeplex/models/skill.py
uv run alembic upgrade head
uv run alembic downgrade -1   # confirm reversibility
uv run alembic upgrade head
```

Expected: mypy clean; alembic upgrade/downgrade clean.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/models/skill.py \
        backend/cubeplex/models/__init__.py \
        backend/alembic/versions/*_m3_skills_marketplace.py
git commit -m "feat(m3): skill catalog models + Alembic migration"
```

---

## Task 2: Skill repositories

**Files:**
- Create: `backend/cubeplex/repositories/skill.py`
- Modify: `backend/cubeplex/repositories/__init__.py`
- Test: `backend/tests/e2e/test_skill_repositories.py`

- [ ] **Step 1: Write the failing E2E test**

Create `backend/tests/e2e/test_skill_repositories.py`:

```python
"""E2E: skill repositories CRUD + uniqueness invariants."""

import pytest

from cubeplex.models import OrgSkillInstall, Skill, SkillVersion
from cubeplex.repositories.skill import (
    OrgSkillInstallRepository,
    SkillRepository,
    SkillVersionRepository,
)


@pytest.mark.asyncio
async def test_create_preinstalled_skill_and_version(db_session) -> None:
    skills = SkillRepository(db_session)
    versions = SkillVersionRepository(db_session)

    skill = await skills.create_preinstalled(
        name="deep-research",
        description="Multi-agent research skill",
        keywords=["research"],
        current_version="1.0.0",
    )
    assert skill.id
    assert skill.source == "preinstalled"
    assert skill.owner_org_id is None

    version = await versions.create(
        skill_id=skill.id,
        version="1.0.0",
        description=skill.description,
        keywords=skill.keywords,
        raw_metadata={},
        storage_prefix="skills/_global/deep-research/1.0.0/",
        entry_file="SKILL.md",
        uploaded_by_user_id=None,
    )
    assert version.skill_id == skill.id
    assert version.version == "1.0.0"


@pytest.mark.asyncio
async def test_skill_name_unique(db_session) -> None:
    skills = SkillRepository(db_session)
    await skills.create_preinstalled(
        name="git-commit",
        description="Commit helper",
        keywords=[],
        current_version="0.1.0",
    )
    with pytest.raises(Exception):  # IntegrityError
        await skills.create_preinstalled(
            name="git-commit",
            description="dup",
            keywords=[],
            current_version="0.2.0",
        )


@pytest.mark.asyncio
async def test_org_install_unique_per_org(db_session) -> None:
    skills = SkillRepository(db_session)
    installs = OrgSkillInstallRepository(db_session)
    skill = await skills.create_preinstalled(
        name="deep-research",
        description="...",
        keywords=[],
        current_version="1.0.0",
    )
    await installs.upsert(
        org_id="org-1",
        skill_id=skill.id,
        installed_version="1.0.0",
        installed_by_user_id="user-1",
    )
    # Same org+skill, different version → updates the row, doesn't insert new.
    row = await installs.upsert(
        org_id="org-1",
        skill_id=skill.id,
        installed_version="1.1.0",
        installed_by_user_id="user-1",
    )
    assert row.installed_version == "1.1.0"
    rows = await installs.list_for_org("org-1")
    assert len(rows) == 1
```

The `db_session` fixture should already exist in `backend/tests/e2e/conftest.py` (used by other repo tests). If not, add it minimally.

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/e2e/test_skill_repositories.py -v
```

Expected: FAIL — `cannot import name 'SkillRepository'`.

- [ ] **Step 3: Implement the repositories**

Create `backend/cubeplex/repositories/skill.py`:

```python
"""Skill catalog repositories — see spec § 3."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import (
    OrgPreinstalledTombstone,
    OrgSkillInstall,
    Skill,
    SkillVersion,
    WorkspaceSkillBinding,
)
from cubeplex.repositories.base import ScopedRepository


class SkillRepository:
    """Global catalog. Not org-scoped (rows can be NULL-org for preinstalled)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, skill_id: str) -> Skill | None:
        return await self.session.get(Skill, skill_id)

    async def find_by_name(self, name: str) -> Skill | None:
        result = await self.session.execute(select(Skill).where(Skill.name == name))
        return result.scalar_one_or_none()

    async def create_preinstalled(
        self,
        *,
        name: str,
        description: str,
        keywords: list[str],
        current_version: str,
    ) -> Skill:
        skill = Skill(
            name=name,
            source="preinstalled",
            owner_org_id=None,
            current_version=current_version,
            description=description,
            keywords=keywords,
        )
        self.session.add(skill)
        await self.session.commit()
        await self.session.refresh(skill)
        return skill

    async def create_uploaded(
        self,
        *,
        canonical_name: str,
        owner_org_id: str,
        description: str,
        keywords: list[str],
        current_version: str,
    ) -> Skill:
        skill = Skill(
            name=canonical_name,
            source="uploaded",
            owner_org_id=owner_org_id,
            current_version=current_version,
            description=description,
            keywords=keywords,
        )
        self.session.add(skill)
        await self.session.commit()
        await self.session.refresh(skill)
        return skill

    async def update_current_version(
        self, skill_id: str, version: str, description: str, keywords: list[str]
    ) -> None:
        skill = await self.get(skill_id)
        if skill is None:
            return
        skill.current_version = version
        skill.description = description
        skill.keywords = keywords
        skill.updated_at = datetime.now(UTC)
        await self.session.commit()

    async def list_visible_for_org(
        self, org_id: str, *, source: str | None = None
    ) -> list[Skill]:
        """Catalog visible to org_id: preinstalled (any) + uploaded (own org)."""
        from sqlalchemy import or_

        stmt = select(Skill).where(
            or_(
                Skill.source == "preinstalled",
                (Skill.source == "uploaded") & (Skill.owner_org_id == org_id),
            )
        )
        if source is not None:
            stmt = stmt.where(Skill.source == source)
        stmt = stmt.order_by(Skill.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class SkillVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, version_id: str) -> SkillVersion | None:
        return await self.session.get(SkillVersion, version_id)

    async def find(self, skill_id: str, version: str) -> SkillVersion | None:
        result = await self.session.execute(
            select(SkillVersion).where(
                SkillVersion.skill_id == skill_id, SkillVersion.version == version
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        skill_id: str,
        version: str,
        description: str,
        keywords: list[str],
        raw_metadata: dict[str, Any],
        storage_prefix: str,
        entry_file: str,
        uploaded_by_user_id: str | None,
    ) -> SkillVersion:
        sv = SkillVersion(
            skill_id=skill_id,
            version=version,
            description=description,
            keywords=keywords,
            raw_metadata=raw_metadata,
            storage_prefix=storage_prefix,
            entry_file=entry_file,
            uploaded_by_user_id=uploaded_by_user_id,
        )
        self.session.add(sv)
        await self.session.commit()
        await self.session.refresh(sv)
        return sv

    async def list_for_skill(self, skill_id: str) -> list[SkillVersion]:
        result = await self.session.execute(
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill_id)
            .order_by(SkillVersion.created_at.desc())
        )
        return list(result.scalars().all())


class OrgSkillInstallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, org_id: str, skill_id: str) -> OrgSkillInstall | None:
        result = await self.session.execute(
            select(OrgSkillInstall).where(
                OrgSkillInstall.org_id == org_id,
                OrgSkillInstall.skill_id == skill_id,
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        org_id: str,
        skill_id: str,
        installed_version: str,
        installed_by_user_id: str,
    ) -> OrgSkillInstall:
        existing = await self.get(org_id, skill_id)
        if existing is not None:
            existing.installed_version = installed_version
            existing.installed_by_user_id = installed_by_user_id
            existing.installed_at = datetime.now(UTC)
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        row = OrgSkillInstall(
            org_id=org_id,
            skill_id=skill_id,
            installed_version=installed_version,
            installed_by_user_id=installed_by_user_id,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete(self, org_id: str, skill_id: str) -> bool:
        row = await self.get(org_id, skill_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.commit()
        return True

    async def list_for_org(self, org_id: str) -> list[OrgSkillInstall]:
        result = await self.session.execute(
            select(OrgSkillInstall).where(OrgSkillInstall.org_id == org_id)
        )
        return list(result.scalars().all())


class WorkspaceSkillBindingRepository(ScopedRepository[WorkspaceSkillBinding]):
    model = WorkspaceSkillBinding

    async def get_by_install(
        self, org_skill_install_id: str
    ) -> WorkspaceSkillBinding | None:
        stmt = self._scoped_select().where(
            WorkspaceSkillBinding.org_skill_install_id == org_skill_install_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def enable(self, org_skill_install_id: str) -> WorkspaceSkillBinding:
        existing = await self.get_by_install(org_skill_install_id)
        if existing is not None:
            existing.enabled = True
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        row = WorkspaceSkillBinding(
            org_skill_install_id=org_skill_install_id,
            enabled=True,
        )
        return await self.add(row)

    async def disable(self, org_skill_install_id: str) -> bool:
        existing = await self.get_by_install(org_skill_install_id)
        if existing is None:
            return False
        await self.session.delete(existing)
        await self.session.commit()
        return True

    async def list_enabled(self) -> list[WorkspaceSkillBinding]:
        stmt = self._scoped_select().where(WorkspaceSkillBinding.enabled.is_(True))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class OrgPreinstalledTombstoneRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, org_id: str, skill_id: str) -> OrgPreinstalledTombstone | None:
        result = await self.session.execute(
            select(OrgPreinstalledTombstone).where(
                OrgPreinstalledTombstone.org_id == org_id,
                OrgPreinstalledTombstone.skill_id == skill_id,
            )
        )
        return result.scalar_one_or_none()

    async def add_tombstone(
        self, *, org_id: str, skill_id: str, hidden_by_user_id: str
    ) -> OrgPreinstalledTombstone:
        row = OrgPreinstalledTombstone(
            org_id=org_id,
            skill_id=skill_id,
            hidden_by_user_id=hidden_by_user_id,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def remove_tombstone(self, org_id: str, skill_id: str) -> bool:
        row = await self.get(org_id, skill_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.commit()
        return True

    async def list_for_org(self, org_id: str) -> list[OrgPreinstalledTombstone]:
        result = await self.session.execute(
            select(OrgPreinstalledTombstone).where(
                OrgPreinstalledTombstone.org_id == org_id
            )
        )
        return list(result.scalars().all())
```

- [ ] **Step 4: Re-export from `repositories/__init__.py`**

Add to `backend/cubeplex/repositories/__init__.py`:

```python
from cubeplex.repositories.skill import (
    OrgPreinstalledTombstoneRepository,
    OrgSkillInstallRepository,
    SkillRepository,
    SkillVersionRepository,
    WorkspaceSkillBindingRepository,
)
```

And include in `__all__`.

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/e2e/test_skill_repositories.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/repositories/skill.py \
        backend/cubeplex/repositories/__init__.py \
        backend/tests/e2e/test_skill_repositories.py
git commit -m "feat(m3): skill repositories with E2E tests"
```

---

## Task 3: Frontmatter parser + storage path helper

**Files:**
- Create: `backend/cubeplex/skills/__init__.py` (empty)
- Create: `backend/cubeplex/skills/frontmatter.py`
- Create: `backend/cubeplex/skills/storage_paths.py`
- Test: `backend/tests/unit/test_skill_frontmatter.py`

- [ ] **Step 1: Write failing unit tests**

Create `backend/tests/unit/test_skill_frontmatter.py`:

```python
"""Unit tests for SKILL.md frontmatter parsing."""

import pytest

from cubeplex.skills.frontmatter import (
    InvalidFrontmatterError,
    SkillFrontmatter,
    parse_skill_md,
)


def test_minimal_valid_frontmatter() -> None:
    text = """---
name: my-skill
description: Does a thing.
version: 1.0.0
---

# My Skill
"""
    fm = parse_skill_md(text)
    assert fm.name == "my-skill"
    assert fm.description == "Does a thing."
    assert fm.version == "1.0.0"
    assert fm.keywords == []
    assert fm.raw_metadata["name"] == "my-skill"


def test_keywords_as_list() -> None:
    text = """---
name: x
description: y
version: 0.1
keywords:
  - foo
  - bar
---
"""
    fm = parse_skill_md(text)
    assert fm.keywords == ["foo", "bar"]


def test_keywords_as_csv_string_normalised() -> None:
    text = """---
name: x
description: y
version: 0.1
keywords: foo, bar, baz
---
"""
    fm = parse_skill_md(text)
    assert fm.keywords == ["foo", "bar", "baz"]


def test_openclaw_alias_merged_into_raw_metadata() -> None:
    text = """---
name: x
description: y
version: 0.1
clawdbot:
  requires:
    bins: [git]
---
"""
    fm = parse_skill_md(text)
    # Alias keys merged at top level of raw_metadata
    assert fm.raw_metadata["requires"] == {"bins": ["git"]}


def test_alias_overrides_top_level() -> None:
    text = """---
name: x
description: y
version: 0.1
requires:
  bins: [old]
openclaw:
  requires:
    bins: [new]
---
"""
    fm = parse_skill_md(text)
    assert fm.raw_metadata["requires"] == {"bins": ["new"]}


def test_unknown_fields_preserved() -> None:
    text = """---
name: x
description: y
version: 0.1
custom_field: hello
---
"""
    fm = parse_skill_md(text)
    assert fm.raw_metadata["custom_field"] == "hello"


@pytest.mark.parametrize(
    "field",
    ["name", "description", "version"],
)
def test_required_field_missing(field: str) -> None:
    fields = {"name": "x", "description": "y", "version": "0.1"}
    fields.pop(field)
    body = "\n".join(f"{k}: {v}" for k, v in fields.items())
    text = f"---\n{body}\n---\n"
    with pytest.raises(InvalidFrontmatterError) as exc:
        parse_skill_md(text)
    assert exc.value.field == field


def test_no_frontmatter_block() -> None:
    text = "# Just markdown, no YAML block\n"
    with pytest.raises(InvalidFrontmatterError):
        parse_skill_md(text)


def test_version_with_whitespace_rejected() -> None:
    text = """---
name: x
description: y
version: " 1 0 "
---
"""
    with pytest.raises(InvalidFrontmatterError) as exc:
        parse_skill_md(text)
    assert exc.value.field == "version"
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/unit/test_skill_frontmatter.py -v
```

Expected: ImportError on `cubeplex.skills.frontmatter`.

- [ ] **Step 3: Implement parser**

Create `backend/cubeplex/skills/__init__.py` (empty file).

Create `backend/cubeplex/skills/frontmatter.py`:

```python
"""SKILL.md YAML frontmatter parser. Replaces the regex parser in middleware/skills.py.

See spec § 4.3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(\n|$)", re.DOTALL)
_OPENCLAW_ALIASES = ("clawdbot", "clawdis", "openclaw")


@dataclass(frozen=True)
class InvalidFrontmatterError(Exception):
    field: str
    reason: str

    def __str__(self) -> str:
        return f"invalid frontmatter field {self.field!r}: {self.reason}"


@dataclass(frozen=True)
class SkillFrontmatter:
    name: str
    description: str
    version: str
    keywords: list[str] = field(default_factory=list)
    raw_metadata: dict[str, Any] = field(default_factory=dict)


def parse_skill_md(text: str) -> SkillFrontmatter:
    """Parse a SKILL.md document; return its frontmatter.

    Raises InvalidFrontmatterError if the YAML block is missing, malformed,
    or required fields are missing/invalid.
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise InvalidFrontmatterError(
            field="<block>",
            reason="missing YAML frontmatter; expected '---\\n...\\n---' at top",
        )

    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        raise InvalidFrontmatterError(field="<block>", reason=f"YAML parse error: {e}") from e

    if not isinstance(data, dict):
        raise InvalidFrontmatterError(
            field="<block>", reason="frontmatter must be a YAML mapping"
        )

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise InvalidFrontmatterError(field="name", reason="required, non-empty string")
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise InvalidFrontmatterError(field="description", reason="required, non-empty string")
    version_raw = data.get("version")
    version = str(version_raw).strip() if version_raw is not None else ""
    if not version or any(c.isspace() for c in version):
        raise InvalidFrontmatterError(
            field="version",
            reason="required, non-empty, must not contain whitespace",
        )

    keywords = _normalise_keywords(data.get("keywords"))

    raw_metadata: dict[str, Any] = dict(data)
    for alias in _OPENCLAW_ALIASES:
        nested = raw_metadata.pop(alias, None)
        if isinstance(nested, dict):
            for k, v in nested.items():
                raw_metadata[k] = v  # alias overrides any top-level same-name field

    return SkillFrontmatter(
        name=name.strip(),
        description=description.strip(),
        version=version,
        keywords=keywords,
        raw_metadata=raw_metadata,
    )


def _normalise_keywords(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return []
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/unit/test_skill_frontmatter.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Implement storage paths helper**

Create `backend/cubeplex/skills/storage_paths.py`:

```python
"""Single source of truth for object-storage layout used by skill files.

See spec § 4.1.
"""

from __future__ import annotations


def global_skill_prefix(skill_slug: str, version: str) -> str:
    """Storage prefix for a preinstalled skill version."""
    return f"skills/_global/{skill_slug}/{version}/"


def org_skill_prefix(org_id: str, skill_slug: str, version: str) -> str:
    """Storage prefix for an org-uploaded skill version."""
    return f"skills/{org_id}/{skill_slug}/{version}/"


def skill_object_key(prefix: str, rel_path: str) -> str:
    """Compose a full object key from prefix + relative path inside the bundle."""
    rel = rel_path.lstrip("/")
    return f"{prefix}{rel}"
```

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/skills/__init__.py \
        backend/cubeplex/skills/frontmatter.py \
        backend/cubeplex/skills/storage_paths.py \
        backend/tests/unit/test_skill_frontmatter.py
git commit -m "feat(m3): YAML frontmatter parser + storage path helper"
```

---

## Task 4: Skill cache layer (local extraction cache)

**Files:**
- Create: `backend/cubeplex/skills/cache.py`
- Test: `backend/tests/e2e/test_skill_cache.py`

- [ ] **Step 1: Write failing E2E test**

Create `backend/tests/e2e/test_skill_cache.py`:

```python
"""E2E: skill cache extracts files from object storage on first miss."""

import pytest

from cubeplex.objectstore import get_objectstore_client
from cubeplex.skills.cache import SkillCache
from cubeplex.skills.storage_paths import global_skill_prefix


@pytest.mark.asyncio
async def test_cache_fetches_files_on_miss(tmp_path) -> None:
    # Seed object storage directly
    store = get_objectstore_client()
    prefix = global_skill_prefix("test-skill", "1.0.0")
    await store.put(f"{prefix}SKILL.md", b"---\nname: test-skill\ndescription: x\nversion: 1.0.0\n---\n# T")
    await store.put(f"{prefix}scripts/run.sh", b"#!/bin/sh\necho hi\n")

    cache = SkillCache(cache_root=tmp_path)
    files = await cache.list_files("sv-test-id", storage_prefix=prefix)
    rel_paths = sorted(f[0] for f in files)
    assert rel_paths == ["SKILL.md", "scripts/run.sh"]
    assert dict(files)["SKILL.md"].startswith(b"---")


@pytest.mark.asyncio
async def test_cache_concurrent_extractions_dedupe(tmp_path) -> None:
    import asyncio

    store = get_objectstore_client()
    prefix = global_skill_prefix("dedup", "1.0.0")
    await store.put(f"{prefix}SKILL.md", b"---\nname: x\ndescription: y\nversion: 1\n---")

    cache = SkillCache(cache_root=tmp_path)

    async def fetch():
        return await cache.list_files("sv-dedup", storage_prefix=prefix)

    results = await asyncio.gather(*(fetch() for _ in range(5)))
    # All five should produce the same result
    for r in results:
        assert sorted(p for p, _ in r) == ["SKILL.md"]
```

- [ ] **Step 2: Run, verify fails**

```bash
uv run pytest tests/e2e/test_skill_cache.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement cache**

Create `backend/cubeplex/skills/cache.py`:

```python
"""Local extraction cache for skill files fetched from object storage.

Layout: <cache_root>/<skill_version_id>/<rel_path>
Concurrent calls for the same skill_version_id deduplicate via per-key asyncio lock.

See spec § 4.2.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from cubeplex.objectstore import get_objectstore_client


class SkillCache:
    """Per-process extraction cache. Cheap to instantiate (lock state stored)."""

    def __init__(self, cache_root: Path) -> None:
        self._root = cache_root
        self._locks: dict[str, asyncio.Lock] = {}

    def cache_dir(self, skill_version_id: str) -> Path:
        return self._root / skill_version_id

    def _lock_for(self, skill_version_id: str) -> asyncio.Lock:
        lock = self._locks.get(skill_version_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[skill_version_id] = lock
        return lock

    async def ensure_extracted(
        self, skill_version_id: str, *, storage_prefix: str
    ) -> Path:
        """Returns local cache dir for this version. Fetches+extracts on miss."""
        target = self.cache_dir(skill_version_id)
        sentinel = target / ".extracted"

        async with self._lock_for(skill_version_id):
            if sentinel.exists():
                return target
            target.mkdir(parents=True, exist_ok=True)

            store = get_objectstore_client()
            keys = await store.list_keys(storage_prefix)
            for key in keys:
                if not key.startswith(storage_prefix):
                    continue
                rel = key[len(storage_prefix) :]
                if not rel:
                    continue
                content = await store.get(key)
                local_path = target / rel
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(content)

            sentinel.write_bytes(b"")
            logger.debug(
                "Skill cache: extracted {} files for {}", len(keys), skill_version_id
            )
        return target

    async def list_files(
        self, skill_version_id: str, *, storage_prefix: str
    ) -> list[tuple[str, bytes]]:
        """Returns [(rel_path, bytes), ...] suitable for Sandbox.upload(...).
        Fetches via cache (extracts if missing)."""
        cache_dir = await self.ensure_extracted(
            skill_version_id, storage_prefix=storage_prefix
        )
        out: list[tuple[str, bytes]] = []
        for path in cache_dir.rglob("*"):
            if not path.is_file() or path.name == ".extracted":
                continue
            rel = path.relative_to(cache_dir).as_posix()
            out.append((rel, path.read_bytes()))
        return out

    def cache_root(self) -> Path:
        return self._root
```

- [ ] **Step 4: Inspect object-store client signatures**

```bash
uv run python -c "from cubeplex.objectstore import get_objectstore_client; \
  c=get_objectstore_client(); print(dir(c))"
```

If `list_keys` / `put` / `get` aren't the actual method names, adjust the cache implementation to match (likely `list_objects` / `upload_bytes` / `download_bytes`). Examine `backend/cubeplex/objectstore/client.py` and tweak the calls in `cache.py`. Also tweak the test seeding calls accordingly.

- [ ] **Step 5: Run, verify pass**

```bash
uv run pytest tests/e2e/test_skill_cache.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/skills/cache.py \
        backend/tests/e2e/test_skill_cache.py
git commit -m "feat(m3): skill cache (object-storage extraction with per-key locks)"
```

---

## Task 5: Preinstalled seeder + Redis lock

**Files:**
- Create: `backend/cubeplex/skills/seeder.py`
- Test: `backend/tests/e2e/test_skills_seeder.py`
- Modify: `backend/skills/builtin/` → rename to `backend/skills/preinstalled/` (later in Task 13; for now just create alongside or reference paths from config)

For this task, set up the seeder to read from a configurable directory. The actual rename happens in Task 13.

- [ ] **Step 1: Write failing E2E test**

Create `backend/tests/e2e/test_skills_seeder.py`:

```python
"""E2E: preinstalled skill seeder."""

from pathlib import Path

import pytest
from redis.asyncio import Redis

from cubeplex.config import config as _config
from cubeplex.repositories.skill import SkillRepository, SkillVersionRepository
from cubeplex.skills.seeder import seed_preinstalled_skills


def _write_skill_md(dir_: Path, name: str, version: str, description: str = "x") -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nversion: {version}\n---\n# {name}\n"
    )


@pytest.mark.asyncio
async def test_seed_creates_global_rows(
    tmp_path: Path, db_session, redis_client: Redis
) -> None:
    src = tmp_path / "preinstalled"
    _write_skill_md(src / "deep-research", name="deep-research", version="1.0.0")
    _write_skill_md(src / "git-commit", name="git-commit", version="0.1.0")

    await seed_preinstalled_skills(
        preinstalled_dir=src, db_session=db_session, redis=redis_client
    )

    skills = SkillRepository(db_session)
    versions = SkillVersionRepository(db_session)

    deep = await skills.find_by_name("deep-research")
    assert deep is not None
    assert deep.source == "preinstalled"
    assert deep.owner_org_id is None
    assert deep.current_version == "1.0.0"

    deep_versions = await versions.list_for_skill(deep.id)
    assert len(deep_versions) == 1
    assert deep_versions[0].storage_prefix == "skills/_global/deep-research/1.0.0/"


@pytest.mark.asyncio
async def test_seed_idempotent(tmp_path, db_session, redis_client: Redis) -> None:
    src = tmp_path / "preinstalled"
    _write_skill_md(src / "x", name="x", version="1.0.0")

    await seed_preinstalled_skills(
        preinstalled_dir=src, db_session=db_session, redis=redis_client
    )
    await seed_preinstalled_skills(
        preinstalled_dir=src, db_session=db_session, redis=redis_client
    )

    skills = SkillRepository(db_session)
    skill = await skills.find_by_name("x")
    versions = await SkillVersionRepository(db_session).list_for_skill(skill.id)
    assert len(versions) == 1


@pytest.mark.asyncio
async def test_seed_adds_new_version_on_bump(
    tmp_path, db_session, redis_client: Redis
) -> None:
    src = tmp_path / "preinstalled"
    _write_skill_md(src / "x", name="x", version="1.0.0")
    await seed_preinstalled_skills(
        preinstalled_dir=src, db_session=db_session, redis=redis_client
    )

    # Bump version
    _write_skill_md(src / "x", name="x", version="1.1.0")
    await seed_preinstalled_skills(
        preinstalled_dir=src, db_session=db_session, redis=redis_client
    )

    skills = SkillRepository(db_session)
    skill = await skills.find_by_name("x")
    assert skill.current_version == "1.1.0"
    versions = await SkillVersionRepository(db_session).list_for_skill(skill.id)
    assert sorted(v.version for v in versions) == ["1.0.0", "1.1.0"]


@pytest.mark.asyncio
async def test_seed_redis_lock_prevents_concurrent_runs(
    tmp_path, db_session, redis_client: Redis
) -> None:
    src = tmp_path / "preinstalled"
    _write_skill_md(src / "x", name="x", version="1.0.0")

    # Acquire the lock manually so seeder finds it held
    holder = redis_client.lock("cubeplex:lock:skill_seeder", timeout=10, blocking=False)
    acquired = await holder.acquire()
    assert acquired

    try:
        # Seeder should skip
        await seed_preinstalled_skills(
            preinstalled_dir=src, db_session=db_session, redis=redis_client
        )
        skills = SkillRepository(db_session)
        assert await skills.find_by_name("x") is None
    finally:
        await holder.release()

    # Now seed should run
    await seed_preinstalled_skills(
        preinstalled_dir=src, db_session=db_session, redis=redis_client
    )
    assert await SkillRepository(db_session).find_by_name("x") is not None
```

You may need to add a `redis_client` fixture in `tests/e2e/conftest.py` if it doesn't exist:

```python
@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Redis]:
    client = Redis.from_url(
        _cubeplex_config.get("redis.url", "redis://127.0.0.1:6379/0"),
        decode_responses=False,
    )
    try:
        yield client
    finally:
        await client.aclose()
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/e2e/test_skills_seeder.py -v
```

Expected: ImportError on `cubeplex.skills.seeder`.

- [ ] **Step 3: Implement seeder**

Create `backend/cubeplex/skills/seeder.py`:

```python
"""Preinstalled-skills seeder: walks preinstalled/ → upserts global skill rows
+ uploads files to skills/_global/<name>/<version>/. Multi-replica safe via
Redis named lock. See spec § 8.2."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from redis.asyncio import Redis
from redis.exceptions import LockNotOwnedError
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.objectstore import get_objectstore_client
from cubeplex.repositories.skill import SkillRepository, SkillVersionRepository
from cubeplex.skills.frontmatter import parse_skill_md
from cubeplex.skills.storage_paths import global_skill_prefix, skill_object_key

LOCK_KEY = "cubeplex:lock:skill_seeder"
LOCK_TTL_SECONDS = 60


async def seed_preinstalled_skills(
    *,
    preinstalled_dir: Path,
    db_session: AsyncSession,
    redis: Redis,
) -> None:
    """Idempotently seed preinstalled skills into the global catalog.

    Multi-replica safe: only one process holding the Redis lock will run the
    seed; others log + return.
    """
    if not preinstalled_dir.exists():
        logger.info("Preinstalled dir does not exist; skipping seed: {}", preinstalled_dir)
        return

    lock = redis.lock(LOCK_KEY, timeout=LOCK_TTL_SECONDS, blocking=False)
    acquired = await lock.acquire()
    if not acquired:
        logger.info("Skill seeder: lock held; skipping this run")
        return

    try:
        await _do_seed(preinstalled_dir, db_session)
    finally:
        try:
            await lock.release()
        except LockNotOwnedError:
            pass


async def _do_seed(preinstalled_dir: Path, db_session: AsyncSession) -> None:
    skills = SkillRepository(db_session)
    versions = SkillVersionRepository(db_session)
    store = get_objectstore_client()

    for skill_dir in sorted(preinstalled_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md_path = skill_dir / "SKILL.md"
        if not skill_md_path.exists():
            logger.warning("Preinstalled skill {} has no SKILL.md; skipping", skill_dir.name)
            continue

        text = skill_md_path.read_text(encoding="utf-8")
        try:
            fm = parse_skill_md(text)
        except Exception as e:
            logger.error("Failed to parse {}/SKILL.md: {}", skill_dir.name, e)
            continue

        # 1. Upsert Skill row
        skill = await skills.find_by_name(fm.name)
        if skill is None:
            skill = await skills.create_preinstalled(
                name=fm.name,
                description=fm.description,
                keywords=fm.keywords,
                current_version=fm.version,
            )
        elif skill.current_version != fm.version:
            # Bump current_version pointer to new version (added below)
            await skills.update_current_version(
                skill.id, fm.version, fm.description, fm.keywords
            )

        # 2. INSERT SkillVersion if not exists
        existing = await versions.find(skill.id, fm.version)
        if existing is not None:
            continue  # already seeded this version

        prefix = global_skill_prefix(fm.name, fm.version)

        # 3. Upload all files in skill_dir to object storage under prefix
        for file_path in skill_dir.rglob("*"):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(skill_dir).as_posix()
            key = skill_object_key(prefix, rel)
            await store.put(key, file_path.read_bytes())

        # 4. Insert SkillVersion row
        await versions.create(
            skill_id=skill.id,
            version=fm.version,
            description=fm.description,
            keywords=fm.keywords,
            raw_metadata=fm.raw_metadata,
            storage_prefix=prefix,
            entry_file="SKILL.md",
            uploaded_by_user_id=None,
        )
        logger.info("Seeded preinstalled skill {} v{}", fm.name, fm.version)
```

If the object-storage client method is not `put` / `list_keys`, adjust to match (see Task 4 Step 4).

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/e2e/test_skills_seeder.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/skills/seeder.py \
        backend/tests/e2e/test_skills_seeder.py \
        backend/tests/e2e/conftest.py
git commit -m "feat(m3): preinstalled-skills seeder with Redis named-lock"
```

---

## Task 6: SkillCatalogService (read path)

**Files:**
- Create: `backend/cubeplex/skills/service.py` (catalog half only; publish half in Task 7)
- Test: `backend/tests/e2e/test_skills_service_catalog.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/e2e/test_skills_service_catalog.py`:

```python
"""E2E: SkillCatalogService.list_enabled_for_workspace + fetch_skill_md."""

import pytest

from cubeplex.objectstore import get_objectstore_client
from cubeplex.repositories.skill import (
    OrgSkillInstallRepository,
    SkillRepository,
    SkillVersionRepository,
    WorkspaceSkillBindingRepository,
)
from cubeplex.skills.cache import SkillCache
from cubeplex.skills.service import SkillCatalogService
from cubeplex.skills.storage_paths import global_skill_prefix


@pytest.mark.asyncio
async def test_list_enabled_for_workspace(tmp_path, db_session) -> None:
    skills = SkillRepository(db_session)
    versions = SkillVersionRepository(db_session)
    installs = OrgSkillInstallRepository(db_session)

    org_id, ws_id, user_id = "org-1", "ws-1", "user-1"
    skill = await skills.create_preinstalled(
        name="deep-research", description="d", keywords=[], current_version="1.0.0"
    )
    prefix = global_skill_prefix("deep-research", "1.0.0")
    await get_objectstore_client().put(
        f"{prefix}SKILL.md",
        b"---\nname: deep-research\ndescription: d\nversion: 1.0.0\n---\n# DR\n",
    )
    await versions.create(
        skill_id=skill.id,
        version="1.0.0",
        description="d",
        keywords=[],
        raw_metadata={},
        storage_prefix=prefix,
        entry_file="SKILL.md",
        uploaded_by_user_id=None,
    )
    install = await installs.upsert(
        org_id=org_id,
        skill_id=skill.id,
        installed_version="1.0.0",
        installed_by_user_id=user_id,
    )

    bindings = WorkspaceSkillBindingRepository(
        db_session, org_id=org_id, workspace_id=ws_id
    )
    await bindings.enable(install.id)

    catalog = SkillCatalogService(
        session=db_session, cache=SkillCache(cache_root=tmp_path / "cache")
    )
    resolved = await catalog.list_enabled_for_workspace(ws_id, org_id=org_id)
    assert len(resolved) == 1
    assert resolved[0].name == "deep-research"
    assert resolved[0].version == "1.0.0"


@pytest.mark.asyncio
async def test_fetch_skill_md_returns_content(tmp_path, db_session) -> None:
    skills = SkillRepository(db_session)
    versions = SkillVersionRepository(db_session)

    skill = await skills.create_preinstalled(
        name="x", description="y", keywords=[], current_version="1.0.0"
    )
    prefix = global_skill_prefix("x", "1.0.0")
    await get_objectstore_client().put(f"{prefix}SKILL.md", b"# Hello\n")
    sv = await versions.create(
        skill_id=skill.id,
        version="1.0.0",
        description="y",
        keywords=[],
        raw_metadata={},
        storage_prefix=prefix,
        entry_file="SKILL.md",
        uploaded_by_user_id=None,
    )

    catalog = SkillCatalogService(
        session=db_session, cache=SkillCache(cache_root=tmp_path / "cache")
    )
    content = await catalog.fetch_skill_md(sv.id)
    assert content == "# Hello\n"
```

- [ ] **Step 2: Run, verify fails (ImportError)**

```bash
uv run pytest tests/e2e/test_skills_service_catalog.py -v
```

- [ ] **Step 3: Implement catalog service**

Create `backend/cubeplex/skills/service.py`:

```python
"""Skill marketplace services — read path (catalog) + write path (publish).

See spec §§ 5.2, 7.1, 7.2.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import OrgSkillInstall, Skill, SkillVersion, WorkspaceSkillBinding
from cubeplex.skills.cache import SkillCache


@dataclass(frozen=True)
class ResolvedSkill:
    """A skill enabled in a workspace, resolved to a specific version."""

    skill_id: str
    skill_version_id: str
    name: str
    description: str
    version: str
    storage_prefix: str
    entry_file: str


class SkillCatalogService:
    """Read-path service: list workspace-enabled skills, fetch SKILL.md content."""

    def __init__(self, *, session: AsyncSession, cache: SkillCache) -> None:
        self.session = session
        self.cache = cache

    async def list_enabled_for_workspace(
        self, workspace_id: str, *, org_id: str
    ) -> list[ResolvedSkill]:
        """JOIN bindings → installs → skills → matching version."""
        stmt = (
            select(Skill, SkillVersion)
            .join(OrgSkillInstall, OrgSkillInstall.skill_id == Skill.id)
            .join(
                SkillVersion,
                (SkillVersion.skill_id == Skill.id)
                & (SkillVersion.version == OrgSkillInstall.installed_version),
            )
            .join(
                WorkspaceSkillBinding,
                WorkspaceSkillBinding.org_skill_install_id == OrgSkillInstall.id,
            )
            .where(
                WorkspaceSkillBinding.workspace_id == workspace_id,
                WorkspaceSkillBinding.enabled.is_(True),
                OrgSkillInstall.org_id == org_id,
            )
            .order_by(Skill.name)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            ResolvedSkill(
                skill_id=skill.id,
                skill_version_id=sv.id,
                name=skill.name,
                description=sv.description,
                version=sv.version,
                storage_prefix=sv.storage_prefix,
                entry_file=sv.entry_file,
            )
            for (skill, sv) in rows
        ]

    async def find_enabled_by_name(
        self, workspace_id: str, *, org_id: str, name: str
    ) -> ResolvedSkill | None:
        for r in await self.list_enabled_for_workspace(workspace_id, org_id=org_id):
            if r.name == name:
                return r
        return None

    async def fetch_skill_md(self, skill_version_id: str) -> str:
        """Read SKILL.md content via local cache. Never touches sandbox."""
        sv = await self.session.get(SkillVersion, skill_version_id)
        if sv is None:
            raise ValueError(f"skill_version_id not found: {skill_version_id}")
        cache_dir = await self.cache.ensure_extracted(
            sv.id, storage_prefix=sv.storage_prefix
        )
        return (cache_dir / sv.entry_file).read_text(encoding="utf-8")

    async def list_files_for_sandbox_sync(
        self, skill_version_id: str, *, storage_prefix: str
    ) -> list[tuple[str, bytes]]:
        return await self.cache.list_files(skill_version_id, storage_prefix=storage_prefix)
```

- [ ] **Step 4: Run, verify pass**

```bash
uv run pytest tests/e2e/test_skills_service_catalog.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/skills/service.py \
        backend/tests/e2e/test_skills_service_catalog.py
git commit -m "feat(m3): SkillCatalogService (list_enabled + fetch_skill_md)"
```

---

## Task 7: SkillPublishService (zip → marketplace)

**Files:**
- Modify: `backend/cubeplex/skills/service.py` (append `SkillPublishService`)
- Test: `backend/tests/e2e/test_skills_publish_service.py`

- [ ] **Step 1: Write failing E2E test**

Create `backend/tests/e2e/test_skills_publish_service.py`:

```python
"""E2E: SkillPublishService.publish_from_zip."""

import io
import zipfile

import pytest

from cubeplex.repositories.skill import (
    OrgSkillInstallRepository,
    SkillRepository,
    SkillVersionRepository,
)
from cubeplex.skills.cache import SkillCache
from cubeplex.skills.service import SkillPublishService


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_publish_from_zip_creates_skill_version_and_install(
    tmp_path, db_session
) -> None:
    zip_bytes = _make_zip(
        {
            "SKILL.md": b"---\nname: my-skill\ndescription: ms\nversion: 0.1.0\n---\n# X\n",
            "scripts/run.sh": b"#!/bin/sh\n",
        }
    )

    publisher = SkillPublishService(
        session=db_session, cache=SkillCache(cache_root=tmp_path / "cache")
    )
    sv = await publisher.publish_from_zip(
        org_id="org-1",
        org_slug="acme",
        actor_user_id="user-1",
        zip_bytes=zip_bytes,
    )

    skill = await SkillRepository(db_session).find_by_name("acme:my-skill")
    assert skill is not None
    assert skill.source == "uploaded"
    assert skill.owner_org_id == "org-1"
    assert sv.version == "0.1.0"
    assert sv.storage_prefix == "skills/org-1/my-skill/0.1.0/"

    # Auto-install in publisher's org
    install = await OrgSkillInstallRepository(db_session).get("org-1", skill.id)
    assert install is not None
    assert install.installed_version == "0.1.0"


@pytest.mark.asyncio
async def test_publish_version_collision_raises(tmp_path, db_session) -> None:
    from cubeplex.skills.service import VersionCollisionError

    z = _make_zip({"SKILL.md": b"---\nname: x\ndescription: y\nversion: 1.0.0\n---\n"})
    publisher = SkillPublishService(
        session=db_session, cache=SkillCache(cache_root=tmp_path / "cache")
    )
    await publisher.publish_from_zip(
        org_id="org-2", org_slug="o", actor_user_id="u", zip_bytes=z
    )
    with pytest.raises(VersionCollisionError):
        await publisher.publish_from_zip(
            org_id="org-2", org_slug="o", actor_user_id="u", zip_bytes=z
        )


@pytest.mark.asyncio
async def test_publish_invalid_frontmatter_raises(tmp_path, db_session) -> None:
    from cubeplex.skills.frontmatter import InvalidFrontmatterError

    z = _make_zip({"SKILL.md": b"# no frontmatter\n"})
    publisher = SkillPublishService(
        session=db_session, cache=SkillCache(cache_root=tmp_path / "cache")
    )
    with pytest.raises(InvalidFrontmatterError):
        await publisher.publish_from_zip(
            org_id="o", org_slug="o", actor_user_id="u", zip_bytes=z
        )


@pytest.mark.asyncio
async def test_publish_rejects_name_with_colon(tmp_path, db_session) -> None:
    from cubeplex.skills.service import InvalidSkillNameError

    z = _make_zip(
        {"SKILL.md": b"---\nname: foo:bar\ndescription: y\nversion: 1.0.0\n---\n"}
    )
    publisher = SkillPublishService(
        session=db_session, cache=SkillCache(cache_root=tmp_path / "cache")
    )
    with pytest.raises(InvalidSkillNameError):
        await publisher.publish_from_zip(
            org_id="o", org_slug="o", actor_user_id="u", zip_bytes=z
        )


@pytest.mark.asyncio
async def test_publish_rejects_oversized_file(tmp_path, db_session) -> None:
    from cubeplex.skills.service import FileTooLargeError

    big = b"x" * (11 * 1024 * 1024)
    z = _make_zip(
        {
            "SKILL.md": b"---\nname: x\ndescription: y\nversion: 1.0.0\n---\n",
            "big.bin": big,
        }
    )
    publisher = SkillPublishService(
        session=db_session, cache=SkillCache(cache_root=tmp_path / "cache")
    )
    with pytest.raises(FileTooLargeError):
        await publisher.publish_from_zip(
            org_id="o", org_slug="o", actor_user_id="u", zip_bytes=z
        )
```

- [ ] **Step 2: Run, verify fails**

```bash
uv run pytest tests/e2e/test_skills_publish_service.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement publish service**

Open `backend/cubeplex/skills/service.py` and append:

```python
import io
import re
import zipfile
from typing import IO

from cubeplex.objectstore import get_objectstore_client
from cubeplex.repositories.skill import (
    OrgSkillInstallRepository,
    SkillRepository,
    SkillVersionRepository,
)
from cubeplex.skills.frontmatter import InvalidFrontmatterError, parse_skill_md
from cubeplex.skills.storage_paths import org_skill_prefix, skill_object_key

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
SKILL_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class InvalidSkillNameError(ValueError):
    pass


class VersionCollisionError(ValueError):
    pass


class FileTooLargeError(ValueError):
    pass


class SkillMdMissingError(ValueError):
    pass


class SkillPublishService:
    """Write-path: extract zip → validate → upload → DB transaction."""

    def __init__(self, *, session: AsyncSession, cache: SkillCache) -> None:
        self.session = session
        self.cache = cache

    async def publish_from_zip(
        self,
        *,
        org_id: str,
        org_slug: str,
        actor_user_id: str,
        zip_bytes: bytes,
    ) -> SkillVersion:
        """Stream-extract, validate, upload, insert. Returns the new SkillVersion."""
        files = _extract_zip(zip_bytes)
        return await self._publish_from_files(
            org_id=org_id,
            org_slug=org_slug,
            actor_user_id=actor_user_id,
            files=files,
        )

    async def _publish_from_files(
        self,
        *,
        org_id: str,
        org_slug: str,
        actor_user_id: str,
        files: dict[str, bytes],
    ) -> SkillVersion:
        if "SKILL.md" not in files:
            raise SkillMdMissingError("zip must contain SKILL.md at root")
        fm = parse_skill_md(files["SKILL.md"].decode("utf-8"))

        if ":" in fm.name:
            raise InvalidSkillNameError(
                "frontmatter 'name' must not contain ':'; the org prefix is added by the server"
            )
        if not SKILL_SLUG_RE.match(fm.name):
            raise InvalidSkillNameError(
                f"name must match {SKILL_SLUG_RE.pattern}; got {fm.name!r}"
            )

        canonical_name = f"{org_slug}:{fm.name}"
        skills = SkillRepository(self.session)
        versions = SkillVersionRepository(self.session)
        installs = OrgSkillInstallRepository(self.session)

        existing_skill = await skills.find_by_name(canonical_name)
        if existing_skill is not None:
            existing_version = await versions.find(existing_skill.id, fm.version)
            if existing_version is not None:
                raise VersionCollisionError(
                    f"version {fm.version} already exists for {canonical_name}"
                )

        prefix = org_skill_prefix(org_id, fm.name, fm.version)

        # Upload all files
        store = get_objectstore_client()
        for rel, data in files.items():
            await store.put(skill_object_key(prefix, rel), data)

        # DB transaction
        if existing_skill is None:
            skill = await skills.create_uploaded(
                canonical_name=canonical_name,
                owner_org_id=org_id,
                description=fm.description,
                keywords=fm.keywords,
                current_version=fm.version,
            )
        else:
            await skills.update_current_version(
                existing_skill.id, fm.version, fm.description, fm.keywords
            )
            skill = existing_skill

        sv = await versions.create(
            skill_id=skill.id,
            version=fm.version,
            description=fm.description,
            keywords=fm.keywords,
            raw_metadata=fm.raw_metadata,
            storage_prefix=prefix,
            entry_file="SKILL.md",
            uploaded_by_user_id=actor_user_id,
        )
        await installs.upsert(
            org_id=org_id,
            skill_id=skill.id,
            installed_version=fm.version,
            installed_by_user_id=actor_user_id,
        )
        return sv


def _extract_zip(zip_bytes: bytes) -> dict[str, bytes]:
    """Stream-extract a .zip into a {rel_path: bytes} dict, enforcing size caps."""
    out: dict[str, bytes] = {}
    total = 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes), mode="r") as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            if info.file_size > MAX_FILE_BYTES:
                raise FileTooLargeError(
                    f"{info.filename} is {info.file_size} bytes; cap is {MAX_FILE_BYTES}"
                )
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise FileTooLargeError(
                    f"bundle exceeds total cap of {MAX_TOTAL_BYTES} bytes"
                )
            with z.open(info) as fp:
                out[info.filename] = fp.read()
    return out
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/e2e/test_skills_publish_service.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/skills/service.py \
        backend/tests/e2e/test_skills_publish_service.py
git commit -m "feat(m3): SkillPublishService.publish_from_zip"
```

---

## Task 8: Pydantic API schemas

**Files:**
- Create: `backend/cubeplex/api/schemas/skill.py`

- [ ] **Step 1: Define schemas**

Create `backend/cubeplex/api/schemas/skill.py`:

```python
"""Pydantic response schemas for skill marketplace endpoints."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class SkillFiles(BaseModel):
    rel_path: str
    size: int
    mime: str | None = None


class SkillSummary(BaseModel):
    """Row in the marketplace list view."""

    id: str
    name: str
    source: Literal["preinstalled", "uploaded"]
    description: str
    current_version: str
    keywords: list[str]
    install_state: Literal["uninstalled", "installed", "update_available"]
    installed_version: str | None = None
    workspace_bindings_count: int = 0


class SkillVersionDetail(BaseModel):
    id: str
    version: str
    description: str
    keywords: list[str]
    storage_prefix: str
    entry_file: str
    uploaded_by_user_id: str | None
    created_at: datetime


class SkillDetail(BaseModel):
    id: str
    name: str
    source: Literal["preinstalled", "uploaded"]
    description: str
    current_version: str
    keywords: list[str]
    versions: list[SkillVersionDetail]
    install_state: Literal["uninstalled", "installed", "update_available"]
    installed_version: str | None = None


class SkillContentResponse(BaseModel):
    """Used by preview endpoints; returns SKILL.md content + sibling files list."""

    skill_id: str
    skill_version_id: str
    name: str
    version: str
    content: str
    files: list[SkillFiles]


class InstallRequest(BaseModel):
    version: str


class WorkspaceBindingsRequest(BaseModel):
    skill_ids: list[str]


class PublishFromArtifactRequest(BaseModel):
    artifact_id: str
```

- [ ] **Step 2: Commit**

```bash
git add backend/cubeplex/api/schemas/skill.py
git commit -m "feat(m3): pydantic response schemas for skill marketplace"
```

---

## Task 9: Admin HTTP routes

**Files:**
- Create: `backend/cubeplex/api/routes/v1/admin_skills.py`
- Modify: `backend/cubeplex/api/app.py` (mount router — wired in Task 15)

This task implements the admin endpoints listed in spec § 5.1. Test in Task 16 (the comprehensive E2E suite).

- [ ] **Step 1: Implement admin_skills.py**

Create `backend/cubeplex/api/routes/v1/admin_skills.py`:

```python
"""Admin-only skill marketplace endpoints. Gated by require_org_admin (M2).

See spec § 5.1.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.skill import (
    InstallRequest,
    SkillContentResponse,
    SkillDetail,
    SkillFiles,
    SkillSummary,
    SkillVersionDetail,
    WorkspaceBindingsRequest,
)
from cubeplex.auth.dependencies import RequestContext, get_request_context, require_org_admin
from cubeplex.db.session import get_session
from cubeplex.models import (
    OrgPreinstalledTombstone,
    Organization,
    User,
    Workspace,
)
from cubeplex.repositories.organization import OrganizationRepository
from cubeplex.repositories.skill import (
    OrgPreinstalledTombstoneRepository,
    OrgSkillInstallRepository,
    SkillRepository,
    SkillVersionRepository,
    WorkspaceSkillBindingRepository,
)
from cubeplex.skills.cache import SkillCache
from cubeplex.skills.service import (
    FileTooLargeError,
    InvalidSkillNameError,
    SkillCatalogService,
    SkillMdMissingError,
    SkillPublishService,
    VersionCollisionError,
)
from cubeplex.skills.frontmatter import InvalidFrontmatterError
from cubeplex.config import config as _config
from pathlib import Path

router = APIRouter(prefix="/admin/skills", tags=["admin-skills"])


def _cache() -> SkillCache:
    cache_root = Path(_config.get("skills.cache_root", "skills_cache"))
    return SkillCache(cache_root=cache_root)


@router.get("", response_model=list[SkillSummary])
async def list_skills(
    *,
    user: Annotated[User, Depends(require_org_admin)],
    rc: Annotated[RequestContext, Depends(get_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: str | None = Query(None),
    installed: bool | None = Query(None),
    q: str | None = Query(None),
    tag: str | None = Query(None),
) -> list[SkillSummary]:
    skills = await SkillRepository(session).list_visible_for_org(
        rc.org_id, source=source
    )
    installs_repo = OrgSkillInstallRepository(session)
    bindings_repo = WorkspaceSkillBindingRepository(
        session, org_id=rc.org_id, workspace_id=""  # workspace_id unused for org-level count
    )

    summaries: list[SkillSummary] = []
    for s in skills:
        if q and q.lower() not in s.name.lower() and q.lower() not in s.description.lower():
            continue
        if tag and tag not in s.keywords:
            continue
        install = await installs_repo.get(rc.org_id, s.id)
        if install is None:
            install_state = "uninstalled"
            installed_version: str | None = None
        elif install.installed_version != s.current_version:
            install_state = "update_available"
            installed_version = install.installed_version
        else:
            install_state = "installed"
            installed_version = install.installed_version

        if installed is True and install is None:
            continue
        if installed is False and install is not None:
            continue

        summaries.append(
            SkillSummary(
                id=s.id,
                name=s.name,
                source=s.source,  # type: ignore[arg-type]
                description=s.description,
                current_version=s.current_version,
                keywords=s.keywords,
                install_state=install_state,  # type: ignore[arg-type]
                installed_version=installed_version,
                workspace_bindings_count=0,  # TODO: query when needed; cheap to skip in v1
            )
        )
    return summaries


@router.get("/{skill_id}", response_model=SkillDetail)
async def get_skill(
    skill_id: str,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    rc: Annotated[RequestContext, Depends(get_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillDetail:
    skill = await SkillRepository(session).get(skill_id)
    if skill is None or not _visible(skill, rc.org_id):
        raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")

    versions = await SkillVersionRepository(session).list_for_skill(skill_id)
    install = await OrgSkillInstallRepository(session).get(rc.org_id, skill_id)
    if install is None:
        install_state = "uninstalled"
        installed_version = None
    elif install.installed_version != skill.current_version:
        install_state = "update_available"
        installed_version = install.installed_version
    else:
        install_state = "installed"
        installed_version = install.installed_version

    return SkillDetail(
        id=skill.id,
        name=skill.name,
        source=skill.source,  # type: ignore[arg-type]
        description=skill.description,
        current_version=skill.current_version,
        keywords=skill.keywords,
        versions=[
            SkillVersionDetail(
                id=v.id,
                version=v.version,
                description=v.description,
                keywords=v.keywords,
                storage_prefix=v.storage_prefix,
                entry_file=v.entry_file,
                uploaded_by_user_id=v.uploaded_by_user_id,
                created_at=v.created_at,
            )
            for v in versions
        ],
        install_state=install_state,  # type: ignore[arg-type]
        installed_version=installed_version,
    )


@router.get("/{skill_id}/versions/{version}", response_model=SkillContentResponse)
async def get_skill_version(
    skill_id: str,
    version: str,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    rc: Annotated[RequestContext, Depends(get_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillContentResponse:
    skill = await SkillRepository(session).get(skill_id)
    if skill is None or not _visible(skill, rc.org_id):
        raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")
    sv = await SkillVersionRepository(session).find(skill_id, version)
    if sv is None:
        raise HTTPException(status_code=404, detail="SKILL_VERSION_NOT_FOUND")

    catalog = SkillCatalogService(session=session, cache=_cache())
    content = await catalog.fetch_skill_md(sv.id)
    files_list = await catalog.list_files_for_sandbox_sync(
        sv.id, storage_prefix=sv.storage_prefix
    )
    return SkillContentResponse(
        skill_id=skill.id,
        skill_version_id=sv.id,
        name=skill.name,
        version=sv.version,
        content=content,
        files=[SkillFiles(rel_path=p, size=len(b)) for p, b in files_list],
    )


@router.post("/{skill_id}/install", status_code=200)
async def install_skill(
    skill_id: str,
    body: InstallRequest,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    rc: Annotated[RequestContext, Depends(get_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    skill = await SkillRepository(session).get(skill_id)
    if skill is None or not _visible(skill, rc.org_id):
        raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")
    sv = await SkillVersionRepository(session).find(skill_id, body.version)
    if sv is None:
        raise HTTPException(status_code=404, detail="SKILL_VERSION_NOT_FOUND")

    # Remove tombstone if present (admin re-installing a previously hidden preinstalled)
    tomb_repo = OrgPreinstalledTombstoneRepository(session)
    await tomb_repo.remove_tombstone(rc.org_id, skill_id)

    install = await OrgSkillInstallRepository(session).upsert(
        org_id=rc.org_id,
        skill_id=skill_id,
        installed_version=body.version,
        installed_by_user_id=user.id,
    )
    return {"install_id": install.id, "installed_version": install.installed_version}


@router.delete("/{skill_id}/install", status_code=204)
async def uninstall_skill(
    skill_id: str,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    rc: Annotated[RequestContext, Depends(get_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    skill = await SkillRepository(session).get(skill_id)
    if skill is None or not _visible(skill, rc.org_id):
        raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")
    install = await OrgSkillInstallRepository(session).get(rc.org_id, skill_id)
    if install is not None:
        # Cascade: delete all WorkspaceSkillBinding rows for this install in this org
        from sqlalchemy import select
        from cubeplex.models import WorkspaceSkillBinding

        result = await session.execute(
            select(WorkspaceSkillBinding).where(
                WorkspaceSkillBinding.org_id == rc.org_id,
                WorkspaceSkillBinding.org_skill_install_id == install.id,
            )
        )
        for binding in result.scalars().all():
            await session.delete(binding)
        await session.delete(install)
        await session.commit()

    # Tombstone for preinstalled
    if skill.source == "preinstalled":
        await OrgPreinstalledTombstoneRepository(session).add_tombstone(
            org_id=rc.org_id, skill_id=skill_id, hidden_by_user_id=user.id
        )


@router.post("/upload", status_code=201)
async def upload_skill(
    file: Annotated[UploadFile, File(...)],
    *,
    user: Annotated[User, Depends(require_org_admin)],
    rc: Annotated[RequestContext, Depends(get_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    org = await OrganizationRepository(session).get(rc.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")
    zip_bytes = await file.read()
    publisher = SkillPublishService(session=session, cache=_cache())
    try:
        sv = await publisher.publish_from_zip(
            org_id=rc.org_id,
            org_slug=org.slug,
            actor_user_id=user.id,
            zip_bytes=zip_bytes,
        )
    except InvalidFrontmatterError as e:
        raise HTTPException(status_code=400, detail={"code": "INVALID_FRONTMATTER", "field": e.field, "reason": e.reason})
    except InvalidSkillNameError as e:
        raise HTTPException(status_code=400, detail={"code": "INVALID_SKILL_NAME", "reason": str(e)})
    except SkillMdMissingError as e:
        raise HTTPException(status_code=400, detail={"code": "SKILL_MD_MISSING", "reason": str(e)})
    except FileTooLargeError as e:
        raise HTTPException(status_code=400, detail={"code": "FILE_TOO_LARGE", "reason": str(e)})
    except VersionCollisionError as e:
        raise HTTPException(status_code=409, detail={"code": "VERSION_EXISTS", "reason": str(e)})
    return {"skill_version_id": sv.id, "skill_id": sv.skill_id, "version": sv.version}


# --- Workspace bindings (admin-managed) ----------------------------------


bindings_router = APIRouter(
    prefix="/admin/workspaces/{ws_id}/skills", tags=["admin-skill-bindings"]
)


@bindings_router.get("", response_model=list[SkillSummary])
async def list_workspace_skills(
    ws_id: str,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    rc: Annotated[RequestContext, Depends(get_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[SkillSummary]:
    bindings = WorkspaceSkillBindingRepository(
        session, org_id=rc.org_id, workspace_id=ws_id
    )
    enabled = await bindings.list_enabled()
    if not enabled:
        return []
    install_repo = OrgSkillInstallRepository(session)
    skill_repo = SkillRepository(session)
    out: list[SkillSummary] = []
    for b in enabled:
        install = await session.get(type(enabled[0]).__mro__[0], b.org_skill_install_id)  # placeholder
        # Simpler: load install row
        from cubeplex.models import OrgSkillInstall

        install_obj = await session.get(OrgSkillInstall, b.org_skill_install_id)
        if install_obj is None:
            continue
        skill = await skill_repo.get(install_obj.skill_id)
        if skill is None:
            continue
        out.append(
            SkillSummary(
                id=skill.id,
                name=skill.name,
                source=skill.source,  # type: ignore[arg-type]
                description=skill.description,
                current_version=skill.current_version,
                keywords=skill.keywords,
                install_state="installed",
                installed_version=install_obj.installed_version,
                workspace_bindings_count=1,
            )
        )
    return out


@bindings_router.post("", status_code=200)
async def enable_skills_in_workspace(
    ws_id: str,
    body: WorkspaceBindingsRequest,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    rc: Annotated[RequestContext, Depends(get_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    install_repo = OrgSkillInstallRepository(session)
    bindings = WorkspaceSkillBindingRepository(
        session, org_id=rc.org_id, workspace_id=ws_id
    )
    enabled_count = 0
    for skill_id in body.skill_ids:
        install = await install_repo.get(rc.org_id, skill_id)
        if install is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "SKILL_NOT_INSTALLED", "skill_id": skill_id},
            )
        await bindings.enable(install.id)
        enabled_count += 1
    return {"enabled": enabled_count}


@bindings_router.delete("/{skill_id}", status_code=204)
async def disable_skill_in_workspace(
    ws_id: str,
    skill_id: str,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    rc: Annotated[RequestContext, Depends(get_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    install = await OrgSkillInstallRepository(session).get(rc.org_id, skill_id)
    if install is None:
        return
    bindings = WorkspaceSkillBindingRepository(
        session, org_id=rc.org_id, workspace_id=ws_id
    )
    await bindings.disable(install.id)


def _visible(skill, org_id: str) -> bool:
    return skill.source == "preinstalled" or skill.owner_org_id == org_id
```

NOTE: `RequestContext` and `require_org_admin` dependency injection lookup paths may need to match the actual M2 admin dependencies — adjust imports to point to whatever `auth/dependencies.py` exposes (`require_org_admin` was added in M2). The placeholder `# Simpler: load install row` was sketched twice — clean that block to the simpler form before commit.

- [ ] **Step 2: Type-check + lint**

```bash
uv run mypy cubeplex/api/routes/v1/admin_skills.py
uv run ruff check cubeplex/api/routes/v1/admin_skills.py
```

Fix any errors; the duplicated `Simpler: load install row` block is a known cleanup.

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/api/routes/v1/admin_skills.py
git commit -m "feat(m3): admin skill marketplace HTTP routes"
```

---

## Task 10: Member HTTP routes

**Files:**
- Create: `backend/cubeplex/api/routes/v1/ws_skills.py`

- [ ] **Step 1: Implement member routes**

Create `backend/cubeplex/api/routes/v1/ws_skills.py`:

```python
"""Member-callable skill endpoints under /api/v1/ws/{wsId}/skills.

See spec § 5.1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.skill import (
    SkillContentResponse,
    SkillFiles,
    SkillSummary,
)
from cubeplex.auth.dependencies import RequestContext, get_request_context
from cubeplex.config import config as _config
from cubeplex.db.session import get_session
from cubeplex.models import OrgSkillInstall
from cubeplex.repositories.organization import OrganizationRepository
from cubeplex.repositories.skill import (
    OrgSkillInstallRepository,
    SkillRepository,
    SkillVersionRepository,
    WorkspaceSkillBindingRepository,
)
from cubeplex.skills.cache import SkillCache
from cubeplex.skills.frontmatter import InvalidFrontmatterError
from cubeplex.skills.service import (
    FileTooLargeError,
    InvalidSkillNameError,
    SkillCatalogService,
    SkillMdMissingError,
    SkillPublishService,
    VersionCollisionError,
)

router = APIRouter(prefix="/ws/{ws_id}/skills", tags=["ws-skills"])


def _cache() -> SkillCache:
    return SkillCache(cache_root=Path(_config.get("skills.cache_root", "skills_cache")))


@router.get("", response_model=list[SkillSummary])
async def list_skills_in_ws(
    ws_id: str,
    *,
    rc: Annotated[RequestContext, Depends(get_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
    scope: Literal["workspace", "org", "catalog"] = Query("workspace"),
    source: str | None = Query(None),
    q: str | None = Query(None),
    tag: str | None = Query(None),
) -> list[SkillSummary]:
    if scope == "workspace":
        catalog = SkillCatalogService(session=session, cache=_cache())
        resolved = await catalog.list_enabled_for_workspace(ws_id, org_id=rc.org_id)
        skill_ids = [r.skill_id for r in resolved]
        skills = [
            await SkillRepository(session).get(sid) for sid in skill_ids
        ]
        return [
            SkillSummary(
                id=s.id,
                name=s.name,
                source=s.source,  # type: ignore[arg-type]
                description=s.description,
                current_version=s.current_version,
                keywords=s.keywords,
                install_state="installed",
                installed_version=None,
                workspace_bindings_count=1,
            )
            for s in skills
            if s is not None
        ]
    elif scope == "org":
        skills = await SkillRepository(session).list_visible_for_org(
            rc.org_id, source=source
        )
        installs = await OrgSkillInstallRepository(session).list_for_org(rc.org_id)
        installed_ids = {i.skill_id for i in installs}
        return [
            SkillSummary(
                id=s.id,
                name=s.name,
                source=s.source,  # type: ignore[arg-type]
                description=s.description,
                current_version=s.current_version,
                keywords=s.keywords,
                install_state="installed" if s.id in installed_ids else "uninstalled",
                workspace_bindings_count=0,
            )
            for s in skills
            if (q is None or q.lower() in s.name.lower() or q.lower() in s.description.lower())
            and (tag is None or tag in s.keywords)
            and s.id in installed_ids
        ]
    else:  # catalog
        skills = await SkillRepository(session).list_visible_for_org(
            rc.org_id, source=source
        )
        return [
            SkillSummary(
                id=s.id,
                name=s.name,
                source=s.source,  # type: ignore[arg-type]
                description=s.description,
                current_version=s.current_version,
                keywords=s.keywords,
                install_state="uninstalled",  # member view doesn't expose install state
                workspace_bindings_count=0,
            )
            for s in skills
            if (q is None or q.lower() in s.name.lower() or q.lower() in s.description.lower())
            and (tag is None or tag in s.keywords)
        ]


@router.get("/{skill_id}", response_model=SkillContentResponse)
async def preview_skill(
    ws_id: str,
    skill_id: str,
    *,
    rc: Annotated[RequestContext, Depends(get_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
    version: str | None = Query(None),
) -> SkillContentResponse:
    skill = await SkillRepository(session).get(skill_id)
    if skill is None or not _visible(skill, rc.org_id):
        raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")

    target_version = version
    if target_version is None:
        install = await OrgSkillInstallRepository(session).get(rc.org_id, skill_id)
        target_version = install.installed_version if install else skill.current_version

    sv = await SkillVersionRepository(session).find(skill_id, target_version)
    if sv is None:
        raise HTTPException(status_code=404, detail="SKILL_VERSION_NOT_FOUND")

    catalog = SkillCatalogService(session=session, cache=_cache())
    content = await catalog.fetch_skill_md(sv.id)
    files_list = await catalog.list_files_for_sandbox_sync(
        sv.id, storage_prefix=sv.storage_prefix
    )
    return SkillContentResponse(
        skill_id=skill.id,
        skill_version_id=sv.id,
        name=skill.name,
        version=sv.version,
        content=content,
        files=[SkillFiles(rel_path=p, size=len(b)) for p, b in files_list],
    )


@router.get("/{skill_id}/files/{path:path}")
async def get_skill_file(
    ws_id: str,
    skill_id: str,
    path: str,
    *,
    rc: Annotated[RequestContext, Depends(get_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
    version: str | None = Query(None),
) -> bytes:
    skill = await SkillRepository(session).get(skill_id)
    if skill is None or not _visible(skill, rc.org_id):
        raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")
    target_version = version
    if target_version is None:
        install = await OrgSkillInstallRepository(session).get(rc.org_id, skill_id)
        target_version = install.installed_version if install else skill.current_version
    sv = await SkillVersionRepository(session).find(skill_id, target_version)
    if sv is None:
        raise HTTPException(status_code=404, detail="SKILL_VERSION_NOT_FOUND")

    cache_dir = await _cache().ensure_extracted(sv.id, storage_prefix=sv.storage_prefix)
    target = cache_dir / path
    if not target.is_file():
        raise HTTPException(status_code=404, detail="FILE_NOT_FOUND")
    return target.read_bytes()


@router.post("/publish", status_code=201)
async def publish_from_ws(
    ws_id: str,
    file: Annotated[UploadFile | None, File()] = None,
    *,
    rc: Annotated[RequestContext, Depends(get_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    """Member publish: multipart .zip OR JSON {artifact_id} (Batch 2)."""
    if file is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "MISSING_BODY", "reason": "expected multipart file or {artifact_id}"},
        )
    org = await OrganizationRepository(session).get(rc.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")
    zip_bytes = await file.read()
    publisher = SkillPublishService(session=session, cache=_cache())
    try:
        sv = await publisher.publish_from_zip(
            org_id=rc.org_id,
            org_slug=org.slug,
            actor_user_id=rc.user.id,
            zip_bytes=zip_bytes,
        )
    except InvalidFrontmatterError as e:
        raise HTTPException(status_code=400, detail={"code": "INVALID_FRONTMATTER", "field": e.field, "reason": e.reason})
    except InvalidSkillNameError as e:
        raise HTTPException(status_code=400, detail={"code": "INVALID_SKILL_NAME", "reason": str(e)})
    except SkillMdMissingError as e:
        raise HTTPException(status_code=400, detail={"code": "SKILL_MD_MISSING", "reason": str(e)})
    except FileTooLargeError as e:
        raise HTTPException(status_code=400, detail={"code": "FILE_TOO_LARGE", "reason": str(e)})
    except VersionCollisionError as e:
        raise HTTPException(status_code=409, detail={"code": "VERSION_EXISTS", "reason": str(e)})
    return {"skill_version_id": sv.id, "skill_id": sv.skill_id, "version": sv.version}


def _visible(skill, org_id: str) -> bool:
    return skill.source == "preinstalled" or skill.owner_org_id == org_id
```

NOTE: `RequestContext.user` shape may differ; if `rc.user` doesn't exist, get the user via the auth dependency directly (mirror the M2 admin-routes pattern).

- [ ] **Step 2: Type-check**

```bash
uv run mypy cubeplex/api/routes/v1/ws_skills.py
```

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/api/routes/v1/ws_skills.py
git commit -m "feat(m3): member skill marketplace HTTP routes"
```

---

## Task 11: SkillsMiddleware refactor (catalog-driven)

**Files:**
- Modify: `backend/cubeplex/middleware/skills.py`
- Test: existing E2E tests must continue to pass; new test added in Task 16

- [ ] **Step 1: Replace the file with catalog-driven version**

Open `backend/cubeplex/middleware/skills.py` and replace the entire file with:

```python
"""SkillsMiddleware — injects available skills into system prompt.

After M3, this is catalog-driven (queries the SkillCatalogService). Old
filesystem-based loader (load_builtin_skills + SkillSpec dataclass) is removed.
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool

from cubeplex.middleware._utils import append_to_system_message
from cubeplex.prompts.skills import SKILLS_PROMPT_TEMPLATE
from cubeplex.skills.service import ResolvedSkill, SkillCatalogService


class SkillsMiddleware(AgentMiddleware[Any, Any, Any]):
    """Injects workspace-enabled skills into the system prompt each model call."""

    tools: Sequence[BaseTool] = []

    def __init__(
        self,
        *,
        catalog: SkillCatalogService,
        workspace_id: str,
        org_id: str,
    ) -> None:
        self._catalog = catalog
        self._workspace_id = workspace_id
        self._org_id = org_id
        self._cached: list[ResolvedSkill] | None = None

    def _build_prompt(self, skills: list[ResolvedSkill]) -> str:
        if not skills:
            return ""
        skills_list = "\n".join(
            f"- **{s.name}** v{s.version}: {s.description}" for s in skills
        )
        return SKILLS_PROMPT_TEMPLATE.format(skills_list=skills_list)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        if self._cached is None:
            self._cached = await self._catalog.list_enabled_for_workspace(
                self._workspace_id, org_id=self._org_id
            )
        prompt = self._build_prompt(self._cached)
        if not prompt:
            return await handler(request)
        new_system = append_to_system_message(request.system_message, prompt)
        return await handler(request.override(system_message=new_system))
```

- [ ] **Step 2: Update prompt template if needed**

Open `backend/cubeplex/prompts/skills.py`. The template uses `{skills_list}` and may include hardcoded path references. Update it to drop any `/.skills/builtin/...` references; the prompt should say "Use `load_skill(name)` to read a skill's instructions." and not reference filesystem paths.

```python
SKILLS_PROMPT_TEMPLATE = """\

# Available skills

{skills_list}

Use `load_skill(name)` to read a skill's instructions. Skills' sibling files
(scripts, templates) are available at `/.skills/<name>/<version>/` inside the
sandbox when you actually use them.
"""
```

- [ ] **Step 3: Update `agents/graph.py`**

Open `backend/cubeplex/agents/graph.py`. Locate the existing `from cubeplex.middleware.skills import SkillsMiddleware, SkillSpec` line; remove `SkillSpec`. Find the `skills: list[SkillSpec] | None = None` parameter on `create_cubeplex_agent`; remove it. Find the `SkillsMiddleware(skills=_skills)` line and replace with:

```python
from cubeplex.skills.cache import SkillCache
from cubeplex.skills.service import SkillCatalogService

# Inside create_cubeplex_agent — at the point where SkillsMiddleware is constructed:
skill_cache = SkillCache(cache_root=Path(config.get("skills.cache_root", "skills_cache")))
skill_catalog = SkillCatalogService(session=session, cache=skill_cache)
middleware.append(
    SkillsMiddleware(catalog=skill_catalog, workspace_id=workspace_id, org_id=org_id)
)
```

`session`, `workspace_id`, and `org_id` should already be available in the factory's local scope (per existing patterns like ArtifactMiddleware that's also constructed there). If `session` isn't currently passed in, thread it through similarly to how it's done for artifact middleware.

- [ ] **Step 4: Run existing agent E2E tests**

```bash
uv run pytest tests/e2e/test_conversations.py tests/e2e/test_conversation_flow.py -v
```

Expected: all pass (or unchanged from baseline). The middleware no longer pulls from filesystem; with no installed skills, prompt block is empty.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/middleware/skills.py \
        backend/cubeplex/prompts/skills.py \
        backend/cubeplex/agents/graph.py
git commit -m "feat(m3): SkillsMiddleware catalog-driven (drops filesystem loader)"
```

---

## Task 12: `load_skill` tool refactor (catalog-driven, no sandbox)

**Files:**
- Modify: `backend/cubeplex/tools/builtin/load_skill.py`
- Test: backend E2E in Task 16 covers this

- [ ] **Step 1: Replace the file**

Open `backend/cubeplex/tools/builtin/load_skill.py` and replace with:

```python
"""load_skill — read a skill's SKILL.md content via the catalog service.

Backend-only: never touches the sandbox. See spec § 7.2.
"""

from __future__ import annotations

import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from cubeplex.skills.service import SkillCatalogService


class LoadSkillInput(BaseModel):
    skill_name: str = Field(
        description="Name of the skill to load. Use the canonical name from your"
        " 'Available skills' list (e.g. 'deep-research' or 'acme:my-skill')."
    )


class LoadSkillOutput(BaseModel):
    skill_name: str
    content: str
    version: str
    loaded: bool
    error: str | None = None

    def __str__(self) -> str:
        return self.model_dump_json()


def create_load_skill_tool(
    *,
    catalog: SkillCatalogService,
    workspace_id: str,
    org_id: str,
) -> StructuredTool:
    async def _load_skill(skill_name: str) -> str:
        resolved = await catalog.find_enabled_by_name(
            workspace_id, org_id=org_id, name=skill_name
        )
        if resolved is None:
            return LoadSkillOutput(
                skill_name=skill_name,
                content="",
                version="",
                loaded=False,
                error=f"Skill '{skill_name}' is not enabled in this workspace",
            ).model_dump_json()
        try:
            content = await catalog.fetch_skill_md(resolved.skill_version_id)
        except Exception as e:
            return LoadSkillOutput(
                skill_name=skill_name,
                content="",
                version=resolved.version,
                loaded=False,
                error=f"Failed to fetch skill content: {e}",
            ).model_dump_json()
        return LoadSkillOutput(
            skill_name=skill_name,
            content=content,
            version=resolved.version,
            loaded=True,
            error=None,
        ).model_dump_json()

    return StructuredTool.from_function(
        coroutine=_load_skill,
        name="load_skill",
        description=(
            "Read a skill's instructions. Returns SKILL.md content plus version. "
            "Skills are listed in your system prompt; pass the exact name."
        ),
        args_schema=LoadSkillInput,
    )
```

- [ ] **Step 2: Update tool registration in graph.py**

Wherever `load_skill` is registered today (likely in `cubeplex/tools/__init__.py` or `agents/graph.py`), replace the call to `create_load_skill_tool()` with the new signature, passing `catalog`, `workspace_id`, `org_id`.

```python
load_skill_tool = create_load_skill_tool(
    catalog=skill_catalog, workspace_id=workspace_id, org_id=org_id
)
tool_registry.register(load_skill_tool)
```

- [ ] **Step 3: Run existing agent tests**

```bash
uv run pytest tests/e2e/test_conversations.py -v
```

Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/tools/builtin/load_skill.py \
        backend/cubeplex/agents/graph.py \
        backend/cubeplex/tools/__init__.py
git commit -m "feat(m3): load_skill tool catalog-driven (backend-only, no sandbox wake)"
```

---

## Task 13: Sandbox.has_synced + LazySandbox sync hook

**Files:**
- Modify: `backend/cubeplex/sandbox/base.py`
- Modify: `backend/cubeplex/sandbox/lazy.py`
- Test: `backend/tests/e2e/test_lazy_sandbox_skill_sync.py`

- [ ] **Step 1: Write failing E2E test**

Create `backend/tests/e2e/test_lazy_sandbox_skill_sync.py`:

```python
"""E2E: LazySandbox transparently syncs workspace-enabled skills on first use."""

import pytest

from cubeplex.objectstore import get_objectstore_client
from cubeplex.repositories.skill import (
    OrgSkillInstallRepository,
    SkillRepository,
    SkillVersionRepository,
    WorkspaceSkillBindingRepository,
)
from cubeplex.sandbox.local import LocalSandbox
from cubeplex.sandbox.lazy import LazySandbox
from cubeplex.skills.cache import SkillCache
from cubeplex.skills.service import SkillCatalogService
from cubeplex.skills.storage_paths import global_skill_prefix


@pytest.mark.asyncio
async def test_lazy_sandbox_syncs_enabled_skills(tmp_path, db_session) -> None:
    # Arrange: install + enable a skill in workspace ws-1 (org-1)
    skills = SkillRepository(db_session)
    versions = SkillVersionRepository(db_session)
    skill = await skills.create_preinstalled(
        name="t", description="d", keywords=[], current_version="1.0.0"
    )
    prefix = global_skill_prefix("t", "1.0.0")
    store = get_objectstore_client()
    await store.put(f"{prefix}SKILL.md", b"---\nname: t\ndescription: d\nversion: 1.0.0\n---\n")
    await store.put(f"{prefix}scripts/run.sh", b"#!/bin/sh\necho ok\n")
    sv = await versions.create(
        skill_id=skill.id,
        version="1.0.0",
        description="d",
        keywords=[],
        raw_metadata={},
        storage_prefix=prefix,
        entry_file="SKILL.md",
        uploaded_by_user_id=None,
    )
    install = await OrgSkillInstallRepository(db_session).upsert(
        org_id="org-1",
        skill_id=skill.id,
        installed_version="1.0.0",
        installed_by_user_id="u",
    )
    bindings = WorkspaceSkillBindingRepository(
        db_session, org_id="org-1", workspace_id="ws-1"
    )
    await bindings.enable(install.id)

    # Build a LazySandbox with a SkillCatalogService injected
    catalog = SkillCatalogService(
        session=db_session, cache=SkillCache(cache_root=tmp_path / "cache")
    )

    # Mock manager.get_or_create to return a LocalSandbox we can inspect
    class _ManagerStub:
        async def get_or_create(self, *a, **kw):
            return LocalSandbox(workdir="/workspace")
        async def release(self, *a, **kw):
            pass

    sb = LazySandbox(
        manager=_ManagerStub(),
        user_id="u",
        org_id="org-1",
        workspace_id="ws-1",
        catalog=catalog,
    )

    # Act: trigger first execute
    result = await sb.execute("ls /.skills/t/1.0.0/")

    # Assert: SKILL.md + scripts/run.sh present in sandbox
    assert "SKILL.md" in result.stdout
    assert "scripts" in result.stdout


@pytest.mark.asyncio
async def test_lazy_sandbox_skips_already_synced_skills(tmp_path, db_session) -> None:
    # Same setup as above; second execute call should not re-upload.
    # Use a counter on a fake sandbox to assert.
    pass  # implementation-time; assert via log scan or a counter wrapper
```

- [ ] **Step 2: Run, verify fails**

```bash
uv run pytest tests/e2e/test_lazy_sandbox_skill_sync.py -v
```

Expected: FAIL — `LazySandbox.__init__() got unexpected keyword argument 'catalog'` or AttributeError on `sandbox.has_synced`.

- [ ] **Step 3: Add `has_synced` / `mark_synced` to `Sandbox` ABC**

Open `backend/cubeplex/sandbox/base.py` and add to the `Sandbox` class (after existing abstract methods):

```python
def has_synced(self, skill_version_id: str) -> bool:
    """Return True if this skill_version's files have already been pushed
    to /.skills/<name>/<version>/ in this sandbox lifetime. Default impl
    uses an in-memory set; subclasses may persist if they want cross-restart
    behavior."""
    if not hasattr(self, "_synced_skill_version_ids"):
        self._synced_skill_version_ids = set()  # type: ignore[attr-defined]
    return skill_version_id in self._synced_skill_version_ids  # type: ignore[attr-defined]

def mark_synced(self, skill_version_id: str) -> None:
    if not hasattr(self, "_synced_skill_version_ids"):
        self._synced_skill_version_ids = set()  # type: ignore[attr-defined]
    self._synced_skill_version_ids.add(skill_version_id)  # type: ignore[attr-defined]
```

- [ ] **Step 4: Wire sync into `LazySandbox._ensure()`**

Open `backend/cubeplex/sandbox/lazy.py`. Add `catalog: SkillCatalogService | None = None` (default None for tests / non-skill paths) to `__init__`. Then update `_ensure`:

```python
# Inside __init__:
def __init__(
    self,
    *,
    manager: SandboxManager,
    user_id: str,
    org_id: str,
    workspace_id: str,
    catalog: "SkillCatalogService | None" = None,
    workdir: str = "/workspace",
) -> None:
    ...
    self._catalog = catalog
    ...

# Inside _ensure (after sandbox is created):
async def _ensure(self) -> Sandbox:
    if self._sandbox is not None:
        return self._sandbox
    async with self._lock:
        if self._sandbox is not None:
            return self._sandbox
        sandbox = await self._manager.get_or_create(
            self._user_id, org_id=self._org_id, workspace_id=self._workspace_id
        )
        if self._catalog is not None:
            try:
                await _sync_skills(
                    catalog=self._catalog,
                    workspace_id=self._workspace_id,
                    org_id=self._org_id,
                    sandbox=sandbox,
                )
            except Exception:
                logger.exception(
                    "Skill sync failed for ws {}; sandbox usable without skills",
                    self._workspace_id,
                )
        self._sandbox = sandbox
        return sandbox
```

Add the helper near the top of `lazy.py`:

```python
async def _sync_skills(
    *,
    catalog: "SkillCatalogService",
    workspace_id: str,
    org_id: str,
    sandbox: Sandbox,
) -> None:
    skills = await catalog.list_enabled_for_workspace(workspace_id, org_id=org_id)
    files: list[tuple[str, bytes]] = []
    for s in skills:
        if sandbox.has_synced(s.skill_version_id):
            continue
        per_skill = await catalog.list_files_for_sandbox_sync(
            s.skill_version_id, storage_prefix=s.storage_prefix
        )
        target_root = f"/.skills/{s.name}/{s.version}/"
        for rel, data in per_skill:
            files.append((target_root + rel, data))
        sandbox.mark_synced(s.skill_version_id)
    if files:
        await sandbox.upload(files)
```

The TYPE_CHECKING import block should now include `SkillCatalogService`:

```python
if TYPE_CHECKING:
    from cubeplex.sandbox.manager import SandboxManager
    from cubeplex.skills.service import SkillCatalogService
```

- [ ] **Step 5: Update `agents/graph.py` to pass `catalog` to LazySandbox**

Wherever `LazySandbox(...)` is constructed, add `catalog=skill_catalog`.

- [ ] **Step 6: Run, verify pass**

```bash
uv run pytest tests/e2e/test_lazy_sandbox_skill_sync.py::test_lazy_sandbox_syncs_enabled_skills -v
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/sandbox/base.py \
        backend/cubeplex/sandbox/lazy.py \
        backend/cubeplex/agents/graph.py \
        backend/tests/e2e/test_lazy_sandbox_skill_sync.py
git commit -m "feat(m3): transparent skill sync in LazySandbox._ensure()"
```

---

## Task 14: Drop SkillLoader, rename builtin → preinstalled, fix SKILL.md paths

**Files:**
- Delete: `backend/cubeplex/sandbox/skills.py`
- Modify: `backend/cubeplex/sandbox/manager.py`
- Modify: `backend/config.yaml`, `backend/config.development.yaml`, `backend/config.production.yaml`, `backend/config.test.yaml`
- Rename: `backend/skills/builtin/` → `backend/skills/preinstalled/`
- Edit: `backend/skills/preinstalled/pdf-creator/SKILL.md`, `backend/skills/preinstalled/web-artifacts-builder/SKILL.md` (path references)

- [ ] **Step 1: Delete `sandbox/skills.py`**

```bash
git rm backend/cubeplex/sandbox/skills.py
```

- [ ] **Step 2: Remove SkillLoader from `sandbox/manager.py`**

Open `backend/cubeplex/sandbox/manager.py:255-265` (the area that calls `SkillLoader`). Delete the import `from cubeplex.sandbox.skills import SkillLoader`, the `skills_dir_str = config.get("sandbox.skills.builtin_dir", ...)` line, and any block that calls `SkillLoader(...).load_builtin()` and uploads the result. The sandbox should now be created without any skill files; sync happens via LazySandbox.

- [ ] **Step 3: Rename builtin → preinstalled**

```bash
git mv backend/skills/builtin backend/skills/preinstalled
```

- [ ] **Step 4: Edit SKILL.md path references**

```bash
grep -rn "/.skills/builtin/" backend/skills/preinstalled/
```

For every match (likely in `pdf-creator/SKILL.md` and `web-artifacts-builder/SKILL.md`), edit the path to `/.skills/<name>/<version>/...`. Use the actual version from each skill's frontmatter.

E.g., if `pdf-creator/SKILL.md` says `bash /.skills/builtin/pdf-creator/scripts/foo.sh`, change to `bash /.skills/pdf-creator/<version>/scripts/foo.sh`.

Also ensure each preinstalled skill has `name`, `description`, `version` (and ideally `keywords`) in frontmatter. Bump frontmatter to compliance:

```bash
for f in backend/skills/preinstalled/*/SKILL.md; do
  echo "=== $f ==="
  head -10 "$f"
done
```

Manually edit any with missing required fields.

- [ ] **Step 5: Drop config keys**

In `backend/config.yaml` find `sandbox.skills.builtin_dir` and the path. Remove the `builtin_dir` line. Keep the `container_path` line (it's still used as the parent of `/.skills/`).

Add a new top-level config:
```yaml
skills:
  cache_root: "skills_cache"
  preinstalled_dir: "skills/preinstalled"  # relative to backend/
```

Repeat in `config.development.yaml` if it overrides. Confirm no test config references `builtin_dir` either.

- [ ] **Step 6: Run all backend tests to confirm no regressions**

```bash
uv run pytest tests/e2e/ -v -x
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/sandbox/manager.py \
        backend/config*.yaml \
        backend/skills/preinstalled/
git rm backend/cubeplex/sandbox/skills.py 2>/dev/null || true
git commit -m "refactor(m3): drop SkillLoader, rename builtin/ → preinstalled/, update paths"
```

---

## Task 15: FastAPI lifespan wiring + router mounts

**Files:**
- Modify: `backend/cubeplex/api/app.py`

- [ ] **Step 1: Wire seeder into lifespan**

Open `backend/cubeplex/api/app.py`. Find the existing `lifespan` async context manager (likely with redis connect, MCP startup, etc). Add the seeder call:

```python
from pathlib import Path
from cubeplex.skills.seeder import seed_preinstalled_skills

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # ... existing startup hooks ...
    redis = Redis.from_url(config.get("redis.url"))

    # M3: seed preinstalled skills from disk → global catalog
    preinstalled_dir = Path(__file__).resolve().parent.parent.parent / config.get(
        "skills.preinstalled_dir", "skills/preinstalled"
    )
    async with async_session_maker() as session:
        await seed_preinstalled_skills(
            preinstalled_dir=preinstalled_dir,
            db_session=session,
            redis=redis,
        )

    yield

    # ... existing shutdown ...
```

- [ ] **Step 2: Mount admin + ws routers**

In the same `app.py`, find where existing routers are included. Add:

```python
from cubeplex.api.routes.v1 import admin_skills, ws_skills

app.include_router(admin_skills.router, prefix="/api/v1")
app.include_router(admin_skills.bindings_router, prefix="/api/v1")
app.include_router(ws_skills.router, prefix="/api/v1")
```

- [ ] **Step 3: Smoke-test app boots**

```bash
uv run python -c "from cubeplex.api.app import create_app; app = create_app(); print([r.path for r in app.routes if 'skill' in r.path.lower()])"
```

Expected: prints the new skill routes.

- [ ] **Step 4: Run all E2E tests**

```bash
uv run pytest tests/e2e/ -v -x
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/api/app.py
git commit -m "feat(m3): wire seeder lifespan + mount admin/ws skill routers"
```

---

## Task 16: Comprehensive backend E2E suite

**Files:**
- Create: `backend/tests/e2e/test_skills_marketplace.py` (the spec § 9.1 table fully expressed)

This task consolidates the spec § 9.1 test table into one E2E file using the FastAPI TestClient + admin/member auth fixtures. Each test from the spec table maps to one Python test below.

- [ ] **Step 1: Write the full E2E suite**

Create `backend/tests/e2e/test_skills_marketplace.py`:

```python
"""Full E2E coverage for the skills marketplace — see spec § 9.1."""

import io
import zipfile

import httpx
import pytest


def _zip_skill(name: str, version: str, extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "SKILL.md",
            f"---\nname: {name}\ndescription: d\nversion: {version}\n---\n# {name}\n",
        )
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return buf.getvalue()


# --- seeding (covered by Task 5 unit tests; smoke at app boot) ----------


@pytest.mark.asyncio
async def test_admin_can_list_preinstalled_skills(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = admin_client
    resp = await client.get("/api/v1/admin/skills?source=preinstalled")
    assert resp.status_code == 200
    rows = resp.json()
    # Seeder runs on app startup; should have at least the preinstalled set
    assert any(r["name"] == "deep-research" for r in rows)


@pytest.mark.asyncio
async def test_admin_install_preinstalled_creates_org_install(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = admin_client
    list_resp = await client.get("/api/v1/admin/skills?source=preinstalled")
    skill = next(r for r in list_resp.json() if r["name"] == "deep-research")

    resp = await client.post(
        f"/api/v1/admin/skills/{skill['id']}/install",
        json={"version": skill["current_version"]},
    )
    assert resp.status_code == 200

    detail = await client.get(f"/api/v1/admin/skills/{skill['id']}")
    assert detail.json()["install_state"] == "installed"


@pytest.mark.asyncio
async def test_admin_uninstall_preinstalled_creates_tombstone(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = admin_client
    list_resp = await client.get("/api/v1/admin/skills?source=preinstalled")
    skill = next(r for r in list_resp.json() if r["name"] == "git-commit")
    await client.post(
        f"/api/v1/admin/skills/{skill['id']}/install",
        json={"version": skill["current_version"]},
    )

    resp = await client.delete(f"/api/v1/admin/skills/{skill['id']}/install")
    assert resp.status_code == 204

    # Tombstone path: subsequent re-list shows uninstalled even though seeder ran on app boot
    list2 = await client.get("/api/v1/admin/skills")
    git = next(r for r in list2.json() if r["name"] == "git-commit")
    assert git["install_state"] == "uninstalled"


@pytest.mark.asyncio
async def test_admin_upgrade_changes_pin(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = admin_client
    # Use an uploaded skill to control the version flow exactly
    z1 = _zip_skill("upgrade-target", "1.0.0")
    up1 = await client.post(
        "/api/v1/admin/skills/upload",
        files={"file": ("a.zip", z1, "application/zip")},
    )
    skill_id = up1.json()["skill_id"]

    z2 = _zip_skill("upgrade-target", "2.0.0")
    await client.post(
        "/api/v1/admin/skills/upload",
        files={"file": ("a.zip", z2, "application/zip")},
    )

    resp = await client.post(
        f"/api/v1/admin/skills/{skill_id}/install",
        json={"version": "2.0.0"},
    )
    assert resp.status_code == 200

    detail = await client.get(f"/api/v1/admin/skills/{skill_id}")
    assert detail.json()["installed_version"] == "2.0.0"


@pytest.mark.asyncio
async def test_member_publish_via_zip_creates_uploaded_skill(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = member_client
    z = _zip_skill("my-skill", "0.1.0", {"scripts/run.sh": b"#!/bin/sh\n"})
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/skills/publish",
        files={"file": ("a.zip", z, "application/zip")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["version"] == "0.1.0"

    # Visible in catalog scope
    list_resp = await client.get(f"/api/v1/ws/{ws_id}/skills?scope=catalog")
    found = [r for r in list_resp.json() if r["name"].endswith(":my-skill")]
    assert len(found) == 1


@pytest.mark.asyncio
async def test_member_publish_version_collision_returns_409(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = member_client
    z = _zip_skill("dup", "1.0.0")
    await client.post(
        f"/api/v1/ws/{ws_id}/skills/publish",
        files={"file": ("a.zip", z, "application/zip")},
    )
    r2 = await client.post(
        f"/api/v1/ws/{ws_id}/skills/publish",
        files={"file": ("a.zip", z, "application/zip")},
    )
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "VERSION_EXISTS"


@pytest.mark.asyncio
async def test_publish_invalid_frontmatter_returns_400(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = member_client
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SKILL.md", b"# no frontmatter\n")

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/skills/publish",
        files={"file": ("a.zip", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "INVALID_FRONTMATTER"


@pytest.mark.asyncio
async def test_workspace_toggle_changes_skill_prompt(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = admin_client
    list_resp = await client.get("/api/v1/admin/skills?source=preinstalled")
    skill = next(r for r in list_resp.json() if r["name"] == "deep-research")

    # Install at org level (no-op if previous test already did)
    await client.post(
        f"/api/v1/admin/skills/{skill['id']}/install",
        json={"version": skill["current_version"]},
    )

    # Enable in workspace
    enable = await client.post(
        f"/api/v1/admin/workspaces/{ws_id}/skills",
        json={"skill_ids": [skill["id"]]},
    )
    assert enable.status_code == 200

    # ws-skills list (scope=workspace) returns this skill
    ws_list = await client.get(f"/api/v1/ws/{ws_id}/skills?scope=workspace")
    names = [r["name"] for r in ws_list.json()]
    assert "deep-research" in names

    # Disable
    await client.delete(f"/api/v1/admin/workspaces/{ws_id}/skills/{skill['id']}")
    ws_list2 = await client.get(f"/api/v1/ws/{ws_id}/skills?scope=workspace")
    names2 = [r["name"] for r in ws_list2.json()]
    assert "deep-research" not in names2


@pytest.mark.asyncio
async def test_visibility_blocks_cross_org_uploads(
    member_client_org_a: tuple[httpx.AsyncClient, str],
    member_client_org_b: tuple[httpx.AsyncClient, str],
) -> None:
    # Org A publishes
    client_a, ws_a = member_client_org_a
    z = _zip_skill("private-thing", "1.0.0")
    await client_a.post(
        f"/api/v1/ws/{ws_a}/skills/publish",
        files={"file": ("a.zip", z, "application/zip")},
    )

    # Org B can't see it
    client_b, ws_b = member_client_org_b
    catalog_b = await client_b.get(f"/api/v1/ws/{ws_b}/skills?scope=catalog")
    names_b = [r["name"] for r in catalog_b.json()]
    assert not any(n.endswith(":private-thing") for n in names_b)
```

This test file requires `admin_client`, `member_client`, `member_client_org_a`, `member_client_org_b` fixtures in `tests/e2e/conftest.py`. The first two likely exist (used in other admin tests); the cross-org pair is new. Add fixtures to `conftest.py`:

```python
@pytest_asyncio.fixture
async def member_client_org_a(...) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """Fresh org A with a member user."""
    # Create a new org+ws+admin via register endpoint; yield (client, ws_id).
    ...

@pytest_asyncio.fixture
async def member_client_org_b(...) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """Fresh org B with a member user — different from org A."""
    ...
```

Mirror the existing fixture pattern (likely uses register → login → return scoped client).

- [ ] **Step 2: Run the suite**

```bash
uv run pytest tests/e2e/test_skills_marketplace.py -v
```

Expected: 9 passed (after the cross-org fixtures are wired).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_skills_marketplace.py \
        backend/tests/e2e/conftest.py
git commit -m "test(m3): comprehensive backend E2E suite for skills marketplace"
```

---

## Task 17: Migration helper script

**Files:**
- Create: `backend/scripts/dev/auto_install_preinstalled_for_existing_orgs.py`

- [ ] **Step 1: Write the script**

Create `backend/scripts/dev/auto_install_preinstalled_for_existing_orgs.py`:

```python
"""One-shot: for every existing Organization × every preinstalled skill,
create OrgSkillInstall + WorkspaceSkillBinding rows so users don't see a
behavior regression after M3 ships.

Usage:
    cd backend
    uv run python scripts/dev/auto_install_preinstalled_for_existing_orgs.py
"""

import asyncio

from sqlalchemy import select

from cubeplex.db.engine import async_session_maker
from cubeplex.models import (
    Organization,
    OrgSkillInstall,
    Skill,
    Workspace,
    WorkspaceSkillBinding,
)


async def main() -> None:
    async with async_session_maker() as session:
        orgs = (await session.execute(select(Organization))).scalars().all()
        skills = (
            await session.execute(select(Skill).where(Skill.source == "preinstalled"))
        ).scalars().all()

        for org in orgs:
            workspaces = (
                await session.execute(
                    select(Workspace).where(Workspace.org_id == org.id)
                )
            ).scalars().all()

            for skill in skills:
                # Skip if already installed
                existing = (
                    await session.execute(
                        select(OrgSkillInstall).where(
                            OrgSkillInstall.org_id == org.id,
                            OrgSkillInstall.skill_id == skill.id,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    continue

                install = OrgSkillInstall(
                    org_id=org.id,
                    skill_id=skill.id,
                    installed_version=skill.current_version,
                    installed_by_user_id="migration-script",
                )
                session.add(install)
                await session.flush()
                for ws in workspaces:
                    session.add(
                        WorkspaceSkillBinding(
                            org_id=org.id,
                            workspace_id=ws.id,
                            org_skill_install_id=install.id,
                            enabled=True,
                        )
                    )
        await session.commit()
        print(f"Auto-installed preinstalled skills for {len(orgs)} orgs.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Smoke-run it**

```bash
uv run python scripts/dev/auto_install_preinstalled_for_existing_orgs.py
```

Expected: prints count.

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/dev/auto_install_preinstalled_for_existing_orgs.py
git commit -m "feat(m3): one-shot migration script for existing orgs"
```

---

# Frontend (Batch 1)

For frontend tasks, follow existing patterns from `frontend/packages/web/components/admin/` (M2 admin shell), `hooks/` (M2 useAdminAccess / useAdminExtensions), and the shared API client. The spec § 6 mockup is the visual target.

## Task 18: Frontend types + hooks

**Files:**
- Create: `frontend/packages/core/src/types/skills.ts`
- Create: `frontend/packages/web/hooks/useAdminSkills.ts`
- Create: `frontend/packages/web/hooks/useAdminSkill.ts`
- Create: `frontend/packages/web/hooks/useWorkspaceSkills.ts`

- [ ] **Step 1: Define shared types**

Create `frontend/packages/core/src/types/skills.ts`:

```typescript
export type SkillSource = "preinstalled" | "uploaded";
export type InstallState = "uninstalled" | "installed" | "update_available";

export interface SkillSummary {
  id: string;
  name: string;
  source: SkillSource;
  description: string;
  current_version: string;
  keywords: string[];
  install_state: InstallState;
  installed_version: string | null;
  workspace_bindings_count: number;
}

export interface SkillVersionDetail {
  id: string;
  version: string;
  description: string;
  keywords: string[];
  storage_prefix: string;
  entry_file: string;
  uploaded_by_user_id: string | null;
  created_at: string;
}

export interface SkillDetail {
  id: string;
  name: string;
  source: SkillSource;
  description: string;
  current_version: string;
  keywords: string[];
  versions: SkillVersionDetail[];
  install_state: InstallState;
  installed_version: string | null;
}

export interface SkillContent {
  skill_id: string;
  skill_version_id: string;
  name: string;
  version: string;
  content: string;
  files: { rel_path: string; size: number; mime: string | null }[];
}

export interface SkillFilters {
  source?: SkillSource;
  installed?: boolean;
  q?: string;
  tag?: string;
}
```

- [ ] **Step 2: Re-export from `packages/core/src/types/index.ts`** (or wherever the package exports types).

```typescript
export * from "./skills";
```

- [ ] **Step 3: Implement hooks**

Create `frontend/packages/web/hooks/useAdminSkills.ts`:

```typescript
import useSWR from "swr";
import type { SkillFilters, SkillSummary } from "@cubeplex/core/types";
import { fetcher } from "@/lib/fetcher";

export function useAdminSkills(filters: SkillFilters = {}) {
  const params = new URLSearchParams();
  if (filters.source) params.set("source", filters.source);
  if (filters.installed !== undefined) params.set("installed", String(filters.installed));
  if (filters.q) params.set("q", filters.q);
  if (filters.tag) params.set("tag", filters.tag);
  const qs = params.toString() ? `?${params}` : "";
  const { data, error, isLoading, mutate } = useSWR<SkillSummary[]>(
    `/api/v1/admin/skills${qs}`,
    fetcher,
  );
  return { skills: data ?? [], error, isLoading, mutate };
}
```

Create `frontend/packages/web/hooks/useAdminSkill.ts`:

```typescript
import useSWR from "swr";
import type { SkillDetail } from "@cubeplex/core/types";
import { fetcher } from "@/lib/fetcher";

export function useAdminSkill(skillId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<SkillDetail>(
    skillId ? `/api/v1/admin/skills/${skillId}` : null,
    fetcher,
  );
  return { skill: data, error, isLoading, mutate };
}
```

Create `frontend/packages/web/hooks/useWorkspaceSkills.ts`:

```typescript
import useSWR from "swr";
import type { SkillSummary } from "@cubeplex/core/types";
import { fetcher } from "@/lib/fetcher";

export function useWorkspaceSkills(wsId: string) {
  const { data, error, isLoading, mutate } = useSWR<SkillSummary[]>(
    `/api/v1/admin/workspaces/${wsId}/skills`,
    fetcher,
  );
  return { skills: data ?? [], error, isLoading, mutate };
}
```

(If a `fetcher` helper doesn't exist, use the API client pattern from `useAdminSkills`-style hooks already in the repo — likely `apiClient.get(url)` returning JSON.)

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/core/src/types/skills.ts \
        frontend/packages/web/hooks/useAdminSkills.ts \
        frontend/packages/web/hooks/useAdminSkill.ts \
        frontend/packages/web/hooks/useWorkspaceSkills.ts \
        frontend/packages/core/src/types/index.ts
git commit -m "feat(m3): frontend skills types + SWR hooks"
```

---

## Task 19: Admin Skills tab — list view

**Files:**
- Modify: `frontend/packages/web/app/admin/skills/page.tsx`
- Create: `frontend/packages/web/components/admin/skills/SkillsToolbar.tsx`
- Create: `frontend/packages/web/components/admin/skills/SkillsList.tsx`
- Create: `frontend/packages/web/components/admin/skills/SkillCard.tsx`

- [ ] **Step 1: Replace ComingSoonCard with skills page shell**

Open `frontend/packages/web/app/admin/skills/page.tsx`. Replace contents:

```tsx
"use client";

import { useState } from "react";
import { useAdminSkills } from "@/hooks/useAdminSkills";
import { SkillsToolbar } from "@/components/admin/skills/SkillsToolbar";
import { SkillsList } from "@/components/admin/skills/SkillsList";
import { SkillDetailPanel } from "@/components/admin/skills/SkillDetailPanel";
import type { SkillFilters } from "@cubeplex/core/types";

export default function SkillsPage() {
  const [filters, setFilters] = useState<SkillFilters>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { skills, isLoading, mutate } = useAdminSkills(filters);

  return (
    <div className="flex h-full flex-col">
      <SkillsToolbar filters={filters} onFiltersChange={setFilters} onUploaded={mutate} />
      <div className="flex flex-1 overflow-hidden">
        <SkillsList
          skills={skills}
          isLoading={isLoading}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
        <SkillDetailPanel
          skillId={selectedId}
          onActionDone={() => mutate()}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Implement `SkillsToolbar`**

Create `frontend/packages/web/components/admin/skills/SkillsToolbar.tsx`:

```tsx
"use client";

import { Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useState } from "react";
import { UploadSkillModal } from "./UploadSkillModal";
import type { SkillFilters } from "@cubeplex/core/types";

export function SkillsToolbar({
  filters,
  onFiltersChange,
  onUploaded,
}: {
  filters: SkillFilters;
  onFiltersChange: (f: SkillFilters) => void;
  onUploaded: () => void;
}) {
  const [uploadOpen, setUploadOpen] = useState(false);
  return (
    <div className="flex items-center gap-2 border-b px-4 py-2">
      <Input
        placeholder="搜索..."
        value={filters.q ?? ""}
        onChange={(e) => onFiltersChange({ ...filters, q: e.target.value || undefined })}
        className="max-w-xs"
      />
      <Select
        value={filters.source ?? "all"}
        onValueChange={(v) =>
          onFiltersChange({
            ...filters,
            source: v === "all" ? undefined : (v as "preinstalled" | "uploaded"),
          })
        }
      >
        <SelectTrigger className="w-32">
          <SelectValue placeholder="来源" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部</SelectItem>
          <SelectItem value="preinstalled">预装</SelectItem>
          <SelectItem value="uploaded">自上传</SelectItem>
        </SelectContent>
      </Select>
      <Select
        value={filters.installed === undefined ? "all" : filters.installed ? "yes" : "no"}
        onValueChange={(v) =>
          onFiltersChange({
            ...filters,
            installed: v === "all" ? undefined : v === "yes",
          })
        }
      >
        <SelectTrigger className="w-32">
          <SelectValue placeholder="状态" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部</SelectItem>
          <SelectItem value="yes">已安装</SelectItem>
          <SelectItem value="no">未安装</SelectItem>
        </SelectContent>
      </Select>
      <div className="ml-auto">
        <Button variant="outline" size="sm" onClick={() => setUploadOpen(true)}>
          <Upload className="mr-1 h-4 w-4" />
          上传 skill
        </Button>
      </div>
      <UploadSkillModal
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onUploaded={() => {
          setUploadOpen(false);
          onUploaded();
        }}
      />
    </div>
  );
}
```

- [ ] **Step 3: Implement `SkillsList` and `SkillCard`**

Create `frontend/packages/web/components/admin/skills/SkillCard.tsx`:

```tsx
import { cn } from "@/lib/utils";
import type { SkillSummary } from "@cubeplex/core/types";

export function SkillCard({
  skill,
  selected,
  onClick,
}: {
  skill: SkillSummary;
  selected: boolean;
  onClick: () => void;
}) {
  const sourceColor = skill.source === "preinstalled" ? "bg-blue-500" : "bg-emerald-500";
  const stateLabel = {
    installed: `已安装 v${skill.installed_version}`,
    update_available: `有更新 v${skill.current_version}`,
    uninstalled: "未安装",
  }[skill.install_state];

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full flex-col gap-1 border-b px-4 py-3 text-left hover:bg-muted/40",
        selected && "bg-muted",
      )}
    >
      <div className="flex items-center gap-2">
        <span className={cn("h-2 w-2 rounded-full", sourceColor)} />
        <span className="font-medium">{skill.name}</span>
      </div>
      <span className="text-xs text-muted-foreground">{stateLabel}</span>
      {skill.workspace_bindings_count > 0 && (
        <span className="text-xs text-muted-foreground">
          启用于 {skill.workspace_bindings_count} 个 workspace
        </span>
      )}
    </button>
  );
}
```

Create `frontend/packages/web/components/admin/skills/SkillsList.tsx`:

```tsx
import type { SkillSummary } from "@cubeplex/core/types";
import { SkillCard } from "./SkillCard";

export function SkillsList({
  skills,
  isLoading,
  selectedId,
  onSelect,
}: {
  skills: SkillSummary[];
  isLoading: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (isLoading) {
    return <div className="p-4 text-sm text-muted-foreground">加载中…</div>;
  }
  if (skills.length === 0) {
    return <div className="p-4 text-sm text-muted-foreground">暂无 skill</div>;
  }
  return (
    <div className="w-[360px] flex-shrink-0 overflow-y-auto border-r">
      {skills.map((s) => (
        <SkillCard
          key={s.id}
          skill={s}
          selected={s.id === selectedId}
          onClick={() => onSelect(s.id)}
        />
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Type-check + lint**

```bash
pnpm --filter web type-check
pnpm --filter web lint
```

Fix any errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/app/admin/skills/page.tsx \
        frontend/packages/web/components/admin/skills/
git commit -m "feat(m3): admin Skills tab list view"
```

(`SkillDetailPanel` and `UploadSkillModal` are referenced by the page but implemented in the next tasks; this commit will not type-check until Task 20+21 land. Either bundle these tasks together or temporarily stub the missing imports with empty component shells before committing.)

For pragmatism, before the commit, create stub components so the build passes:

```tsx
// SkillDetailPanel.tsx (stub for now; replaced in Task 20)
export function SkillDetailPanel(_: { skillId: string | null; onActionDone: () => void }) {
  return <div className="flex-1 p-4 text-muted-foreground">选择一个 skill 查看详情</div>;
}

// UploadSkillModal.tsx (stub for now; replaced in Task 21)
export function UploadSkillModal(_: { open: boolean; onOpenChange: (v: boolean) => void; onUploaded: () => void }) {
  return null;
}
```

---

## Task 20: SkillDetailPanel + actions + bindings

**Files:**
- Replace: `frontend/packages/web/components/admin/skills/SkillDetailPanel.tsx`
- Create: `frontend/packages/web/components/admin/skills/OrgInstallActions.tsx`
- Create: `frontend/packages/web/components/admin/skills/WorkspaceBindingsTable.tsx`

- [ ] **Step 1: Implement `SkillDetailPanel`**

Replace `frontend/packages/web/components/admin/skills/SkillDetailPanel.tsx`:

```tsx
"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR from "swr";
import { useAdminSkill } from "@/hooks/useAdminSkill";
import { fetcher } from "@/lib/fetcher";
import { proseClasses } from "@/lib/utils";
import { OrgInstallActions } from "./OrgInstallActions";
import { WorkspaceBindingsTable } from "./WorkspaceBindingsTable";
import type { SkillContent } from "@cubeplex/core/types";

export function SkillDetailPanel({
  skillId,
  onActionDone,
}: {
  skillId: string | null;
  onActionDone: () => void;
}) {
  const { skill } = useAdminSkill(skillId);
  const [selectedVersion, setSelectedVersion] = useState<string | null>(null);

  const versionToFetch = selectedVersion ?? skill?.current_version ?? null;
  const { data: content } = useSWR<SkillContent>(
    skill && versionToFetch
      ? `/api/v1/admin/skills/${skill.id}/versions/${versionToFetch}`
      : null,
    fetcher,
  );

  if (!skill) {
    return <div className="flex-1 p-4 text-muted-foreground">选择一个 skill 查看详情</div>;
  }

  return (
    <div className="flex flex-1 flex-col overflow-y-auto">
      <header className="border-b p-4">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold">{skill.name}</h2>
          <select
            value={versionToFetch ?? ""}
            onChange={(e) => setSelectedVersion(e.target.value)}
            className="rounded border px-2 py-1 text-sm"
          >
            {skill.versions.map((v) => (
              <option key={v.id} value={v.version}>
                v{v.version}
              </option>
            ))}
          </select>
        </div>
        <p className="text-sm text-muted-foreground">{skill.source}</p>
      </header>

      <div className={`${proseClasses} flex-1 p-4`}>
        {content ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content.content}</ReactMarkdown>
        ) : (
          <p className="text-muted-foreground">加载中…</p>
        )}
        {content && content.files.length > 0 && (
          <div className="mt-4">
            <p className="text-sm font-medium">文件:</p>
            <ul className="text-sm text-muted-foreground">
              {content.files.map((f) => (
                <li key={f.rel_path}>
                  {f.rel_path}{" "}
                  <span className="text-xs">({f.size} bytes)</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <OrgInstallActions skill={skill} onActionDone={onActionDone} />
      <WorkspaceBindingsTable skillId={skill.id} onActionDone={onActionDone} />
    </div>
  );
}
```

- [ ] **Step 2: Implement `OrgInstallActions`**

Create `frontend/packages/web/components/admin/skills/OrgInstallActions.tsx`:

```tsx
"use client";

import { Button } from "@/components/ui/button";
import { apiClient } from "@/lib/api";
import { toast } from "sonner";
import type { SkillDetail } from "@cubeplex/core/types";

export function OrgInstallActions({
  skill,
  onActionDone,
}: {
  skill: SkillDetail;
  onActionDone: () => void;
}) {
  async function install() {
    try {
      await apiClient.post(`/api/v1/admin/skills/${skill.id}/install`, {
        version: skill.current_version,
      });
      toast.success(`已安装 v${skill.current_version}`);
      onActionDone();
    } catch (e) {
      toast.error(`安装失败: ${e}`);
    }
  }

  async function upgrade() {
    try {
      await apiClient.post(`/api/v1/admin/skills/${skill.id}/install`, {
        version: skill.current_version,
      });
      toast.success(`已升级到 v${skill.current_version}`);
      onActionDone();
    } catch (e) {
      toast.error(`升级失败: ${e}`);
    }
  }

  async function uninstall() {
    if (!confirm(`确认卸载 ${skill.name}?`)) return;
    try {
      await apiClient.delete(`/api/v1/admin/skills/${skill.id}/install`);
      toast.success("已卸载");
      onActionDone();
    } catch (e) {
      toast.error(`卸载失败: ${e}`);
    }
  }

  return (
    <div className="flex items-center gap-2 border-t bg-muted/20 px-4 py-3">
      <span className="text-sm">⚙ 组织安装:</span>
      {skill.install_state === "uninstalled" && (
        <Button size="sm" onClick={install}>
          安装 v{skill.current_version}
        </Button>
      )}
      {skill.install_state === "installed" && (
        <>
          <span className="text-sm text-muted-foreground">
            已安装 v{skill.installed_version}
          </span>
          <Button size="sm" variant="outline" onClick={uninstall}>
            卸载
          </Button>
        </>
      )}
      {skill.install_state === "update_available" && (
        <>
          <span className="text-sm text-muted-foreground">
            v{skill.installed_version} → v{skill.current_version}
          </span>
          <Button size="sm" onClick={upgrade}>
            升级
          </Button>
          <Button size="sm" variant="outline" onClick={uninstall}>
            卸载
          </Button>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Implement `WorkspaceBindingsTable`**

Create `frontend/packages/web/components/admin/skills/WorkspaceBindingsTable.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import { Checkbox } from "@/components/ui/checkbox";
import { apiClient } from "@/lib/api";
import { fetcher } from "@/lib/fetcher";
import { toast } from "sonner";

interface WorkspaceRow {
  id: string;
  name: string;
}

export function WorkspaceBindingsTable({
  skillId,
  onActionDone,
}: {
  skillId: string;
  onActionDone: () => void;
}) {
  const { data: workspaces } = useSWR<WorkspaceRow[]>("/api/v1/workspaces", fetcher);
  const [enabledMap, setEnabledMap] = useState<Record<string, boolean>>({});

  // Load current enabled state per workspace
  useEffect(() => {
    if (!workspaces) return;
    const fetchAll = async () => {
      const next: Record<string, boolean> = {};
      for (const ws of workspaces) {
        const list = await apiClient.get<{ id: string }[]>(
          `/api/v1/admin/workspaces/${ws.id}/skills`,
        );
        next[ws.id] = list.some((s) => s.id === skillId);
      }
      setEnabledMap(next);
    };
    fetchAll();
  }, [workspaces, skillId]);

  async function toggle(wsId: string, enabled: boolean) {
    try {
      if (enabled) {
        await apiClient.post(`/api/v1/admin/workspaces/${wsId}/skills`, {
          skill_ids: [skillId],
        });
      } else {
        await apiClient.delete(`/api/v1/admin/workspaces/${wsId}/skills/${skillId}`);
      }
      setEnabledMap((m) => ({ ...m, [wsId]: enabled }));
      onActionDone();
    } catch (e) {
      toast.error(`操作失败: ${e}`);
    }
  }

  if (!workspaces) return null;

  return (
    <div className="border-t p-4">
      <p className="mb-2 text-sm font-medium">Workspace 启用:</p>
      <div className="flex flex-wrap gap-3">
        {workspaces.map((ws) => (
          <label key={ws.id} className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={enabledMap[ws.id] ?? false}
              onCheckedChange={(v) => toggle(ws.id, Boolean(v))}
            />
            {ws.name}
          </label>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Type-check, lint**

```bash
pnpm --filter web type-check
pnpm --filter web lint
```

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/components/admin/skills/SkillDetailPanel.tsx \
        frontend/packages/web/components/admin/skills/OrgInstallActions.tsx \
        frontend/packages/web/components/admin/skills/WorkspaceBindingsTable.tsx
git commit -m "feat(m3): SkillDetailPanel + install actions + workspace bindings UI"
```

---

## Task 21: UploadSkillModal

**Files:**
- Replace: `frontend/packages/web/components/admin/skills/UploadSkillModal.tsx`

- [ ] **Step 1: Implement modal with drag-drop**

Replace `frontend/packages/web/components/admin/skills/UploadSkillModal.tsx`:

```tsx
"use client";

import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { apiClient } from "@/lib/api";
import { toast } from "sonner";

export function UploadSkillModal({
  open,
  onOpenChange,
  onUploaded,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onUploaded: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [errorDetail, setErrorDetail] = useState<string | null>(null);

  async function handleSubmit() {
    if (!file) return;
    setSubmitting(true);
    setErrorDetail(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const resp = await fetch("/api/v1/admin/skills/upload", {
        method: "POST",
        body: fd,
        credentials: "include",
        headers: {
          "X-CSRF-Token": readCookie("cubeplex_csrf") ?? "",
        },
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        const code = body?.detail?.code ?? `HTTP ${resp.status}`;
        const reason = body?.detail?.reason ?? body?.detail?.field ?? "";
        setErrorDetail(`${code}: ${reason}`);
        return;
      }
      toast.success("已上传");
      onUploaded();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>上传 skill</DialogTitle>
          <DialogDescription>
            选择 .zip 包；包根目录必须有 SKILL.md。
          </DialogDescription>
        </DialogHeader>
        <input
          type="file"
          accept=".zip"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="block w-full text-sm"
        />
        {errorDetail && (
          <p className="rounded bg-destructive/10 p-2 text-sm text-destructive">{errorDetail}</p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={handleSubmit} disabled={!file || submitting}>
            {submitting ? "上传中…" : "上传"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie
    .split("; ")
    .find((row) => row.startsWith(name + "="));
  return match ? decodeURIComponent(match.split("=")[1]) : null;
}
```

- [ ] **Step 2: Type-check + commit**

```bash
pnpm --filter web type-check
git add frontend/packages/web/components/admin/skills/UploadSkillModal.tsx
git commit -m "feat(m3): UploadSkillModal with drag-drop"
```

---

## Task 22: SkillView refactor (in-chat side panel)

**Files:**
- Modify: `frontend/packages/web/components/panel/SkillView.tsx`

- [ ] **Step 1: Replace contents**

Open `frontend/packages/web/components/panel/SkillView.tsx`. Replace with:

```tsx
"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR from "swr";
import { fetcher } from "@/lib/fetcher";
import { proseClasses } from "@/lib/utils";
import type { SkillContent } from "@cubeplex/core/types";

interface SkillViewProps {
  workspaceId: string;
  skillId: string;
  version?: string;
}

export function SkillView({ workspaceId, skillId, version }: SkillViewProps) {
  const params = version ? `?version=${encodeURIComponent(version)}` : "";
  const { data, error, isLoading } = useSWR<SkillContent>(
    `/api/v1/ws/${workspaceId}/skills/${skillId}${params}`,
    fetcher,
  );

  if (isLoading) return <div className="p-4 text-muted-foreground">加载中…</div>;
  if (error || !data) {
    return <div className="p-4 text-destructive">加载失败</div>;
  }

  return (
    <div className="space-y-3 p-4">
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground">Skill:</span>
        <span className="font-mono text-sm font-semibold">{data.name}</span>
        <span className="rounded-full bg-muted px-2 py-0.5 text-xs">v{data.version}</span>
      </div>
      <div className={proseClasses}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.content}</ReactMarkdown>
      </div>
      {data.files.length > 0 && (
        <div className="border-t pt-2">
          <p className="text-xs font-medium">文件:</p>
          <ul className="text-xs text-muted-foreground">
            {data.files.map((f) => (
              <li key={f.rel_path}>{f.rel_path}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
```

Update any callers (likely `frontend/packages/web/components/panel/ToolDetailPanel.tsx`) to pass `(workspaceId, skillId)` instead of `(args, result)`. The tool result for `load_skill` no longer drives the rendering — instead, when a `load_skill` tool result is selected, parse `skill_id` from the result JSON and use that.

For backward compatibility during the migration window, accept both prop shapes (legacy `{args, result}` and new `{workspaceId, skillId, version}`) and switch internally:

```tsx
type Props = SkillViewProps | { args: Record<string, unknown>; result: string | null };

export function SkillView(props: Props) {
  if ("workspaceId" in props) {
    return <SkillViewByApi {...props} />;
  }
  // Legacy fallback: render content from tool result
  return <SkillViewLegacy {...props} />;
}
```

(Keep the legacy renderer for the in-progress tool-call view; the new path handles previews from the catalog endpoint.)

- [ ] **Step 2: Commit**

```bash
git add frontend/packages/web/components/panel/SkillView.tsx
git commit -m "feat(m3): SkillView fetches from catalog API"
```

---

## Task 23: Frontend Playwright E2E specs

**Files:**
- Create: `frontend/packages/web/__tests__/e2e/skills/admin-skills-list.spec.ts`
- Create: `frontend/packages/web/__tests__/e2e/skills/admin-skills-install.spec.ts`
- Create: `frontend/packages/web/__tests__/e2e/skills/admin-skills-upload.spec.ts`
- Create: `frontend/packages/web/__tests__/e2e/skills/admin-workspace-toggle.spec.ts`

These are smoke specs; deeper interaction is covered by backend E2E. Pattern: open `/admin/skills` as an admin, assert visible elements + clicks. The login helper used by existing M2 admin tests should already exist (`adminLogin(page)` or similar) — reuse it.

- [ ] **Step 1: Implement `admin-skills-list.spec.ts`**

```typescript
import { test, expect } from "@playwright/test";
import { adminLogin } from "../helpers/auth";

test("admin can see skills list with at least the preinstalled set", async ({ page }) => {
  await adminLogin(page);
  await page.goto("/admin/skills");
  await expect(page.getByText("deep-research")).toBeVisible();
});

test("source filter narrows the list", async ({ page }) => {
  await adminLogin(page);
  await page.goto("/admin/skills");
  await page.getByRole("combobox", { name: /来源/ }).click();
  await page.getByRole("option", { name: "预装" }).click();
  await expect(page.getByText("deep-research")).toBeVisible();
});
```

- [ ] **Step 2: Implement `admin-skills-install.spec.ts`**

```typescript
import { test, expect } from "@playwright/test";
import { adminLogin } from "../helpers/auth";

test("install button changes detail panel to 'Installed'", async ({ page }) => {
  await adminLogin(page);
  await page.goto("/admin/skills");
  await page.getByText("deep-research").click();
  await page.getByRole("button", { name: /^安装/ }).click();
  await expect(page.getByText(/已安装/)).toBeVisible();
});
```

- [ ] **Step 3: Implement `admin-skills-upload.spec.ts`**

```typescript
import { test, expect } from "@playwright/test";
import path from "path";
import { adminLogin } from "../helpers/auth";

test("upload .zip creates new skill row", async ({ page }) => {
  await adminLogin(page);
  await page.goto("/admin/skills");
  await page.getByRole("button", { name: /上传 skill/ }).click();
  // Provide a fixture zip from __tests__/fixtures/sample-skill.zip
  const fixturePath = path.join(__dirname, "../fixtures/sample-skill.zip");
  await page.locator('input[type="file"]').setInputFiles(fixturePath);
  await page.getByRole("button", { name: "上传" }).click();
  await expect(page.getByText("已上传")).toBeVisible();
});
```

Create `frontend/packages/web/__tests__/fixtures/sample-skill.zip`. Use a small Python helper or pre-zip a SKILL.md (commit binary fixture).

- [ ] **Step 4: Implement `admin-workspace-toggle.spec.ts`**

```typescript
import { test, expect } from "@playwright/test";
import { adminLogin } from "../helpers/auth";

test("toggle workspace checkbox persists across reload", async ({ page }) => {
  await adminLogin(page);
  await page.goto("/admin/skills");
  await page.getByText("deep-research").click();
  // Ensure org-installed first
  await page.getByRole("button", { name: /^安装/ }).click().catch(() => {}); // ignore if already installed
  const checkbox = page.locator('label:has-text("Personal") input[type="checkbox"]');
  await checkbox.check();
  await page.reload();
  await page.getByText("deep-research").click();
  await expect(checkbox).toBeChecked();
});
```

- [ ] **Step 5: Run Playwright suite**

```bash
cd frontend && pnpm test:e2e --grep skills
```

Expected: all four pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/__tests__/e2e/skills/ \
        frontend/packages/web/__tests__/fixtures/sample-skill.zip
git commit -m "test(m3): Playwright E2E specs for admin skills tab"
```

---

# Batch 2 — `skill-creator` + artifact-based publish

## Task 24: `skill-creator` preinstalled skill

**Files:**
- Create: `backend/skills/preinstalled/skill-creator/SKILL.md`

- [ ] **Step 1: Author the skill prose**

Create `backend/skills/preinstalled/skill-creator/SKILL.md`:

```markdown
---
name: skill-creator
description: Use when the user wants to author a new skill bundle for the org marketplace. Walks them through frontmatter, body, and supporting files, then captures the result as a skill artifact ready to publish.
version: 0.1.0
keywords:
  - skill-authoring
  - marketplace
  - meta
---

# Skill Creator

You help the user author a new skill bundle that ends up as a skill artifact in
this conversation. The user can then click "Publish to org marketplace" on the
artifact.

## Workflow

1. **Ground the request.** Ask the user briefly:
   - What does the skill do? (one sentence)
   - When should it be used? (so we can write the `description` triggering string)
   - Does it need supporting files (templates, scripts)? Most skills are
     SKILL.md only — only add siblings if the user names a real script.

2. **Draft `SKILL.md`** with:
   - YAML frontmatter: `name`, `description`, `version` (start at `0.1.0`),
     and `keywords` if helpful.
     - `name` must be a slug like `weekly-okr-summarizer`. Do not include any
       `:` — the server prepends the org prefix on publish.
   - A short body in markdown that explains how to use the skill.

3. **Write to the sandbox.** Use the `execute` tool to create the directory
   and files:
   ```bash
   mkdir -p /workspace/<skill-name>
   cat > /workspace/<skill-name>/SKILL.md <<'SKILL'
   ---
   name: <skill-name>
   description: <description>
   version: 0.1.0
   ---

   # ...body...
   SKILL
   ```
   For each sibling file, write it with another `execute` call.

4. **Register as a skill artifact.** Call `save_artifact` with:
   - `name`: the skill's name (no colon prefix)
   - `artifact_type`: `"skill"`
   - `path`: `/workspace/<skill-name>` (the directory, not SKILL.md)
   - `entry_file`: `"SKILL.md"`
   - `description`: same as the frontmatter description

5. **Hand off to the user.** Tell them:
   > "Skill draft is ready. Open the artifact panel and click **发布到组织市场**
   > to publish it as v0.1.0. To iterate, ask me to edit the skill and re-save."

## Iteration

If the user wants changes:
- Edit the relevant file via `execute` (`cat > path <<EOF ... EOF`).
- Call `save_artifact` again with the same path; it auto-versions the artifact.
- The user re-publishes; remind them: **must bump the SKILL.md `version`
  field** before re-publishing, otherwise the marketplace returns 409.

## Constraints

- `artifact_type` must be exactly `"skill"`.
- Total bundle size ≤ 50 MB; single file ≤ 10 MB.
- Names must match `^[a-z0-9][a-z0-9-]{0,62}$`.
```

- [ ] **Step 2: Commit**

```bash
git add backend/skills/preinstalled/skill-creator/SKILL.md
git commit -m "feat(m3): skill-creator preinstalled skill (Batch 2)"
```

---

## Task 25: Publish from artifact (backend)

**Files:**
- Modify: `backend/cubeplex/skills/service.py` (add `publish_from_artifact`)
- Modify: `backend/cubeplex/api/routes/v1/ws_skills.py` (accept `{artifact_id}` JSON)
- Test: `backend/tests/e2e/test_skills_artifact_flow.py`

- [ ] **Step 1: Write failing E2E test**

Create `backend/tests/e2e/test_skills_artifact_flow.py`:

```python
"""E2E: publish a skill from an artifact (Batch 2)."""

import httpx
import pytest

from cubeplex.objectstore import get_objectstore_client
from cubeplex.repositories.artifact import ArtifactRepository
from cubeplex.repositories.skill import SkillRepository


@pytest.mark.asyncio
async def test_publish_from_artifact_creates_marketplace_version(
    member_client_with_artifact: tuple[httpx.AsyncClient, str, str],
    db_session,
) -> None:
    """member_client_with_artifact yields (client, ws_id, artifact_id) where the
    artifact has artifact_type='skill', SKILL.md at root in the artifact's
    object-storage path."""
    client, ws_id, artifact_id = member_client_with_artifact

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/skills/publish",
        json={"artifact_id": artifact_id},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "skill_version_id" in body

    # Skill row exists and is namespaced
    skill = await SkillRepository(db_session).get(body["skill_id"])
    assert skill is not None
    assert ":" in skill.name


@pytest.mark.asyncio
async def test_publish_from_artifact_with_invalid_skill_md_returns_400(
    member_client_with_bad_artifact: tuple[httpx.AsyncClient, str, str],
) -> None:
    client, ws_id, artifact_id = member_client_with_bad_artifact
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/skills/publish",
        json={"artifact_id": artifact_id},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "INVALID_FRONTMATTER"
```

The fixtures `member_client_with_artifact` and `member_client_with_bad_artifact` need to set up a skill artifact in object storage + DB. Add to `tests/e2e/conftest.py`:

```python
@pytest_asyncio.fixture
async def member_client_with_artifact(member_client, db_session):
    """Create an Artifact + upload SKILL.md bytes to its prefix; yield ids."""
    client, ws_id = member_client
    # Use existing helpers to create a conversation + artifact row
    # ... fixture body details depend on existing test helpers
    yield (client, ws_id, "<artifact_id>")
```

- [ ] **Step 2: Run, verify fails**

```bash
uv run pytest tests/e2e/test_skills_artifact_flow.py -v
```

- [ ] **Step 3: Implement `publish_from_artifact`**

In `backend/cubeplex/skills/service.py`, add a method on `SkillPublishService`:

```python
from cubeplex.models import Artifact

async def publish_from_artifact(
    self,
    *,
    org_id: str,
    org_slug: str,
    actor_user_id: str,
    artifact_id: str,
    workspace_id: str,
) -> SkillVersion:
    """Read artifact bytes from object storage, run publish pipeline."""
    artifact_repo = ArtifactRepository(
        self.session, org_id=org_id, workspace_id=workspace_id
    )
    artifact = await artifact_repo.get(artifact_id)
    if artifact is None:
        raise SkillMdMissingError(f"artifact {artifact_id} not found")
    if artifact.artifact_type != "skill":
        raise SkillMdMissingError(
            f"artifact {artifact_id} has type {artifact.artifact_type!r}, expected 'skill'"
        )

    store = get_objectstore_client()
    prefix = f"artifacts/{artifact.conversation_id}/{artifact.id}/v{artifact.version}/"
    keys = await store.list_keys(prefix)
    files: dict[str, bytes] = {}
    for key in keys:
        rel = key[len(prefix) :].lstrip("/")
        if not rel:
            continue
        files[rel] = await store.get(key)

    return await self._publish_from_files(
        org_id=org_id,
        org_slug=org_slug,
        actor_user_id=actor_user_id,
        files=files,
    )
```

(Add `from cubeplex.repositories.artifact import ArtifactRepository` to imports.)

- [ ] **Step 4: Update `ws_skills.py` publish endpoint to accept JSON body**

Edit `backend/cubeplex/api/routes/v1/ws_skills.py`. Change the publish handler signature to accept either a multipart upload or a JSON body. FastAPI doesn't natively dispatch by content-type; explicit branching:

```python
from fastapi import Body, Request
from cubeplex.api.schemas.skill import PublishFromArtifactRequest

@router.post("/publish", status_code=201)
async def publish_from_ws(
    ws_id: str,
    request: Request,
    *,
    rc: Annotated[RequestContext, Depends(get_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    org = await OrganizationRepository(session).get(rc.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")
    publisher = SkillPublishService(session=session, cache=_cache())

    content_type = request.headers.get("content-type", "")
    try:
        if content_type.startswith("application/json"):
            body = await request.json()
            req = PublishFromArtifactRequest(**body)
            sv = await publisher.publish_from_artifact(
                org_id=rc.org_id,
                org_slug=org.slug,
                actor_user_id=rc.user.id,
                artifact_id=req.artifact_id,
                workspace_id=ws_id,
            )
        else:
            form = await request.form()
            file = form.get("file")
            if file is None or not isinstance(file, UploadFile):
                raise HTTPException(
                    status_code=400,
                    detail={"code": "MISSING_BODY", "reason": "expected file= upload"},
                )
            zip_bytes = await file.read()
            sv = await publisher.publish_from_zip(
                org_id=rc.org_id,
                org_slug=org.slug,
                actor_user_id=rc.user.id,
                zip_bytes=zip_bytes,
            )
    except InvalidFrontmatterError as e:
        raise HTTPException(status_code=400, detail={"code": "INVALID_FRONTMATTER", "field": e.field, "reason": e.reason})
    except InvalidSkillNameError as e:
        raise HTTPException(status_code=400, detail={"code": "INVALID_SKILL_NAME", "reason": str(e)})
    except SkillMdMissingError as e:
        raise HTTPException(status_code=400, detail={"code": "SKILL_MD_MISSING", "reason": str(e)})
    except FileTooLargeError as e:
        raise HTTPException(status_code=400, detail={"code": "FILE_TOO_LARGE", "reason": str(e)})
    except VersionCollisionError as e:
        raise HTTPException(status_code=409, detail={"code": "VERSION_EXISTS", "reason": str(e)})
    return {"skill_version_id": sv.id, "skill_id": sv.skill_id, "version": sv.version}
```

- [ ] **Step 5: Run tests, verify pass**

```bash
uv run pytest tests/e2e/test_skills_artifact_flow.py tests/e2e/test_skills_publish_service.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/skills/service.py \
        backend/cubeplex/api/routes/v1/ws_skills.py \
        backend/tests/e2e/test_skills_artifact_flow.py \
        backend/tests/e2e/conftest.py
git commit -m "feat(m3): publish skill from artifact_id (Batch 2)"
```

---

## Task 26: `save_artifact` skill type docs

**Files:**
- Modify: `backend/cubeplex/middleware/artifacts.py`
- Modify: `backend/cubeplex/prompts/artifacts.py`

- [ ] **Step 1: Extend `save_artifact` description**

Open `backend/cubeplex/middleware/artifacts.py:155-164`. Update the StructuredTool description string to include skill semantics:

```python
description=(
    "Register a file or directory created in the sandbox as an artifact "
    "so the user can preview and download it. "
    "First create the files with the execute tool, then call this. "
    "For agent-authored skills, use artifact_type='skill', entry_file='SKILL.md', "
    "and ensure path points to a directory containing SKILL.md at the root."
),
```

Also extend the artifact_type description in `_SaveArtifactArgs` (line 27):

```python
artifact_type: str = Field(
    description="Type of artifact: file, website, code, document, image, data, or skill"
)
```

- [ ] **Step 2: Extend ARTIFACT_PROMPT**

Open `backend/cubeplex/prompts/artifacts.py`. Add a paragraph explaining when to use `artifact_type="skill"`:

```python
ARTIFACT_PROMPT = """\
... existing prompt ...

## Artifact types

- file / website / code / document / image / data — generic deliverables
- skill — a skill bundle (directory with SKILL.md at root). When the user is
  authoring a skill via skill-creator, save the bundle with artifact_type='skill'
  and entry_file='SKILL.md'. The user can publish the artifact to the org
  marketplace from the artifact preview panel.
"""
```

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/middleware/artifacts.py \
        backend/cubeplex/prompts/artifacts.py
git commit -m "feat(m3): document artifact_type='skill' (Batch 2)"
```

---

## Task 27: SkillArtifactPreview component

**Files:**
- Create: `frontend/packages/web/components/panel/artifact/SkillArtifactPreview.tsx`
- Modify: artifact panel router (likely `frontend/packages/web/components/panel/artifact/index.tsx` or similar)

- [ ] **Step 1: Implement preview component**

Create `frontend/packages/web/components/panel/artifact/SkillArtifactPreview.tsx`:

```tsx
"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR from "swr";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { fetcher } from "@/lib/fetcher";
import { proseClasses } from "@/lib/utils";
import { toast } from "sonner";

interface ArtifactRow {
  id: string;
  conversation_id: string;
  name: string;
  artifact_type: string;
  path: string;
  entry_file: string | null;
  version: number;
  description: string | null;
  updated_at: string;
}

interface ArtifactFileTree {
  files: { rel_path: string; size: number }[];
}

export function SkillArtifactPreview({
  artifact,
  workspaceId,
}: {
  artifact: ArtifactRow;
  workspaceId: string;
}) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Fetch the artifact's SKILL.md content (re-uses the artifact bytes endpoint
  // — adjust path if existing artifact preview has a different one)
  const { data: skillMd } = useSWR<string>(
    `/api/v1/ws/${workspaceId}/conversations/${artifact.conversation_id}/artifacts/${artifact.id}/files/SKILL.md`,
    (url: string) => fetch(url, { credentials: "include" }).then((r) => r.text()),
  );

  async function handlePublish() {
    setSubmitting(true);
    try {
      const resp = await fetch(`/api/v1/ws/${workspaceId}/skills/publish`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": readCookie("cubeplex_csrf") ?? "",
        },
        body: JSON.stringify({ artifact_id: artifact.id }),
      });
      if (resp.status === 409) {
        toast.error("版本已存在；请在 SKILL.md 中 bump version 后再发布");
        return;
      }
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        toast.error(`发布失败: ${body?.detail?.reason ?? resp.status}`);
        return;
      }
      toast.success("已发布到组织市场");
      setConfirmOpen(false);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 p-4">
      <header className="flex items-baseline gap-2">
        <span className="font-mono font-semibold">{artifact.name}</span>
        <span className="text-xs text-muted-foreground">entry: SKILL.md</span>
        <span className="text-xs text-muted-foreground">
          artifact v{artifact.version}
        </span>
      </header>

      <div className={proseClasses}>
        {skillMd ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{skillMd}</ReactMarkdown>
        ) : (
          <p className="text-muted-foreground">加载中…</p>
        )}
      </div>

      <div className="border-t pt-3">
        <Button onClick={() => setConfirmOpen(true)}>发布到组织市场</Button>
      </div>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认发布</DialogTitle>
          </DialogHeader>
          <p className="text-sm">
            将这个 skill 作为 v0.1.0（取自 SKILL.md frontmatter 的 version 字段）
            发布到组织市场。一旦发布无法修改 — 需要在 SKILL.md 里 bump version
            后才能再次发布。
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              取消
            </Button>
            <Button onClick={handlePublish} disabled={submitting}>
              {submitting ? "发布中…" : "确认发布"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie
    .split("; ")
    .find((row) => row.startsWith(name + "="));
  return match ? decodeURIComponent(match.split("=")[1]) : null;
}
```

- [ ] **Step 2: Register the component for `artifact_type === "skill"`**

The artifact panel currently dispatches by `artifact_type` (file / website / code / etc.). Find that dispatch logic (search for `artifact_type` in `frontend/packages/web/components/panel/`) and add a `case "skill": return <SkillArtifactPreview ... />` branch.

If there's a registry-style map, add an entry:

```typescript
import { SkillArtifactPreview } from "./SkillArtifactPreview";

const ARTIFACT_PREVIEWS: Record<string, ComponentType<...>> = {
  // ... existing
  skill: SkillArtifactPreview,
};
```

- [ ] **Step 3: Type-check**

```bash
pnpm --filter web type-check
```

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/panel/artifact/SkillArtifactPreview.tsx \
        frontend/packages/web/components/panel/artifact/
git commit -m "feat(m3): SkillArtifactPreview with publish action (Batch 2)"
```

---

## Task 28: Batch 2 Playwright E2E

**Files:**
- Create: `frontend/packages/web/__tests__/e2e/skills/chat-skill-artifact-preview.spec.ts`

- [ ] **Step 1: Implement spec**

```typescript
import { test, expect } from "@playwright/test";
import { memberLogin } from "../helpers/auth";

test("publishing a skill artifact ends up in admin marketplace", async ({ page, context }) => {
  await memberLogin(page);
  // Navigate to a chat where a skill artifact was already created
  // (test fixture creates conversation + artifact via API to keep this fast)
  await page.goto("/w/PERSONAL_WS/c/CONV_WITH_SKILL_ARTIFACT");

  // Open artifact panel
  await page.getByRole("button", { name: /artifact/i }).click();

  // Click publish
  await page.getByRole("button", { name: "发布到组织市场" }).click();
  await page.getByRole("button", { name: "确认发布" }).click();

  // Toast appears
  await expect(page.getByText("已发布到组织市场")).toBeVisible();

  // Switch to admin tab to confirm catalog has the skill
  const adminPage = await context.newPage();
  await memberLogin(adminPage); // assumes member is also admin in test org
  await adminPage.goto("/admin/skills");
  await expect(adminPage.getByText(/:.*$/)).toBeVisible(); // any namespaced skill row
});
```

The spec assumes a fixture creates the chat + skill artifact ahead of time. Wire this in `playwright.config.ts` global setup, or use API helpers in `beforeAll`.

- [ ] **Step 2: Run spec**

```bash
cd frontend && pnpm test:e2e --grep skill-artifact
```

Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/__tests__/e2e/skills/chat-skill-artifact-preview.spec.ts
git commit -m "test(m3): Playwright e2e for skill artifact publish (Batch 2)"
```

---

# Wrap-up

## Task 29: Final verification

- [ ] **Step 1: Run all backend tests**

```bash
cd backend && uv run pytest -v
```

Expected: full backend suite passes.

- [ ] **Step 2: Run all frontend tests**

```bash
cd frontend && pnpm type-check && pnpm test:e2e
```

Expected: type-check + Playwright pass.

- [ ] **Step 3: Smoke-test in browser**

```bash
cd backend && uv run python main.py &
cd frontend && pnpm dev &
```

Open http://localhost:3000, register a new user, navigate to `/admin/skills`. Verify:
- Preinstalled skills visible
- Can install + see workspace bindings table
- Can upload a sample .zip
- Chat creates a skill artifact via `skill-creator` and publishes successfully

- [ ] **Step 4: Pre-push hooks**

```bash
cd backend && make check
cd frontend && pnpm lint
```

- [ ] **Step 5: Push branch + open PR**

```bash
git push -u origin m3-skills-marketplace
gh pr create --title "M3: Skills marketplace (Batch 1 + Batch 2)" \
  --body "$(cat <<'EOF'
## Summary
- 3-tier skills marketplace (global pool → org marketplace → workspace bindings)
- Catalog-driven SkillsMiddleware + load_skill (no filesystem)
- Transparent sandbox sync via LazySandbox
- skill-creator + artifact-based publish flow

## Test plan
- [ ] backend pytest suite green
- [ ] frontend type-check + Playwright green
- [ ] manual smoke: install + workspace toggle + upload + skill-creator publish
EOF
)"
```

---

# Self-review checklist

Run through these before declaring the plan done:

- [ ] **Spec coverage:** every spec section (1-14) has at least one task implementing it. Cross-check § 9.1 test rows against test files in Tasks 5/6/7/13/16/25.
- [ ] **No placeholders:** scan for "TBD", "TODO", "implement later" — only allowed in `# TODO(M3-followup):` comments that explicitly mark Batch 2+ work.
- [ ] **Type consistency:** `SkillCatalogService` / `SkillPublishService` / `ResolvedSkill` / `SkillFrontmatter` shapes match across tasks 6, 7, 9, 10, 11, 13, 25.
- [ ] **Commit hygiene:** every task ends in a single `git commit`; no orphan files.
- [ ] **Org slug rule:** Task 0 wires it in; Tasks 7/9/10 actually use it (`org.slug`).
- [ ] **No DB FKs:** Task 1 spec confirms no `foreign_key=` on new tables.
- [ ] **Redis lock:** Task 5 implementation uses `redis.lock(...)` with `blocking=False` and `timeout=60`.

