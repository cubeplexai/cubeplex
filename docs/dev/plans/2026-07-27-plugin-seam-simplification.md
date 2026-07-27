# Plugin Seam Simplification (OSS/EE stage 0)

**Goal:** Replace entry-points-based plugin discovery and multi-candidate arbitration with a
single optional import of `cubeplex_ee`, keeping the six Protocols and the CE defaults. This
is stage 0 of the OSS/EE split and a **prerequisite** of stage 1: stage 1's license check
attaches to the import gate this plan creates.

**Architecture:** `plugins/registry.py` keeps the Protocols, the CE defaults, and the getter
surface every call site uses. It loses `discover()`, `resolve_singular`/`resolve_plural`, the
six entry-point group constants, and the `selected`/`disabled` config plumbing — all of which
exist to mediate between an unknown number of third-party plugins from unknown authors, a
population that is now exactly one first-party package on the same release train. In their
place the registry gains explicit `register_*` mutators that `cubeplex_ee.register()` calls,
and `bind_defaults()` fills any slot EE left empty with the CE default.

**Tech Stack:** Python 3.12, FastAPI, mypy strict, pytest. No dependencies added or removed.
No schema changes, no migrations, no frontend changes.

**Spec:** [`docs/dev/specs/2026-07-07-oss-ee-split-design.md`](../specs/2026-07-07-oss-ee-split-design.md)
§9 (what stays, what goes, and why) and §11 (delivery staging).

**Why this is a prerequisite, not a cleanup:** stage 1 Task 3 puts the license check at the
`import cubeplex_ee` gate. Landing stage 1 first would mean writing that check against
`PluginRegistry.discover()` and then rewriting it here — the same code twice, with the
intermediate version never shipping to anyone.

## Global Constraints

- Backend: mypy strict, full type annotations, line length 100.
- Test placement per `docs/testing.md`: pure in-process → `backend/tests/unit/`; anything
  touching the app/DB → `backend/tests/e2e/`. This plan also relocates `backend/tests/plugins/`,
  which currently sits outside the taxonomy altogether.
- **Do not touch `cubeplex/parsers/registry.py`.** It has its own `discover()` and its own
  `cubeplex.parsers` entry-points group for file parsers. It is unrelated to the CE/EE seam,
  it keeps entry-points discovery, and its tests (`tests/parsers/`) must stay green. The same
  applies to any other `*_registry` in the tree — this plan touches `cubeplex/plugins/` only.
- No user-facing behavior changes: a CE deployment behaves identically before and after. No
  `docs/site/` page documents the plugin mechanism (verified 2026-07-27), so the
  docs-ship-with-code rule has nothing to satisfy here; the user-facing editions page arrives
  in stage 1.
- No backwards-compat shims — per `CLAUDE.md`, the project is not publicly shipped, so cut
  over cleanly rather than deprecating.
- Noisy commands (pytest, mypy) → pipe through `tee tmp/<task>.log`, read the tail.
- Execution happens in a worktree; `cat .worktree.env` first — ports 8000/3000 are wrong
  inside worktrees. Plain `uv run pytest` from `backend/` is safe (conftest auto-routes to the
  per-slot test DB).

## What Must Keep Working

The invariants below are what the test suite protects. Every one of them holds today with
zero external plugins installed, and must still hold after this plan:

1. A CE deployment binds `DefaultAuthProvider`, `DefaultPermissionChecker`, at least a
   `DefaultAuditSink`, no `UserDirectorySyncer`s, and at most `DefaultAdminPanelExtension`.
2. `GET /api/v1/admin/_extensions/manifest` is auth-gated and returns `[]` in CE.
3. `require_admin` still routes through `PermissionChecker.check` — member denied 403, admin
   allowed 200.
4. A workspace rename still emits `workspace.renamed` through the audit sink.

`backend/tests/e2e/test_plugin_architecture_e2e.py` (113 lines) already covers all four
through the real app, builds no synthetic wheels, and touches none of the removed API.
**It stays exactly as it is** and is the regression net for this plan. Read it before
starting.

