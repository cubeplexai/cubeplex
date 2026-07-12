# cubeplex on Kubernetes — Install Guide

A single `helm upgrade --install` deploys cubeplex (backend + frontend +
Postgres + Redis + MinIO, optionally the alibaba OpenSandbox umbrella) to
an existing Kubernetes cluster.

Design notes: [docs/dev/specs/2026-06-10-helm-deploy-design.md](../../docs/dev/specs/2026-06-10-helm-deploy-design.md).

---

## Contents

1. [Prerequisites](#1-prerequisites)
2. [Architecture](#2-architecture)
3. [Build and push images](#3-build-and-push-images)
4. [Author `values.local.yaml`](#4-author-valueslocalyaml)
5. [Install](#5-install)
6. [Post-install verification](#6-post-install-verification)
7. [Troubleshooting](#7-troubleshooting)
8. [Values reference](#8-values-reference)

---

## 1. Prerequisites

| Item | Requirement | Notes |
|---|---|---|
| Kubernetes | ≥ 1.21 | kubeadm / k3s / managed clusters all fine |
| Ingress controller | ingress-nginx recommended | chart uses `ingressClassName: nginx` |
| StorageClass | a dynamic provisioner | chart can create `cubeplex-work-hostpath` on top of openebs hostpath, or you can point at an existing one |
| Docker registry | writable + pullable from cluster nodes | default `192.168.1.101:8050/library` — override per your env |
| Helm | ≥ 3.9 | dep update + install |
| LLM provider credentials | at least one | api_key or base_url + api_key, see §4.4 |

**On the operator workstation** (not the cluster):

- `uv` — generates `requirements-frozen.txt`, used by `build-and-push.sh`
- `docker` — builds the images
- `helm`, `kubectl` — installs the chart

---

## 2. Architecture

```
Namespace: cubeplex
┌───────────────────────────────────────────────────────────────┐
│  Ingress (cubeplex.local)                                      │
│    /api/*, /health/* → backend  Service:8000                 │
│    /*                → frontend Service:3000                 │
├───────────────────────────────────────────────────────────────┤
│  backend Deployment (1 replica)                               │
│    initContainer: wait for postgres, run `alembic upgrade`    │
│    container:     uvicorn (cubeplex.api.app:create_app)        │
│    mounts: ConfigMap (non-secret) + Secret (secret)           │
├───────────────────────────────────────────────────────────────┤
│  frontend Deployment (1 replica)                              │
│    Next.js standalone runtime (node server.js)                │
├──────────────┬─────────────┬───────────────┬──────────────────┤
│ postgres SS  │ redis SS    │ rustfs SS     │ opensandbox      │
│  + PVC       │  + PVC      │  + PVC + Job  │ (optional        │
│              │             │  (bucket init)│  subchart)       │
└──────────────┴─────────────┴───────────────┴──────────────────┘
                                            │
                                            └── LLM providers (external)
```

All PVCs default to the `cubeplex-work-hostpath` StorageClass that the
chart creates. Override `storageClass.basePath` for a different node path,
or set `storageClass.create: false` and point each StatefulSet at an
existing class.

---

## 3. Build and push images

```bash
deploy/kubernetes/scripts/build-and-push.sh
```

The script:

1. Runs `uv export` on the host against `backend/uv.lock` to produce
   `backend/requirements-frozen.txt` (gitignored — `uv.lock` stays the
   source of truth).
2. `docker build` for backend and frontend, tagging
   `<REGISTRY>/<REPO>/cubeplex-<target>:<git-sha>` and `…:latest`.
3. `docker push` both tags.

### Common variables

| Variable | Default | Purpose |
|---|---|---|
| `REGISTRY` | `192.168.1.101:8050` | registry host:port |
| `REPO` | `library` | second-level namespace inside the registry |
| `TAG` | `git rev-parse --short HEAD` | image tag (also accepted as positional arg 1) |
| `TARGET` | `backend frontend` | build only one of these |

### Mirror knobs (network tuning)

The Dockerfiles default to upstream package sources. If your build host
hits Debian, PyPI, npm, or GitHub slowly, override at build time:

| Variable | Example | Effect |
|---|---|---|
| `APT_MIRROR_HOST` | `mirrors.tuna.tsinghua.edu.cn` | rewrites Debian sources inside both image stages |
| `PIP_INDEX_URL` | `https://pypi.tuna.tsinghua.edu.cn/simple` | passes through to pip |
| `UV_INDEX_URL` | same as PIP | passes through to uv |
| `NPM_REGISTRY` | `https://registry.npmmirror.com` | sets `pnpm config registry` in the frontend build |
| `GITHUB_MIRROR` | `https://githubfast.com/` | substitutes `https://github.com/` in the generated `requirements-frozen.txt` (only affects the cubepi git+url dependency) |

Empty / unset → upstream.

---

## 4. Author `values.local.yaml`

`values.local.yaml` is the single file an operator edits. Start from the
template:

```bash
cp deploy/kubernetes/charts/cubeplex/values.local.yaml.example \
   deploy/kubernetes/charts/cubeplex/values.local.yaml
$EDITOR deploy/kubernetes/charts/cubeplex/values.local.yaml
```

Each section below is documented in the order you fill it in.

### 4.1 Image tags (required)

```yaml
image:
  backend:
    tag: "9ab4005f"     # the git sha that build-and-push.sh just produced
  frontend:
    tag: "9ab4005f"
```

If you push to a non-default registry, also override:

```yaml
image:
  registry: "harbor.example.com"
  repository: "cubeplex"
  backend:
    name: "backend"
    tag: "v1.0.0"
```

### 4.2 Backend non-secret config

```yaml
backend:
  configOverrides:
    api:
      public_url: "http://cubeplex.example.com"
    public_base_url: "http://cubeplex.example.com"
    frontend_base_url: "http://cubeplex.example.com"
    deployment:
      mode: single_tenant       # single_tenant | multi_tenant
    auth:
      cookie_secure: false      # ★ HTTP installs MUST set false; HTTPS keep true
```

| Field | Default | Notes |
|---|---|---|
| `api.public_url` | `http://cubeplex.local` | absolute URL the backend hands out (OAuth redirects, etc.) |
| `public_base_url` | same | used by the backend for absolute URL construction |
| `frontend_base_url` | same | where the backend redirects browsers |
| `deployment.mode` | `single_tenant` | single-tenant auto-creates the org on first user registration |
| `auth.cookie_secure` | `true` (from `config.production.yaml`) | **must be false on plain HTTP** or clients silently drop the auth cookie |

Anything you put under `configOverrides` is rendered into
`config.production.local.yaml` and merged by dynaconf on top of
`config.production.yaml`. Any field in `backend/config.yaml` can be
overridden here, e.g.:

```yaml
backend:
  configOverrides:
    streaming:
      run_event_ttl_seconds: 86400      # default 12h → 24h
    attachments:
      max_file_bytes: 104857600         # 100 MiB
    compaction:
      threshold_ratio: 0.5
```

### 4.3 Backend secrets (required)

```yaml
backend:
  secrets:
    auth:
      jwt_secret: "..."     # openssl rand -hex 32
      csrf_secret: "..."    # openssl rand -hex 32
      vault_key: "..."      # python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

| Field | Purpose | Generate with |
|---|---|---|
| `jwt_secret` | signs / verifies user JWT cookies | `openssl rand -hex 32` |
| `csrf_secret` | CSRF double-submit cookie | `openssl rand -hex 32` |
| `vault_key` | Fernet key for the MCP / credentials vault | `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` |

All three are required. The chart fails install fast if any is empty.

### 4.4 LLM providers

```yaml
backend:
  secrets:
    llm:
      default_model: "deepseek/deepseek-v4-flash"
      fallback_models:
        - "cubeplex/qwen3.5-plus-thinking"
      providers:
        # Mode A — use a cubepi built-in preset (simplest)
        deepseek:
          preset: "deepseek/cn/anthropic-messages"
          api_key: "sk-..."

        # Mode B — fully custom (private gateway / self-hosted)
        cubeplex:
          base_url: "https://gateway.example.com/v1"
          api_key: "..."
          api: "openai-completions"
          models:
            - id: "qwen3.5-plus-thinking"
              name: "Qwen3.5 Plus"
              reasoning: true
              input: ["text", "image"]
              context_window: 991000
              max_tokens: 64000

        # Mode C — Volcengine ark coding interface
        arkcode:
          preset: "volcengine/cn/openai-completions/coding"
          api_key: "ark-..."
```

Notes:

- `default_model` format is `"<provider_name>/<model_id>"`; the
  `provider_name` must appear under `providers`.
- `fallback_models` is the same format; tried in order if `default_model`
  fails.
- Available `preset` names live in `cubepi/llm/catalog/data/vendors.yaml`
  (deepseek / doubao / qwen / minimax / openrouter / volcengine / …).
- Custom providers must declare `base_url`, `api_key`, `api`, and at
  least one entry in `models`.

Minimal viable config:

```yaml
backend:
  secrets:
    llm:
      default_model: "deepseek/deepseek-v4-flash"
      providers:
        deepseek:
          preset: "deepseek/cn/anthropic-messages"
          api_key: "sk-..."
```

### 4.5 Sandbox (optional)

The sandbox is the container runtime where agent tools (bash, file_read,
…) execute. Disabled = agents can still chat but tool calls fail.

```yaml
backend:
  secrets:
    sandbox:
      domain: "39.99.248.80:18080"     # OpenSandbox API host:port (no scheme)
      image: "hub.sensedeal.vip/library/cubeplex-sandbox:24.04-20260531"
      api_key: "..."
  sandbox:
    enabled: true                       # ★ flip this on if using an external sandbox
    use_server_proxy: false             # true when the cluster can't reach sandbox pods directly
```

Three typical layouts:

| Layout | values.local.yaml |
|---|---|
| Bundled OpenSandbox subchart | `opensandbox.enabled: true`; `backend.secrets.sandbox.domain` points at `cubeplex-opensandbox-server.cubeplex.svc.cluster.local:8090` |
| External OpenSandbox | `opensandbox.enabled: false`; `backend.sandbox.enabled: true`; `backend.secrets.sandbox.domain` points at the external host |
| No sandbox (chat-only) | `opensandbox.enabled: false`; leave `backend.sandbox.enabled` unset (it follows `opensandbox.enabled` → false) |

### 4.6 Bundled infra passwords (required)

```yaml
postgres:
  auth:
    password: "..."     # openssl rand -hex 16

redis:
  auth:
    password: "..."     # openssl rand -hex 16

rustfs:
  auth:
    secretKey: "..."   # openssl rand -hex 16
```

To use **external** Postgres / Redis / RustFS instead, disable the bundled
ones and point the backend at the external endpoints:

```yaml
postgres:
  enabled: false
backend:
  configOverrides:
    database:
      host: "external-pg.example.com"
      port: 5432
      user: cubeplex
      name: cubeplex
  secrets:
    database:
      password: "..."
```

(same pattern for redis / rustfs).

### 4.7 Ingress

```yaml
ingress:
  enabled: true
  className: "nginx"
  host: "cubeplex.example.com"
  tls:
    enabled: false
```

For HTTPS via cert-manager:

```yaml
ingress:
  tls:
    enabled: true
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
backend:
  configOverrides:
    api:
      public_url: "https://cubeplex.example.com"
    public_base_url: "https://cubeplex.example.com"
    frontend_base_url: "https://cubeplex.example.com"
    auth:
      cookie_secure: true
```

### 4.8 StorageClass

```yaml
storageClass:
  create: true                  # set false to use an existing class
  name: cubeplex-work-hostpath
  basePath: /work/cubeplex       # node directory to back the PVCs
```

Using an existing class instead:

```yaml
storageClass:
  create: false
postgres:
  persistence:
    storageClass: "fast-ssd"
redis:
  persistence:
    storageClass: "fast-ssd"
rustfs:
  persistence:
    storageClass: "fast-ssd"
```

### 4.9 OpenSandbox subchart (optional)

The chart can bundle alibaba's OpenSandbox umbrella (controller +
server) under the same release. Its execd / egress images come from
`sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com`, which the cluster
nodes need to be able to pull.

```yaml
opensandbox:
  enabled: false                # default in values.yaml is true; turn off
                                # when using an external sandbox
```

### 4.10 Egress secret-injection (optional)

When enabled, the chart deploys cubeplex's secret-injection feature: a
mitmproxy addon inside each sandbox container intercepts outbound HTTP,
swaps `cbxref_<id>` placeholders for real secret values fetched from the
backend over mTLS. End result: agent tool calls can reference
**credentials by name** (e.g. `Authorization: Bearer cbxref_slack_xyz`)
and the real token never enters the sandbox memory, the LLM prompt, or
the conversation history.

Moving pieces the chart wires up:

| Component | Location |
|---|---|
| Mutating admission webhook (Deployment + Service + SA + RBAC) | cubeplex namespace |
| `MutatingWebhookConfiguration` matching sandbox pods | cluster |
| Long-lived MITM CA Secret (`helm.sh/resource-policy: keep`) | cubeplex ns + mirrored into sandbox ns |
| `inject.py` mitmproxy addon ConfigMap | sandbox ns (hardcoded name `egress-inject-addon`) |
| Backend mTLS server cert + mTLS listener on `:8443` | cubeplex ns |
| Updated backend Service exposing `:8443` | cubeplex ns |

Build the extra image:

```bash
TARGET="backend frontend egress-webhook" \
  deploy/kubernetes/scripts/build-and-push.sh
```

Then turn on in `values.local.yaml`:

```yaml
egress:
  enabled: true
  # Namespace where sandbox pods actually run.
  # When using the bundled opensandbox subchart, "opensandbox-system".
  sandboxNamespace: "opensandbox-system"
  webhook:
    image:
      tag: "<git-sha>"          # same tag build-and-push.sh just produced
    # MUST exactly match opensandbox-server's configured egress.image.
    egressImage: "sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/egress:v1.0.12"
```

Notes:

- The chart auto-generates the MITM CA (`genCA`) on first install and
  marks the Secret `helm.sh/resource-policy: keep`, so upgrades and
  `helm uninstall` do not rotate the CA. Re-installs into an existing
  cluster pick up the same CA via `lookup`.
- The webhook serving cert and the backend mTLS server cert are signed
  by the same CA and follow the same lookup-or-mint rule.
- The webhook's `MutatingWebhookConfiguration` has `failurePolicy: Ignore`:
  a webhook outage never blocks sandbox pod creation. Affected sandboxes
  start without secret injection (placeholders stay literal) — alert on
  webhook health separately.
- Source code for the webhook + addon lives under
  `deploy/kubernetes/egress-bundle/`. The canonical `inject.py` is at
  `deploy/kubernetes/charts/cubeplex/files/egress/inject.py` (so the
  chart can read it via `Files.Get`);
  `deploy/kubernetes/egress-bundle/addon/inject.py` is a symlink to it.

---

## 5. Install

```bash
deploy/kubernetes/scripts/helm-install.sh
```

equivalent to:

```bash
helm dependency update deploy/kubernetes/charts/cubeplex
helm upgrade --install cubeplex deploy/kubernetes/charts/cubeplex \
  --namespace cubeplex --create-namespace \
  -f deploy/kubernetes/charts/cubeplex/values.yaml \
  -f deploy/kubernetes/charts/cubeplex/values.local.yaml \
  --wait --timeout 10m
```

`helm dependency update` re-packages `charts/opensandbox-0.2.0.tgz` from
`vendor/opensandbox/`, so the `vendor/` directory must be present
(committed in this repo; refresh with
`deploy/kubernetes/scripts/vendor-opensandbox.sh` when alibaba ships a
new version).

### Uninstall

```bash
helm uninstall cubeplex -n cubeplex
# StatefulSet PVCs are not auto-deleted:
kubectl delete pvc -n cubeplex -l app.kubernetes.io/name=cubeplex
```

---

## 6. Post-install verification

### 6.1 Pods

```bash
kubectl -n cubeplex get pods
# Expected:
#   cubeplex-backend-...     1/1  Running
#   cubeplex-frontend-...    1/1  Running
#   cubeplex-postgresql-0    1/1  Running
#   cubeplex-redis-master-0  1/1  Running
#   cubeplex-minio-0         1/1  Running
```

### 6.2 Smoke test (deployment correctness)

```bash
INGRESS_IP=<your node IP> deploy/kubernetes/scripts/smoke-test.sh
```

Checks: rollout complete, health endpoints respond, ingress routes
backend + frontend, Next.js renders HTML. Does **not** hit the LLM.

### 6.3 End-to-end test (LLM round-trip)

```bash
HOST=cubeplex.local IP=<your node IP> PORT=30019 \
PROMPT="Say the word hello and nothing else." \
  deploy/kubernetes/scripts/e2e.sh
```

Drives the full path:

```
GET  /api/v1/system/info     — confirm deployment_mode
POST /api/v1/auth/register   — single-tenant auto-setup
POST /api/v1/auth/login      — cookie jar
GET  /api/v1/auth/me         — receive CSRF cookie (safe methods only)
POST /ws/{ws}/conversations  — conv_id
POST .../conversations/{conv}/messages — run_id
GET  .../runs/{run}/stream   — SSE; assert text_delta arrives
```

To exercise the sandbox path too:

```bash
PROMPT='List the contents of /workspace (run `ls -la /workspace`).' \
  deploy/kubernetes/scripts/e2e.sh
# Expected: SSE contains tool_call / tool_result events.
```

### 6.4 Manual browser check

```bash
# On the operator workstation
echo "<node IP> cubeplex.local" | sudo tee -a /etc/hosts
# Then visit http://cubeplex.local:<ingress NodePort>/
```

Find the ingress NodePort with
`kubectl -n ingress-nginx get svc ingress-nginx-controller`.

---

## 7. Troubleshooting

### Backend CrashLoopBackOff

```bash
kubectl -n cubeplex logs deploy/cubeplex-backend -c backend --previous
```

| Symptom | Fix |
|---|---|
| `PermissionError: '/app/logs'` | image is older than `75da36fb`; rebuild |
| `CUBEPLEX_AUTH__VAULT_KEY is required` | add `backend.secrets.auth.vault_key` to values.local.yaml |
| `Could not connect to 'cubeplex-postgresql:5432'` | postgres still starting; usually self-heals |
| `Provider 'X' not found` | `default_model: "X/..."` references a provider not in `providers` |

### PVC stays `Pending`

```bash
kubectl get pods -A | grep init-pvc
# ErrImagePull on openebs/linux-utils → pre-pull on every node:
docker pull openebs/linux-utils:3.5.0
# or use an existing StorageClass instead of the chart's openebs SC
```

### Login cookie missing / API 403

- HTTP installs need `backend.configOverrides.auth.cookie_secure: false`.
- 403 on mutating endpoints = CSRF: send any GET first to receive
  `cubeplex_csrf`, then pass it as `X-CSRF-Token` header on POST/PUT/PATCH/DELETE.

### Ingress 502

- Backend pod is still in Init / not Ready.
- The ingress controller NodePort lives on the node, not on 127.0.0.1 —
  check `kubectl -n ingress-nginx get svc`.

### LLM responses empty / errors

- Watch the backend log:
  `kubectl -n cubeplex logs deploy/cubeplex-backend -c backend -f`
- Typical causes: invalid `api_key`, wrong `preset` name, model retired.
- Validate the provider out-of-band:
  `curl https://<base_url>/v1/models -H "Authorization: Bearer <key>"`.

---

## 8. Values reference

Abridged tree of chart values:

```yaml
image:
  registry: "192.168.1.101:8050"
  repository: "library"
  pullPolicy: "IfNotPresent"
  backend:  { name: "cubeplex-backend",  tag: "" }     # tag required
  frontend: { name: "cubeplex-frontend", tag: "" }     # tag required

backend:
  replicaCount: 1
  service: { port: 8000 }
  sandbox:                          # see §4.5
    enabled: <follows opensandbox.enabled>
    use_server_proxy: false
  resources: { requests, limits }
  env: { ENV_FOR_DYNACONF: production }
  configOverrides:                  # ConfigMap, non-secret
    api: { host, port, public_url }
    deployment: { mode }
    public_base_url
    frontend_base_url
    auth: { cookie_secure }
    # …any backend/config.yaml key
  secrets:                          # Secret
    auth:    { jwt_secret, csrf_secret, vault_key }     # required
    llm:     { default_model, fallback_models, providers }
    sandbox: { domain, image, api_key }

frontend:
  replicaCount: 1
  service: { port: 3000 }
  resources: { ... }

ingress:
  enabled: true
  className: "nginx"
  host: "cubeplex.local"
  tls: { enabled: false }
  annotations: { ... }              # SSE-friendly defaults included

storageClass:
  create: true
  name: "cubeplex-work-hostpath"
  basePath: "/work/cubeplex"

postgres:
  enabled: true
  image: "postgres:16-alpine"
  auth: { username, database, password }
  persistence: { storageClass, size }
  resources: { ... }

redis:
  enabled: true
  image: "redis:7-alpine"
  auth: { password }
  persistence: { storageClass, size }
  resources: { ... }

rustfs:
  enabled: true
  image: "rustfs/rustfs:1.0.0-beta.4"
  mcImage: "minio/mc:..."
  auth: { accessKey, secretKey }
  defaultBucket: "cubeplex"
  persistence: { storageClass, size }
  resources: { ... }

opensandbox:
  enabled: true
  opensandbox-server:     { server:     { replicaCount: 1 } }
  opensandbox-controller: { controller: { replicaCount: 1 } }
```

### Minimal `values.local.yaml`

```yaml
image:
  backend:  { tag: "<git-sha>" }
  frontend: { tag: "<git-sha>" }

backend:
  configOverrides:
    api:
      public_url: "http://cubeplex.local"
    public_base_url: "http://cubeplex.local"
    frontend_base_url: "http://cubeplex.local"
    auth:
      cookie_secure: false
  secrets:
    auth:
      jwt_secret: "<openssl rand -hex 32>"
      csrf_secret: "<openssl rand -hex 32>"
      vault_key: "<Fernet.generate_key()>"
    llm:
      default_model: "deepseek/deepseek-v4-flash"
      providers:
        deepseek:
          preset: "deepseek/cn/anthropic-messages"
          api_key: "sk-..."

postgres: { auth: { password: "<openssl rand -hex 16>" } }
redis:    { auth: { password: "<openssl rand -hex 16>" } }
rustfs:   { auth: { secretKey: "<openssl rand -hex 16>" } }

opensandbox:
  enabled: false
```

A fuller annotated template lives at
`deploy/kubernetes/charts/cubeplex/values.local.yaml.example`.
