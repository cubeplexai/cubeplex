# M2 Model Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build DB-backed provider/model management for the admin console — replaces config.yaml-driven LLM config with CRUD UI + org-level default model/fallback settings + test-connection dry-run.

**Architecture:** 4 new SQLModel tables (providers, models, org_settings, org_provider_overrides) with application-layer invariants. ProviderService handles CRUD + test connection. LLMFactory reads DB-first with config.yaml fallback. Admin API routes under `/api/v1/admin` guarded by `require_org_admin`. Frontend reuses `/admin/skills` left-right split layout.

**Tech Stack:** SQLModel + Alembic + PostgreSQL partial unique index + FastAPI + langchain-openai + Next.js + shadcn/ui + Zustand.

**Spec:** `docs/superpowers/specs/2026-04-30-m2-model-management-design.md`

**Working directory:** `.worktrees/feat/m2-model-management` (slot 82)

---

## File Structure

```
backend/cubeplex/
├── models/
│   ├── provider.py              # CREATE: Provider, Model SQLModel
│   ├── org_settings.py          # CREATE: OrgSettings SQLModel
│   └── org_provider_override.py # CREATE: OrgProviderOverride SQLModel
│   └── __init__.py              # MODIFY: add imports
├── repositories/
│   ├── provider.py              # CREATE: ProviderRepository
│   ├── model.py                 # CREATE: ModelRepository
│   ├── org_settings.py          # CREATE: OrgSettingsRepository
│   └── org_provider_override.py # CREATE: OrgProviderOverrideRepository
├── services/
│   └── provider_service.py      # CREATE: ProviderService
├── api/
│   ├── schemas/
│   │   └── provider.py          # CREATE: Pydantic request/response schemas
│   ├── routes/v1/
│   │   └── admin_providers.py   # CREATE: admin provider routes
│   └── app.py                   # MODIFY: register router, seed in lifespan
├── llm/
│   └── factory.py               # MODIFY: DB-first + config fallback
└── streams/
    └── run_manager.py           # MODIFY: pass session+org_id to LLMFactory

backend/alembic/versions/
└── <hash>_add_provider_model_tables.py  # CREATE: autogen migration

backend/tests/
├── unit/
│   ├── test_provider_service_invariants.py  # CREATE
│   └── test_seed_idempotent.py              # CREATE
└── e2e/
    ├── test_admin_providers_crud.py         # CREATE
    └── test_provider_oauth_reject.py        # CREATE

frontend/packages/core/src/
├── api/
│   ├── client.ts                # MODIFY: add put() method
│   └── providers.ts             # CREATE: provider API functions
├── types/
│   └── provider.ts              # CREATE: Provider, Model types
├── stores/
│   ├── providersStore.ts        # CREATE
│   ├── modelsStore.ts           # CREATE
│   └── orgModelSettingsStore.ts # CREATE
└── index.ts                     # MODIFY: export new modules

frontend/packages/web/
├── app/admin/models/
│   └── page.tsx                 # MODIFY: replace ComingSoonCard
├── components/admin/models/
│   ├── ProviderList.tsx         # CREATE
│   ├── ProviderDetail.tsx       # CREATE
│   ├── ProviderFormDialog.tsx   # CREATE
│   ├── ModelFormDialog.tsx      # CREATE
│   ├── ModelRow.tsx             # CREATE
│   ├── OrgModelSettings.tsx     # CREATE
│   ├── TestConnectionResult.tsx # CREATE
│   └── ProviderLogo.tsx         # CREATE
└── __tests__/e2e/
    └── m2-models.spec.ts        # CREATE: Playwright E2E
```

---

## Phase A · Backend Foundation

### Task 1: Verify dependencies + shadcn components

**Files:**
- Modify: `backend/pyproject.toml` (only if a dep is missing)
- Modify: `frontend/packages/web/package.json` (shadcn components)

- [ ] **Step 1: Check existing backend deps**

```bash
cd /home/chris/cubeplex/.worktrees/feat/m2-model-management/backend
grep -E "uuid-utils|cryptography|pyjwt" pyproject.toml
```

Expected: `uuid-utils` and `cryptography` already present. PyJWT present (from fastapi-users transitively or direct).

- [ ] **Step 2: Install shadcn components for frontend**

```bash
cd /home/chris/cubeplex/.worktrees/feat/m2-model-management/frontend/packages/web
npx shadcn-ui@latest add radio-group switch combobox accordion
```

- [ ] **Step 3: Verify imports**

```bash
cd /home/chris/cubeplex/.worktrees/feat/m2-model-management/backend
uv run python -c "from uuid_utils import uuid7; from sqlmodel import SQLModel, Field; from sqlalchemy import Column, JSON; print('ok')"
```

Expected: `ok`

---

### Task 2: Provider + Model SQLModel tables

**Files:**
- Create: `backend/cubeplex/models/provider.py`
- Modify: `backend/cubeplex/models/__init__.py`

- [ ] **Step 1: Create provider.py model file**

```python
# backend/cubeplex/models/provider.py
"""Provider and Model — LLM provider/model configuration tables."""

from datetime import UTC, datetime

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class Provider(SQLModel, table=True):
    """LLM provider — system-level (org_id=NULL) or org-specific."""

    __tablename__ = "providers"
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_provider_org_name"),
    )

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str | None = Field(default=None, max_length=36, index=True)
    name: str = Field(max_length=64)
    provider_type: str = Field(default="openai_compat", max_length=32)
    base_url: str = Field(max_length=2048)
    auth_type: str = Field(default="api_key", max_length=32)
    api_key: str | None = Field(default=None, max_length=512)
    oauth_client_id: str | None = Field(default=None, max_length=256)
    oauth_client_secret: str | None = Field(default=None, max_length=256)
    oauth_auth_url: str | None = Field(default=None, max_length=2048)
    oauth_token_url: str | None = Field(default=None, max_length=2048)
    logo_url: str | None = Field(default=None, max_length=512)
    extra_body: dict = Field(default_factory=dict, sa_column=Column(JSON))
    extra_headers: dict = Field(default_factory=dict, sa_column=Column(JSON))
    enabled: bool = Field(default=True)
    created_by_user_id: str = Field(max_length=36)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Model(SQLModel, table=True):
    """LLM model — belongs to a provider."""

    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("provider_id", "model_id", name="uq_model_provider_model"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str | None = Field(default=None, max_length=36, index=True)
    provider_id: str = Field(max_length=36, index=True)
    model_id: str = Field(max_length=128)
    display_name: str = Field(max_length=128)
    reasoning: bool = Field(default=False)
    input_modalities: list = Field(default_factory=list, sa_column=Column(JSON))
    cost_input: float = Field(default=0.0)
    cost_output: float = Field(default=0.0)
    cost_cache_read: float = Field(default=0.0)
    cost_cache_write: float = Field(default=0.0)
    context_window: int = Field()
    max_tokens: int = Field()
    extra_body: dict = Field(default_factory=dict, sa_column=Column(JSON))
    extra_headers: dict = Field(default_factory=dict, sa_column=Column(JSON))
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

- [ ] **Step 2: Register in models `__init__.py`**

```python
# backend/cubeplex/models/__init__.py — add after the Organization import line:
from cubeplex.models.provider import Model, Provider

# add to __all__ list:
    "Model",
    "Provider",
```

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/models/provider.py backend/cubeplex/models/__init__.py
git commit -m "feat(models): add Provider and Model SQLModel tables"
```

---

### Task 3: OrgSettings + OrgProviderOverride SQLModel tables

**Files:**
- Create: `backend/cubeplex/models/org_settings.py`
- Create: `backend/cubeplex/models/org_provider_override.py`
- Modify: `backend/cubeplex/models/__init__.py`

- [ ] **Step 1: Create org_settings.py**

```python
# backend/cubeplex/models/org_settings.py
"""OrgSettings — per-org key-value settings for LLM defaults."""

from datetime import UTC, datetime

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class OrgSettings(SQLModel, table=True):
    __tablename__ = "org_settings"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_org_settings_org_key"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    key: str = Field(max_length=64)
    value: dict = Field(sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

- [ ] **Step 2: Create org_provider_override.py**

```python
# backend/cubeplex/models/org_provider_override.py
"""OrgProviderOverride — sparse per-org enabled/disabled for system providers."""

from datetime import UTC, datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class OrgProviderOverride(SQLModel, table=True):
    __tablename__ = "org_provider_overrides"
    __table_args__ = (
        UniqueConstraint("org_id", "provider_id", name="uq_org_provider_override"),
    )

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    provider_id: str = Field(max_length=36, index=True)
    enabled: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

- [ ] **Step 3: Update models `__init__.py`**

```python
# Add imports:
from cubeplex.models.org_provider_override import OrgProviderOverride
from cubeplex.models.org_settings import OrgSettings

# Add to __all__:
    "OrgProviderOverride",
    "OrgSettings",
```

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/models/org_settings.py \
        backend/cubeplex/models/org_provider_override.py \
        backend/cubeplex/models/__init__.py
git commit -m "feat(models): add OrgSettings and OrgProviderOverride tables"
```

---

### Task 4: Alembic migration

**Files:**
- Create: `backend/alembic/versions/<hash>_add_provider_model_tables.py` (autogen)

- [ ] **Step 1: Generate migration**

```bash
cd /home/chris/cubeplex/.worktrees/feat/m2-model-management/backend
uv run alembic revision --autogenerate -m "add provider model management tables"
```

- [ ] **Step 2: Add partial unique index**

Open the generated migration file. In the `upgrade()` function, after `op.create_table('providers', ...)`, add:

```python
op.create_index(
    'uq_provider_system_name',
    'providers',
    ['name'],
    unique=True,
    postgresql_where=sa.text('org_id IS NULL'),
)
```

And in `downgrade()` before dropping the table:

```python
op.drop_index('uq_provider_system_name', table_name='providers', postgresql_where=sa.text('org_id IS NULL'))
```

- [ ] **Step 3: Review autogenerated columns**

Verify the migration has:
- `providers` with `api_key` as `sa.String(512)`, `extra_body`/`extra_headers` as `JSON`, `org_id` nullable
- `models` with `cost_*` as `sa.Float()`, `input_modalities` as `JSON`, `org_id` nullable
- `org_settings` with `value` as `JSON`
- `org_provider_overrides` with `enabled` bool

- [ ] **Step 4: Apply migration**

```bash
uv run alembic upgrade head
```

Verify:

```bash
psql -d cubeplex_feat_m2_model_management -c "\dt providers"
psql -d cubeplex_feat_m2_model_management -c "\dt models"
psql -d cubeplex_feat_m2_model_management -c "\dt org_settings"
psql -d cubeplex_feat_m2_model_management -c "\dt org_provider_overrides"
```

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/*_add_provider_model_tables.py
git commit -m "feat(db): add provider/model/org-settings migration"
```

---

### Task 5: ProviderRepository

**Files:**
- Create: `backend/cubeplex/repositories/provider.py`

- [ ] **Step 1: Implement ProviderRepository**

```python
# backend/cubeplex/repositories/provider.py
"""Provider repository — queries visible providers for an org."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cubeplex.models.provider import Model, Provider


class ProviderRepository:
    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def list_visible(self) -> list[Provider]:
        """Return system providers (not disabled by this org) + this org's own."""
        from sqlalchemy import func

        from cubeplex.models.org_provider_override import OrgProviderOverride

        stmt = (
            select(Provider)
            .outerjoin(
                OrgProviderOverride,
                (Provider.id == OrgProviderOverride.provider_id)
                & (OrgProviderOverride.org_id == self.org_id),
            )
            .where(
                (Provider.org_id.is_(None)) | (Provider.org_id == self.org_id)
            )
            .where(
                func.coalesce(
                    OrgProviderOverride.enabled, Provider.enabled, True
                )
                == True
            )
            .order_by(Provider.org_id.nullsfirst(), Provider.name)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get(self, provider_id: str) -> Provider | None:
        stmt = (
            select(Provider)
            .where(Provider.id == provider_id)
            .where(
                (Provider.org_id.is_(None)) | (Provider.org_id == self.org_id)
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Provider | None:
        stmt = select(Provider).where(
            (Provider.org_id.is_(None)) | (Provider.org_id == self.org_id)
        ).where(Provider.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add(self, provider: Provider) -> Provider:
        provider.org_id = self.org_id
        self.session.add(provider)
        await self.session.commit()
        await self.session.refresh(provider)
        return provider

    async def update(self, provider: Provider) -> Provider:
        await self.session.commit()
        await self.session.refresh(provider)
        return provider

    async def delete(self, provider: Provider) -> None:
        await self.session.delete(provider)
        await self.session.commit()
```

