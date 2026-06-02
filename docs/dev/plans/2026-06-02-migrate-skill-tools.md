# Migrate Skill Tools to Agent Platform Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `find_skills`, `preview_skill`, `install_skill` from ad-hoc cubepi tool factories to a single `skills` capability built on the agent platform actions mechanism (PR #185).

**Architecture:** Introduce a `SkillDeps` dataclass + `build_skills_capability(deps)` factory that returns an `AgentCapability` with three operations (`find`/`preview`/`install`); extend `tools_for_run` with an optional `skill_deps` param; collapse the 80-line bespoke wiring in `run_manager.py` to a single registry call; delete the three legacy tool factory files.

**Tech Stack:** Python 3.12, Pydantic v2 discriminated unions, cubepi `AgentTool`, FastAPI, SQLAlchemy async, pytest.

**Spec:** `docs/dev/specs/2026-06-02-migrate-skill-tools-design.md`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `backend/cubebox/agents/actions/capabilities/skills.py` | `SkillDeps` dataclass + `build_skills_capability` factory + 3 handlers (find/preview/install) |
| Modify | `backend/cubebox/agents/actions/registry.py` | `tools_for_run` accepts optional `skill_deps` and dynamically appends the skills capability |
| Modify | `backend/cubebox/streams/run_manager.py` | Delete lines 1133-1195 (bespoke wiring); construct `SkillDeps` and pass to `tools_for_run` |
| Delete | `backend/cubebox/tools/builtin/find_skills.py` | Superseded by capability |
| Delete | `backend/cubebox/tools/builtin/preview_skill.py` | Superseded by capability (logic lifted into `skills.py`) |
| Delete | `backend/cubebox/tools/builtin/install_skill.py` | Superseded by capability |
| Delete | `backend/tests/unit/test_install_skill.py` | Replaced by capability tests |
| Delete | `backend/tests/unit/test_preview_skill.py` | Replaced by capability tests |
| Delete | `backend/tests/e2e/test_find_skills_tool.py` | Replaced by capability tests |
| Create | `backend/tests/unit/test_skills_capability.py` | Unit tests for the 3 handlers + mutation gate |

---

### Task 1: Skills capability — `SkillDeps`, input models, factory skeleton

**Files:**
- Create: `backend/cubebox/agents/actions/capabilities/skills.py`

This task creates the file scaffold with `SkillDeps`, the three Pydantic input models, and a `build_skills_capability` that returns an `AgentCapability` with three `AgentOperation`s whose handlers are placeholders that raise `NotImplementedError`. Subsequent tasks fill in each handler one at a time using TDD.

- [ ] **Step 1: Create the skeleton**

