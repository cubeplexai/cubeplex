# Sandbox Scoping + Org Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `UserSandbox` ownership provably isolated per `(org_id, workspace_id, user_id)` and add an org-admin policy (single default image, network egress rules, command rules) that is enforced in the sandbox create + execute paths. Drop `allowed_images` entirely (no override surface in v1, see spec OQ-4/12); reserve a nullable `scope_workspace_id` column for v2 per-workspace overrides.

**Architecture:** Harden `UserSandbox` with a partial unique index over the active states `('provisioning','running')`, and change `get_or_create` to a reserve-row-first flow so a concurrent loser never provisions a provider sandbox (no leak). Key the persistent volume on `(workspace_id, user_id)`. Add an org-only `sandbox_policies` table (direct `org_id` FK + nullable `scope_workspace_id`, **no** `OrgScopedMixin`) with an `OrgSettings`-style repo, a `SandboxPolicyService`/`SandboxPolicyResolver`, and a pure-function matcher module `sandbox_policy/rules.py` that is the single reuse boundary between admin route, manager, and exec middleware. Image + merged network rules are delivered at `Sandbox.create`; image drift is **lazy** (next new conversation picks up the new image; existing sandboxes finish on their original — OQ-5). Command rules are enforced in `_make_execute_tool._execute` with deny → confirm → allow precedence; `confirm` degrades to `deny` in v1 (OQ-1: real HITL is a cubepi-upstream follow-up). Admin routes `GET`/`PUT /api/v1/admin/sandbox-policy` are separate handlers, never workspace-parameterized; the PUT returns a `warnings[]` array on credential-host conflicts (OQ-6) instead of rejecting. A frontend deliverable adds the admin policy editor, a workspace-side read-only sandbox status page, and a credential-editor warning banner.

**Tech Stack:** Python 3.12, FastAPI, SQLModel + Alembic (Postgres prod / SQLite unit driver), `opensandbox` provider SDK, cubepi agent runtime, pytest (E2E under `tests/e2e/`, unit under `tests/unit/`).

---

## File Structure

New files:

- `backend/cubeplex/models/sandbox_policy.py` — `SandboxPolicy` table (org-only, with reserved nullable `scope_workspace_id`).
- `backend/cubeplex/sandbox_policy/__init__.py` — package marker.
- `backend/cubeplex/sandbox_policy/rules.py` — pure matchers: `evaluate_command`, `merge_network_rules`, `split_shell_command`. The single reuse boundary.
- `backend/cubeplex/repositories/sandbox_policy.py` — `SandboxPolicyRepository`, keyed by `org_id` (with override column nullable; modeled on `OrgSettingsRepository`).
- `backend/cubeplex/services/sandbox_policy.py` — `SandboxPolicyService` (CRUD + validation) and `SandboxPolicyResolver` (effective policy / defaults).
- `backend/cubeplex/services/sandbox_policy_conflicts.py` — shared helper for OQ-6 credential-host conflict warnings (used by both admin policy PUT and credential editor routes).
- `backend/cubeplex/api/schemas/sandbox_policy.py` — request/response models (with `warnings: list[str]`).
- `backend/cubeplex/api/routes/v1/admin_sandbox_policy.py` — `GET`/`PUT /admin/sandbox-policy`.
- `backend/cubeplex/api/routes/v1/ws_sandbox.py` — workspace sandbox status GET (read-only).
- `backend/scripts/dev/migrate_user_pvcs.py` — one-time PVC migration helper.
- `backend/tests/unit/test_sandbox_policy_rules.py` — matcher unit tests.
- `backend/tests/unit/test_sandbox_policy_resolver.py` — resolver default tests.
- `backend/tests/unit/test_migrate_user_pvcs.py` — PVC migration planner unit tests.
- `backend/tests/e2e/test_sandbox_policy_routes.py` — admin route E2E (incl. credential-conflict warning).
- `backend/tests/e2e/test_sandbox_scoping.py` — ownership/volume/command-deny/lazy-image-drift E2E.
- `frontend/packages/web/app/(app)/admin/sandbox-policy/page.tsx` + `_components/` — admin policy editor.
- `frontend/packages/web/app/api/v1/admin/sandbox-policy/route.ts` — admin proxy route.
- `frontend/packages/web/app/(app)/w/[wsId]/sandbox/page.tsx` — workspace sandbox status (read-only).
- `frontend/packages/web/app/api/v1/ws/[wsId]/sandbox-status/route.ts` — workspace status proxy route.
- `frontend/packages/web/e2e/sandbox-policy.spec.ts` — Playwright smoke.

Modified files:

- `backend/cubeplex/models/public_id.py` — add `PREFIX_SANDBOX_POLICY = "sbxp"`.
- `backend/cubeplex/models/user_sandbox.py` — add partial unique index + `provisioning` allowed.
- `backend/cubeplex/repositories/user_sandbox.py` — add `reserve`, `promote_to_running`, `delete_record`; widen active query to include `provisioning`; drop "newest wins".
- `backend/cubeplex/sandbox/manager.py` — reserve-row-first create flow; `_build_user_volume(workspace_id, user_id)`; resolve policy for image + merged network rules; recreate on image drift; expose resolved `command_rules` via `get_or_create` return path.
- `backend/cubeplex/middleware/sandbox.py` — `command_rules` + `confirm_v1_as_deny` on `SandboxMiddleware`; enforce in `_execute`.
- `backend/cubeplex/streams/run_manager.py` — pass resolved `command_rules` into `SandboxMiddleware`.
- `backend/cubeplex/api/routes/v1/__init__.py` + `backend/cubeplex/api/app.py` — register the new admin router.
- `backend/alembic/versions/*` — one autogenerated migration (both the policy table and the active unique index land together; see Task 7).

---

## Conventions (read once)

- Run all backend commands from `backend/` inside this worktree. Tests auto-route to the per-slot DB (`tests/conftest.py`), so plain `uv run pytest` never touches your dev DB.
- Line length 100, mypy strict, full type annotations.
- `cd backend && uv run pytest <path> -v` for tests; `uv run mypy cubeplex` for typing.
- Migrations: `uv run alembic revision --autogenerate -m "..."`. Do not hand-write the schema ops; the only manual edit allowed is **appending a data step** to the autogenerated ownership migration (the duplicate-collapse).
- Each task ends in a commit (no `--amend`, no push).

---

## Task 1: Public-ID prefix + `SandboxPolicy` model

**Files:**
- Modify: `backend/cubeplex/models/public_id.py:35`
- Create: `backend/cubeplex/models/sandbox_policy.py`
- Modify: `backend/cubeplex/models/__init__.py`
- Test: `backend/tests/unit/test_sandbox_policy_model.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_sandbox_policy_model.py`:

```python
from cubeplex.models.public_id import PREFIX_SANDBOX_POLICY
from cubeplex.models.sandbox_policy import SandboxPolicy


def test_prefix_value() -> None:
    assert PREFIX_SANDBOX_POLICY == "sbxp"


def test_policy_autofills_prefixed_id() -> None:
    p = SandboxPolicy(org_id="org-1", default_image="ubuntu:22.04")
    assert p.id.startswith("sbxp-")
    assert p.org_id == "org-1"
    assert p.default_image == "ubuntu:22.04"
    # v1 only writes the org-default row (scope_workspace_id=NULL).
    assert p.scope_workspace_id is None
    assert p.network_rules is None
    assert p.command_rules is None


def test_policy_is_not_workspace_scoped_via_mixin() -> None:
    # Org-only table: must NOT carry a REQUIRED workspace_id column from
    # OrgScopedMixin. scope_workspace_id is a separate nullable column reserved
    # for v2 overrides; it is NOT the OrgScopedMixin's workspace_id.
    assert "workspace_id" not in SandboxPolicy.model_fields
    assert "scope_workspace_id" in SandboxPolicy.model_fields
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_sandbox_policy_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'PREFIX_SANDBOX_POLICY'` / `No module named 'cubeplex.models.sandbox_policy'`.

- [ ] **Step 3: Add the prefix**

In `backend/cubeplex/models/public_id.py`, after line 35 (`PREFIX_EGRESS_REF`):

```python
PREFIX_EGRESS_REF: str = "eref"
PREFIX_SANDBOX_POLICY: str = "sbxp"
```

- [ ] **Step 4: Create the model**

Create `backend/cubeplex/models/sandbox_policy.py`:

```python
"""SandboxPolicy — default image, egress rules, command rules.

Org-only table. It declares ``org_id`` as a direct FK and deliberately does
NOT use ``OrgScopedMixin``: that mixin adds a REQUIRED ``workspace_id`` FK,
but a per-org default has no workspace. ``scope_workspace_id`` is a separate
NULLABLE column reserved for v2 per-workspace overrides — v1 only ever writes
NULL (the org-default row). One row per (org, scope) is enforced by a unique
index on ``(org_id, scope_workspace_id)``.
"""

from typing import Any, ClassVar

from sqlalchemy import Column, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase
from cubeplex.models.public_id import PREFIX_SANDBOX_POLICY


class SandboxPolicy(CubeplexBase, table=True):
    _PREFIX: ClassVar[str] = PREFIX_SANDBOX_POLICY
    __tablename__ = "sandbox_policies"
    __table_args__ = (
        Index(
            "uq_sandbox_policy_scope",
            "org_id", "scope_workspace_id",
            unique=True,
        ),
    )

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    # NULL = org-default row (only shape v1 writes). v2 will populate this for
    # per-workspace overrides without a schema migration.
    scope_workspace_id: str | None = Field(
        default=None, foreign_key="workspaces.id", max_length=20, index=True,
    )
    default_image: str = Field(max_length=512)
    # JSONB list of {action, target}; rules are inherently lists (multiple
    # allows/denies); image is a single value because v1 has no override
    # surface to pick one from a list.
    network_rules: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSONB))
    # JSONB list of {action, pattern}. ``action`` ∈ {allow, deny, confirm};
    # confirm degrades to deny at runtime in v1 (see Task 8 + cubepi follow-up).
    command_rules: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSONB))
```

> **Note for SQLite unit tests.** `JSONB` is a Postgres dialect type. The
> `JSON` generic type maps to `JSONB` on Postgres and falls back to TEXT on
> SQLite. If unit tests use SQLite-in-memory, swap `JSONB` for
> `sqlalchemy.types.JSON` here (Postgres still uses JSONB via the dialect
> mapping). Confirm with the existing `sandbox_env` repo's pattern.

Add to `backend/cubeplex/models/__init__.py` (find the existing import/`__all__` block and add):

```python
from cubeplex.models.sandbox_policy import SandboxPolicy
```
and add `"SandboxPolicy"` to `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_sandbox_policy_model.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/models/public_id.py backend/cubeplex/models/sandbox_policy.py backend/cubeplex/models/__init__.py backend/tests/unit/test_sandbox_policy_model.py
git commit -m "feat(sandbox): add SandboxPolicy org-only model + sbxp public-id prefix"
```

---

## Task 2: Command + network rule matcher (pure functions)

**Files:**
- Create: `backend/cubeplex/sandbox_policy/__init__.py`
- Create: `backend/cubeplex/sandbox_policy/rules.py`
- Test: `backend/tests/unit/test_sandbox_policy_rules.py`

The matcher is the one subtle piece (shell chaining), so it earns a unit test per the spec.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_sandbox_policy_rules.py`:

```python
from opensandbox.models.sandboxes import NetworkPolicy, NetworkRule

from cubeplex.sandbox_policy.rules import (
    evaluate_command,
    merge_network_rules,
    split_shell_command,
)

DENY_RM = [{"action": "deny", "pattern": "rm *"}]
CONFIRM_PUSH = [{"action": "confirm", "pattern": "git push *"}]


def test_no_rules_allows() -> None:
    assert evaluate_command("rm -rf /workspace", []) == ("allow", None)


def test_deny_first_match_wins() -> None:
    action, pat = evaluate_command("rm -rf /workspace", DENY_RM)
    assert action == "deny"
    assert pat == "rm *"


