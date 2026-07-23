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
- **Effective read path today:** `SandboxPolicyResolver.resolve()` →
  `EffectivePolicy` (org row or built-in defaults). `SandboxPolicyService` is
  CRUD/validation only — do **not** use it as the agent read path.

**Env** (`SandboxEnvVar`):

- `env_name`, `is_secret`, `scope` (`org` | `workspace` | `user`)
- Secrets: `hosts`, `header_names`, `status`, `credential_id`
- **Effective read path today:** `SandboxEnvResolver.resolve(workspace_id,
  user_id)` over `SandboxEnvRepository.list_for_resolution`, which filters
  `status == "valid"` before precedence (user > workspace > org).
- `SandboxEnvService` is **CRUD only** and holds `CredentialService` — **never**
  construct it on the agent diagnosis path.
- Plain values injected for real; secrets become host-scoped placeholders
- UI/API already list metadata without re-displaying secret values after save

**Agent surface today**:

- `SANDBOX_PROMPT_TEMPLATE` is workdir / file-tools centric only
- Command deny messages at runtime for some cases (HITL / policy_deny on
  `execute`)
- No `sandbox_config` / policy tool
- Agent *can* `printenv` — **discouraged** as primary design (leaks plain
  secrets into transcript or shows useless placeholders)
- `SandboxMiddleware` today is constructed with
  `sandbox`, `conversation_id`, `workspace_id`, `command_rules`, `channel` —
  **no** `org_id`, `user_id`, or session factory. Wiring for the new tool must
  pass those explicitly (see Delivery → DI).

## Goals

1. **Effective network policy** visible in compact non-secret form.
2. **Env inventory**: name + kind + scope + status + secret host/header
   constraints for the **injectable** set — **never** plaintext secret values.
3. Teach diagnosis via **tool description + result guidance** and **v1
   deterministic hints on reliable policy-deny paths** — **not** by growing
   the stable system prompt.
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
- Guaranteeing the model always calls `sandbox_config` on every opaque
  timeout/401 (model recall is soft; v1 only hardens **detectable** policy_deny
  paths).

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

Source: `SandboxPolicyResolver.resolve()` → serialize `EffectivePolicy`
fields only (whitelist). Never `SandboxPolicyService.get()` raw row alone
without applying the same defaulting as the resolver.

#### B. Env inventory (names + types only)

Per **effective injectable** entry after scope merge for this run’s
org/workspace/user — **same set as injection**.

**v1 status semantics (closed decision):**

- Today `list_for_resolution` already filters `status == "valid"`. An invalid
  row **cannot** be a winning injectable row.
- v1 inventory therefore matches the **inject set only**: omit non-winning
  and omit invalid / non-resolvable entries.
- Do **not** promise “configured but not injectable” for invalid winners in
  v1 — that requires a separate metadata-resolution path with its own
  precedence rules (out of scope).
- `status` on inventory items may still be included for forward compatibility
  but is expected to be `"valid"` for every returned row under the current
  resolver.
- Distinguishing “not configured” vs “configured as secret for hosts […]”
  is by **presence in the inventory** (and `kind` / `hosts`), not by invalid
  status.

**DTO requirement:** `ResolvedEnv` today has `env_name`, `is_secret`, `hosts`,
`header_names`, `credential_id`, optional `value` — **not** `scope`.
Implementation must introduce an agent-facing metadata DTO (`EnvInventoryItem`)
built during or parallel to resolve from the **winning valid row**: scope +
hosts/headers + kind, with **no** decrypted value field and **no**
`credential_id` on the object serializers see.

| Field | Include? |
| --- | --- |
| `env_name` | Yes |
| `kind` / `is_secret` | Yes (`plain` / `secret`) |
| `scope` | Yes (from winning valid row) |
| `status` | Optional; if present, expect `"valid"` under current resolve |
| `hosts` | Yes for secrets |
| `header_names` | Yes if set |
| **value / secret** | **Never** |
| placeholder string | Prefer **no** |
| `credential_id` | **Never** |

Plain entries: inventory says the name is configured; agent may read the value
inside the sandbox only when needed for execution — **not** dump into the
system prompt. Prefer reading via task-specific commands over blanket
`printenv`.

#### C. Command policy (v1 optional, recommended if cheap)

Short list of deny/confirm patterns so the agent avoids known-denied commands
and understands HITL confirms are policy, not random errors.

### Delivery: tool-only + reliable failure hints (chosen)

| Layer | Content |
| --- | --- |
| **Eager tool `sandbox_config`** | Registered with other sandbox tools (`execute`, `write_file`, …) when the sandbox is available. JSON: network summary + env inventory (+ optional command rules). Read DB at **tool-call** time. |
| **Tool description + result guidance** | When to call (network/auth/env failures); never invent credentials; never print secrets; prefer `sandbox_config` over `printenv`; point user to settings for missing allow rules / env entries. Optional short static `guidance` field in the tool result. |
| **v1 deterministic hints (reliable paths only)** | When `execute` (or egress mapping) already knows the failure is **policy_deny** / command deny, append a short line that points at `sandbox_config` and, when known, the deny reason. **Not** a system-prompt blurb; not required for opaque timeouts/401s. |
| **System prompt** | **No change** for this feature: keep existing workdir / file-tools sandbox section. Do **not** append a diagnosis blurb; do **not** interpolate live rules or env names. |

