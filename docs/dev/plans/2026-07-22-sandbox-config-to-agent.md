# Sandbox config visibility for agent — implementation plan

Related: #398 · Spec: `docs/dev/specs/2026-07-22-sandbox-config-to-agent-design.md`

**Goal**: Tool-only delivery of network policy + env inventory (no secrets) to
the agent via an eager `sandbox_config` tool. No system-prompt diagnosis blurb;
no `DeferredToolGroup` in v1. Reliable policy_deny paths get a short
deterministic nudge toward the tool.

**Architecture**: Pure serialization helpers over **resolvers** (not CRUD
services); one tool registered with sandbox tools; per-call session factory
supplies `org_id` / `workspace_id` / `user_id`. Playbook lives in the tool
description, optional result `guidance`, and policy_deny error appends.

**Tech stack** (use these types — names matter):

| Use | Do not use on agent path |
| --- | --- |
| `SandboxPolicyResolver.resolve()` → `EffectivePolicy` | `SandboxPolicyService` alone as “read” without defaults |
| `SandboxEnvResolver.resolve()` + winning-row metadata | `SandboxEnvService` (CRUD + `CredentialService`) |
| Fresh `AsyncSession` per tool call | Long-lived session on `SandboxMiddleware` |
| `EnvInventoryItem` DTO (no value / credential_id) | Raw `ResolvedEnv` with `value` / `credential_id` |

cubepi `AgentTool`; extend `SandboxMiddleware` / `run_manager` wiring.

---

## Unit 1: Safe serializers (no values)

**Files**:
- `backend/cubeplex/services/sandbox_runtime_config.py` (new) **or**
  `backend/cubeplex/sandbox_policy/agent_view.py` (new)

**Functions** (sketch):

```python
def serialize_network_policy(policy: EffectivePolicy) -> dict[str, Any]:
    # WHITELIST only: default_action, rules[{action, target}],
    # egress_proxy: "set"|"unset", truncated, policy_source, sandbox_note
    # Never forward raw rule dicts / model_dump()

def serialize_env_inventory(meta: list[EnvInventoryItem]) -> list[dict]:
    # WHITELIST: env_name, kind, scope, status?, hosts, header_names
    # NEVER value, credential_id, secret material, extra keys

def serialize_command_rules(policy: EffectivePolicy) -> list[dict]:
    # WHITELIST: action + pattern only
```

**DTO**: `EnvInventoryItem` populated from the **winning valid** `SandboxEnvVar`
row during resolve (or a parallel walk of the same resolution order): scope +
hosts/headers + kind. **No** decrypted value field. Do not re-query unscoped
rows in a way that bypasses effective merge.

**Invalid rows**: `list_for_resolution` already filters `status == "valid"`.
v1 inventory = inject set only. Do not invent “invalid winner” reporting.

**Important**: `ResolvedEnv` has `value` / `credential_id` for injection —
agent path must not receive that object unfiltered. Never construct
`CredentialService` here.

**Tests** (`backend/tests/unit/test_sandbox_runtime_config_serialize.py`):
- Secret row with fake value never appears in JSON output
- Plain row value never appears even if present on DTO
- Caps/truncation marker
- Hosts and header_names preserved for secrets
- Fixtures with **extra forbidden keys** on rules/env rows never appear
- Recursive scan: no `credential_id`, proxy credentials, or value-like fields
- Scope present for winning rows; precedence cases
- Invalid / non-valid rows do not appear (match inject set)

---

## Unit 2: `sandbox_config` tool (eager) + DI

**Files**:
- `backend/cubeplex/tools/builtin/sandbox_config.py` (new) **or** factory inside
  `middleware/sandbox.py` next to execute/write tools
- `middleware/sandbox.py` — extend constructor kwargs for diagnosis context
- `streams/run_manager.py` — pass `org_id`, `user_id`, session factory (or
  thin manager) when building `SandboxMiddleware`

**Behavior**:
1. On each call: open session → `SandboxPolicyResolver.resolve()` →
   `SandboxEnvResolver` / inventory build for `(workspace_id, user_id)` →
   serialize → close session.