---

### Task 1: Strip discovery and arbitration from the registry

**Files:**
- Modify: `backend/cubeplex/plugins/registry.py`
- Test: `backend/tests/unit/test_plugin_registry.py` (new; replaces four deleted files)

**Interfaces:**
- Removed: `GROUP_MANIFEST`, `GROUP_AUTH`, `GROUP_PERMISSIONS`, `GROUP_AUDIT`,
  `GROUP_DIRECTORY`, `GROUP_ADMIN_PANEL`, `PROTOCOL_GROUPS`, `RESERVED_NAME`,
  `PluginRegistry.discover()`, `PluginRegistry._dist_name()`,
  `PluginRegistry.resolve_singular()`, `PluginRegistry.resolve_plural()`,
  `PluginRegistry._cfg()`, and `bind_defaults`'s `config=` parameter.
- Added (this is the EE-facing surface `cubeplex_ee.register()` uses):
  - `register_auth_provider(provider: object) -> None`
  - `register_permission_checker(checker: object) -> None`
  - `register_audit_sink(sink: object) -> None`
  - `register_user_directory_syncer(syncer: object) -> None`
  - `register_admin_panel_extension(ext: object) -> None`
- Unchanged: `get_auth_provider()`, `get_permission_checker()`, `get_audit_sinks()`,
  `get_user_directory_syncers()`, `get_admin_panel_extensions()`, `get_registry()`,
  `reset_registry_for_tests()`.

Ordering contract: EE registers first, `bind_defaults()` second. `bind_defaults()` fills
only what is still empty, so there is no override semantics and no "who wins" question. The
two singular slots raise on double registration — two auth providers is a bug, not a
configuration to arbitrate.

- [ ] **Step 1: Read the current file and the four tests being replaced**

Read `backend/cubeplex/plugins/registry.py` in full, plus
`backend/tests/plugins/test_registry_singular.py`, `test_registry_plural.py`,
`test_registry_manifest.py`, and `test_registry_getters.py`. The goal is to confirm each
assertion is either (a) about a scenario that can no longer occur, or (b) preserved by the
new test below. Anything that is neither is a missed invariant — stop and reconsider.

- [ ] **Step 2: Write the replacement test**

`backend/tests/unit/test_plugin_registry.py`:

```python
"""Unit: registry binds CE defaults, and honours EE registrations when present."""

import pytest

from cubeplex.plugins.defaults.admin_panel import DefaultAdminPanelExtension
from cubeplex.plugins.defaults.audit import DefaultAuditSink
from cubeplex.plugins.defaults.auth import DefaultAuthProvider
from cubeplex.plugins.defaults.permissions import DefaultPermissionChecker
from cubeplex.plugins.registry import PluginRegistry


class _OtherAuthProvider:
    async def authenticate(self, request: object) -> None:
        return None

    def get_auth_routers(self) -> list[object]:
        return []


class _OtherAuditSink:
    async def record(self, event: object) -> None:
        return None


def test_ce_only_binds_every_default() -> None:
    reg = PluginRegistry()
    reg.bind_defaults()
    assert isinstance(reg.get_auth_provider(), DefaultAuthProvider)
    assert isinstance(reg.get_permission_checker(), DefaultPermissionChecker)
    assert any(isinstance(s, DefaultAuditSink) for s in reg.get_audit_sinks())
    assert reg.get_user_directory_syncers() == []
    assert all(isinstance(e, DefaultAdminPanelExtension) for e in reg.get_admin_panel_extensions())


def test_registered_auth_provider_wins_over_default() -> None:
    reg = PluginRegistry()
    provider = _OtherAuthProvider()
    reg.register_auth_provider(provider)
    reg.bind_defaults()
    assert reg.get_auth_provider() is provider


def test_registered_audit_sink_is_added_alongside_default() -> None:
    reg = PluginRegistry()
    sink = _OtherAuditSink()
    reg.register_audit_sink(sink)
    reg.bind_defaults()
    sinks = reg.get_audit_sinks()
    assert sink in sinks
    assert any(isinstance(s, DefaultAuditSink) for s in sinks)


def test_double_registration_of_singular_slot_raises() -> None:
    reg = PluginRegistry()
    reg.register_auth_provider(_OtherAuthProvider())
    with pytest.raises(RuntimeError, match="already registered"):
        reg.register_auth_provider(_OtherAuthProvider())


def test_getters_before_bind_defaults_raise() -> None:
    reg = PluginRegistry()
    with pytest.raises(RuntimeError, match="bind_defaults"):
        reg.get_auth_provider()
    with pytest.raises(RuntimeError, match="bind_defaults"):
        reg.get_permission_checker()


def test_bind_defaults_is_idempotent() -> None:
    reg = PluginRegistry()
    reg.bind_defaults()
    first = reg.get_auth_provider()
    reg.bind_defaults()
    assert reg.get_auth_provider() is first
```

