# Sandbox Env Vault & Egress Secret Injection — Design

- **Status:** Draft (rev 8 — re-scoped to skill-tool env-var secrets after
  brainstorming; supersedes the LLM-provider model of rev 1–6; incorporates
  Codex review of rev 7)
- **Date:** 2026-05-25
- **Branch:** `feat/egress-key-injection`
- **Owner:** xfgong

---

## 1. Problem & scope

Skills run tool software **inside** the sandbox (CLIs, scripts). Those tools
often need a secret, supplied via an **environment variable** (the openclaw
convention: a skill declares `requires.env: [NAME, …]`). The tool reads the env
var and puts the secret into an HTTP header to a fixed API (e.g. `gh` reads
`GITHUB_TOKEN`, sends it to `api.github.com`).

Putting the real secret into the sandbox env is the leak we must avoid: the
sandbox runs untrusted, agent-generated code that can read its own env, files,
and process memory.

**Goal:** the tool gets a *working* env var, but the **real secret never enters
the sandbox**. The env var holds an opaque placeholder; the real secret is
swapped in at the network egress boundary, in a component the sandbox cannot
read.

**In scope (confirmed):** secrets used as **HTTP request headers to a fixed
host**. egress can intercept and substitute these.
**Out of scope:** secrets used for request signing (AWS SigV4-style HMAC),
non-HTTP credentials (DB/SSH), or any use where the tool needs the raw secret
locally — egress cannot keep those out of the sandbox. Skills needing those are
not supported by this feature in v1.

This design does **not** touch the backend LLM call path (provider keys are
decrypted backend-side today and are not injected into sandbox env).

## 2. Goals

- Real secrets never present in the sandbox (env/fs/process) **and not
  obtainable by sandbox code** even though the sandbox can reach the exchange
  endpoint.
- A skill declares only the env var **names** it needs (openclaw
  `requires.env`); it does not declare hosts.
- Secret **values** are managed in a standalone **Sandbox Env Vault** UI (not
  attached to skill pages), scoped per org/workspace/user, mirroring the MCP
  credential model.
- The sandbox can only reach approved hosts; a placeholder can only be swapped
  for its real secret when sent to that secret's declared host.
- Plain (non-secret) config env vars are supported too — injected verbatim, no
  placeholder/egress.
- Developable/testable when the cubeplex backend runs **bare** (`python
  main.py`), not only in Kubernetes.

## 3. Non-goals

- Hiding from the sandbox that a proxy exists (we protect the *secret*, not the
  fact of interception).
- Signing-key / non-HTTP / raw-secret use cases (see scope).
- IPv6 egress; per-request approval/billing; CA rotation (v1 — see §9).

## 4. Trust model

The placeholder lives in the sandbox and must be assumed to leak. So:

- **Placeholder ref `R`** (the injected env value, e.g. `cbxref_<random>`) is a
  **selector only** — holding it grants no authorization.
- **Sidecar identity** (only in the egress sidecar, never in the app
  container) **authorizes** the swap.

The exchange check is: **(1) prove you are *this sandbox's* sidecar → (2) `R`
selects which secret → (3) the request host must be the secret's declared
host.** Sandbox code holds `R` but cannot prove sidecar identity, so even with
exchange-endpoint reachability it cannot obtain the real secret.

### 4.1 Sidecar identity — mTLS (decided), via a pluggable authenticator

Identity verification is a config-selected strategy so the same exchange
endpoint works in production and bare-local runs:

```
SidecarAuthenticator        # verify(request) -> sidecar identity (incl. sandbox_id) | reject
├── MtlsAuthenticator        # PRODUCTION (chosen)
└── DevSharedSecretAuthenticator  # bare-local only
```