def test_allow_when_no_pattern_matches() -> None:
    assert evaluate_command("ls -la", DENY_RM) == ("allow", None)


def test_precedence_deny_beats_confirm_beats_allow() -> None:
    rules = [
        {"action": "allow", "pattern": "git *"},
        {"action": "confirm", "pattern": "git push *"},
        {"action": "deny", "pattern": "git push --force *"},
    ]
    # deny wins even though allow + confirm also match, and is listed last.
    assert evaluate_command("git push --force origin main", rules)[0] == "deny"
    # confirm beats the broad allow.
    assert evaluate_command("git push origin main", rules)[0] == "confirm"
    # only the broad allow matches.
    assert evaluate_command("git status", rules)[0] == "allow"


def test_split_shell_command_operators() -> None:
    assert split_shell_command("safe && rm -rf /") == ["safe", "rm -rf /"]
    assert split_shell_command("safe; denied") == ["safe", "denied"]
    assert split_shell_command("a | b") == ["a", "b"]


def test_substitution_subcommands_are_extracted() -> None:
    assert "denied" in split_shell_command("echo $(denied)")
    assert "denied" in split_shell_command("echo `denied`")


def test_chaining_cannot_smuggle_a_denied_command() -> None:
    # Every sub-command must pass; a denied sub-command denies the whole call.
    assert evaluate_command("safe && rm -rf /", DENY_RM)[0] == "deny"
    assert evaluate_command("ls; rm x", DENY_RM)[0] == "deny"
    assert evaluate_command("echo $(rm x)", DENY_RM)[0] == "deny"


def test_confirm_subcommand_propagates_when_no_deny() -> None:
    assert evaluate_command("ls && git push origin", CONFIRM_PUSH)[0] == "confirm"


def test_merge_network_rules_union_and_deny_wins() -> None:
    base = NetworkPolicy(
        defaultAction="deny",
        egress=[NetworkRule(action="allow", target="api.github.com")],
    )
    admin = [
        {"action": "allow", "target": "pypi.org"},
        {"action": "deny", "target": "api.github.com"},
    ]
    merged = merge_network_rules(base, admin)
    assert merged.default_action == "deny"
    targets = {(r.action, r.target) for r in merged.egress}
    # admin deny on api.github.com removes it from allow and adds an explicit deny
    assert ("allow", "api.github.com") not in targets
    assert ("deny", "api.github.com") in targets
    assert ("allow", "pypi.org") in targets
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_sandbox_policy_rules.py -v`
Expected: FAIL — `No module named 'cubeplex.sandbox_policy'`.

- [ ] **Step 3: Implement the matcher**

Create `backend/cubeplex/sandbox_policy/__init__.py` (empty file):

```python
"""Pure-function sandbox policy matchers (command + network rules)."""
```

Create `backend/cubeplex/sandbox_policy/rules.py`:

```python
"""Pure matchers for sandbox command + network policy rules.

These functions have no I/O and no DB access: they are the single reuse
boundary shared by the admin route (validation), the manager (network merge),
and the exec middleware (command enforcement). Keep them side-effect free.

Command-rule semantics mirror Claude Code permissions: an ordered rule list is
evaluated with precedence deny > confirm > allow, first matching rule per
action-tier wins, and shell chaining cannot smuggle a denied command past an
allow rule — every sub-command of a chained/substituted command line must pass.
"""

from __future__ import annotations

import re
import shlex
from fnmatch import fnmatchcase
from typing import Any, Literal

from opensandbox.models.sandboxes import NetworkPolicy, NetworkRule

CommandAction = Literal["deny", "confirm", "allow"]
_ACTION_RANK: dict[str, int] = {"deny": 0, "confirm": 1, "allow": 2}

# Operators that chain or compose separate commands. Split on these to inspect
# each constituent command independently.
_CHAIN_SPLIT = re.compile(r"&&|\|\||[;\n|&]")
# $(...) and `...` command substitutions.
_SUBST = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")