```python
# backend/cubebox/agents/actions/capabilities/skills.py
"""skills capability — find/preview/install operations.

Unlike SCHEDULED_TASKS_CAPABILITY (a module-level constant), the skills
capability is built per-run via build_skills_capability(deps) because its
handlers must close over run-scoped dependencies: a SkillCatalogService,
the catalog AsyncSession, a SkillsAdapterManager (itself built async at
run start), and the org id/slug.

load_skill is intentionally NOT migrated here. It is runtime infrastructure
(SkillsMiddleware intercepts its result to append SKILL.md to the next
system prompt) and stays wired directly in run_manager.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.agents.actions.context import ScopeContext
from cubebox.agents.actions.types import (
    ActionInvalidInput,
    AgentCapability,
    AgentOperation,
)
from cubebox.skills.service import SkillCatalogService
from cubebox.skills.sources.registry import SkillsAdapterManager


@dataclass(frozen=True)
class SkillDeps:
    """Run-scoped dependencies for the skills capability.

    Constructed once at the start of a cubepi run when the catalog is
    reachable; captured in the handler closures returned by
    build_skills_capability.
    """

    catalog: SkillCatalogService
    catalog_session: AsyncSession
    registry: SkillsAdapterManager
    org_id: str
    org_slug: str
    workspace_id: str | None


# --- Input models per operation ---

class FindInput(BaseModel):
    query: str = Field(
        description="Plain-language description of the capability you need.",
    )
    limit: int = Field(default=5, ge=1, le=20)


class PreviewInput(BaseModel):
    candidate_id: str = Field(
        description=(
            "The candidate_id from a find result. Returns the SKILL.md "
            "content so you can describe the skill before suggesting installation."
        ),
    )


class InstallInput(BaseModel):
    candidate_id: str = Field(
        description=(
            "The candidate_id from a find result. "
            "Only call this after the user has explicitly confirmed they want to install."
        ),
    )


# --- Handler stubs (filled in by Task 2/3/4) ---

async def _handle_find(ctx: ScopeContext, session: Any, inp: FindInput) -> Any:
    raise NotImplementedError("Task 2 fills this in")


async def _handle_preview(ctx: ScopeContext, session: Any, inp: PreviewInput) -> Any:
    raise NotImplementedError("Task 3 fills this in")


async def _handle_install(ctx: ScopeContext, session: Any, inp: InstallInput) -> Any:
    raise NotImplementedError("Task 4 fills this in")


def build_skills_capability(deps: SkillDeps) -> AgentCapability:
    """Build the skills capability with run-scoped deps closed over the handlers."""
    # NOTE: handlers receive (ctx, session, input). They close over `deps`
    # to access the catalog/registry/catalog_session built once per run.

    async def find_handler(ctx: ScopeContext, session: Any, inp: FindInput) -> Any:
        return await _handle_find_impl(deps, ctx, session, inp)

    async def preview_handler(ctx: ScopeContext, session: Any, inp: PreviewInput) -> Any:
        return await _handle_preview_impl(deps, ctx, session, inp)

    async def install_handler(ctx: ScopeContext, session: Any, inp: InstallInput) -> Any:
        return await _handle_install_impl(deps, ctx, session, inp)

    return AgentCapability(
        name="skills",
        description=(
            "Search, preview, and install skills available to this workspace. "
            "Use find to discover candidates, preview to read SKILL.md before "
            "suggesting installation, and install only when the user has "
            "explicitly confirmed."
        ),
        operations=[
            AgentOperation(
                name="find",
                description=(
                    "Search available skills (your org's catalog + registered "
                    "remote registries) by a plain-language need. Read-only: "
                    "returns ranked candidates with descriptions; never installs."
                ),
                input_model=FindInput,
                handler=find_handler,
                mutates=False,
            ),
            AgentOperation(
                name="preview",
                description=(
                    "Fetch the full SKILL.md of any candidate — installed or not. "
                    "Use after find to read what a skill does before recommending installation. "
                    "Pass the candidate_id from the find result."
                ),
                input_model=PreviewInput,
                handler=preview_handler,
                mutates=False,
            ),
            AgentOperation(
                name="install",
                description=(
                    "Install a skill candidate into the current workspace. "
                    "Only call this when the user has explicitly asked to install. "
                    "Pass the candidate_id from the find result."
                ),
                input_model=InstallInput,
                handler=install_handler,
                mutates=True,
            ),
        ],
    )


# Implementation stubs (Task 2/3/4 fill these in)

async def _handle_find_impl(
    deps: SkillDeps, ctx: ScopeContext, session: Any, inp: FindInput,
) -> Any:
    raise NotImplementedError("Task 2 fills this in")


async def _handle_preview_impl(
    deps: SkillDeps, ctx: ScopeContext, session: Any, inp: PreviewInput,
) -> Any:
    raise NotImplementedError("Task 3 fills this in")


async def _handle_install_impl(
    deps: SkillDeps, ctx: ScopeContext, session: Any, inp: InstallInput,
) -> Any:
    raise NotImplementedError("Task 4 fills this in")
```

- [ ] **Step 2: Verify imports + structure**

Run: `cd backend && uv run python -c "from cubebox.agents.actions.capabilities.skills import SkillDeps, build_skills_capability; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Run mypy on the new file**

Run: `cd backend && uv run mypy cubebox/agents/actions/capabilities/skills.py`
Expected: `Success: no issues found`

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/agents/actions/capabilities/skills.py
git commit -m "feat(actions): skeleton for skills capability (deps, inputs, factory)"
```

---

### Task 2: `find` handler

**Files:**
- Modify: `backend/cubebox/agents/actions/capabilities/skills.py`
- Create: `backend/tests/unit/test_skills_capability.py`

