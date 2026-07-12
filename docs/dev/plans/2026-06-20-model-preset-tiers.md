# Model preset tiers — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace free-form model presets with four fixed tiers (lite/flash/pro/max, each `enabled` + `primary` + `fallbacks`) plus admin custom presets, a default preset, and task routing — backed by a restructured config file, a clean-cutover migration, and a redesigned admin editor.

**Architecture:** `org_settings.model_presets` JSON holds a `ModelPresetsConfig` (structured: tiers dict + custom_presets + default_preset + task_routing). The snapshot loader flattens it into a uniform `list[ModelPreset]` (key/primary/fallbacks/kind/is_default) so the resolver/builder are key-based and tier-agnostic. Tier descriptions are fixed i18n product copy in the frontend; custom presets carry their own text. The config file seeds only the four tiers.

**Tech Stack:** FastAPI + Pydantic v2 + SQLModel/Alembic (backend), Next.js 16 + React 19 + next-intl (frontend), pytest + vitest.

**Spec:** `docs/dev/specs/2026-06-20-model-preset-tiers-design.md`

**Branch:** work in the current `chore/2026-06-17-drop-org-llm-settings` worktree (no new worktree).

---

## File structure

Backend:
- `backend/cubeplex/llm/snapshot_schema.py` — REWRITE: `ModelTier`, `TaskKey`, `TierSetting`, `CustomPreset`, `ModelPresetsConfig` + validation.
- `backend/cubeplex/llm/snapshot.py` — `LLMPreset`→`ModelPreset` (+ `chain` property + `key`/`kind`); `LLMSnapshot.presets`→`model_presets`, `.task_presets`→`.task_routing`; `_load_presets` flattens config.
- `backend/cubeplex/llm/resolver.py` — `resolve_preset`→`resolve_model_preset` (key-based); `resolve_task_preset` reads `task_routing`.
- `backend/cubeplex/llm/__init__.py` — export renames.
- `backend/cubeplex/streams/run_manager.py`, `backend/cubeplex/llm/builder.py` — update call sites (names only; `.chain` unchanged).
- `backend/cubeplex/seeders/provider_seeder.py` — `seed_default_presets_from_config`→`seed_model_presets_from_config` (reads `llm.model_presets`).
- `backend/cubeplex/api/app.py` — call the renamed seeder.
- `backend/config.yaml`, `backend/config.development.local.yaml` (+ main checkout copy) — `llm.model_presets` block.
- `backend/alembic/versions/<new>_drop_legacy_model_presets_rows.py` — data migration.
- `backend/cubeplex/api/schemas/model_presets.py` — re-export `ModelPresetsConfig`; new `WorkspacePresetSummary` fields.
- `backend/cubeplex/services/model_presets.py` — `write_org_presets` validates primary+fallbacks availability.
- `backend/cubeplex/api/routes/v1/model_presets.py` — workspace route maps `snap.model_presets`.

Frontend:
- `frontend/packages/web/lib/types/presets.ts` — new types.
- `frontend/packages/web/lib/api/presets.ts` — return-type updates.
- `frontend/packages/web/app/admin/presets/PresetEditor.tsx` — REWRITE editor.
- `frontend/packages/web/app/admin/presets/__tests__/page.test.tsx` — update.
- `frontend/packages/web/lib/stores/preset-selection.ts` — `presetLabel`→`modelPresetKey`.
- `frontend/packages/web/components/chat/PresetPicker.tsx` — minimal update to new API shape (full redesign is a separate follow-up).
- `frontend/packages/web/messages/en.json`, `messages/zh.json` — tier/task descriptions + UI strings.

Tests:
- `backend/tests/e2e/test_model_presets_*.py`, `backend/tests/unit/test_model_preset_resolver.py`.

---

## Phase 1 — Backend data model + runtime

### Task 1: New `ModelPresetsConfig` schema + validation

