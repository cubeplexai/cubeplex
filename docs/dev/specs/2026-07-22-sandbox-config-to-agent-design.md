# Expose sandbox network policy and env metadata to the agent

Related: #398

## Goal

When network or auth fails in the sandbox, the agent should diagnose from
**policy and env inventory metadata** instead of trial-and-error or asking the
user for vague screenshots — **never** exposing secret values.

## Context

### Failure modes today

| What the user sees | What the agent knows | What it should know |
| --- | --- | --- |
| Connection refused / timeout | Opaque shell error | Host vs allow/deny + default action |
| 401 / wrong auth | Placeholder may exist; policy opaque | Env name is **secret**, hosts, header names — **not** value |
| Env unset | `printenv` useless or dangerous | Configured names + kind + scope |
| Command blocked | Sometimes a deny pattern | Optional command-policy summary |

### Architecture (existing)

**Network policy** (`SandboxPolicy`):

- `network_default_action` (`allow` | `deny`)
- `network_rules` JSON `[{action, target}, …]`
- `command_rules`, image, resources
- Built via `sandbox_policy/rules.py` → egress sidecar
- Admin API `GET/PUT` sandbox policy

**Env** (`SandboxEnvVar`):

- `env_name`, `is_secret`, `scope` (`org` | `workspace` | `user`)
- Secrets: `hosts`, `header_names`, `status`, `credential_id`
- Resolution: user > workspace > org (`SandboxEnvService.resolve`)
- Plain values injected for real; secrets become host-scoped placeholders
- UI/API already list metadata without re-displaying secret values after save

**Agent surface today**:

- `SANDBOX_PROMPT_TEMPLATE` is workdir / file-tools centric only
- Command deny messages at runtime for some cases
- No `sandbox_config` / policy tool
- Agent *can* `printenv` — **discouraged** as primary design (leaks plain
  secrets into transcript or shows useless placeholders)

## Goals

1. **Effective network policy** visible in compact non-secret form.
2. **Env inventory**: name + kind + scope + status + secret host/header
   constraints — **never** plaintext secret values.
3. Teach diagnosis via **tool description + result guidance** (and optional
   error-path hints later) — **not** by growing the stable system prompt.
4. Respect **prompt-cache discipline**: full dump only on-demand via tool;
   leave `SANDBOX_PROMPT_TEMPLATE` free of live policy/env and free of a
   diagnosis playbook.
5. Prefer DB metadata over scraping the sandbox environment.

## Non-goals

- Exposing secret values, vault ciphertext, or placeholder→secret mapping.
- Letting the agent **mutate** network policy or env entries from chat.
- Full packet capture / MITM debug logs.
- Replacing command HITL confirm flows.
- Injecting live policy/env (or a long diagnosis blurb) into the system prompt.
- Wrapping `sandbox_config` in a `DeferredToolGroup` in v1 (see Delivery).

## Design

### What to expose

#### A. Network policy (safe)

```text
network_default_action: deny
rules:
  - allow  pypi.org, files.pythonhosted.org
  - allow  registry.npmjs.org
  - deny   *.evil.example
egress_proxy: set|unset   # presence only, never credentials
```

Enough for: “Is `api.github.com` allowed under current policy?”

Optional one-liner: policy is applied at sandbox creation; admin changes may
require a new sandbox (align with deploy docs).

#### B. Env inventory (names + types only)

Per **effective** entry after scope merge for this run’s org/workspace/user
(same merge as injection).

**DTO requirement:** `ResolvedEnv` today has `env_name`, `is_secret`, `hosts`,
`header_names`, `credential_id`, optional `value` — **not** `scope` or
`status`. Implementation must introduce an agent-facing metadata DTO (or extend
resolution with a parallel non-secret view) that carries the **winning row’s**
`scope` and `status` without ever attaching decrypted values.

| Field | Include? |
| --- | --- |
| `env_name` | Yes |
| `kind` / `is_secret` | Yes (`plain` / `secret`) |
| `scope` | Yes (from winning row) |
| `status` | Yes (from winning row; invalid winners reported if present) |
| `hosts` | Yes for secrets |
| `header_names` | Yes if set |
| **value / secret** | **Never** |
| placeholder string | Prefer **no** |
| `credential_id` | **Never** |

Invalid / suppressed rows: document v1 choice — **omit non-winning and
non-resolvable entries** (match inject set) unless a row wins but is invalid
(then show `status` so the agent can say “configured but not injectable”).

Plain entries: inventory says the name is configured; agent may read the value
inside the sandbox only when needed for execution — **not** dump into the
system prompt.

#### C. Command policy (v1 optional, recommended if cheap)

Short list of deny/confirm patterns so the agent avoids known-denied commands
and understands HITL confirms are policy, not random errors.

### Delivery: tool-only (chosen)

| Layer | Content |
| --- | --- |
| **Eager tool `sandbox_config`** | Registered with other sandbox tools (`execute`, `write_file`, …) when the sandbox is available. JSON: network summary + env inventory (+ optional command rules). Read DB at call time. |
| **Tool description + result guidance** | When to call (network/auth/env failures); never print secrets; point user to settings for missing allow rules / env entries. Optional short `guidance` field in the tool result so the playbook is not system-prompt-resident. |
| **System prompt** | **No change** for this feature: keep existing workdir / file-tools sandbox section. Do **not** append a diagnosis blurb; do **not** interpolate live rules or env names. |
| **Later optional** | Structured one-line hints on blocked egress / policy_deny execute errors (error path, not stable prefix). |