The last case pins something the current code leaves ambiguous: `plugins/__init__.py`'s
`ensure_registry_bound()` guards on `reg._auth_provider is None` to avoid rebinding. Making
`bind_defaults()` idempotent lets that guard stop reaching into a private attribute (Task 2).

- [ ] **Step 3: Run to verify it fails**

```bash
cd backend && uv run pytest tests/unit/test_plugin_registry.py --no-cov 2>&1 | tee tmp/seam.log | tail -3
```

Expected: FAIL — `AttributeError: 'PluginRegistry' object has no attribute 'register_auth_provider'`.

- [ ] **Step 4: Rewrite `registry.py`**

The whole file becomes roughly this — note the module docstring change (it no longer
discovers anything):

```python
"""CE/EE Protocol binding.

EE (the optional `cubeplex_ee` distribution) calls the ``register_*`` methods during
startup; ``bind_defaults`` then fills every slot EE left empty with the CE default. There
is no discovery step: absence of the EE distribution is what makes a deployment OSS.
See docs/dev/specs/2026-07-07-oss-ee-split-design.md §9.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Holds the resolved implementation of each Protocol."""

    def __init__(self) -> None:
        self._auth_provider: object | None = None
        self._permission_checker: object | None = None
        self._audit_sinks: list[object] = []
        self._user_directory_syncers: list[object] = []
        self._admin_panel_extensions: list[object] = []
        self._bound = False

    # ---- EE registration surface ---- #

    def register_auth_provider(self, provider: object) -> None:
        if self._auth_provider is not None:
            raise RuntimeError("auth_provider already registered")
        self._auth_provider = provider

    def register_permission_checker(self, checker: object) -> None:
        if self._permission_checker is not None:
            raise RuntimeError("permission_checker already registered")
        self._permission_checker = checker

    def register_audit_sink(self, sink: object) -> None:
        self._audit_sinks.append(sink)

    def register_user_directory_syncer(self, syncer: object) -> None:
        self._user_directory_syncers.append(syncer)

    def register_admin_panel_extension(self, ext: object) -> None:
        self._admin_panel_extensions.append(ext)

    # ---- CE fallback ---- #

    def bind_defaults(self) -> None:
        """Fill every slot EE left empty with its CE default. Idempotent."""
        from cubeplex.plugins.defaults.admin_panel import DefaultAdminPanelExtension
        from cubeplex.plugins.defaults.audit import DefaultAuditSink
        from cubeplex.plugins.defaults.auth import DefaultAuthProvider
        from cubeplex.plugins.defaults.permissions import DefaultPermissionChecker

        if self._bound:
            return
        if self._auth_provider is None:
            self._auth_provider = DefaultAuthProvider()
        if self._permission_checker is None:
            self._permission_checker = DefaultPermissionChecker()
        self._audit_sinks.append(DefaultAuditSink())
        self._admin_panel_extensions.append(DefaultAdminPanelExtension())
        self._bound = True

    def is_bound(self) -> bool:
        return self._bound

    # ---- accessors ---- #

    def get_auth_provider(self) -> object:
        if self._auth_provider is None:
            raise RuntimeError("call bind_defaults() first")
        return self._auth_provider

    def get_permission_checker(self) -> object:
        if self._permission_checker is None:
            raise RuntimeError("call bind_defaults() first")
        return self._permission_checker

    def get_audit_sinks(self) -> list[object]:
        return self._audit_sinks

    def get_user_directory_syncers(self) -> list[object]:
        return self._user_directory_syncers

    def get_admin_panel_extensions(self) -> list[object]:
        return self._admin_panel_extensions


# Module-level singleton, populated by app startup.
_registry: PluginRegistry | None = None


def get_registry() -> PluginRegistry:
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
    return _registry


def reset_registry_for_tests() -> None:
    global _registry
    _registry = None
```