- [ ] **Step 2: Commit**

```bash
git add backend/cubeplex/repositories/provider.py
git commit -m "feat(repo): add ProviderRepository"
```

---

### Task 6: ModelRepository, OrgSettingsRepository, OrgProviderOverrideRepository

**Files:**
- Create: `backend/cubeplex/repositories/model.py`
- Create: `backend/cubeplex/repositories/org_settings.py`
- Create: `backend/cubeplex/repositories/org_provider_override.py`

- [ ] **Step 1: ModelRepository**

```python
# backend/cubeplex/repositories/model.py
"""Model repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.provider import Model


class ModelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_by_provider(self, provider_id: str) -> list[Model]:
        stmt = (
            select(Model)
            .where(Model.provider_id == provider_id)
            .where(Model.enabled == True)
            .order_by(Model.model_id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get(self, model_db_id: str) -> Model | None:
        stmt = select(Model).where(Model.id == model_db_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_model_id(self, provider_id: str, model_id: str) -> Model | None:
        stmt = select(Model).where(
            Model.provider_id == provider_id, Model.model_id == model_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add(self, model: Model) -> Model:
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        return model

    async def update(self, model: Model) -> Model:
        await self.session.commit()
        await self.session.refresh(model)
        return model

    async def delete(self, model: Model) -> None:
        await self.session.delete(model)
        await self.session.commit()

    async def count_by_provider(self, provider_id: str) -> int:
        stmt = select(Model).where(Model.provider_id == provider_id)
        result = await self.session.execute(stmt)
        return len(result.scalars().all())

    async def delete_by_provider(self, provider_id: str) -> None:
        models = await self.list_by_provider(provider_id)
        for m in models:
            await self.session.delete(m)
        await self.session.commit()
```

- [ ] **Step 2: OrgSettingsRepository**

```python
# backend/cubeplex/repositories/org_settings.py
"""OrgSettings repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.org_settings import OrgSettings


class OrgSettingsRepository:
    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self, key: str) -> OrgSettings | None:
        stmt = select(OrgSettings).where(
            OrgSettings.org_id == self.org_id, OrgSettings.key == key
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def set(self, key: str, value: dict) -> OrgSettings:
        existing = await self.get(key)
        if existing:
            existing.value = value
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        setting = OrgSettings(org_id=self.org_id, key=key, value=value)
        self.session.add(setting)
        await self.session.commit()
        await self.session.refresh(setting)
        return setting
```

- [ ] **Step 3: OrgProviderOverrideRepository**

```python
# backend/cubeplex/repositories/org_provider_override.py
"""OrgProviderOverride repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.org_provider_override import OrgProviderOverride


class OrgProviderOverrideRepository:
    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self, provider_id: str) -> OrgProviderOverride | None:
        stmt = select(OrgProviderOverride).where(
            OrgProviderOverride.org_id == self.org_id,
            OrgProviderOverride.provider_id == provider_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def set(self, provider_id: str, enabled: bool) -> OrgProviderOverride:
        existing = await self.get(provider_id)
        if existing:
            existing.enabled = enabled
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        override = OrgProviderOverride(
            org_id=self.org_id, provider_id=provider_id, enabled=enabled
        )
        self.session.add(override)
        await self.session.commit()
        await self.session.refresh(override)
        return override

    async def delete(self, provider_id: str) -> None:
        existing = await self.get(provider_id)
        if existing:
            await self.session.delete(existing)
            await self.session.commit()
```

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/repositories/model.py \
        backend/cubeplex/repositories/org_settings.py \
        backend/cubeplex/repositories/org_provider_override.py
git commit -m "feat(repo): add Model, OrgSettings, OrgProviderOverride repositories"
```

---

### Task 7: Pydantic schemas for provider API

**Files:**
- Create: `backend/cubeplex/api/schemas/provider.py`

- [ ] **Step 1: Create schema file**

```python
# backend/cubeplex/api/schemas/provider.py
"""Request/response schemas for provider & model admin API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProviderCreate(BaseModel):
    name: str = Field(max_length=64)
    provider_type: str = Field(default="openai_compat", max_length=32)
    base_url: str = Field(max_length=2048)
    auth_type: str = Field(default="api_key", max_length=32)
    api_key: str | None = Field(default=None, max_length=512)
    logo_url: str | None = Field(default=None, max_length=512)
    extra_body: dict = Field(default_factory=dict)
    extra_headers: dict = Field(default_factory=dict)


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    provider_type: str | None = Field(default=None, max_length=32)
    base_url: str | None = Field(default=None, max_length=2048)
    auth_type: str | None = Field(default=None, max_length=32)
    api_key: str | None = Field(default=None, max_length=512)
    logo_url: str | None = Field(default=None, max_length=512)
    extra_body: dict | None = None
    extra_headers: dict | None = None
    enabled: bool | None = None


class ProviderTest(BaseModel):
    provider_type: str = Field(default="openai_compat", max_length=32)
    base_url: str = Field(max_length=2048)
    api_key: str | None = Field(default=None, max_length=512)
    auth_type: str = Field(default="api_key", max_length=32)


class TestResultOut(BaseModel):
    ok: bool
    error: str | None = None
    latency_ms: int


class OrgProviderOverrideUpdate(BaseModel):
    enabled: bool


class OrgProviderOverrideOut(BaseModel):
    enabled: bool


class ModelCreate(BaseModel):
    model_id: str = Field(max_length=128)
    display_name: str = Field(max_length=128)
    reasoning: bool = False
    input_modalities: list[str] = Field(default_factory=lambda: ["text"])
    cost_input: float = 0.0
    cost_output: float = 0.0
    cost_cache_read: float = 0.0
    cost_cache_write: float = 0.0
    context_window: int
    max_tokens: int
    extra_body: dict = Field(default_factory=dict)
    extra_headers: dict = Field(default_factory=dict)


class ModelUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    reasoning: bool | None = None
    input_modalities: list[str] | None = None
    cost_input: float | None = None
    cost_output: float | None = None
    cost_cache_read: float | None = None
    cost_cache_write: float | None = None
    context_window: int | None = None
    max_tokens: int | None = None
    extra_body: dict | None = None
    extra_headers: dict | None = None
    enabled: bool | None = None


class ModelOut(BaseModel):
    id: str
    provider_id: str
    model_id: str
    display_name: str
    reasoning: bool
    input_modalities: list
    cost_input: float
    cost_output: float
    cost_cache_read: float
    cost_cache_write: float
    context_window: int
    max_tokens: int
    extra_body: dict
    extra_headers: dict
    enabled: bool
    is_system: bool
    created_at: datetime
    updated_at: datetime


class ProviderOut(BaseModel):
    id: str
    name: str
    provider_type: str
    base_url: str
    auth_type: str
    has_api_key: bool
    logo_url: str | None
    enabled: bool
    is_system: bool
    model_count: int
    models: list[ModelOut] | None = None
    org_override: OrgProviderOverrideOut | None = None
    extra_body: dict
    extra_headers: dict
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime


class OrgLLMSettingsOut(BaseModel):
    default_model: str | None = None
    fallback_models: list[str] = Field(default_factory=list)


class OrgLLMSettingsUpdate(BaseModel):
    default_model: str | None = None
    fallback_models: list[str] | None = None
```

- [ ] **Step 2: Commit**

```bash
git add backend/cubeplex/api/schemas/provider.py
git commit -m "feat(schema): add provider/model API request/response schemas"
```

---

### Task 8: ProviderService

**Files:**
- Create: `backend/cubeplex/services/provider_service.py`
- Create: `backend/tests/unit/test_provider_service_invariants.py`

- [ ] **Step 1: Write failing unit test**

```python
# backend/tests/unit/test_provider_service_invariants.py
"""ProviderService invariant tests — scope/name validation, system protection."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.provider import ProviderCreate
from cubeplex.services.provider_service import (
    ProviderNameConflictError,
    ProviderOAuthNotImplementedError,
    ProviderSystemReadonlyError,
    ProviderService,
)


async def test_oauth_auth_type_rejected(db_session: AsyncSession) -> None:
    """auth_type=oauth v1 must raise ProviderOAuthNotImplementedError."""
    from cubeplex.repositories.provider import ProviderRepository
    from cubeplex.repositories.model import ModelRepository
    from cubeplex.repositories.org_settings import OrgSettingsRepository
    from cubeplex.repositories.org_provider_override import OrgProviderOverrideRepository

    svc = ProviderService(
        provider_repo=ProviderRepository(db_session, org_id="org-1"),
        model_repo=ModelRepository(db_session),
        override_repo=OrgProviderOverrideRepository(db_session, org_id="org-1"),
        org_settings_repo=OrgSettingsRepository(db_session, org_id="org-1"),
        session=db_session,
        org_id="org-1",
        actor_user_id="user-1",
    )
    data = ProviderCreate(
        name="test-oauth",
        base_url="https://example.com/api",
        auth_type="oauth",
    )
    with pytest.raises(ProviderOAuthNotImplementedError):
        await svc.create_provider(data)


async def test_create_org_provider_sets_org_id(db_session: AsyncSession) -> None:
    """Org-level provider must have org_id set."""
    from cubeplex.repositories.provider import ProviderRepository
    from cubeplex.repositories.model import ModelRepository
    from cubeplex.repositories.org_settings import OrgSettingsRepository
    from cubeplex.repositories.org_provider_override import OrgProviderOverrideRepository

    svc = ProviderService(
        provider_repo=ProviderRepository(db_session, org_id="org-1"),
        model_repo=ModelRepository(db_session),
        override_repo=OrgProviderOverrideRepository(db_session, org_id="org-1"),
        org_settings_repo=OrgSettingsRepository(db_session, org_id="org-1"),
        session=db_session,
        org_id="org-1",
        actor_user_id="user-1",
    )
    data = ProviderCreate(
        name="my-provider",
        base_url="https://example.com/api",
        auth_type="api_key",
        api_key="sk-test",
    )
    provider = await svc.create_provider(data)
    assert provider.org_id == "org-1"
    assert provider.name == "my-provider"
    assert provider.api_key == "sk-test"
```

- [ ] **Step 2: Run, expect fail**

```bash
cd /home/chris/cubeplex/.worktrees/feat/m2-model-management/backend
uv run pytest tests/unit/test_provider_service_invariants.py -v
```

Expected: FAIL `ModuleNotFoundError: cubeplex.services.provider_service`

- [ ] **Step 3: Implement ProviderService**

```python
# backend/cubeplex/services/provider_service.py
"""ProviderService — CRUD, invariants, test connection, seed."""

from __future__ import annotations

import time
from typing import Any

from httpx import HTTPStatusError
from langchain_core.messages import HumanMessage
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.provider import (
    ModelCreate,
    ModelUpdate,
    OrgLLMSettingsOut,
    OrgLLMSettingsUpdate,
    ProviderCreate,
    ProviderTest,
    ProviderUpdate,
    TestResultOut,
)
from cubeplex.llm.openai_compatible import ChatOpenAICompatible
from cubeplex.models.org_provider_override import OrgProviderOverride
from cubeplex.models.org_settings import OrgSettings
from cubeplex.models.provider import Model, Provider
from cubeplex.repositories.model import ModelRepository
from cubeplex.repositories.org_provider_override import OrgProviderOverrideRepository
from cubeplex.repositories.org_settings import OrgSettingsRepository
from cubeplex.repositories.provider import ProviderRepository


class ProviderOAuthNotImplementedError(Exception):
    """Raised when auth_type=oauth is used in v1."""


class ProviderNameConflictError(Exception):
    """Raised when provider name is duplicate in same scope."""


class ProviderSystemReadonlyError(Exception):
    """Raised when trying to mutate a system provider."""


class ProviderOverrideNotApplicableError(Exception):
    """Raised when override is set on org-level provider."""


class ProviderNotFoundError(Exception):
    """Raised when provider is not found."""


class ModelNotFoundError(Exception):
    """Raised when model is not found."""