#### DI / session lifecycle (required)

`SandboxMiddleware` today has no org/user/session. Implementation must:

1. Pass run-scoped ids into tool construction: at least `org_id`, `workspace_id`,
   `user_id` (and whatever is needed for policy repo scoping).
2. Provide a **session factory** (or equivalent manager) that opens a **fresh
   `AsyncSession` per tool call**, uses
   `SandboxPolicyResolver` + `SandboxEnvResolver` (or a thin facade over them),
   builds `EnvInventoryItem` list without decrypting secrets, serializes, and
   closes the session. Do **not** hold a long-lived session on the middleware.
3. **Prohibit** constructing `SandboxEnvService` / `CredentialService` on this
   path. Never call credential decrypt for diagnosis.
4. Prefer a single facade e.g. `sandbox_runtime_config.load_agent_view(...)`
   so middleware/tool code does not reassemble resolvers ad hoc.

#### Why not hybrid prompt blurb

- Matches other on-demand capabilities (`conversation_history`, `artifacts`):
  discovery via tool surface, data only after the agent calls.
- Avoids growing the always-on system prompt (#391 / #412 direction).
- Cache-safe by construction: no new stable-prefix fragment at all.
- Soft recall gaps are addressed with **deterministic error-path hints** on
  known policy_deny, not with always-on prose.

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

### Agent behavior (encoded in tool description / result / deny hints)

When network or auth fails:

1. Call `sandbox_config` (or use prior result if still relevant). Prefer this
   over `printenv` / inventing credentials.
2. Host denied / default deny → tell user which allow rule is missing; point to
   Admin → Sandbox network policy (conceptual path).
3. Env name missing from inventory → ask user to add plain/secret env at
   correct scope.
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

- Tool path: read **current** org policy + effective env from DB at call time
  via resolvers (fresh session).
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
| **1** | Safe serializers + `EnvInventoryItem` + redaction tests |
| **2** | Eager `sandbox_config` tool with correct DI (session factory, resolvers); tool description + optional result guidance; wiring |
| **3** | **v1 required for reliable paths:** append short hints on known policy_deny / command-deny execute results pointing at `sandbox_config` |
| **4** | Optional: richer egress-timeout mapping when detection is reliable; deferred multi-op group **only if** more diagnosis tools appear |

## Acceptance criteria

1. Agent can obtain network default action + rules without user screenshots.
2. Agent can obtain env inventory for the **injectable** set (name, plain vs
   secret, scope; secrets include hosts and header_names if any); **no secret
   values** in tool results or system prompt.
3. Blocked-host diagnosis cites policy (eval or e2e with mocked policy) when
   the tool is called.
4. Missing vs present env name is distinguishable by inventory presence
   (“not configured” vs “configured as secret for hosts […]”).
5. Unit tests ensure secret values never appear in serialization helpers;
   no `CredentialService` / decrypt on the agent path.
6. Docs note: agent can see policy/env **metadata** for troubleshooting.
7. `SANDBOX_PROMPT_TEMPLATE` does **not** gain a diagnosis blurb or live
   policy/env dump for this feature.
8. On a **known** command/policy deny from `execute`, the tool result or
   error text includes a deterministic nudge toward `sandbox_config` (or
   already-clear deny reason + inventory pointer).
9. Tool description (or tests around registration) discourages `printenv` as
   the first diagnosis step for network/auth failures.

## Open questions (v1 decisions)

| Question | Decision |
| --- | --- |
| Prompt vs tool vs hybrid | **Tool-only** (eager `sandbox_config`; no system-prompt blurb) |
| DeferredToolGroup | **No** for v1 single tool |
| Service boundary | **`SandboxPolicyResolver` + `SandboxEnvResolver`** via per-call session factory; never `SandboxEnvService` on this path |
| Invalid env rows | **Omit** (match inject set); no “invalid winner” reporting in v1 |
| Command rules in v1 | **Include if present on policy row** (cheap, high value) |
| Org env visibility | **Effective merge** as injected into the sandbox |
| Auto error enrichment | **v1 for reliable policy_deny paths**; optional later for opaque network failures |
| Egress proxy | Presence only, never credentials |

## Related code

- `backend/cubeplex/models/sandbox_policy.py`, `sandbox_env.py`
- `backend/cubeplex/sandbox_policy/rules.py`
- `backend/cubeplex/services/sandbox_env.py` (`ResolvedEnv`, `SandboxEnvResolver`)
- `backend/cubeplex/services/sandbox_policy.py` (`EffectivePolicy`, `SandboxPolicyResolver`)
- `backend/cubeplex/repositories/sandbox_env.py` (`list_for_resolution`, valid-only filter)
- `backend/cubeplex/sandbox_env/injector.py`
- `backend/cubeplex/prompts/sandbox.py`, `middleware/sandbox.py`
- `backend/cubeplex/streams/run_manager.py` (SandboxMiddleware construction)
- `backend/cubeplex/agents/actions/registry.py` (deferred capability pattern — **not** used for v1)
- `docs/site/docs/admin/sandbox.md`
- `backend/docs/prompt-cache-discipline.md`
