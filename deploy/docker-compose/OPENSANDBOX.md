# OpenSandbox under docker-compose

This doc covers the optional `compose.opensandbox.yaml` overlay: how
to deploy alibaba's [OpenSandbox](https://github.com/alibaba/OpenSandbox)
lifecycle server in **docker runtime mode** alongside the cubebox
compose stack, and **what cubebox features that mode cannot satisfy**.

If you only need cubebox chat without agent tool calls, you don't need
this overlay — leave `sandbox.enabled: false` in
`config.production.local.yaml`.

---

## 1. What the overlay deploys

`compose.opensandbox.yaml` adds **one** service to the stack:

```
opensandbox-server   image: opensandbox/server:latest
                     mounts: /var/run/docker.sock
                     reads:  /etc/opensandbox/config.toml
                     port:   8090
```

The OpenSandbox server is itself a normal Python/FastAPI container. When
it receives a `POST /sandboxes` request, it talks to the host docker
daemon via the mounted socket to spawn **sibling** sandbox containers
(not nested). That means sandbox containers run on the same docker
engine as cubebox, on a separate bridge network.

Security note: anything inside the `opensandbox-server` container can
effectively root the host via the docker socket. Keep it on your
private network; don't expose port 8090 publicly.

---

## 2. Quickstart

```bash
cd deploy/docker-compose

# 1. opensandbox config (gitignored)
cp config/opensandbox.toml.example config/opensandbox.toml
$EDITOR config/opensandbox.toml          # set api_key, eip/host_ip, execd_image, egress.image

# 2. backend secrets — sandbox section
$EDITOR config/config.production.secrets.yaml
#   sandbox:
#     domain:  "opensandbox-server:8090"   # docker DNS name from this overlay
#     image:   "<your sandbox image>"      # e.g. cubebox-sandbox:24.04-...
#     api_key: "<same as [server].api_key in opensandbox.toml>"

# 3. backend non-secret — enable sandbox + force server proxy
$EDITOR config/config.production.local.yaml
#   sandbox:
#     enabled: true
#     use_server_proxy: true     # required: docker bridge endpoints
#                                # rewrite via the server gateway

# 4. up with the overlay
docker compose \
  -f compose.yaml \
  -f compose.opensandbox.yaml \
  up -d
```

Operator-managed values (no template):

| Key | Where | Notes |
|---|---|---|
| `opensandbox.toml [server].api_key` | `config/opensandbox.toml` | required, must match `sandbox.api_key` in cubebox secrets |
| `opensandbox.toml [server].eip` | same | host/IP returned to cubebox in endpoint URLs; usually `host.docker.internal` |
| `opensandbox.toml [runtime].execd_image` | same | image carrying the **execd** binary; pull-reachable by host docker |
| `opensandbox.toml [egress].image` | same | egress sidecar image; required when cubebox sends network_policy (it always does) |
| `opensandbox.toml [docker].network_mode` | same | **must be `bridge`** for cubebox (see §3) |

---

## 3. Compatibility matrix — cubebox features under docker-mode OpenSandbox

Source of truth for the limitations: `~/OpenSandbox/server/opensandbox_server/services/docker.py` and `api/pool.py`. This was verified empirically against `opensandbox-server v0.1.14` by issuing the requests cubebox would make and reading the responses.

### ✅ Resolved: secure-access toggle

The docker runtime rejects `secureAccess=True` with HTTP 400 — that's
an OpenSandbox design choice (secured endpoints are an ingress-gateway
feature). cubebox exposes a config knob `sandbox.secure_access` which
defaults to `true` (preserves the Kubernetes-deployment behaviour);
the compose mode's `config.production.local.yaml.example` sets it to
`false`. With that flag flipped, cubebox sends `secureAccess: false`
on every create and the docker runtime accepts the request.

Verified end-to-end on this stack: chat → sandbox tool call →
`tool_result` returned containing real `ls -la /workspace` output.

### ⚠ Subject to constraints

| Feature | What works | What doesn't |
|---|---|---|
| `networkPolicy` (egress firewall) | Yes — but ONLY when `[docker].network_mode = "bridge"` | Rejected when `network_mode=host` or when bridge is a user-defined network |
| signed endpoint URLs (`expires=…`) | – | Not implemented for docker; cubebox doesn't use this today |
| server-proxy mode (`use_server_proxy: true`) | – | OpenSandbox v0.1.x has a known issue where the proxied endpoint URL drops the port. The example config sets `use_server_proxy: false` instead; the overlay wires `host.docker.internal` via `extra_hosts` so the backend can reach the host-mapped bridge ports of sandbox containers. |
| `pvc.claimName` volumes | Yes — but treated as docker named volumes | No CSI features, no ReadWriteMany |
| Pause / resume (`POST /sandboxes/{id}/pause` etc.) | Calls docker `pause/unpause` (cgroup freezer) | No checkpoint to disk — paused state is lost on host docker restart. cubebox already defaults `pause_on_idle: false` because of this |

### 🚫 K8s-only APIs (any runtime, listed for completeness)

The following routes return **501 Not Implemented** on docker runtime
even though they exist in the OpenAPI spec. cubebox does not currently
call any of them:

- `POST /pools` and related (pre-warmed pod pools)
- Snapshot APIs (`POST /sandboxes/{id}/snapshots` etc.)

---

## 4. Verifying

The `opensandbox-server` container exposes a health endpoint cubebox's
healthcheck consumes:

```bash
docker compose -f compose.yaml -f compose.opensandbox.yaml ps
# expect: opensandbox-server   Up (healthy)
```

Direct API probe (from inside the backend container, using docker DNS):

```bash
docker exec cubebox-backend-1 python -c "
import urllib.request, json
req = urllib.request.Request(
    'http://opensandbox-server:8090/sandboxes',
    headers={'OPEN-SANDBOX-API-KEY': '<your api_key>'},
)
print(urllib.request.urlopen(req, timeout=5).read().decode())
"
# expect: {"items":[], ...}
```

End-to-end (cubebox chat → sandbox tool call) works once
`config.production.local.yaml` has `sandbox.enabled: true` AND
`sandbox.secure_access: false` AND `sandbox.use_server_proxy: false`.
Verified on this stack against opensandbox-server v0.1.14 — a
`ls -la /workspace` prompt produced a real `tool_result` containing
the sandbox filesystem contents.

---

## 5. Down

```bash
docker compose -f compose.yaml -f compose.opensandbox.yaml down
# This also removes the cubebox stack. Use `down opensandbox-server`
# to remove only the overlay's service.
```

The mitm CA and any sandbox containers spawned by the server stay on
the host docker engine — they're not part of this project's compose
network. Inspect with `docker ps --filter "name=sandbox-"`.