**Files:**
- Rewrite: `backend/cubeplex/llm/snapshot_schema.py`
- Test: `backend/tests/unit/test_model_presets_schema.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/unit/test_model_presets_schema.py
import pytest
from pydantic import ValidationError
from cubeplex.llm.snapshot_schema import ModelPresetsConfig

def _tiers(**over):
    base = {
        "lite": {"enabled": True, "primary": "p/lite"},
        "flash": {"enabled": True, "primary": "p/flash"},
        "pro": {"enabled": True, "primary": "p/pro"},
        "max": {"enabled": False, "primary": None},
    }
    base.update(over)
    return base

def test_valid_config():
    cfg = ModelPresetsConfig.model_validate(
        {"tiers": _tiers(), "default_preset": "pro", "task_routing": {"title": "lite"}}
    )
    assert cfg.default_preset == "pro"
    assert cfg.tiers["max"].enabled is False

def test_missing_tier_key_rejected():
    bad = _tiers(); bad.pop("max")
    with pytest.raises(ValidationError):
        ModelPresetsConfig.model_validate({"tiers": bad, "default_preset": "pro"})

def test_enabled_tier_needs_primary():
    with pytest.raises(ValidationError):
        ModelPresetsConfig.model_validate(
            {"tiers": _tiers(pro={"enabled": True, "primary": None}), "default_preset": "lite"}
        )

def test_default_must_be_available():
    with pytest.raises(ValidationError, match="default_preset"):
        ModelPresetsConfig.model_validate({"tiers": _tiers(), "default_preset": "max"})

def test_custom_label_cannot_collide_with_tier():
    with pytest.raises(ValidationError, match="collides"):
        ModelPresetsConfig.model_validate({
            "tiers": _tiers(), "default_preset": "pro",
            "custom_presets": [{"label": "pro", "primary": "p/x"}],
        })

def test_task_routing_must_be_available():
    with pytest.raises(ValidationError, match="task_routing"):
        ModelPresetsConfig.model_validate(
            {"tiers": _tiers(), "default_preset": "pro", "task_routing": {"summarize": "max"}}
        )

def test_custom_preset_available_as_default_and_task():
    cfg = ModelPresetsConfig.model_validate({
        "tiers": _tiers(), "default_preset": "fast-custom",
        "custom_presets": [{"label": "fast-custom", "primary": "p/c", "description": "hi"}],
        "task_routing": {"title": "fast-custom"},
    })
    assert cfg.default_preset == "fast-custom"
```

- [ ] **Step 2: Run → fails** — `uv run pytest tests/unit/test_model_presets_schema.py -q` → import error.

- [ ] **Step 3: Rewrite the schema**

```python
# backend/cubeplex/llm/snapshot_schema.py
"""Pydantic schema for the OrgSettings.model_presets row value.

Structured authoring shape: four built-in tiers + admin custom presets + a
default + task routing. Tier descriptions are NOT stored here (fixed i18n copy
in the frontend). Ref well-formedness / ref-exists-in-providers is enforced at
write/resolve time, not here.
"""

from enum import Enum
from typing import Self

from pydantic import BaseModel, Field, model_validator

_LABEL_PATTERN = r"^[A-Za-z0-9_-]+$"


class ModelTier(str, Enum):
    lite = "lite"
    flash = "flash"
    pro = "pro"
    max = "max"


class TaskKey(str, Enum):
    title = "title"
    summarize = "summarize"
    compaction = "compaction"


_TIER_NAMES: frozenset[str] = frozenset(t.value for t in ModelTier)


class TierSetting(BaseModel):
    enabled: bool = False
    primary: str | None = None
    fallbacks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _enabled_needs_primary(self) -> Self:
        if self.enabled and not self.primary:
            raise ValueError("an enabled tier must have a primary model")
        return self


class CustomPreset(BaseModel):
    label: str = Field(min_length=1, max_length=64, pattern=_LABEL_PATTERN)
    primary: str = Field(min_length=1)
    fallbacks: list[str] = Field(default_factory=list)
    description: str = ""


class ModelPresetsConfig(BaseModel):
    tiers: dict[ModelTier, TierSetting]
    custom_presets: list[CustomPreset] = Field(default_factory=list)
    default_preset: str
    task_routing: dict[TaskKey, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _invariants(self) -> Self:
        if set(self.tiers.keys()) != set(ModelTier):
            raise ValueError("tiers must contain exactly: lite, flash, pro, max")
        available: set[str] = {
            t.value for t, s in self.tiers.items() if s.enabled and s.primary
        }
        labels = [c.label for c in self.custom_presets]
        if len(set(labels)) != len(labels):
            raise ValueError("custom preset labels must be unique")
        for label in labels:
            if label in _TIER_NAMES:
                raise ValueError(f"custom label {label!r} collides with a tier name")
        available |= set(labels)
        if self.default_preset not in available:
            raise ValueError(
                f"default_preset {self.default_preset!r} is not an available preset"
            )
        for task, key in self.task_routing.items():
            if key not in available:
                raise ValueError(
                    f"task_routing[{task.value!r}]={key!r} is not an available preset"
                )
        return self
```

- [ ] **Step 4: Run → pass** — `uv run pytest tests/unit/test_model_presets_schema.py -q` → all pass.

- [ ] **Step 5: Commit** — `git add backend/cubeplex/llm/snapshot_schema.py backend/tests/unit/test_model_presets_schema.py && git commit -m "feat(llm): model preset tiers schema (tiers + custom + default + task_routing)"`

