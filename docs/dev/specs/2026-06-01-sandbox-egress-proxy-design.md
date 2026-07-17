# Sandbox egress upstream proxy (admin-configurable)

- **Date:** 2026-06-01
- **Status:** Design, pending review.
- **Area:** admin sandbox policy (`cubeplex/models/sandbox_policy.py`,
  `cubeplex/api/.../sandbox_policy`), egress addon
  (`deploy/egress-bundle/addon/inject.py`), internal egress API
  (`cubeplex/api/routes/internal_egress.py`), admin sandbox policy page (frontend).
- **Background:** [docs/dev/notes/2026-06-01-egress-tls-fingerprint-vs-mitm.md](../notes/2026-06-01-egress-tls-fingerprint-vs-mitm.md)

## Background & motivation

Sandboxes in a GFW region can't reach blocked services (x.com, etc.). The
egress sidecar runs mitmproxy in transparent mode; mitmproxy's **upstream hop**
to the target isn't tunneled, so the GFW resets it at the TLS handshake. The
isolated experiment in the notes doc proved the failure is the GFW, not a TLS
fingerprint — so the clean fix is simply to give mitmproxy an **upstream proxy**
for its outbound hop.

Today the only way to cross the GFW is to set `TWITTER_PROXY` (or `https_proxy`)
**inside the sandbox**, which routes traffic to port 7892, bypassing mitmproxy
entirely — and with it the whole egress control plane (host allow-list +
`cbxref_` substitution). That is a security regression (see notes doc).

This spec makes the upstream proxy an **admin-configurable** property of the
sandbox policy, applied by the egress addon, so the sandbox stays unprivileged
and the egress control plane stays intact.

## Goals

- An org admin can configure **one** upstream proxy (an `http(s)://host:port`
  URL) that applies to **all** sandbox egress in the org.
- The egress addon routes mitmproxy's upstream through that proxy. The proxy
  (e.g. clash/mihomo) does its own geo-splitting (CN direct / overseas via
  proxy), so a single global proxy is sufficient.
- Preserve the existing `cbxref_` substitution and host allow-list — the proxy
  is purely additive, set in the same addon hook.

## Non-goals (YAGNI)

- Per-host proxy mapping or multiple proxies. Global only; the proxy splits.
- Proxy authentication / credentials. No-auth `http(s)` proxy only.
- Live hot-reload for already-running sandboxes. The proxy is fetched lazily on
  first use and cached for the sandbox's life; a config change takes effect on
  the next sandbox start. A future TTL re-fetch can be added if needed.
- SOCKS proxies. mitmproxy's `via` supports `http`/`https` upstreams only.

## Requirements

- **Scope:** org admin, stored on the existing `SandboxPolicy` (admin scope).
  v1 resolves the **org-default** row (`scope_workspace_id=NULL`) only: the
  sidecar cert proves `sandbox_id` → org, but carries no workspace, so
  per-workspace overrides are out of reach until the identity carries one.
- **Range:** global — all outbound, not per-host.
- **Auth:** none.

## Design

### Data model

`SandboxPolicy` gains:

```python
egress_proxy: str | None  # full URL, e.g. "http://192.168.1.150:7892"
                          # None = disabled = direct (current behavior)
```

