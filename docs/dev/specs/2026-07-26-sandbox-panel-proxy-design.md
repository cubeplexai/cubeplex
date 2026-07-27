# Sandbox panel proxy — unified live-view/terminal gateway

**Status:** design · 2026-07-26
**Branch:** `fix/2026-07-23-deploy-script-dedup`

## Goal

Make the sandbox **browser (Neko) and terminal (ttyd) panels work in both docker
and Kubernetes deployments** by routing panel traffic through one authenticated
reverse proxy inside the cubeplex backend, instead of the k8s-only OpenSandbox
ingress-gateway.

## Context — why this is changing

Today the endpoint methods in `backend/cubeplex/sandbox/opensandbox.py`
(`get_browser_endpoint`, `get_terminal_endpoint`) **unconditionally** call the
OpenSandbox SDK's `get_signed_endpoint(...)`, which returns an OSEP-0011 signed
URL pointing at the OpenSandbox **ingress-gateway**. That gateway only exists in
the Kubernetes runtime. In docker-compose there is no gateway, so both panels
return **503**. The route comment at `ws_browser.py` already flags the gap:
*"same-origin proxy not yet implemented"*.

The panel URL is handed straight to the frontend and loaded in an **iframe by the
user's remote browser** (`ws_browser.py`, `ws_sandbox.py`). So the URL must be:

1. **reachable from the user's browser** — not a docker-internal name, and
2. **authenticated without request headers** — an iframe navigation and a
   WebSocket sub-resource cannot attach `Authorization`/CSRF headers, and cookies
   don't cross to the backend's origin.

The k8s gateway satisfies both with a signed token **in the URL path**
(`gateway.route.mode=uri`): the token is the auth, and because it's in the path,
every relative asset + WebSocket request the panel client makes carries it.

### What we verified before choosing this design (2026-07-26, live k8s)

OpenSandbox's lifecycle server already exposes an unsigned **server-proxy** route
(`components proxy.py`) with **both HTTP and WebSocket** handlers:
`@router.api_route("/sandboxes/{id}/proxy/{port}/{full_path:path}")` and
`@router.websocket(...)`, port-parameterised, full bidirectional relay. Measured
against the real k8s dev cluster with a freshly created sandbox:

- **HTTP proxy → Pod** (execd `:44772`) returns real data, **with or without**
  the server api_key.
- **WebSocket proxy → Pod** (`:7681`, a stand-in WS echo server) upgrades and
  relays bidirectionally, **with or without** the server api_key.
- The proxy route **deliberately skips the server's own api_key auth** (upstream
  commit "skip auth for proxy-to-sandbox paths"; tenant auth is multi-tenant
  only). So in cubeplex's single-tenant use it is **open** — which means the
  security boundary MUST be enforced by cubeplex, and opensandbox-server must
  never be exposed publicly.

Both runtimes reach the same place: `opensandbox-server/sandboxes/{id}/proxy/{port}`
→ Pod (k8s) or container (docker). So one cubeplex-side proxy unifies both.

## Approaches considered

- **A — backend reverse proxy, unified (chosen).** cubeplex backend adds one
  token-authenticated HTTP+WS reverse-proxy route that forwards to
  opensandbox-server's server-proxy. Endpoint methods mint a cubeplex-signed URL.
  Both docker and k8s use it; the OpenSandbox gateway is dropped. Cleanest and
  removes a whole subsystem (gateway deploy, OSEP-0011 signing keys, a separate
  public NodePort/host). Cost: panel WS/streaming now flows through the backend,
  and it's the first WebSocket route in the backend.
- **B — endpoint-selection + graceful degrade.** Branch on `secure_access`: k8s
  keeps the signed gateway, docker returns a clean 501 "panel only in k8s". No
  proxy built. Fast and safe, but docker panels stay unavailable and the two
  runtimes never unify.
- **C — unified code, gateway kept as optional fallback.** Same proxy as A but
  keep helm `gateway.enabled` as a togglable bypass. Hedge against backend panel
  load, at the cost of maintaining two panel paths.

Chosen **A**: the WS/auth/Pod-reach risks are now measured, not assumed, and
unification simplifies the k8s deployment surface (no separate gateway host).

