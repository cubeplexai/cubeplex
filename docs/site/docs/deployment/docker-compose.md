---
sidebar_position: 2
title: Docker Compose
---

# CubePlex on Docker Compose

`docker compose up -d` deploys CubePlex (backend + frontend + Postgres +
Redis + rustfs S3 store) on a single host. It uses the same container
images as the Kubernetes deployment mode — only the orchestration differs.

## 1. Prerequisites

| Item | Requirement |
|---|---|
| Linux host with Docker engine | ≥ 24, with `docker compose` v2 |
| Outbound network to your image registry | wherever your images are hosted/pushed |
| LLM provider credentials | at least one — see [LLM provider configuration](./overview.md#llm-provider-configuration) |
| Open ports on the host | one for the frontend (default 3000), optionally one for the backend (default 8000) |

No Kubernetes, no Helm.

## 2. Architecture

```
Host
  ├─ port :3000 → frontend (Next.js)  ── proxies /api/* server-side ──┐
  │                                                                   │
  └─ port :8000 → backend  (FastAPI / uvicorn) ◄─────────────────────┘
                    ├─ depends on → postgres   (named volume)
                    ├─ depends on → redis      (named volume)
                    └─ depends on → rustfs     (S3 store, named volume)

Bootstrap services (run-to-completion):
  backend-migrate  alembic upgrade head (gates backend boot)
  bucket-init      mc mb (idempotent rustfs bucket create)
```

All inter-service communication uses Docker DNS (for example, the backend
reaches Postgres at `postgres:5432`). The host only sees the frontend port
(and optionally the backend port, for direct API access).

## 3. Build images

For GitHub releases, use the immutable image tag from the release manifest.
For a local or private-registry build, use the Kubernetes mode's build
script — the backend and frontend images are identical either way:

```bash
deploy/kubernetes/scripts/build-and-push.sh
# pushes to ${REGISTRY:-192.168.1.101:8050}/${REPO:-library}/cubeplex-{backend,frontend}:<YYMMDD>-<branch>-<short-sha>
```

Then, in `.env`, set `BACKEND_TAG` and `FRONTEND_TAG` to that immutable tag.

## 4. Configure (`.env` + two YAML files)

Three files, all gitignored:

| File | What it does |
|---|---|
| `.env` | Image tags, host port mappings, infra passwords. Read directly by `docker compose` for variable substitution in `compose.yaml`. |
| `config/config.production.local.yaml` | Non-secret runtime config (mode, public URL, cookie security, sandbox toggle). Mounted into the backend. |
| `config/config.production.secrets.yaml` | Secrets — JWT/CSRF/vault material, infra passwords (must match `.env`), LLM provider API keys. Mounted into the backend. |

### 4.1 `.env`

```bash
cp .env.example .env
$EDITOR .env
```

Required:

```dotenv
IMAGE_REGISTRY=192.168.1.101:8050
IMAGE_REPO=library
BACKEND_TAG=<YYMMDD>-<branch>-<short-sha>
FRONTEND_TAG=<YYMMDD>-<branch>-<short-sha>

# openssl rand -hex 16
POSTGRES_PASSWORD=<...>
REDIS_PASSWORD=<...>
RUSTFS_SECRET_KEY=<...>
```

Optional (defaults shown):

```dotenv
BACKEND_PORT=8000
FRONTEND_PORT=3000
POSTGRES_USER=cubeplex
POSTGRES_DB=cubeplex
RUSTFS_ACCESS_KEY=cubeplex
OBJECTSTORE_BUCKET=cubeplex
```

### 4.2 `config.production.local.yaml`

```bash
cp config/config.production.local.yaml.example   config/config.production.local.yaml
$EDITOR config/config.production.local.yaml
```

| Field | Default | Notes |
|---|---|---|
| `api.public_url` | `http://localhost:8000` | The URL clients reach the backend at; if you put a reverse proxy in front, use **that** URL. |
| `public_base_url` | same | Used by the backend for absolute URL construction. |
| `frontend_base_url` | `http://localhost:3000` | Where the backend redirects browsers. |
| `deployment.mode` | `single_tenant` | `single_tenant` auto-creates an org on first user registration; `multi_tenant` requires explicit org bootstrap. |
| `auth.cookie_secure` | `false` | Must stay `false` on plain HTTP — otherwise clients silently drop the auth cookie. |
| `sandbox.enabled` | `false` | Flip to `true` and fill `sandbox.{domain,image,api_key}` in `secrets.yaml` to use an external OpenSandbox. See [Optional: sandbox execution](#optional-sandbox-execution-opensandbox) below. |

:::note
`database.host`, `redis.url`, and `objectstore.endpoint` use Docker DNS names
(`postgres`, `redis`, `rustfs`) — don't change them unless you renamed the
services.
:::

### 4.3 `config.production.secrets.yaml`

```bash
cp config/config.production.secrets.yaml.example   config/config.production.secrets.yaml
$EDITOR config/config.production.secrets.yaml
```

Required:

```yaml
production:
  auth:
    jwt_secret:  "<openssl rand -hex 32>"
    csrf_secret: "<openssl rand -hex 32>"
    vault_key:   "<Fernet.generate_key()>"
  database:
    password: "<same as POSTGRES_PASSWORD>"
  redis:
    url: "redis://:<REDIS_PASSWORD>@redis:6379/0"
  objectstore:
    access_key:    "cubeplex"             # same as RUSTFS_ACCESS_KEY
    access_secret: "<RUSTFS_SECRET_KEY>"
```

See [Required secrets](./overview.md#required-secrets) for what
`jwt_secret`, `csrf_secret`, and `vault_key` are for and how to generate
them.

### 4.4 LLM providers

Configured under `production.llm` in `config.production.secrets.yaml` — see
[LLM provider configuration](./overview.md#llm-provider-configuration) for
the full field reference and examples.

## 5. Up / down / logs

```bash
# bring up (also pulls the latest tags)
deploy/docker-compose/scripts/up.sh

# tail logs
docker compose -f deploy/docker-compose/compose.yaml logs -f backend

# stop and remove containers (volumes preserved)
docker compose -f deploy/docker-compose/compose.yaml down
```

:::warning
`docker compose -f deploy/docker-compose/compose.yaml down -v` stops and
**deletes volumes** (Postgres data, rustfs data, Redis data) — destructive,
use only when you intend to wipe the deployment.
:::

`up.sh` refuses to start if `.env` or either YAML config file is missing.

## 6. Verification

```bash
# fast health-only check
deploy/docker-compose/scripts/smoke-test.sh

# end-to-end including a real LLM call
PROMPT="Say the word hello and nothing else." \
  deploy/docker-compose/scripts/e2e.sh
```

`e2e.sh` drives:

```
register → single-tenant auto-setup → create conversation
        → POST message → SSE stream → assert text_delta arrived
```

Both scripts default to `localhost`; override with `HOST`, `BACKEND_PORT`,
`FRONTEND_PORT` to run against a remote host.

## 7. Troubleshooting

### Backend keeps restarting

```bash
docker compose -f deploy/docker-compose/compose.yaml logs backend --tail=50
```

| Symptom | Fix |
|---|---|
| `CUBEPLEX_AUTH__VAULT_KEY is required` | Add `auth.vault_key` in `secrets.yaml`. |
| `connection refused on postgres:5432` | Postgres is still starting; should self-heal — check `docker compose ps`. |
| `Provider 'X' not found` | `default_model: "X/..."` references a provider not listed under `providers`. |

### Login cookie missing on HTTP

`config.production.local.yaml`'s `auth.cookie_secure` must be `false` —
otherwise the browser (or curl) silently drops the auth cookie because the
connection is plain HTTP.

### Frontend → backend fails (CORS / 502)

`compose.yaml` sets `CUBEPLEX_API_URL=http://backend:8000` on the frontend
container, so Next.js proxies `/api/*` server-side over the Docker network.
If you changed service names, update that env var too.

### Image pull denied

If your registry is private:

```bash
docker login ${IMAGE_REGISTRY}
```

The compose stack inherits the Docker daemon's credentials.

### Stuck `bucket-init`

```bash
docker compose -f deploy/docker-compose/compose.yaml logs bucket-init
```

If rustfs isn't reachable, check the rustfs container's healthcheck — rustfs
publishes a console on `:9001` you can hit locally to confirm it's up.

## Optional: sandbox execution (OpenSandbox)

CubePlex executes agent tool calls (bash, file read/write, …) inside a
sandbox. Without it, chat still works but tool calls fail. This section
covers deploying alibaba's [OpenSandbox](https://github.com/alibaba/OpenSandbox)
lifecycle server in **Docker runtime mode** alongside the compose stack.

If you only need CubePlex chat without agent tool calls, skip this section
and leave `sandbox.enabled: false` in `config.production.local.yaml`.

### What the overlay deploys

The optional `compose.opensandbox.yaml` overlay adds one service:

```
opensandbox-server   image: opensandbox/server:latest
                     mounts: /var/run/docker.sock
                     reads:  /etc/opensandbox/config.toml
                     port:   8090
```

The OpenSandbox server is a normal Python/FastAPI container. When it
receives `POST /sandboxes`, it talks to the host Docker daemon via the
mounted socket to spawn **sibling** sandbox containers (not nested) — they
run on the same Docker engine as CubePlex, on a separate bridge network.

:::danger
Anything inside the `opensandbox-server` container can effectively root the
host via the Docker socket. Keep it on your private network — don't expose
port 8090 publicly.
:::

### Quickstart

```bash
cd deploy/docker-compose

# 1. OpenSandbox config (gitignored)
cp config/opensandbox.toml.example config/opensandbox.toml
$EDITOR config/opensandbox.toml          # set api_key, eip/host_ip, execd_image, egress.image

# 2. backend secrets — sandbox section
$EDITOR config/config.production.secrets.yaml
#   sandbox:
#     domain:  "opensandbox-server:8090"   # Docker DNS name from this overlay
#     image:   "<your sandbox image>"      # e.g. cubeplex-sandbox:24.04-...
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
| `opensandbox.toml [server].api_key` | `config/opensandbox.toml` | Required; must match `sandbox.api_key` in CubePlex secrets. |
| `opensandbox.toml [server].eip` | same | Host/IP returned to CubePlex in endpoint URLs; usually `host.docker.internal`. |
| `opensandbox.toml [runtime].execd_image` | same | Image carrying the **execd** binary; pull-reachable by the host Docker daemon. |
| `opensandbox.toml [egress].image` | same | Egress sidecar image; required because CubePlex always sends a network policy. |
| `opensandbox.toml [docker].network_mode` | same | Must be `bridge` for CubePlex (see the compatibility matrix below). |

### Compatibility — CubePlex features under Docker-mode OpenSandbox

Docker runtime mode has real limitations compared to Kubernetes-mode
OpenSandbox. This matrix reflects `opensandbox-server v0.1.14`.

**Secure-access toggle:** the Docker runtime rejects `secureAccess=True`
with HTTP 400 — secured endpoints are a Kubernetes ingress-gateway feature.
CubePlex exposes a `sandbox.secure_access` config knob that defaults to
`true` (matching Kubernetes-mode behavior); the compose mode's example
config sets it to `false`, so CubePlex sends `secureAccess: false` and the
Docker runtime accepts the request. With that flag set, chat → sandbox tool
call → `tool_result` works end to end.

| Feature | What works | What doesn't |
|---|---|---|
| Network policy (egress firewall) | Yes — but only when `[docker].network_mode = "bridge"` | Rejected when `network_mode=host` or a user-defined bridge network |
| Signed endpoint URLs (`expires=…`) | – | Not implemented for Docker mode; CubePlex doesn't use this today |
| Server-proxy mode (`use_server_proxy: true`) | – | OpenSandbox v0.1.x drops the port from the proxied endpoint URL. The example config uses `use_server_proxy: false` instead, and the overlay wires `host.docker.internal` via `extra_hosts` so the backend can reach the host-mapped bridge ports of sandbox containers. |
| `pvc.claimName` volumes | Yes — but treated as Docker named volumes | No CSI features, no ReadWriteMany |
| Pause / resume (`POST /sandboxes/{id}/pause`, etc.) | Calls Docker `pause`/`unpause` (cgroup freezer) | No checkpoint to disk — paused state is lost on host Docker restart. CubePlex defaults `pause_on_idle: false` for this reason. |

The following routes return `501 Not Implemented` on Docker runtime, even
though they exist in the OpenAPI spec (CubePlex doesn't call any of them
today): `POST /pools` and related pre-warmed pod pools, and the snapshot
APIs (`POST /sandboxes/{id}/snapshots`, etc.) — both are Kubernetes-only.

### Verifying

```bash
docker compose -f compose.yaml -f compose.opensandbox.yaml ps
# expect: opensandbox-server   Up (healthy)
```

Direct API probe (from inside the backend container, using Docker DNS):

```bash
docker exec cubeplex-backend-1 python -c "
import urllib.request, json
req = urllib.request.Request(
    'http://opensandbox-server:8090/sandboxes',
    headers={'OPEN-SANDBOX-API-KEY': '<your api_key>'},
)
print(urllib.request.urlopen(req, timeout=5).read().decode())
"
# expect: {"items":[], ...}
```

End-to-end (CubePlex chat → sandbox tool call) works once
`config.production.local.yaml` has `sandbox.enabled: true` **and**
`sandbox.secure_access: false` **and** `sandbox.use_server_proxy: false`. A
prompt like `ls -la /workspace` should produce a real `tool_result`
containing the sandbox filesystem contents.

### Tearing down

```bash
docker compose -f compose.yaml -f compose.opensandbox.yaml down
# This also removes the CubePlex stack. Use `down opensandbox-server`
# to remove only the overlay's service.
```

The MITM CA and any sandbox containers spawned by the server stay on the
host Docker engine — they aren't part of this project's compose network.
Inspect with `docker ps --filter "name=sandbox-"`.

## Optional: document parsing (docling-serve)

The backend's `file_read` tool converts uploaded PDF / office documents to
markdown by calling a [docling-serve](https://github.com/docling-project/docling-serve)
instance. Without it, other file types still work but document parsing
doesn't. The optional `compose.docling.yaml` overlay is self-contained — it
doesn't reference or extend any service from `compose.yaml` — so it
supports two deployment shapes.

### Combined: same host, same Docker network

```bash
cd deploy/docker-compose

docker compose \
  -f compose.yaml \
  -f compose.docling.yaml \
  --profile cpu \
  up -d
```

Backend reaches it as `docling-serve-cpu:5001` over Docker DNS — no manual
network bridging needed. Use `--profile gpu` instead for the CUDA image
(`docling-serve-cu130`, requires the NVIDIA container runtime on the host).

### Standalone: a separate host

Because the overlay doesn't depend on anything else in `compose.yaml`, you
can copy just `compose.docling.yaml` to its own host — for example a
dedicated GPU box shared by multiple projects — and run it there on its
own:

```bash
docker compose -f compose.docling.yaml --profile gpu up -d
```

Then point CubePlex's backend at that host from wherever it runs.

### Configure the backend

Either way, always pass `--profile cpu` or `--profile gpu` — neither
`docling-serve-*` service starts without one (the model-download job runs
for either). Set the resulting URL in `config.production.local.yaml`:

```yaml
parsers:
  docling_serve:
    base_url: "http://docling-serve-cpu:5001"     # combined, --profile cpu
    # base_url: "http://docling-serve-cu130:5001" # combined, --profile gpu
    # base_url: "http://<standalone host>:<port>" # standalone deployment
```

### Model download and registries

The `docling-models` service downloads the model set (layout, table former,
OCR, and VLM models — several GB) into a named volume on first start, and
reuses it on restart. Both `docling-serve-cpu` and `docling-serve-cu130`
wait for it to finish before serving.

If the default GHCR registry or HuggingFace are slow or blocked from your
build host, override before starting:

```bash
# Alternate image registry (quay.io mirror, or a China mainland sync — verify before production use)
export DOCLING_REGISTRY=quay.io/docling-project
# or: export DOCLING_REGISTRY=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/ghcr.io/docling-project

# HuggingFace mirror for model downloads
export HF_ENDPOINT=https://hf-mirror.com
# HF_TOKEN=hf_xxx   # only needed for gated/private repos
```
