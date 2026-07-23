# Sandbox config visibility for agent — implementation plan

Related: #398 · Spec: `docs/dev/specs/2026-07-22-sandbox-config-to-agent-design.md`

**Goal**: Hybrid delivery of network policy + env inventory (no secrets) to the
agent via `sandbox_config` tool + short stable prompt guidance.

**Architecture**: Pure serialization helpers over existing policy/env services;
one DI-backed tool registered when sandbox tools are attached; append a fixed
paragraph to `SANDBOX_PROMPT_TEMPLATE` (or adjacent fragment).

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
    # default_action, rules[{action, target}], egress_proxy: "set"|"unset"
    # cap rules; truncated flag

def serialize_env_inventory(resolved: list[ResolvedEnv], *, include_scope: ...) -> list[dict]:
    # env_name, kind, scope if available, status if available, hosts, header_names
    # NEVER value, credential_id, secret material

def serialize_command_rules(policy) -> list[dict]:
    # action + pattern only
```

**Important**: `ResolvedEnv` currently has `value` for injection — serializers
must not include it. Prefer building from repo rows with scope/status if
`ResolvedEnv` lacks scope; extend a lightweight DTO if needed **without**
passing decrypted values into the agent path.

**Tests** (`backend/tests/unit/test_sandbox_runtime_config_serialize.py`):
- Secret row with fake value never appears in JSON output
- Plain row value never appears even if present on DTO
- Caps/truncation marker
- Hosts and header_names preserved for secrets

---

## Unit 2: `sandbox_config` tool

**Files**:
- `backend/cubeplex/tools/builtin/sandbox_config.py` (new) **or** factory inside
  `middleware/sandbox.py` next to execute/write tools
- Register in sandbox tool list construction (only when sandbox is available)

**Behavior**:
1. Load org sandbox policy via existing service.
2. Resolve effective env for `(workspace_id, user_id)` via `SandboxEnvService`.
3. Return JSON text content with sections: `network`, `env`, optional
   `command_rules`, `truncated` flags, short `notes` (e.g. sandbox recreate).

**Args**: none required for v1 (optional `section: network|env|all` later).

**Tests**:
- unit with mocked services
- e2e optional: member agent tool call returns rules from seeded policy

---

## Unit 3: Prompt guidance (stable)

**Files**:
- `backend/cubeplex/prompts/sandbox.py` — append fixed diagnosis blurb to
  `SANDBOX_PROMPT_TEMPLATE` **or** separate constant concatenated in middleware

**Content** (short, static English):
- Egress and env vars are admin/workspace configured.
- On network/auth failure call `sandbox_config` before inventing credentials.
- Never print secret values; secrets are host-scoped placeholders.
- Point user to settings for missing allow rules / env entries.

**Cache**: text is constant → no extra busts. Do not interpolate live rules.

**Tests**: unit snapshot of template contains key phrases; no dynamic fields.

---

## Unit 4: Wiring + docs

**Files**:
- `middleware/sandbox.py` / `run_manager` — ensure tool appears with other
  sandbox tools and has workspace/user/org context
- `docs/site/docs/admin/sandbox.md` — short “agent troubleshooting metadata”
  note (implementation PR)

**Tests**: registry/tool-list unit if pattern exists.

---

## Unit 5 (optional Phase 3): Error enrichment

**Files**: execute path / egress error mapping

When a command or network failure is clearly policy deny, append one line:
`egress_default=deny; host X not in allow rules` (only when detection is
reliable). Skip if ambiguous.

Defer unless Phase 1–2 are solid.

---

## Delivery order

1. Unit 1 serializers + redaction tests (must land first)
2. Unit 2 tool
3. Unit 3 prompt blurb
4. Unit 4 wiring + admin docs
5. Unit 5 optional

## Out of scope

- Agent mutating policy/env
- Putting secret values or placeholders into system prompt
- Always-on full policy dump in stable prefix (Phase 4 later)

## Risks

| Risk | Mitigation |
| --- | --- |
| Accidental value leak via `ResolvedEnv.value` | Explicit serializer tests; never `model_dump()` raw |
| Tool unused by model | Stable blurb + later error hints |
| Huge rule lists | Cap + truncated flag |
| Stale sandbox vs new admin policy | Note in tool result about recreate |
