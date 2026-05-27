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
   `git push`", or "only allow these images". Org admins need a console where they
   set, per org: the **default sandbox image**, the **network egress rules**, and
   **command execution rules** (deny / require-confirmation / allow). The command
   rules must be *enforced in the exec path*, not just advisory text in the prompt.

## Goals / Non-goals

### Goals

- Make sandbox ownership structurally `(org_id, workspace_id, user_id)` with a DB
  uniqueness guarantee for the active sandbox, plus a documented backfill.
- Add an org-admin policy record (one per org) holding: default image, egress
  network rules, and command rules.
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

- `backend/cubebox/models/user_sandbox.py` — `UserSandbox(CubeboxBase,
  OrgScopedMixin)`. Carries `user_id`, `workspace_id` (via mixin), `org_id`,
  `sandbox_id` (provider id, `unique=True`), `status`, `image`, `ttl_seconds`,
  `last_activity_at`. Two non-unique indexes:
  `ix_user_sandboxes_user_ws_status` and `ix_user_sandboxes_org_ws`. There is
  **no** uniqueness on `(org_id, workspace_id, user_id, status='running')`, so
  two concurrent `get_or_create` calls could both create a running row.
- `backend/cubebox/repositories/user_sandbox.py` —
  `UserSandboxRepository(ScopedRepository[UserSandbox])`. `get_active_by_user`
  is already workspace-scoped (the repo is constructed with `workspace_id`), and
  returns the newest `status='running'` row. The "newest wins" `order_by` is a
  symptom of the missing unique constraint.
- `backend/cubebox/sandbox/manager.py` — `get_or_create(user_id, *, org_id,
  workspace_id)` looks up the active row for that `(user, workspace)`, health-
  checks it, reuses or recreates. Create persists via `repo.create(...)` and
  sets `image=self._image`.

The data model is *already* per-`(workspace, user)`. The remaining work is
correctness hardening (unique constraint, race handling) + a documented backfill
for any pre-existing single-key rows, not a structural redesign.

### Exec path

- `backend/cubebox/middleware/sandbox.py` — `SandboxMiddleware` exposes
  `execute`, `write_file`, `edit_file`, `file_read` as cubepi `AgentTool`s.
  `_make_execute_tool._execute` calls `sandbox.execute(args.command)` directly,
  with no inspection of the command string. The only existing hook is an
  audit ring buffer (`_record_executed`) gated behind `enable_audit()` for tests.
  **This `_execute` closure is the natural enforcement point for command rules.**
- `backend/cubebox/sandbox/opensandbox.py` — `OpenSandbox.execute` runs the
  command via the provider; `set_run_env` injects run-level env.

### Admin config today

- Sandbox image: global config only (`sandbox.image` in
  `backend/cubebox/sandbox/manager.py:__init__`). Not org-configurable.
- Network policy: built by `backend/cubebox/sandbox_env/injector.py`
  (`SandboxEnvInjector.build`) as `NetworkPolicy(defaultAction="deny",
  egress=[allow <secret hosts> + exchange_host])`. It is a side effect of the
  secret env vault, set once at `Sandbox.create` (manager.py ~line 267). There is
  no admin-authored allow/deny list.
- Command rules: none. The system prompt (`SANDBOX_PROMPT_TEMPLATE`) is the only
  influence on what the agent runs, and it is not enforcement.