- Alembic migration adds the nullable column.
- Validation on write: URL parses; scheme ∈ {`http`, `https`}; host + port
  present; reject `socks*` schemes (mitmproxy `via` can't do SOCKS).

### API

Two endpoints, both reusing existing mechanisms:

1. **Admin config** — the existing admin sandbox-policy `GET`/`PUT`
   (`SandboxPolicyOut` / `UpdateSandboxPolicyIn`) each gain an `egress_proxy`
   field. No new route; it reads/writes alongside the existing
   `network_rules` / `network_default_action`.

2. **Addon fetch (new)** — `GET /api/v1/internal/egress/proxy-config`,
   authenticated by the sidecar's mTLS client cert (reuse the `SidecarIdentity`
   verification the exchange endpoint already uses). `SidecarIdentity` proves
   only `sandbox_id`, so the lookup chain is explicit: verify cert → **unscoped**
   `UserSandbox` lookup by `sandbox_id` → `org_id` → resolve the org-default
   `SandboxPolicy` (`services/sandbox_policy.py`) → return its `egress_proxy`.
   (`UserSandbox.get_by_sandbox_id` is org-scoped, so a small unscoped variant —
   like the exchange path's sandbox→org resolution — is needed.) Response:

   ```json
   { "proxy": "http://192.168.1.150:7892" }   // or { "proxy": null }
   ```

### Addon (`inject.py`)

- **Lazy fetch (not startup-snapshot)**: on the **first** HTTPS flow, if the
  proxy config hasn't been resolved yet, fetch `proxy-config` over the existing
  mTLS channel, parse the URL into `(scheme, (host, port))`, and cache it in a
  module-level variable (cache `None` when the policy has no proxy). If the
  fetch fails, leave it *unresolved* (don't cache) and let that flow go direct —
  the next flow retries. This avoids a startup race against a cold/rolling
  control plane turning into a permanently direct-only sandbox (a startup-only
  fetch would do exactly that). See Error handling.
- **`request` hook**: once a proxy is resolved, assign
  `flow.server_conn.via = (scheme, (host, port))` for **every** flow —
  unconditional, including private/LAN destinations. Geo/private splitting is
  delegated to the proxy (clash), which must be configured to send private
  ranges DIRECT (e.g. `GEOIP,private,DIRECT`). The only skip is the proxy's own
  address (self-loop guard; in practice it's reached on a non-80/443 port nft
  doesn't redirect, so it won't hit mitmproxy anyway).

The proxy step is layered on top of the existing `cbxref_` substitution and
host allow-list in the same `request` hook — both security properties are
preserved unchanged.

### Data flow

```
admin sets proxy URL in the sandbox policy page → SandboxPolicy.egress_proxy (DB)

first sandbox HTTPS flow → mitmproxy request hook
  → if proxy not yet resolved: mTLS GET /internal/egress/proxy-config
       → cubeplex: cert → sandbox_id → UserSandbox → org_id → org-default policy → proxy
  → cache (scheme,(host,port)) or None; on failure: this flow direct, retry next flow

every sandbox outbound 443 → nft redirect → mitmproxy
  → request hook: substitute cbxref_ (existing) + set server_conn.via=proxy (new)
  → mitmproxy → clash (host:port) → split (private/CN direct, overseas via) → target
```

### Error handling

- **Fetch fails** (network / mTLS / cubeplex down) → that flow goes **direct**
  (fail-open), and the config stays *unresolved* so the **next** flow retries —
  a transient control-plane outage never becomes a permanent direct-only
  sandbox. Rationale: the proxy is an *availability* feature, not a security
  boundary — `cbxref_` substitution and the host allow-list remain enforced, so
  failing open degrades to direct (CN works, overseas fails) without leaking
  secrets or cutting off networking. Logged + alerted.
- **Unconfigured / empty** → direct (current behavior).
- **Invalid URL** → rejected at write time; the addon also defensively treats a
  parse failure as unconfigured and logs it.
- **Proxy unreachable at runtime** (clash down) → mitmproxy upstream error
  (502 to the caller). This is a proxy-ops issue, outside the addon's remit.

### Frontend

The admin sandbox policy page gains a single input for the proxy URL, next to
the existing network rules. Scope-isolated: this is the admin page only; no
workspace/user surface.

## Testing

E2E is intentionally skipped: the full stack (sandbox pod + egress sidecar +
mitmproxy + an upstream proxy + clash, on k8s) is too heavy to stand up in CI,
and a mocked version wouldn't exercise the real `via` path. Covered by unit
tests + a manual end-to-end check instead.

- **Unit:**
  - Addon: proxy parse + `via` assignment — configured URL → correct
    `(scheme,(host,port))`; empty → no `via`; destination == proxy address →
    skipped. Lazy fetch: a failed fetch stays unresolved and is retried on the
    next flow; a success caches the value (or `None`). Tested as pure helpers
    (same style as `scan_placeholders` / `should_substitute_header`).
  - `proxy-config` endpoint: mTLS identity → correct org's proxy; no policy /
    empty proxy → `null`.
  - URL validation: scheme allow-list; SOCKS rejected; malformed rejected.
- **Manual:** admin sets a proxy → start a sandbox → confirm egress goes
  through it (proxy logs show the requests / a blocked domain becomes
  reachable); unset → confirm direct (proxy sees no sandbox traffic).

## Security notes

- The proxy URL is not a secret (a LAN clash address); stored plain on
  `SandboxPolicy`, like `network_rules`.
- The sandbox never sees the proxy config — the addon in the sidecar fetches
  it. The sandbox stays unprivileged: no `TWITTER_PROXY` env, no bypass of the
  egress control plane. This is the whole point versus the workaround documented
  in the notes file.
- **Network policy still applies (verified against the live nft ruleset).** Host
  allow/deny is enforced in `table inet opensandbox` (filter hook, policy drop)
  on the **destination IP of the sandbox's own connection** — the DNS proxy
  admits allowed names into a dynamic allow-set. Setting `via` only changes
  mitmproxy's *upstream* hop; the sandbox's original connection still targets the
  real destination, so allow/deny is judged on the real target exactly as before
  (a denied host is dropped before it ever reaches mitmproxy). This is precisely
  why it is NOT the `TWITTER_PROXY` bypass — there the sandbox itself dialed the
  proxy (daddr = proxy, port 7892 not redirected) so the real target was never
  seen by the filter; here the sandbox still dials the real target and the proxy
  is one hop downstream inside the sidecar.
- **Config dependency:** the proxy host must be reachable under the egress policy
  (mitmproxy→proxy has daddr = proxy). Today `192.168.1.150` is already allowed
  (the exchange endpoint lives there too); a new proxy host must be allowed
  likewise, or mitmproxy's upstream to it is dropped → 502.
