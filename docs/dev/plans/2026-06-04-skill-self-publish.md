# Skill Self-Publish Agent Capability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `publish_artifact` operation to the `skills` agent capability so the agent (including scheduled-task runs) can publish a skill artifact it just created to the current workspace, enabling self-updating skill loops.

**Architecture:** Add `PublishArtifactInput` + `_handle_publish_artifact_impl` to the existing `skills.py` capability module, wire as a 4th `AgentOperation` with `mutates=False` (available to automated runs). Handler constructs `SkillPublishService` from the per-call session + `deps.catalog.cache`, calls `publish_from_artifact`, then looks up the canonical name via `_SkillRepository`. No new files needed.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy async, cubepi `AgentTool`, pytest-asyncio.

**Worktree:** `/home/chris/cubebox/.worktrees/feat/skill-self-publish/backend`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `backend/cubebox/agents/actions/capabilities/skills.py` | Add `PublishArtifactInput`, `_handle_publish_artifact_impl`, new `AgentOperation` in factory |
| Modify | `backend/tests/unit/test_skills_capability.py` | Add 3 tests covering success + error cases |

---

### Task 1: `publish_artifact` operation — TDD

**Files:**
- Modify: `backend/cubebox/agents/actions/capabilities/skills.py`
- Modify: `backend/tests/unit/test_skills_capability.py`

#### What the handler must do

1. Construct `_SkillPublishService(session=session, cache=deps.catalog.cache)`.
2. Call `await publisher.publish_from_artifact(org_id=deps.org_id, org_slug=deps.org_slug, actor_user_id=ctx.user_id, artifact_id=inp.artifact_id, workspace_id=deps.workspace_id)`.
3. On success: look up `skill = await _SkillRepository(session).get(sv.skill_id)` and return `{"published": True, "canonical_name": skill.name, "version": sv.version}`.
4. On `(InvalidFrontmatterError, InvalidSkillNameError, SkillMdMissingError, VersionCollisionError)` → raise `ActionInvalidInput(str(exc))`.

`InvalidFrontmatterError` lives in `cubebox.skills.frontmatter`. The others (`InvalidSkillNameError`, `SkillMdMissingError`, `VersionCollisionError`) live in `cubebox.skills.service`. All four are already importable; `SkillMdMissingError` and `VersionCollisionError` need to be added to the existing `from cubebox.skills.service import` line.

`_SkillRepository` and `_SkillPublishService` module-level aliases already exist in `skills.py` — reuse them.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_skills_capability.py`:

```python
# --- publish_artifact tests ---

from cubebox.agents.actions.capabilities.skills import (  # noqa: E402
    PublishArtifactInput,
    _handle_publish_artifact_impl,
)
from cubebox.skills.service import SkillMdMissingError, VersionCollisionError  # noqa: E402


@pytest.mark.asyncio
async def test_publish_artifact_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_sv = MagicMock()
    fake_sv.skill_id = "skl-1"
    fake_sv.version = "1.0.0"

    fake_publisher = MagicMock()
    fake_publisher.publish_from_artifact = AsyncMock(return_value=fake_sv)
    monkeypatch.setattr(_skills_mod, "_SkillPublishService", lambda **_kw: fake_publisher)

    fake_skill = MagicMock()
    fake_skill.name = "org-slug:my-skill"
    fake_skill_repo = MagicMock()
    fake_skill_repo.get = AsyncMock(return_value=fake_skill)
    monkeypatch.setattr(_skills_mod, "_SkillRepository", lambda _s: fake_skill_repo)

    deps = _make_deps()
    fake_session = MagicMock()

    result = await _handle_publish_artifact_impl(
        deps, _ctx(), fake_session, PublishArtifactInput(artifact_id="art-abc")
    )

    assert result == {
        "published": True,
        "canonical_name": "org-slug:my-skill",
        "version": "1.0.0",
    }
    fake_publisher.publish_from_artifact.assert_awaited_once_with(
        org_id="org-test",
        org_slug="org-slug",
        actor_user_id="usr-test",
        artifact_id="art-abc",
        workspace_id="ws-test",
    )