def split_shell_command(command: str) -> list[str]:
    """Split a command line into the constituent commands a shell would run.

    Handles &&, ||, ;, |, & and newline chaining, plus $(...) / backtick
    substitutions. Returns trimmed non-empty fragments. Best-effort: the goal
    is "no denied command hides inside a chain", not a full shell parser.
    """
    pieces: list[str] = []
    remaining = command

    def _pull_substitutions(text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            inner = m.group(1) if m.group(1) is not None else m.group(2)
            if inner and inner.strip():
                pieces.append(inner.strip())
            return " "

        return _SUBST.sub(repl, text)

    remaining = _pull_substitutions(remaining)
    for frag in _CHAIN_SPLIT.split(remaining):
        frag = frag.strip()
        if frag:
            pieces.append(frag)
    return pieces


def _matches(command: str, pattern: str) -> bool:
    """Glob-match a single command against a rule pattern.

    Match against the whole command line and against the argv form so that
    both ``rm *`` (string glob) and tokenized intent are covered.
    """
    if fnmatchcase(command, pattern):
        return True
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = command.split()
    return bool(argv) and fnmatchcase(" ".join(argv), pattern)


def _eval_single(command: str, rules: list[dict[str, Any]]) -> tuple[CommandAction, str | None]:
    best: tuple[CommandAction, str | None] = ("allow", None)
    best_rank = _ACTION_RANK["allow"]
    for rule in rules:
        action = str(rule.get("action", "allow"))
        pattern = str(rule.get("pattern", ""))
        if action not in _ACTION_RANK or not pattern:
            continue
        if _matches(command, pattern) and _ACTION_RANK[action] < best_rank:
            best = (action, pattern)  # type: ignore[assignment]
            best_rank = _ACTION_RANK[action]
    return best


def evaluate_command(
    command: str, rules: list[dict[str, Any]]
) -> tuple[CommandAction, str | None]:
    """Return the strictest (action, matched_pattern) over every sub-command.

    deny > confirm > allow. No rule match → ("allow", None).
    """
    subcommands = split_shell_command(command) or [command.strip()]
    strongest: tuple[CommandAction, str | None] = ("allow", None)
    strongest_rank = _ACTION_RANK["allow"]
    for sub in subcommands:
        action, pattern = _eval_single(sub, rules)
        if _ACTION_RANK[action] < strongest_rank:
            strongest = (action, pattern)
            strongest_rank = _ACTION_RANK[action]
    return strongest


def merge_network_rules(
    base: NetworkPolicy, admin_rules: list[dict[str, Any]] | None
) -> NetworkPolicy:
    """Compose the vault-derived NetworkPolicy with admin-authored rules.

    Union of allow targets; admin ``deny`` rules win (remove the target from
    allow and add an explicit deny). default_action stays ``deny``.
    """
    base_egress = base.egress or []
    allows: set[str] = {r.target for r in base_egress if r.action == "allow"}
    denies: set[str] = {r.target for r in base_egress if r.action == "deny"}
    for rule in admin_rules or []:
        action = str(rule.get("action", ""))
        target = str(rule.get("target", ""))
        if not target:
            continue
        if action == "deny":
            denies.add(target)
            allows.discard(target)
        elif action == "allow":
            if target not in denies:
                allows.add(target)
    # Emit DENY rules FIRST. The sidecar evaluates `egress` in order, so listing
    # denies ahead of allows makes deny win even when a deny and an allow target
    # OVERLAP via wildcards (e.g. allow=api.evil.com, deny=*.evil.com) — exact-set
    # removal above can't catch wildcard overlap, so ordering is what guarantees
    # deny-wins. Targets are FQDN/wildcard only (OpenSandbox `NetworkRule.target`);
    # the policy service must reject regex/`*`-only targets at write time
    # (validate_host_pattern already does — see Task 3 test
    # `test_service_rejects_bad_network_target`).
    egress = [NetworkRule(action="deny", target=t) for t in sorted(denies)]
    egress += [NetworkRule(action="allow", target=t) for t in sorted(allows)]
    return NetworkPolicy(defaultAction="deny", egress=egress)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_sandbox_policy_rules.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/sandbox_policy/__init__.py backend/cubeplex/sandbox_policy/rules.py backend/tests/unit/test_sandbox_policy_rules.py
git commit -m "feat(sandbox): add pure command/network policy matchers"
```

---

## Task 3: SandboxPolicy repository + resolver + service

**Files:**
- Create: `backend/cubeplex/repositories/sandbox_policy.py`
- Create: `backend/cubeplex/services/sandbox_policy.py`
- Test: `backend/tests/unit/test_sandbox_policy_resolver.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_sandbox_policy_resolver.py`:

```python
import pytest

from cubeplex.services.sandbox_policy import (
    EffectivePolicy,
    SandboxPolicyResolver,
    SandboxPolicyService,
    SandboxPolicyValidationError,
)


class _FakeRepo:
    """In-memory stand-in for SandboxPolicyRepository (org-default row)."""

    def __init__(self) -> None:
        self.row: dict | None = None

    async def get(self):
        return self.row

    async def upsert(self, **fields):
        self.row = {"org_id": "org-1", "scope_workspace_id": None, **fields}
        return self.row


async def test_resolver_returns_defaults_when_no_row() -> None:
    eff = await SandboxPolicyResolver(_FakeRepo(), default_image="ubuntu:22.04").resolve()
    assert isinstance(eff, EffectivePolicy)
    assert eff.default_image == "ubuntu:22.04"
    assert eff.network_rules == []
    assert eff.command_rules == []


async def test_resolver_returns_row_values() -> None:
    repo = _FakeRepo()
    repo.row = {
        "org_id": "org-1",
        "scope_workspace_id": None,
        "default_image": "python:3.12",
        "network_rules": [{"action": "deny", "target": "evil.test"}],
        "command_rules": [{"action": "deny", "pattern": "rm *"}],
    }
    eff = await SandboxPolicyResolver(repo, default_image="ubuntu:22.04").resolve()
    assert eff.default_image == "python:3.12"
    assert eff.command_rules == [{"action": "deny", "pattern": "rm *"}]


async def test_service_rejects_empty_default_image() -> None:
    svc = SandboxPolicyService(_FakeRepo())
    with pytest.raises(SandboxPolicyValidationError):
        await svc.upsert(
            default_image="",
            network_rules=None,
            command_rules=None,
        )


async def test_service_rejects_bad_command_action() -> None:
    svc = SandboxPolicyService(_FakeRepo())
    with pytest.raises(SandboxPolicyValidationError):
        await svc.upsert(
            default_image="ubuntu:22.04",
            network_rules=None,
            command_rules=[{"action": "nuke", "pattern": "rm *"}],
        )


async def test_service_rejects_bad_network_target() -> None:
    svc = SandboxPolicyService(_FakeRepo())
    with pytest.raises(SandboxPolicyValidationError):
        await svc.upsert(
            default_image="ubuntu:22.04",
            network_rules=[{"action": "allow", "target": "*"}],
            command_rules=None,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_sandbox_policy_resolver.py -v`
Expected: FAIL — `No module named 'cubeplex.services.sandbox_policy'`.

- [ ] **Step 3: Implement the repository**

Create `backend/cubeplex/repositories/sandbox_policy.py`:

```python
"""SandboxPolicy repository — keyed on org_id only (no workspace dimension).

Modeled on OrgSettingsRepository, NOT ScopedRepository: the policy table is
org-only and has no workspace_id column.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.sandbox_policy import SandboxPolicy


class SandboxPolicyRepository:
    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self) -> SandboxPolicy | None:
        """Return the org-default row (scope_workspace_id IS NULL)."""
        stmt = (
            select(SandboxPolicy)
            .where(SandboxPolicy.org_id == self.org_id)  # type: ignore[arg-type]
            .where(SandboxPolicy.scope_workspace_id.is_(None))  # type: ignore[union-attr]
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        default_image: str,
        network_rules: list[dict[str, Any]] | None,
        command_rules: list[dict[str, Any]] | None,
    ) -> SandboxPolicy:
        """Upsert the org-default policy row (scope_workspace_id=NULL).

        v2 will add ``upsert_for_workspace(workspace_id, ...)`` for override
        rows without touching this method.
        """
        existing = await self.get()
        if existing is not None:
            existing.default_image = default_image
            existing.network_rules = network_rules
            existing.command_rules = command_rules
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        row = SandboxPolicy(
            org_id=self.org_id,
            scope_workspace_id=None,  # v1 only writes org-default rows
            default_image=default_image,
            network_rules=network_rules,
            command_rules=command_rules,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row
```

- [ ] **Step 4: Implement the service + resolver**

Create `backend/cubeplex/services/sandbox_policy.py`:

```python
"""SandboxPolicy service (CRUD + validation) and resolver (effective policy)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from cubeplex.sandbox_env.host_rules import HostPatternError, validate_host_pattern

_VALID_COMMAND_ACTIONS = {"deny", "confirm", "allow"}
_VALID_NETWORK_ACTIONS = {"allow", "deny"}


class SandboxPolicyValidationError(ValueError):
    """Raised when a submitted policy is malformed."""


class _PolicyRepo(Protocol):
    async def get(self) -> Any: ...
    async def upsert(
        self,
        *,
        default_image: str,
        network_rules: list[dict[str, Any]] | None,
        command_rules: list[dict[str, Any]] | None,
    ) -> Any: ...


@dataclass
class EffectivePolicy:
    default_image: str
    network_rules: list[dict[str, Any]] = field(default_factory=list)
    command_rules: list[dict[str, Any]] = field(default_factory=list)


def _row_field(row: Any, name: str) -> Any:
    return row.get(name) if isinstance(row, dict) else getattr(row, name)


class SandboxPolicyService:
    """CRUD + validation on top of the repo. No allowlist in v1 (OQ-4)."""

    def __init__(self, repo: _PolicyRepo) -> None:
        self._repo = repo

    @staticmethod
    def _validate(
        default_image: str,
        network_rules: list[dict[str, Any]] | None,
        command_rules: list[dict[str, Any]] | None,
    ) -> None:
        if not default_image.strip():
            raise SandboxPolicyValidationError("default_image must not be empty")
        for rule in command_rules or []:
            if rule.get("action") not in _VALID_COMMAND_ACTIONS:
                raise SandboxPolicyValidationError(f"invalid command action: {rule!r}")
            if not str(rule.get("pattern", "")).strip():
                raise SandboxPolicyValidationError(f"command rule needs a pattern: {rule!r}")
        for rule in network_rules or []:
            if rule.get("action") not in _VALID_NETWORK_ACTIONS:
                raise SandboxPolicyValidationError(f"invalid network action: {rule!r}")
            target = str(rule.get("target", ""))
            try:
                validate_host_pattern(target)
            except HostPatternError as exc:
                raise SandboxPolicyValidationError(str(exc)) from exc

    async def get(self) -> Any:
        return await self._repo.get()

    async def upsert(
        self,
        *,
        default_image: str,
        network_rules: list[dict[str, Any]] | None,
        command_rules: list[dict[str, Any]] | None,
    ) -> Any:
        self._validate(default_image, network_rules, command_rules)
        return await self._repo.upsert(
            default_image=default_image,
            network_rules=network_rules,
            command_rules=command_rules,
        )


class SandboxPolicyResolver:
    """Return the effective policy for an org (row or built-in defaults).

    v1 only resolves the org-default row. v2 will gain a ``resolve(*,
    workspace_id)`` overload that prefers a workspace-override row when one
    exists (precedence: workspace override > org default > built-in defaults).
    Until then, the workspace branch is dead code.
    """

    def __init__(self, repo: _PolicyRepo, *, default_image: str) -> None:
        self._repo = repo
        self._default_image = default_image

    async def resolve(self) -> EffectivePolicy:
        row = await self._repo.get()
        if row is None:
            return EffectivePolicy(default_image=self._default_image)
        return EffectivePolicy(
            default_image=_row_field(row, "default_image") or self._default_image,
            network_rules=list(_row_field(row, "network_rules") or []),
            command_rules=list(_row_field(row, "command_rules") or []),
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_sandbox_policy_resolver.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/repositories/sandbox_policy.py backend/cubeplex/services/sandbox_policy.py backend/tests/unit/test_sandbox_policy_resolver.py
git commit -m "feat(sandbox): add SandboxPolicy repo, service, resolver"
```

---

## Task 4: Admin routes `GET`/`PUT /api/v1/admin/sandbox-policy`

**Files:**
- Create: `backend/cubeplex/api/schemas/sandbox_policy.py`
- Create: `backend/cubeplex/api/routes/v1/admin_sandbox_policy.py`
- Modify: `backend/cubeplex/api/routes/v1/__init__.py`
- Modify: `backend/cubeplex/api/app.py:450,478`
- Test: `backend/tests/e2e/test_sandbox_policy_routes.py`

- [ ] **Step 1: Write the failing E2E test**

Create `backend/tests/e2e/test_sandbox_policy_routes.py`:

```python
"""E2E for org-admin sandbox policy routes.

``admin_client`` yields ``(client, workspace_id)`` — unpack before use.
"""


async def test_get_returns_defaults_when_unset(admin_client) -> None:
    client, _ws = admin_client
    resp = await client.get("/api/v1/admin/sandbox-policy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["default_image"]  # a non-empty default
    assert body["network_rules"] == []
    assert body["command_rules"] == []


async def test_put_then_get_roundtrip(admin_client) -> None:
    client, _ws = admin_client
    put = await client.put(
        "/api/v1/admin/sandbox-policy",
        json={
            "default_image": "python:3.12",
            "network_rules": [{"action": "deny", "target": "evil.example.com"}],
            "command_rules": [{"action": "deny", "pattern": "rm *"}],
        },
    )
    assert put.status_code == 200, put.text
    # PUT response includes a warnings array (empty when no conflicts).
    assert put.json().get("warnings") == []
    got = await client.get("/api/v1/admin/sandbox-policy")
    body = got.json()
    assert body["default_image"] == "python:3.12"
    assert body["command_rules"] == [{"action": "deny", "pattern": "rm *"}]


async def test_put_rejects_bad_network_target(admin_client) -> None:
    client, _ws = admin_client
    resp = await client.put(
        "/api/v1/admin/sandbox-policy",
        json={
            "default_image": "ubuntu:22.04",
            "network_rules": [{"action": "allow", "target": "*"}],
            "command_rules": None,
        },
    )
    assert resp.status_code == 400


async def test_put_warns_on_credential_host_conflict(admin_client, seeded_credential) -> None:
    """OQ-6: deny on a host that an installed credential requires returns a
    warnings[] entry, but the PUT is NOT rejected — the policy still saves."""
    client, _ws = admin_client
    # ``seeded_credential`` is a small fixture that inserts one credential whose
    # required_hosts contains 'api.github.com'. Defined in tests/e2e/conftest.py.
    cred_id = seeded_credential
    resp = await client.put(
        "/api/v1/admin/sandbox-policy",
        json={
            "default_image": "ubuntu:22.04",
            "network_rules": [{"action": "deny", "target": "api.github.com"}],
            "command_rules": None,
        },
    )
    assert resp.status_code == 200, resp.text
    warnings = resp.json().get("warnings") or []
    assert any(cred_id in str(w) or "api.github.com" in str(w) for w in warnings)
    # Confirm the policy DID save despite the warning.
    got = await client.get("/api/v1/admin/sandbox-policy")
    assert got.json()["network_rules"] == [
        {"action": "deny", "target": "api.github.com"}
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/e2e/test_sandbox_policy_routes.py -v`
Expected: FAIL — 404 on the new path (router not registered).

- [ ] **Step 3: Add schemas**

Create `backend/cubeplex/api/schemas/sandbox_policy.py`:

```python
"""Request/response schemas for admin sandbox policy."""

from typing import Any

from pydantic import BaseModel


class SandboxPolicyOut(BaseModel):
    default_image: str
    network_rules: list[dict[str, Any]] = []
    command_rules: list[dict[str, Any]] = []
    # OQ-6 soft-conflict warnings (e.g. deny rule covers an installed
    # credential's required host). Empty on GET and on a clean PUT.
    warnings: list[str] = []


class UpdateSandboxPolicyIn(BaseModel):
    default_image: str
    network_rules: list[dict[str, Any]] | None = None
    command_rules: list[dict[str, Any]] | None = None
```

- [ ] **Step 4: Add the router**

Create `backend/cubeplex/api/routes/v1/admin_sandbox_policy.py`:

```python
"""Org-scope sandbox policy routes (org admins only). Org-wide; no ws counterpart."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.sandbox_policy import SandboxPolicyOut, UpdateSandboxPolicyIn
from cubeplex.auth.context import RequestContext
from cubeplex.config import config
from cubeplex.db.session import get_session
from cubeplex.mcp.dependencies import get_admin_request_context
from cubeplex.repositories.sandbox_policy import SandboxPolicyRepository
from cubeplex.services.sandbox_policy import (
    SandboxPolicyResolver,
    SandboxPolicyService,
    SandboxPolicyValidationError,
)

router = APIRouter(prefix="/admin/sandbox-policy", tags=["admin-sandbox-policy"])


def _default_image() -> str:
    return config.get("sandbox.image", "ubuntu:22.04")


@router.get("", response_model=SandboxPolicyOut)
async def get_sandbox_policy(
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> SandboxPolicyOut:
    repo = SandboxPolicyRepository(session, org_id=ctx.org_id)
    eff = await SandboxPolicyResolver(repo, default_image=_default_image()).resolve()
    return SandboxPolicyOut(
        default_image=eff.default_image,
        network_rules=eff.network_rules,
        command_rules=eff.command_rules,
        warnings=[],
    )


def _credential_conflict_warnings(
    network_rules: list[dict[str, Any]] | None,
    installed_creds: list[Any],
) -> list[str]:
    """OQ-6: warn (do NOT reject) when a deny rule covers a host that an
    installed credential declares as required. Returns one warning per match."""
    out: list[str] = []
    deny_targets = {
        str(r.get("target", ""))
        for r in (network_rules or [])
        if r.get("action") == "deny"
    }
    if not deny_targets:
        return out
    for cred in installed_creds:
        required = list(getattr(cred, "required_hosts", []) or [])
        for host in required:
            if host in deny_targets:
                out.append(
                    f"credential {cred.id} ({getattr(cred, 'name', '?')}) requires "
                    f"host {host} which is denied by the policy; outbound calls "
                    f"will be blocked"
                )
    return out


@router.put("", response_model=SandboxPolicyOut)
async def put_sandbox_policy(
    body: UpdateSandboxPolicyIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> SandboxPolicyOut:
    repo = SandboxPolicyRepository(session, org_id=ctx.org_id)
    svc = SandboxPolicyService(repo)
    try:
        row = await svc.upsert(
            default_image=body.default_image,
            network_rules=body.network_rules,
            command_rules=body.command_rules,
        )
    except SandboxPolicyValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # OQ-6: soft-warning on credential-host conflicts. Do NOT reject.
    from cubeplex.repositories.sandbox_env import SandboxEnvRepository
    cred_repo = SandboxEnvRepository(session, org_id=ctx.org_id)
    installed_creds = await cred_repo.list_org_credentials()  # implementer:
    # reuse the existing list method (name may differ — check the repo) that
    # returns rows with .id, .name, .required_hosts.
    warnings = _credential_conflict_warnings(body.network_rules, installed_creds)

    return SandboxPolicyOut(
        default_image=row.default_image,
        network_rules=row.network_rules or [],
        command_rules=row.command_rules or [],
        warnings=warnings,
    )
```

- [ ] **Step 5: Register the router**

In `backend/cubeplex/api/routes/v1/__init__.py`, add `admin_sandbox_policy` to both the import list (near line 9, next to `admin_sandbox_env`) and `__all__` (near line 32).

In `backend/cubeplex/api/app.py`:
- Add `admin_sandbox_policy,` to the import block at line ~450 (next to `admin_sandbox_env,`).
- Add after line ~478 (`app.include_router(admin_sandbox_env.router, prefix="/api/v1")`):

```python
    app.include_router(admin_sandbox_policy.router, prefix="/api/v1")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/e2e/test_sandbox_policy_routes.py -v`
Expected: PASS (4 passed: defaults / roundtrip / bad-target-rejected / credential-conflict-warns). (The table doesn't exist yet in the test DB; the E2E DB is created from metadata, so `SandboxPolicy` being imported in `models/__init__` is enough — the migration in Task 7 is for the running DB.)

- [ ] **Step 7: Symmetric warning on the credential editor route (OQ-6)**

In the existing vault credential editor route (search for `admin_sandbox_env`
or `ws_sandbox_env` PUT handlers that save a credential — pick the one the
credential editor actually calls), add a small after-save check: load the
org's `SandboxPolicy` and, if any `deny` rule's target appears in the saved
credential's `required_hosts`, attach the same shape of warning to the
response (the credential PUT response gains a `warnings: list[str]` field —
mirror what the policy response already does in this task). Do not reject;
just surface the warning. Add an E2E test in the existing credential routes
test file: with a deny rule in place for `api.github.com`, save a credential
whose `required_hosts` includes `api.github.com`, assert the response's
`warnings` array is non-empty. The implementer should reuse
`_credential_conflict_warnings` from this task (move it into a shared module
like `backend/cubeplex/services/sandbox_policy_conflicts.py` if both routes
need to import it).

- [ ] **Step 8: Commit**

```bash
git add backend/cubeplex/api/schemas/sandbox_policy.py backend/cubeplex/api/routes/v1/admin_sandbox_policy.py backend/cubeplex/api/routes/v1/__init__.py backend/cubeplex/api/app.py backend/tests/e2e/test_sandbox_policy_routes.py
git add backend/cubeplex/api/routes/v1/admin_sandbox_env.py backend/cubeplex/api/routes/v1/ws_sandbox_env.py backend/cubeplex/services/sandbox_policy_conflicts.py 2>/dev/null || true
git commit -m "feat(sandbox): admin GET/PUT /admin/sandbox-policy + credential conflict warnings"
```

---

## Task 5: UserSandbox active-state hardening (model + repo)

The model is already keyed per `(org_id, workspace_id, user_id)`; this task adds the partial unique index over the active states and the reserve/promote/delete repo methods. The `get_or_create` rewrite is Task 6.

**Files:**
- Modify: `backend/cubeplex/models/user_sandbox.py:23-34`
- Modify: `backend/cubeplex/repositories/user_sandbox.py`
- Test: `backend/tests/unit/test_user_sandbox_repo.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_user_sandbox_repo.py`. It uses the existing async in-memory session fixture pattern; if none exists, build the engine inline:

```python
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from cubeplex.models import SQLModel  # re-exported metadata
from cubeplex.repositories.user_sandbox import UserSandboxRepository


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    async with AsyncSession(engine) as s:
        yield s
    await engine.dispose()


async def _seed_org_ws_user(session: AsyncSession) -> None:
    # Minimal parent rows so FKs resolve. SQLite has FKs off by default, but
    # insert them anyway to mirror prod shape.
    await session.execute(
        sa.text("INSERT INTO organizations (id, name, created_at, updated_at) "
                "VALUES ('org-1','o',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    )
    await session.commit()


async def test_reserve_then_active_query_includes_provisioning(session) -> None:
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    rec = await repo.reserve(user_id="user-1", image="ubuntu:22.04", ttl_seconds=600)
    assert rec.status == "provisioning"
    active = await repo.get_active_by_user("user-1")
    assert active is not None and active.id == rec.id


async def test_second_reserve_for_same_identity_raises(session) -> None:
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    await repo.reserve(user_id="user-1", image="ubuntu:22.04", ttl_seconds=600)
    with pytest.raises(Exception):
        await repo.reserve(user_id="user-1", image="ubuntu:22.04", ttl_seconds=600)


async def test_promote_sets_running_and_sandbox_id(session) -> None:
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    rec = await repo.reserve(user_id="user-1", image="ubuntu:22.04", ttl_seconds=600)
    await repo.promote_to_running(rec.id, sandbox_id="prov-abc")
    refreshed = await repo.get(rec.id)
    assert refreshed is not None
    assert refreshed.status == "running"
    assert refreshed.sandbox_id == "prov-abc"


async def test_delete_record_frees_the_slot(session) -> None:
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    rec = await repo.reserve(user_id="user-1", image="ubuntu:22.04", ttl_seconds=600)
    await repo.delete_record(rec.id)
    # Slot free again — a fresh reserve must succeed.
    again = await repo.reserve(user_id="user-1", image="ubuntu:22.04", ttl_seconds=600)
    assert again.status == "provisioning"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_user_sandbox_repo.py -v`
Expected: FAIL — `AttributeError: 'UserSandboxRepository' object has no attribute 'reserve'` and the second-reserve test fails (no unique index yet).

- [ ] **Step 3: Add the partial unique index + provisioning placeholder sandbox_id**

In `backend/cubeplex/models/user_sandbox.py`, update imports and `__table_args__`:

```python
from sqlalchemy import Column, Index, text
```

```python
    __table_args__ = (
        Index("ix_user_sandboxes_user_ws_status", "user_id", "workspace_id", "status"),
        Index("ix_user_sandboxes_org_ws", "org_id", "workspace_id"),
        Index(
            "uq_user_sandbox_active",
            "org_id",
            "workspace_id",
            "user_id",
            unique=True,
            postgresql_where="status IN ('provisioning','running')",
            sqlite_where=text("status IN ('provisioning','running')"),
        ),
    )
```

A reserved row has no provider id yet but `sandbox_id` is `unique=True` NOT NULL. Make the reserve method mint a unique placeholder (`pending-<id>`) so the column constraint holds before `Sandbox.create` returns the real id; `promote_to_running` overwrites it.

- [ ] **Step 4: Add repo methods + widen the active query**

In `backend/cubeplex/repositories/user_sandbox.py`, replace `get_active_by_user` and add the new methods:

```python
    _ACTIVE_STATUSES = ("provisioning", "running")

    async def reserve(
        self,
        *,
        user_id: str,
        image: str,
        volumes_config: dict[str, Any] | None = None,
        ttl_seconds: int = 3600,
    ) -> UserSandbox:
        """Insert a provisioning placeholder row BEFORE provider create.

        The partial unique index over ('provisioning','running') makes a
        concurrent second reserve for the same identity raise an IntegrityError,
        so the loser never provisions a provider sandbox. ``sandbox_id`` gets a
        unique ``pending-<row id>`` placeholder until promote overwrites it.
        """
        record = UserSandbox(
            user_id=user_id,
            sandbox_id="",  # set below once the row id is minted
            status="provisioning",
            image=image,
            volumes_config=volumes_config,
            ttl_seconds=ttl_seconds,
        )
        record.sandbox_id = f"pending-{record.id}"
        return await self.add(record)

    async def promote_to_running(self, record_id: str, *, sandbox_id: str) -> None:
        record = await self.get(record_id)
        if record is None:
            raise ValueError(f"sandbox record {record_id} vanished mid-create")
        record.sandbox_id = sandbox_id
        record.status = "running"
        await self.session.commit()

    async def delete_record(self, record_id: str) -> None:
        await self.delete(record_id)

    async def get_active_by_user(self, user_id: str) -> UserSandbox | None:
        """Return the active (provisioning OR running) sandbox for this user.

        The partial unique index guarantees at most one active row per identity,
        so no ``order_by(... desc()).limit(1)`` "newest wins" is needed.
        """
        stmt = (
            self._scoped_select()
            .where(UserSandbox.user_id == user_id)
            .where(UserSandbox.status.in_(self._ACTIVE_STATUSES))  # type: ignore[attr-defined]
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
```

Keep the existing `create(...)` method for any non-reserve callers, but the manager will switch to `reserve` + `promote_to_running` in Task 6.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_user_sandbox_repo.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/models/user_sandbox.py backend/cubeplex/repositories/user_sandbox.py backend/tests/unit/test_user_sandbox_repo.py
git commit -m "feat(sandbox): partial unique active index + reserve/promote repo methods"
```

---

## Task 6: Reserve-row-first create flow, PVC volume rename, policy delivery

**Files:**
- Modify: `backend/cubeplex/sandbox/manager.py:90-106` (volume), `156-309` (get_or_create)
- Test: `backend/tests/unit/test_sandbox_manager_create.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_sandbox_manager_create.py`. Drive the manager against a fake `opensandbox.Sandbox` and the real (sqlite) repo to assert: reserve-before-create ordering, volume claim name carries the workspace, and a concurrent second create reuses rather than leaking.

```python
import opensandbox
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cubeplex.models import SQLModel
from cubeplex.sandbox.manager import SandboxManager


class _FakeRaw:
    _counter = 0

    def __init__(self) -> None:
        _FakeRaw._counter += 1
        self.id = f"prov-{_FakeRaw._counter}"

    async def is_healthy(self) -> bool:
        return True

    async def close(self) -> None:
        return None


@pytest.fixture
async def session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def fake_create(image, **kwargs):  # noqa: ANN001
        fake_create.calls += 1
        fake_create.last_volumes = kwargs.get("volumes")
        return _FakeRaw()

    fake_create.calls = 0
    fake_create.last_volumes = None

    async def fake_connect(sandbox_id, **kwargs):  # noqa: ANN001
        return _FakeRaw()

    monkeypatch.setattr(opensandbox.Sandbox, "create", staticmethod(fake_create))
    monkeypatch.setattr(opensandbox.Sandbox, "connect", staticmethod(fake_connect))
    yield factory, fake_create
    await engine.dispose()


async def test_volume_claim_name_carries_workspace(session_factory, monkeypatch):
    factory, fake_create = session_factory
    mgr = SandboxManager(factory)
    monkeypatch.setattr(mgr, "_volume_enabled", True)
    await mgr.get_or_create("user-1", org_id="org-1", workspace_id="ws-A")
    vols = fake_create.last_volumes
    assert vols is not None
    claim = vols[0].pvc.claim_name
    assert "ws-a" in claim and "user-1" in claim


async def test_same_user_two_workspaces_get_distinct_claims(session_factory, monkeypatch):
    factory, fake_create = session_factory
    mgr = SandboxManager(factory)
    monkeypatch.setattr(mgr, "_volume_enabled", True)
    await mgr.get_or_create("user-1", org_id="org-1", workspace_id="ws-A")
    claim_a = fake_create.last_volumes[0].pvc.claim_name
    await mgr.get_or_create("user-1", org_id="org-1", workspace_id="ws-B")
    claim_b = fake_create.last_volumes[0].pvc.claim_name
    assert claim_a != claim_b


async def test_create_reserves_before_provider_create(session_factory):
    factory, fake_create = session_factory
    mgr = SandboxManager(factory)
    await mgr.get_or_create("user-1", org_id="org-1", workspace_id="ws-A")
    assert fake_create.calls == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_sandbox_manager_create.py -v`
Expected: FAIL — `_build_user_volume` takes one arg / claim name lacks the workspace.

- [ ] **Step 3: Rename the volume builder to carry the workspace**

Replace `_build_user_volume` in `backend/cubeplex/sandbox/manager.py`:

```python
    def _build_user_volume(self, workspace_id: str, user_id: str) -> Volume:
        """Build a PVC Volume keyed on (workspace_id, user_id).

        Keying on the workspace too is the storage half of the ownership
        boundary the unique index enforces in the DB: the same user in two
        workspaces must never mount the same /workspace PVC.
        """
        raw = f"ws-{workspace_id}-user-{user_id}"
        sanitized = re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-")
        if not sanitized:
            sanitized = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

        max_suffix_len = 63 - len(self._volume_pvc_prefix) - 1
        if len(sanitized) > max_suffix_len:
            sanitized = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

        pvc_name = f"{self._volume_pvc_prefix}-{sanitized}"
        return Volume(
            name="user-workspace",
            pvc=PVC(claimName=pvc_name),
            mountPath=self._volume_mount_path,
            readOnly=False,
        )
```

- [ ] **Step 4: Rewrite the create path (reserve-row-first + policy delivery)**

In `get_or_create`, after the reuse branch (which now also covers `provisioning` rows via `get_active_by_user`), replace the create block. Add the policy resolve at the top of the `async with self._session_factory()` block and use the rules matcher for the network merge.

**Image drift is LAZY (OQ-5).** Existing running sandboxes keep their original image until they terminate normally (TTL or conversation end); they are NOT torn down because the admin changed `default_image`. The new image is only used by sandboxes created *after* the change. Concretely: the running-reuse branch reuses the sandbox unconditionally on image mismatch (just logs a one-line info), and only the create-new branch reads `policy.default_image`. No mid-life image swap.

```python
        from cubeplex.repositories.sandbox_policy import SandboxPolicyRepository
        from cubeplex.sandbox_policy.rules import merge_network_rules
        from cubeplex.services.sandbox_policy import SandboxPolicyResolver

        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            policy = await SandboxPolicyResolver(
                SandboxPolicyRepository(session, org_id=org_id),
                default_image=self._image,
            ).resolve()
            record = await repo.get_active_by_user(user_id)

            if record and record.status == "running":
                # LAZY image drift: log only; keep reusing the existing sandbox.
                # The new image takes effect on the NEXT new-conversation create.
                if record.image != policy.default_image:
                    logger.info(
                        "Image drift detected (sandbox={} on={}, policy now={}); "
                        "reusing existing sandbox; new image takes effect on next "
                        "new conversation",
                        record.sandbox_id, record.image, policy.default_image,
                    )
                # existing connect / is_healthy / _apply_egress reuse logic,
                # unchanged: just reuse the running sandbox regardless of drift.
                ...
```

Then the create-new block becomes reserve-first:

```python
            # Reserve the row BEFORE provider create. A concurrent loser's reserve
            # raises IntegrityError; it never provisions a provider sandbox.
            try:
                reserved = await repo.reserve(
                    user_id=user_id,
                    image=policy.default_image,
                    ttl_seconds=self._ttl,
                )
            except Exception:
                await session.rollback()
                # Lost the race. The winner may still be `provisioning` (it hasn't
                # called promote_to_running yet), so poll the active row until it
                # reaches `running` or a bounded timeout elapses — do NOT raise on a
                # provisioning winner. Re-query in a fresh transaction each loop so we
                # see the winner's committed promotion.
                deadline = time.monotonic() + self._reserve_wait_timeout  # e.g. 30s
                winner = await repo.get_active_by_user(user_id)
                while (
                    winner is not None
                    and winner.status == "provisioning"
                    and time.monotonic() < deadline
                ):
                    await asyncio.sleep(self._reserve_poll_interval)  # e.g. 0.5s
                    await session.rollback()  # drop the snapshot before re-reading
                    winner = await repo.get_active_by_user(user_id)
                if winner is not None and winner.status == "running":
                    raw_sandbox = await opensandbox.Sandbox.connect(
                        winner.sandbox_id, connection_config=conn_config
                    )
                    return OpenSandbox(sandbox=raw_sandbox, workdir=self._workdir)
                raise SandboxError("concurrent create lost the race with no usable winner")

            volumes: list[Volume] | None = None
            if self._volume_enabled:
                volume = self._build_user_volume(workspace_id, user_id)
                volumes = [volume]

            create_conn_config = self._build_connection_config(request_timeout=self._create_timeout)

            injection = None
            if self._exchange_host:
                resolver = SandboxEnvResolver(SandboxEnvRepository(session, org_id=org_id))
                resolved = await resolver.resolve(workspace_id=workspace_id, user_id=user_id)
                injection = SandboxEnvInjector(exchange_host=self._exchange_host).build(resolved)

            base_policy = injection.network_policy if injection else NetworkPolicy(
                defaultAction="deny", egress=[]
            )
            network_policy = merge_network_rules(base_policy, policy.network_rules)

            try:
                raw_sandbox = await opensandbox.Sandbox.create(
                    policy.default_image,
                    connection_config=create_conn_config,
                    timeout=None,
                    ready_timeout=timedelta(seconds=self._ready_timeout),
                    volumes=volumes,
                    resource={"cpu": self._resource_cpu, "memory": self._resource_memory},
                    secure_access=True,
                    network_policy=network_policy,
                )
                sandbox_id = raw_sandbox.id
                await repo.promote_to_running(reserved.id, sandbox_id=sandbox_id)
                raw_sandbox = await opensandbox.Sandbox.connect(
                    sandbox_id, connection_config=conn_config, skip_health_check=True
                )
            except ProviderSandboxError as exc:
                # Provider failed — free the reserved slot so the next turn can retry.
                await repo.delete_record(reserved.id)
                raise SandboxError(str(exc)) from exc

            backend = OpenSandbox(sandbox=raw_sandbox, workdir=self._workdir)
            if self._exchange_host:
                await self._apply_egress(
                    session, backend, org_id=org_id, workspace_id=workspace_id,
                    user_id=user_id, sandbox_id=sandbox_id,
                )
            return backend
```

Add `from opensandbox.models.sandboxes import NetworkPolicy` to the imports (alongside the existing `PVC, Volume` import), ensure `import asyncio` and `import time` are present (used by the race-poll loop above), add `self._reserve_wait_timeout` (default 30s) and `self._reserve_poll_interval` (default 0.5s) to `SandboxManager.__init__`, and remove the now-unused inline `self._image` create reference. The reaper (`cleanup_expired`/`list_expired_system`) already filters on `status == "running"`; widen `list_expired_system` and `list_expired` to also sweep `status == "provisioning"` rows older than their TTL so a crash mid-create cannot orphan a reserved row. Change both `.where(UserSandbox.status == "running")` clauses to `.where(UserSandbox.status.in_(("provisioning", "running")))`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_sandbox_manager_create.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Run the manager + repo regression**

Run: `cd backend && uv run pytest tests/unit/test_user_sandbox_repo.py tests/unit/test_sandbox_manager_create.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/sandbox/manager.py backend/cubeplex/repositories/user_sandbox.py backend/tests/unit/test_sandbox_manager_create.py
git commit -m "feat(sandbox): reserve-row-first create, workspace-scoped PVC, policy image+network delivery"
```

---

## Task 7: Alembic migration (autogenerate)

**One** migration, fully autogenerated — no manual data step. OQ-7 resolution: the project has not shipped publicly, so we assume pre-migration data has no `(org, workspace, user)` duplicate active rows. If a real deployment ever hits dirty data here, it's an ops event (run a one-off cleanup script), not migration logic.

By the time this task runs, BOTH the `SandboxPolicy` model [Task 1] and the `UserSandbox` index change [Task 5] already exist in the metadata, so the single `--autogenerate` run captures both the `sandbox_policies` table and the `uq_user_sandbox_active` index. A second run would be empty.

**Files:**
- Create: `backend/alembic/versions/<rev>_sandbox_policies_and_active_unique_index.py`

- [ ] **Step 1: Confirm current head**

Run: `cd backend && uv run alembic heads`
Expected: prints `00948a73877f (head)` (or the current head if rebased).

- [ ] **Step 2: Autogenerate the combined migration**

Run: `cd backend && uv run alembic revision --autogenerate -m "sandbox policies and active unique index"`
Expected: a single new file under `alembic/versions/` whose `upgrade()` calls **both** `op.create_table('sandbox_policies', ...)` (with the `uq_sandbox_policy_scope` unique index on `(org_id, scope_workspace_id)`) **and** `op.create_index('uq_user_sandbox_active', 'user_sandboxes', ['org_id','workspace_id','user_id'], unique=True, postgresql_where=...)`. No seed rows.

- [ ] **Step 3: Add the clean-data assumption note**

Open the generated migration and add a short module docstring (or top-of-`upgrade()` comment) capturing the OQ-7 assumption — do NOT add a data-collapse step:

```python
"""sandbox policies and active unique index

Assumes clean data: no (org_id, workspace_id, user_id) duplicate rows in
status IN ('provisioning','running'). The project has not shipped publicly,
so this is a safe assumption. If a real deployment ever hits dirty data,
operators run a one-off cleanup script before applying this migration;
that is NOT migration logic.
"""
```

Leave both `upgrade()` and `downgrade()` exactly as autogenerated.

- [ ] **Step 4: Apply and verify the migration**

Run: `cd backend && uv run alembic upgrade head`
Expected: one `Running upgrade ...` line for the new revision, no error.

Run: `cd backend && uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: clean down then up — the revision is reversible.

- [ ] **Step 5: Verify no further drift**

Run: `cd backend && uv run alembic revision --autogenerate -m "drift check"`
Expected: the generated file's `upgrade()`/`downgrade()` are empty (`pass`). If empty, delete it:

```bash
git status --short backend/alembic/versions/
# delete the empty drift-check file before committing
```

Run: `rm backend/alembic/versions/*drift_check*.py` (only if it was the empty one).

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/
git commit -m "feat(sandbox): migration for active unique index + sandbox_policies"
```

---

## Task 8: Command-rule enforcement in the execute middleware

**Scope (OQ-10):** v1 command rules apply to the `execute` tool ONLY. The
dotfile / config-file protection control (denying `write_file` /
`edit_file` writes to patterns like `~/.bashrc`, `**/.git/config`) is
**deferred to a fast-follow PR** — it needs a separate matcher applied to
file *paths*, not command strings, and the UX (showing "blocked path"
errors in the editor tools) is different. Do not extend the matcher to the
write/edit tools in this task.

**Files:**
- Modify: `backend/cubeplex/middleware/sandbox.py:128-158,362-382`
- Modify: `backend/cubeplex/streams/run_manager.py:1248-1260`
- Test: `backend/tests/unit/test_sandbox_middleware_command_rules.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_sandbox_middleware_command_rules.py`:

```python
import pytest

from cubeplex.middleware.sandbox import _make_execute_tool


class _FakeResult:
    def __init__(self, output: str, exit_code: int = 0) -> None:
        self.output = output
        self.exit_code = exit_code


class _FakeSandbox:
    workdir = "/workspace"

    def __init__(self) -> None:
        self.ran: list[str] = []

    async def execute(self, command: str):
        self.ran.append(command)
        return _FakeResult("ok")


async def test_deny_blocks_and_never_runs() -> None:
    sb = _FakeSandbox()
    tool = _make_execute_tool(sb, command_rules=[{"action": "deny", "pattern": "rm *"}])
    result = await tool.execute("c1", type(tool.parameters)(command="rm -rf /workspace"))
    text = result.content[0].text
    assert "blocked by org policy" in text
    assert result.is_error is True  # surfaces as a tool error, not a success
    assert sb.ran == []  # nothing executed


async def test_allow_runs() -> None:
    sb = _FakeSandbox()
    tool = _make_execute_tool(sb, command_rules=[{"action": "deny", "pattern": "rm *"}])
    await tool.execute("c1", type(tool.parameters)(command="ls -la"))
    assert sb.ran == ["ls -la"]


async def test_confirm_degrades_to_deny_in_v1() -> None:
    sb = _FakeSandbox()
    tool = _make_execute_tool(sb, command_rules=[{"action": "confirm", "pattern": "git push *"}])
    result = await tool.execute("c1", type(tool.parameters)(command="git push origin main"))
    text = result.content[0].text
    assert "requires confirmation" in text
    assert result.is_error is True
    assert sb.ran == []


async def test_chained_denied_subcommand_blocks_whole_call() -> None:
    sb = _FakeSandbox()
    tool = _make_execute_tool(sb, command_rules=[{"action": "deny", "pattern": "rm *"}])
    result = await tool.execute("c1", type(tool.parameters)(command="ls && rm -rf /"))
    assert "blocked by org policy" in result.content[0].text
    assert sb.ran == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_sandbox_middleware_command_rules.py -v`
Expected: FAIL — `_make_execute_tool()` got an unexpected keyword `command_rules`.

- [ ] **Step 3: Enforce rules in `_execute`**

In `backend/cubeplex/middleware/sandbox.py`, add the import at the top:

```python
from cubeplex.sandbox_policy.rules import evaluate_command
```

Change `_make_execute_tool` signature and body:

```python
def _make_execute_tool(
    sandbox: Sandbox,
    *,
    workspace_id: str | None = None,
    conversation_id: str | None = None,
    command_rules: list[dict[str, Any]] | None = None,
) -> AgentTool[_ExecuteArgs]:
    """Build the execute cubepi.AgentTool backed by a sandbox instance.

    Command rules are enforced here — the last cubeplex-owned point before the
    command reaches the provider. Precedence deny > confirm > allow; in v1
    ``confirm`` degrades to ``deny`` because cubepi has no elicit/approve
    event channel yet (see TODO below + spec OQ-1/OQ-2).
    """
    rules = command_rules or []

    async def _execute(
        tool_call_id: str,
        args: _ExecuteArgs,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        action, pattern = evaluate_command(args.command, rules)
        if action == "deny":
            # Surface as a tool ERROR (is_error=True) so cubepi's finalized result
            # reads as a failure, not a successful command that printed a message.
            return AgentToolResult(
                content=[TextContent(text=f"command blocked by org policy: {pattern}")],
                is_error=True,
            )
        if action == "confirm":
            # TODO(cubepi-hitl): real prompt-and-approve flow once upstream ships
            # the elicit/approve event channel. Until then, treat confirm as deny
            # with a distinct message so admins still see their rule fire. The
            # audit row tag is `confirmed-action-deferred`. Acceptance criteria
            # for the upstream cubepi work: confirmation blocks only the tool
            # call (not the whole run); 180s timeout; timed-out = deny + audit
            # row; sandbox TTL clock does not pause while waiting. See OQ-1/OQ-2.
            return AgentToolResult(
                content=[
                    TextContent(
                        text=(
                            f"command requires confirmation (pattern: {pattern}); "
                            "not yet supported in this deployment"
                        )
                    )
                ],
                is_error=True,
            )

        result = await sandbox.execute(args.command)
        if workspace_id is not None and conversation_id is not None and result.exit_code == 0:
            _record_executed(workspace_id, conversation_id, args.command)
        output = result.output
        if result.exit_code is not None and result.exit_code != 0:
            output += f"\n[exit code: {result.exit_code}]"
        return AgentToolResult(content=[TextContent(text=output)])

    return AgentTool(
        name="execute",
        description="Execute a shell command in the sandbox environment.",
        parameters=_ExecuteArgs,
        execute=_execute,
    )
```

Thread `command_rules` through `SandboxMiddleware.__init__`:

```python
    def __init__(
        self,
        *,
        sandbox: Sandbox,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        command_rules: list[dict[str, Any]] | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.conversation_id = conversation_id
        self.workspace_id = workspace_id
        self.command_rules = command_rules or []

        self._tools: list[AgentTool[Any]] = [
            _make_execute_tool(
                sandbox,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                command_rules=self.command_rules,
            ),
            _make_write_file_tool(sandbox),
            _make_edit_file_tool(sandbox),
            _make_file_read_tool(sandbox, conversation_id),
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_sandbox_middleware_command_rules.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Wire the resolved command rules in run_manager**

In `backend/cubeplex/streams/run_manager.py`, in the SandboxMiddleware construction block (~line 1251), resolve the org policy and pass `command_rules`. Add near the block:

```python
                from cubeplex.middleware.sandbox import SandboxMiddleware
                from cubeplex.repositories.sandbox_policy import SandboxPolicyRepository
                from cubeplex.services.sandbox_policy import SandboxPolicyResolver
                from cubeplex.config import config as _cfg

                async with self._session_factory() as _pol_session:  # use the same factory the
                    _eff = await SandboxPolicyResolver(                # manager uses; see note below
                        SandboxPolicyRepository(_pol_session, org_id=ctx.org_id),
                        default_image=_cfg.get("sandbox.image", "ubuntu:22.04"),
                    ).resolve()

                sandbox_mw = SandboxMiddleware(
                    sandbox=sandbox,
                    conversation_id=conversation_id,
                    workspace_id=ctx.workspace_id,
                    command_rules=_eff.command_rules,
                )
```

NOTE for the implementer: `run_manager` already has access to a session factory used elsewhere in this method (search this method for the existing `self._session_factory` / `async_sessionmaker` usage and reuse that exact attribute name; do not introduce a new one). If no factory is in scope here, import `get_sandbox_manager()` and add a thin `SandboxManager.resolve_command_rules(org_id)` helper that opens its own session and returns `EffectivePolicy.command_rules` — keep the DB access in the manager, not the stream layer. Pick whichever matches the surrounding code; the observable contract is "the middleware receives the org's resolved command_rules".

- [ ] **Step 6: Run middleware + matcher regression**

Run: `cd backend && uv run pytest tests/unit/test_sandbox_middleware_command_rules.py tests/unit/test_sandbox_policy_rules.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/middleware/sandbox.py backend/cubeplex/streams/run_manager.py backend/tests/unit/test_sandbox_middleware_command_rules.py
git commit -m "feat(sandbox): enforce command rules in execute tool (confirm=deny v1)"
```

---

## Task 9: E2E — ownership isolation + command deny end-to-end

This is the priority test per repo discipline. It drives the admin route + an agent run, asserting the isolation boundary and the command block.

**Files:**
- Create: `backend/tests/e2e/test_sandbox_scoping.py`

Note: these tests drive `SandboxManager.get_or_create`, which calls
`opensandbox.Sandbox.create/connect`. The DB-level assertions here do NOT need a
real provider — but only if the provider calls are faked. **Monkeypatch
`opensandbox.Sandbox.create` and `.connect` with the same `_FakeRaw` fakes used
in Task 6** (`test_sandbox_manager_create.py`); factor those fakes into a shared
fixture in `tests/e2e/conftest.py` (or a small test helper) so both modules
reuse them. Without the fakes these tests require a live OpenSandbox and would
hang/fail in CI. Reserve `@pytest.mark.requires_sandbox` (search
`tests/e2e/test_opensandbox.py` for the exact marker name) only for any
assertion that genuinely needs a real provider (e.g. real filesystem
isolation); the row-count/image/command-deny assertions run fully faked.

- [ ] **Step 1: Write the DB-level isolation test**

Create `backend/tests/e2e/test_sandbox_scoping.py`:

```python
"""E2E for sandbox ownership isolation + org command-deny policy.

The dual-workspace fixture yields two clients for the SAME user in DIFFERENT
workspaces (see tests/e2e/conftest.py ~line 956).
"""

import pytest
import sqlalchemy as sa

from cubeplex.middleware import sandbox as sandbox_mw
from cubeplex.sandbox.manager import SandboxManager


async def test_same_user_two_workspaces_distinct_active_rows(
    session_factory, seeded_org_ws_user
) -> None:
    """Two get_or_create calls for the same user in two workspaces yield two
    distinct active UserSandbox rows (storage + provider isolation boundary)."""
    org_id, ws_a, ws_b, user_id = seeded_org_ws_user
    mgr = SandboxManager(session_factory)
    await mgr.get_or_create(user_id, org_id=org_id, workspace_id=ws_a)
    await mgr.get_or_create(user_id, org_id=org_id, workspace_id=ws_b)
    async with session_factory() as s:
        rows = (
            await s.execute(
                sa.text(
                    "SELECT workspace_id, sandbox_id FROM user_sandboxes "
                    "WHERE user_id=:u AND status='running'"
                ),
                {"u": user_id},
            )
        ).all()
    ws_ids = {r[0] for r in rows}
    sbx_ids = {r[1] for r in rows}
    assert ws_ids == {ws_a, ws_b}
    assert len(sbx_ids) == 2  # distinct provider sandboxes


async def test_concurrent_create_reuses_not_duplicates(
    session_factory, seeded_org_ws_user
) -> None:
    """A second create for the same identity reuses; never a second running row."""
    org_id, ws_a, _ws_b, user_id = seeded_org_ws_user
    mgr = SandboxManager(session_factory)
    await mgr.get_or_create(user_id, org_id=org_id, workspace_id=ws_a)
    await mgr.get_or_create(user_id, org_id=org_id, workspace_id=ws_a)
    async with session_factory() as s:
        count = (
            await s.execute(
                sa.text(
                    "SELECT COUNT(*) FROM user_sandboxes "
                    "WHERE user_id=:u AND workspace_id=:w AND status='running'"
                ),
                {"u": user_id, "w": ws_a},
            )
        ).scalar_one()
    assert count == 1
```

Add the `session_factory` / `seeded_org_ws_user` fixtures to this test module (top of file) if they are not already in `tests/e2e/conftest.py`. The `seeded_org_ws_user` fixture inserts one org, two workspaces, one user via raw SQL (mirror `_seed_org_ws_user` from Task 5 and add a second workspace).

- [ ] **Step 2: Write the command-deny E2E (audit-buffer assertion)**

Append to the same file:

```python
async def test_command_deny_blocks_and_filesystem_untouched(
    admin_client_with_user_id, monkeypatch
) -> None:
    """Admin denies 'rm *'; the agent's execute attempt is blocked and the
    audit buffer (what actually hit the fs) stays empty for that command."""
    client, ws_id, _user_id = admin_client_with_user_id

    put = await client.put(
        "/api/v1/admin/sandbox-policy",
        json={
            "default_image": "ubuntu:22.04",
            "network_rules": None,
            "command_rules": [{"action": "deny", "pattern": "rm *"}],
        },
    )
    assert put.status_code == 200

    sandbox_mw.enable_audit()
    sandbox_mw.reset_executed_commands()
    try:
        # Build the middleware exactly as run_manager would, with the denied rule,
        # and invoke the execute tool directly (no provider needed for a deny).
        from cubeplex.middleware.sandbox import _make_execute_tool

        class _Sb:
            workdir = "/workspace"

            async def execute(self, command):  # noqa: ANN001
                raise AssertionError("denied command must not reach the sandbox")

        tool = _make_execute_tool(
            _Sb(),
            workspace_id=ws_id,
            conversation_id="conv-1",
            command_rules=[{"action": "deny", "pattern": "rm *"}],
        )
        res = await tool.execute("c1", type(tool.parameters)(command="rm -rf /workspace"))
        assert "blocked by org policy" in res.content[0].text
        assert sandbox_mw.executed_commands(ws_id, "conv-1") == []
    finally:
        sandbox_mw.disable_audit()
```

For `admin_client_with_user_id`: if `tests/e2e/conftest.py` doesn't already expose the admin client's user id, add a small fixture there that yields `(client, workspace_id, user_id)` derived from the existing `admin_client` setup. Reuse the existing admin-login machinery; do not re-implement auth.

- [ ] **Step 3: Run the E2E (DB-level first)**

Run: `cd backend && uv run pytest tests/e2e/test_sandbox_scoping.py -v`
Expected: PASS for the DB-level + command-deny tests. Provider-dependent file-isolation assertions, if added, are skipped without a live sandbox (`requires_sandbox` marker).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/e2e/test_sandbox_scoping.py backend/tests/e2e/conftest.py
git commit -m "test(sandbox): E2E ownership isolation + command-deny enforcement"
```

---

## Task 10: Image-policy E2E (lazy drift) + pre-PR sweep

**Files:**
- Modify: `backend/tests/e2e/test_sandbox_scoping.py`

- [ ] **Step 1: Add the lazy-drift E2E**

OQ-5 resolution: image drift is **lazy**. Existing running sandboxes keep
their original image. Only sandboxes created *after* the policy change pick
up the new image. The E2E asserts both halves.

Append to `backend/tests/e2e/test_sandbox_scoping.py`:

```python
async def test_image_drift_is_lazy_existing_keeps_old_new_uses_new(
    session_factory, seeded_org_ws_user
) -> None:
    """Admin default_image is used at create and persisted on the row.
    Changing it does NOT recreate the existing sandbox (lazy drift): the
    existing row stays running on its original image. A NEW user/workspace
    (or a freshly recreated sandbox after the existing one is terminated)
    picks up the new image."""
    org_id, ws_a, ws_b, user_id = seeded_org_ws_user
    from cubeplex.repositories.sandbox_policy import SandboxPolicyRepository

    async with session_factory() as s:
        await SandboxPolicyRepository(s, org_id=org_id).upsert(
            default_image="python:3.12",
            network_rules=None,
            command_rules=None,
        )

    mgr = SandboxManager(session_factory)
    await mgr.get_or_create(user_id, org_id=org_id, workspace_id=ws_a)
    async with session_factory() as s:
        img1 = (
            await s.execute(
                sa.text(
                    "SELECT image FROM user_sandboxes WHERE user_id=:u "
                    "AND workspace_id=:w AND status='running'"
                ),
                {"u": user_id, "w": ws_a},
            )
        ).scalar_one()
    assert img1 == "python:3.12"

    # Change the policy image; the EXISTING sandbox keeps its old image
    # (lazy drift). No row is terminated by the policy change.
    async with session_factory() as s:
        await SandboxPolicyRepository(s, org_id=org_id).upsert(
            default_image="ubuntu:22.04",
            network_rules=None,
            command_rules=None,
        )
    await mgr.get_or_create(user_id, org_id=org_id, workspace_id=ws_a)
    async with session_factory() as s:
        still_running = (
            await s.execute(
                sa.text(
                    "SELECT image FROM user_sandboxes WHERE user_id=:u "
                    "AND workspace_id=:w AND status='running'"
                ),
                {"u": user_id, "w": ws_a},
            )
        ).scalar_one()
        terminated = (
            await s.execute(
                sa.text(
                    "SELECT COUNT(*) FROM user_sandboxes WHERE user_id=:u "
                    "AND workspace_id=:w AND status='terminated'"
                ),
                {"u": user_id, "w": ws_a},
            )
        ).scalar_one()
    assert still_running == "python:3.12"  # lazy: NOT torn down
    assert terminated == 0  # nothing demoted by the policy change

    # A brand-new sandbox (different workspace, same user) picks up the new image.
    await mgr.get_or_create(user_id, org_id=org_id, workspace_id=ws_b)
    async with session_factory() as s:
        img_new = (
            await s.execute(
                sa.text(
                    "SELECT image FROM user_sandboxes WHERE user_id=:u "
                    "AND workspace_id=:w AND status='running'"
                ),
                {"u": user_id, "w": ws_b},
            )
        ).scalar_one()
    assert img_new == "ubuntu:22.04"
```

- [ ] **Step 2: Run the new E2E**

Run: `cd backend && uv run pytest tests/e2e/test_sandbox_scoping.py::test_image_drift_is_lazy_existing_keeps_old_new_uses_new -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_sandbox_scoping.py
git commit -m "test(sandbox): E2E lazy image drift (existing keeps, new picks up)"
```

---

## Task 11: One-time PVC migration helper (`migrate_user_pvcs.py`)

**OQ-9 resolution.** The volume rename in Task 6 changes the PVC claim-name
shape from `user-<user_id>` to `ws-<workspace_id>-user-<user_id>`. Pre-rename
PVCs become orphaned. This task ships a CLI helper that migrates them
**when unambiguous** (the user belongs to exactly one workspace at run time);
ambiguous cases (user in multiple workspaces, since there's no single
correct target) are listed for manual operator cleanup. Dry-run by default,
`--apply` to actually move PVCs.

**Files:**
- Create: `backend/scripts/dev/migrate_user_pvcs.py`
- Test: `backend/tests/unit/test_migrate_user_pvcs.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_migrate_user_pvcs.py`:

```python
"""Unit test for the unambiguous-only PVC migration plan logic.

The CLI itself talks to the cluster; this test exercises the pure-function
planner that decides what to do per user (rename / leave-for-manual /
skip-already-migrated). No cluster I/O.
"""

from cubeplex.scripts.dev.migrate_user_pvcs import build_migration_plan


def test_user_with_one_workspace_gets_a_rename_action() -> None:
    plan = build_migration_plan(
        existing_pvcs=["user-u1", "user-u2"],
        memberships={"u1": ["ws-A"], "u2": ["ws-X", "ws-Y"]},
        target_prefix="user-",
        new_template="ws-{ws}-user-{user}",
    )
    actions = {a.user_id: a for a in plan}
    assert actions["u1"].kind == "rename"
    assert actions["u1"].new_name == "ws-ws-A-user-u1"
    # u2 is in two workspaces -> ambiguous, surfaced for manual cleanup.
    assert actions["u2"].kind == "manual_cleanup"


def test_user_with_no_existing_pvc_is_skipped() -> None:
    plan = build_migration_plan(
        existing_pvcs=[],
        memberships={"u1": ["ws-A"]},
        target_prefix="user-",
        new_template="ws-{ws}-user-{user}",
    )
    assert plan == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_migrate_user_pvcs.py -v`
Expected: FAIL — `No module named 'cubeplex.scripts.dev.migrate_user_pvcs'`.

- [ ] **Step 3: Implement the helper**

Create `backend/scripts/dev/migrate_user_pvcs.py`:

```python
"""One-time migrator: rename pre-(workspace,user) PVCs to the new shape.

Run from the backend dir. Dry-run is the default; pass --apply to actually
rename. Ambiguous cases (user in multiple workspaces) are NOT touched and
are listed for operator manual cleanup.

    uv run python -m cubeplex.scripts.dev.migrate_user_pvcs            # dry-run
    uv run python -m cubeplex.scripts.dev.migrate_user_pvcs --apply    # do it

This script is intentionally simple and lives under scripts/dev/ — it is a
one-shot helper, not a long-term commitment.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from typing import Literal


@dataclass
class MigrationAction:
    user_id: str
    kind: Literal["rename", "manual_cleanup", "skip_no_pvc"]
    old_name: str | None = None
    new_name: str | None = None
    reason: str = ""


def build_migration_plan(
    *,
    existing_pvcs: list[str],
    memberships: dict[str, list[str]],
    target_prefix: str,
    new_template: str,
) -> list[MigrationAction]:
    """Return one action per user with a pre-rename PVC.

    - exactly one workspace  -> rename
    - multiple workspaces    -> manual_cleanup (ambiguous target)
    - no pre-rename PVC      -> omitted (nothing to do)
    """
    pvc_set = set(existing_pvcs)
    actions: list[MigrationAction] = []
    for user_id, workspaces in memberships.items():
        old_name = f"{target_prefix}{user_id}"
        if old_name not in pvc_set:
            continue
        if len(workspaces) == 1:
            new_name = new_template.format(ws=workspaces[0], user=user_id)
            actions.append(
                MigrationAction(user_id=user_id, kind="rename",
                                old_name=old_name, new_name=new_name)
            )
        else:
            actions.append(
                MigrationAction(
                    user_id=user_id, kind="manual_cleanup", old_name=old_name,
                    reason=f"user belongs to {len(workspaces)} workspaces; pick one manually",
                )
            )
    return actions


async def _fetch_memberships() -> dict[str, list[str]]:
    """Open a session, return {user_id: [workspace_id, ...]} for all users."""
    from cubeplex.db.session import async_sessionmaker_for_app
    from sqlalchemy import text

    async with async_sessionmaker_for_app()() as s:
        rows = (await s.execute(
            text("SELECT user_id, workspace_id FROM memberships")
        )).all()
    out: dict[str, list[str]] = {}
    for user_id, ws_id in rows:
        out.setdefault(user_id, []).append(ws_id)
    return out


async def _list_pvcs() -> list[str]:
    """Return all PVC claim names in the configured namespace.

    Reuses the provider helper used by SandboxManager to talk to the volume
    backend; if the deployment runs without a real PVC backend, returns [].
    The implementer wires this to the same client the manager already uses;
    leave this as a thin call.
    """
    return []  # IMPLEMENT: wire to the same PVC client SandboxManager uses


def _apply_rename(action: MigrationAction) -> None:
    """Perform the rename in the cluster. IMPLEMENT against the same client.

    Most PVC backends don't support rename in-place — typical pattern is:
    create the new PVC bound to the same PV (reclaimPolicy=Retain), then
    delete the old PVC. Leave the concrete steps to whoever runs this; this
    is a one-shot script."""
    raise NotImplementedError("wire to your PVC client")


async def main_async(*, apply: bool) -> int:
    memberships = await _fetch_memberships()
    pvcs = await _list_pvcs()
    plan = build_migration_plan(
        existing_pvcs=pvcs,
        memberships=memberships,
        target_prefix="user-",
        new_template="ws-{ws}-user-{user}",
    )
    if not plan:
        print("nothing to migrate")
        return 0
    rename = [a for a in plan if a.kind == "rename"]
    manual = [a for a in plan if a.kind == "manual_cleanup"]
    print(f"plan: {len(rename)} renames, {len(manual)} manual-cleanup entries")
    for a in rename:
        print(f"  RENAME {a.old_name} -> {a.new_name}")
    for a in manual:
        print(f"  MANUAL {a.old_name} ({a.reason})")
    if not apply:
        print("dry-run: re-run with --apply to perform the renames")
        return 0
    for a in rename:
        _apply_rename(a)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="actually perform the renames")
    args = p.parse_args()
    return asyncio.run(main_async(apply=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_migrate_user_pvcs.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Smoke the dry-run path**

Run: `cd backend && uv run python -m cubeplex.scripts.dev.migrate_user_pvcs`
Expected: prints `nothing to migrate` (the placeholder `_list_pvcs` returns
`[]`). Real PVC-listing wiring is done by whoever runs the migration in a
deployment with real PVCs.

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/dev/migrate_user_pvcs.py backend/tests/unit/test_migrate_user_pvcs.py
git commit -m "feat(sandbox): one-time PVC migration helper (dry-run by default)"
```

---

## Task 12: Frontend — admin policy editor, workspace sandbox status, credential warning

Scope-isolated frontend deliverable. The policy is org-scoped, so the
editor lives under `/admin/sandbox-policy` (NOT under `/w/[wsId]/...`). The
workspace-side sandbox status view lives under `/w/[wsId]/sandbox`. The
vault credential editor gets a yellow-banner warning. A Playwright smoke
exercises the admin happy path.

**Files:**

- Create: `frontend/packages/web/app/(app)/admin/sandbox-policy/page.tsx`
- Create: `frontend/packages/web/app/(app)/admin/sandbox-policy/_components/PolicyEditor.tsx`
- Create: `frontend/packages/web/app/(app)/admin/sandbox-policy/_components/NetworkRulesTable.tsx`
- Create: `frontend/packages/web/app/(app)/admin/sandbox-policy/_components/CommandRulesTable.tsx`
- Create: `frontend/packages/web/app/api/v1/admin/sandbox-policy/route.ts`
- Create: `frontend/packages/web/app/(app)/w/[wsId]/sandbox/page.tsx`
- Create: `frontend/packages/web/app/api/v1/ws/[wsId]/sandbox-status/route.ts`
- Modify: existing credential editor — add a `<CredentialDenyWarning />`
  banner (path depends on where the credential edit page already lives;
  search for the existing `sandbox_env` credential edit route file).
- Modify: `frontend/packages/core/src/api/types.ts` — add
  `SandboxPolicyOut`, `UpdateSandboxPolicyIn`, `SandboxStatusOut`.
- Modify: `frontend/packages/web/app/(app)/admin/_components/AdminNav.tsx`
  (or wherever admin nav lives) — add a "Sandbox policy" entry.
- Create: `frontend/packages/web/e2e/sandbox-policy.spec.ts` — Playwright smoke.

- [ ] **Step 1: Add types in `@cubeplex/core`**

In `frontend/packages/core/src/api/types.ts`, add:

```ts
export interface SandboxNetworkRule {
  action: "allow" | "deny";
  target: string;
}

export interface SandboxCommandRule {
  action: "allow" | "deny" | "confirm";
  pattern: string;
}

export interface SandboxPolicyOut {
  default_image: string;
  network_rules: SandboxNetworkRule[];
  command_rules: SandboxCommandRule[];
  warnings: string[];
}

export interface UpdateSandboxPolicyIn {
  default_image: string;
  network_rules: SandboxNetworkRule[] | null;
  command_rules: SandboxCommandRule[] | null;
}

export interface SandboxStatusOut {
  status: "provisioning" | "running" | "paused" | "terminated" | "absent";
  default_image: string | null;
  last_activity_at: string | null;
  browser_url: string | null;
}
```

Then `pnpm --filter @cubeplex/core build` so the web package sees the types.

- [ ] **Step 2: Add the proxy route (admin)**

Create `frontend/packages/web/app/api/v1/admin/sandbox-policy/route.ts`:

```ts
import { NextRequest } from "next/server";
import { proxyToBackend } from "@/lib/proxy";

export async function GET(req: NextRequest) {
  return proxyToBackend(req, "/api/v1/admin/sandbox-policy");
}

export async function PUT(req: NextRequest) {
  return proxyToBackend(req, "/api/v1/admin/sandbox-policy");
}
```

(The `proxyToBackend` helper already exists for the other admin routes;
reuse it. No SSE involved here, so no `compress: false` concern.)

- [ ] **Step 3: Build the admin policy editor page**

Create `frontend/packages/web/app/(app)/admin/sandbox-policy/page.tsx`:

```tsx
import { PolicyEditor } from "./_components/PolicyEditor";

export default function Page() {
  return (
    <div className="space-y-6 p-6">
      <h1 className="text-2xl font-semibold">Sandbox policy</h1>
      <p className="text-sm text-muted-foreground">
        Default image, network egress rules, and command rules for all sandboxes
        in this organization. Changes to the default image apply lazily — existing
        sandboxes finish on their original image; new conversations pick up the
        new image.
      </p>
      <PolicyEditor />
    </div>
  );
}
```

Then `PolicyEditor.tsx` (a thin client component that fetches `GET`, holds
form state, sends `PUT`, renders warnings). Wire the two child tables
(`NetworkRulesTable`, `CommandRulesTable`) for add/remove/reorder rows. The
command-rules `action` column is a `<select>` with `allow`/`deny`/`confirm`;
when `confirm` is selected, render an inline hint:

> `confirm` is currently treated as `deny` at runtime. Full prompt-for-
> approval requires upstream cubepi changes (tracked separately).

On save, if the PUT response includes `warnings[]`, render each as a small
yellow banner above the network table.

- [ ] **Step 4: Add the admin nav entry**

In `frontend/packages/web/app/(app)/admin/_components/AdminNav.tsx` (or
wherever the admin nav list lives — search for the existing "Sandbox env"
entry next to it), add a "Sandbox policy" item linking to
`/admin/sandbox-policy`.

- [ ] **Step 5: Workspace sandbox status page + backend route**

Add a tiny backend route at `backend/cubeplex/api/routes/v1/ws_sandbox.py`
returning the current user's `UserSandbox` row in the workspace as a
`SandboxStatusOut` (state, image, `last_activity_at` via `utc_isoformat()`,
optional browser URL). Workspace-scoped per the project's scope-isolated
APIs rule — separate handler from any admin route, no `?scope=`.

Frontend page `frontend/packages/web/app/(app)/w/[wsId]/sandbox/page.tsx`
fetches that endpoint and renders a small read-only card with: state badge,
image text, last-active timestamp, and an "Open browser" link when
`browser_url` is set. No mutation controls in v1.

- [ ] **Step 6: Credential editor warning banner**

In the existing credential edit page (search for the file under
`frontend/packages/web/app/(app)/w/[wsId]/.../credentials/...` that already
calls the PUT credential endpoint), after the save response or on initial
load, compare the credential's `required_hosts` against the current policy's
`deny` targets (use the new types from Step 1). If any host is covered,
render a yellow banner above the form:

> This credential's required hosts include `<host>`; the org sandbox policy
> currently denies `<host>`. Outbound calls to `<host>` will be blocked.
> Coordinate with your admin to allow this host.

No hard block — the credential still saves.

- [ ] **Step 7: Playwright smoke**

Create `frontend/packages/web/e2e/sandbox-policy.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import { loginAsAdmin, seedCredentialWithHost } from "./helpers";

test("admin policy editor warns on credential-host conflict, then clears", async ({ page }) => {
  await loginAsAdmin(page);
  await seedCredentialWithHost(page, "api.github.com");

  await page.goto("/admin/sandbox-policy");
  // Add a deny rule for api.github.com.
  await page.getByRole("button", { name: /add network rule/i }).click();
  await page.getByLabel(/action/i).first().selectOption("deny");
  await page.getByLabel(/target/i).first().fill("api.github.com");
  await page.getByRole("button", { name: /save/i }).click();
  await expect(page.getByText(/conflicts with installed credential/i)).toBeVisible();

  // Remove the deny rule; warning clears.
  await page.getByRole("button", { name: /remove/i }).first().click();
  await page.getByRole("button", { name: /save/i }).click();
  await expect(page.getByText(/conflicts with installed credential/i)).not.toBeVisible();
});
```

Replace `loginAsAdmin` / `seedCredentialWithHost` with the actual helpers
that already exist in the repo's e2e folder (search for `loginAsAdmin` in
`frontend/packages/web/e2e/` and reuse it). If a seed helper for credentials
doesn't exist yet, add one alongside — the credential must declare
`required_hosts: ["api.github.com"]`.

- [ ] **Step 8: Run the Playwright smoke**

Run: `cd frontend && pnpm --filter web exec playwright test sandbox-policy.spec.ts`
Expected: 1 passed.

- [ ] **Step 9: Commit**

```bash
git add frontend/packages/core/src/api/types.ts frontend/packages/web/app frontend/packages/web/e2e/sandbox-policy.spec.ts backend/cubeplex/api/routes/v1/ws_sandbox.py
git commit -m "feat(sandbox-ui): admin policy editor + workspace sandbox status + credential warning"
```

---

## Task 13: Full type + test sweep before PR

- [ ] **Step 1: mypy**

Run: `cd backend && uv run mypy cubeplex`
Expected: `Success: no issues found`.

- [ ] **Step 2: Backend test sweep**

Run: `cd backend && uv run pytest tests/unit/test_sandbox_policy_rules.py tests/unit/test_sandbox_policy_resolver.py tests/unit/test_sandbox_policy_model.py tests/unit/test_user_sandbox_repo.py tests/unit/test_sandbox_manager_create.py tests/unit/test_sandbox_middleware_command_rules.py tests/unit/test_migrate_user_pvcs.py tests/e2e/test_sandbox_policy_routes.py tests/e2e/test_sandbox_scoping.py -v`
Expected: all PASS.

- [ ] **Step 3: Frontend type-check + Playwright**

Run: `cd frontend && pnpm --filter @cubeplex/core build && pnpm --filter web typecheck && pnpm --filter web exec playwright test sandbox-policy.spec.ts`
Expected: build succeeds, typecheck clean, 1 Playwright test passing.

- [ ] **Step 4: Commit any sweep fixups (typically none)**

If the sweep surfaces nothing new, no commit is needed. If it does, commit
the smallest possible fix and re-run.

---

## Network-rule E2E note (per spec testing strategy)

The spec allows the network-rule check to fall back to a **unit assertion on the merged `NetworkPolicy`** when the egress/exchange infra can't be simulated locally (the "no fake E2E for unsimulatable systems" discipline). That assertion is already covered by `test_merge_network_rules_union_and_deny_wins` in Task 2. If the egress harness (exchange host) is available in CI, add a `requires_sandbox`-marked E2E that adds an admin `deny` target and asserts egress to it fails while an allowed target succeeds — but do **not** write a fake-server E2E for it.

---

## Out of scope for this plan (tracked, not built here)

Per the spec's v1 scope and Open Questions, these are deliberately deferred:

- **`confirm` HITL interrupt channel** (OQ-1/OQ-2) — v1 degrades `confirm` to a distinct deny message (Task 8). The real `tool_confirmation_required` event channel is an **upstream cubepi follow-up**: file an issue in cubepi for the elicit/approve event. Acceptance criteria from cubeplex's side: blocks only the tool call (not the whole run); 180s timeout; timed-out = deny + audit row; sandbox TTL clock does not pause.
- **`allowed_images` / `sandbox_images` table** (OQ-4 / OQ-12) — dropped entirely from v1. Becomes meaningful only when an override surface exists (most likely #153 managed agents declaring `image: ...`). Add together with that override, not before.
- **Per-workspace/per-user policy overrides** (OQ-3, v2) — schema is ready (the `scope_workspace_id` column lands NULL in v1). v2 will populate it for workspace-override rows without a schema migration.
- **Command rules on `write_file`/`edit_file`** (OQ-10) — v1 covers `execute` only. The dotfile/config-file protection control is a fast-follow PR (separate file-path matcher, different UX).
- **Active-kill of orphaned provider sandboxes** — the migration does NOT include any data-collapse step (OQ-7 dropped that); the clean-data assumption stands. If a real deployment somehow has duplicates, that's an ops event, not migration logic.
- **Migration of PVCs for users in multiple workspaces** (OQ-9 ambiguous half) — `backend/scripts/dev/migrate_user_pvcs.py` only migrates unambiguous cases (single-workspace users); multi-workspace users are listed for manual operator cleanup.

---

## Self-Review

- **Spec coverage:**
  - Ownership: partial unique index over `('provisioning','running')` → Task 5; reserve-row-first create → Task 6; no duplicate-collapse step (OQ-7) → Task 7; reaper sweeps `provisioning` → Task 6 Step 4.
  - PVC re-key on `(workspace, user)` → Task 6; one-time migration helper (unambiguous-only, dry-run) → Task 11.
  - `SandboxPolicy` org-only model + `scope_workspace_id` reserved nullable column + `sbxp` prefix → Task 1; repo (OrgSettings-style) + service + resolver → Task 3; `rules.py` matcher → Task 2. `allowed_images` dropped throughout (OQ-4/OQ-12).
  - Admin routes separate from ws → Task 4 (no ws counterpart, no `?scope=`); credential-host conflict warnings (OQ-6) on both PUT routes → Task 4 Steps 3 + 7.
  - Image + merged network delivery at create → Task 6; image drift is **lazy** (OQ-5) → Task 6 + Task 10 E2E.
  - Command-rule enforcement deny/confirm(=deny v1, with `TODO(cubepi-hitl)`)/allow with shell-chaining → Task 8 (+ matcher Task 2). v1 covers `execute` only (OQ-10).
  - Migrations autogenerated, no manual data step (OQ-7) → Task 7.
  - E2E-first: ownership/command-deny → Task 9; lazy image drift → Task 10; matcher unit (subtle) → Task 2; resolver defaults unit → Task 3; network-rule unit fallback → Task 2; credential-conflict warnings → Task 4 + Task 12 Playwright smoke.
  - Frontend: admin policy editor (`/admin/sandbox-policy`, scope-isolated org-admin route), workspace sandbox status (`/w/[wsId]/sandbox`, read-only), credential editor warning banner, Playwright smoke → Task 12.
  - Deferred: confirm HITL channel (cubepi follow-up), `allowed_images`, v2 overrides, write/edit command rules — see "Out of scope".
- **Placeholder scan:** every code step contains full code; no "TBD"/"add validation"/"similar to". The judgment calls (session factory in `run_manager`, PVC-listing client) are flagged with a concrete fallback contract, not left blank.
- **Type consistency:** `evaluate_command`/`merge_network_rules`/`split_shell_command` (Task 2) are used with matching signatures in Tasks 6 and 8. `EffectivePolicy`/`SandboxPolicyResolver`/`SandboxPolicyService`/`SandboxPolicyValidationError` (Task 3) reused identically in Tasks 4, 6, 8, 10. Repo methods `reserve`/`promote_to_running`/`delete_record`/`get_active_by_user` (Task 5) called with the same names in Task 6. `_make_execute_tool(..., command_rules=...)` consistent across Tasks 8 and 9. Frontend types in `@cubeplex/core` mirror the backend `SandboxPolicyOut` (Task 12 Step 1).

---

## Execution Handoff

Plan complete and saved to `docs/dev/plans/2026-05-27-sandbox-scoping-policy.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task into this worktree (pin the absolute path + `cat .worktree.env` first), review between tasks.
2. **Inline Execution** — execute tasks in this session via executing-plans, batched with checkpoints.