## Design — what literally happens

### Signed panel token (auth in the URL path)

A short-lived **HS256 JWT** signed with the existing `auth.jwt_secret`, mirroring
`cubeplex/im/link.py`. Claims: `sid` (sandbox id), `port`, `exp`, `iat`,
`iss="cubeplex:sandbox-panel"`. TTL matches the endpoint method's `expires_in`
(default 1h). The token carries the sandbox id and port, so the proxy trusts the
**token**, never a path/query the client could edit.

### Panel URL shape (token in the path prefix)

```
{api.public_url}/sandbox-panel/{token}/
```

The token sits in the path, so the panel client's relative asset requests and its
WebSocket handshake all resolve under `/sandbox-panel/{token}/…` and carry the
token automatically (same trick as the k8s gateway's `mode=uri`). The trailing
slash after the token is required so relative paths resolve correctly (same reason
the current signed-URL code appends one).

`api.public_url` is the backend's own public base URL (already a config key;
`http://localhost:8000` in the compose example). If it is empty, panels are
unavailable and the endpoint methods raise a clean 501 — no silent breakage.

### Backend reverse-proxy route

New router mounted at app **root** (not under `/api/v1`, so it is never caught by
the frontend's `/api/*` Next rewrite — which cannot proxy WebSocket anyway):

```
GET/HEAD/…  /sandbox-panel/{token}/{full_path:path}
WEBSOCKET   /sandbox-panel/{token}/{full_path:path}
```

Both handlers: verify the token (else **403**) → read `sid`, `port` from claims →
forward to `{sandbox.domain}/sandboxes/{sid}/proxy/{port}/{full_path}` preserving
query string:

- **HTTP** — stream via `httpx.AsyncClient`; copy method/body/headers minus
  hop-by-hop headers; stream the upstream response (status + headers + body) back.
- **WebSocket** — accept the client WS, open an upstream WS with the `websockets`
  library to the `ws://…/proxy/{port}/…` URL, and run two relay tasks pumping
  text+binary frames both ways until either side closes. Mirrors opensandbox's own
  `proxy.py` relay.

The route is **public** (no `require_member`): the signed token is the credential,
exactly like the k8s gateway model. This is the only viable auth for iframe
sub-resources + WS, and it's why opensandbox-server must stay private.

### Endpoint methods (`opensandbox.py`)

`get_browser_endpoint` / `get_terminal_endpoint` stop calling the SDK's
`get_signed_endpoint`. They mint a panel token for `(self._sandbox.id, PORT, exp)`
and return `BrowserEndpoint(url=f"{panel_base}/sandbox-panel/{token}/…", headers={})`.
No headers ⇒ the existing `endpoint.headers` 501 guards in `ws_browser.py` /
`ws_sandbox.py` pass unchanged. The frontend is **unchanged**: it still receives an
absolute URL and iframes it (it already does this for the cross-origin k8s
gateway URL today).

### Deployment / unification

- Sandboxes are created with `secure_access=false` (already the case in the dev
  values) — the signed gateway is no longer used, so no signed access is required
  on the sandbox side.
- Helm: `gateway.enabled` defaults **false**; the OSEP-0011 signing keys and the
  separate gateway host/NodePort are no longer needed for panels.
- opensandbox-server stays cluster-internal (ClusterIP / internal docker network)
  — never public. cubeplex's backend is the only public entry, gated by the token.

## Out of scope

- Rate-limiting / DoS protection on the proxy route (future hardening).
- Per-request re-authorization beyond the signed token's TTL.
- Keeping the OpenSandbox gateway as a runtime-selectable fallback (removed; can be
  re-introduced if backend panel-streaming load proves a problem).
- Any frontend code change (the response contract `{url}` is unchanged).

## Success criteria

- **docker-compose:** opening the browser panel renders the Neko live view and the
  terminal panel renders a working ttyd shell — no 503.
- **k8s:** same panels render through the backend proxy with the gateway removed.
- A missing / expired / tampered token ⇒ **403**; opensandbox-server needs **no**
  api_key from the proxy and is not publicly reachable.
- Existing tool calls and the file-tree panel are unaffected.