- **Production = mTLS.** The webhook mints a short-lived **per-sandbox** client
  cert carrying `sandbox_id`, mounted **only** into the egress container. The
  exchange endpoint verifies the cert chain and reads `sandbox_id` from it.
  Chosen over SA-token because (a) it carries `sandbox_id` directly with no
  K8s-API round-trip, and (b) it avoids the SA-token default-automount footgun
  (a normal SA token mounts into *all* pod containers, including the untrusted
  app container). The exchange service verifies client certs directly
  (uvicorn `ssl_ca_certs` + `ssl_cert_reqs=CERT_REQUIRED`), so no ingress mTLS
  dependency.
- **Bare-local = shared secret.** `EGRESS_EXCHANGE_DEV_TOKEN` from config,
  accepted via header; the local test harness is the trusted "sidecar" and
  supplies `sandbox_id` as an explicit field. Guardrails: **startup fails** if
  the dev authenticator is selected in a production deployment mode; the dev
  token is a sidecar credential and is **never** placed in the sandbox.

## 5. Architecture overview

```
 sandbox app container             egress sidecar (stock image)          cubeplex (control-plane)
 ┌────────────────────┐            ┌──────────────────────────┐        ┌────────────────────────┐
 │ tool reads env      │  HTTPS    │ mitmproxy (transparent)   │ mTLS   │ internal exchange svc   │
 │ GITHUB_TOKEN=cbxref │ ───────▶ │  inject.py:                │ ──────▶│ 1. verify sidecar cert  │
 │ → Authorization:     │  (CA      │   scan headers for cbxref_ │        │    + sandbox_id match   │
 │   token cbxref_…     │  trusted) │   exchange(ref, host)      │        │ 2. R→binding, host ∈    │
 │ (no real secret)     │           │   replace ref→real secret  │ ◀──────│    allowed; decrypt     │
 └────────────────────┘            │   re-encrypt upstream      │        └────────────────────────┘
        ▲                           └────────────┬─────────────┘
        │ CA injected via initContainer          │ verified upstream TLS
        │ (webhook patches app container)        ▼
                                           api.github.com
```

Flow:

1. **At run start**, cubeplex resolves **all** applicable **Env Vault** entries
   for the (user, workspace) scope (vault-driven, *not* filtered by which
   skills are loaded — §6.5). For every **secret** entry it mints a placeholder
   `R`, freezes a ref record (revoking the prior one — schema in §6.5), injects
   env `NAME=R` into the sandbox, and adds the entry's hosts to the egress
   `network_policy`. **Plain** entries are injected as their literal value (no
   ref). The internal exchange host is also allow-listed.
2. The mutating webhook patches the egress sidecar: enable transparent MITM,
   load `inject.py`, mount the fixed CA + the per-sandbox mTLS identity, and
   inject CA trust into the app container (initContainer).
3. The tool reads `NAME` and sends the placeholder in a header to its API.
4. `inject.py` scans outbound header values for `cbxref_` tokens; for each, it
   calls the exchange endpoint over mTLS with `R` + the request host. cubeplex
   verifies sidecar identity + `sandbox_id`, checks the host is in `R`'s allowed
   hosts, decrypts the bound secret, returns it; the addon replaces the `R`
   substring with the real secret and forwards.
5. Response streams back unchanged.

## 6. Components

### 6.1 Mutating admission webhook (new, cubeplex-owned)

- **Trigger:** `CREATE` pods in the **dedicated sandbox namespace** (decided),
  matched on `Sandbox` CRD ownerReference (`sandbox.opensandbox.io/v1alpha1`) +
  presence of the `egress` container with the expected image. Anything that
  fails these invariants is **not** patched (fail closed).
- **Patches the `egress` container:** enable transparent MITM env, point the
  addon script env at the mounted `inject.py`, mount the fixed CA into the
  mitmproxy confdir, and mount the **per-sandbox mTLS identity** (cert carrying
  `sandbox_id`) — only here, never in the app container. The webhook mints that
  cert at admission (it knows the sandbox).
- **Patches the app container/pod:** an **initContainer** that writes the CA
  public cert into the system trust store (decided over env-vars-only, for
  universal runtime coverage), plus the pod-level volumes.