Two deliberate behavior changes to call out in the PR description:

- The CE default audit sink and admin-panel extension are always present; the old
  `disabled: ["builtin"]` escape hatch is gone with the config block (Task 3). Nothing in
  the tree used it.
- The instance attributes move from class-level to `__init__`. They were class attributes
  before (`registry.py:165-169`), which meant the mutable list defaults were shared across
  instances — latent cross-test contamination that only went unnoticed because
  `resolve_plural` always reassigned them.

- [ ] **Step 5: Run the new test plus the e2e regression net**

```bash
cd backend && uv run pytest tests/unit/test_plugin_registry.py --no-cov 2>&1 | tee tmp/seam.log | tail -3
uv run pytest tests/e2e/test_plugin_architecture_e2e.py --no-cov 2>&1 | tee -a tmp/seam.log | tail -3
```

Expected: unit green. The e2e run will still fail at this point — `app.py` calls the
now-deleted `discover()`. That is Task 5; do not fix it here.

- [ ] **Step 6: mypy**

```bash
cd backend && uv run mypy cubeplex/plugins/registry.py 2>&1 | tail -2
```

---

### Task 2: Delete `PluginManifest` and the plugin API version

**Files:**
- Modify: `backend/cubeplex/plugins/protocols.py`
- Modify: `backend/cubeplex/plugins/__init__.py`

**Interfaces:**
- Removed: `PluginManifest`, `CUBEPLEX_PLUGIN_API_VERSION`, and both from the package
  `__all__`.
- Changed: `ensure_registry_bound()` stops reading the private `_auth_provider` and uses
  `is_bound()` from Task 1.

Rationale: `PluginManifest` existed to be validated during discovery — every wheel had to
publish one so `discover()` could reject unknown plugins and mismatched API versions. With
one first-party package released from this repository on this repository's version, there is
no provenance to check and no version to negotiate. Keeping an unused dataclass and a
constant nothing reads would be dead code that looks load-bearing.

The other five Protocols and all supporting dataclasses (`PermissionResource`, `AuditEvent`,
`SyncResult`, `SyncSchedule`, `AdminNavItem`) stay untouched.

- [ ] **Step 1: Confirm there are no remaining consumers**

```bash
cd backend && grep -rn "PluginManifest\|CUBEPLEX_PLUGIN_API_VERSION" --include="*.py" cubeplex tests
```

Expected after Task 1 and the Task 4 deletions: hits only in `protocols.py` and
`__init__.py`. If anything else appears, resolve it before deleting.

- [ ] **Step 2: Remove the dataclass, the constant, and the stale docstring**

In `protocols.py`, delete `CUBEPLEX_PLUGIN_API_VERSION` (line 18) and the `PluginManifest`
dataclass (lines 21-28), and rewrite the module docstring — it currently describes the
version-negotiation contract that is going away:

```python
"""Plugin Protocols + supporting dataclasses.

The optional `cubeplex_ee` distribution implements these; CE defaults in
`plugins/defaults/` implement them when EE is absent.
"""
```

Drop the now-unused `Final` from the `typing` import. Leave `field` alone if it is still
used; if the `# noqa: F401` on line 7 becomes the only thing keeping the import, remove the
unused name rather than the noqa.

- [ ] **Step 3: Update the package surface**