@pytest.mark.asyncio
async def test_publish_artifact_skill_md_missing_raises_invalid_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_publisher = MagicMock()
    fake_publisher.publish_from_artifact = AsyncMock(
        side_effect=SkillMdMissingError("zip must contain SKILL.md at root")
    )
    monkeypatch.setattr(_skills_mod, "_SkillPublishService", lambda **_kw: fake_publisher)

    deps = _make_deps()
    with pytest.raises(ActionInvalidInput, match="SKILL.md"):
        await _handle_publish_artifact_impl(
            deps, _ctx(), MagicMock(), PublishArtifactInput(artifact_id="art-bad")
        )


@pytest.mark.asyncio
async def test_publish_artifact_version_exists_raises_invalid_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_publisher = MagicMock()
    fake_publisher.publish_from_artifact = AsyncMock(
        side_effect=VersionCollisionError(
            "version 1.0.0 already exists for org-slug:my-skill",
            canonical_name="org-slug:my-skill",
            version="1.0.0",
        )
    )
    monkeypatch.setattr(_skills_mod, "_SkillPublishService", lambda **_kw: fake_publisher)

    deps = _make_deps()
    with pytest.raises(ActionInvalidInput, match="already exists"):
        await _handle_publish_artifact_impl(
            deps, _ctx(), MagicMock(), PublishArtifactInput(artifact_id="art-dup")
        )
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
cd /home/chris/cubebox/.worktrees/feat/skill-self-publish/backend
uv run pytest tests/unit/test_skills_capability.py -k "publish_artifact" -v --no-header 2>&1 | tail -10
```

Expected: 3 tests FAIL with `ImportError` or `AttributeError` (symbols not defined yet).

- [ ] **Step 3: Implement `PublishArtifactInput` and `_handle_publish_artifact_impl`**

In `backend/cubebox/agents/actions/capabilities/skills.py`:

**3a.** Add the missing error imports to the existing `from cubebox.skills.service import` line (line ~36):

```python
from cubebox.skills.service import (
    InvalidSkillNameError,
    SkillCatalogService,
    SkillMdMissingError,
    SkillPublishService,
    VersionCollisionError,
)
```

**3b.** Add `InvalidFrontmatterError` import after the existing `from cubebox.skills.frontmatter import` line:

```python
from cubebox.skills.frontmatter import extract_env_vars, parse_skill_md, InvalidFrontmatterError
```

**3c.** Add `PublishArtifactInput` after `InstallInput` (around line 99):

```python
class PublishArtifactInput(BaseModel):
    artifact_id: str = Field(
        description=(
            "The artifact_id from a save_artifact result. "
            "The artifact must have artifact_type='skill' and contain SKILL.md at its root."
        ),
    )
```

**3d.** Add `_handle_publish_artifact_impl` after `_handle_install_impl` (before `build_skills_capability`):

```python
async def _handle_publish_artifact_impl(
    deps: SkillDeps, ctx: ScopeContext, session: Any, inp: PublishArtifactInput
) -> Any:
    publisher = _SkillPublishService(session=session, cache=deps.catalog.cache)
    try:
        sv = await publisher.publish_from_artifact(
            org_id=deps.org_id,
            org_slug=deps.org_slug,
            actor_user_id=ctx.user_id,
            artifact_id=inp.artifact_id,
            workspace_id=deps.workspace_id,
        )
    except (
        InvalidFrontmatterError,
        InvalidSkillNameError,
        SkillMdMissingError,
        VersionCollisionError,
    ) as exc:
        raise ActionInvalidInput(str(exc)) from exc

    skill = await _SkillRepository(session).get(sv.skill_id)
    canonical_name = skill.name if skill is not None else sv.skill_id

    return {
        "published": True,
        "canonical_name": canonical_name,
        "version": sv.version,
    }
