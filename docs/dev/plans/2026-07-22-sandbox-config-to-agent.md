# Sandbox config visibility for agent — implementation plan

Related: #398 · Spec: `docs/dev/specs/2026-07-22-sandbox-config-to-agent-design.md`

**Goal**: Tool-only delivery of network policy + env inventory (no secrets) to
the agent via an eager `sandbox_config` tool. No system-prompt diagnosis blurb;
no `DeferredToolGroup` in v1.

**Architecture**: Pure serialization helpers over existing policy/env services;
one DI-backed tool registered when sandbox tools are attached (same place as
`execute` / `write_file`). Playbook lives in the tool description and optional
result `guidance` field.

**Tech stack**: Existing `SandboxPolicyService`, `SandboxEnvService.resolve`,
cubepi `AgentTool`, sandbox middleware / `run_manager` tool wiring.

---

## Unit 1: Safe serializers (no values)

**Files**:
- `backend/cubeplex/sandbox_policy/agent_view.py` (new) **or**
  `backend/cubeplex/services/sandbox_runtime_config.py` (new)

**Functions** (sketch):

```python
def serialize_network_policy(policy) -> dict[str, Any]:
    # WHITELIST only: default_action, rules[{action, target}],
    # egress_proxy: "set"|"unset", truncated, policy_source, sandbox_note
    # Never forward raw rule dicts / model_dump()

def serialize_env_inventory(meta: list[EnvInventoryItem]) -> list[dict]:
    # WHITELIST: env_name, kind, scope, status, hosts, header_names
    # NEVER value, credential_id, secret material, extra keys

def serialize_command_rules(policy) -> list[dict]:
    # WHITELIST: action + pattern only
```

**DTO**: introduce `EnvInventoryItem` (or equivalent) populated during resolve
from the **winning** `SandboxEnvVar` row: scope + status + hosts/headers +
kind, with **no** decrypted value field on the object that serializers see.
Do not re-query unscoped rows in a way that bypasses effective merge.

**Important**: `ResolvedEnv` currently has `value` for injection — agent path
must not receive that object unfiltered.

**Tests** (`backend/tests/unit/test_sandbox_runtime_config_serialize.py`):
- Secret row with fake value never appears in JSON output
- Plain row value never appears even if present on DTO
- Caps/truncation marker
- Hosts and header_names preserved for secrets
- Fixtures with **extra forbidden keys** on rules/env rows never appear
- Recursive scan: no `credential_id`, proxy credentials, or value-like fields
- Scope/status present for winning rows; precedence cases

---

## Unit 2: `sandbox_config` tool (eager)

**Files**:
- `backend/cubeplex/tools/builtin/sandbox_config.py` (new) **or** factory inside
  `middleware/sandbox.py` next to execute/write tools
- Register in sandbox tool list construction (only when sandbox is available)

**Behavior**:
1. Load org sandbox policy via existing service (admin-authored; agent sees
   diagnosis view, not admin UI parity — see spec Security).
2. Resolve effective env inventory DTO for `(workspace_id, user_id)`.
3. Return JSON with sections: `network`, `env`, optional `command_rules`,
   `truncated` flags, `policy_source`, and `sandbox_note` that network rules
   apply at sandbox create / may require recreate after admin edits.
4. Optional short static `guidance` string in the result (when to use, no secret
   echo, point user to settings) — **not** copied into `SANDBOX_PROMPT_TEMPLATE`.

**Tool description** (static English, in the AgentTool schema): call on
network / auth / missing-env failures; do not invent credentials; never print
secret values.

**Args**: none required for v1 (optional `section: network|env|all` later).

**Not in v1**: `DeferredToolGroup` / `load_tools` path; capability registry
entry under `agents/actions/`.

**Tests**:
- unit with mocked services
- e2e optional: member agent tool call returns rules from seeded policy
- assert tool result never contains secret values / credential ids
- assert tool is present when sandbox tools are registered

---

## Unit 3: Wiring + docs

**Files**:
- `middleware/sandbox.py` / `run_manager` — ensure tool appears with other
  sandbox tools and has workspace/user/org context
- `docs/site/docs/admin/sandbox.md` — short “agent troubleshooting metadata”
  note (implementation PR)

**Explicit non-work**: do **not** edit `prompts/sandbox.py` /
`SANDBOX_PROMPT_TEMPLATE` for a diagnosis blurb.

**Tests**: registry/tool-list unit if pattern exists; no new system-prompt
snapshot requirement for this feature.

---

## Unit 4 (optional Phase 3): Error enrichment

**Files**: execute path / egress error mapping

When a command or network failure is clearly policy deny, append one line:
`egress_default=deny; host X not in allow rules` and/or `call sandbox_config`
(only when detection is reliable). Skip if ambiguous.

Prefer this over adding a stable system-prompt blurb if model recall is weak.

Defer unless Phase 1–2 are solid.

---

## Delivery order

1. Unit 1 serializers + redaction tests (must land first)
2. Unit 2 eager tool (+ description / optional result guidance)
3. Unit 3 wiring + admin docs
4. Unit 4 optional error enrichment

## Out of scope

- Agent mutating policy/env
- Putting secret values or placeholders into system prompt
- Diagnosis playbook or live policy dump in the stable system prefix
- `DeferredToolGroup` for a single `sandbox_config` tool
- Always-on full policy dump in stable prefix

## Risks

| Risk | Mitigation |
| --- | --- |
| Accidental value leak via `ResolvedEnv.value` | Separate inventory DTO; whitelist serializers; recursive redaction tests |
| Extra keys on persisted rule JSON | Whitelist only `action`/`target` (etc.); plant extras in fixtures |
| Member sees org env names via agent | Document intentional diagnosis visibility; no value disclosure |
| Tool unused by model | Strong tool description + optional result `guidance`; Phase 3 error hints if needed — **not** system-prompt blurb |
| Huge rule lists | Cap + truncated flag |
| Stale sandbox vs new admin policy | `sandbox_note` + policy_source; never claim live re-apply |