- **failurePolicy:** `Ignore` + alerting (outage → no injection → tool calls
  fail auth; never a key leak or blocked creation).

> Deployment prerequisite (not code): the OpenSandbox server's `egress.image`
> must include mitmproxy transparent support (`docker/egress` ≥ 2026-05 line).

### 6.2 The `inject.py` mitmproxy addon (in a ConfigMap)

Loaded after the stock system addon; uses a `request` hook:

- **Token scan:** scan outbound **header values** for the `cbxref_` placeholder
  pattern (high-entropy, recognizable prefix). Body/query are **not** scanned
  (confirmed scope: header-to-fixed-API). If the entry sets `header_names`, only
  those header(s) are eligible for substitution.
- **Placeholder format:** `cbxref_` + ≥128 bits base32 random, no embedded
  separators that could appear in normal header values; a request carrying more
  than a small cap of distinct refs is rejected (defense against scanning
  abuse). Exact length/alphabet finalized in the plan.
- For each found ref: call the exchange endpoint (mTLS identity + `R` + the
  **canonical upstream host**). The exchange returns the secret **and** the
  binding's `header_names`. **`header_names` enforcement (addon-side):** the
  addon knows which header the ref was found in; if `header_names` is non-empty
  and that header is not in it, the addon **skips substitution** (leaves the
  placeholder). With `header_names` null, any header is eligible. (The exchange
  cannot see headers, so enforcement lives in the addon, which does.) The host MUST be the **TLS-verified upstream
  host** mitmproxy is actually forwarding to (SNI / original-destination
  resolved + certificate-verified), **never** the mutable `Host` / `:authority`
  request header — otherwise the sandbox could claim an allowed host while
  routing elsewhere. Normalize to lowercase, strip default ports, before
  matching. On success, replace the `R` substring with the returned secret. If the exchange rejects (host not allowed for this ref,
  identity/`sandbox_id` mismatch, revoked/expired), **leave the header unchanged
  and do not forward a guessed secret** (fail closed for that secret).
- Cache by `(R, host)` with a short absolute TTL (a few minutes), capped by ref
  expiry and the exchange-returned max-age. Replay note: `R` rotates per run and
  the prior `R` is revoked, so stale entries become unreachable within a run;
  the short TTL bounds the residual window. (Tighter revocation = explicit
  purge signal; not in v1.)
- **Secret hygiene:** disable mitmproxy flow persistence; redact the
  placeholder and any substituted header in addon/sidecar logs.

Lives in a ConfigMap → editable without rebuilding any image.

### 6.3 Skill env declaration (openclaw format)

A skill's `SKILL.md` frontmatter declares the env var names it needs, parsed
into `SkillVersion.raw_metadata` by the existing `cubeplex/skills/frontmatter.py`:

```json
"requires": { "bins": ["uv"], "env": ["GITHUB_TOKEN"] },
"primaryEnv": "GITHUB_TOKEN"
```

