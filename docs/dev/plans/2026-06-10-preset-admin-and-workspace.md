# Preset Admin + Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship the admin and workspace UX for the LLM Preset system landed in PR #215 (Spec 1). Adds admin CRUD for `OrgSettings.model_presets`, a workspace endpoint that exposes available presets, a chat-composer preset picker + thinking control, frontend rendering of `model_failover` SSE events, and resolves Fix-6 (subagent failover attribution).

**Architecture:** Backend adds two route modules (`admin_model_presets.py`, `model_presets.py` under `/ws/{ws}`). Admin writes the full `OrgSettings.model_presets` row in one PUT. Frontend gains an admin page (`/admin/presets`) and a composer dropdown + thinking slider on the chat input. Subagent failover attribution is fixed by giving subagents their own `FallbackBoundModel` copy with `on_failover=None` — keeping main-agent SSE attribution clean.

**Tech Stack:** FastAPI + pydantic (backend), Next.js 14 App Router + React Server Components + shadcn (frontend), TypeScript strict, Playwright E2E.

**Worktree:** `/home/chris/cubebox/.worktrees/feat/preset-admin-and-workspace` — slot 17, port 8017/3017, DB `cubebox_feat_preset_admin_and_workspace`.

**PR strategy:** Single PR. Backend + frontend changes are coupled (frontend depends on backend endpoints). Subagent-driven execution; `/code-review` after plan, after major milestones, and on the final PR until clean.

---

## Decisions made (defaults — challenge during plan review if any are wrong)

| # | Decision | Alternative |
|---|----------|-------------|
| D1 | Admin PUT replaces the entire `OrgSettings.model_presets` row (not granular field-PATCH) | per-preset CRUD endpoints |
| D2 | Admin sees the system row when no org row exists; first save creates org row | only show org row, blank by default |
| D3 | Workspace endpoint returns just labels + `is_default` (no chain refs) — chains are admin-only | expose chains so frontend can show "fall back to X" badges |
| D4 | Per-message preset persistence (already in Spec 1 API). Composer remembers last choice in localStorage, not server-side. | per-conversation persistence in DB |
| D5 | Thinking control: dropdown next to preset picker. Same value re-used until user changes it. | inline slider in message draft, separate per-message |
| D6 | Delete-model guard: model delete endpoint scans org rows for refs; on conflict returns `409` with list of preset labels referencing the model. Admin must edit presets first. | cascade-clear refs; or RESTRICT at DB level |
| D7 | `model_failover` rendering: inline gray banner in the message stream where the event arrived, expandable to show failed_ref/next_ref/reason. | toast/notification only |
| D8 | Subagent failover attribution (resolves Fix-6): subagent middleware receives a copy of the chain model with `on_failover=None`. Subagent failovers still occur transparently but emit no SSE event. Main-agent failovers remain visible. | per-subagent agent_id baked into the closure |
| D9 | Admin frontend uses existing `/admin/models` patterns (table-based, shadcn) | freeform editor |
| D10 | `thinking` default in composer: `off`. UI shows it as "Standard" with a tooltip; advanced users open the dropdown. | always show explicit value |

---

## File structure

### New files

**Backend:**
- `backend/cubebox/api/routes/v1/admin_model_presets.py` — admin CRUD endpoints
- `backend/cubebox/api/routes/v1/model_presets.py` — workspace listing endpoint
- `backend/cubebox/api/schemas/model_presets.py` — request/response Pydantic
- `backend/cubebox/services/model_presets.py` — service layer (read/write OrgSettings, ref-existence validation)
- `backend/tests/unit/api/test_admin_model_presets_schemas.py`
- `backend/tests/e2e/test_admin_model_presets_e2e.py`
- `backend/tests/e2e/test_workspace_model_presets_e2e.py`

**Frontend:**
- `frontend/packages/web/src/app/admin/presets/page.tsx` — server component shell
- `frontend/packages/web/src/app/admin/presets/PresetEditor.tsx` — client component
- `frontend/packages/web/src/app/admin/presets/__tests__/page.test.tsx`
- `frontend/packages/web/src/components/chat/PresetPicker.tsx`
- `frontend/packages/web/src/components/chat/ThinkingControl.tsx`
- `frontend/packages/web/src/components/chat/FailoverBanner.tsx`
- `frontend/packages/web/src/lib/api/presets.ts` — fetch helpers
- `frontend/packages/web/src/lib/stores/preset-selection.ts` — Zustand store for chosen preset+thinking
- `frontend/packages/web/playwright/tests/admin-presets.spec.ts`
- `frontend/packages/web/playwright/tests/chat-preset-picker.spec.ts`

### Modified files

**Backend:**
- `backend/cubebox/api/routes/v1/admin.py` — register `admin_model_presets.router`
- `backend/cubebox/api/routes/v1/__init__.py` — register `model_presets.router` under workspace scope
- `backend/cubebox/services/provider_service.py` — delete-model guard checks preset refs
- `backend/cubebox/streams/run_manager.py` — Fix-6: subagent gets `replace(this_run_model, on_failover=None)`

