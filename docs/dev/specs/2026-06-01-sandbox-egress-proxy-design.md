# Sandbox egress upstream proxy (admin-configurable)

- **Date:** 2026-06-01
- **Status:** Design, pending review.
- **Area:** admin sandbox policy (`cubebox/models/sandbox_policy.py`,
  `cubebox/api/.../sandbox_policy`), egress addon
  (`deploy/egress-bundle/addon/inject.py`), internal egress API
  (`cubebox/api/routes/internal_egress.py`), admin sandbox policy page (frontend).
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
- Live hot-reload for already-running sandboxes. Config is read at sandbox
  start (matches the existing static `network_rules` model). A future TTL
  re-fetch can be added if needed.
- SOCKS proxies. mitmproxy's `via` supports `http`/`https` upstreams only.

## Requirements

- **Scope:** org admin, stored on the existing `SandboxPolicy` (admin scope;
  v1 writes the org-default row, `scope_workspace_id=NULL`; per-workspace
  overrides come for free via the existing resolver).
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
   verification that the exchange endpoint already uses). cubebox maps
   cert → `sandbox_id` → org/workspace, resolves the effective `SandboxPolicy`
   (reusing the resolver in `services/sandbox_policy.py`; v1 = org-default row),
   and returns its `egress_proxy`:

   ```json
   { "proxy": "http://192.168.1.150:7892" }   // or { "proxy": null }
   ```

### Addon (`inject.py`)

- **`load` / `running` hook** (mitmproxy startup): fetch `proxy-config` once
  over the existing mTLS channel; parse the URL into `(scheme, (host, port))`;
  store in a module-level variable. Retry a few times with short backoff; on
  persistent failure, **fail-open** (leave proxy unset).
- **`request` hook**: if a proxy is set, assign
  `flow.server_conn.via = (scheme, (host, port))` for **every** flow (global).
  Skip connections whose destination is the proxy's own address (self-loop
  guard; in practice the proxy is reached on a non-80/443 port that nft doesn't
  redirect, so it won't normally hit mitmproxy, but guard anyway).

The proxy step is layered on top of the existing `cbxref_` substitution and
host allow-list in the same `request` hook — both security properties are
preserved unchanged.

### Data flow

```
admin sets proxy URL in the sandbox policy page → SandboxPolicy.egress_proxy (DB)

sandbox starts → mitmproxy loads inject.py
  → load hook: mTLS GET /internal/egress/proxy-config
       → cubebox maps cert → sandbox → resolve effective policy → returns its proxy
  → addon caches (scheme, (host, port))

sandbox outbound 443 → nft redirect → mitmproxy
  → request hook: substitute cbxref_ (existing) + set server_conn.via=proxy (new)
  → mitmproxy → clash (192.168.1.150:7892) → split (CN direct / overseas) → target
```

### Error handling

- **Fetch fails** (network / mTLS / cubebox down) → **fail-open** (direct, no
  via). Rationale: the proxy is an *availability* feature, not a security
  boundary — `cbxref_` substitution and the host allow-list remain enforced.
  Failing open degrades to direct (CN works, overseas fails) without leaking
  secrets or cutting off all networking. Logged + alerted.
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
    skipped. Tested as pure helpers (same style as `scan_placeholders` /
    `should_substitute_header`).
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
```