---

### Task 2: Runtime `ModelPreset` + snapshot flatten

**Files:**
- Modify: `backend/cubeplex/llm/snapshot.py`

- [ ] **Step 1: Replace `LLMPreset`, `LLMSnapshot`, and `_load_presets`**

```python
# snapshot.py — dataclasses
from typing import Literal

@dataclass(frozen=True)
class ModelPreset:
    key: str                       # tier name or custom label
    primary: str
    fallbacks: tuple[str, ...]
    kind: Literal["tier", "custom"]
    is_default: bool

    @property
    def chain(self) -> tuple[str, ...]:
        return (self.primary, *self.fallbacks)


@dataclass(frozen=True)
class LLMSnapshot:
    providers: Mapping[str, ProviderConfig]
    model_presets: tuple[ModelPreset, ...]
    task_routing: Mapping[str, str]    # TaskKey value -> preset key
```

```python
# snapshot.py — load_llm_snapshot body
    providers = await _load_providers(session, org_id, encryption_backend)
    model_presets, task_routing = await _load_presets(session, org_id)
    return LLMSnapshot(
        providers=providers, model_presets=model_presets, task_routing=task_routing
    )
```

```python
# snapshot.py — _load_presets (flatten config -> uniform list)
from cubeplex.llm.snapshot_schema import ModelPresetsConfig, ModelTier

async def _load_presets(
    session: AsyncSession,
    org_id: str,
) -> tuple[tuple[ModelPreset, ...], dict[str, str]]:
    from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings

    org_stmt = select(OrgSettings).where(
        OrgSettings.org_id == org_id,  # type: ignore[arg-type]
        OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
    )
    row = (await session.execute(org_stmt)).scalar_one_or_none()
    if row is None:
        sys_stmt = select(OrgSettings).where(
            OrgSettings.org_id.is_(None),  # type: ignore[union-attr]
            OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
        )
        row = (await session.execute(sys_stmt)).scalar_one_or_none()
    if row is None:
        return (), {}
    try:
        cfg = ModelPresetsConfig.model_validate(row.value)
    except ValidationError as exc:
        raise CorruptPresetsRowError(org_id=row.org_id, errors=exc.errors()) from exc

    presets: list[ModelPreset] = []
    for tier in ModelTier:  # definition order: lite, flash, pro, max
        s = cfg.tiers[tier]
        if not s.enabled or not s.primary:
            continue
        presets.append(
            ModelPreset(
                key=tier.value,
                primary=s.primary,
                fallbacks=tuple(s.fallbacks),
                kind="tier",
                is_default=(cfg.default_preset == tier.value),
            )
        )
    for c in cfg.custom_presets:
        presets.append(
            ModelPreset(
                key=c.label,
                primary=c.primary,
                fallbacks=tuple(c.fallbacks),
                kind="custom",
                is_default=(cfg.default_preset == c.label),
            )
        )
    return tuple(presets), {k.value: v for k, v in cfg.task_routing.items()}
```

- [ ] **Step 2: Update the import line** — change `from cubeplex.llm.snapshot_schema import ModelPresetsValue` to `from cubeplex.llm.snapshot_schema import ModelPresetsConfig, ModelTier`.

- [ ] **Step 3: Commit** — `git commit -am "refactor(llm): ModelPreset + snapshot.model_presets flattening"` (compile checked in Task 4 once call sites are updated).

---

### Task 3: Key-based resolver

**Files:**
- Modify: `backend/cubeplex/llm/resolver.py`, `backend/cubeplex/llm/__init__.py`
- Test: `backend/tests/unit/test_model_preset_resolver.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/unit/test_model_preset_resolver.py
import pytest
from cubeplex.llm.snapshot import LLMSnapshot, ModelPreset
from cubeplex.llm.config import ProviderConfig, ModelConfig  # adjust to actual ctor
from cubeplex.llm.resolver import resolve_model_preset, resolve_task_preset
from cubeplex.llm.errors import UnknownPresetError, NoDefaultPresetError

def _snap():
    prov = {"p": ProviderConfig(slug="p", models=[ModelConfig(id="pro")], base_url="x", api_key="x", api="openai-completions")}  # adjust
    presets = (
        ModelPreset(key="pro", primary="p/pro", fallbacks=(), kind="tier", is_default=True),
    )
    return LLMSnapshot(providers=prov, model_presets=presets, task_routing={"title": "pro"})

def test_resolve_by_key():
    assert resolve_model_preset(_snap(), "pro").key == "pro"

def test_resolve_default_when_none():
    assert resolve_model_preset(_snap(), None).is_default

def test_unknown_key_raises():
    with pytest.raises(UnknownPresetError):
        resolve_model_preset(_snap(), "nope")

def test_chain_property():
    p = ModelPreset(key="x", primary="p/a", fallbacks=("p/b",), kind="custom", is_default=False)
    assert p.chain == ("p/a", "p/b")

def test_task_routing_falls_back_to_default():
    snap = LLMSnapshot(providers=_snap().providers, model_presets=_snap().model_presets, task_routing={})
    assert resolve_task_preset(snap, "title").key == "pro"
```

