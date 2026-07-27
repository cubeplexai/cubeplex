# Plan — sandbox panel proxy

**Spec:** [specs/2026-07-26-sandbox-panel-proxy-design.md](../specs/2026-07-26-sandbox-panel-proxy-design.md)
**Branch:** `fix/2026-07-23-deploy-script-dedup`

**Goal:** one token-authenticated HTTP+WS reverse proxy in the cubeplex backend
that serves the sandbox browser/terminal panels in both docker and k8s, replacing
the k8s-only OpenSandbox ingress-gateway.

**Architecture:** the panel URL embeds a short-lived HS256 JWT in its **path**
(`{api.public_url}/sandbox-panel/{token}/…`) so every relative asset + WS request
carries the credential. A public backend route verifies the token and reverse-
proxies to the cluster-internal `opensandbox-server/sandboxes/{id}/proxy/{port}`
(HTTP via httpx, WS via the `websockets` lib). Endpoint methods mint the URL;
nothing downstream (opensandbox-server) trusts anything but the network boundary.

**Tech stack:** FastAPI/Starlette (first backend WebSocket route), `httpx` and
`websockets` (both already direct deps), PyJWT (already used by `im/link.py`).

---

## Unit 1 — panel token sign/verify

- **Files:** `backend/cubeplex/sandbox/panel_token.py` (new). Mirror
  `cubeplex/im/link.py`.
- **Interfaces:**
  - `sign_panel_token(*, sandbox_id: str, port: int, secret: str, ttl: timedelta) -> str`
  - `verify_panel_token(token: str, *, secret: str) -> PanelClaims`
    where `PanelClaims` is a frozen dataclass `{sandbox_id: str, port: int}`.
  - `get_panel_base_url() -> str` (reads `api.public_url`, rstrip `/`)
    and reuse `get_jwt_secret()` (lift the one in `im/link.py` or import it).
- **Core logic:** HS256, `iss="cubeplex:sandbox-panel"`, claims `sid`/`port`/
  `exp`/`iat`. `verify` decodes with issuer check; any `PyJWTError` → `ValueError`.
  `port` is coerced back to `int`.
- **Tests** (`backend/tests/unit/test_panel_token.py`): roundtrip; expired token
  rejected; wrong-secret rejected; wrong-issuer rejected; tampered payload
  rejected; `port` type preserved. Pure, in-process → **unit**.

## Unit 2 — reverse-proxy route (HTTP + WebSocket)

- **Files:** `backend/cubeplex/api/routes/sandbox_panel.py` (new, router with **no**
  `/api/v1` prefix); mount in `backend/cubeplex/api/app.py` at root
  (`app.include_router(sandbox_panel.router)`).
- **Interfaces / routes:**
  - `@router.api_route("/sandbox-panel/{token}/{full_path:path}", methods=[...])`
    and a root variant `"/sandbox-panel/{token}/"`.
  - `@router.websocket("/sandbox-panel/{token}/{full_path:path}")` (+ root).
  - Forward target: `{sandbox.domain}/sandboxes/{sid}/proxy/{port}/{full_path}`,
    query string preserved; ws/http scheme chosen per handler.