**Frontend:**
- `frontend/packages/web/src/app/admin/layout.tsx` — add "Model Presets" to sidebar
- `frontend/packages/web/src/components/chat/MessageComposer.tsx` — embed `PresetPicker` + `ThinkingControl`, send `preset_label` + `thinking` in body
- `frontend/packages/web/src/components/chat/MessageStream.tsx` — render `model_failover` events via `FailoverBanner`
- `frontend/packages/web/src/lib/api/conversations.ts` — extend message-send request type

---

## Backend tasks

### Task B1: schema module

**Files:**
- Create: `backend/cubebox/api/schemas/model_presets.py`
- Test: `backend/tests/unit/api/test_admin_model_presets_schemas.py`

- [ ] **Step 1: Write failing schema tests**

```python
"""Admin/workspace API schemas for model presets."""

import pytest
from pydantic import ValidationError

from cubebox.api.schemas.model_presets import (
    AdminModelPresetsBody,
    AdminPresetEntry,
    WorkspacePresetSummary,
)


def test_admin_body_minimal_valid():
    body = AdminModelPresetsBody.model_validate({
        "presets": [{"label": "default", "chain": ["acme/m1"], "is_default": True}],
        "task_presets": {},
    })
    assert body.presets[0].label == "default"


def test_admin_body_rejects_duplicate_labels():
    with pytest.raises(ValidationError, match="label"):
        AdminModelPresetsBody.model_validate({
            "presets": [
                {"label": "x", "chain": ["a/b"], "is_default": True},
                {"label": "x", "chain": ["a/c"], "is_default": False},
            ],
            "task_presets": {},
        })


def test_admin_body_requires_one_default():
    with pytest.raises(ValidationError, match="default"):
        AdminModelPresetsBody.model_validate({
            "presets": [{"label": "x", "chain": ["a/b"], "is_default": False}],
            "task_presets": {},
        })


def test_admin_body_rejects_unknown_task_key():
    with pytest.raises(ValidationError, match="task"):
        AdminModelPresetsBody.model_validate({
            "presets": [{"label": "x", "chain": ["a/b"], "is_default": True}],
            "task_presets": {"unknown": "x"},
        })


def test_admin_body_rejects_task_value_not_in_labels():
    with pytest.raises(ValidationError, match="task_presets"):
        AdminModelPresetsBody.model_validate({
            "presets": [{"label": "x", "chain": ["a/b"], "is_default": True}],
            "task_presets": {"title": "ghost"},
        })


def test_workspace_summary_excludes_chain():
    summary = WorkspacePresetSummary(label="default", is_default=True)
    dumped = summary.model_dump()
    assert "chain" not in dumped
```

- [ ] **Step 2: Run, expect ImportError**

```bash
cd backend && uv run pytest tests/unit/api/test_admin_model_presets_schemas.py -v
```

- [ ] **Step 3: Create `cubebox/api/schemas/model_presets.py`**

Reuse `cubebox/llm/snapshot_schema.py`'s `ModelPresetsValue` validation — the admin body is structurally identical. Wrap it:

```python
"""API schemas for model preset admin + workspace endpoints.

The admin body is structurally identical to the on-disk
OrgSettings.model_presets value, so we re-export the existing schema
under an API-namespaced name.
"""

from cubebox.llm.snapshot_schema import LLMPresetSchema as AdminPresetEntry
from cubebox.llm.snapshot_schema import ModelPresetsValue as AdminModelPresetsBody

from pydantic import BaseModel


class WorkspacePresetSummary(BaseModel):
    """Per-preset summary exposed to workspace users (no chain refs)."""

    label: str
    is_default: bool


class WorkspacePresetsResponse(BaseModel):
    presets: list[WorkspacePresetSummary]
```

(Chain refs are admin-only — D3. WorkspacePresetSummary is the minimum frontend needs to render the picker.)

