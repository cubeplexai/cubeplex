# Sandbox network policy: configurable default action

Date: 2026-05-30
Status: Draft (brainstorming output)
Worktree: `feat/sandbox-network-default-action` (slot 4)

## Problem

The admin sandbox policy editor lets an org restrict which hosts a sandbox can
reach via a list of network rules. Today the effective egress policy is
**hard-wired to `defaultAction: "deny"`**: anything not explicitly allowed is
blocked. There is no way to express "allow everything except a blacklist", and
there is no way to express the common exception pattern "block a whole domain
but punch one host through".

Two deeper design flaws surfaced while scoping this:

1. **vault ↔ network policy conflation.** `SandboxEnvInjector.build` currently
   pushes every credential-vault host into the egress allow-list
   (`targets.update(_allowlist_targets(r.hosts))`). That couples two
   independent concerns. The vault decides **whether to substitute** a
   credential when the sandbox talks to a host; the network policy decides
   **whether the sandbox can reach the host at all**. They must not cross: a
   host stored in the vault does not get network access for free, and if the
   network policy denies it, the credential is simply unusable.

2. **action-based ordering is wrong.** `merge_network_rules` sorts all `deny`
   rules ahead of all `allow` rules to make "deny always win". With a
   default-deny policy a bare `deny` rule is redundant (the host is already
   denied), and the action-based sort breaks the symmetric allow-mode case.
   The correct ordering principle is **specificity**, not action.

## Background: how the sidecar evaluates rules

The OpenSandbox egress sidecar (`components/egress/pkg/policy/policy.go`)
evaluates `egress` as an **ordered, first-match-wins** list, then falls back to
`defaultAction`:

```go
for _, r := range p.Egress {
    if r.matchesDomain(domain) { return r.Action }  // first match wins
}
return p.DefaultAction
```

`matchesDomain` supports exact FQDN (`api.github.com`) and wildcard
(`*.github.com`, which matches subdomains but not the apex). `defaultAction`
accepts `"allow" | "deny"` (Python SDK `NetworkPolicy.default_action`, alias
`defaultAction`). cubeplex controls evaluation order purely by the order it
emits rules in the array.

## Design

### 1. Decouple the vault from the network policy

`SandboxEnvInjector.build` returns **only** `env` + `bindings`. It no longer
builds a `NetworkPolicy` and no longer adds vault hosts to any allow-list.
`InjectionResult.network_policy` is removed.

The credential-exchange proxy host (`exchange_host`) is **infrastructure**, not
a vault entry: the placeholder-substitution proxy cannot work unless the
sandbox can reach it. The manager force-allows it when egress injection is
enabled, independent of vault contents and independent of the chosen default
action (harmless in allow mode, required in deny mode).

### 2. Configurable default action

Add `network_default_action: "allow" | "deny"` to `SandboxPolicy`, default
**`"deny"`** — this preserves today's hard-wired deny-by-default egress, so a
brand-new org with no policy row, and any pre-existing row created before this
column, both resolve to a strict whitelist. The migration's `server_default`
is `'deny'`. An org opts into "open by default, blacklist via `deny` rules" by
setting `network_default_action: "allow"`. Surfaced through `SandboxPolicyOut`
/ `UpdateSandboxPolicyIn` and the admin editor.

Both rule actions are valid in both modes — they express **exceptions** to the
default:

| Mode (default) | Meaningful rule | Example |
|---|---|---|
| `deny` | `allow` = whitelist entry; `deny` = carve a hole in a broader `allow` wildcard | `allow *.github.com` + `deny secret.github.com` |
| `allow` | `deny` = blacklist entry; `allow` = carve a hole in a broader `deny` wildcard | `deny *.github.com` + `allow api.github.com` |

### 3. Specificity-based ordering (most-specific-match wins)

Replace `merge_network_rules` with `build_network_policy(*, admin_rules,
default_action, force_allow_hosts)`:

1. Start with `force_allow_hosts` (exchange host) as `allow` rules.
2. Append admin-authored rules.
3. Sort **descending by specificity**, then emit in that order so the sidecar's
   first-match resolves to the most specific rule.
4. Set `defaultAction = default_action`.

**Specificity key** (higher = more specific, evaluated first):
- Exact FQDN beats any wildcard.
- Among wildcards, more labels in the suffix beats fewer
  (`*.api.github.com` > `*.github.com`).

This makes the exception pattern work in **both** modes without the admin
manually ordering rules — the UI stays a flat list:

- allow mode: `allow api.github.com` (exact) sorts before `deny *.github.com`
  (wildcard) → api allowed, rest of github blocked.
- deny mode: `deny secret.github.com` (exact) sorts before `allow *.github.com`
  (wildcard) → secret blocked, rest of github allowed.