- **Core logic:**
  - Verify token first (both handlers). HTTP invalid → `403`; WS invalid → accept
    then close with a policy-violation code (can't send a 403 body pre-accept).
  - **HTTP:** `httpx.AsyncClient.stream` with the incoming method/body/filtered
    headers (drop hop-by-hop: connection, keep-alive, transfer-encoding, upgrade,
    te, trailer, proxy-*). Relay upstream status + filtered headers + streamed
    body. Upstream connect failure → `502`.
  - **WS:** `await ws.accept()`, open upstream `websockets.connect(ws_url,
    subprotocols=…)`, run two tasks relaying `str`/`bytes` frames each way; on
    either close/`WebSocketDisconnect`, cancel the other and close both. Model on
    opensandbox `proxy.py` `_relay_client_messages` / `_relay_backend_messages`.
  - Internal opensandbox-server needs no api_key (proxy route is open) — do not
    attach one.
- **Tests** (`backend/tests/e2e/test_sandbox_panel_proxy.py`): stand up a tiny
  in-test upstream ASGI app (an HTTP echo + a WS echo) bound to a local port; set
  `sandbox.domain` to it. Assert: valid token proxies HTTP body through; valid
  token proxies a WS echo round-trip; **bad/expired token → 403 (HTTP) / close
  (WS)**; upstream-down → 502. Touches the FastAPI app + a real socket → **e2e**.

## Unit 3 — endpoint methods build the panel URL

- **Files:** `backend/cubeplex/sandbox/opensandbox.py`.
- **Change:** `get_browser_endpoint` / `get_terminal_endpoint` stop calling
  `self._sandbox.get_signed_endpoint(...)`. They mint a token for
  `(self._sandbox.id, PORT, exp)` and return
  `BrowserEndpoint(url=f"{base}/sandbox-panel/{token}/", headers={})`, where `base`
  = `get_panel_base_url()`. If `base` is empty → raise `SandboxError` (surfaces as
  the existing 501/503 path). Keep the trailing-slash + `_NEKO_URL_PARAMS`-compat
  behavior (params still appended by `ws_browser.py`).
  Remove the now-dead `_BROWSER_IRRELEVANT_HEADERS` handling only if fully orphaned.
- **Interfaces:** unchanged (`BrowserEndpoint`), so `ws_browser.py` /
  `ws_sandbox.py` are untouched; their `endpoint.headers` guards still hold
  (headers now always empty).
- **Tests** (`backend/tests/unit/test_opensandbox_endpoints.py`): a fake SDK
  sandbox with a known `id`; assert the returned URL matches
  `{base}/sandbox-panel/<jwt>/` and that `verify_panel_token` on the embedded JWT
  yields the right `sandbox_id`/`port`. Pure → **unit**.

## Unit 4 — config + deployment unification

- **Files:**
  - `deploy/kubernetes/charts/cubeplex/vendor/opensandbox-server/values.yaml`:
    `gateway.enabled: false` (default); note the OSEP-0011 keys are now optional.
  - `deploy/kubernetes/charts/cubeplex/values.local.yaml.example` +
    `deploy/docker-compose/config/config.production.local.yaml.example`: ensure
    `api.public_url` is documented as the panel base and `sandbox.secure_access:
    false`.
- **Core logic:** no signed gateway ⇒ `secure_access` stays false; panels flow
  through the backend. Keep `opensandbox-server` service internal (ClusterIP /
  docker network) — do **not** add a public gateway host.
- **Tests:** none (config); covered by the live verification below.

## Unit 5 — docs (ship with the code)

- **Files:** `docs/site/docs/deployment/docker-compose.md` (+ zh-Hans) capability
  matrix: browser/terminal **Yes** (was "Broken (signed routes k8s-only, 503)");
  `docs/site/docs/deployment/kubernetes.md` (+ zh-Hans): panels go through the
  backend proxy, the ingress-gateway is no longer required. Note `api.public_url`
  must be set (and be WS-reachable) for panels.
- No version internals in user docs (per prior guidance).

## Verification (before PR)

1. Backend: `uv run pytest tests/unit/test_panel_token.py
   tests/unit/test_opensandbox_endpoints.py tests/e2e/test_sandbox_panel_proxy.py`.
2. Live: on a real deployment, open the browser + terminal panels and confirm the
   Neko view and ttyd shell render (not 503); confirm a tampered token → 403.
   (Server-proxy HTTP+WS reachability to the Pod is already measured — see spec.)

## Notes / risks

- **First backend WebSocket route.** Confirm the ASGI server serves WS (uvicorn +
  `websockets`, already a dep) and that no middleware assumes HTTP-only. Health-
  check the WS path in the e2e.
- **Token TTL vs long panel sessions.** TTL 1h + the existing `/keepalive`. If a
  WS outlives the token it drops and the frontend re-opens (re-mints). Bump TTL if
  this bites in practice.
- **Backend carries panel streaming** in both modes now (the accepted trade-off).