- Existing scope-isolated admin/ws split to mirror:
  `backend/cubebox/api/routes/v1/admin_sandbox_env.py` (uses
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
  `status = 'running'` (Postgres partial unique; mirror with `sqlite_where` for
  tests, matching the `sandbox_env` pattern). This removes the need for
  "newest wins" `order_by` and makes a duplicate-create attempt a catchable
  integrity error instead of a silent second sandbox.
- **Race handling in `get_or_create`.** On the create path, catch the unique-
  violation, roll back, re-query the now-winning row, and reuse it (after a health
  check). This turns the concurrency window into a reuse, not a 500.
- **Backfill.** Pre-existing rows already carry `workspace_id` (the column was
  always present via `OrgScopedMixin`), so the migration is mostly a constraint
  add. Before adding the unique index, the migration must collapse any duplicate
  `running` rows for the same `(org_id, workspace_id, user_id)` down to the newest
  one and mark the rest `terminated` (the providers' sandboxes for the demoted
  rows are left to the TTL reaper — see Open Questions on whether to actively kill
  them). If the platform truly has not shipped, this may be a no-op in practice,
  but the migration must be safe either way.
- No public-ID change: `UserSandbox._PREFIX = "sbx"` already exists.

### (b) Org-admin policy model: image, network rules, command rules

**Where stored.** One row per org in a new table `sandbox_policies`
(`SandboxPolicy`, `OrgScopedMixin`, new public-ID prefix `PREFIX_SANDBOX_POLICY
= "sbxp"`). One-per-org enforced by a unique index on `org_id`. Fields:

- `default_image: str` — the image used at sandbox creation for this org.
- `allowed_images: list[str] | None` (JSON) — optional allowlist the
  `default_image` (and any future per-run image choice) must belong to. `None`
  means "no restriction beyond default_image".
- `network_rules: list[NetworkRuleSpec]` (JSON) — admin-authored egress rules,
  each `{action: "allow" | "deny", target: "<fqdn|wildcard>"}`. Composed with the
  vault-derived allowlist (see delivery below). `default_action` stays `deny`.
- `command_rules: list[CommandRuleSpec]` (JSON) — ordered list, each
  `{effect: "deny" | "confirm" | "allow", pattern: "<glob>"}`. Evaluated
  deny → confirm → allow, first match wins (Claude Code semantics).

A pure-function policy module (`backend/cubebox/sandbox_policy/`, e.g.
`rules.py`) holds the matchers and is the single reuse boundary shared by the
admin route, the manager, and the exec middleware — never the route layer.

**Admin routes (scope-isolated).** New `admin_sandbox_policy.py` under
`/api/v1/admin/sandbox-policy`, guarded by `get_admin_request_context`
(org-admin), mirroring `admin_sandbox_env.py`:

- `GET  /admin/sandbox-policy` — read the org's policy (returns defaults if none).
- `PUT  /admin/sandbox-policy` — upsert image / network / command rules. Validates
  command patterns and network targets (reuse `host_rules.validate_hosts` for
  targets where applicable). Image allowlist membership is validated here.

There is **no** workspace-scoped counterpart in v1 (policy is org-wide). If v2
adds per-workspace overrides, that becomes a separate `ws_sandbox_policy.py`
handler — not a `?scope=` parameter on this one.

A `SandboxPolicyService` (`services/sandbox_policy.py`) does CRUD + validation;
a `SandboxPolicyResolver` returns the effective policy for an org (policy row or
built-in defaults), used by both the manager and the middleware.

**Delivery to creation (image + network rules).** In
`SandboxManager.get_or_create`, before `opensandbox.Sandbox.create`:

- Resolve the org policy. Use `policy.default_image` instead of the global
  `self._image` (global config becomes the fallback when no policy row exists).
  Persist the chosen image onto the `UserSandbox` row (already a column) so
  reuse never silently runs a stale image — if the policy's `default_image`
  changed since creation, treat the existing sandbox as stale and recreate
  (single-image constraint: cannot swap mid-life).
- Merge the admin `network_rules` with the vault-derived `NetworkPolicy` from
  `SandboxEnvInjector`: union of allow targets, plus admin `deny` rules applied
  on top (deny wins). Keep `defaultAction="deny"`. Pass the merged policy to
  `Sandbox.create`. Network policy remains immutable for the sandbox's life.

**Delivery to exec + enforcement point (command rules).** Command rules are
enforced in `_make_execute_tool._execute` in
`backend/cubebox/middleware/sandbox.py` — the last cubebox-owned point before the
command reaches the provider. `SandboxMiddleware.__init__` gains the resolved
`command_rules` (and a confirmation callback — see below). For each `execute`
call:

1. Evaluate `args.command` against the ordered rules using the shared matcher.
   The matcher must be shell-operator-aware: a command containing `&&`, `;`,
   `|`, backticks, `$(...)` is split into its constituent commands and **every**
   sub-command must pass; if any sub-command matches a `deny`, the whole call is
   denied. (Mirrors Claude Code's "don't let chaining smuggle a denied command".)
2. **deny** → return an `AgentToolResult` with an error text ("command blocked by
   org policy: <pattern>"); never call `sandbox.execute`. Record nothing in the
   audit buffer.
3. **confirm** → request a human decision (mechanism is an Open Question). On
   approve, run; on reject, return a tool error explaining the user declined.
4. **allow** (or no match → default allow, matching the permissive baseline; the
   org sets `deny`/`confirm` for what it cares about) → run as today.

The matcher is a pure function in `sandbox_policy/rules.py` so it is unit-testable
in isolation from the middleware.

## Data Model & Alembic Migration Sketch

New table `sandbox_policies`:

```
SandboxPolicy(CubeboxBase, OrgScopedMixin, table=True)
  _PREFIX = PREFIX_SANDBOX_POLICY  # "sbxp"
  __tablename__ = "sandbox_policies"
  org_id: str (FK organizations.id)         # from OrgScopedMixin
  default_image: str
  allowed_images: list[str] | None   (JSON)
  network_rules: list[dict] | None   (JSON)  # [{action, target}, ...]
  command_rules: list[dict] | None   (JSON)  # [{effect, pattern}, ...]
  __table_args__ = (
      Index("uq_sandbox_policy_org", "org_id", unique=True),
  )
```

Add `PREFIX_SANDBOX_POLICY = "sbxp"` to `backend/cubebox/models/public_id.py`.

Two migrations, generated with `alembic revision --autogenerate` (do not
hand-edit; the data-collapse step below is the one place autogen needs a manual
data op appended):

1. **Ownership hardening** — add partial unique index `uq_user_sandbox_active`
   on `user_sandboxes (org_id, workspace_id, user_id)` where `status='running'`.
   Prepend a data step that demotes duplicate running rows to `terminated`
   (keep newest by `created_at`) so the index can be created without violation.
2. **Policy table** — create `sandbox_policies` with the unique-on-`org_id`
   index. No seed rows; absence means "use defaults".

CHECK constraints for rule shape are optional in v1 (the JSON arrays are validated
in the service layer); revisit if we want DB-level guarantees like `sandbox_env`.

## v1 Scope

- `UserSandbox` partial unique index + race handling + duplicate-collapse
  migration.
- `SandboxPolicy` table + public-ID prefix + migration.
- `SandboxPolicyService` / `SandboxPolicyResolver` + `sandbox_policy/rules.py`
  matcher (network + command).
- Admin routes `GET`/`PUT /api/v1/admin/sandbox-policy`.
- Manager wiring: resolve policy → org `default_image` (with allowlist check) +
  merged network policy at create; recreate on image drift.
- Middleware wiring: command-rule enforcement at `_execute` for deny + allow.
- **Confirm** enforcement: implement deny + allow fully; ship `confirm` behind the
  decision in Open Questions — if no interrupt channel lands in v1, `confirm`
  degrades to `deny` with a distinct message ("requires confirmation; not yet
  supported") rather than silently allowing.
- Admin console UI page (separate Next route) to edit the three rule sets.

## Testing Strategy (E2E-first)

E2E is the priority per repo discipline; the matcher is the one piece that earns a
unit test because its correctness (shell-chaining) is subtle and hard to observe
end-to-end.

- **E2E — ownership isolation.** Same user, two workspaces, drive an agent run in
  each; assert two distinct `UserSandbox` rows / provider ids, and that a file
  written in workspace A is absent in workspace B's sandbox. Assert a second
  concurrent `get_or_create` reuses, not duplicates (no second running row).
- **E2E — image policy.** Admin sets `default_image`; a new run creates a sandbox
  with that image (assert persisted `UserSandbox.image`). Change the image; assert
  the next run recreates rather than reusing the stale one.
- **E2E — network rules.** Admin adds a `deny` target; assert egress to it fails
  while an allowed target succeeds (reuse the egress test harness; needs the
  exchange host configured, otherwise fall back to asserting the `NetworkPolicy`
  passed to create).
- **E2E — command deny.** Admin denies `rm *`; agent attempts `rm -rf /workspace`;
  assert the tool returns the blocked error and the file system is untouched
  (use the audit buffer to confirm nothing executed).
- **E2E — command confirm** (only if the interrupt channel ships): assert the run
  pauses, surfaces the pending command, and resumes/aborts on the decision.
- **Unit — matcher.** `sandbox_policy/rules.py`: precedence (deny > confirm >
  allow), wildcard matching, and the shell-chaining cases
  (`safe && rm -rf /`, `safe; denied`, `$(denied)`, backticks).
- **Unit — policy resolver defaults** when no row exists.

If the egress/exchange infra cannot be simulated locally, the network-rule check
falls back to a unit assertion on the merged `NetworkPolicy` — not a fake-server
E2E (per the "no fake E2E for unsimulatable systems" discipline).

## Open Questions

- **How does "require confirmation" surface to the user mid-run?** The SSE
  conversation stream (`api/routes/v1/conversations.py`) has no
  elicit/interrupt/approve event today. Options: (a) a new SSE event type
  `tool_confirmation_required` + a `POST .../confirm` endpoint that unblocks the
  paused `_execute` (LangChain/OpenAI HITL pattern), persisting pending state so a
  reconnect can resume; (b) a cubepi-level approval hook if one exists upstream —
  needs investigation (cubepi is the self-developed runtime; prefer fixing
  upstream over a cubebox hack). Until decided, `confirm` degrades to `deny`.
- **Does confirmation block the whole run or just the tool call?** A long human
  delay holds the agent loop and the sandbox TTL clock. Need a confirmation
  timeout and a defined "timed-out = reject" behaviour.
- **Per-workspace / per-user policy overrides (v2).** Out of v1, but the table
  shape (one row per org) forecloses it. If we want overrides later, do we add a
  scope column like `sandbox_env`, or a separate `workspace_sandbox_policies`
  table + a resolver with precedence? Decide before locking the schema if v2 is
  near.
- **Image allowlist semantics.** Is `allowed_images` an exact-match set or do we
  allow tags/wildcards (`myrepo/img:*`)? And what happens to *running* sandboxes
  whose image is later removed from the allowlist — kill on next turn, or leave to
  TTL?
- **Image drift recreation cost.** Recreating on every `default_image` change is
  correct but expensive (cold pull + lost in-sandbox state). Do we recreate
  eagerly, or only mark stale and recreate on the next *new* conversation? What is
  the UX when an admin changes the image while a user is mid-conversation?
- **Network rule conflict resolution.** When an admin `deny` target overlaps a
  vault secret's required host allowlist, the secret silently can't reach its
  host. Should the admin `PUT` reject such a conflict, or warn, or let deny win
  silently? (Deny-wins is the safe default but produces confusing failures.)
- **Duplicate-collapse: kill demoted provider sandboxes?** The ownership migration
  marks duplicate rows `terminated` in the DB; should it also call the provider to
  destroy the orphaned sandboxes, or rely on the TTL reaper? Active kill is cleaner
  but couples a migration to the provider being reachable.
- **Command matcher scope.** Do command rules also apply to `write_file` /
  `edit_file` (e.g. deny writing to `~/.bashrc` / dotfiles, per NVIDIA's
  config-file-protection control), or only to `execute`? v1 sketch covers
  `execute` only.
- **Single-tenant deployment.** In `single_tenant` mode there is one shared org —
  one policy row governs everyone. Is that acceptable, or does OSS want the policy
  to live in static config instead of an admin-editable DB row?

## References

- Code: `backend/cubebox/models/user_sandbox.py`,
  `backend/cubebox/repositories/user_sandbox.py`,
  `backend/cubebox/sandbox/manager.py`, `backend/cubebox/sandbox/opensandbox.py`,
  `backend/cubebox/middleware/sandbox.py`,
  `backend/cubebox/sandbox_env/injector.py`,
  `backend/cubebox/api/routes/v1/admin_sandbox_env.py`,
  `backend/cubebox/api/routes/v1/ws_sandbox_env.py`,
  `backend/cubebox/services/sandbox_env.py`, `backend/cubebox/models/public_id.py`.
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