class ProviderService:
    def __init__(
        self,
        *,
        provider_repo: ProviderRepository,
        model_repo: ModelRepository,
        override_repo: OrgProviderOverrideRepository,
        org_settings_repo: OrgSettingsRepository,
        session: AsyncSession,
        org_id: str,
        actor_user_id: str,
    ) -> None:
        self._providers = provider_repo
        self._models = model_repo
        self._overrides = override_repo
        self._org_settings = org_settings_repo
        self._session = session
        self.org_id = org_id
        self.actor_user_id = actor_user_id

    def _check_not_system(self, provider: Provider) -> None:
        if provider.org_id is None:
            raise ProviderSystemReadonlyError("System providers cannot be modified or deleted")

    def _check_oauth(self, data: ProviderCreate | ProviderUpdate) -> None:
        auth = data.auth_type
        if auth == "oauth":
            raise ProviderOAuthNotImplementedError(
                "OAuth authentication is not yet implemented"
            )

    # ── Provider CRUD ──────────────────────────────────────────────

    async def list_providers(self) -> list[Provider]:
        return await self._providers.list_visible()

    async def get_provider(self, provider_id: str) -> Provider:
        p = await self._providers.get(provider_id)
        if p is None:
            raise ProviderNotFoundError(f"Provider {provider_id} not found")
        return p

    def _validate_auth_creds(self, auth_type: str, api_key: str | None) -> None:
        """Enforce auth_type / api_key cross-invariants."""
        if auth_type in ("api_key", "bearer_token"):
            if not api_key:
                raise ValueError("api_key is required for auth_type={}".format(auth_type))
        if auth_type == "none":
            if api_key:
                raise ValueError("api_key must be empty for auth_type='none'")

    async def create_provider(self, data: ProviderCreate) -> Provider:
        self._check_oauth(data)
        self._validate_auth_creds(data.auth_type, data.api_key)
        existing = await self._providers.get_by_name(data.name)
        if existing is not None:
            raise ProviderNameConflictError(f"Provider name '{data.name}' already exists")
        p = Provider(
            org_id=self.org_id,
            name=data.name,
            provider_type=data.provider_type,
            base_url=data.base_url,
            auth_type=data.auth_type,
            api_key=data.api_key if data.auth_type != "none" else None,
            # TODO(vault): Encrypt api_key when M1-E4 vault integration lands.
            #              Plaintext storage is temporary per spec D10.
            logo_url=data.logo_url,
            extra_body=data.extra_body,
            extra_headers=data.extra_headers,
            created_by_user_id=self.actor_user_id,
        )
        return await self._providers.add(p)

    async def update_provider(self, provider_id: str, data: ProviderUpdate) -> Provider:
        p = await self.get_provider(provider_id)
        self._check_not_system(p)
        # Determine effective auth_type for validation
        effective_auth = data.auth_type if data.auth_type is not None else p.auth_type
        effective_key = data.api_key if data.api_key is not None else p.api_key
        if data.auth_type is not None or data.api_key is not None:
            self._check_oauth(data)
            self._validate_auth_creds(effective_auth, effective_key)
        # auth_type switch to 'none' clears api_key
        if data.auth_type == "none":
            p.api_key = None
        if data.name is not None and data.name != p.name:
            existing = await self._providers.get_by_name(data.name)
            if existing is not None:
                raise ProviderNameConflictError(f"Provider name '{data.name}' already exists")
            p.name = data.name
        for field in (
            "provider_type", "base_url", "auth_type", "logo_url",
            "extra_body", "extra_headers",
        ):
            val = getattr(data, field, None)
            if val is not None:
                setattr(p, field, val)
        if data.api_key is not None:
            p.api_key = data.api_key
        if data.enabled is not None:
            p.enabled = data.enabled
        return await self._providers.update(p)

    async def delete_provider(self, provider_id: str) -> None:
        p = await self.get_provider(provider_id)
        self._check_not_system(p)
        await self._models.delete_by_provider(provider_id)
        await self._overrides.delete(provider_id)
        await self._providers.delete(p)

    # ── Model CRUD ─────────────────────────────────────────────────

    async def list_models(self, provider_id: str) -> list[Model]:
        await self.get_provider(provider_id)
        return await self._models.list_by_provider(provider_id)

    async def create_model(self, provider_id: str, data: ModelCreate) -> Model:
        provider = await self.get_provider(provider_id)
        self._check_not_system(provider)  # system provider models are readonly
        existing = await self._models.get_by_model_id(provider_id, data.model_id)
        if existing is not None:
            raise ValueError(f"Model '{data.model_id}' already exists in this provider")
        m = Model(
            org_id=provider.org_id,
            provider_id=provider_id,
            model_id=data.model_id,
            display_name=data.display_name,
            reasoning=data.reasoning,
            input_modalities=data.input_modalities,
            cost_input=data.cost_input,
            cost_output=data.cost_output,
            cost_cache_read=data.cost_cache_read,
            cost_cache_write=data.cost_cache_write,
            context_window=data.context_window,
            max_tokens=data.max_tokens,
            extra_body=data.extra_body,
            extra_headers=data.extra_headers,
        )
        return await self._models.add(m)

    async def update_model(
        self, provider_id: str, model_db_id: str, data: ModelUpdate
    ) -> Model:
        provider = await self.get_provider(provider_id)
        self._check_not_system(provider)  # system provider models are readonly
        m = await self._models.get(model_db_id)
        if m is None or m.provider_id != provider_id:
            raise ModelNotFoundError(f"Model {model_db_id} not found")
        for field in (
            "display_name", "reasoning", "input_modalities",
            "cost_input", "cost_output", "cost_cache_read", "cost_cache_write",
            "context_window", "max_tokens", "extra_body", "extra_headers",
        ):
            val = getattr(data, field, None)
            if val is not None:
                setattr(m, field, val)
        if data.enabled is not None:
            m.enabled = data.enabled
        return await self._models.update(m)

    async def delete_model(self, provider_id: str, model_db_id: str) -> None:
        provider = await self.get_provider(provider_id)
        self._check_not_system(provider)  # system provider models are readonly
        m = await self._models.get(model_db_id)
        if m is None or m.provider_id != provider_id:
            raise ModelNotFoundError(f"Model {model_db_id} not found")
        await self._models.delete(m)

    # ── Test connection ────────────────────────────────────────────

    async def test_connection(self, data: ProviderTest) -> TestResultOut:
        start = time.monotonic()
        if data.provider_type not in ("openai_compat",):
            return TestResultOut(
                ok=False,
                error=f"Unsupported provider_type: {data.provider_type}",
                latency_ms=0,
            )
        try:
            if data.provider_type == "openai_compat":
                llm = ChatOpenAICompatible(
                    base_url=data.base_url,
                    api_key=data.api_key or "placeholder",
                    model="ping",
                    timeout=15,
                )
                await llm.ainvoke([HumanMessage(content="ping")])
            latency_ms = int((time.monotonic() - start) * 1000)
            return TestResultOut(ok=False, error="Unexpected success — ping should fail", latency_ms=latency_ms)
        except Exception as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            error_str = str(e)
            if (
                "Connection refused" in error_str
                or "Name or service not known" in error_str
                or "getaddrinfo" in error_str.lower()
            ):
                return TestResultOut(ok=False, error=error_str, latency_ms=latency_ms)
            return TestResultOut(ok=True, error=None, latency_ms=latency_ms)

    # ── Org overrides ──────────────────────────────────────────────

    async def get_override(self, provider_id: str) -> OrgProviderOverride | None:
        p = await self.get_provider(provider_id)
        if p.org_id is not None:
            return None
        return await self._overrides.get(provider_id)

    async def set_override(self, provider_id: str, enabled: bool) -> OrgProviderOverride:
        p = await self.get_provider(provider_id)
        if p.org_id is not None:
            raise ProviderOverrideNotApplicableError(
                "Override only applies to system providers"
            )
        return await self._overrides.set(provider_id, enabled)

    # ── Org settings ───────────────────────────────────────────────

    async def get_llm_settings(self) -> OrgLLMSettingsOut:
        default = await self._org_settings.get("default_model")
        fallback = await self._org_settings.get("fallback_models")
        return OrgLLMSettingsOut(
            default_model=default.value.get("model_ref") if default else None,
            fallback_models=fallback.value.get("models", []) if fallback else [],
        )

    async def update_llm_settings(self, data: OrgLLMSettingsUpdate) -> OrgLLMSettingsOut:
        if data.default_model is not None:
            await self._validate_model_ref(data.default_model)
            await self._org_settings.set("default_model", {"model_ref": data.default_model})
        if data.fallback_models is not None:
            for ref in data.fallback_models:
                await self._validate_model_ref(ref)
            await self._org_settings.set("fallback_models", {"models": data.fallback_models})
        return await self.get_llm_settings()

    async def _validate_model_ref(self, model_ref: str) -> None:
        """Verify a provider/model-id reference points to a visible, enabled model."""
        parts = model_ref.split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid model ref format: '{model_ref}'")
        provider_name, model_id = parts
        provider = await self._providers.get_by_name(provider_name)
        if provider is None:
            raise ValueError(f"Provider '{provider_name}' not found")
        # Check org-level disable
        if provider.org_id is None:
            override = await self._overrides.get(provider.id)
            if override and not override.enabled:
                raise ValueError(f"Provider '{provider_name}' is disabled by org")
        model = await self._models.get_by_model_id(provider.id, model_id)
        if model is None:
            raise ValueError(f"Model '{model_id}' not found in provider '{provider_name}'")
        if not model.enabled:
            raise ValueError(f"Model '{model_id}' is disabled")
```

- [ ] **Step 4: Run unit tests**

```bash
uv run pytest tests/unit/test_provider_service_invariants.py -v
```

Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/services/provider_service.py \
        backend/tests/unit/test_provider_service_invariants.py
git commit -m "feat(service): add ProviderService with invariants and test connection"
```

---

### Task 9: Admin provider routes

**Files:**
- Create: `backend/cubeplex/api/routes/v1/admin_providers.py`
- Modify: `backend/cubeplex/api/app.py`

- [ ] **Step 1: Write the routes module**