#### Why not hybrid prompt blurb

- Matches other on-demand capabilities (`conversation_history`, `artifacts`):
  discovery via tool surface, data only after the agent calls.
- Avoids growing the always-on system prompt (#391 / #412 direction).
- Cache-safe by construction: no new stable-prefix fragment at all.

#### Why not `DeferredToolGroup` in v1

Deferred groups (`cubeplex:conversation_history`, MCP servers, …) hide **many
tool schemas** behind a catalog line + `load_tools`. `sandbox_config` is a
**single small diagnostic tool** used on the failure path — a deferred group
still costs catalog text in the system prompt and an extra `load_tools`
round-trip with almost no schema savings (same reason `generate_image` stays
eager). Register it eagerly next to other sandbox tools.

If diagnosis later grows into multiple ops (e.g. network check, env lookup),
revisit a `cubeplex:sandbox_runtime` deferred group then.

Why not always-on full dump: token cost and cache busts when admin edits.

### Agent behavior (encoded in tool description / result, not system prompt)

When network or auth fails:

1. Call `sandbox_config` (or use prior result if still relevant).
2. Host denied / default deny → tell user which allow rule is missing; point to
   Admin → Sandbox network policy (conceptual path).
3. Env name missing → ask user to add plain/secret env at correct scope.
4. Secret exists but host not allowed → ask to add host to that secret’s
   allowlist (or use an allowed host).
5. Do not invent API keys; do not ask user to paste secrets into chat if the
   settings UI exists.
6. Never echo secret values or vault material.

### Security

- **Authz (explicit, not “same as member list”)**: today members may list only
  **user-scoped** env rows; workspace- and org-scoped env metadata and org
  network policy require **admin** APIs. The agent tool intentionally exposes
  the **effective inject set** for this run (names + kinds + secret host/header
  constraints — never values) so diagnosis matches runtime. That is a deliberate
  expansion of what a member can see via HTTP for org/workspace env **names**.
  Document this product decision; do **not** claim parity with list APIs.
- Cap rule list and env list (e.g. 100) with a `truncated: true` marker.
- Treat env **names** and rule **patterns** as untrusted data (prompt injection);
  never execute them.
- Serializers must **whitelist fields only** (no `model_dump()` / raw JSON
  passthrough of rule dicts). Tests must plant forbidden extra keys and assert
  recursive absence of values, `credential_id`, proxy credentials, and unknown keys.

### Cache / freshness

- Tool path: read **current** org policy + effective env from DB at call time.
- **Sandbox policy may be stale**: network policy is structural and applied at
  sandbox **create** (manager does not re-apply on reuse). Tool output must
  include a clear note that DB policy is “desired / next create”, and when
  possible flag drift if the active sandbox record can expose which policy was
  applied (image drift already exists as precedent). Prefer wording:
  `policy_source: "org_db_current"` + `sandbox_note: "network rules apply at
  create; recreate sandbox after admin changes"`. Do not claim live enforcement
  of newly edited rules on an old sandbox.
- No new system-prompt fragment → no extra cache surface for this feature.
- Optional future inline summary would need deterministic snapshot discipline.

## Phasing

| Phase | Deliverable |
| --- | --- |
| **1** | Safe serializers + redaction tests |
| **2** | Eager `sandbox_config` tool (description + optional result guidance); wiring with other sandbox tools |
| **3** | Optional: structured hints on blocked egress / policy_deny execute errors |
| **4** | Optional: deferred multi-op group **only if** more sandbox-diagnosis tools appear; not a v1 goal |

## Acceptance criteria

1. Agent can obtain network default action + rules without user screenshots.
2. Agent can obtain env inventory (name, plain vs secret, scope, status; secrets
   include hosts and header_names if any); **no secret values** in tool results
   or system prompt.
3. Blocked-host diagnosis cites policy (eval or e2e with mocked policy).
4. Missing vs present env name is distinguishable (“not configured” vs
   “configured as secret for hosts […]”).
5. Unit tests ensure secret values never appear in serialization helpers.
6. Docs note: agent can see policy/env **metadata** for troubleshooting.
7. `SANDBOX_PROMPT_TEMPLATE` (or equivalent always-on sandbox section) does
   **not** gain a diagnosis blurb or live policy/env dump for this feature.

## Open questions (v1 decisions)

| Question | Decision |
| --- | --- |
| Prompt vs tool vs hybrid | **Tool-only** (eager `sandbox_config`; no system-prompt blurb) |
| DeferredToolGroup | **No** for v1 single tool |
| Command rules in v1 | **Include if present on policy row** (cheap, high value) |
| Org env visibility | **Effective merge** as injected into the sandbox |
| Auto error enrichment | **Phase 3** optional (preferred over prompt blurb if recall is weak) |
| Egress proxy | Presence only, never credentials |

## Related code

- `backend/cubeplex/models/sandbox_policy.py`, `sandbox_env.py`
- `backend/cubeplex/sandbox_policy/rules.py`
- `backend/cubeplex/services/sandbox_env.py` (`ResolvedEnv`, `resolve`)
- `backend/cubeplex/services/sandbox_policy.py`
- `backend/cubeplex/sandbox_env/injector.py`
- `backend/cubeplex/prompts/sandbox.py`, `middleware/sandbox.py`
- `backend/cubeplex/agents/actions/registry.py` (deferred capability pattern — **not** used for v1)
- `docs/site/docs/admin/sandbox.md`
- `backend/docs/prompt-cache-discipline.md`