2. Return JSON: `network`, `env`, optional `command_rules`, `truncated` flags,
   `policy_source`, `sandbox_note` (rules apply at create; recreate after
   admin edits).
3. Optional short static `guidance` string in the result — **not** copied into
   `SANDBOX_PROMPT_TEMPLATE`.
4. Explicitly **do not** construct `SandboxEnvService` / decrypt credentials.

**Tool description** (static English, in the AgentTool schema): call on
network / auth / missing-env failures; do not invent credentials; never print
secret values; prefer this tool over `printenv` for diagnosis.

**Args**: none required for v1 (optional `section: network|env|all` later).

**Not in v1**: `DeferredToolGroup` / `load_tools` path; capability registry
entry under `agents/actions/`.

**Tests**:
- unit with mocked resolvers / session factory
- assert fresh session opened per call (mock factory call count) if easy
- e2e optional: member agent tool call returns rules from seeded policy
- assert tool result never contains secret values / credential ids
- assert tool is present when sandbox tools are registered
- assert no CredentialService construction on path

---

## Unit 3: Reliable policy_deny hints (v1)

**Files**: execute path / command-rule deny messages already emitted by
`SandboxMiddleware.before_tool_call` / execute error mapping

When a failure is **already known** to be policy/command deny, append one
short line, e.g. `For network/env inventory call sandbox_config` (and keep any
existing pattern-specific deny text). Skip if the failure is ambiguous
(generic timeout, generic 401 without structured egress reason).

This is **not** a system-prompt blurb and is **not** optional for clear
policy_deny paths in v1 — it is the deterministic recall path.

**Tests**: unit/e2e that a denied command (or mocked policy_deny) includes the
nudge; ambiguous errors do not fabricate a false “host X denied” claim.

---

## Unit 4: Wiring + docs

**Files**:
- finish `middleware` / `run_manager` wiring from Unit 2
- `docs/site/docs/admin/sandbox.md` — short “agent troubleshooting metadata”
  note (implementation PR); mention expanded visibility of env **names**

**Explicit non-work**: do **not** edit `prompts/sandbox.py` /
`SANDBOX_PROMPT_TEMPLATE` for a diagnosis blurb.

**Tests**: registry/tool-list unit if pattern exists; no new system-prompt
snapshot requirement for this feature.

---

## Unit 5 (optional later): Opaque network enrichment

When egress/sidecar returns a **structured** host deny that is reliable, map
it to a one-line hint. Do not guess on plain connection refused / timeout.

---

## Delivery order

1. Unit 1 serializers + inventory DTO + redaction tests
2. Unit 2 eager tool + DI (session factory, resolvers)
3. Unit 3 policy_deny hints (v1)
4. Unit 4 admin docs / polish
5. Unit 5 optional

## Out of scope

- Agent mutating policy/env
- Putting secret values or placeholders into system prompt
- Diagnosis playbook or live policy dump in the stable system prefix
- `DeferredToolGroup` for a single `sandbox_config` tool
- Always-on full policy dump in stable prefix
- Reporting invalid non-injectable env winners (separate metadata path)
- Constructing `CredentialService` for diagnosis

## Risks

| Risk | Mitigation |
| --- | --- |
| Accidental value leak via `ResolvedEnv.value` | Separate inventory DTO; whitelist serializers; recursive redaction tests; ban CredentialService on path |
| Wrong service (`SandboxEnvService` / raw policy row) | Spec names resolvers; plan tech table; code review gate |
| Extra keys on persisted rule JSON | Whitelist only `action`/`target` (etc.); plant extras in fixtures |
| Member sees org env names via agent | Document intentional diagnosis visibility; no value disclosure |
| Tool unused by model | Tool description + **v1 policy_deny hints**; optional later opaque enrichment — **not** system-prompt blurb |
| Long-lived DB session on middleware | Per-call session factory |
| Huge rule lists | Cap + truncated flag |
| Stale sandbox vs new admin policy | `sandbox_note` + policy_source; never claim live re-apply |
| Invalid-status confusion | v1 = inject set only; no invalid-winner promise |