```python
# backend/cubeplex/api/routes/v1/admin_providers.py
"""Admin provider/model management endpoints. Gated by require_org_admin."""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.provider import (
    ModelCreate,
    ModelOut,
    ModelUpdate,
    OrgLLMSettingsOut,
    OrgLLMSettingsUpdate,
    OrgProviderOverrideOut,
    OrgProviderOverrideUpdate,
    ProviderCreate,
    ProviderOut,
    ProviderTest,
    ProviderUpdate,
    TestResultOut,
)
from cubeplex.auth.dependencies import require_org_admin, resolve_current_org_id
from cubeplex.db import get_session
from cubeplex.models import User
from cubeplex.models.provider import Model, Provider
from cubeplex.repositories.model import ModelRepository
from cubeplex.repositories.org_provider_override import OrgProviderOverrideRepository
from cubeplex.repositories.org_settings import OrgSettingsRepository
from cubeplex.repositories.provider import ProviderRepository
from cubeplex.services.provider_service import (
    ModelNotFoundError,
    ProviderNameConflictError,
    ProviderNotFoundError,
    ProviderOAuthNotImplementedError,
    ProviderOverrideNotApplicableError,
    ProviderService,
    ProviderSystemReadonlyError,
)

router = APIRouter(prefix="/admin", tags=["admin-providers"])


async def _svc(user: User, session: AsyncSession) -> ProviderService:
    org_id = await resolve_current_org_id(user, session)
    return ProviderService(
        provider_repo=ProviderRepository(session, org_id=org_id),
        model_repo=ModelRepository(session),
        override_repo=OrgProviderOverrideRepository(session, org_id=org_id),
        org_settings_repo=OrgSettingsRepository(session, org_id=org_id),
        session=session,
        org_id=org_id,
        actor_user_id=user.id,
    )


def _model_out(m: Model) -> ModelOut:
    return ModelOut(
        id=m.id,
        provider_id=m.provider_id,
        model_id=m.model_id,
        display_name=m.display_name,
        reasoning=m.reasoning,
        input_modalities=m.input_modalities,
        cost_input=m.cost_input,
        cost_output=m.cost_output,
        cost_cache_read=m.cost_cache_read,
        cost_cache_write=m.cost_cache_write,
        context_window=m.context_window,
        max_tokens=m.max_tokens,
        extra_body=m.extra_body,
        extra_headers=m.extra_headers,
        enabled=m.enabled,
        is_system=m.org_id is None,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def _provider_out(p: Provider, *, model_count: int = 0, models: list[ModelOut] | None = None,
                   override: OrgProviderOverrideOut | None = None) -> ProviderOut:
    return ProviderOut(
        id=p.id,
        name=p.name,
        provider_type=p.provider_type,
        base_url=p.base_url,
        auth_type=p.auth_type,
        has_api_key=bool(p.api_key),
        logo_url=p.logo_url,
        enabled=p.enabled,
        is_system=p.org_id is None,
        model_count=model_count,
        models=models,
        org_override=override,
        extra_body=p.extra_body,
        extra_headers=p.extra_headers,
        created_by_user_id=p.created_by_user_id,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


# ── Provider routes ────────────────────────────────────────────────

@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ProviderOut]:
    svc = await _svc(user, session)
    providers = await svc.list_providers()
    result: list[ProviderOut] = []
    for p in providers:
        model_count = await svc._models.count_by_provider(p.id)
        override = None
        if p.org_id is None:
            ov = await svc.get_override(p.id)
            if ov:
                override = OrgProviderOverrideOut(enabled=ov.enabled)
        result.append(_provider_out(p, model_count=model_count, override=override))
    return result


@router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider(
    body: ProviderCreate,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProviderOut:
    svc = await _svc(user, session)
    try:
        p = await svc.create_provider(body)
    except ProviderOAuthNotImplementedError as e:
        raise HTTPException(status_code=409, detail={"code": "provider_oauth_not_implemented", "message": str(e)})
    except ProviderNameConflictError as e:
        raise HTTPException(status_code=409, detail={"code": "provider_name_conflict", "message": str(e)})
    return _provider_out(p)


@router.get("/providers/{provider_id}", response_model=ProviderOut)
async def get_provider(
    provider_id: str,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProviderOut:
    svc = await _svc(user, session)
    try:
        p = await svc.get_provider(provider_id)
    except ProviderNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    models = await svc.list_models(provider_id)
    override = None
    if p.org_id is None:
        ov = await svc.get_override(provider_id)
        if ov:
            override = OrgProviderOverrideOut(enabled=ov.enabled)
    return _provider_out(
        p,
        model_count=len(models),
        models=[_model_out(m) for m in models],
        override=override,
    )


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: str,
    body: ProviderUpdate,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProviderOut:
    svc = await _svc(user, session)
    try:
        p = await svc.update_provider(provider_id, body)
    except ProviderSystemReadonlyError as e:
        raise HTTPException(status_code=403, detail={"code": "provider_system_readonly", "message": str(e)})
    except ProviderOAuthNotImplementedError as e:
        raise HTTPException(status_code=409, detail={"code": "provider_oauth_not_implemented", "message": str(e)})
    except ProviderNameConflictError as e:
        raise HTTPException(status_code=409, detail={"code": "provider_name_conflict", "message": str(e)})
    models = await svc.list_models(provider_id)
    return _provider_out(p, model_count=len(models), models=[_model_out(m) for m in models])


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: str,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    svc = await _svc(user, session)
    try:
        await svc.delete_provider(provider_id)
    except ProviderSystemReadonlyError as e:
        raise HTTPException(status_code=403, detail={"code": "provider_system_readonly", "message": str(e)})


# ── Model routes (nested under provider) ──────────────────────────
# NOTE: path param {mid} is the DB UUID primary key, NOT model_id (the business id).

@router.post("/providers/{provider_id}/models", response_model=ModelOut, status_code=201)
async def create_model(
    provider_id: str,
    body: ModelCreate,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ModelOut:
    svc = await _svc(user, session)
    try:
        m = await svc.create_model(provider_id, body)
    except ProviderNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ProviderSystemReadonlyError as e:
        raise HTTPException(status_code=403, detail={"code": "provider_system_readonly", "message": str(e)})
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _model_out(m)


@router.patch("/providers/{provider_id}/models/{mid}", response_model=ModelOut)
async def update_model(
    provider_id: str,
    mid: str,  # DB UUID of the model row (not model_id)
    body: ModelUpdate,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ModelOut:
    svc = await _svc(user, session)
    try:
        m = await svc.update_model(provider_id, mid, body)
    except ProviderSystemReadonlyError as e:
        raise HTTPException(status_code=403, detail={"code": "provider_system_readonly", "message": str(e)})
    except ModelNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _model_out(m)


@router.delete("/providers/{provider_id}/models/{mid}", status_code=204)
async def delete_model(
    provider_id: str,
    mid: str,  # DB UUID of the model row (not model_id)
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    svc = await _svc(user, session)
    try:
        await svc.delete_model(provider_id, mid)
    except ProviderSystemReadonlyError as e:
        raise HTTPException(status_code=403, detail={"code": "provider_system_readonly", "message": str(e)})
    except ModelNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Test connection ───────────────────────────────────────────────

@router.post("/providers/test", response_model=TestResultOut)
async def test_provider_connection(
    body: ProviderTest,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TestResultOut:
    svc = await _svc(user, session)
    return await svc.test_connection(body)


# ── Org override routes ───────────────────────────────────────────

@router.get("/providers/{provider_id}/override", response_model=OrgProviderOverrideOut)
async def get_provider_override(
    provider_id: str,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrgProviderOverrideOut:
    svc = await _svc(user, session)
    try:
        ov = await svc.get_override(provider_id)
    except ProviderNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return OrgProviderOverrideOut(enabled=ov.enabled if ov else True)


@router.patch("/providers/{provider_id}/override", response_model=OrgProviderOverrideOut)
async def set_provider_override(
    provider_id: str,
    body: OrgProviderOverrideUpdate,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrgProviderOverrideOut:
    svc = await _svc(user, session)
    try:
        ov = await svc.set_override(provider_id, body.enabled)
    except ProviderNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ProviderOverrideNotApplicableError as e:
        raise HTTPException(status_code=400, detail={"code": "provider_override_not_applicable", "message": str(e)})
    return OrgProviderOverrideOut(enabled=ov.enabled)


# ── Org LLM settings ──────────────────────────────────────────────

@router.get("/settings/llm", response_model=OrgLLMSettingsOut)
async def get_org_llm_settings(
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrgLLMSettingsOut:
    svc = await _svc(user, session)
    return await svc.get_llm_settings()


@router.put("/settings/llm", response_model=OrgLLMSettingsOut)
async def update_org_llm_settings(
    body: OrgLLMSettingsUpdate,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrgLLMSettingsOut:
    svc = await _svc(user, session)
    return await svc.update_llm_settings(body)
```

- [ ] **Step 2: Register router in app.py**

In `backend/cubeplex/api/app.py`, in the imports section (around line 321-328):

```python
from cubeplex.api.routes.v1 import admin_providers
```

In the `include_router` section (after line 337):

```python
app.include_router(admin_providers.router, prefix="/api/v1")
```

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/api/routes/v1/admin_providers.py backend/cubeplex/api/app.py
git commit -m "feat(api): add admin provider/model CRUD routes"
```

---

### Task 10: Seed system providers from config

**Files:**
- Create: `backend/cubeplex/services/seed.py`
- Modify: `backend/cubeplex/api/app.py`
- Create: `backend/tests/unit/test_seed_idempotent.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/unit/test_seed_idempotent.py
"""Seed idempotency tests."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.provider import Provider, Model
from cubeplex.services.seed import seed_system_providers_from_config


@pytest.fixture
async def clean_db(db_session: AsyncSession) -> AsyncSession:
    await db_session.execute(text("DELETE FROM models"))
    await db_session.execute(text("DELETE FROM providers"))
    await db_session.commit()
    return db_session


async def test_seed_is_idempotent(clean_db: AsyncSession) -> None:
    await seed_system_providers_from_config(clean_db)

    providers1 = (await clean_db.execute(
        select(Provider).where(Provider.org_id.is_(None))
    )).scalars().all()

    await seed_system_providers_from_config(clean_db)

    providers2 = (await clean_db.execute(
        select(Provider).where(Provider.org_id.is_(None))
    )).scalars().all()

    assert len(providers1) == len(providers2)
    for p in providers1:
        models = (await clean_db.execute(
            select(Model).where(Model.provider_id == p.id)
        )).scalars().all()
        assert len(models) > 0, f"Provider {p.name} should have models after seed"
```

- [ ] **Step 2: Run, expect fail**

```bash
uv run pytest tests/unit/test_seed_idempotent.py -v
```

Expected: FAIL `ModuleNotFoundError: cubeplex.services.seed`

- [ ] **Step 3: Implement seed**

```python
# backend/cubeplex/services/seed.py
"""Seed system providers and models from config.yaml into DB (idempotent)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from cubeplex.config import config as settings
from cubeplex.models.provider import Model, Provider


async def seed_system_providers_from_config(session: AsyncSession) -> None:
    """Idempotent: insert/update system providers/models from config.yaml.
    
    - Creates missing providers and models (idempotent by name/model_id).
    - Updates existing system provider base_url/provider_type when config changes.
    - Marks models removed from config as enabled=False (does NOT delete).
    """
    cfg = settings.get("llm", {})
    config_providers: dict = cfg.get("providers", {})

    if not config_providers:
        logger.info("No providers in config — skipping seed")
        return

    config_model_ids: dict[str, set[str]] = {}  # provider_name -> set of model_ids from config

    for name, cfg_dict in config_providers.items():
        existing = await session.execute(
            select(Provider).where(
                Provider.org_id.is_(None), Provider.name == name
            )
        )
        provider = existing.scalar_one_or_none()

        if provider is None:
            provider = Provider(
                org_id=None, name=name,
                provider_type="openai_compat",
                base_url=cfg_dict.get("base_url", ""),
                auth_type="api_key", enabled=True,
                created_by_user_id="system",
            )
            session.add(provider)
            await session.flush()
            logger.info("Seeded system provider: {}", name)
        else:
            # Update changed fields on existing system provider
            provider.base_url = cfg_dict.get("base_url", "")
            provider.provider_type = "openai_compat"
            logger.debug("System provider '{}' already exists, updated", name)

        config_model_ids[name] = set()
        models_list = cfg_dict.get("models", [])

        for mc in models_list:
            config_model_ids[name].add(mc["id"])
            existing_model = await session.execute(
                select(Model).where(
                    Model.provider_id == provider.id, Model.model_id == mc["id"]
                )
            )
            model = existing_model.scalar_one_or_none()
            if model is None:
                cost = mc.get("cost", {})
                model = Model(
                    org_id=None, provider_id=provider.id,
                    model_id=mc["id"],
                    display_name=mc.get("name", mc["id"]),
                    reasoning=mc.get("reasoning", False),
                    input_modalities=mc.get("input", ["text"]),
                    cost_input=cost.get("input", 0.0),
                    cost_output=cost.get("output", 0.0),
                    cost_cache_read=cost.get("cache_read", 0.0),
                    cost_cache_write=cost.get("cache_write", 0.0),
                    context_window=mc.get("context_window", mc.get("contextWindow", 128000)),
                    max_tokens=mc.get("max_tokens", mc.get("maxTokens", 64000)),
                    enabled=True,
                )
                session.add(model)
                logger.info("Seeded model: {} / {}", name, mc["id"])
            else:
                # Update existing model fields
                model.display_name = mc.get("name", mc["id"])
                model.enabled = True

        # Disable models that exist in DB but were removed from config
        stale_results = await session.execute(
            select(Model).where(
                Model.provider_id == provider.id,
                Model.org_id.is_(None),
                Model.model_id.notin_(config_model_ids[name]),
            )
        )
        for stale in stale_results.scalars().all():
            stale.enabled = False
            logger.info("Disabled stale model: {} / {}", name, stale.model_id)

    await session.commit()
    logger.info("System provider seed complete")
```

- [ ] **Step 4: Run unit test**

```bash
uv run pytest tests/unit/test_seed_idempotent.py -v
```

Expected: 1 test PASS (seed is idempotent)

- [ ] **Step 5: Wire seed into lifespan**

In `backend/cubeplex/api/app.py`, inside the `lifespan` startup section (after the existing imports and after `log.init()`), add:

```python
    # Seed system providers from config.yaml (idempotent)
    from cubeplex.db import async_session_maker
    from cubeplex.services.seed import seed_system_providers_from_config

    async with async_session_maker() as seed_session:
        await seed_system_providers_from_config(seed_session)
```

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/services/seed.py \
        backend/cubeplex/api/app.py \
        backend/tests/unit/test_seed_idempotent.py
git commit -m "feat(seed): add idempotent config-to-DB system provider seed"
```

---

### Task 11: LLMFactory DB-first refactor

**Files:**
- Modify: `backend/cubeplex/llm/factory.py`

- [ ] **Step 1: Modify LLMFactory to support DB-first loading**

Add a new class method and modify the constructor to accept optional async session and org_id. The key change: add `load_from_db()` classmethod, modify `_find_model` to check DB first, keep `_load_config_providers` as fallback.