Names only — no host, no value. **This is advisory/UX only, not a runtime
gate.** Its sole purpose is to let the Env Vault UI show which declared names
are still unset for a workspace/user (e.g. "skill `github` needs `GITHUB_TOKEN`
— not set"). Runtime injection is driven entirely by the vault (§6.5), not by
which skills are loaded.

### 6.4 Sandbox Env Vault (new — standalone management surface)

A first-class "env vault" for sandboxes, **not** attached to skill pages. Each
entry:

```
SandboxEnvVar (CubeplexBase, _PREFIX = "senv")
  org_id        FK organizations
  env_name      str                     (e.g. GITHUB_TOKEN)
  is_secret     bool                     (true → ref + egress; false → plain literal)
  hosts         list[str] | NULL          (required when is_secret; match patterns —
                                          exact FQDN, wildcard *.x.com, or /regex/. The
                                          set of hosts the secret may be sent to.)
  header_names  list[str] | NULL          (optional; if set, the placeholder is only
                                          swapped when it appears in one of these headers)
  scope         'org' | 'workspace' | 'user'
  workspace_id  FK workspaces  NULL       ┐ aligned with scope via CHECK + partial unique
  user_id       FK users       NULL       ┘ indexes (copied from MCPCredentialGrant)
  credential_id FK credentials  NULL       (secret value; kind = sandbox_env; NULL for plain)
  plain_value   str | NULL                 (literal; non-NULL only when is_secret = false)
  status        'valid' | 'revoked'
```

- **Value-shape invariants (DB CHECK + service):** a **secret** row requires
  non-empty `hosts` and non-null `credential_id`, and forbids `plain_value`; a
  **plain** row requires `plain_value` and forbids `credential_id`/`hosts`.
- **Uniqueness:** partial unique indexes over `(env_name, <scope columns>)` per
  scope (mirroring `MCPCredentialGrant`), so there is at most one entry per env
  name per (org) / (workspace) / (workspace,user) — guaranteeing the "one
  effective value per `env_name` after precedence" rule in §6.5 is
  well-defined.
- **Scopes & precedence mirror MCP** (`MCPCredentialGrant`): user > workspace >
  org. Public-id prefix `senv` registered in `public_id.py`.
- **Secret values** go through the existing `CredentialService` (new kind
  `sandbox_env`), encrypted at rest; the vault is the management layer over it.
- **Management routes are scope-isolated** (per the project rule): org-scope
  entries via org-admin routes (`/api/v1/admin/...`); workspace- and user-scope
  entries via workspace routes (`/api/v1/ws/{workspace_id}/...`), with a user
  managing only their own user-scope entries unless delegated. A grant may only
  bind a credential the actor can see (same-org/visible), validated at
  create/update.
- **Host patterns (list + regex).** `hosts` is a list; each item is an exact
  FQDN, a wildcard (`*.example.com`), or a regex (`/…/`). It feeds **two
  consumers with different matching power**:
  - **Substitution boundary** (exchange + addon): full pattern matching incl.
    regex — this is where a placeholder may be swapped.
  - **egress allow-list** (`network_policy`): OpenSandbox NetworkRule supports
    only FQDN + wildcard, **not** arbitrary regex. So exact/wildcard items map
    straight through; a **regex-only** item cannot be expressed as an allow-list
    rule and must be paired with an allow-list-expressible host (wildcard/FQDN)
    on the same entry, or the operator widens the sandbox allow-list separately.
    Validation rejects a secret entry whose hosts can't produce a valid
    allow-list. (Regex must be anchored — see §7.)

### 6.5 Run-start resolution (in `cubeplex/sandbox/manager.py`)

Injection is **vault-driven, not skill-driven**: resolve **all** Env Vault
entries that apply to the run's (user, workspace) by scope precedence
(user > workspace > org) and inject every one. Skills' `requires.env` does not
filter this (§6.3). Per run (per-run ref lifecycle, decided):

1. Resolve the applicable vault entries for (user, workspace) — one effective
   value per `env_name` after scope precedence.
2. For each **secret** entry: mint `R = cbxref_<random>`, freeze
   `{R_hash, env_name, hosts, credential_id}` into the ref record (Postgres,
   `_PREFIX = "eref"`, store only the hash), inject env `NAME=R`, and add the
   entry's `hosts` to the `network_policy` allow-list.
3. For each **plain** entry: inject `NAME=plain_value` directly (no ref, no
   egress involvement).
4. Always allow-list the **distinct internal exchange host/port** (see below).
5. Revoke the previous run's ref record for this sandbox. On sandbox reuse
   (`get_or_create`), the ref is still refreshed at run start.

Ref record schema (Postgres table; the exchange depends on these fields):

```
EgressRef (CubeplexBase, _PREFIX = "eref")
  ref_hash      str   UNIQUE        (hash of R; R itself only lives in the sandbox)
  sandbox_id    str   index         (enforced == cert.sandbox_id at exchange)
  org_id / workspace_id / user_id   (issuing scope)
  run_id        str | NULL          (the run this ref was minted for)
  bindings      JSON: [ { env_name, hosts, header_names|NULL, credential_id }, … ]
  status        'valid' | 'revoked'
  expires_at    datetime            (per-run lifetime)
  created_at
```

The egress allow-list is therefore the union of all injected secrets' `hosts`
plus the exchange endpoint — i.e. everything the vault is configured to reach,
not a per-run-narrowed subset (accepted trade-off; tighter per-run scoping is a
possible later refinement).

Today no `network_policy` is passed, so **no egress sidecar exists at all** —
setting it is what brings the sidecar (and the webhook patch) into being.

> **Exchange endpoint exposure.** Only a **distinct internal exchange
> host/port** (separate from the user-facing cubeplex API) is on the egress
> allow-list — never the full cubeplex API. The sandbox can reach the exchange
> service and its declared upstream hosts, nothing else.

### 6.6 Exchange endpoint (internal control-plane)

A **separate internal endpoint**, not a workspace/admin route (it returns
plaintext secrets to a machine identity). Steps: (1) verify sidecar mTLS
identity and enforce `cert.sandbox_id == ref.sandbox_id`; (2) load the ref
record by `hash(R)`, check `status=valid` and not expired; (3) **require the
**canonical TLS-verified upstream host** (passed by the addon, §6.2 — not a
client-supplied header) to match one of the ref binding's host patterns
(exact / wildcard / anchored regex; reject otherwise — this is the substitution
boundary that replaces per-skill host declarations);
(4) decrypt the bound credential via `CredentialService`; (5) return the raw
secret value. Logs redact secret material.

(The addon does the header substitution itself, so the exchange just returns
the value for the matched ref — no provider/auth-template logic is needed here,
unlike the obsolete LLM-provider model.)

### 6.7 CA trust — fixed CA, initContainer injection

A **fixed** mitm CA (stable key+cert) is generated once, stored as a Secret. The
webhook mounts the CA private material into the sidecar's mitmproxy confdir
(stable fingerprint) and adds an **initContainer** to the app pod that writes
the CA public cert into the system trust store (decided — universal coverage
across Python/Node/Go/Java/curl, not just env-var-honoring runtimes). The
sandbox image stays stock.

### 6.8 Deployment

All new pieces — webhook, `inject.py` ConfigMap, fixed-CA Secret, per-sandbox
mTLS issuing material — ship as a **cubeplex-owned bundle** (decided), deployed
to the dedicated sandbox namespace. OpenSandbox server and egress image stay
100% stock.

## 7. Security considerations

- **Sandbox compromise leaks only `R`** — a selector, per-run, revocable,
  useless without the per-sandbox mTLS identity and only swappable when sent to
  its declared host.
- **Host boundary comes from the vault entry** (not the skill): the secret is
  swapped only for requests matching its declared host pattern(s), which also
  drive the egress allow-list. **Host regexes must be fully anchored**
  (`^…$`) and match the host only — an unanchored pattern like `github\.com`
  would also match `evil-github.com.attacker.net`; validation rejects
  unanchored host regexes.
- **Token-scan risk (accepted, mitigated):** scanning headers for `cbxref_`
  could in principle mis-match; mitigated by a high-entropy recognizable prefix,
  header-only scanning, a per-request ref cap, and the host check at exchange (a
  found ref still can't be redeemed for the wrong host).
- **Header capability (residual).** Host validation binds the secret to the
  declared host but not to a specific header/grammar: malicious sandbox code
  could place the placeholder in a non-auth header and have the real secret sent
  to that *same allowed host* (relevant if the host has an endpoint that
  reflects/logs arbitrary headers). Optional `header_names` (§6.4) narrows this;
  otherwise the placeholder grants arbitrary-header request capability **to the
  declared host only**. Documented and accepted for v1.
- **Broad host patterns are dangerous.** A wildcard like `*.example.com` where
  attackers can obtain a subdomain would let the exchange legitimately swap the
  secret to an attacker host. **Concrete validation rule:** a host pattern must
  resolve to a **single registrable domain (eTLD+1)** — using the Public Suffix
  List. A wildcard is allowed only as `*.<eTLD+1>` or deeper (e.g.
  `*.example.com` ✓, `api.example.com` ✓), and **rejected** at or above the
  registrable boundary (`*.com`, `*.co.uk`, `*` ✗). Anchored regexes must
  likewise match within one eTLD+1 (validation extracts the host the regex can
  match and applies the same test); reject if it can match more than one
  registrable domain. Exact hosts are preferred; wildcard/regex entries emit a
  warning, and widening a secret's host set is an elevated (admin) action.
- **Webhook blast radius:** dedicated namespace + ownerRef/image match +
  `Ignore` failurePolicy.
- **Secret hygiene:** flow persistence off; placeholder & substituted headers
  redacted; ref stored as hash; cache TTL bounded.
- **Exchange exposure:** only the distinct internal exchange host/port is
  reachable from the sandbox, not the full cubeplex API.
- **CA:** one fixed shared CA, no rotation in v1 (accepted; §9).

## 8. Testing & rollout

Two layers (full path needs Kubernetes; the vault/exchange logic does not):

- **Bare/local:** unit-test the `SidecarAuthenticator` strategies (incl.
  prod-guardrail startup failure), Env Vault scope resolution (user/workspace/
  org precedence; secret vs plain), run-start injection (ref mint + plain
  passthrough + network_policy assembly), and the exchange endpoint's identity +
  `sandbox_id` + host-allow + expiry checks — using the dev shared-secret
  authenticator.
- **Real cluster E2E** (self-hosted `kubernetes-admin@kubernetes`): a real
  sandbox with webhook + egress sidecar. Assert: (a) a tool's call to a
  real/test-mode host succeeds with the placeholder swapped; (b) the real secret
  is absent from sandbox env/fs/process; (c) sandbox code cannot redeem with `R`
  alone (no mTLS identity → rejected); (c2) a genuine sidecar with another
  sandbox's leaked `R` is rejected (`sandbox_id` mismatch); (d) a ref sent to a
  non-declared host is not swapped; (e) revoked/expired ref → fail closed;
  (f) logs contain no plaintext secret; (g) plain config env injects verbatim.
  Do not build a fake local sidecar.

Rollout: behind a per-workspace (or global) enablement flag; default off.

## 9. Open questions / recorded decisions

**Recorded decisions (this brainstorm):**

1. Sidecar identity: **mTLS**, per-sandbox cert carrying `sandbox_id`, verified
   directly by the exchange service (no ingress dependency).
2. CA trust: **initContainer** writes the CA into the system trust store.
3. Sandbox runs in a **dedicated namespace**; webhook scoped to it.
4. Scope = **skill-tool secrets via env vars** (HTTP-header-to-fixed-host), not
   LLM provider keys.
5. CA rotation: **not in v1**.
6. Deployment: **cubeplex-owned bundle**; OpenSandbox stays stock.
7. Host source: **Env Vault entry carries host** (doubles as allow-list +
   substitution boundary); skills declare env names only.
8. Substitution: **token find-and-replace** of `cbxref_` in headers.
9. Vault supports **plain (non-secret) config** entries injected verbatim.
11. Injection is **vault-driven**: all applicable vault entries are injected
    (secret → ref, plain → literal); skill `requires.env` is advisory UI only,
    not a runtime filter.

10. Host field is a **list** supporting exact / wildcard / **anchored regex**;
    regex-only patterns need an allow-list-expressible companion (§6.4).

**Still open:**
- Exact `requires.env` parsing surface in `frontmatter.py` (top-level vs nested
  `requires`).
- mTLS issuing mechanism for per-sandbox certs (webhook-embedded CA vs
  cert-manager `CertificateRequest`).