Same-specificity entries that could both match the same host are either
disjoint (different domains) or a direct contradiction; contradictions are
rejected at write time (next section). So order within a specificity tier is
irrelevant.

### 4. Validation

In `SandboxPolicyService._validate`:
- `network_default_action` must be `"allow"` or `"deny"`.
- Reject **contradictory pairs**: the same target with both `allow` and `deny`.
  Compare **canonicalized** targets, not raw strings — the sidecar matches
  domains case-insensitively and strips a trailing dot
  (`strings.ToLower(strings.TrimSuffix(domain, "."))`), so `API.GITHUB.COM` /
  `api.github.com` / `api.github.com.` are one host at runtime. Lowercase and
  strip the trailing dot before the equality check; otherwise a contradiction
  slips through and the policy becomes order-dependent instead of rejected.
- Network targets remain **FQDN + `*.domain.tld` wildcard only**. Regex (the
  `/^...$/` form the vault accepts) stays **rejected** for network rules — the
  sidecar's `matchesDomain` only honors exact FQDN and a leading-label `*.`
  wildcard, so a regex target would silently never match (false security).
  Mid-pattern globs (`api-*.github.com`) are likewise not supported. Bare `*`
  stays rejected — "allow all" is expressed by setting
  `network_default_action: "allow"` (with no rules), never by a wildcard target.
- Redundant rules (a bare `deny` in deny mode / bare `allow` in allow mode that
  matches nothing broader) are **not** errors — harmless, left as-is.

### 5. Keep the credential-conflict warning

The OQ-6 advisory (`CredentialConflictBanner`: "you denied a host you have a
credential for; the credential will be unusable") is **kept**. It is consistent
with the decoupling — precisely *because* the two layers are independent, a
deny that strands a credential is worth flagging. It is purely advisory and
changes no behavior. In allow mode the relevant check is "an explicit `deny`
covers a credential host" (a bare allow-mode default cannot strand a
credential).

### 6. Cleanup: confirm command-rule is now real HITL

PR #169 + the `feat(sandbox-ui)` series shipped real human-in-the-loop for
`confirm` command rules (pause `execute`, `SandboxConfirmCard` approve/deny).
Two stale notes claim `confirm` still degrades to `deny`; both are corrected
here:
- `frontend/.../CommandRulesTable.tsx` help text → describe the real
  pause-for-approval behavior.
- `backend/.../sandbox_policy.py` model comment → drop the "degrades to deny"
  line.

## Out of scope

- Per-workspace policy overrides (`scope_workspace_id` stays NULL — v2).
- IP / CIDR network targets (sidecar supports them; cubeplex surface is
  FQDN/wildcard only, unchanged).
- Drag-to-reorder rule UI — specificity sort removes the need.

## Affected components

| Component | Change |
|---|---|
| `backend/cubeplex/models/sandbox_policy.py` | new `network_default_action` column; fix stale comment |
| `backend/alembic/versions/` | autogenerated migration (varchar, `server_default='deny'`) |
| `backend/cubeplex/api/schemas/sandbox_policy.py` | add field to `SandboxPolicyOut` / `UpdateSandboxPolicyIn` |
| `backend/cubeplex/services/sandbox_policy.py` | validate default action + contradictory pairs; resolver carries the field |
| `backend/cubeplex/sandbox_policy/rules.py` | replace `merge_network_rules` → `build_network_policy` (specificity sort, default action, force-allow) |
| `backend/cubeplex/sandbox_env/injector.py` | drop network-policy/vault-host coupling; return env + bindings only |
| `backend/cubeplex/sandbox/manager.py` | assemble policy from admin rules + default action + forced exchange host |
| `backend/cubeplex/api/routes/v1/admin_sandbox_policy.py` | pass the new field through |
| `frontend/packages/core/src/api/sandboxPolicy.ts` | types: `network_default_action` |
| `frontend/.../PolicyEditor.tsx` + `NetworkRulesTable.tsx` | default-action toggle; mode-aware copy |
| `frontend/.../CommandRulesTable.tsx` | fix stale confirm help text |

## Testing

- **Unit** (`rules.py`): specificity ordering in both modes; the two exception
  scenarios resolve correctly; force-allow exchange host present; contradictory
  pair rejected.
- **Unit** (`injector.py`): vault hosts no longer leak into any network policy;
  bindings/env unaffected.
- **Unit** (`sandbox_policy.py` service): default-action + contradiction
  validation.
- **E2E** (`test_sandbox_policy_routes.py`): round-trip a policy with
  `network_default_action: "allow"` + a deny rule; GET returns it; warnings
  behavior intact.
- Existing `merge_network_rules` tests rewritten against `build_network_policy`.