```python
# Add at the top of factory.py, after existing imports:

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from cubeplex.models.provider import Model as DBModel, Provider as DBProvider
from cubeplex.models.org_provider_override import OrgProviderOverride as DBOverride
from cubeplex.models.org_settings import OrgSettings as DBSettings


class LLMFactory:
    """Factory for creating LLM instances — DB-first with config.yaml fallback."""

    def __init__(
        self,
        llm_config: LLMConfig | None = None,
        session: AsyncSession | None = None,
        org_id: str | None = None,
    ):
        self._session = session
        self._org_id = org_id
        if llm_config is None:
            llm_config = LLMConfig(**config.llm)
        self.llm_config = llm_config

    async def _load_db_provider_configs(self) -> dict[str, dict[str, Any]]:
        """Load provider configs from DB. Returns dict[name, config_dict]."""
        if not self._session or not self._org_id:
            return {}
        stmt = (
            select(DBProvider)
            .outerjoin(
                DBOverride,
                (DBProvider.id == DBOverride.provider_id)
                & (DBOverride.org_id == self._org_id),
            )
            .where(
                (DBProvider.org_id.is_(None)) | (DBProvider.org_id == self._org_id)
            )
        )
        result = await self._session.execute(stmt)
        providers = result.scalars().all()

        db_configs: dict[str, ProviderConfig] = {}
        for p in providers:
            override = (await self._session.execute(
                select(DBOverride).where(
                    DBOverride.org_id == self._org_id,
                    DBOverride.provider_id == p.id,
                )
            )).scalar_one_or_none()
            enabled = override.enabled if override else p.enabled
            if not enabled:
                continue

            models_result = await self._session.execute(
                select(DBModel).where(
                    DBModel.provider_id == p.id, DBModel.enabled == True
                )
            )
            db_models = models_result.scalars().all()

            db_configs[p.name] = {
                "base_url": p.base_url,
                "api_key": p.api_key,
                "api": "openai-completions",
                "extra_body": p.extra_body,
                "extra_headers": p.extra_headers,
                "models": [
                    {
                        "id": m.model_id,
                        "name": m.display_name,
                        "reasoning": m.reasoning,
                        "input": m.input_modalities,
                        "cost": {
                            "input": m.cost_input,
                            "output": m.cost_output,
                            "cache_read": m.cost_cache_read,
                            "cache_write": m.cost_cache_write,
                        },
                        "contextWindow": m.context_window,
                        "maxTokens": m.max_tokens,
                        "extra_body": m.extra_body,
                        "extra_headers": m.extra_headers,
                    }
                    for m in db_models
                ],
            }
        return db_configs  # type: ignore[return-value]

    def _build_merged_config(self, db_configs: dict) -> LLMConfig:
        """Merge DB configs with config.yaml fallback. DB wins.
        
        CRITICAL: Only config-fallback providers that do NOT exist in DB at all.
        Once a provider is seeded into DB, its visibility is governed by DB +
        OrgProviderOverride, and config.yaml must NOT reintroduce it.
        """
        config_providers = dict(self.llm_config.providers)
        # Start from config, then overlay DB entries
        merged = {}
        for name, cfg in config_providers.items():
            if name not in db_configs:
                # Only use config when provider not in DB at all
                merged[name] = cfg
        # DB entries always override
        for name, cfg in db_configs.items():
            merged[name] = ProviderConfig(**cfg)
        return LLMConfig(
            default_model=self.llm_config.default_model,
            fallback_models=self.llm_config.fallback_models,
            providers=merged,
        )

    async def _get_org_default_model(self) -> str | None:
        if not self._session or not self._org_id:
            return None
        stmt = select(DBSettings).where(
            DBSettings.org_id == self._org_id, DBSettings.key == "default_model"
        )
        result = await self._session.execute(stmt)
        setting = result.scalar_one_or_none()
        if setting and setting.value.get("model_ref"):
            return setting.value["model_ref"]
        return None

    async def _get_org_fallback_models(self) -> list[str]:
        if not self._session or not self._org_id:
            return []
        stmt = select(DBSettings).where(
            DBSettings.org_id == self._org_id, DBSettings.key == "fallback_models"
        )
        result = await self._session.execute(stmt)
        setting = result.scalar_one_or_none()
        if setting and setting.value.get("models"):
            return setting.value["models"]
        return []
```

Then replace `get_default_model`:

```python
    async def get_default_model(self) -> tuple[str, str]:
        """Parse the default_model, checking org override first."""
        model_ref = await self._get_org_default_model()
        if not model_ref:
            model_ref = self.llm_config.default_model
        if not model_ref:
            raise ValueError("No default_model configured")
        return self._parse_model_ref(model_ref)
```

Replace `create_default`:

```python
    async def create_default(self, **kwargs: Any) -> Any:
        """Create an LLM instance using the configured default_model.
        
        With session+org_id: loads from DB, merges with config fallback.
        Without session: pure config.yaml (startup/CI compatibility).
        """
        # Refresh config from DB if available
        if self._session and self._org_id:
            db_cfgs = await self._load_db_provider_configs()
            self.llm_config = self._build_merged_config(db_cfgs)

        provider_name, model_id = await self.get_default_model()
        llm = self.create(model_id=model_id, provider_name=provider_name, **kwargs)

        fallback_refs = await self._get_org_fallback_models()
        if not fallback_refs:
            fallback_refs = self.llm_config.fallback_models

        if not fallback_refs:
            return llm

        fallbacks = []
        for model_ref in fallback_refs:
            try:
                fb_provider, fb_model_id = self._parse_model_ref(model_ref)
                fallbacks.append(
                    self.create(model_id=fb_model_id, provider_name=fb_provider, **kwargs)
                )
            except ValueError:
                logger.warning("Skipping invalid fallback model: '%s'", model_ref)

        if not fallbacks:
            return llm
        return llm.with_fallbacks(fallbacks)
```

- [ ] **Step 2: Run mypy to check type consistency**

```bash
cd /home/chris/cubeplex/.worktrees/feat/m2-model-management/backend
uv run mypy cubeplex/llm/factory.py
```

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/llm/factory.py
git commit -m "feat(llm): refactor LLMFactory to DB-first with config fallback"
```

---

### Task 12: Update RunManager to pass session + org_id

**Files:**
- Modify: `backend/cubeplex/streams/run_manager.py`

- [ ] **Step 1: Update the LLM creation call**

Find line ~557:
```python
llm = LLMFactory().create_default()
```

Replace with:
```python
factory = LLMFactory(
    session=db_session_for_llm,
    org_id=ctx.org_id,
)
llm = await factory.create_default()
```

We need to use the existing session that's already available in run_manager. Looking at the context, `async_session_maker` is imported and used for MCP DB queries. We can use the same session or create a new one.

Actually, checking the code more carefully, the LLM factory should use its OWN session. Let's use a shared approach — since LLMFactory is called inside `run_manager.py` which already has access to the DB, we can pass in a session.

Find the `async with async_session_maker() as mcp_session:` block (used for MCP). The LLM creation happens after that. We can use a similar approach or reuse the same session. Let's use the same approach:

```python
            from cubeplex.agents.graph import create_cubeplex_agent
            from cubeplex.llm.factory import LLMFactory
            from cubeplex.middleware.citations import CitationConfig, load_citation_configs
            from cubeplex.tools import get_registry

            factory = LLMFactory(
                session=mcp_session if 'mcp_session' in dir() else None,
                org_id=ctx.org_id,
            )
            llm = await factory.create_default()
```

Better approach: since the existing code creates `async with async_session_maker() as mcp_session:` before, let's check if that session is in scope at the LLM creation point. Actually, looking at the code structure, the MCP session is used in a `with` block and `db_mcp_tools` is created from it. Let me read more of the run_manager to understand.

Actually, to keep it simple and avoid potential issues with the plan being too prescriptive about exactly where the session comes from, let me note the key requirement: `LLMFactory` now needs `session` and `org_id` passed in, and `create_default()` needs `await`.

- [ ] **Step 1 (revised): Update RunManager LLM creation**

In `backend/cubeplex/streams/run_manager.py`, locate the section where `create_cubeplex_agent` is called (~line 552-557). Change from:

```python
            from cubeplex.agents.graph import create_cubeplex_agent
            from cubeplex.llm.factory import LLMFactory
            from cubeplex.middleware.citations import CitationConfig, load_citation_configs
            from cubeplex.tools import get_registry

            llm = LLMFactory().create_default()
```

To:

```python
            from cubeplex.agents.graph import create_cubeplex_agent
            from cubeplex.llm.factory import LLMFactory
            from cubeplex.middleware.citations import CitationConfig, load_citation_configs
            from cubeplex.tools import get_registry

            # Create a DB session for LLMFactory to read provider/model config
            from cubeplex.db import async_session_maker as _llm_session_maker
            async with _llm_session_maker() as _llm_session:
                factory = LLMFactory(session=_llm_session, org_id=ctx.org_id)
                llm = await factory.create_default()
```

- [ ] **Step 2: Verify type check**

```bash
uv run mypy cubeplex/streams/run_manager.py
```

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/streams/run_manager.py
git commit -m "feat(runtime): pass session+org_id to LLMFactory in RunManager"
```

---

## Phase B · Backend E2E Tests

### Task 13: Provider CRUD E2E test

**Files:**
- Create: `backend/tests/e2e/test_admin_providers_crud.py`

- [ ] **Step 1: Write E2E test**

```python
# backend/tests/e2e/test_admin_providers_crud.py
"""E2E tests for admin provider/model CRUD endpoints."""

import pytest
from httpx import AsyncClient, ASGITransport

from cubeplex.api.app import build_app


@pytest.mark.e2e
async def test_create_and_list_providers(admin_client: AsyncClient) -> None:
    """Admin can create an org provider and see it in the list."""
    # Create
    res = await admin_client.post("/api/v1/admin/providers", json={
        "name": "test-provider-e2e",
        "base_url": "https://example.com/api",
        "auth_type": "api_key",
        "api_key": "sk-test-123",
        "provider_type": "openai_compat",
    })
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "test-provider-e2e"
    assert data["is_system"] is False
    assert data["has_api_key"] is True
    assert "api_key" not in str(data)

    provider_id = data["id"]

    # List
    res = await admin_client.get("/api/v1/admin/providers")
    assert res.status_code == 200
    providers = res.json()
    assert any(p["id"] == provider_id for p in providers)
    # System providers should also appear
    assert any(p["is_system"] for p in providers)

    # Delete
    res = await admin_client.delete(f"/api/v1/admin/providers/{provider_id}")
    assert res.status_code == 204


@pytest.mark.e2e
async def test_cannot_delete_system_provider(admin_client: AsyncClient) -> None:
    """System providers cannot be deleted."""
    res = await admin_client.get("/api/v1/admin/providers")
    system_providers = [p for p in res.json() if p["is_system"]]
    if not system_providers:
        pytest.skip("No system providers seeded")
    sys_id = system_providers[0]["id"]

    res = await admin_client.delete(f"/api/v1/admin/providers/{sys_id}")
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "provider_system_readonly"


@pytest.mark.e2e
async def test_provider_name_conflict(admin_client: AsyncClient) -> None:
    """Duplicate provider name returns 409."""
    res = await admin_client.post("/api/v1/admin/providers", json={
        "name": "dup-provider",
        "base_url": "https://example.com/api",
        "auth_type": "none",
    })
    assert res.status_code == 201

    res2 = await admin_client.post("/api/v1/admin/providers", json={
        "name": "dup-provider",
        "base_url": "https://other.com/api",
        "auth_type": "none",
    })
    assert res2.status_code == 409
    assert res2.json()["detail"]["code"] == "provider_name_conflict"

    # Cleanup
    pid = res.json()["id"]
    await admin_client.delete(f"/api/v1/admin/providers/{pid}")


@pytest.mark.e2e
async def test_model_crud(admin_client: AsyncClient) -> None:
    """Admin can create, list, update, and delete models."""
    # Create a provider first
    res = await admin_client.post("/api/v1/admin/providers", json={
        "name": "model-test-provider",
        "base_url": "https://example.com/api",
        "auth_type": "api_key",
        "api_key": "sk-test",
    })
    assert res.status_code == 201
    pid = res.json()["id"]

    # Create model
    res = await admin_client.post(f"/api/v1/admin/providers/{pid}/models", json={
        "model_id": "test-model-1",
        "display_name": "Test Model",
        "reasoning": True,
        "input_modalities": ["text", "image"],
        "cost_input": 3.0,
        "cost_output": 15.0,
        "context_window": 200000,
        "max_tokens": 64000,
    })
    assert res.status_code == 201
    model_data = res.json()
    assert model_data["model_id"] == "test-model-1"
    assert model_data["is_system"] is False
    mid = model_data["id"]

    # Update model
    res = await admin_client.patch(
        f"/api/v1/admin/providers/{pid}/models/{mid}",
        json={"display_name": "Updated Model", "enabled": False},
    )
    assert res.status_code == 200
    assert res.json()["display_name"] == "Updated Model"

    # Delete model
    res = await admin_client.delete(f"/api/v1/admin/providers/{pid}/models/{mid}")
    assert res.status_code == 204

    # Cleanup provider
    await admin_client.delete(f"/api/v1/admin/providers/{pid}")


@pytest.mark.e2e
async def test_org_settings_default_model(admin_client: AsyncClient) -> None:
    """Admin can set and read org LLM settings."""
    # Read default
    res = await admin_client.get("/api/v1/admin/settings/llm")
    assert res.status_code == 200
    settings = res.json()

    # Set default model
    res = await admin_client.put("/api/v1/admin/settings/llm", json={
        "default_model": "cubeplex/test-model",
        "fallback_models": ["cubeplex/fallback-1"],
    })
    assert res.status_code == 200
    assert res.json()["default_model"] == "cubeplex/test-model"
    assert res.json()["fallback_models"] == ["cubeplex/fallback-1"]


@pytest.mark.e2e
async def test_config_fallback_when_db_empty(admin_client: AsyncClient) -> None:
    """LLMFactory.create_default() works from config.yaml when DB has no providers."""
    from cubeplex.llm.factory import LLMFactory

    # LLMFactory with no DB session must still work via config fallback
    factory = LLMFactory()
    llm = factory.create_default()
    assert llm is not None


@pytest.mark.e2e
async def test_system_provider_model_mutations_rejected(admin_client: AsyncClient) -> None:
    """Org admin cannot create/edit/delete models on system providers."""
    # Get a system provider
    res = await admin_client.get("/api/v1/admin/providers")
    system = [p for p in res.json() if p["is_system"]]
    if not system:
        pytest.skip("No system providers available")
    sys_id = system[0]["id"]

    # Try to create a model on system provider
    res = await admin_client.post(f"/api/v1/admin/providers/{sys_id}/models", json={
        "model_id": "hacker-model",
        "display_name": "Hack",
        "context_window": 128000,
        "max_tokens": 64000,
    })
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "provider_system_readonly"
```

