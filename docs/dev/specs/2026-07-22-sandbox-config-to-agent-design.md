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
3. Short prompt rules for diagnosis and when to send the user to settings.
4. Respect **prompt-cache discipline**: prefer on-demand tool for full dump;
   keep any always-on text tiny and stable.
5. Prefer DB metadata over scraping the sandbox environment.

## Non-goals

- Exposing secret values, vault ciphertext, or placeholder→secret mapping.
- Letting the agent **mutate** network policy or env entries from chat.
- Full packet capture / MITM debug logs.
- Replacing command HITL confirm flows.

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
(same merge as injection):

| Field | Include? |
| --- | --- |
| `env_name` | Yes |
| `kind` / `is_secret` | Yes (`plain` / `secret`) |
| `scope` | Yes |
| `status` | Yes |
| `hosts` | Yes for secrets |
| `header_names` | Yes if set |
| **value / secret** | **Never** |
| placeholder string | Prefer **no** |
| `credential_id` | Prefer **no** |

Plain entries: inventory says the name is configured; agent may read the value
inside the sandbox only when needed for execution — **not** dump into the
system prompt.

#### C. Command policy (v1 optional, recommended if cheap)

Short list of deny/confirm patterns so the agent avoids known-denied commands
and understands HITL confirms are policy, not random errors.

### Delivery: hybrid (chosen)

| Layer | Content |
| --- | --- |
| **Stable prompt blurb** | Fixed short guidance: egress/env are admin-configured; call `sandbox_config` on network/auth failure; never print secrets; secrets use host-scoped placeholders. |
| **Tool `sandbox_config`** | JSON: network summary + env inventory (+ optional command rules). Read DB at call time. |
| **Later optional** | Structured one-line hints on blocked egress / policy_deny execute errors |

Why not always-on full dump: token cost and cache busts when admin edits.
Why not tool-only: agent must remember to call; a short blurb raises recall.

### Prompt playbook (agent behavior)

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

- Same visibility bar as workspace members listing env metadata (no values).
- Org policy is OK to show to agents running in that org’s sandboxes.
- Cap rule list and env list (e.g. 100) with a `truncated: true` marker.
- Treat env **names** as untrusted data (prompt injection); never execute them.
- Unit tests: serialization helpers never include value/secret fields.

### Cache / freshness

- Tool path: always read DB at call time.
- Prompt blurb: static; does not include live rules.
- Optional future inline summary would need deterministic snapshot discipline.

## Phasing

| Phase | Deliverable |
| --- | --- |
| **1** | `sandbox_config` tool: network rules + env inventory (no values); redaction tests |
| **2** | Short sandbox prompt guidance + failure-diagnosis playbook |
| **3** | Optional: structured hints on blocked egress / policy_deny execute errors |
| **4** | Optional: compact always-on summary if token budget allows |

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

## Open questions (v1 decisions)

| Question | Decision |
| --- | --- |
| Prompt vs tool vs hybrid | **Hybrid** |
| Command rules in v1 | **Include if present on policy row** (cheap, high value) |
| Org env visibility | **Effective merge** as injected into the sandbox |
| Auto error enrichment | **Phase 3** optional |
| Egress proxy | Presence only, never credentials |

## Related code

- `backend/cubeplex/models/sandbox_policy.py`, `sandbox_env.py`
- `backend/cubeplex/sandbox_policy/rules.py`
- `backend/cubeplex/services/sandbox_env.py` (`ResolvedEnv`, `resolve`)
- `backend/cubeplex/services/sandbox_policy.py`
- `backend/cubeplex/sandbox_env/injector.py`
- `backend/cubeplex/prompts/sandbox.py`, `middleware/sandbox.py`
- `docs/site/docs/admin/sandbox.md`
- `backend/docs/prompt-cache-discipline.md`