In `plugins/__init__.py`: drop `CUBEPLEX_PLUGIN_API_VERSION` and `PluginManifest` from both
the import block and `__all__`, fix the module docstring (it says "entry_points-based
discovery"), and rewrite the helper:

```python
def ensure_registry_bound() -> None:
    """Idempotent: call from app startup or test fixtures to seed CE defaults."""
    reg = get_registry()
    if not reg.is_bound():
        reg.bind_defaults()
```

- [ ] **Step 4: mypy + ruff**

```bash
cd backend && uv run mypy cubeplex/plugins 2>&1 | tail -2
uv run ruff check cubeplex/plugins 2>&1 | tail -3
```

---

### Task 3: Remove the `plugins.*` config block

**Files:**
- Modify: `backend/config.yaml` (lines 296-306)

**Interfaces:**
- Removed config keys: `plugins.auth_provider.selected`,
  `plugins.permission_checker.selected`, `plugins.audit_sink.disabled`,
  `plugins.user_directory_syncer.disabled`, `plugins.admin_panel_extension.disabled`.

These only ever fed `bind_defaults(config=...)`, which Task 1 deleted. Selecting between
implementations by entry-point name has no meaning when there is one implementation.

- [ ] **Step 1: Confirm nothing else reads them**

```bash
cd backend && grep -rn "auth_provider\|permission_checker\|audit_sink\|user_directory_syncer\|admin_panel_extension" \
  --include="*.yaml" --include="*.py" . | grep -v "cubeplex/plugins/\|tests/\|\.venv" | grep -iE "config|selected|disabled"
```

Expected: no hits outside the `config.yaml` block itself. Check
`config.development.yaml` / `config.production.yaml` too if they exist — the grep above
covers them.

- [ ] **Step 2: Delete the block, then verify config still loads**

```bash
cd backend && uv run python -c "from cubeplex.config import config; print(config.get('plugins', 'absent'))"
```

Expected: `absent` (or an empty result) with no exception — dynaconf must not require the key.

- [ ] **Step 3: Commit Tasks 1-3 together**

They are one change — the arbitration layer and the config that drove it.

```bash
cd backend && uv run pytest tests/unit/test_plugin_registry.py --no-cov 2>&1 | tail -3
git add cubeplex/plugins/registry.py cubeplex/plugins/protocols.py cubeplex/plugins/__init__.py \
  config.yaml tests/unit/test_plugin_registry.py
git commit -m "refactor(plugins): replace entry-points discovery with explicit registration"
```

---

### Task 4: Prune the test suite and move it into the taxonomy

**Files:**
- Delete: `backend/tests/plugins/test_contracts.py` (351)
- Delete: `backend/tests/plugins/test_registry_manifest.py` (72)
- Delete: `backend/tests/plugins/test_registry_singular.py` (72)
- Delete: `backend/tests/plugins/test_registry_plural.py` (62)
- Delete: `backend/tests/plugins/test_registry_getters.py` (54)
- Move: `backend/tests/plugins/test_protocols.py` (148) → `backend/tests/unit/`
- Move: `backend/tests/plugins/test_default_auth.py` (25) → `backend/tests/unit/`
- Move: `backend/tests/plugins/test_default_permissions.py` (56) → `backend/tests/unit/`
- Move: `backend/tests/plugins/test_default_audit.py` (31) → `backend/tests/unit/`
- Move: `backend/tests/plugins/test_default_admin_panel.py` (13) → `backend/tests/unit/`
- Move: `backend/tests/plugins/test_audit_helper.py` (29) → `backend/tests/unit/`
- Delete: `backend/tests/plugins/` (the now-empty directory and its `__init__.py`)
- Delete: `backend/tests/fixtures/fake_plugin/` (the in-tree installable fake plugin)
- Modify: `backend/Makefile` (drop the `test-contracts` target + `.PHONY` + help line)
- Modify: `Makefile` (drop `backend-test-contracts` + `.PHONY` + help line)
- Modify: `.github/workflows/ci.yml` (drop the `test-ee-compat` job and its wiring)
- Unchanged: `backend/tests/e2e/test_plugin_architecture_e2e.py`

The last four were **not** in the first draft of this plan and were found while executing it.
`test_contracts.py` does not fake entry-points in-process — it `uv pip install -e`s a real
fixture package, `backend/tests/fixtures/fake_plugin/`, which publishes entry-points in its
`entry_points.txt`, then uninstalls it in fixture teardown. That fixture has no other
consumer. It is also reachable through a dedicated CI job, `test-ee-compat`
("EE Compat (Layer 1 contract tests)"), which runs `make backend-test-contracts` and is
listed in the `report` job's `needs` plus its conclusion loop and summary string. Deleting
the test without unwiring all of that breaks CI. The job was the M-CI placeholder from
`docs/dev/specs/2026-04-21-v1-oss-release-backlog.md`; with no synthetic wheel there is
nothing for it to run. Stage 1 can add a "boots with EE / boots without" check, which
belongs in `backend-e2e` anyway.

The five deletions (611 lines) cover scenarios that can no longer occur: two auth providers
competing for one slot, a plugin missing its manifest, an `api_version` mismatch, a plugin
claiming the reserved `builtin` entry-point name, and `disabled` filtering by entry-point
name. Per `docs/testing.md` these no longer protect a business invariant, so they are deleted
rather than ported. The moves are placement fixes: `tests/plugins/` is outside the
`unit/` / `integration/` / `e2e/` taxonomy, which `CLAUDE.md` warns breaks `make check-ci`.

- [ ] **Step 1: Read `test_contracts.py` before deleting it**

It is the largest file and the only one whose scope is not obvious from its name. Confirm
every test in it is about discovery, manifests, or multi-candidate arbitration. If any test
asserts something about a CE default's *behavior*, port that assertion into the matching
`test_default_*.py` file instead of deleting it.

- [ ] **Step 2: Delete and move**

```bash
cd backend
git rm tests/plugins/test_contracts.py tests/plugins/test_registry_manifest.py \
  tests/plugins/test_registry_singular.py tests/plugins/test_registry_plural.py \
  tests/plugins/test_registry_getters.py
git rm -r tests/fixtures/fake_plugin
git mv tests/plugins/test_protocols.py tests/plugins/test_default_auth.py \
  tests/plugins/test_default_permissions.py tests/plugins/test_default_audit.py \
  tests/plugins/test_default_admin_panel.py tests/plugins/test_audit_helper.py tests/unit/
git rm tests/plugins/__init__.py
rm -rf tests/plugins   # __pycache__ survives git rm
```

Then unwire the build and CI references, and confirm none survive:

```bash
cd .. && grep -rn "test-contracts\|test_contracts\|tests/plugins\|test-ee-compat\|EE_COMPAT" \
  Makefile backend/Makefile .github/
python3 -c "import yaml; d=yaml.safe_load(open('.github/workflows/ci.yml')); print(', '.join(d['jobs']))"
```

Expected: no grep hits, and the job list is `gate, changes, backend-check, frontend-check,
backend-e2e, frontend-e2e, report`.

- [ ] **Step 3: Fix imports in the moved files and run them**

The moved files may import from `tests.plugins.*` helpers or rely on a conftest in the old
directory. Check for both:

```bash
cd backend && ls tests/plugins/ 2>/dev/null; grep -rn "tests.plugins\|tests\.plugins" --include="*.py" tests | head
uv run pytest tests/unit/test_protocols.py tests/unit/test_default_auth.py \
  tests/unit/test_default_permissions.py tests/unit/test_default_audit.py \
  tests/unit/test_default_admin_panel.py tests/unit/test_audit_helper.py --no-cov 2>&1 \
  | tee tmp/seam-tests.log | tail -3
```

Expected: green. `test_protocols.py` asserts `runtime_checkable` conformance of the
Protocols — if it also asserts on `PluginManifest`, drop those cases (Task 2 deleted it).

- [ ] **Step 4: Commit**

```bash
git commit -m "test(plugins): drop discovery-era tests, move the rest into tests/unit"
```

---

### Task 5: Simplify the app startup block

**Files:**
- Modify: `backend/cubeplex/api/app.py` (the plugin block at lines 82-125)

**Interfaces:**
- Removed: `await _reg.discover()` and the `config=_cubeplex_config` argument.
- Produces: the insertion point stage 1 Task 3 attaches `load_ee(_reg)` to.

Everything downstream of `bind_defaults` — mounting `AuthProvider` routers, the
admin-extensions manifest router, each extension's router and static mount — is unchanged.
Discovery is the only thing leaving.

- [ ] **Step 1: Replace the discover/bind pair**

In `backend/cubeplex/api/app.py`, the block currently reads:

```python
    _reg = get_registry()
    await _reg.discover()
    _reg.bind_defaults(config=_cubeplex_config)
```

Replace with:

```python
    _reg = get_registry()
    # EE (the optional cubeplex_ee distribution) registers here in stage 1;
    # bind_defaults then fills every slot EE left empty with the CE default.
    _reg.bind_defaults()
```

Then check whether `_cubeplex_config` is still used elsewhere in the function. If this was
its only consumer, remove the now-orphaned
`from cubeplex.config import config as _cubeplex_config` import — and only that one, per the
surgical-changes rule.

- [ ] **Step 2: Verify the whole plugin path end to end**

```bash
cd backend && uv run pytest tests/e2e/test_plugin_architecture_e2e.py --no-cov 2>&1 | tee tmp/seam-e2e.log | tail -5
```

Expected: all five tests pass — the four invariants from "What Must Keep Working" plus the
auth-gating case. This is the moment the refactor is proven: same app, same behavior, no
discovery.

- [ ] **Step 3: Verify the unrelated parser registry is untouched**

```bash
cd backend && uv run pytest tests/parsers --no-cov 2>&1 | tee -a tmp/seam-e2e.log | tail -3
```

Expected: green. `cubeplex/parsers/registry.py` keeps its own entry-points discovery; a
failure here means the wrong `discover()` was edited.

- [ ] **Step 4: mypy + commit**

```bash
cd backend && uv run mypy cubeplex/api/app.py 2>&1 | tail -2
git add cubeplex/api/app.py
git commit -m "refactor(app): drop plugin discovery from startup"
```

---

### Task 6: Mark the superseded plugin-architecture docs

**Files:**
- Modify: `docs/dev/specs/2026-04-22-ce-ee-plugin-architecture-design.md`
- Modify: `docs/dev/plans/2026-04-23-m0-ce-ee-plugin-architecture.md`

Both describe the entry-points design as current. They are frozen snapshots, so add a
superseded note at the top pointing at
`docs/dev/specs/2026-07-07-oss-ee-split-design.md` §9 and leave the body intact — the same
treatment the April OSS-release backlog received in the stage-1 docs PR.

- [ ] **Step 1: Read both, then add the note**

Match the existing note's wording and placement so the three read consistently. State
plainly what changed: entry-points discovery and multi-candidate arbitration are gone; the
six Protocols and CE defaults remain; EE is one first-party package loaded by optional
import.

- [ ] **Step 2: Commit**

```bash
git add docs/dev/specs/2026-04-22-ce-ee-plugin-architecture-design.md \
  docs/dev/plans/2026-04-23-m0-ce-ee-plugin-architecture.md
git commit -m "docs: mark the entry-points plugin design superseded"
```

---

### Task 7: Pre-PR sweep

- [ ] **Step 1: Full backend check**

```bash
cd backend && uv run mypy cubeplex 2>&1 | tee tmp/sweep-mypy.log | tail -2
uv run pytest tests/unit tests/integration tests/e2e --no-cov 2>&1 | tee tmp/sweep-pytest.log | tail -5
```

Expected: mypy clean, suite green. Watch-items: anything that imported `PluginManifest`,
`CUBEPLEX_PLUGIN_API_VERSION`, or a `GROUP_*` constant; any conftest fixture that called
`discover()`; and `tests/e2e/test_file_read_docling_e2e.py`, which calls `discover()` on the
**parser** registry (lines 69, 94) and must not be touched.

- [ ] **Step 2: Confirm the removal is complete**

```bash
cd backend && grep -rn "resolve_singular\|resolve_plural\|PROTOCOL_GROUPS\|RESERVED_NAME\|plugin_manifest" \
  --include="*.py" cubeplex tests
```

Expected: no hits.

- [ ] **Step 3: Verification evidence, then PR**

Paste the sweep tails into the task report. PR title: a brief description, e.g. "Replace
plugin entry-points discovery with explicit EE registration". Note in the description that
this unblocks stage 1, and list the two behavior changes from Task 1 Step 4 (CE defaults
always present; instance attributes moved off the class). Then run
`/pr-codex-review-loop`.

---

## Self-Review (done at plan time)

- **Spec coverage** vs §9: discovery/arbitration removal ✓ (Tasks 1-3), Protocols and CE
  defaults retained ✓ (Task 1 keeps the getters, Task 2 removes only the two discovery-era
  names), test pruning and taxonomy fix ✓ (Task 4), the import gate's insertion point ✓
  (Task 5). Deferred by design: the gate's license check itself, which is stage 1 Task 3.
- **Correction to §9's test note:** the spec says
  `tests/e2e/test_plugin_architecture_e2e.py` "likely builds synthetic wheels and should
  become 'boots with EE installed / boots without'". Verified on 2026-07-27 — it does not.
  It exercises CE defaults, manifest auth-gating, RBAC, and audit emission through the real
  app, touches none of the removed API, and needs no changes. This plan keeps it and uses it
  as the regression net instead. The "boots with / boots without" e2e belongs to stage 1,
  where there is a license to boot with.
- **A functional argument for the top-level package name**, found while reading `app.py`:
  both `app.py:109` and `admin_extensions.py:27` derive an extension's mount prefix and
  iframe base URL from `type(ext).__module__.split(".")[0]`. With `cubeplex_ee` that yields
  `"cubeplex_ee"`. A `cubeplex.ee` subpackage would yield `"cubeplex"` and collide with core
  in the URL space. Spec §8 presents top-level naming as convention plus a packaging
  constraint; this is a third, concrete reason and it is already load-bearing in shipped
  code.
- **Known risk:** `bind_defaults` becoming idempotent changes `ensure_registry_bound`'s
  guard from "auth provider unset" to "not bound". Any test fixture that called
  `bind_defaults()` twice expecting a fresh rebind would now get the first binding. Task 4
  Step 3's run over the moved tests is where that surfaces; `reset_registry_for_tests()`
  remains the correct way to get a clean registry.
- **Ordering hazard this plan hands to stage 1.** Splitting one `bind_defaults(config=…)`
  call into "EE registers, then defaults fill the gaps" creates an ordering contract that
  did not exist before: registration must happen *before* binding. In production that holds
  trivially — a fresh process, `load_ee()` then `bind_defaults()`. In tests it does not:
  `backend/tests/conftest.py:75-78` is an autouse fixture that calls
  `reset_registry_for_tests()` then `ensure_registry_bound()` for **every** test, so the
  singleton is already bound by the time an app fixture triggers lifespan. Once the dev and
  CI environments actually have `cubeplex-ee` installed — the normal stage-1 setup —
  `load_ee()` would try to register into bound slots.

  This plan makes that raise, with an actionable message, rather than silently replacing or
  silently ignoring. The alternative (let registration overwrite a default) would let EE
  tests pass against CE defaults without anyone noticing, which is a far worse failure than
  a loud startup error. **Stage 1 must therefore adjust that conftest fixture** so the
  registry is bound by the app lifespan rather than ahead of it — most likely by having the
  autouse fixture only reset, and letting unit tests that need a bound registry call
  `ensure_registry_bound()` themselves. Noted here so stage 1 does not discover it as a
  mystery failure.
- **Blast radius:** `cubeplex/plugins/` (4 files), `api/app.py` (one block), `config.yaml`
  (one block), `tests/` (5 deleted, 6 moved, 1 added). No models, no migrations, no routes,
  no frontend.