- [ ] **Step 2: Setup admin_client fixture in conftest**

Add to `backend/tests/e2e/conftest.py` (check existing patterns for how auth is handled in E2E tests). The fixture should register/login an admin user and return an authenticated httpx AsyncClient:

```python
@pytest.fixture
async def admin_client() -> AsyncClient:
    # Follow existing E2E patterns for auth
    ...
```

(If existing E2E fixtures already handle admin auth, reuse them.)

- [ ] **Step 3: Run E2E tests**

```bash
uv run pytest tests/e2e/test_admin_providers_crud.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/e2e/test_admin_providers_crud.py
git commit -m "test(e2e): add admin provider/model CRUD E2E tests"
```

---

### Task 14: OAuth placeholder E2E

**Files:**
- Create: `backend/tests/e2e/test_provider_oauth_reject.py`

- [ ] **Step 1: Write E2E test**

```python
# backend/tests/e2e/test_provider_oauth_reject.py
"""OAuth placeholder — v1 must reject with 409."""

import pytest
from httpx import AsyncClient


@pytest.mark.e2e
async def test_oauth_auth_type_rejected(admin_client: AsyncClient) -> None:
    """Creating a provider with auth_type=oauth returns 409."""
    res = await admin_client.post("/api/v1/admin/providers", json={
        "name": "oauth-test",
        "base_url": "https://example.com/api",
        "auth_type": "oauth",
    })
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "provider_oauth_not_implemented"


@pytest.mark.e2e
async def test_test_connection_works(admin_client: AsyncClient) -> None:
    """Test connection endpoint returns a result (ok or error, not 500)."""
    res = await admin_client.post("/api/v1/admin/providers/test", json={
        "provider_type": "openai_compat",
        "base_url": "https://httpbin.org/post",
        "api_key": "test",
        "auth_type": "api_key",
    })
    assert res.status_code == 200
    data = res.json()
    assert "ok" in data
    assert "latency_ms" in data
```

- [ ] **Step 2: Run**

```bash
uv run pytest tests/e2e/test_provider_oauth_reject.py -v
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_provider_oauth_reject.py
git commit -m "test(e2e): add OAuth placeholder and test-connection E2E tests"
```

---

## Phase C · Frontend

### Task 15: ApiClient put() method

**Files:**
- Modify: `frontend/packages/core/src/api/client.ts`

- [ ] **Step 1: Add put method**

In `client.ts`, add to the `ApiClient` interface after `patch`:

```typescript
  put(path: string, body: unknown): Promise<Response>
```

In the `createApiClient` factory, add the `put` implementation after `patch`:

```typescript
    put(path, body) {
      return doFetch(path, {
        method: 'PUT',
        headers: buildHeaders('PUT', { 'Content-Type': 'application/json' }),
        body: JSON.stringify(body),
      })
    },
```

- [ ] **Step 2: Verify types**

```bash
cd /home/chris/cubeplex/.worktrees/feat/m2-model-management/frontend
pnpm --filter @cubeplex/core type-check
```

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/core/src/api/client.ts
git commit -m "feat(core): add put() method to ApiClient"
```

---

### Task 16: Frontend types

**Files:**
- Create: `frontend/packages/core/src/types/provider.ts`
- Modify: `frontend/packages/core/src/index.ts`

- [ ] **Step 1: Create provider types**

```typescript
// frontend/packages/core/src/types/provider.ts
export interface Provider {
  id: string
  name: string
  provider_type: string
  base_url: string
  auth_type: 'api_key' | 'oauth' | 'bearer_token' | 'none'
  has_api_key: boolean
  logo_url: string | null
  enabled: boolean
  is_system: boolean
  model_count: number
  models?: Model[]
  org_override?: { enabled: boolean }
  extra_body: Record<string, unknown>
  extra_headers: Record<string, unknown>
  created_by_user_id: string
  created_at: string
  updated_at: string
}

export interface Model {
  id: string
  provider_id: string
  model_id: string
  display_name: string
  reasoning: boolean
  input_modalities: string[]
  cost_input: number
  cost_output: number
  cost_cache_read: number
  cost_cache_write: number
  context_window: number
  max_tokens: number
  extra_body: Record<string, unknown>
  extra_headers: Record<string, unknown>
  enabled: boolean
  is_system: boolean
  created_at: string
  updated_at: string
}

export interface ProviderCreate {
  name: string
  provider_type?: string
  base_url: string
  auth_type?: string
  api_key?: string | null
  logo_url?: string | null
  extra_body?: Record<string, unknown>
  extra_headers?: Record<string, unknown>
}

export interface ProviderUpdate {
  name?: string | null
  provider_type?: string | null
  base_url?: string | null
  auth_type?: string | null
  api_key?: string | null
  logo_url?: string | null
  extra_body?: Record<string, unknown> | null
  extra_headers?: Record<string, unknown> | null
  enabled?: boolean | null
}

export interface ModelCreate {
  model_id: string
  display_name: string
  reasoning?: boolean
  input_modalities?: string[]
  cost_input?: number
  cost_output?: number
  cost_cache_read?: number
  cost_cache_write?: number
  context_window: number
  max_tokens: number
  extra_body?: Record<string, unknown>
  extra_headers?: Record<string, unknown>
}

export interface ModelUpdate {
  display_name?: string | null
  reasoning?: boolean | null
  input_modalities?: string[] | null
  cost_input?: number | null
  cost_output?: number | null
  cost_cache_read?: number | null
  cost_cache_write?: number | null
  context_window?: number | null
  max_tokens?: number | null
  extra_body?: Record<string, unknown> | null
  extra_headers?: Record<string, unknown> | null
  enabled?: boolean | null
}

export interface TestResult {
  ok: boolean
  error: string | null
  latency_ms: number
}

export interface OrgLLMSettings {
  default_model: string | null
  fallback_models: string[]
}

export interface OrgLLMSettingsUpdate {
  default_model?: string | null
  fallback_models?: string[] | null
}
```

- [ ] **Step 2: Export from index**

In `frontend/packages/core/src/index.ts`, add:

```typescript
export * from './types/provider'
```

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/core/src/types/provider.ts frontend/packages/core/src/index.ts
git commit -m "feat(core): add provider/model TypeScript types"
```

---

### Task 17: API functions

**Files:**
- Create: `frontend/packages/core/src/api/providers.ts`
- Modify: `frontend/packages/core/src/index.ts`

- [ ] **Step 1: Create API functions**

```typescript
// frontend/packages/core/src/api/providers.ts
import type { ApiClient } from './client'
import { toApiError } from './client'
import type {
  Provider,
  ProviderCreate,
  ProviderUpdate,
  Model,
  ModelCreate,
  ModelUpdate,
  TestResult,
  OrgLLMSettings,
  OrgLLMSettingsUpdate,
} from '../types/provider'

export async function fetchProviders(client: ApiClient): Promise<Provider[]> {
  const res = await client.get('/api/v1/admin/providers')
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<Provider[]>
}

export async function fetchProvider(client: ApiClient, id: string): Promise<Provider> {
  const res = await client.get(`/api/v1/admin/providers/${id}`)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<Provider>
}

// ... etc, all functions use: import { toApiError } from './client'
// and: if (!res.ok) throw await toApiError(res)

export async function fetchProvider(client: ApiClient, id: string): Promise<Provider> {
  const res = await client.get(`/api/v1/admin/providers/${id}`)
  if (!res.ok) throw await (await import('./client')).toApiError(res)
  return res.json() as Promise<Provider>
}

export async function createProvider(
  client: ApiClient,
  body: ProviderCreate
): Promise<Provider> {
  const res = await client.post('/api/v1/admin/providers', body)
  if (!res.ok) throw await (await import('./client')).toApiError(res)
  return res.json() as Promise<Provider>
}

export async function updateProvider(
  client: ApiClient,
  id: string,
  body: ProviderUpdate
): Promise<Provider> {
  const res = await client.patch(`/api/v1/admin/providers/${id}`, body)
  if (!res.ok) throw await (await import('./client')).toApiError(res)
  return res.json() as Promise<Provider>
}

export async function deleteProvider(client: ApiClient, id: string): Promise<void> {
  const res = await client.del(`/api/v1/admin/providers/${id}`)
  if (!res.ok) throw await (await import('./client')).toApiError(res)
}

export async function testConnection(
  client: ApiClient,
  body: {
    provider_type: string
    base_url: string
    api_key?: string | null
    auth_type: string
  }
): Promise<TestResult> {
  const res = await client.post('/api/v1/admin/providers/test', body)
  if (!res.ok) throw await (await import('./client')).toApiError(res)
  return res.json() as Promise<TestResult>
}

export async function createModel(
  client: ApiClient,
  providerId: string,
  body: ModelCreate
): Promise<Model> {
  const res = await client.post(`/api/v1/admin/providers/${providerId}/models`, body)
  if (!res.ok) throw await (await import('./client')).toApiError(res)
  return res.json() as Promise<Model>
}

export async function updateModel(
  client: ApiClient,
  providerId: string,
  modelId: string,
  body: ModelUpdate
): Promise<Model> {
  const res = await client.patch(
    `/api/v1/admin/providers/${providerId}/models/${modelId}`,
    body
  )
  if (!res.ok) throw await (await import('./client')).toApiError(res)
  return res.json() as Promise<Model>
}

export async function deleteModel(
  client: ApiClient,
  providerId: string,
  modelId: string
): Promise<void> {
  const res = await client.del(`/api/v1/admin/providers/${providerId}/models/${modelId}`)
  if (!res.ok) throw await (await import('./client')).toApiError(res)
}

export async function fetchOrgLLMSettings(
  client: ApiClient
): Promise<OrgLLMSettings> {
  const res = await client.get('/api/v1/admin/settings/llm')
  if (!res.ok) throw await (await import('./client')).toApiError(res)
  return res.json() as Promise<OrgLLMSettings>
}

export async function updateOrgLLMSettings(
  client: ApiClient,
  body: OrgLLMSettingsUpdate
): Promise<OrgLLMSettings> {
  const res = await client.put('/api/v1/admin/settings/llm', body)
  if (!res.ok) throw await (await import('./client')).toApiError(res)
  return res.json() as Promise<OrgLLMSettings>
}

export async function setProviderOverride(
  client: ApiClient,
  providerId: string,
  enabled: boolean
): Promise<{ enabled: boolean }> {
  const res = await client.patch(
    `/api/v1/admin/providers/${providerId}/override`,
    { enabled }
  )
  if (!res.ok) throw await (await import('./client')).toApiError(res)
  return res.json() as Promise<{ enabled: boolean }>
}
```

- [ ] **Step 2: Export from index**