```

**3e.** Wire as a 4th `AgentOperation` inside `build_skills_capability`. Add a closure before the `return AgentCapability(...)`:

```python
    async def publish_artifact_handler(
        ctx: ScopeContext, session: Any, inp: PublishArtifactInput
    ) -> Any:
        return await _handle_publish_artifact_impl(deps, ctx, session, inp)
```

Then append to the `operations=[...]` list:

```python
            AgentOperation(
                name="publish_artifact",
                description=(
                    "Publish a skill artifact to the current workspace so it becomes "
                    "available via load_skill. Use after save_artifact produces an artifact "
                    "with artifact_type='skill'. Available in both interactive and automated runs — "
                    "a scheduled task can self-update its own skill based on observed feedback. "
                    'Example: {"operation":"publish_artifact","artifact_id":"art-1abc..."}'
                ),
                input_model=PublishArtifactInput,
                handler=publish_artifact_handler,
                mutates=False,
            ),
```

- [ ] **Step 4: Run the tests to confirm they pass**

```bash
cd /home/chris/cubebox/.worktrees/feat/skill-self-publish/backend
uv run pytest tests/unit/test_skills_capability.py -v --no-header 2>&1 | tail -15
```

Expected: all 11 tests PASS (8 existing + 3 new).

- [ ] **Step 5: Run mypy**

```bash
uv run mypy cubebox/agents/actions/capabilities/skills.py
```

Expected: `Success: no issues found`

- [ ] **Step 6: Verify mutation gate still correct**

```bash
uv run pytest tests/unit/test_skills_capability.py::test_skills_capability_mutation_gate -v --no-header 2>&1 | tail -5
```

Expected: PASS. (The existing gate test checks `install` is mutating and `find`/`preview` are not — `publish_artifact` is `mutates=False` so it won't break the gate test. It doesn't appear in the gate test's explicit name checks, which is fine.)

- [ ] **Step 7: Commit**

```bash
git add cubebox/agents/actions/capabilities/skills.py tests/unit/test_skills_capability.py
git commit -m "feat(skills): add publish_artifact operation — agent can self-update skills"
```

---

### Task 2: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Full mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/skill-self-publish/backend
uv run mypy cubebox/ 2>&1 | tail -3
```

Expected: `Success: no issues found`

- [ ] **Step 2: Full unit test suite**

```bash
uv run pytest tests/unit/ --no-header -q 2>&1 | tail -5
```

Expected: all pass (previously 1251; now 1254 with 3 new).

- [ ] **Step 3: Scheduled-tasks E2E regression**

```bash
uv run pytest tests/e2e/test_scheduled_tasks_api.py tests/e2e/test_scheduled_tasks_firing.py --no-header -q 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 4: No additional commit** unless failures found.

---

## Self-Review

**Spec coverage:**
- ✅ `publish_artifact` operation added to `skills` capability: Task 1 step 3e
- ✅ `mutates=False` (automated/scheduled runs can call it): Task 1 step 3e
- ✅ Uses `deps.org_id`, `deps.org_slug`, `deps.workspace_id`, `ctx.user_id`: Task 1 step 3d
- ✅ `SkillPublishService(session=session, cache=deps.catalog.cache)`: Task 1 step 3d
- ✅ Error mapping: all 4 exception types → `ActionInvalidInput`: Task 1 step 3d
- ✅ Returns `{published, canonical_name, version}`: Task 1 step 3d
- ✅ JSON example in description: Task 1 step 3e
- ✅ 3 tests (success, SKILL_MD_MISSING, VERSION_EXISTS): Task 1 step 1
- ✅ TDD order (tests first, then implementation): Task 1 steps 1→2→3→4

**Placeholder scan:** No TBD/TODO. All code blocks complete.

**Type consistency:**
- `PublishArtifactInput(artifact_id: str)` defined in step 3c, imported in test step 1. ✓
- `_handle_publish_artifact_impl(deps, ctx, session, inp)` defined in step 3d, imported in test step 1. ✓
- `VersionCollisionError(msg, canonical_name=..., version=...)` — check constructor signature.