(Adjust `ProviderConfig`/`ModelConfig` constructors to the real signatures in `cubeplex/llm/config.py`.)

- [ ] **Step 2: Run → fail** — `uv run pytest tests/unit/test_model_preset_resolver.py -q`.

- [ ] **Step 3: Rewrite resolver**

```python
# resolver.py — replace imports + functions
from cubeplex.llm.snapshot import ModelPreset, LLMSnapshot

def resolve_model_preset(snap: LLMSnapshot, key: str | None) -> ModelPreset:
    if key is None:
        preset = next((p for p in snap.model_presets if p.is_default), None)
        if preset is None:
            raise NoDefaultPresetError()
    else:
        preset = next((p for p in snap.model_presets if p.key == key), None)
        if preset is None:
            raise UnknownPresetError(key)
    missing = _missing_refs(preset, snap.providers)
    if missing:
        raise BrokenPresetError(preset.key, missing_refs=missing)
    return preset

def resolve_task_preset(snap: LLMSnapshot, task: str) -> ModelPreset:
    key = snap.task_routing.get(task)
    if key is not None:
        for p in snap.model_presets:
            if p.key == key:
                return p
    return resolve_model_preset(snap, None)

def _missing_refs(preset: ModelPreset, providers: Mapping[str, ProviderConfig]) -> list[str]:
    missing: list[str] = []
    for ref in preset.chain:
        try:
            slug, model_id = ref.split("/", 1)
        except ValueError:
            missing.append(ref); continue
        cfg = providers.get(slug)
        if cfg is None or all(m.id != model_id for m in cfg.models):
            missing.append(ref)
    return missing
```

- [ ] **Step 4: Update `cubeplex/llm/__init__.py`** — `resolve_preset`→`resolve_model_preset` in imports and `__all__`.

- [ ] **Step 5: Run → pass.**

- [ ] **Step 6: Commit** — `git commit -am "refactor(llm): key-based resolve_model_preset + task_routing"`

---

### Task 4: Update call sites (builder, run_manager)

**Files:**
- Modify: `backend/cubeplex/llm/builder.py`, `backend/cubeplex/streams/run_manager.py`

- [ ] **Step 1: Grep + fix** — `grep -rn "resolve_preset\|\.presets\b\|task_presets\|LLMPreset\|\.label" backend/cubeplex/llm backend/cubeplex/streams` and update:
  - `resolve_preset(` → `resolve_model_preset(`
  - `snap.presets` → `snap.model_presets`
  - `snap.task_presets` → `snap.task_routing`
  - `LLMPreset` → `ModelPreset`
  - `preset.label` → `preset.key`
  - `preset.chain` — unchanged (now a property; still a tuple of refs).

- [ ] **Step 2: Whole-repo type-check** — `cd backend && uv run mypy cubeplex 2>&1 | tail -5` → no errors.

- [ ] **Step 3: Commit** — `git commit -am "refactor(llm): update builder/run_manager call sites to ModelPreset"`

---

## Phase 2 — Config + seeder + migration

### Task 5: Restructure config files

**Files:**
- Modify: `backend/config.yaml`, `backend/config.development.local.yaml` (and the main-checkout copy at `/home/chris/cubeplex/backend/config.development.local.yaml`).

- [ ] **Step 1: `config.yaml`** — replace `llm.default_model` / `fallback_models` / `title_model` / `summarize_model` / `compaction.summary_model` (preset-seed part) with:

```yaml
    model_presets:
      tiers:
        lite:  { enabled: true,  primary: "cubeplex/doubao-seed-1.8-thinking", fallbacks: [] }
        flash: { enabled: false, primary: null, fallbacks: [] }
        pro:   { enabled: true,  primary: "cubeplex/doubao-seed-1.8-thinking",
                 fallbacks: ["cubeplex/qwen3.5-plus-thinking"] }
        max:   { enabled: false, primary: null, fallbacks: [] }
      default_preset: pro
      task_routing: {}
```

  (Keep `llm.compaction.*` runtime settings that are NOT the preset seed, e.g. context-window knobs — only remove `summary_model`/`summary_provider` if they only fed the old seeder. Verify with `grep -rn "summary_model\|summary_provider\|title_model\|summarize_model\|default_model\|fallback_models" backend/cubeplex` before deleting; keep any still-read keys.)