In `packages/core/src/index.ts`, add:

```typescript
export * from './api/providers'
```

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/core/src/api/providers.ts frontend/packages/core/src/index.ts
git commit -m "feat(core): add provider/model API functions"
```

---

### Task 18: Stores

**Files:**
- Create: `frontend/packages/core/src/stores/providersStore.ts`
- Create: `frontend/packages/core/src/stores/modelsStore.ts`
- Create: `frontend/packages/core/src/stores/orgModelSettingsStore.ts`
- Modify: `frontend/packages/core/src/index.ts`

- [ ] **Step 1: providersStore**

```typescript
// frontend/packages/core/src/stores/providersStore.ts
import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import {
  fetchProviders,
  createProvider,
  updateProvider,
  deleteProvider,
  testConnection,
  setProviderOverride,
} from '../api/providers'
import type { Provider, ProviderCreate, ProviderUpdate, TestResult } from '../types/provider'

interface ProvidersState {
  providers: Provider[]
  selectedId: string | null
  loading: boolean
  error: string | null
  fetchProviders: (client: ApiClient) => Promise<void>
  selectProvider: (id: string | null) => void
  createProvider: (client: ApiClient, body: ProviderCreate) => Promise<Provider>
  updateProvider: (client: ApiClient, id: string, body: ProviderUpdate) => Promise<void>
  deleteProvider: (client: ApiClient, id: string) => Promise<void>
  testConnection: (
    client: ApiClient,
    body: { provider_type: string; base_url: string; api_key?: string | null; auth_type: string }
  ) => Promise<TestResult>
  toggleOverride: (client: ApiClient, providerId: string, enabled: boolean) => Promise<void>
}

export const useProvidersStore = create<ProvidersState>((set, _get) => ({
  providers: [],
  selectedId: null,
  loading: false,
  error: null,

  fetchProviders: async (client) => {
    set({ loading: true, error: null })
    try {
      const providers = await fetchProviders(client)
      set({ providers, loading: false })
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
    }
  },

  selectProvider: (id) => set({ selectedId: id }),

  createProvider: async (client, body) => {
    const provider = await createProvider(client, body)
    set((s) => ({ providers: [...s.providers, provider] }))
    return provider
  },

  updateProvider: async (client, id, body) => {
    const updated = await updateProvider(client, id, body)
    set((s) => ({
      providers: s.providers.map((p) => (p.id === id ? updated : p)),
    }))
  },

  deleteProvider: async (client, id) => {
    await deleteProvider(client, id)
    set((s) => ({
      providers: s.providers.filter((p) => p.id !== id),
      selectedId: s.selectedId === id ? null : s.selectedId,
    }))
  },

  testConnection: async (client, body) => {
    return testConnection(client, body)
  },

  toggleOverride: async (client, providerId, enabled) => {
    await setProviderOverride(client, providerId, enabled)
    set((s) => ({
      providers: s.providers.map((p) =>
        p.id === providerId
          ? { ...p, org_override: { enabled } }
          : p
      ),
    }))
  },
}))
```

- [ ] **Step 2: modelsStore**

```typescript
// frontend/packages/core/src/stores/modelsStore.ts
import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import {
  fetchProvider,
  createModel,
  updateModel,
  deleteModel,
} from '../api/providers'
import type { Model, ModelCreate, ModelUpdate } from '../types/provider'

interface ModelsState {
  models: Model[]
  loading: boolean
  error: string | null
  fetchModels: (client: ApiClient, providerId: string) => Promise<void>
  createModel: (client: ApiClient, providerId: string, body: ModelCreate) => Promise<Model>
  updateModel: (
    client: ApiClient,
    providerId: string,
    modelId: string,
    body: ModelUpdate
  ) => Promise<void>
  deleteModel: (client: ApiClient, providerId: string, modelId: string) => Promise<void>
}

export const useModelsStore = create<ModelsState>((set) => ({
  models: [],
  loading: false,
  error: null,

  fetchModels: async (client, providerId) => {
    set({ loading: true, error: null })
    try {
      const provider = await fetchProvider(client, providerId)
      set({ models: provider.models || [], loading: false })
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
    }
  },

  createModel: async (client, providerId, body) => {
    const model = await createModel(client, providerId, body)
    set((s) => ({ models: [...s.models, model] }))
    return model
  },

  updateModel: async (client, providerId, modelId, body) => {
    const updated = await updateModel(client, providerId, modelId, body)
    set((s) => ({
      models: s.models.map((m) => (m.id === modelId ? updated : m)),
    }))
  },

  deleteModel: async (client, providerId, modelId) => {
    await deleteModel(client, providerId, modelId)
    set((s) => ({ models: s.models.filter((m) => m.id !== modelId) }))
  },
}))
```

- [ ] **Step 3: orgModelSettingsStore**

```typescript
// frontend/packages/core/src/stores/orgModelSettingsStore.ts
import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import { fetchOrgLLMSettings, updateOrgLLMSettings } from '../api/providers'
import type { OrgLLMSettings, OrgLLMSettingsUpdate } from '../types/provider'

interface OrgModelSettingsState {
  settings: OrgLLMSettings | null
  loading: boolean
  error: string | null
  fetchSettings: (client: ApiClient) => Promise<void>
  updateSettings: (client: ApiClient, body: OrgLLMSettingsUpdate) => Promise<void>
}

export const useOrgModelSettingsStore = create<OrgModelSettingsState>((set) => ({
  settings: null,
  loading: false,
  error: null,

  fetchSettings: async (client) => {
    set({ loading: true, error: null })
    try {
      const settings = await fetchOrgLLMSettings(client)
      set({ settings, loading: false })
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
    }
  },

  updateSettings: async (client, body) => {
    const settings = await updateOrgLLMSettings(client, body)
    set({ settings })
  },
}))
```

- [ ] **Step 4: Export from index**

```typescript
export { useProvidersStore } from './stores/providersStore'
export { useModelsStore } from './stores/modelsStore'
export { useOrgModelSettingsStore } from './stores/orgModelSettingsStore'
```

- [ ] **Step 5: Type check + build**

```bash
cd /home/chris/cubeplex/.worktrees/feat/m2-model-management/frontend
pnpm --filter @cubeplex/core type-check
pnpm --filter @cubeplex/core build
```

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/core/src/stores/providersStore.ts \
        frontend/packages/core/src/stores/modelsStore.ts \
        frontend/packages/core/src/stores/orgModelSettingsStore.ts \
        frontend/packages/core/src/index.ts
git commit -m "feat(core): add provider/model Zustand stores"
```

---

### Task 19: Frontend components

**Files:**
- Create: `frontend/packages/web/components/admin/models/ProviderLogo.tsx`
- Create: `frontend/packages/web/components/admin/models/ProviderFormDialog.tsx`
- Create: `frontend/packages/web/components/admin/models/ModelFormDialog.tsx`
- Create: `frontend/packages/web/components/admin/models/ModelRow.tsx`
- Create: `frontend/packages/web/components/admin/models/TestConnectionResult.tsx`
- Create: `frontend/packages/web/components/admin/models/OrgModelSettings.tsx`
- Create: `frontend/packages/web/components/admin/models/ProviderList.tsx`
- Create: `frontend/packages/web/components/admin/models/ProviderDetail.tsx`
- Modify: `frontend/packages/web/app/admin/models/page.tsx`

This is a large task. Break into sub-steps:

- [ ] **Step 1: ProviderLogo component**

```tsx
// frontend/packages/web/components/admin/models/ProviderLogo.tsx
import { cn } from '@/lib/utils'

const COLORS = [
  'bg-blue-100 text-blue-700',
  'bg-green-100 text-green-700',
  'bg-purple-100 text-purple-700',
  'bg-amber-100 text-amber-700',
  'bg-rose-100 text-rose-700',
  'bg-cyan-100 text-cyan-700',
]

function hashColor(name: string): string {
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) | 0
  return COLORS[Math.abs(h) % COLORS.length]
}

export function ProviderLogo({
  logoUrl,
  name,
  size = 24,
}: {
  logoUrl?: string | null
  name: string
  size?: number
}) {
  if (logoUrl) {
    return (
      <img
        src={logoUrl}
        alt={name}
        className="rounded-md object-cover"
        style={{ width: size, height: size }}
        onError={(e) => {
          (e.target as HTMLImageElement).style.display = 'none'
          const fallback = (e.target as HTMLImageElement).nextElementSibling
          if (fallback) (fallback as HTMLElement).style.display = 'flex'
        }}
      />
    )
  }

  const initial = name.charAt(0).toUpperCase()
  return (
    <div
      className={cn(
        'flex items-center justify-center rounded-full font-semibold',
        hashColor(name)
      )}
      style={{ width: size, height: size, fontSize: size * 0.5 }}
    >
      {initial}
    </div>
  )
}
```

- [ ] **Step 2: ProviderFormDialog**

```tsx
// frontend/packages/web/components/admin/models/ProviderFormDialog.tsx
'use client'

import { useState } from 'react'
import { Check, Globe, Key, Loader2, Plug, Shield } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion'
import type { ProviderCreate, ProviderUpdate, Provider } from '@cubeplex/core'
import { TestConnectionResult } from './TestConnectionResult'

type AuthType = 'api_key' | 'oauth' | 'bearer_token' | 'none'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSave: (data: ProviderCreate) => Promise<void>
  onTestConnection: (data: {
    provider_type: string
    base_url: string
    api_key?: string | null
    auth_type: string
  }) => Promise<{ ok: boolean; error: string | null; latency_ms: number }>
  provider?: Provider | null
}

export function ProviderFormDialog({
  open,
  onOpenChange,
  onSave,
  onTestConnection,
  provider,
}: Props) {
  const isEdit = !!provider
  const [name, setName] = useState(provider?.name || '')
  const [providerType, setProviderType] = useState(provider?.provider_type || 'openai_compat')
  const [baseUrl, setBaseUrl] = useState(provider?.base_url || '')
  const [authType, setAuthType] = useState<AuthType>(
    (provider?.auth_type as AuthType) || 'api_key'
  )
  const [apiKey, setApiKey] = useState('')
  const [logoUrl, setLogoUrl] = useState(provider?.logo_url || '')
  const [saving, setSaving] = useState(false)
  const [testResult, setTestResult] = useState<{
    ok: boolean
    error: string | null
    latency_ms: number
  } | null>(null)
  const [testing, setTesting] = useState(false)

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const result = await onTestConnection({
        provider_type: providerType,
        base_url: baseUrl,
        api_key: apiKey || null,
        auth_type: authType,
      })
      setTestResult(result)
    } finally {
      setTesting(false)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await onSave({
        name,
        provider_type: providerType,
        base_url: baseUrl,
        auth_type: authType,
        api_key: apiKey || null,
        logo_url: logoUrl || null,
      })
      onOpenChange(false)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? '编辑 Provider' : '添加 Provider'}</DialogTitle>
          <DialogDescription>
            配置 LLM 提供商连接信息
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label htmlFor="p-name">名称 *</Label>
            <Input
              id="p-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如 Anthropic, OpenAI"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="p-type">Provider 类型</Label>
            <Select value={providerType} onValueChange={setProviderType}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="openai_compat">OpenAI Compatible</SelectItem>
                <SelectItem value="anthropic">Anthropic</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="p-url">Base URL *</Label>
            <Input
              id="p-url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.example.com/v1"
            />
          </div>

          <div className="space-y-2">
            <Label>认证方式</Label>
            <RadioGroup
              value={authType}
              onValueChange={(v) => setAuthType(v as AuthType)}
              className="grid grid-cols-2 gap-3"
            >
              {([
                { value: 'api_key', label: 'API Key', icon: Key, disabled: false },
                { value: 'bearer_token', label: 'Bearer Token', icon: Shield, disabled: false },
                { value: 'none', label: '无认证', icon: Plug, disabled: false },
                { value: 'oauth', label: 'OAuth', icon: Globe, disabled: true },
              ] as const).map((item) => (
                <label
                  key={item.value}
                  className={`flex items-center gap-3 rounded-lg border p-3 cursor-pointer hover:bg-muted/50 ${
                    item.disabled ? 'opacity-50 cursor-not-allowed' : ''
                  } ${authType === item.value ? 'border-primary ring-2 ring-primary/20' : ''}`}
                >
                  <RadioGroupItem
                    value={item.value}
                    disabled={item.disabled}
                    id={`auth-${item.value}`}
                  />
                  <item.icon className="h-4 w-4 shrink-0" />
                  <div className="text-sm font-medium">
                    {item.label}
                    {item.disabled && (
                      <span className="block text-xs text-muted-foreground">即将推出</span>
                    )}
                  </div>
                </label>
              ))}
            </RadioGroup>
          </div>

          {(authType === 'api_key' || authType === 'bearer_token') && (
            <div className="space-y-2">
              <Label htmlFor="p-key">
                {authType === 'api_key' ? 'API Key' : 'Token'}
                {!isEdit && ' *'}
              </Label>
              <Input
                id="p-key"
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={isEdit ? '输入以替换（留空保持不变）' : 'sk-...'}
              />
            </div>
          )}

          <Accordion type="single" collapsible>
            <AccordionItem value="advanced">
              <AccordionTrigger className="text-sm">高级设置</AccordionTrigger>
              <AccordionContent className="space-y-3">
                <div className="space-y-2">
                  <Label htmlFor="p-logo">Logo URL</Label>
                  <Input
                    id="p-logo"
                    value={logoUrl}
                    onChange={(e) => setLogoUrl(e.target.value)}
                    placeholder="https://example.com/logo.png"
                  />
                </div>
              </AccordionContent>
            </AccordionItem>
          </Accordion>

          {testResult && <TestConnectionResult result={testResult} />}
        </div>

        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={handleTest} disabled={testing || !baseUrl}>
            {testing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            测试连接
          </Button>
          <Button onClick={handleSave} disabled={saving || !name || !baseUrl}>
            {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {saving ? '保存中...' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
```

