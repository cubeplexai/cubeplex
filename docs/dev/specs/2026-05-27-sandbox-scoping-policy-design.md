# Sandbox Scoping + Org Policy Design

Date: 2026-05-27
Issue: #144
Status: Draft spec (no implementation yet)

## Problem & Motivation

Two related gaps in how sandboxes are owned and governed:

1. **Cross-workspace sandbox sharing (ownership).** A sandbox is the agent's remote
   code-execution environment. If one user belongs to two workspaces, both
   workspaces must not share a single sandbox: files, installed packages, and
   leaked secrets from workspace A would be visible in workspace B. The
   `UserSandbox` model and `SandboxManager.get_or_create` already key on
   `(user_id, workspace_id)`, but this was never given a clean spec, a unique
   constraint, or a data-backfill story, and downstream code paths still talk
   about "the user's sandbox" in single-key terms. We want the ownership model
   pinned down and provably isolated per `(workspace_id, user_id)`.

2. **No org-level governance of sandbox behaviour.** Today the sandbox image is a
   single global config value (`sandbox.image`, default `ubuntu:22.04`), egress
   network policy is derived only as a side effect of the secret env vault, and
   there is no way for an org admin to say "deny `rm -rf /`", "ask a human before
   `git push`", or "swap the default image". Org admins need a console where they
   set, per org: the **default sandbox image** (a single value, not a list — see
   Architecture note), the **network egress rules**, and **command execution
   rules** (deny / require-confirmation / allow). The command rules must be
   *enforced in the exec path*, not just advisory text in the prompt.

## Goals / Non-goals

### Goals

- Make sandbox ownership structurally `(org_id, workspace_id, user_id)` with a DB
  uniqueness guarantee for the active sandbox, plus a documented backfill.
- Add an org-admin policy record (one per org-default row) holding: a single
  `default_image`, egress network rules, and command rules. Schema reserves a
  `scope_workspace_id` column (NULL = org default; populated in v2 for
  workspace overrides) so v2 needs no migration.
- Deliver the image + network rules into `SandboxManager.get_or_create` at sandbox
  **creation** time (image and network policy are immutable for a live sandbox).
- Enforce command rules at the **execute tool** boundary, before the command
  reaches the sandbox: deny (block + tool error), confirm (pause for a human
  decision), allow (run).
- Keep admin routes (`/api/v1/admin/...`) and workspace routes
  (`/api/v1/ws/{ws}/...`) as separate handlers; share only the service layer.

### Non-goals

- Per-workspace or per-user *overrides* of the org policy. v1 is org-wide only.
  (Listed in Open Questions for v2.)
- Switching a running sandbox's image or network policy mid-run — not possible
  with the single-image / immutable-network-policy constraint; a policy change
  takes effect only on the next sandbox creation.
- Replacing the existing egress secret-vault allowlist mechanism. The new
  admin network rules are an *additional, org-wide* allow/deny layer that composes
  with the per-secret host allowlist already produced by `SandboxEnvInjector`.
- MicroVM / kernel-level isolation upgrades. Out of scope; tracked separately.
- A general policy-as-code DSL. Command rules are simple match lists in v1.

## Current State

### Ownership model

- `backend/cubeplex/models/user_sandbox.py` — `UserSandbox(CubeplexBase,
  OrgScopedMixin)`. Carries `user_id`, `workspace_id` (via mixin), `org_id`,
  `sandbox_id` (provider id, `unique=True`), `status`, `image`, `ttl_seconds`,
  `last_activity_at`. Two non-unique indexes:
  `ix_user_sandboxes_user_ws_status` and `ix_user_sandboxes_org_ws`. There is
  **no** uniqueness on `(org_id, workspace_id, user_id, status='running')`, so
  two concurrent `get_or_create` calls could both create a running row.
- `backend/cubeplex/repositories/user_sandbox.py` —
  `UserSandboxRepository(ScopedRepository[UserSandbox])`. `get_active_by_user`
  is already workspace-scoped (the repo is constructed with `workspace_id`), and
  returns the newest `status='running'` row. The "newest wins" `order_by` is a
  symptom of the missing unique constraint.
- `backend/cubeplex/sandbox/manager.py` — `get_or_create(user_id, *, org_id,
  workspace_id)` looks up the active row for that `(user, workspace)`, health-
  checks it, reuses or recreates. Create persists via `repo.create(...)` and
  sets `image=self._image`.

The data model is *already* per-`(workspace, user)`. The remaining work is
correctness hardening (unique constraint, race handling) + a documented backfill
for any pre-existing single-key rows, not a structural redesign.

### Exec path

- `backend/cubeplex/middleware/sandbox.py` — `SandboxMiddleware` exposes
  `execute`, `write_file`, `edit_file`, `file_read` as cubepi `AgentTool`s.
  `_make_execute_tool._execute` calls `sandbox.execute(args.command)` directly,
  with no inspection of the command string. The only existing hook is an
  audit ring buffer (`_record_executed`) gated behind `enable_audit()` for tests.
  **This `_execute` closure is the natural enforcement point for command rules.**