- [ ] **Step 2: `config.development.local.yaml`** (both checkouts) — under the existing `dynaconf_merge: false` llm block:

```yaml
    model_presets:
      tiers:
        lite:  { enabled: true, primary: "deepseek/deepseek-v4-flash", fallbacks: [] }
        flash: { enabled: true, primary: "alicode/qwen3.6-plus",
                 fallbacks: ["minimax/MiniMax-M2.7"] }
        pro:   { enabled: true, primary: "vllm/gemma-4-31b-it",
                 fallbacks: ["openrouter/stepfun/step-3.5-flash:free"] }
        max:   { enabled: false, primary: null, fallbacks: [] }
      default_preset: pro
      task_routing: {}
    # default_model / fallback_models removed (superseded by model_presets)
```

- [ ] **Step 3: Verify resolution** — `cd backend && uv run --active python -c "from cubeplex.config import config; print(config.get('llm',{}).get('model_presets'))"` → shows the nested structure.

- [ ] **Step 4: Commit** — `git commit -am "config: restructure llm into model_presets tiers"` (local files are gitignored; only `config.yaml` commits).

---

### Task 6: Seeder reads `llm.model_presets`

**Files:**
- Modify: `backend/cubeplex/seeders/provider_seeder.py`, `backend/cubeplex/api/app.py`, `backend/cubeplex/seeders/__init__.py`
- Test: `backend/tests/e2e/test_model_presets_seed.py`

- [ ] **Step 1: Write failing e2e test** — seeds into a fresh org_settings system row from a config dict.

```python
# backend/tests/e2e/test_model_presets_seed.py
import pytest
from sqlalchemy import select
from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings
from cubeplex.seeders.provider_seeder import seed_model_presets_from_config

@pytest.mark.asyncio
async def test_seed_writes_system_row(db_session, monkeypatch):
    # delete any existing system row first
    await db_session.execute(
        OrgSettings.__table__.delete().where(OrgSettings.key == MODEL_PRESETS_KEY)
    )
    await db_session.commit()
    monkeypatch.setattr(
        "cubeplex.seeders.provider_seeder.settings",
        {"llm": {"model_presets": {
            "tiers": {"lite": {"enabled": True, "primary": "p/l"},
                      "flash": {"enabled": False, "primary": None},
                      "pro": {"enabled": True, "primary": "p/p"},
                      "max": {"enabled": False, "primary": None}},
            "default_preset": "pro", "task_routing": {}}}},
        raising=False,
    )
    await seed_model_presets_from_config(db_session)
    row = (await db_session.execute(select(OrgSettings).where(
        OrgSettings.org_id.is_(None), OrgSettings.key == MODEL_PRESETS_KEY))).scalar_one()
    assert row.value["default_preset"] == "pro"
    assert row.value["tiers"]["pro"]["primary"] == "p/p"
```

(`settings` is a `dict` in the test; the real seeder calls `settings.get("llm", {})` — a plain dict supports `.get`.)

- [ ] **Step 2: Replace the seeder function**

```python
# provider_seeder.py — replace seed_default_presets_from_config
async def seed_model_presets_from_config(session: AsyncSession) -> None:
    """Seed the system OrgSettings.model_presets row from llm.model_presets.
    Idempotent: skip if the system row exists (never clobber admin edits)."""
    from cubeplex.llm.snapshot_schema import ModelPresetsConfig
    from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings

    raw = dict(settings.get("llm", {})).get("model_presets")
    if not raw:
        logger.info("No llm.model_presets in config — skipping preset seed")
        return
    existing = (
        await session.execute(
            select(OrgSettings).where(
                OrgSettings.org_id.is_(None),  # type: ignore[union-attr]
                OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        logger.debug("system model_presets row present — preserving")
        return
    # dynaconf returns Box/DynaBox; model_validate accepts mappings. If a
    # DynaBox slips through model_dump, the json mode below normalises it.
    cfg = ModelPresetsConfig.model_validate(raw)
    session.add(
        OrgSettings(org_id=None, key=MODEL_PRESETS_KEY, value=cfg.model_dump(mode="json"))
    )
    await session.flush()
    await session.commit()
    logger.info("Seeded system model_presets (default=%s)", cfg.default_preset)
```

- [ ] **Step 3: Update `__init__.py` + `app.py`** — export + call `seed_model_presets_from_config` (replace the old name at `app.py:275-277`).