- [ ] **Step 4: Run, expect 6 PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/api/schemas/model_presets.py backend/tests/unit/api/test_admin_model_presets_schemas.py
git commit -m "feat(api): add model_presets request/response schemas"
```

---

### Task B2: service layer

**Files:**
- Create: `backend/cubebox/services/model_presets.py`
- Test: `backend/tests/unit/test_model_presets_service.py`

Service responsibilities:
- `read_org_presets(session, org_id)` — return current `OrgSettings.model_presets` value or the system row if absent; returns `(AdminModelPresetsBody, origin: Literal["org", "system", "none"])`
- `write_org_presets(session, org_id, body, available_models)` — validate refs against `available_models` (list of `slug/model_id`), then upsert the org row; raises `BrokenPresetError` if any ref unknown
- `find_preset_refs_to_model(session, org_id, slug, model_id)` — used by model-delete guard; returns list of preset labels referencing that ref

- [ ] **Step 1: Write failing tests**

```python
"""Model-presets service layer."""

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from cubebox.api.schemas.model_presets import AdminModelPresetsBody
from cubebox.credentials.encryption import FernetBackend
from cubebox.llm.errors import BrokenPresetError
from cubebox.models.org_settings import MODEL_PRESETS_KEY, OrgSettings
from cubebox.services.model_presets import (
    find_preset_refs_to_model,
    read_org_presets,
    write_org_presets,
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_read_returns_system_when_no_org_row(session):
    session.add(OrgSettings(
        org_id=None, key=MODEL_PRESETS_KEY,
        value={
            "presets": [{"label": "sys", "chain": ["a/b"], "is_default": True}],
            "task_presets": {},
        },
    ))
    await session.commit()
    body, origin = await read_org_presets(session, "org_x")
    assert origin == "system"
    assert body.presets[0].label == "sys"


@pytest.mark.asyncio
async def test_read_returns_org_when_present(session):
    session.add(OrgSettings(
        org_id=None, key=MODEL_PRESETS_KEY,
        value={"presets": [{"label": "sys", "chain": ["a/b"], "is_default": True}], "task_presets": {}},
    ))
    session.add(OrgSettings(
        org_id="org_x", key=MODEL_PRESETS_KEY,
        value={"presets": [{"label": "org", "chain": ["a/b"], "is_default": True}], "task_presets": {}},
    ))
    await session.commit()
    body, origin = await read_org_presets(session, "org_x")
    assert origin == "org"
    assert body.presets[0].label == "org"


@pytest.mark.asyncio
async def test_read_returns_empty_when_neither_exists(session):
    body, origin = await read_org_presets(session, "org_x")
    assert origin == "none"
    assert body is None


@pytest.mark.asyncio
async def test_write_upserts_org_row(session):
    body = AdminModelPresetsBody.model_validate({
        "presets": [{"label": "default", "chain": ["acme/m1"], "is_default": True}],
        "task_presets": {},
    })
    await write_org_presets(session, "org_x", body, available_models={"acme/m1"})
    await session.commit()
    body2, origin = await read_org_presets(session, "org_x")
    assert origin == "org"
    assert body2.presets[0].label == "default"


@pytest.mark.asyncio
async def test_write_rejects_unknown_ref(session):
    body = AdminModelPresetsBody.model_validate({
        "presets": [{"label": "default", "chain": ["ghost/x"], "is_default": True}],
        "task_presets": {},
    })
    with pytest.raises(BrokenPresetError) as exc:
        await write_org_presets(session, "org_x", body, available_models={"acme/m1"})
    assert "ghost/x" in exc.value.missing_refs


@pytest.mark.asyncio
async def test_find_preset_refs_to_model(session):
    session.add(OrgSettings(
        org_id="org_x", key=MODEL_PRESETS_KEY,
        value={
            "presets": [
                {"label": "ultra", "chain": ["acme/m1", "acme/m2"], "is_default": True},
                {"label": "mini", "chain": ["acme/m1"], "is_default": False},
            ],
            "task_presets": {"title": "mini"},
        },
    ))
    await session.commit()
    refs = await find_preset_refs_to_model(session, "org_x", "acme", "m1")
    assert set(refs) == {"ultra", "mini"}
    refs2 = await find_preset_refs_to_model(session, "org_x", "acme", "m2")
    assert refs2 == ["ultra"]
    refs3 = await find_preset_refs_to_model(session, "org_x", "ghost", "x")
    assert refs3 == []
```

- [ ] **Step 2: Run, expect ImportError**

- [ ] **Step 3: Create `cubebox/services/model_presets.py`**

```python
"""Service-layer for OrgSettings.model_presets read/write + delete guards."""

from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.model_presets import AdminModelPresetsBody
from cubebox.llm.errors import BrokenPresetError
from cubebox.models.org_settings import MODEL_PRESETS_KEY, OrgSettings


async def read_org_presets(
    session: AsyncSession,
    org_id: str,
) -> tuple[AdminModelPresetsBody | None, Literal["org", "system", "none"]]:
    """Return org row if present, else system row, else (None, 'none')."""
    org_stmt = select(OrgSettings).where(
        OrgSettings.org_id == org_id,  # type: ignore[arg-type]
        OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
    )
    row = (await session.execute(org_stmt)).scalar_one_or_none()
    if row is not None:
        return AdminModelPresetsBody.model_validate(row.value), "org"

    sys_stmt = select(OrgSettings).where(
        OrgSettings.org_id.is_(None),  # type: ignore[union-attr]
        OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
    )
    row = (await session.execute(sys_stmt)).scalar_one_or_none()
    if row is not None:
        return AdminModelPresetsBody.model_validate(row.value), "system"
    return None, "none"


async def write_org_presets(
    session: AsyncSession,
    org_id: str,
    body: AdminModelPresetsBody,
    *,
    available_models: set[str],
) -> None:
    """Upsert OrgSettings.model_presets for org. Raises BrokenPresetError on unknown refs."""
    missing: list[str] = []
    for preset in body.presets:
        for ref in preset.chain:
            if ref not in available_models:
                missing.append(ref)
    if missing:
        raise BrokenPresetError(
            label="<admin write>",
            missing_refs=missing,
        )

    existing_stmt = select(OrgSettings).where(
        OrgSettings.org_id == org_id,  # type: ignore[arg-type]
        OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
    )
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    payload = body.model_dump()
    if existing is None:
        session.add(OrgSettings(org_id=org_id, key=MODEL_PRESETS_KEY, value=payload))
    else:
        existing.value = payload
    await session.flush()


async def find_preset_refs_to_model(
    session: AsyncSession,
    org_id: str,
    slug: str,
    model_id: str,
) -> list[str]:
    """Return labels of org presets whose chain references the given model ref."""
    ref = f"{slug}/{model_id}"
    org_stmt = select(OrgSettings).where(
        OrgSettings.org_id == org_id,  # type: ignore[arg-type]
        OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
    )
    row = (await session.execute(org_stmt)).scalar_one_or_none()
    if row is None:
        return []
    out: list[str] = []
    for preset in row.value.get("presets", []):
        if ref in preset.get("chain", []):
            out.append(preset["label"])
    return out
```

- [ ] **Step 4: Run, expect 6 PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/services/model_presets.py backend/tests/unit/test_model_presets_service.py
git commit -m "feat(services): add model_presets service (read/write/find_refs)"
```

---

### Task B3: admin endpoints

**Files:**
- Create: `backend/cubebox/api/routes/v1/admin_model_presets.py`
- Modify: `backend/cubebox/api/routes/v1/admin.py` (register router)
- E2E: `backend/tests/e2e/test_admin_model_presets_e2e.py`

Endpoints:
- `GET /api/v1/admin/model-presets` → `{value: AdminModelPresetsBody, origin: "org"|"system"|"none"}`
- `PUT /api/v1/admin/model-presets` → accepts `AdminModelPresetsBody`; returns the same; 400 on broken refs

- [ ] **Step 1: Write router**

```python
"""Admin endpoints for managing OrgSettings.model_presets."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from cubebox.api.schemas.model_presets import AdminModelPresetsBody
from cubebox.auth.dependencies import require_org_admin
from cubebox.db.engine import async_session_maker
from cubebox.llm.snapshot import load_llm_snapshot
from cubebox.models import User
from cubebox.services.model_presets import read_org_presets, write_org_presets

router = APIRouter(prefix="/admin/model-presets", tags=["admin-model-presets"])


class AdminModelPresetsResponse(BaseModel):
    value: AdminModelPresetsBody | None
    origin: Literal["org", "system", "none"]


@router.get("")
async def get_admin_model_presets(
    *,
    user: Annotated[User, Depends(require_org_admin)],
) -> AdminModelPresetsResponse:
    async with async_session_maker() as session:
        value, origin = await read_org_presets(session, user.org_id)
    return AdminModelPresetsResponse(value=value, origin=origin)


@router.put("")
async def put_admin_model_presets(
    raw_request: Request,
    body: AdminModelPresetsBody,
    *,
    user: Annotated[User, Depends(require_org_admin)],
) -> AdminModelPresetsResponse:
    async with async_session_maker() as session:
        snap = await load_llm_snapshot(
            session,
            user.org_id,
            raw_request.app.state.encryption_backend,
        )
        available_models: set[str] = {
            f"{slug}/{m.id}" for slug, cfg in snap.providers.items() for m in cfg.models
        }
        await write_org_presets(session, user.org_id, body, available_models=available_models)
        await session.commit()
        value, origin = await read_org_presets(session, user.org_id)
    return AdminModelPresetsResponse(value=value, origin=origin)
```

- [ ] **Step 2: Register in `admin.py`**

```python
# In backend/cubebox/api/routes/v1/admin.py, add:
from cubebox.api.routes.v1.admin_model_presets import router as admin_model_presets_router
# In whatever pattern admin.py uses to compose routers (likely include_router):
admin_router.include_router(admin_model_presets_router)
```

(Inspect admin.py to confirm pattern.)

- [ ] **Step 3: Write E2E tests**

Reuse `tests/e2e/test_admin_providers_crud.py` as template. Cover:
- GET when no row → `origin="none"`, `value=None`
- GET after PUT → `origin="org"`, value matches
- PUT with broken ref → 400 `broken_preset`
- PUT then GET round-trip
- Non-admin user → 403

- [ ] **Step 4: Run E2E**

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/api/routes/v1/admin_model_presets.py backend/cubebox/api/routes/v1/admin.py backend/tests/e2e/test_admin_model_presets_e2e.py
git commit -m "feat(api): admin GET/PUT /model-presets endpoints"
```

---

### Task B4: workspace listing endpoint

**Files:**
- Create: `backend/cubebox/api/routes/v1/model_presets.py`
- E2E: `backend/tests/e2e/test_workspace_model_presets_e2e.py`

Endpoint:
- `GET /api/v1/ws/{ws_id}/model-presets` → `WorkspacePresetsResponse{presets: [{label, is_default}]}`

Returns the effective preset list (org row if present, else system) — chain refs stripped (D3).

- [ ] **Step 1: Write router**

```python
"""Workspace endpoint exposing available model presets to chat composer."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from cubebox.api.schemas.model_presets import (
    WorkspacePresetSummary,
    WorkspacePresetsResponse,
)
from cubebox.auth.dependencies import require_workspace_member
from cubebox.db.engine import async_session_maker
from cubebox.llm.snapshot import load_llm_snapshot
from cubebox.models import User

router = APIRouter(prefix="/ws/{ws_id}/model-presets", tags=["workspace-model-presets"])


@router.get("")
async def get_workspace_model_presets(
    raw_request: Request,
    ws_id: str,
    *,
    user: Annotated[User, Depends(require_workspace_member)],
) -> WorkspacePresetsResponse:
    async with async_session_maker() as session:
        snap = await load_llm_snapshot(
            session,
            user.org_id,
            raw_request.app.state.encryption_backend,
        )
    return WorkspacePresetsResponse(
        presets=[
            WorkspacePresetSummary(label=p.label, is_default=p.is_default)
            for p in snap.presets
        ],
    )
```

- [ ] **Step 2: Register router under workspace prefix**

In `backend/cubebox/api/routes/v1/__init__.py` (or wherever workspace routes are composed), include this router.

- [ ] **Step 3: E2E tests**

- workspace member can GET; sees the effective preset list
- non-member of workspace → 403
- when no presets configured at all → empty list (not error — this is workspace-side, not the send_message error matrix)

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/api/routes/v1/model_presets.py backend/cubebox/api/routes/v1/__init__.py backend/tests/e2e/test_workspace_model_presets_e2e.py
git commit -m "feat(api): GET /ws/{ws}/model-presets workspace endpoint"
```

---

### Task B5: delete-model guard

**Files:**
- Modify: `backend/cubebox/services/provider_service.py` (or wherever model-delete lives)
- Modify: `backend/cubebox/api/exceptions.py` (add `ModelInUseByPresetError`)
- Test: `backend/tests/unit/test_model_delete_guard.py`

Before deleting a model, scan all `OrgSettings.model_presets` rows in any org and refuse if any preset references the ref. Return a 409 with the list of `{org_id, preset_label}` pairs.

- [ ] **Step 1: Add `ModelInUseByPresetError` to `cubebox/api/exceptions.py`**

```python
class ModelInUseByPresetError(APIException):
    def __init__(self, slug: str, model_id: str, refs: list[dict[str, str]]) -> None:
        super().__init__(
            error_code="model_in_use_by_preset",
            message=f"model {slug}/{model_id} is referenced by presets and cannot be deleted",
            status_code=409,
            details=f"refs={refs}",
        )
        self.refs = refs
```

- [ ] **Step 2: Find delete-model endpoint**

```bash
grep -rn "def delete_model\|delete_model_endpoint\|@router.delete.*models" backend/cubebox/
```

Likely in `cubebox/api/routes/v1/admin_models.py` or `admin_providers.py`. Read the handler.

- [ ] **Step 3: Add the guard**

Before performing the actual delete:

```python
from cubebox.services.model_presets import find_preset_refs_to_model
from cubebox.api.exceptions import ModelInUseByPresetError
from sqlalchemy import select
from cubebox.models.org_settings import MODEL_PRESETS_KEY, OrgSettings

# Find all orgs that have a model_presets row
preset_rows = (await session.execute(
    select(OrgSettings).where(OrgSettings.key == MODEL_PRESETS_KEY)
)).scalars().all()
refs: list[dict[str, str]] = []
ref = f"{slug}/{model_id}"
for row in preset_rows:
    for preset in row.value.get("presets", []):
        if ref in preset.get("chain", []):
            refs.append({
                "org_id": row.org_id or "system",
                "preset_label": preset["label"],
            })
if refs:
    raise ModelInUseByPresetError(slug=slug, model_id=model_id, refs=refs)
# Proceed with delete...
```

- [ ] **Step 4: Tests**

Unit test: seed two orgs' OrgSettings rows referencing a model; assert the delete raises `ModelInUseByPresetError` with both refs.

E2E test (optional but valuable): admin tries to delete a referenced model via the actual route, gets 409.

- [ ] **Step 5: Commit**

```bash
git add -A backend/
git commit -m "feat(admin): guard model delete with preset-reference check"
```

---

### Task B6: Fix-6 subagent failover attribution

**Files:**
- Modify: `backend/cubebox/streams/run_manager.py`

Currently the subagent gets the SAME `FallbackBoundModel` instance as the main agent, so subagent failovers fire the main-agent `on_failover` closure and emit `model_failover` events misattributed to the top-level conversation.

Fix: give the subagent middleware a copy of the chain model with `on_failover=None`. `FallbackBoundModel` is a frozen dataclass — use `dataclasses.replace`.

- [ ] **Step 1: Locate the subagent wiring**

Around `run_manager.py:~2609`:

```python
subagent_mw = SubagentMiddleware(
    subagents={},
    default_model=this_run_model,
    ...
)
```

- [ ] **Step 2: Build the subagent variant**

```python
from dataclasses import replace
from cubepi.providers.fallback import FallbackBoundModel

# Strip on_failover so subagent failovers don't emit model_failover SSE
# events misattributed to the main agent. Spec 3 can revisit if subagent
# failover visibility becomes a product requirement.
if isinstance(this_run_model, FallbackBoundModel):
    subagent_model = replace(this_run_model, on_failover=None)
else:
    subagent_model = this_run_model  # plain BoundModel has no callback

subagent_mw = SubagentMiddleware(
    subagents={},
    default_model=subagent_model,
    ...
)
```

- [ ] **Step 3: Unit test**

```python
"""Subagent receives chain model without on_failover (Fix-6)."""

import pytest
from dataclasses import is_dataclass
from cubepi.providers.fallback import FallbackBoundModel

# Test through _build_agent_for_conversation or a more direct unit test
# of the replace() behavior. The simplest assertion: build a
# FallbackBoundModel with an on_failover callback, call replace(...,
# on_failover=None), confirm the copy has None and the original is
# unchanged (frozen contract preserved).


def test_replace_strips_on_failover():
    from cubepi.providers.faux import FauxProvider
    from cubepi.providers.base import BoundModel  # ensure import path

    primary = FauxProvider(provider_id="p1").model("m1")
    secondary = FauxProvider(provider_id="p2").model("m2")
    async def cb(failed, nxt, err): ...
    fb = FallbackBoundModel(chain=(primary, secondary), on_failover=cb)
    from dataclasses import replace
    stripped = replace(fb, on_failover=None)
    assert stripped.on_failover is None
    assert fb.on_failover is cb  # frozen original unchanged
    assert stripped.chain == fb.chain
```

- [ ] **Step 4: E2E test (extend existing fallback E2E)**

In `tests/e2e/test_fallback_e2e.py`, add a test that triggers a subagent failover and asserts NO `model_failover` SSE event is emitted (only the cubepi warning log fires internally). This requires a multi-turn flow where the agent invokes a subagent — may be substantial. If too complex, defer to a follow-up and mark with a TODO.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/streams/run_manager.py backend/tests/unit/test_subagent_failover_attribution.py
git commit -m "fix(run_manager): subagent gets chain model without on_failover (Fix-6)"
```

---

## Frontend tasks

### Task F1: API client + types

**Files:**
- Create: `frontend/packages/web/src/lib/api/presets.ts`
- Create: `frontend/packages/web/src/lib/types/presets.ts`
- Test: `frontend/packages/web/src/lib/api/__tests__/presets.test.ts`

```typescript
// presets.ts
import type { AdminModelPresetsBody, WorkspacePresetSummary } from "../types/presets";

export async function fetchAdminModelPresets(): Promise<{
  value: AdminModelPresetsBody | null;
  origin: "org" | "system" | "none";
}> {
  const res = await fetch("/api/v1/admin/model-presets", { credentials: "include" });
  if (!res.ok) throw new Error(`Failed to fetch admin model presets: ${res.status}`);
  return res.json();
}

export async function putAdminModelPresets(body: AdminModelPresetsBody): Promise<void> {
  const res = await fetch("/api/v1/admin/model-presets", {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const errBody = await res.json().catch(() => null);
    throw new Error(errBody?.error?.message ?? `Failed: ${res.status}`);
  }
}

export async function fetchWorkspaceModelPresets(wsId: string): Promise<WorkspacePresetSummary[]> {
  const res = await fetch(`/api/v1/ws/${wsId}/model-presets`, { credentials: "include" });
  if (!res.ok) throw new Error(`Failed to fetch workspace model presets: ${res.status}`);
  const data = await res.json();
  return data.presets;
}
```

- [ ] **Step 1: Define types**

```typescript
// types/presets.ts
export type ThinkingLevel = "off" | "minimal" | "low" | "medium" | "high" | "xhigh";

export interface AdminPresetEntry {
  label: string;
  chain: string[];
  is_default: boolean;
}

export interface AdminModelPresetsBody {
  presets: AdminPresetEntry[];
  task_presets: Record<"title" | "compaction" | "summarize", string>;
}

export interface WorkspacePresetSummary {
  label: string;
  is_default: boolean;
}
```

- [ ] **Step 2-4: Implement + test + commit**

```bash
git add frontend/packages/web/src/lib/api/presets.ts \
        frontend/packages/web/src/lib/types/presets.ts \
        frontend/packages/web/src/lib/api/__tests__/presets.test.ts
git commit -m "feat(web): API client + types for model presets"
```

---

### Task F2: PresetPicker + ThinkingControl components

**Files:**
- Create: `frontend/packages/web/src/components/chat/PresetPicker.tsx`
- Create: `frontend/packages/web/src/components/chat/ThinkingControl.tsx`
- Create: `frontend/packages/web/src/lib/stores/preset-selection.ts`
- Test: vitest unit tests + Playwright

```typescript
// PresetPicker.tsx
"use client";

import { useEffect } from "react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { fetchWorkspaceModelPresets } from "@/lib/api/presets";
import { usePresetSelectionStore } from "@/lib/stores/preset-selection";

export function PresetPicker({ wsId }: { wsId: string }) {
  const { presetLabel, setPresetLabel, presets, setPresets } = usePresetSelectionStore();

  useEffect(() => {
    fetchWorkspaceModelPresets(wsId).then(setPresets);
  }, [wsId, setPresets]);

  return (
    <Select value={presetLabel ?? ""} onValueChange={setPresetLabel}>
      <SelectTrigger className="w-32">
        <SelectValue placeholder="Preset" />
      </SelectTrigger>
      <SelectContent>
        {presets.map((p) => (
          <SelectItem key={p.label} value={p.label}>
            {p.label} {p.is_default && "(default)"}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
```

```typescript
// ThinkingControl.tsx
"use client";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { usePresetSelectionStore } from "@/lib/stores/preset-selection";
import type { ThinkingLevel } from "@/lib/types/presets";

const LEVELS: { value: ThinkingLevel; label: string }[] = [
  { value: "off", label: "Standard" },
  { value: "minimal", label: "Minimal" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "xhigh", label: "Extra High" },
];

export function ThinkingControl() {
  const { thinking, setThinking } = usePresetSelectionStore();
  return (
    <Select value={thinking} onValueChange={(v) => setThinking(v as ThinkingLevel)}>
      <SelectTrigger className="w-32">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {LEVELS.map((l) => (
          <SelectItem key={l.value} value={l.value}>{l.label}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
```

```typescript
// stores/preset-selection.ts
import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { ThinkingLevel, WorkspacePresetSummary } from "@/lib/types/presets";

interface State {
  presets: WorkspacePresetSummary[];
  presetLabel: string | null; // null = use default
  thinking: ThinkingLevel;
  setPresets: (p: WorkspacePresetSummary[]) => void;
  setPresetLabel: (l: string | null) => void;
  setThinking: (t: ThinkingLevel) => void;
}

export const usePresetSelectionStore = create<State>()(
  persist(
    (set) => ({
      presets: [],
      presetLabel: null,
      thinking: "off",
      setPresets: (presets) => set({ presets }),
      setPresetLabel: (presetLabel) => set({ presetLabel }),
      setThinking: (thinking) => set({ thinking }),
    }),
    { name: "preset-selection-v1" },
  ),
);
```

- [ ] **Steps**: unit tests via vitest, then commit.

```bash
git add frontend/packages/web/src/components/chat/PresetPicker.tsx \
        frontend/packages/web/src/components/chat/ThinkingControl.tsx \
        frontend/packages/web/src/lib/stores/preset-selection.ts \
        frontend/packages/web/src/components/chat/__tests__/PresetPicker.test.tsx \
        frontend/packages/web/src/components/chat/__tests__/ThinkingControl.test.tsx
git commit -m "feat(web): PresetPicker + ThinkingControl + selection store"
```

---

### Task F3: Wire into MessageComposer

**Files:**
- Modify: `frontend/packages/web/src/components/chat/MessageComposer.tsx`
- Modify: `frontend/packages/web/src/lib/api/conversations.ts`

Locate `MessageComposer.tsx` — embed `<PresetPicker wsId={wsId} />` and `<ThinkingControl />` next to the send button. When sending a message, read `presetLabel` and `thinking` from the store and include them in the request body.

```typescript
// conversations.ts — extend the send-message body type:
export interface SendMessageRequest {
  content: string;
  attachments?: string[];
  preset_label?: string | null;
  thinking?: ThinkingLevel;
}
```

In the send handler:

```typescript
import { usePresetSelectionStore } from "@/lib/stores/preset-selection";

const { presetLabel, thinking } = usePresetSelectionStore.getState();

await sendMessage(wsId, conversationId, {
  content,
  attachments,
  preset_label: presetLabel,
  thinking,
});
```

- [ ] **Steps**: tests + commit.

```bash
git add frontend/packages/web/src/components/chat/MessageComposer.tsx \
        frontend/packages/web/src/lib/api/conversations.ts
git commit -m "feat(web): embed preset picker + thinking control in MessageComposer"
```

---

### Task F4: FailoverBanner in MessageStream

**Files:**
- Create: `frontend/packages/web/src/components/chat/FailoverBanner.tsx`
- Modify: `frontend/packages/web/src/components/chat/MessageStream.tsx`

`FailoverBanner` renders a `model_failover` SSE event inline as a small gray banner between messages: "Switched from `failed_ref` to `next_ref` — reason: …". Collapsible.

Locate the SSE event-rendering switch in `MessageStream.tsx`. Add a case for `type === "model_failover"` that renders `<FailoverBanner event={event} />`.

- [ ] **Steps**: tests + commit.

```bash
git add frontend/packages/web/src/components/chat/FailoverBanner.tsx \
        frontend/packages/web/src/components/chat/MessageStream.tsx
git commit -m "feat(web): render model_failover SSE events as inline banner"
```

---

### Task F5: Admin Preset Editor page

**Files:**
- Create: `frontend/packages/web/src/app/admin/presets/page.tsx`
- Create: `frontend/packages/web/src/app/admin/presets/PresetEditor.tsx`
- Modify: `frontend/packages/web/src/app/admin/layout.tsx`

Server component shell loads initial data; client component handles editing.

```typescript
// page.tsx
import { fetchAdminModelPresets } from "@/lib/api/presets";
import { PresetEditor } from "./PresetEditor";

export default async function AdminPresetsPage() {
  const initial = await fetchAdminModelPresets();
  return <PresetEditor initial={initial} />;
}
```

```typescript
// PresetEditor.tsx
"use client";
// - Form to add/edit presets (label, chain, is_default)
// - Chain editor: ordered list with add/remove/reorder
// - Task overrides: dropdowns for title/compaction/summarize
// - Save button → PUT /admin/model-presets
// - On 400 broken_preset, surface the failed refs inline
```

- [ ] **Steps**: tests + Playwright + commit.

```bash
git add frontend/packages/web/src/app/admin/presets/page.tsx \
        frontend/packages/web/src/app/admin/presets/PresetEditor.tsx \
        frontend/packages/web/src/app/admin/presets/__tests__/page.test.tsx \
        frontend/packages/web/src/app/admin/layout.tsx
git commit -m "feat(web): admin preset editor page"
```

---

### Task F6: Playwright E2E

**Files:**
- Create: `frontend/packages/web/playwright/tests/admin-presets.spec.ts`
- Create: `frontend/packages/web/playwright/tests/chat-preset-picker.spec.ts`

Cover:
- Admin: create + edit + save → verify GET reflects the change
- Admin: delete a model that's referenced → 409 with refs displayed
- Chat: pick a non-default preset → assert request body contains the right preset_label
- Chat: change thinking level → assert request body contains the right thinking
- Chat: server emits model_failover → banner renders

- [ ] **Steps**: write specs, run them, commit.

```bash
git add frontend/packages/web/playwright/tests/admin-presets.spec.ts \
        frontend/packages/web/playwright/tests/chat-preset-picker.spec.ts
git commit -m "test(web): playwright E2E for preset admin + chat picker"
```

---

## Final integration

### Task I1: End-to-end smoke

- [ ] Run full backend suite: `cd backend && uv run pytest tests/ -v`
- [ ] Run full frontend suite: `cd frontend && pnpm -r test && pnpm -r e2e`
- [ ] Manual smoke (slot 17): start backend on :8017 + frontend on :3017, create a preset in admin, send a chat message with the new preset, observe model_failover banner if chain triggers it.

### Task I2: Push PR + run /code-review until clean

```bash
git push -u origin feat/preset-admin-and-workspace
gh pr create --title "feat(presets): admin CRUD + workspace picker + failover UI (Spec 2+3)" --body "..."
```

Then run `/code-review` on the PR. Fix findings. Repeat until clean.

---

## Test strategy summary

- **Unit:** schema validation, service layer logic, frontend store, component rendering
- **E2E (backend):** admin CRUD round-trip, workspace listing, delete-model guard, subagent failover suppression
- **E2E (Playwright):** admin UI flow, chat composer preset + thinking, failover banner

Mocking discipline: backend E2E uses cubepi's `FauxProvider` (already established pattern from PR #215). Frontend tests use MSW for API mocks.

---

## Spec coverage check

| Spec section | Tasks |
|---|---|
| Admin CRUD endpoints | B1, B2, B3 |
| Admin "delete model blocked by referencing presets" UX | B5, F5 |
| Workspace API listing | B1, B2, B4 |
| Workspace chat picker | F1, F2, F3 |
| Thinking depth control | F2, F3 |
| `model_failover` rendering | F4 |
| Subagent failover attribution (Fix-6) | B6 |
| Admin frontend management page | F5 |
| Playwright E2E for admin + chat | F6 |

---

## Open follow-ups (out of scope)

- **cubepi Tracer/Meter chain coverage** — tracked in cubepi#167; not blocking this PR.
- **Per-conversation preset persistence** (alternative to D4 localStorage) — defer until product feedback.
- **Subagent failover visibility** in UI (counterpoint to D8) — only if product asks.
- **Granular admin field-PATCH** (alternative to D1 full PUT) — only if product asks.