- `backend/cubeplex/sandbox/opensandbox.py` — `OpenSandbox.execute` runs the
  command via the provider; `set_run_env` injects run-level env.

### Admin config today

- Sandbox image: global config only (`sandbox.image` in
  `backend/cubeplex/sandbox/manager.py:__init__`). Not org-configurable.
- Network policy: built by `backend/cubeplex/sandbox_env/injector.py`
  (`SandboxEnvInjector.build`) as `NetworkPolicy(defaultAction="deny",
  egress=[allow <secret hosts> + exchange_host])`. It is a side effect of the
  secret env vault, set once at `Sandbox.create` (manager.py ~line 267). There is
  no admin-authored allow/deny list.
- Command rules: none. The system prompt (`SANDBOX_PROMPT_TEMPLATE`) is the only
  influence on what the agent runs, and it is not enforcement.
- Existing scope-isolated admin/ws split to mirror:
  `backend/cubeplex/api/routes/v1/admin_sandbox_env.py` (uses
  `get_admin_request_context`) vs `ws_sandbox_env.py` (uses `require_admin` /
  `require_member`), both delegating to `services/sandbox_env.py`.

## Industry Research

- **Allow / deny / ask command gating.** Claude Code's permission system is the
  closest reference: three rule lists — `deny`, `ask`, `allow` — evaluated in that
  order, first match wins, so `deny` always beats `ask` beats `allow`. Rules use a
  `Tool(pattern)` form (e.g. `Bash(rm *)`, `Bash(git push *)`). Critically, the
  matcher is shell-aware: a rule for `safe-cmd *` does **not** match
  `safe-cmd && malicious` — chained commands are not silently allowed. We adopt
  the same deny → confirm → allow precedence and the same "don't let shell
  operators smuggle a denied command past an allow rule" rule.
  ([Claude Code permissions](https://code.claude.com/docs/en/agent-sdk/permissions),
  [Claude Code Permissions 2026](https://www.claudedirectory.org/blog/claude-code-permissions-guide))

- **Per-tenant sandbox isolation.** Industry guidance (NVIDIA's 2026 practical
  controls, Northflank, DigitalApplied) treats network-egress allowlists, workspace
  write restrictions, and config-file protection as the non-negotiable baseline,
  and stresses that in multi-tenant platforms one tenant's workload must not read
  another tenant's data. Container/namespace isolation is "adequate for low-risk,
  insufficient for compliance-sensitive" — which is why our isolation boundary is
  the `(workspace, user)` sandbox identity plus default-deny egress, with MicroVM
  upgrades left out of scope.
  ([Northflank: sandbox AI agents](https://northflank.com/blog/how-to-sandbox-ai-agents),
  [BeyondScale enterprise guide](https://beyondscale.tech/blog/ai-agent-sandboxing-enterprise-security-guide),
  [DigitalApplied isolation patterns](https://www.digitalapplied.com/blog/ai-agent-sandboxing-isolation-patterns-2026))

- **Egress / network policy.** Consensus is **default-deny egress, allowlist only
  the endpoints the agent needs**, enforced via an egress proxy or network policy
  and alerting on everything else (Claude Managed Agents route all egress through a
  JWT-authed TLS-inspecting proxy; iron-proxy is an open-source domain-allowlist
  egress firewall). This matches our existing `defaultAction="deny"` policy; the
  admin rules extend the allowlist (and can add explicit denies) org-wide.
  ([Penligent: Claude Code egress exfil](https://www.penligent.ai/hackinglabs/claude-code-sandbox-bypass/),
  [PipeLab: agent security control layers](https://pipelab.org/learn/agent-security-control-layers/),
  [INNOQ: control their network](https://www.innoq.com/en/blog/2026/03/dev-sandbox-network/))

- **Human-in-the-loop "confirm" UX.** LangChain HITL middleware, OpenAI Agents
  SDK (`needsApproval`), Cloudflare, and Microsoft Agent Framework all converge on:
  the tool call is intercepted, execution is *interrupted* and state persisted, a
  request with the pending action is surfaced to a human, and on response the call
  is approved / edited / rejected and the run resumes. This is the model for our
  "require confirmation" rule — and the part we have the least existing plumbing
  for (see Open Questions).
  ([LangChain HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop),
  [OpenAI Agents SDK HITL](https://openai.github.io/openai-agents-js/guides/human-in-the-loop/),
  [Microsoft Agent Framework approval](https://www.devleader.ca/2026/03/11/tool-approval-and-humanintheloop-in-microsoft-agent-framework))

## Proposed Design

### (a) Ownership migration to (workspace_id, user_id)

The model is already keyed correctly; the work is hardening + backfill.

- **Add a partial unique index** enforcing one running sandbox per identity:
  `uq_user_sandbox_active` on `(org_id, workspace_id, user_id)` where
  `status IN ('provisioning','running')` — both active/in-flight states, so a
  reserved `provisioning` row already blocks a concurrent create (Postgres
  partial unique; mirror with `sqlite_where` for
  tests, matching the `sandbox_env` pattern). This removes the need for
  "newest wins" `order_by` and makes a duplicate-create attempt a catchable
  integrity error instead of a silent second sandbox.
- **Race handling in `get_or_create`.** The naive fix — call
  `opensandbox.Sandbox.create` first, then `repo.create` and catch the unique
  violation — leaks: the losing request has already provisioned a real provider
  sandbox before the violation fires, and after we roll back the DB and reuse the
  winner's row, that just-created provider sandbox has no DB row at all, so the
  TTL reaper (which iterates DB rows) never finds it and the resources leak. So
  the create path must be leak-free in one of two ways, and the plan should pick
  one before coding:
  - **Reserve the row first (preferred).** Insert the `UserSandbox` row in a
    `provisioning` (or `pending`) status *before* calling
    `opensandbox.Sandbox.create`, with the partial unique index covering
    `running`/`provisioning` so the insert itself loses the race and raises the
    integrity error. The loser never provisions a provider sandbox; it rolls
    back, re-queries the winner, and reuses it. The winner provisions, then flips
    its row to `running`. A row stuck in `provisioning` (e.g. crash mid-create)
    is swept by the reaper like any other row — no orphan is possible.
  - **Clean up the orphan (fallback).** If we keep create-then-insert, the race
    handler must explicitly call the provider to destroy the just-created
    `raw_sandbox` before rolling back and reusing the winner, so no provider
    sandbox is ever left without a DB row. This couples the hot path to the
    provider being reachable for the cleanup call; the reserve-row-first approach
    avoids that.
  Either way the concurrency window becomes a reuse (after a health check on the
  winner), not a 500 and not a leak.
- **Backfill.** Pre-existing rows already carry `workspace_id` (the column was
  always present via `OrgScopedMixin`), so the migration is mostly a constraint
  add. Before adding the unique index, the migration must collapse any duplicate
  `running` rows for the same `(org_id, workspace_id, user_id)` down to the newest
  one and mark the rest `terminated` (the providers' sandboxes for the demoted
  rows are left to the TTL reaper — see Open Questions on whether to actively kill
  them). If the platform truly has not shipped, this may be a no-op in practice,
  but the migration must be safe either way.
- No public-ID change: `UserSandbox._PREFIX = "sbx"` already exists.
- **Key the persistent volume on `(workspace_id, user_id)`, not just `user_id`.**
  When `sandbox.volume.enabled` is true, `SandboxManager._build_user_volume(user_id)`
  builds the PVC claim name from the user id alone, and `get_or_create` passes only
  that. So even after we enforce one `UserSandbox` row per `(workspace, user)`, the
  same user in two workspaces mounts the *same* `/workspace` PVC — files, browser
  profiles, and anything written to disk leak across workspaces, and the
  "file written in workspace A is absent in workspace B" isolation E2E would fail.
  The fix is to make the volume identity carry the workspace too: `_build_user_volume`
  takes `(workspace_id, user_id)` and the claim name includes both (e.g. a
  `ws-<workspace_id>-user-<user_id>` shape, sanitised to the provider's PVC
  naming rules), and `get_or_create` passes both. A given `(workspace, user)` then
  gets its own durable volume; two workspaces never share storage. This is the
  storage-layer half of the same ownership boundary the unique index enforces in
  the DB — without it the row-level isolation is cosmetic.
- **Volume back-compat / migration.** Existing PVCs are named from `user_id` only,
  so a user who already has a persistent volume in one workspace will, after the
  rename, get a *new* empty volume keyed on `(workspace, user)` and lose access to
  the old data through the sandbox. The old PVC is not deleted by the rename; it
  is simply orphaned (no sandbox references it). The migration story has two parts,
  both of which the plan must settle (see Open Questions): (1) decide whether to
  leave old single-user PVCs in place for manual recovery / cleanup, or reap them;
  and (2) decide whether to actively migrate any existing user-only volume into one
  of the user's workspaces (only safe if the user has exactly one workspace — with
  multiple, there is no single correct target, so the safe default is "start fresh
  per workspace and leave the old PVC orphaned for an operator to clean up"). If the
  platform truly has not shipped, there may be no real PVCs to migrate and this is a
  no-op — but the claim-name change must still ship so future volumes are scoped.

### (b) Org-admin policy model: image, network rules, command rules

**Where stored.** One row per org-default in a new table `sandbox_policies`
(`SandboxPolicy`, new public-ID prefix `PREFIX_SANDBOX_POLICY = "sbxp"`). This
table is org-only: it carries an `org_id` FK directly and does **not** use
`OrgScopedMixin`, because that mixin adds a required `workspace_id` FK and a
per-org default has no workspace — the admin route context has no real workspace
id to supply, so a `workspace_id` column would force a fake value or trip the
not-null/FK constraint. Instead, a nullable `scope_workspace_id` column is
declared on the table from day one: NULL marks the org-default row (the only
shape v1 ever writes), and a non-NULL value is reserved for v2 per-workspace
overrides — so adding overrides later needs no schema migration. One-per-scope
is enforced by a unique index on `(org_id, scope_workspace_id)` (the same shape
`OrgSettings` uses for org-wide rows, just widened with the override column).
Fields:

- `org_id: str` — FK, the scoping key (declared directly, no mixin).
- `scope_workspace_id: str | None` — NULL = org default; non-NULL reserved for
  v2 workspace overrides; v1 only ever writes NULL.
- `default_image: str` — the single image used at sandbox creation for this
  org. No allowlist in v1: users and agents have no surface to pick an image,
  so an allowlist has nothing to validate against. See OQ-12 for when an
  allowlist becomes meaningful.
- `network_rules: list[NetworkRuleSpec]` (JSONB) — admin-authored egress rules,
  each `{action: "allow" | "deny", target: "<fqdn|wildcard>"}`. Composed with
  the vault-derived allowlist (see delivery below). `default_action` stays
  `deny`.
- `command_rules: list[CommandRuleSpec]` (JSONB) — ordered list, each
  `{action: "deny" | "confirm" | "allow", pattern: "<glob>"}`. Evaluated
  deny → confirm → allow, first match wins (Claude Code semantics).

**Why rules are lists but image is a single value.** Network and command rules
are inherently lists: an org wants to deny several patterns and allow several
hosts in one policy. Image, in v1, has no override surface — no per-user, no
per-agent, no per-task selection — so a list would have nothing to choose
between at runtime. List-shaped fields are reserved for rule lists; image
becomes a list only when a real selector ships (see OQ-12). The network and
command rules ride on the same row as JSONB columns rather than separate
`network_rule` / `command_rule` tables, because they are authored as a single
admin PUT and have no independent lifecycle.

A pure-function policy module (`backend/cubeplex/sandbox_policy/`, e.g.
`rules.py`) holds the matchers and is the single reuse boundary shared by the
admin route, the manager, and the exec middleware — never the route layer.

**Admin routes (scope-isolated).** New `admin_sandbox_policy.py` under
`/api/v1/admin/sandbox-policy`, guarded by `get_admin_request_context`
(org-admin), mirroring `admin_sandbox_env.py`:

- `GET  /admin/sandbox-policy` — read the org's policy (returns defaults if none).
- `PUT  /admin/sandbox-policy` — upsert image / network / command rules.
  Validates command patterns and network targets (reuse
  `host_rules.validate_hosts` for targets where applicable). The handler also
  runs the OQ-6 soft-conflict check: if any `deny` rule covers a host that an
  installed credential in the vault declares as required, the response carries
  a `warnings: [...]` array; the PUT is **not** rejected (admin still saves).
  The vault credential editor route does the symmetric check from the other
  side (warn when a credential's required host is covered by an existing
  `deny` rule).

There is **no** workspace-scoped counterpart in v1 (policy is org-wide; v2
will populate `scope_workspace_id` on a per-workspace row without a schema
change). If v2 adds per-workspace overrides, those land via a separate
`ws_sandbox_policy.py` handler — not a `?scope=` parameter on this one.

A `SandboxPolicyRepository` keyed by `org_id` (with the override column
nullable) handles persistence — modeled on `OrgSettingsRepository`
(`repositories/org_settings.py`), which takes `session` + `org_id` and filters
on `org_id` alone, **not** the workspace-scoped `ScopedRepository[T]` /
`OrgScopedMixin` path that every business-table repo uses. A
`SandboxPolicyService` (`services/sandbox_policy.py`) does CRUD + validation
on top of that repo; a `SandboxPolicyResolver` returns the effective policy
for an `(org, workspace)` pair (workspace override > org default > built-in
defaults), used by both the manager and the middleware. The resolver sketches
the precedence shape but in v1 only the org-default branch (NULL
`scope_workspace_id`) is enabled — the workspace-override branch is dead code
until v2 lights it up.

**Delivery to creation (image + network rules).** In
`SandboxManager.get_or_create`, before `opensandbox.Sandbox.create`:

- Resolve the org policy. Use `policy.default_image` instead of the global
  `self._image` (global config becomes the fallback when no policy row
  exists). Persist the chosen image onto the `UserSandbox` row (already a
  column).
- **Image drift is lazy.** Changing `default_image` only affects sandboxes
  created *after* the change. An existing running sandbox finishes its
  conversation on its original image and is terminated normally at TTL or at
  conversation end. We do **not** recreate mid-conversation: that would lose
  in-sandbox state and violate the single-image-per-run constraint. The next
  new conversation for that user gets the new image. (This is the explicit
  trade-off: a few minutes of "old image still running" for predictable runs.)
- Merge the admin `network_rules` with the vault-derived `NetworkPolicy` from
  `SandboxEnvInjector`: union of allow targets, plus admin `deny` rules
  applied on top (deny wins). Keep `defaultAction="deny"`. Pass the merged
  policy to `Sandbox.create`. Network policy remains immutable for the
  sandbox's life.

**Delivery to exec + enforcement point (command rules).** Command rules are
enforced in `_make_execute_tool._execute` in
`backend/cubeplex/middleware/sandbox.py` — the last cubeplex-owned point before
the command reaches the provider. `SandboxMiddleware.__init__` gains the
resolved `command_rules`. For each `execute` call:

1. Evaluate `args.command` against the ordered rules using the shared
   matcher. The matcher must be shell-operator-aware: a command containing
   `&&`, `;`, `|`, backticks, `$(...)` is split into its constituent commands
   and **every** sub-command must pass; if any sub-command matches a `deny`,
   the whole call is denied. (Mirrors Claude Code's "don't let chaining
   smuggle a denied command".)
2. **deny** → return an `AgentToolResult` with `is_error=True` and text
   "command blocked by org policy: <pattern>"; never call `sandbox.execute`.
   Record nothing in the audit buffer.
3. **confirm** → in v1, **degrade to deny** with a distinct error text
   ("requires confirmation; not yet supported in this deployment") and an
   audit-row tag like `confirmed-action-deferred`. The data is preserved
   (admin can save `confirm`), but the runtime returns deny until HITL ships.
   See the External follow-up callout below.
4. **allow** (or no match → default allow; the org sets `deny`/`confirm` for
   what it cares about) → run as today.

> **External follow-up — cubepi HITL (OQ-1/OQ-2).** Real "pause and prompt
> for approval" is not a cubeplex-only concern: the agent loop, the SSE event
> stream, and the resume hook all live in cubepi (the self-developed
> runtime). The right place to add it is **upstream in cubepi**, not as a
> bespoke cubeplex hack. Concretely: file a cubepi issue for an
> `elicit`/`approve` event channel (a `tool_confirmation_required` SSE event
> + an approve-or-reject hook). Acceptance criteria from cubeplex's side:
> confirmation blocks **only the tool call**, not the whole run (the agent
> loop holds at the pending tool, other middleware keeps responding); the
> approval timeout is **180 seconds**, after which the call is treated as
> `deny` and an audit row is written; the sandbox TTL clock is not paused
> while waiting for approval. Once cubepi ships this channel, cubeplex flips
> the `confirm` branch above from deny-with-message to a real pause-and-
> resume flow (no schema change, the data already carries `confirm`).

The matcher is a pure function in `sandbox_policy/rules.py` so it is
unit-testable in isolation from the middleware.

**v1 command-rule scope.** Command rules in v1 apply only to the `execute`
tool. The dotfile / config-file protection control (`write_file` and
`edit_file` blocked on patterns like `~/.bashrc`, `**/.git/config`) is
deferred to a fast-follow PR — it needs a separate matcher applied to *file
paths*, not command strings, and the validation UX is different. Listed in
"Out of scope" below.

## Data Model & Alembic Migration Sketch

New table `sandbox_policies`:

Org-only table — `org_id` FK declared directly, no `OrgScopedMixin` and no
required `workspace_id` column (see "Where stored" above for why). A nullable
`scope_workspace_id` is reserved for v2 overrides:

```
SandboxPolicy(CubeplexBase, table=True)   # NOT OrgScopedMixin
  _PREFIX = PREFIX_SANDBOX_POLICY  # "sbxp"
  __tablename__ = "sandbox_policies"
  org_id: str = Field(foreign_key="organizations.id", index=True)
  scope_workspace_id: str | None = Field(
      default=None, foreign_key="workspaces.id", index=True,
  )  # NULL = org default (v1); non-NULL = workspace override (v2)
  default_image: str                # single value, no allowlist in v1
  network_rules: list[dict] | None  (JSONB)  # [{action, target}, ...]
  command_rules: list[dict] | None  (JSONB)  # [{action, pattern}, ...]
  __table_args__ = (
      Index(
          "uq_sandbox_policy_scope",
          "org_id", "scope_workspace_id",
          unique=True,
      ),
  )
```

Add `PREFIX_SANDBOX_POLICY = "sbxp"` to `backend/cubeplex/models/public_id.py`.

**One** Alembic migration, generated with `alembic revision --autogenerate`
(do not hand-edit; autogen captures both schema changes in a single run since
both `SandboxPolicy` and the `UserSandbox` index land in metadata at the
same point in the plan):

- Add partial unique index `uq_user_sandbox_active` on `user_sandboxes
  (org_id, workspace_id, user_id)` where `status IN ('provisioning','running')`
  — covering in-flight states so the reserve-row-first insert is genuinely
  single-flight.
- Create `sandbox_policies` with the `(org_id, scope_workspace_id)` unique
  index. No seed rows; absence means "use defaults".
- **No duplicate-collapse step.** OQ-7 dropped that requirement: the
  project has not shipped publicly, so pre-migration data is assumed to have
  no `(org, workspace, user)` duplicate active rows. The migration prose
  notes the assumption explicitly: if a real deployment ever hits dirty
  data here, it's an ops event (run a one-off cleanup script), not migration
  logic.

CHECK constraints for rule shape are optional in v1 (the JSONB arrays are
validated in the service layer); revisit if we want DB-level guarantees like
`sandbox_env`.

## v1 Scope

- `UserSandbox` partial unique index over `('provisioning','running')` +
  reserve-row-first create race handling + stuck-provisioning reaper. No
  duplicate-collapse step in the migration (clean-data assumption, OQ-7).
- Persistent volume claim-name re-keyed on `(workspace_id, user_id)` in
  `SandboxManager._build_user_volume` / `get_or_create`. One-time CLI helper
  `backend/scripts/dev/migrate_user_pvcs.py` (dry-run by default, `--apply`
  flag) migrates pre-rename single-user PVCs *only when unambiguous* (user
  has exactly one workspace); ambiguous cases are flagged for manual
  operator cleanup. See OQ-9.
- `SandboxPolicy` table (org-only, with reserved `scope_workspace_id`
  column) + public-ID prefix + single migration covering both schema
  changes.
- `SandboxPolicyService` / `SandboxPolicyResolver` + `sandbox_policy/rules.py`
  matcher (network + command).
- Admin routes `GET`/`PUT /api/v1/admin/sandbox-policy`. PUT returns a
  `warnings: [...]` array on credential-host conflicts (OQ-6); it does NOT
  reject. The vault credential editor route gains a symmetric warning.
- Manager wiring: resolve policy → org `default_image` + merged network
  policy at sandbox create. Image drift is lazy (next new conversation
  picks up the new image; existing sandboxes finish on their original).
- Middleware wiring: command-rule enforcement at `_execute` scoped to the
  `execute` tool only. `confirm` action degrades to deny at runtime (data
  preserved). `write_file`/`edit_file` protection deferred (OQ-10).
- Admin console UI page (`/admin/sandbox-policy`) for editing image +
  network + command rules.
- Workspace-side read-only sandbox status page (`/w/[wsId]/sandbox`) for
  users to verify their sandbox state and the active default image.
- Vault credential editor: yellow-banner warning when a credential's
  required hosts overlap an existing `deny` rule.

## Workspace UI (scope-isolated pages)

Per the project's "scope-isolated pages" rule, the policy is org-scoped and
the user's sandbox status is workspace-scoped, so they get separate Next
routes and separate page files — no `mode?: 'admin' | 'workspace'` prop.

**Admin policy editor** — `/admin/sandbox-policy` (org-admin area, NOT
under `w/[wsId]/`):

- A single form, one PUT on save.
- `default_image` — plain text input (v1 has no allowlist; a "previously
  used" suggestions list can come later).
- Network rules — a small table with two columns (`action`, `target`), add
  / remove / reorder rows. Targets validated client-side as FQDN or
  wildcard before submit.
- Command rules — a small table with two columns (`action`, `pattern`).
  `action` is a select with `allow` / `deny` / `confirm`. An inline hint
  next to the `confirm` option reads: "confirm is currently treated as
  deny at runtime; full prompt-for-approval requires upstream cubepi
  changes."
- On save, if the response carries `warnings[]`, render each as an inline
  banner under the network-rules table (e.g. "deny on `api.github.com`
  conflicts with installed credential `github-pat`; outbound calls will be
  blocked until the rule is removed").

**Workspace sandbox status** — `/w/[wsId]/sandbox`, read-only:

- Shows the current `UserSandbox` row for the signed-in user in this
  workspace: state (`provisioning` / `running` / `paused` / `terminated`),
  the `default_image` in use, `last_activity_at`, and an "Open browser"
  link if the sandbox exposes a live browser panel.
- No mutations in v1. The purpose is to let a user verify their sandbox is
  alive and matches the admin's `default_image`.

**Vault credential editor warning** — extension of the existing credential
edit page:

- When the user saves (or opens) a credential whose declared required
  hosts are covered by an existing `deny` rule in the org's
  `SandboxPolicy`, show a yellow banner: "This credential's required
  hosts include X; the org sandbox policy currently denies X. Outbound
  calls to X will be blocked. Coordinate with your admin to allow this
  host." No hard block.

## Testing Strategy (E2E-first)

E2E is the priority per repo discipline; the matcher is the one piece that earns a
unit test because its correctness (shell-chaining) is subtle and hard to observe
end-to-end.

- **E2E — ownership isolation.** Same user, two workspaces, drive an agent run in
  each; assert two distinct `UserSandbox` rows / provider ids, and that a file
  written in workspace A is absent in workspace B's sandbox. Assert a second
  concurrent `get_or_create` reuses, not duplicates (no second running row).
- **E2E — image policy.** Admin sets `default_image`; a new run creates a sandbox
  with that image (assert persisted `UserSandbox.image`). Change the image
  mid-conversation; assert the *existing* sandbox keeps its old image until
  conversation end / TTL (lazy drift), and that a brand-new conversation
  picks up the new image.
- **E2E — network rules.** Admin adds a `deny` target; assert egress to it fails
  while an allowed target succeeds (reuse the egress test harness; needs the
  exchange host configured, otherwise fall back to asserting the `NetworkPolicy`
  passed to create).
- **E2E — command deny.** Admin denies `rm *`; agent attempts `rm -rf /workspace`;
  assert the tool returns the blocked error and the file system is untouched
  (use the audit buffer to confirm nothing executed).
- **E2E — command confirm degrades to deny.** v1: assert a `confirm` rule
  returns the deny-with-message error and never executes. (Real
  pause-and-prompt assertions land alongside the cubepi HITL follow-up.)
- **E2E — credential conflict warning.** Install a credential whose required
  host is `api.github.com`; admin PUTs a policy that denies
  `api.github.com`; assert the PUT response carries a `warnings[]` entry
  naming the credential, and that the PUT was NOT rejected. Symmetric test:
  with the deny already in place, save a credential targeting that host and
  assert the credential editor shows the yellow banner.
- **Playwright smoke — admin policy page.** Open `/admin/sandbox-policy`,
  add a `deny` rule whose target matches an installed credential, save,
  assert the warning banner is visible; remove the deny, save, assert the
  banner clears.
- **Unit — matcher.** `sandbox_policy/rules.py`: precedence (deny > confirm >
  allow), wildcard matching, and the shell-chaining cases
  (`safe && rm -rf /`, `safe; denied`, `$(denied)`, backticks).
- **Unit — policy resolver defaults** when no row exists.

If the egress/exchange infra cannot be simulated locally, the network-rule check
falls back to a unit assertion on the merged `NetworkPolicy` — not a fake-server
E2E (per the "no fake E2E for unsimulatable systems" discipline).

## Open Questions

Resolved as of 2026-05-28 (session pass). The original numbering is kept so
PR history references are still legible; CHANGE markers flag OQs whose
resolution diverges from the originally-sketched answer.

- **OQ-1 — How does "require confirmation" surface to the user mid-run?**
  **Resolved 2026-05-28:** v1 degrades `confirm` to `deny` at runtime
  (data is preserved; admin can save `confirm`). Real HITL is an **external
  follow-up in cubepi** — cubepi is the self-developed runtime and owns the
  agent loop, the SSE event channel, and the resume hook, so the right fix
  is upstream, not a cubeplex hack. File a cubepi issue for an
  `elicit`/`approve` event channel (`tool_confirmation_required` SSE event
  + approve/reject hook). Once shipped, cubeplex switches the `confirm`
  branch from deny-with-message to a real pause-and-resume flow (no schema
  change).
- **OQ-2 — Does confirmation block the whole run or just the tool call?
  Timeout?** **CHANGE — Resolved 2026-05-28:** depends on OQ-1 cubepi work.
  Confirmation blocks **only the tool call**, not the whole run (the agent
  loop holds at the pending tool; other middleware keeps responding). The
  approval **timeout is 180 seconds**; timed-out is treated as `deny` with
  an audit row. The sandbox TTL clock does not pause while waiting. These
  are cubeplex-side acceptance criteria for the upstream cubepi issue.
- **OQ-3 — Per-workspace / per-user policy overrides (v2).**
  **Resolved 2026-05-28:** add the `scope_workspace_id: str | None` column
  to `SandboxPolicy` **now** (nullable, default NULL). v1 only writes NULL
  (org-default rows). The resolver sketches the precedence shape (workspace
  override > org default) but only the NULL branch is exercised. v2 will
  populate `scope_workspace_id` for per-workspace overrides without a
  schema migration.
- **OQ-4 — Image allowlist semantics.** **CHANGE — Resolved 2026-05-28:
  dropped entirely.** `allowed_images` is removed from v1. With no user /
  agent / per-task surface to override the image, an allowlist has nothing
  to validate against. Single `default_image` only. The "what happens to
  running sandboxes when the image is removed from the allowlist" sub-
  question vanishes with it. See OQ-12 for when an allowlist becomes
  meaningful.
- **OQ-5 — Image drift recreation cost.** **Resolved 2026-05-28:** drift is
  **lazy**. Existing running sandboxes finish their conversation on their
  original image and are terminated normally at TTL or conversation end.
  Only **new conversations** create a sandbox with the new image. Mid-run
  image switching is forbidden (matches the single-image-per-run
  constraint). The admin UX accepts a few minutes of "old image still
  running" in exchange for predictable runs.
- **OQ-6 — Network rule conflict resolution.** **CHANGE — Resolved
  2026-05-28: do NOT hard-reject.** Show **warnings on both sides**:
  - When admin PUTs a policy whose `deny` rule covers a host that an
    installed credential declares as required, the response carries a
    `warnings: [...]` array listing the conflicting credentials, but the
    PUT succeeds and the policy saves.
  - When a user/admin saves or edits a vault credential whose required
    hosts are covered by an existing `deny` rule, the credential editor
    shows a yellow warning banner. No hard block.
  - At runtime, `deny` still wins (a blocked outbound request returns the
    usual blocked-by-policy error) plus a structured warning log when the
    blocked host matches a known credential.
- **OQ-7 — Duplicate-collapse: kill demoted provider sandboxes?**
  **CHANGE — Resolved 2026-05-28: dropped entirely.** Migration does **not**
  include a duplicate-collapse step. The project has not shipped publicly,
  so pre-migration data is assumed to have no `(org, workspace, user)`
  duplicate active rows. If a real deployment ever hits dirty data, it's
  an ops event (run a one-off script), not migration logic. The migration
  prose carries this assumption explicitly.
- **OQ-8 — Create-race strategy: reserve-row-first vs orphan-cleanup.**
  **Resolved 2026-05-28: reserve-row-first.** Confirm and lock the strategy:
  INSERT a `UserSandbox` row with `status='provisioning'` **before**
  `provider.create()`; the partial unique index covers both
  `('provisioning','running')` so the reservation participates in
  single-flight; a stuck-provisioning reaper periodically cleans rows
  older than N minutes still in `provisioning`. (Plan Task 5/6 already
  reflect this from codex round 3.)
- **OQ-9 — Orphaned single-user PVCs after the volume rename.**
  **Resolved 2026-05-28:** one-time migration of single-user PVCs to
  `(workspace, user)` keying **when unambiguous** (user has exactly one
  workspace at migration time). Ambiguous cases (user in multiple
  workspaces) are **left to operator manual cleanup** — there is no single
  correct target workspace to choose. The CLI helper
  `backend/scripts/dev/migrate_user_pvcs.py` prints planned actions in
  dry-run mode by default and accepts `--apply` to perform them.
- **OQ-10 — Command matcher scope (execute vs write_file/edit_file).**
  **Resolved 2026-05-28:** v1 command rules apply to `execute` **only**.
  Dotfile / config-file protection for `write_file` / `edit_file` is
  deferred to a fast-follow PR (separate matcher applied to file paths,
  different validation UX). Listed in "Out of scope".
- **OQ-11 — Single-tenant deployment.** **Resolved 2026-05-28:** in
  `single_tenant` mode the single shared org has one policy row in the DB.
  This is acceptable — the org owner is the operator, the admin route is
  reachable, and OSS doesn't need a separate static-config path.
- **OQ-12 (NEW) — Future work: allowed_images / sandbox_images table.**
  **Deferred.** `allowed_images` becomes meaningful only when an override
  surface exists. The most likely trigger is #153 managed agents declaring
  `image: ...` in their definition (or a future per-task image option). At
  that point, add `allowed_images` (or a separate `sandbox_images` table)
  *together with* the override surface and a validator. Until then,
  `default_image` is the only image knob.

## References

- Code: `backend/cubeplex/models/user_sandbox.py`,
  `backend/cubeplex/repositories/user_sandbox.py`,
  `backend/cubeplex/sandbox/manager.py`, `backend/cubeplex/sandbox/opensandbox.py`,
  `backend/cubeplex/middleware/sandbox.py`,
  `backend/cubeplex/sandbox_env/injector.py`,
  `backend/cubeplex/api/routes/v1/admin_sandbox_env.py`,
  `backend/cubeplex/api/routes/v1/ws_sandbox_env.py`,
  `backend/cubeplex/services/sandbox_env.py`, `backend/cubeplex/models/public_id.py`.
- Docs: `backend/docs/auth.md`, `CLAUDE.md` (scope-isolation rules),
  `docs/dev/specs/2026-05-20-sandbox-browser-takeover-design.md`,
  `docs/dev/plans/2026-05-25-sandbox-env-vault.md`.
- Industry:
  [Claude Code permissions](https://code.claude.com/docs/en/agent-sdk/permissions),
  [Claude Code Permissions 2026](https://www.claudedirectory.org/blog/claude-code-permissions-guide),
  [Northflank: sandbox AI agents](https://northflank.com/blog/how-to-sandbox-ai-agents),
  [BeyondScale enterprise guide](https://beyondscale.tech/blog/ai-agent-sandboxing-enterprise-security-guide),
  [DigitalApplied isolation patterns](https://www.digitalapplied.com/blog/ai-agent-sandboxing-isolation-patterns-2026),
  [Penligent: Claude Code egress exfil](https://www.penligent.ai/hackinglabs/claude-code-sandbox-bypass/),
  [PipeLab: agent security control layers](https://pipelab.org/learn/agent-security-control-layers/),
  [INNOQ: control their network](https://www.innoq.com/en/blog/2026/03/dev-sandbox-network/),
  [LangChain HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop),
  [OpenAI Agents SDK HITL](https://openai.github.io/openai-agents-js/guides/human-in-the-loop/),
  [Microsoft Agent Framework approval](https://www.devleader.ca/2026/03/11/tool-approval-and-humanintheloop-in-microsoft-agent-framework).