- [ ] **Step 4: Run → pass** — `uv run pytest tests/e2e/test_model_presets_seed.py -q`.

- [ ] **Step 5: Commit** — `git commit -am "feat(seed): seed model_presets tiers from config"`

---

### Task 7: Clean-cutover migration

**Files:**
- Create: `backend/alembic/versions/<rev>_drop_legacy_model_presets_rows.py`

- [ ] **Step 1: Generate an empty revision** (data-only; no schema autogen) — `cd backend && uv run alembic revision -m "drop legacy model_presets rows"`.

- [ ] **Step 2: Fill in the data ops**

```python
def upgrade() -> None:
    # Preset value shape changed incompatibly (tiers/custom/default/task_routing).
    # Old rows would fail validation on read; clear them so the seeder reseeds the
    # system row from config and org admins reconfigure their overrides.
    op.execute("DELETE FROM org_settings WHERE key = 'model_presets'")

def downgrade() -> None:
    pass  # one-way clean cutover
```

- [ ] **Step 3: Apply + verify head is linear** — `uv run alembic upgrade head && uv run alembic heads` → single head.

- [ ] **Step 4: Commit** — `git add backend/alembic/versions/*drop_legacy_model_presets* && git commit -m "migrate: drop legacy model_presets rows (clean cutover)"`

---

## Phase 3 — Admin + workspace API

### Task 8: API schemas

**Files:**
- Modify: `backend/cubeplex/api/schemas/model_presets.py`

- [ ] **Step 1: Re-export config + new workspace summary**

```python
from cubeplex.llm.snapshot_schema import ModelPresetsConfig as AdminModelPresetsBody

class WorkspacePresetSummary(BaseModel):
    key: str
    kind: Literal["tier", "custom"]
    primary: str
    description: str        # "" for tiers (frontend supplies i18n copy by key)
    is_default: bool

class WorkspacePresetsResponse(BaseModel):
    presets: list[WorkspacePresetSummary]
```

(Remove `AdminPresetEntry` re-export; update `__all__`. Update importers that referenced `AdminPresetEntry`.)

- [ ] **Step 2: type-check + commit** — `uv run mypy cubeplex | tail -3 && git commit -am "feat(api): model_presets admin/workspace schemas"`

---

### Task 9: Admin write validation (primary + fallbacks)

**Files:**
- Modify: `backend/cubeplex/services/model_presets.py`
- Test: `backend/tests/e2e/test_admin_model_presets.py`

- [ ] **Step 1: Write failing e2e** — PUT a config whose `pro.primary` ref is unknown → 4xx broken_preset; valid config round-trips.

- [ ] **Step 2: Update `write_org_presets`** — iterate every available preset's `chain` (`primary` + `fallbacks`) and raise `BrokenPresetError` for refs not in `available_models`. Replace the old `for preset in body.presets: for ref in preset.chain` loop with iteration over `body.tiers` (enabled) + `body.custom_presets`, building each `chain = [primary, *fallbacks]`.

```python
def _available_chains(body: AdminModelPresetsBody) -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = []
    for tier, s in body.tiers.items():
        if s.enabled and s.primary:
            out.append((tier.value, [s.primary, *s.fallbacks]))
    for c in body.custom_presets:
        out.append((c.label, [c.primary, *c.fallbacks]))
    return out
```

  Use it to collect `missing` refs; keep the existing `BrokenPresetError` raise + upsert tail.

- [ ] **Step 3: Update `read_org_presets`/`find_preset_refs_to_model`** — parse with `ModelPresetsConfig`; `find_preset_refs_to_model` now scans tiers + custom chains. (Update its return shape only if a consumer needs the kind; otherwise keep `{preset_label, source}` keyed by preset key.)

- [ ] **Step 4: Run → pass; commit** — `git commit -am "feat(api): validate model_presets primary+fallbacks on write"`

---

### Task 10: Workspace route exposes available presets

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/model_presets.py`
- Test: `backend/tests/e2e/test_workspace_model_presets.py`

- [ ] **Step 1: Write failing e2e** — GET returns the enabled tiers + custom, each with `key`/`kind`/`primary`/`is_default`; tier `description == ""`.

- [ ] **Step 2: Map `snap.model_presets`**

```python
    return WorkspacePresetsResponse(
        presets=[
            WorkspacePresetSummary(
                key=p.key, kind=p.kind, primary=p.primary,
                description="",  # tier copy supplied by frontend i18n; custom desc added below
                is_default=p.is_default,
            )
            for p in snap.model_presets
        ],
    )