- [ ] **Step 3: ModelFormDialog**

```tsx
// frontend/packages/web/components/admin/models/ModelFormDialog.tsx
'use client'

import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion'
import type { ModelCreate, Model } from '@cubeplex/core'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSave: (data: ModelCreate) => Promise<void>
  model?: Model | null
}

export function ModelFormDialog({ open, onOpenChange, onSave, model }: Props) {
  const isEdit = !!model
  const [modelId, setModelId] = useState(model?.model_id || '')
  const [displayName, setDisplayName] = useState(model?.display_name || '')
  const [reasoning, setReasoning] = useState(model?.reasoning || false)
  const [modalities, setModalities] = useState<string[]>(model?.input_modalities || ['text'])
  const [costInput, setCostInput] = useState(String(model?.cost_input || 0))
  const [costOutput, setCostOutput] = useState(String(model?.cost_output || 0))
  const [costCacheRead, setCostCacheRead] = useState(String(model?.cost_cache_read || 0))
  const [costCacheWrite, setCostCacheWrite] = useState(String(model?.cost_cache_write || 0))
  const [contextWindow, setContextWindow] = useState(String(model?.context_window || 128000))
  const [maxTokens, setMaxTokens] = useState(String(model?.max_tokens || 64000))
  const [saving, setSaving] = useState(false)

  const toggleModality = (m: string) => {
    setModalities((prev) =>
      prev.includes(m) ? prev.filter((x) => x !== m) : [...prev, m]
    )
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await onSave({
        model_id: modelId,
        display_name: displayName,
        reasoning,
        input_modalities: modalities,
        cost_input: parseFloat(costInput) || 0,
        cost_output: parseFloat(costOutput) || 0,
        cost_cache_read: parseFloat(costCacheRead) || 0,
        cost_cache_write: parseFloat(costCacheWrite) || 0,
        context_window: parseInt(contextWindow) || 128000,
        max_tokens: parseInt(maxTokens) || 64000,
      })
      onOpenChange(false)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? '编辑模型' : '添加模型'}</DialogTitle>
          <DialogDescription>配置 Provider 下的模型</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label htmlFor="m-id">Model ID *</Label>
            <Input
              id="m-id"
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              placeholder="claude-sonnet-4-6"
              disabled={isEdit}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="m-name">显示名称 *</Label>
            <Input
              id="m-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Sonnet 4.6"
            />
          </div>

          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <Switch
                id="m-reasoning"
                checked={reasoning}
                onCheckedChange={setReasoning}
              />
              <Label htmlFor="m-reasoning">推理模型</Label>
            </div>
          </div>

          <div className="space-y-2">
            <Label>输入模态</Label>
            <div className="flex gap-4">
              {['text', 'image'].map((m) => (
                <label key={m} className="flex items-center gap-2">
                  <Checkbox
                    checked={modalities.includes(m)}
                    onCheckedChange={() => toggleModality(m)}
                  />
                  <span className="text-sm">{m}</span>
                </label>
              ))}
            </div>
          </div>

          <Accordion type="single" collapsible>
            <AccordionItem value="cost">
              <AccordionTrigger className="text-sm">成本 (per 1M tokens, USD)</AccordionTrigger>
              <AccordionContent className="space-y-2">
                <div className="grid grid-cols-2 gap-3">
                  {([
                    ['costInput', 'Input', costInput, setCostInput],
                    ['costOutput', 'Output', costOutput, setCostOutput],
                    ['costCacheRead', 'Cache Read', costCacheRead, setCostCacheRead],
                    ['costCacheWrite', 'Cache Write', costCacheWrite, setCostCacheWrite],
                  ] as const).map(([id, label, val, set]) => (
                    <div key={id} className="space-y-1">
                      <Label htmlFor={id}>{label}</Label>
                      <Input
                        id={id}
                        type="number"
                        step="0.01"
                        value={val}
                        onChange={(e) => set(e.target.value)}
                      />
                    </div>
                  ))}
                </div>
              </AccordionContent>
            </AccordionItem>
            <AccordionItem value="limits">
              <AccordionTrigger className="text-sm">上下文限制</AccordionTrigger>
              <AccordionContent className="space-y-2">
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <Label htmlFor="m-ctx">Context Window</Label>
                    <Input
                      id="m-ctx"
                      type="number"
                      value={contextWindow}
                      onChange={(e) => setContextWindow(e.target.value)}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="m-maxtok">Max Tokens</Label>
                    <Input
                      id="m-maxtok"
                      type="number"
                      value={maxTokens}
                      onChange={(e) => setMaxTokens(e.target.value)}
                    />
                  </div>
                </div>
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </div>

        <DialogFooter>
          <Button onClick={handleSave} disabled={saving || !modelId || !displayName}>
            {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
```

- [ ] **Step 4: Remaining components**

These components need to follow the approved design but are straightforward React components based on existing shadcn/ui patterns. The implementation should reference the skills page (`components/admin/skills/`) for layout patterns.

Key components to implement:
- `ModelRow.tsx` — single model row with model_id, display_name, cost display, reasoning/input badges, edit/delete actions
- `TestConnectionResult.tsx` — green success card (latency_ms) or red error card
- `OrgModelSettings.tsx` — combobox for default model + tag list for fallback chain
- `ProviderList.tsx` — left sidebar list with logo, name, model count, badges, add button
- `ProviderDetail.tsx` — right panel with provider header, model table, org settings section

- [ ] **Step 5: Replace page.tsx**

```tsx
// frontend/packages/web/app/admin/models/page.tsx
'use client'

import { useEffect, useMemo } from 'react'
import { Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  useProvidersStore,
  useModelsStore,
  useOrgModelSettingsStore,
  createApiClient,
} from '@cubeplex/core'
import { ProviderList } from '@/components/admin/models/ProviderList'
import { ProviderDetail } from '@/components/admin/models/ProviderDetail'

export default function ModelsPage() {
  const client = useMemo(() => createApiClient(''), [])
  const {
    providers,
    selectedId,
    loading,
    fetchProviders,
    createProvider,
    updateProvider,
    deleteProvider,
    testConnection,
    selectProvider,
    toggleOverride,
  } = useProvidersStore()

  const { models, fetchModels, createModel, updateModel, deleteModel } = useModelsStore()
  const { settings, fetchSettings, updateSettings } = useOrgModelSettingsStore()

  useEffect(() => {
    fetchProviders(client)
    fetchSettings(client)
  }, [client]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (selectedId) {
      fetchModels(client, selectedId)
    }
  }, [selectedId, client]) // eslint-disable-line react-hooks/exhaustive-deps

  const selectedProvider = providers.find((p) => p.id === selectedId) || null

  return (
    <div className="flex h-full">
      <div className="w-72 border-r shrink-0 flex flex-col">
        <div className="flex items-center justify-between p-4 border-b">
          <h2 className="font-semibold text-sm">Providers</h2>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              /* open create dialog — handled by ProviderList */
            }}
          >
            <Plus className="h-4 w-4" />
          </Button>
        </div>
        <ProviderList
          providers={providers}
          selectedId={selectedId}
          loading={loading}
          onSelect={selectProvider}
          onCreateProvider={createProvider}
          onTestConnection={testConnection}
          client={client}
        />
      </div>
      <div className="flex-1 overflow-y-auto">
        {selectedProvider ? (
          <ProviderDetail
            provider={selectedProvider}
            models={models}
            settings={settings}
            client={client}
            onUpdateProvider={updateProvider}
            onDeleteProvider={deleteProvider}
            onTestConnection={testConnection}
            onToggleOverride={toggleOverride}
            onCreateModel={createModel}
            onUpdateModel={updateModel}
            onDeleteModel={deleteModel}
            onUpdateSettings={updateSettings}
          />
        ) : (
          <div className="flex items-center justify-center h-full text-muted-foreground">
            选择一个 provider 查看详情
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 6: Build and verify type check**

```bash
cd /home/chris/cubeplex/.worktrees/feat/m2-model-management/frontend
pnpm type-check
```

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/web/components/admin/models/ \
        frontend/packages/web/app/admin/models/page.tsx
git commit -m "feat(frontend): replace ComingSoonCard with full model management UI"
```

---

### Task 20: Playwright E2E test

**Files:**
- Create: `frontend/packages/web/__tests__/e2e/m2-models.spec.ts`

- [ ] **Step 1: Write Playwright test**

```typescript
// frontend/packages/web/__tests__/e2e/m2-models.spec.ts
import { test, expect } from '@playwright/test'

test.describe('M2 Model Management', () => {
  test('admin can create provider and model', async ({ page }) => {
    // Navigate to admin models page
    await page.goto('/admin/models')

    // Should see provider list (at least system providers)
    await expect(page.getByText('Providers')).toBeVisible()

    // Click add provider button
    await page.getByRole('button', { name: /添加/i }).first().click()

    // Fill provider form
    await page.getByLabel('名称 *').fill('e2e-test-provider')
    await page.getByLabel('Base URL *').fill('https://httpbin.org/post')

    // Select auth_type = none for simplicity
    await page.getByText('无认证').click()

    // Save
    await page.getByRole('button', { name: '保存' }).click()

    // Provider should appear in list
    await expect(page.getByText('e2e-test-provider')).toBeVisible()

    // Click the provider to see detail
    await page.getByText('e2e-test-provider').click()

    // Add a model
    await page.getByRole('button', { name: /添加模型/ }).click()
    await page.getByLabel('Model ID *').fill('e2e-test-model')
    await page.getByLabel('显示名称 *').fill('E2E Test Model')
    await page.getByRole('button', { name: '保存' }).last().click()

    // Model should appear
    await expect(page.getByText('e2e-test-model')).toBeVisible()
  })

  test('test connection button works', async ({ page }) => {
    await page.goto('/admin/models')

    // Click add provider
    await page.getByRole('button', { name: /添加/i }).first().click()

    // Fill minimal form
    await page.getByLabel('名称 *').fill('test-conn')
    await page.getByLabel('Base URL *').fill('https://httpbin.org/post')
    await page.getByText('无认证').click()

    // Click test connection
    await page.getByRole('button', { name: '测试连接' }).click()

    // Should show a result (ok or error)
    await expect(
      page.locator('[data-testid="test-result"]')
    ).toBeVisible({ timeout: 20000 })
  })
})
```

- [ ] **Step 2: Run E2E**

```bash
cd /home/chris/cubeplex/.worktrees/feat/m2-model-management/frontend
pnpm test:e2e -- m2-models.spec.ts
```

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/__tests__/e2e/m2-models.spec.ts
git commit -m "test(e2e): add Playwright E2E for model management UI"
```

---

## Self-Review Checklist (for executor)

Before marking the plan complete, verify:

1. `uv run pytest tests/unit/ -v` — unit tests pass
2. `uv run pytest tests/e2e/test_admin_providers_crud.py tests/e2e/test_provider_oauth_reject.py -v` — backend E2E pass
3. `uv run mypy cubeplex/` — type check passes
4. `pnpm type-check` — frontend type check passes
5. `pnpm test:e2e -- m2-models.spec.ts` — Playwright E2E passes
6. `alembic upgrade head` — migration applied cleanly
7. System providers seeded from config.yaml into DB on startup
8. Creating a provider with `auth_type=oauth` returns 409
9. System providers cannot be deleted