The `find` handler is a thin wrapper around `SkillDiscoveryService.discover()`. The shape of the returned dict must match what today's `find_skills.py` returns so any callers in tests / future docs remain compatible.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_skills_capability.py
"""Unit tests for the skills capability (find / preview / install)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from cubebox.agents.actions.capabilities.skills import (
    FindInput,
    SkillDeps,
    _handle_find_impl,
)
from cubebox.agents.actions.context import ScopeContext
from cubebox.models.membership import Role


def _make_deps(
    *,
    discovery_result: list[Any] | None = None,
    registry: Any | None = None,
    catalog: Any | None = None,
    catalog_session: Any | None = None,
) -> SkillDeps:
    return SkillDeps(
        catalog=catalog or MagicMock(),
        catalog_session=catalog_session or MagicMock(),
        registry=registry or MagicMock(),
        org_id="org-test",
        org_slug="org-slug",
        workspace_id="ws-test",
    )


def _ctx() -> ScopeContext:
    return ScopeContext(
        org_id="org-test",
        workspace_id="ws-test",
        user_id="usr-test",
        role=Role.MEMBER,
    )


@dataclass
class _FakeCandidate:
    candidate_id: str
    name: str
    canonical_name: str
    description: str
    source_kind: str
    source_name: str
    repo: str | None
    trust: Any
    install_state: str
    install_count: int


class _TrustEnum:
    """Matches the .value attribute the handler reads off Trust."""

    def __init__(self, value: str) -> None:
        self.value = value


@pytest.mark.asyncio
async def test_find_returns_candidates_payload() -> None:
    fake = _FakeCandidate(
        candidate_id="cid-1",
        name="My Skill",
        canonical_name="myorg:my-skill",
        description="Does something useful",
        source_kind="local",
        source_name="org-catalog",
        repo="https://github.com/x/y",
        trust=_TrustEnum("official"),
        install_state="in_catalog",
        install_count=3,
    )
    # Patch SkillDiscoveryService.discover to return [fake]
    import cubebox.agents.actions.capabilities.skills as mod

    fake_discovery = MagicMock()
    fake_discovery.discover = AsyncMock(return_value=[fake])
    mod._SkillDiscoveryService = MagicMock(return_value=fake_discovery)  # type: ignore[attr-defined]

    deps = _make_deps()
    result = await _handle_find_impl(deps, _ctx(), MagicMock(), FindInput(query="useful"))

    assert isinstance(result, dict)
    assert "candidates" in result and "hint" in result
    assert len(result["candidates"]) == 1
    c = result["candidates"][0]
    assert c["candidate_id"] == "cid-1"
    assert c["name"] == "My Skill"
    assert c["canonical_name"] == "myorg:my-skill"
    assert c["source"] == "local"
    assert c["trust"] == "official"
    assert c["unvetted"] is False  # local source → never unvetted
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_skills_capability.py::test_find_returns_candidates_payload -v`
Expected: FAIL with `NotImplementedError: Task 2 fills this in`

- [ ] **Step 3: Implement the `find` handler**

In `cubebox/agents/actions/capabilities/skills.py`, add the `SkillDiscoveryService` import at the top:

```python
from cubebox.skills.discovery import SkillDiscoveryService

# Module-level alias for tests to monkeypatch.
_SkillDiscoveryService = SkillDiscoveryService
```

Replace `_handle_find_impl` with:

```python
async def _handle_find_impl(
    deps: SkillDeps, ctx: ScopeContext, session: Any, inp: FindInput,
) -> Any:
    del ctx, session  # uses deps' run-scoped registry, not the per-call session
    discovery = _SkillDiscoveryService(deps.registry)
    cands = await discovery.discover(inp.query, limit=inp.limit)
    return {
        "candidates": [
            {
                "candidate_id": c.candidate_id,
                "name": c.name,
                "canonical_name": c.canonical_name,
                "description": c.description,
                "source": c.source_kind,
                "source_name": c.source_name,
                "repo": c.repo,
                "trust": c.trust.value,
                "install_state": c.install_state,
                "install_count": c.install_count,
                "unvetted": c.source_kind == "remote" and c.trust.value != "official",
            }
            for c in cands
        ],
        "hint": (
            "To use an 'enabled' candidate now, call load_skill(canonical_name). "
            "To install an 'in_catalog' or 'available' candidate: present it to the "
            "user with skills(operation='preview', candidate_id=...) so they can see "
            "what it does, then call skills(operation='install', candidate_id=...) "
            "only when the user explicitly asks to install. Never install silently."
        ),
    }
```

- [ ] **Step 4: Run the test**

Run: `cd backend && uv run pytest tests/unit/test_skills_capability.py::test_find_returns_candidates_payload -v`
Expected: PASS

- [ ] **Step 5: Run mypy**

Run: `cd backend && uv run mypy cubebox/agents/actions/capabilities/skills.py tests/unit/test_skills_capability.py`
Expected: `Success: no issues found`

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/agents/actions/capabilities/skills.py backend/tests/unit/test_skills_capability.py
git commit -m "feat(actions): skills.find handler — delegates to SkillDiscoveryService"
```

---

### Task 3: `preview` handler

**Files:**
- Modify: `backend/cubebox/agents/actions/capabilities/skills.py`
- Modify: `backend/tests/unit/test_skills_capability.py`

The `preview` handler ports today's `preview_skill.py` logic verbatim: decode `candidate_id`, branch on local vs remote, return the SKILL.md content with env vars extracted. Errors map to `ActionInvalidInput` with the same text codes today's tool returns.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_skills_capability.py`:

```python
# --- preview tests ---

import cubebox.agents.actions.capabilities.skills as _skills_mod
from cubebox.agents.actions.capabilities.skills import (  # noqa: E402
    PreviewInput,
    _handle_preview_impl,
)
from cubebox.agents.actions.types import ActionInvalidInput  # noqa: E402


@pytest.mark.asyncio
async def test_preview_bad_candidate_id_raises_invalid_input() -> None:
    deps = _make_deps()
    with pytest.raises(ActionInvalidInput, match="BAD_CANDIDATE_ID"):
        await _handle_preview_impl(
            deps, _ctx(), MagicMock(), PreviewInput(candidate_id="!!!bad!!!"),
        )


@pytest.mark.asyncio
async def test_preview_local_returns_content(monkeypatch: pytest.MonkeyPatch) -> None:
    from cubebox.skills.sources.base import encode_candidate_id

    # Fake repos & catalog
    fake_skill = MagicMock(
        id="skl-1",
        name="local-skill",
        source="preinstalled",
        owner_org_id="org-test",
        current_version="1.0.0",
    )
    fake_version = MagicMock(id="skv-1")

    skill_repo = MagicMock()
    skill_repo.get = AsyncMock(return_value=fake_skill)
    tomb_repo = MagicMock()
    tomb_repo.get = AsyncMock(return_value=None)
    version_repo = MagicMock()
    version_repo.find = AsyncMock(return_value=fake_version)

    monkeypatch.setattr(_skills_mod, "_SkillRepository", lambda _s: skill_repo)
    monkeypatch.setattr(
        _skills_mod, "_OrgPreinstalledTombstoneRepository", lambda _s: tomb_repo,
    )
    monkeypatch.setattr(_skills_mod, "_SkillVersionRepository", lambda _s: version_repo)

    fake_catalog = MagicMock()
    fake_catalog.fetch_skill_md = AsyncMock(
        return_value="---\nname: local-skill\nenv_vars: [API_KEY]\n---\n# Local Skill"
    )

    deps = _make_deps(catalog=fake_catalog)
    cid = encode_candidate_id("local", "skl-1", source_id="local")

    result = await _handle_preview_impl(
        deps, _ctx(), MagicMock(), PreviewInput(candidate_id=cid),
    )
    assert isinstance(result, dict)
    assert result["candidate_id"] == cid
    assert result["name"] == "local-skill"
    assert "Local Skill" in result["content"]


@pytest.mark.asyncio
async def test_preview_remote_missing_source_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from cubebox.skills.sources.base import encode_candidate_id

    registry = MagicMock()
    registry.adapter_by_id = MagicMock(return_value=None)
    deps = _make_deps(registry=registry)
    cid = encode_candidate_id("remote", "owner/repo/main/skill", source_id="src-x")

    with pytest.raises(ActionInvalidInput, match="SOURCE_NOT_FOUND"):
        await _handle_preview_impl(
            deps, _ctx(), MagicMock(), PreviewInput(candidate_id=cid),
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_skills_capability.py -v --no-header 2>&1 | tail -10`
Expected: 3 new tests fail with `NotImplementedError` or `AttributeError` (the monkeypatched names don't exist yet)

- [ ] **Step 3: Implement the `preview` handler**

In `cubebox/agents/actions/capabilities/skills.py`:

Add imports at the top:

```python
import httpx

from cubebox.repositories.skill import (
    OrgPreinstalledTombstoneRepository,
    SkillRepository,
    SkillVersionRepository,
)
from cubebox.skills.frontmatter import extract_env_vars, parse_skill_md
from cubebox.skills.sources.base import CandidateIdError, decode_candidate_id

# Module-level aliases so tests can monkeypatch.
_SkillRepository = SkillRepository
_OrgPreinstalledTombstoneRepository = OrgPreinstalledTombstoneRepository
_SkillVersionRepository = SkillVersionRepository
```

Add a `_env_vars` helper just below the imports:

```python
def _env_vars(content: str) -> list[str]:
    try:
        fm = parse_skill_md(content)
        return extract_env_vars(fm.raw_metadata)
    except Exception:  # noqa: BLE001
        return []
```

Replace `_handle_preview_impl` with the full port from `preview_skill.py`:

```python
async def _handle_preview_impl(
    deps: SkillDeps, ctx: ScopeContext, session: Any, inp: PreviewInput,
) -> Any:
    del ctx, session  # uses deps.catalog_session + deps.registry

    try:
        kind, source_id, source_ref = decode_candidate_id(inp.candidate_id)
    except CandidateIdError as exc:
        raise ActionInvalidInput("BAD_CANDIDATE_ID") from exc

    if kind == "local":
        skill = await _SkillRepository(deps.catalog_session).get(source_ref)
        if skill is None or not (
            skill.source == "preinstalled" or skill.owner_org_id == deps.org_id
        ):
            raise ActionInvalidInput("SKILL_NOT_FOUND")
        if skill.source == "preinstalled":
            tomb_repo = _OrgPreinstalledTombstoneRepository(deps.catalog_session)
            tombstone = await tomb_repo.get(deps.org_id, skill.id)
            if tombstone is not None:
                raise ActionInvalidInput("SKILL_NOT_FOUND")
        sv = await _SkillVersionRepository(deps.catalog_session).find(
            skill.id, skill.current_version,
        )
        if sv is None:
            raise ActionInvalidInput("SKILL_VERSION_NOT_FOUND")
        content = await deps.catalog.fetch_skill_md(sv.id)
        return {
            "candidate_id": inp.candidate_id,
            "name": skill.name,
            "content": content,
            "env_vars": _env_vars(content),
        }

    # Remote path
    adapter = deps.registry.adapter_by_id(source_id)
    if adapter is None:
        raise ActionInvalidInput("SOURCE_NOT_FOUND")
    try:
        files = await adapter.fetch(source_ref)
    except (httpx.HTTPError, ValueError) as exc:
        raise ActionInvalidInput(f"REMOTE_FETCH_FAILED: {exc}") from exc
    if "SKILL.md" not in files:
        raise ActionInvalidInput("SKILL_MD_MISSING")
    try:
        content = files["SKILL.md"].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ActionInvalidInput(f"INVALID_UTF8: {exc}") from exc
    slug = source_ref.rsplit("/", 1)[-1]
    try:
        fm = parse_skill_md(content)
        display_name = fm.name or slug
    except Exception:  # noqa: BLE001
        display_name = slug
    return {
        "candidate_id": inp.candidate_id,
        "name": display_name,
        "content": content,
        "env_vars": _env_vars(content),
    }
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/unit/test_skills_capability.py -v --no-header 2>&1 | tail -10`
Expected: all preview tests pass

- [ ] **Step 5: Run mypy**

Run: `cd backend && uv run mypy cubebox/agents/actions/capabilities/skills.py`
Expected: Success

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/agents/actions/capabilities/skills.py backend/tests/unit/test_skills_capability.py
git commit -m "feat(actions): skills.preview handler — ports preview_skill logic"
```

---

### Task 4: `install` handler

**Files:**
- Modify: `backend/cubebox/agents/actions/capabilities/skills.py`
- Modify: `backend/tests/unit/test_skills_capability.py`

The `install` handler validates the candidate_id, instantiates a `SkillInstallService` with run-scoped deps + per-call session, and calls `install()`. `SkillInstallError` is mapped to `ActionInvalidInput` (same text the legacy tool returned), so the builder yields `is_error=True` with identical wire format.

Per the spec's transaction-ownership rule, `install` uses the per-call session (from `ContextFactory`) for its writes — not the long-lived `catalog_session`. This is a slight tightening from today (which used `catalog_session` for install writes too), matching how `ScheduledTaskService` handles its writes.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_skills_capability.py`:

```python
# --- install tests ---

from cubebox.agents.actions.capabilities.skills import (  # noqa: E402
    InstallInput,
    _handle_install_impl,
)
from cubebox.skills.discovery import InstallResult, SkillInstallError  # noqa: E402


@pytest.mark.asyncio
async def test_install_bad_candidate_id_raises_invalid_input() -> None:
    deps = _make_deps()
    with pytest.raises(ActionInvalidInput, match="BAD_CANDIDATE_ID"):
        await _handle_install_impl(
            deps, _ctx(), MagicMock(), InstallInput(candidate_id="!!!bad!!!"),
        )


@pytest.mark.asyncio
async def test_install_success_returns_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from cubebox.skills.sources.base import encode_candidate_id

    fake_svc = MagicMock()
    fake_svc.install = AsyncMock(
        return_value=InstallResult(
            canonical_name="myorg:my-skill",
            skill_id="skl-abc",
            installed_version="1.0.0",
        )
    )
    monkeypatch.setattr(
        _skills_mod, "_SkillInstallService", lambda **_kw: fake_svc,
    )
    monkeypatch.setattr(_skills_mod, "_SkillPublishService", lambda **_kw: MagicMock())

    deps = _make_deps()
    cid = encode_candidate_id("remote", "owner/repo/main/skill", source_id="src-1")
    fake_session = MagicMock()

    result = await _handle_install_impl(
        deps, _ctx(), fake_session, InstallInput(candidate_id=cid),
    )
    assert result == {
        "installed": True,
        "canonical_name": "myorg:my-skill",
        "version": "1.0.0",
    }


@pytest.mark.asyncio
async def test_install_error_raises_invalid_input(monkeypatch: pytest.MonkeyPatch) -> None:
    from cubebox.skills.sources.base import encode_candidate_id

    fake_svc = MagicMock()
    fake_svc.install = AsyncMock(side_effect=SkillInstallError("trust tier too low"))
    monkeypatch.setattr(_skills_mod, "_SkillInstallService", lambda **_kw: fake_svc)
    monkeypatch.setattr(_skills_mod, "_SkillPublishService", lambda **_kw: MagicMock())

    deps = _make_deps()
    cid = encode_candidate_id("remote", "owner/repo/main/skill", source_id="src-1")

    with pytest.raises(ActionInvalidInput, match="trust tier too low"):
        await _handle_install_impl(
            deps, _ctx(), MagicMock(), InstallInput(candidate_id=cid),
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_skills_capability.py -v --no-header 2>&1 | tail -10`
Expected: 3 new install tests fail (NotImplementedError or AttributeError)

- [ ] **Step 3: Implement the `install` handler**

In `cubebox/agents/actions/capabilities/skills.py`, add imports + aliases at the top:

```python
from cubebox.skills.discovery import SkillInstallError, SkillInstallService
from cubebox.skills.service import SkillPublishService

_SkillInstallService = SkillInstallService
_SkillPublishService = SkillPublishService
```

Replace `_handle_install_impl`:

```python
async def _handle_install_impl(
    deps: SkillDeps, ctx: ScopeContext, session: Any, inp: InstallInput,
) -> Any:
    try:
        decode_candidate_id(inp.candidate_id)
    except CandidateIdError as exc:
        raise ActionInvalidInput(f"BAD_CANDIDATE_ID: {exc}") from exc

    publisher = _SkillPublishService(session=session, cache=deps.catalog.cache)
    svc = _SkillInstallService(
        session=session,
        registry=deps.registry,
        publisher=publisher,
        org_id=deps.org_id,
        org_slug=deps.org_slug,
        workspace_id=deps.workspace_id,
        actor_user_id=ctx.user_id,
    )
    try:
        result = await svc.install(inp.candidate_id)
    except SkillInstallError as exc:
        raise ActionInvalidInput(str(exc)) from exc

    return {
        "installed": True,
        "canonical_name": result.canonical_name,
        "version": result.installed_version,
    }
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/unit/test_skills_capability.py -v --no-header 2>&1 | tail -15`
Expected: all install tests pass; all skills_capability tests pass

- [ ] **Step 5: Run mypy**

Run: `cd backend && uv run mypy cubebox/agents/actions/capabilities/skills.py`
Expected: Success

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/agents/actions/capabilities/skills.py backend/tests/unit/test_skills_capability.py
git commit -m "feat(actions): skills.install handler — delegates to SkillInstallService"
```

---

### Task 5: Mutation gate test for the skills capability

**Files:**
- Modify: `backend/tests/unit/test_skills_capability.py`

Add a test that exercises the existing builder to assert: with `allow_mutations=True` the tool exposes `find`/`preview`/`install`; with `allow_mutations=False`, `install` is gone.

- [ ] **Step 1: Write the test**

Append to `backend/tests/unit/test_skills_capability.py`:

```python
# --- mutation gate ---

from collections.abc import AsyncGenerator  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

from cubebox.agents.actions.builder import build_capability_tool  # noqa: E402
from cubebox.agents.actions.capabilities.skills import build_skills_capability  # noqa: E402


@asynccontextmanager
async def _fake_ctx_factory() -> AsyncGenerator[tuple[ScopeContext, Any]]:
    yield (_ctx(), MagicMock())


def test_skills_capability_mutation_gate() -> None:
    deps = _make_deps()
    cap = build_skills_capability(deps)
    # Sanity: 3 operations declared
    assert {op.name for op in cap.operations} == {"find", "preview", "install"}
    assert next(op for op in cap.operations if op.name == "install").mutates is True
    assert next(op for op in cap.operations if op.name == "find").mutates is False
    assert next(op for op in cap.operations if op.name == "preview").mutates is False

    # With mutations allowed, the schema should mention all three ops.
    tool_full = build_capability_tool(cap, _fake_ctx_factory, allow_mutations=True)
    assert tool_full is not None
    schema_full = str(tool_full.parameters.model_json_schema())
    assert "Op_find" in schema_full
    assert "Op_preview" in schema_full
    assert "Op_install" in schema_full

    # Without mutations, install is dropped.
    tool_ro = build_capability_tool(cap, _fake_ctx_factory, allow_mutations=False)
    assert tool_ro is not None
    schema_ro = str(tool_ro.parameters.model_json_schema())
    assert "Op_install" not in schema_ro
    assert "Op_find" in schema_ro
    assert "Op_preview" in schema_ro
```

- [ ] **Step 2: Run the test**

Run: `cd backend && uv run pytest tests/unit/test_skills_capability.py::test_skills_capability_mutation_gate -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/tests/unit/test_skills_capability.py
git commit -m "test(actions): skills capability mutation gate excludes install from automated runs"
```

---

### Task 6: Registry — accept `skill_deps`

**Files:**
- Modify: `backend/cubebox/agents/actions/registry.py`

Extend `tools_for_run` with an optional `skill_deps: SkillDeps | None = None`. When provided, build the skills capability dynamically and append its tool to the list.

- [ ] **Step 1: Update registry.py**

Replace the contents of `cubebox/agents/actions/registry.py` with:

```python
"""Agent capability registry — the single entry point for run_manager."""

from __future__ import annotations

from typing import Any

from cubepi.agent.types import AgentTool

from cubebox.agents.actions.builder import ContextFactory, build_capability_tool
from cubebox.agents.actions.capabilities.scheduled_tasks import SCHEDULED_TASKS_CAPABILITY
from cubebox.agents.actions.capabilities.skills import SkillDeps, build_skills_capability
from cubebox.agents.actions.types import AgentCapability

AGENT_CAPABILITIES: list[AgentCapability] = [
    SCHEDULED_TASKS_CAPABILITY,
]


def tools_for_run(
    context_factory: ContextFactory,
    *,
    allow_mutations: bool,
    skill_deps: SkillDeps | None = None,
) -> list[AgentTool[Any]]:
    """Build agent tools for all registered capabilities.

    Static capabilities (declared in AGENT_CAPABILITIES) are built unconditionally.
    The skills capability is dynamic: built only when skill_deps is supplied,
    because its handlers must close over run-scoped catalog / registry / session.
    """
    tools: list[AgentTool[Any]] = []
    for cap in AGENT_CAPABILITIES:
        tool = build_capability_tool(cap, context_factory, allow_mutations=allow_mutations)
        if tool is not None:
            tools.append(tool)

    if skill_deps is not None:
        skills_cap = build_skills_capability(skill_deps)
        skills_tool = build_capability_tool(
            skills_cap, context_factory, allow_mutations=allow_mutations,
        )
        if skills_tool is not None:
            tools.append(skills_tool)

    return tools
```

- [ ] **Step 2: Verify import**

Run: `cd backend && uv run python -c "from cubebox.agents.actions.registry import tools_for_run, SkillDeps; print('OK')"`

Wait — `SkillDeps` is exported from the capability module, not registry. Verify via the capability module instead:

Run: `cd backend && uv run python -c "from cubebox.agents.actions.registry import tools_for_run; from cubebox.agents.actions.capabilities.skills import SkillDeps; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Run mypy**

Run: `cd backend && uv run mypy cubebox/agents/actions/registry.py`
Expected: Success

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/agents/actions/registry.py
git commit -m "feat(actions): tools_for_run accepts skill_deps for dynamic skills capability"
```

---

### Task 7: Wire `skill_deps` into run_manager and delete bespoke wiring

**Files:**
- Modify: `backend/cubebox/streams/run_manager.py`

Two edits to `_run_cubepi_path`:

1. **Delete the bespoke find/preview/install wiring block** (lines ~1133-1195 — the block that imports `create_find_skills_tool`, `create_install_skill_tool`, `create_preview_skill_tool`, builds `_registry`, and appends the three tools).
2. **Construct `SkillDeps` in the action-tools block** and pass it to `tools_for_run`.

`load_skill` (~lines 1118-1131) stays UNCHANGED.

- [ ] **Step 1: Delete the bespoke block**

Find the block beginning with the comment:

```python
        # find_skills — read-only discovery; needs catalog + a source registry.
        # NOTE: catalog_session is a _run_cubepi_path PARAM, not a local. Guard
        # for None (the catalog DB may be unavailable at run start).
        if skill_catalog is not None and catalog_session is not None:
            try:
                from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
                ...
                    _builtin_tools.append(
                        create_install_skill_tool(install_service_factory=_make_install_factory)
                    )
            except Exception as _exc:  # noqa: BLE001
                logger.warning("find_skills unavailable for cubepi run: {}", _exc)
```

Delete the entire block (~62 lines). Verify the file still parses:

Run: `cd backend && uv run python -c "import cubebox.streams.run_manager; print('OK')"`
Expected: `OK`

- [ ] **Step 2: Add `SkillDeps` construction inside the action-tools try block**

Locate the existing block beginning with the comment:

```python
        # Platform action tools (scheduled_tasks, etc.) — via the capability
        # registry. Automated runs get read-only tools (mutation gate).
```

Inside that `try:` (before the `async with async_session_maker() as _action_session:` block), replace the imports section:

```python
            from cubebox.agents.actions.registry import (
                tools_for_run as _action_tools_for_run,
            )
            from cubebox.repositories.membership import MembershipRepository
```

With this expanded set (additive):

```python
            from cubebox.agents.actions.capabilities.skills import SkillDeps
            from cubebox.agents.actions.registry import (
                tools_for_run as _action_tools_for_run,
            )
            from cubebox.repositories.membership import MembershipRepository
            from cubebox.repositories.organization import OrganizationRepository
            from cubebox.skills.sources.registry import SkillsAdapterManager
```

Then, just before the `_builtin_tools.extend(_action_tools_for_run(...))` call near the bottom of that block, add the SkillDeps construction:

```python
                # Construct SkillDeps only when the skill catalog session is
                # available. Mirrors today's guard: if the catalog DB is
                # unreachable, skills capability is skipped (same as load_skill).
                _skill_deps: SkillDeps | None = None
                if skill_catalog is not None and catalog_session is not None:
                    _org = await OrganizationRepository(catalog_session).get(ctx.org_id)
                    if _org is not None:
                        _registry = await SkillsAdapterManager.build(
                            session=catalog_session,
                            catalog=skill_catalog,
                            org_id=ctx.org_id,
                            org_slug=_org.slug,
                            workspace_id=ctx.workspace_id,
                        )
                        _skill_deps = SkillDeps(
                            catalog=skill_catalog,
                            catalog_session=catalog_session,
                            registry=_registry,
                            org_id=ctx.org_id,
                            org_slug=_org.slug,
                            workspace_id=ctx.workspace_id,
                        )

                _builtin_tools.extend(
                    _action_tools_for_run(
                        _action_ctx_factory,
                        allow_mutations=(trigger == "interactive"),
                        skill_deps=_skill_deps,
                    )
                )
```

Make sure to remove the OLD call (the one without `skill_deps=_skill_deps`).

- [ ] **Step 3: Verify imports + parses**

Run: `cd backend && uv run python -c "import cubebox.streams.run_manager; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Run mypy**

Run: `cd backend && uv run mypy cubebox/streams/run_manager.py`
Expected: Success

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/streams/run_manager.py
git commit -m "refactor(run_manager): wire skills capability via tools_for_run; drop bespoke wiring"
```

---

### Task 8: Delete legacy tool files and obsolete tests

**Files:**
- Delete: `backend/cubebox/tools/builtin/find_skills.py`
- Delete: `backend/cubebox/tools/builtin/preview_skill.py`
- Delete: `backend/cubebox/tools/builtin/install_skill.py`
- Delete: `backend/tests/unit/test_install_skill.py`
- Delete: `backend/tests/unit/test_preview_skill.py`
- Delete: `backend/tests/e2e/test_find_skills_tool.py`

After Task 7, these files have no live imports. Delete them.

- [ ] **Step 1: Confirm no live imports remain**

Run: `cd backend && grep -rn "from cubebox.tools.builtin.find_skills\|from cubebox.tools.builtin.preview_skill\|from cubebox.tools.builtin.install_skill" cubebox tests 2>&1 | grep -v "^Binary file"`
Expected: only references inside the three to-be-deleted test files (or zero results).

If `cubebox/` shows any matches → STOP and re-do Task 7. Else continue.

- [ ] **Step 2: Delete the legacy production files**

```bash
rm backend/cubebox/tools/builtin/find_skills.py
rm backend/cubebox/tools/builtin/preview_skill.py
rm backend/cubebox/tools/builtin/install_skill.py
```

- [ ] **Step 3: Delete the legacy test files**

```bash
rm backend/tests/unit/test_install_skill.py
rm backend/tests/unit/test_preview_skill.py
rm backend/tests/e2e/test_find_skills_tool.py
```

- [ ] **Step 4: Run mypy on the whole backend**

Run: `cd backend && uv run mypy cubebox/`
Expected: `Success: no issues found`

- [ ] **Step 5: Commit**

```bash
git add -A backend/cubebox/tools/builtin/ backend/tests/
git commit -m "chore(actions): delete legacy skill tool factories and their tests"
```

---

### Task 9: Full verification sweep

**Files:** none (verification only)

- [ ] **Step 1: Full mypy**

Run: `cd backend && uv run mypy cubebox/`
Expected: `Success: no issues found`

- [ ] **Step 2: All new unit tests pass**

Run: `cd backend && uv run pytest tests/unit/test_skills_capability.py tests/unit/test_agent_action_builder.py tests/unit/test_scheduled_task_service.py -v --no-header 2>&1 | tail -30`
Expected: all PASS

- [ ] **Step 3: All scheduled-task tests (regression — ensures Task 6 registry change didn't break the other capability)**

Run: `cd backend && uv run pytest tests/e2e/test_scheduled_tasks_api.py tests/e2e/test_scheduled_tasks_firing.py -v --no-header 2>&1 | tail -30`
Expected: all PASS

- [ ] **Step 4: Skills E2E (if any remain after Task 8)**

Run: `cd backend && uv run pytest tests/e2e -k skill -v --no-header 2>&1 | tail -30`
Expected: all PASS (or 0 collected if all skill-tool E2E tests were deleted in Task 8 and no other skill E2E exists)

- [ ] **Step 5: No commit needed unless fixup**

If steps 1-4 all pass cleanly, this task has no commit. Otherwise fix issues and commit a `fix:` before declaring done.

---

## Self-Review

**Spec coverage:**
- Capability shape (1 tool, 3 ops): Task 1 + 2 + 3 + 4
- Mutation gate (`install` only in interactive): Task 5 (test) + Task 4 (declaration)
- Dynamic capability via factory: Task 1 (`build_skills_capability`)
- Registry extension with `skill_deps`: Task 6
- run_manager collapse: Task 7
- Delete legacy files: Task 8
- `load_skill` untouched: Task 7 step 1 (only the find/preview/install block is removed)
- Error mapping (BAD_CANDIDATE_ID, SkillInstallError → ActionInvalidInput): Task 3 + Task 4
- Behavior preservation: Task 3 (port verbatim) + Task 4 (delegate to existing service)

**Placeholder scan:** No TBD / TODO / "fill in later" / "similar to Task N" in any step.

**Type consistency:** `SkillDeps` fields (catalog, catalog_session, registry, org_id, org_slug, workspace_id) appear consistently across Task 1 (definition), Task 4 (install handler usage), Task 6 (registry signature), Task 7 (run_manager construction). `_handle_find_impl` / `_handle_preview_impl` / `_handle_install_impl` are defined in Task 1 and implemented in Tasks 2/3/4 with the same signatures `(deps, ctx, session, input)`. Module-level aliases (`_SkillDiscoveryService`, `_SkillRepository`, `_SkillInstallService`, etc.) used by tests are introduced in the same task that uses them.