```

  For custom presets the `description` is not on `ModelPreset` (snapshot dropped it). Decision: add `description: str = ""` to `ModelPreset` and carry it through `_load_presets` (custom → `c.description`, tier → `""`). Update Task 2's dataclass + flatten accordingly, then set `description=p.description` here.

- [ ] **Step 3: Run → pass; commit** — `git commit -am "feat(api): workspace model_presets returns key/kind/primary/description"`

---

## Phase 4 — Frontend

### Task 11: Frontend types + api layer

**Files:**
- Modify: `frontend/packages/web/lib/types/presets.ts`, `frontend/packages/web/lib/api/presets.ts`

- [ ] **Step 1: New types**

```ts
// lib/types/presets.ts
export type ModelTier = 'lite' | 'flash' | 'pro' | 'max'
export type TaskKey = 'title' | 'summarize' | 'compaction'
export const MODEL_TIERS: ModelTier[] = ['lite', 'flash', 'pro', 'max']

export interface TierSetting { enabled: boolean; primary: string | null; fallbacks: string[] }
export interface CustomPreset { label: string; primary: string; fallbacks: string[]; description: string }

export interface ModelPresetsConfig {
  tiers: Record<ModelTier, TierSetting>
  custom_presets: CustomPreset[]
  default_preset: string
  task_routing: Partial<Record<TaskKey, string>>
}

export interface WorkspacePresetSummary {
  key: string
  kind: 'tier' | 'custom'
  primary: string
  description: string
  is_default: boolean
}
```

- [ ] **Step 2: api layer** — `AdminModelPresetsResponse.value: ModelPresetsConfig | null`; `putAdminModelPresets(body: ModelPresetsConfig)`; `fetchWorkspaceModelPresets` returns `WorkspacePresetSummary[]` (already does). Build `@cubeplex/core` if types are shared (`pnpm --filter @cubeplex/core build`).

- [ ] **Step 3: type-check + commit** — `pnpm -C frontend/packages/web type-check && git commit -am "feat(web): model_presets config types"`

---

### Task 12: PresetEditor redesign

**Files:**
- Rewrite: `frontend/packages/web/app/admin/presets/PresetEditor.tsx`

State shape: hold a `ModelPresetsConfig` in `body`; `savedBody` baseline (keep the dirty/discard pattern already in place). Available-preset keys = enabled tiers (with primary) + custom labels.

- [ ] **Step 1: Tiers section** — render `MODEL_TIERS.map(tier => …)`; each row: tier name + read-only i18n description `t(\`modelTiers.${tier}.description\`)`; an enable `<Switch>` bound to `body.tiers[tier].enabled`; a default `<RadioGroup>` value `body.default_preset` (one group across tiers + custom); when enabled, a `<PrimaryFallbackEditor primary fallbacks onChange>` (single primary select + ordered fallbacks list reusing the existing `ChainEditor` autocomplete for both).

- [ ] **Step 2: Custom section** — "Add preset" pushes `{label:'', primary:'', fallbacks:[], description:''}`; each card: label input, description input, `<PrimaryFallbackEditor>`, default radio (value = label), remove.

- [ ] **Step 3: Task routing section (visual cleanup)** — a vertical list, one row per `TaskKey`:

```tsx
{TASK_KEYS.map((task) => (
  <div key={task} className="flex items-center justify-between gap-4 py-2">
    <div className="min-w-0">
      <div className="text-sm font-medium">{t(`taskRouting.${task}.name`)}</div>
      <div className="text-xs text-muted-foreground">{t(`taskRouting.${task}.hint`)}</div>
    </div>
    <Select
      value={body.task_routing[task] ?? NOT_SET}
      onValueChange={(v) => setTaskRouting(task, v === NOT_SET ? undefined : v)}
    >
      <SelectTrigger className="w-56" aria-label={t(`taskRouting.${task}.name`)}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NOT_SET}>
          <span className="text-muted-foreground">
            {t('taskRouting.useDefault', {
              preset: body.default_preset,
              model: primaryName(defaultPrimaryRef),
            })}
          </span>
        </SelectItem>
        {availablePresets.map((p) => (
          <SelectItem key={p.key} value={p.key}>
            {p.key} · {primaryName(p.primary)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  </div>
))}
```

  `primaryName(ref)` resolves `"slug/model_id"` → the model's catalog `name` via the providers list (fetch admin providers, or pass `availableModels` enriched with names). `defaultPrimaryRef` = the primary of `body.default_preset`.

- [ ] **Step 4: Validation gating** — disable Save while a tier is `enabled` with no `primary`, a custom preset has empty label/primary, `default_preset` is not available, or a label collides with a tier name; surface inline messages (reuse the existing duplicate-label pattern).

- [ ] **Step 5: Build + lint + type-check; commit** — `git commit -am "feat(web): tier/custom/task-routing model preset editor"`

---

### Task 13: i18n strings

**Files:**
- Modify: `messages/en.json`, `messages/zh.json`

- [ ] **Step 1: Add keys** under `adminPresets`:

```jsonc
"modelTiers": {
  "lite":  { "name": "Lite",  "description": "Fastest and cheapest. Best for simple, high-volume tasks." },
  "flash": { "name": "Flash", "description": "Fast with solid quality. A balanced everyday choice." },
  "pro":   { "name": "Pro",   "description": "Recommended. Strong reasoning for most work." },
  "max":   { "name": "Max",   "description": "Most capable. For the hardest, highest-stakes tasks." }
},
"taskRouting": {
  "useDefault": "Default · {preset} ({model})",
  "title":     { "name": "Title",     "hint": "Names new conversations." },
  "summarize": { "name": "Summarize", "hint": "Summarizes long content." },
  "compaction":{ "name": "Compaction","hint": "Compresses context when the chat gets long." }
},
"tierEnabled": "Enabled", "primary": "Primary model", "fallbacks": "Fallbacks",
"addCustom": "Add preset", "customLabel": "Label", "customDescription": "Description"
```

  zh mirror (使用上面 spec 表里的中文话术 + 任务说明).

- [ ] **Step 2: i18n parity + commit** — `node frontend/scripts/check-i18n-keys.mjs && git commit -am "i18n: model tier + task routing strings"`

---

### Task 14: preset-selection store + PresetPicker minimal update + page test

**Files:**
- Modify: `frontend/packages/web/lib/stores/preset-selection.ts`, `frontend/packages/web/components/chat/PresetPicker.tsx`, `frontend/packages/web/app/admin/presets/__tests__/page.test.tsx`

- [ ] **Step 1: Store rename** — `presetLabel`→`modelPresetKey`, `setPresetLabel`→`setModelPresetKey`, `presets: WorkspacePresetSummary[]` (now richer). Update all references.

- [ ] **Step 2: PresetPicker** — map over the new `WorkspacePresetSummary` (`p.key` instead of `p.label`); validate persisted `modelPresetKey` against `key`s. Keep current visual (the full ModelPicker redesign is the next follow-up). Send `model_preset_key` (renamed from `preset_label`) wherever the run request includes it — grep `preset_label` across frontend + backend run intake and rename consistently.

- [ ] **Step 3: Fix `page.test.tsx`** — update fixtures to the new `ModelPresetsConfig` GET shape and assert the tiers render (heading level already `h1`).

- [ ] **Step 4: Full frontend suite + commit** — `pnpm -C frontend/packages/web test --run && git commit -am "feat(web): model preset key selection + picker API update"`

---

## Phase 5 — Sweep

### Task 15: Full verification

- [ ] **Step 1: Backend** — `cd backend && make check-ci 2>&1 | tee tmp/be.log | tail -5` → green (mypy + ruff + unit). Then `uv run pytest tests/e2e/test_model_presets_seed.py tests/e2e/test_admin_model_presets.py tests/e2e/test_workspace_model_presets.py tests/unit/test_model_presets_schema.py tests/unit/test_model_preset_resolver.py -q`.
- [ ] **Step 2: Frontend** — `pnpm -C frontend check-ci 2>&1 | tail -5` → green.
- [ ] **Step 3: Manual** — restart backend (reseeds system row from new config), open Model Settings: four tier rows with descriptions, enable toggles, primary+fallbacks editors, default radio, task-routing list with `Default · Pro (…)` empty state. Send a chat to confirm a run resolves the default preset.
- [ ] **Step 4: Commit any fixups.**

---

## Self-review notes

- **Spec coverage:** tiers/custom/default/task_routing (Task 1), primary/fallbacks split (Tasks 1–2, surfaced Task 10/12), runtime flatten + key resolver (Tasks 2–3), naming renames (Tasks 2–4, 8, 11, 14), config restructure (Task 5), seeder (Task 6), clean-cutover migration (Task 7), admin UI incl. task-routing visual (Tasks 12–13), workspace API for the future ModelPicker (Task 10). ModelPicker redesign intentionally deferred (Task 14 keeps it working only).
- **Carry `description` on `ModelPreset`:** Task 10 Step 2 amends Task 2's dataclass to include `description` — apply that when doing Task 2 if implementing in order, or revisit Task 2 at Task 10.
- **Provider constructor names** in Task 3's test are placeholders for the real `cubeplex/llm/config.py` signatures — confirm before writing the test.
