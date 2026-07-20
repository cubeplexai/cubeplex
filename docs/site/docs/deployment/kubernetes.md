---
sidebar_position: 3
title: Kubernetes (Helm)
---

# CubePlex on Kubernetes

A single `helm upgrade --install` deploys CubePlex (backend + frontend +
Postgres + Redis + rustfs, optionally the alibaba OpenSandbox umbrella) to
an existing Kubernetes cluster.

## 1. Prerequisites

| Item | Requirement | Notes |
|---|---|---|
| Kubernetes | ≥ 1.21 | kubeadm / k3s / managed clusters all fine |
| Ingress controller | ingress-nginx recommended | the chart uses `ingressClassName: nginx` |
| StorageClass | a dynamic provisioner | the chart can create `cubeplex-work-hostpath` on top of OpenEBS hostpath, or you can point at an existing one |
| Image pull access | cluster nodes reach `ghcr.io` + Docker Hub | default images are the public GHCR releases; only needs a private registry if you self-build |
| Helm | ≥ 3.9 | dependency update + install |
| LLM provider credentials | at least one | see [LLM provider configuration](./overview.md#llm-provider-configuration) |

On the **operator workstation** (not the cluster):

- `helm`, `kubectl` — installs the chart

Only needed if you build your own images instead of using the GHCR releases:

- `uv` — generates `requirements-frozen.txt`, used by `build-and-push.sh`
- `docker` — builds the images

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

Two more optional in-namespace services can be turned on: the egress
secret-injection webhook ([§4.10](#410-egress-secret-injection-optional))
and a docling-serve document parser
([§4.11](#411-docling-document-parsing-optional), Deployment + models PVC).
Both are off by default.

## 3. Images

The chart defaults to the **public prebuilt release images on GHCR** — the
cluster nodes pull them directly, so there is no operator build step for a
standard install:

```text
ghcr.io/cubeplexai/cubeplex-backend:<version>
ghcr.io/cubeplexai/cubeplex-frontend:<version>
```

A `v<semver>` release tag (pushed by `.github/workflows/images.yml`) publishes
both, plus `cubeplex-egress-webhook` and `cubeplex-sandbox` (the latter as
`sandbox-v<version>`). Each tag is a multi-platform manifest for `linux/amd64`
and `linux/arm64`. GHCR may also show an `unknown/unknown` provenance entry —
metadata, not a runnable platform. Pick a version from the
[releases page](https://github.com/cubeplexai/cubeplex/releases) and set it as
the image tag in §4.1. Use a real release tag, never `latest`.

### Build your own images (optional)

Only needed for a private registry, an air-gapped cluster, or a patched image.
The script pushes to a registry **your cluster nodes can pull from** — set
`REGISTRY` / `REPO` to your own (there is no working public default for a push
target).

```bash
REGISTRY=your-registry.example.com REPO=cubeplex \
  deploy/kubernetes/scripts/build-and-push.sh
```

The script:

1. Runs `uv export` on the host against `backend/uv.lock` to produce
   `backend/requirements-frozen.txt` (gitignored — `uv.lock` stays the
   source of truth).
2. `docker build` for the selected targets, tagging
   `<REGISTRY>/<REPO>/cubeplex-<target>:<YYMMDD>-<branch>-<short-sha>` by
   default.
3. `docker push` the immutable tag. Set `PUSH_LATEST=true` only when a
   development environment explicitly needs a moving `latest` tag.

### Common variables

| Variable | Default | Purpose |
|---|---|---|
| `REGISTRY` | `localhost:5000` | Registry host:port — set to your own. |
| `REPO` | `cubeplex` | Second-level namespace inside the registry. |
| `TAG` | `<YYMMDD>-<branch>-<short-sha>` | Image tag (also accepted as positional arg 1). |
| `TARGET` | `backend frontend` | Space-separated targets; also supports `sandbox` and `egress-webhook`. |
| `PUSH_LATEST` | `false` | Additionally push `latest` when set to `true`. |

### Mirror knobs (network tuning)

The Dockerfiles default to upstream package sources. If your build host
hits Debian, PyPI, npm, or GitHub slowly, override at build time:

| Variable | Example | Effect |
|---|---|---|
| `APT_MIRROR_HOST` | `mirrors.tuna.tsinghua.edu.cn` | Rewrites Debian sources inside both image stages. |
| `PIP_INDEX_URL` | `https://pypi.tuna.tsinghua.edu.cn/simple` | Passed through to pip. |
| `PIP_TRUSTED_HOST` | `pypi.tuna.tsinghua.edu.cn` | Trusts an HTTP/private PyPI host. |
| `UV_INDEX_URL` | same as PIP | Passed through to uv. |
| `NPM_REGISTRY` | `https://registry.npmmirror.com` | Sets `pnpm config registry` in the frontend build. |
| `GITHUB_MIRROR` | `https://githubfast.com/` | Substitutes `https://github.com/` in the generated `requirements-frozen.txt` (only affects the cubepi git+url dependency). |

Empty / unset → upstream.

### Release sandbox selection

The sandbox version is stored in `deploy/images/sandbox/VERSION`. Increment
it when sandbox contents change. The sandbox workflow publishes both
`<YYMMDD>-<branch>-<short-sha>` and `sandbox-v<version>`; the release
workflow records the corresponding `sandbox-v<version>` reference in the
release manifest. The release workflow does not download a candidate
sandbox image or run a runtime compatibility test — the sandbox E2E/nightly
workflow remains separate.

## 4. Author `values.local.yaml`

`values.local.yaml` is the single file an operator edits. Start from the
template:

```bash
cp deploy/kubernetes/charts/cubeplex/values.local.yaml.example \
   deploy/kubernetes/charts/cubeplex/values.local.yaml
$EDITOR deploy/kubernetes/charts/cubeplex/values.local.yaml
```

Each section below is documented in the order you fill it in. Anything under
`backend.configOverrides` / `backend.secrets` maps to a backend config key —
for the full field reference and the merge rules, see the
[backend configuration reference](./backend-config.md).

### 4.1 Image tags (optional)

`image.registry` / `image.repository` default to `ghcr.io` / `cubeplexai`, and
the tag defaults to the chart's `appVersion` — so an installed release chart
already points at the matching images, and you can skip this section entirely.
Set `image` only to override:

```yaml
image:
  # Pin a different image version than the chart's appVersion:
  backend:  { tag: "v0.2.0" }
  frontend: { tag: "v0.2.0" }
```

For a self-built / private-registry image, also set the location:

```yaml
image:
  registry: "your-registry.example.com"
  repository: "cubeplex"
  backend:  { tag: "<YYMMDD>-<branch>-<short-sha>" }   # build-and-push.sh output
  frontend: { tag: "<YYMMDD>-<branch>-<short-sha>" }
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
      cookie_secure: false      # HTTP installs MUST set false; HTTPS keeps true
```

| Field | Default | Notes |
|---|---|---|
| `api.public_url` | `http://cubeplex.local` | Absolute URL the backend hands out (OAuth redirects, etc.). |
| `public_base_url` | same | Used by the backend for absolute URL construction. |
| `frontend_base_url` | same | Where the backend redirects browsers. |
| `deployment.mode` | `single_tenant` | Single-tenant auto-creates the org on first user registration. |
| `auth.cookie_secure` | `true` (from `config.production.yaml`) | Must be `false` on plain HTTP, or clients silently drop the auth cookie. |

Anything under `configOverrides` is rendered into
`config.production.local.yaml` and merged by dynaconf on top of
`config.production.yaml`. Any field in `backend/config.yaml` can be
overridden here, for example:

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

See [Required secrets](./overview.md#required-secrets) for what each field
is for. All three are required — the chart fails install fast if any is
empty.

### 4.4 LLM providers

```yaml
backend:
  secrets:
    llm:
      # see LLM provider configuration for the full field reference
```

Configured the same way as the shared
[LLM provider configuration](./overview.md#llm-provider-configuration) —
just nested under `backend.secrets.llm` instead of `production.llm`.

### 4.5 Sandbox (optional)

The sandbox is the container runtime where agent tools (bash, file_read,
…) execute. Disabled = agents can still chat but tool calls fail.

```yaml
backend:
  secrets:
    sandbox:
      domain: "<opensandbox-host>:8090"  # OpenSandbox API host:port (no scheme)
      image: "ghcr.io/cubeplexai/cubeplex-sandbox:sandbox-v0.1.0"
      api_key: "..."
  sandbox:
    enabled: true                       # flip this on if using an external sandbox
    use_server_proxy: false             # true when the cluster can't reach sandbox pods directly
```

Three typical layouts:

| Layout | `values.local.yaml` |
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

To use **external** Postgres / Redis / rustfs instead, disable the bundled
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

(Same pattern for Redis / rustfs.)

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

The chart can bundle alibaba's OpenSandbox umbrella (controller + server)
under the same release. Its controller / server / execd / egress images default
to Docker Hub (`opensandbox/*`), which the cluster nodes need to be able to
pull. For mainland-China clusters, override each with the
`sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/` prefix (same
image names and tags) in the vendored subchart values.

```yaml
opensandbox:
  enabled: false                # default in values.yaml is true; turn off
                                 # when using an external sandbox
```

### 4.10 Egress secret-injection (optional)

When enabled, the chart deploys CubePlex's secret-injection feature: a
mitmproxy addon inside each sandbox container intercepts outbound HTTP,
swapping `cbxref_<id>` placeholders for real secret values fetched from the
backend over mTLS. The result: agent tool calls can reference **credentials
by name** (for example, `Authorization: Bearer cbxref_slack_xyz`), and the
real token never enters the sandbox memory, the LLM prompt, or the
conversation history.

Moving pieces the chart wires up:

| Component | Location |
|---|---|
| Mutating admission webhook (Deployment + Service + SA + RBAC) | cubeplex namespace |
| `MutatingWebhookConfiguration` matching sandbox pods | cluster |
| Long-lived MITM CA Secret (`helm.sh/resource-policy: keep`) | cubeplex ns + mirrored into sandbox ns |
| `inject.py` mitmproxy addon ConfigMap | sandbox ns (hardcoded name `egress-inject-addon`) |
| Backend mTLS server cert + mTLS listener on `:8443` | cubeplex ns |
| Updated backend Service exposing `:8443` | cubeplex ns |

The `cubeplex-egress-webhook` image ships with each GHCR release, so no extra
build is needed. Turn the feature on in `values.local.yaml`:

```yaml
egress:
  enabled: true
  # Namespace where sandbox pods actually run.
  # When using the bundled opensandbox subchart, "opensandbox-system".
  sandboxNamespace: "opensandbox-system"
  webhook:
    image:
      tag: "v0.2.0"             # same release version as backend/frontend
    # MUST exactly match opensandbox-server's configured egress.image.
    # China mirror: sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/egress:v1.0.12
    egressImage: "opensandbox/egress:v1.0.12"
```

(Self-building instead? Add `egress-webhook` to `TARGET` when running
`build-and-push.sh` and use that tag.)

Notes:

- The chart auto-generates the MITM CA (`genCA`) on first install and marks
  the Secret `helm.sh/resource-policy: keep`, so upgrades and
  `helm uninstall` do not rotate the CA. Re-installs into an existing
  cluster pick up the same CA via `lookup`.
- The webhook serving cert and the backend mTLS server cert are signed by
  the same CA and follow the same lookup-or-mint rule.
- The webhook's `MutatingWebhookConfiguration` has `failurePolicy: Ignore`:
  a webhook outage never blocks sandbox pod creation. Affected sandboxes
  start without secret injection (placeholders stay literal) — alert on
  webhook health separately.

### 4.11 Docling document parsing (optional)

The backend's `DoclingParser` converts uploaded PDF / office documents to
markdown by calling a [docling-serve](https://github.com/docling-project/docling-serve)
instance. Turn it on to deploy that service in-cluster; the chart then
auto-points the backend at it (config key `parsers.docling_serve.base_url`).
Leave it off to skip docling parsing, or point the backend at an external
docling-serve via `backend.configOverrides`.

```yaml
docling:
  enabled: true
  # Default is the CPU image. For GPU, use docling-serve-cu130 and add GPU
  # resources / a nodeSelector under docling.resources.
  # image: ghcr.io/docling-project/docling-serve-cpu:v1.16.1
  persistence:
    storageClass: cubeplex-work-hostpath
    size: 15Gi      # model cache; ~10 GB downloaded on first start
  # Mainland-China HuggingFace mirror for the model download (optional):
  # env:
  #   HF_ENDPOINT: https://hf-mirror.com
  #   HF_TOKEN: hf_xxx        # only for gated/private repos
```

The model set is downloaded once by an initContainer into a ReadWriteOnce
PVC and reused across restarts (single replica, `Recreate` strategy). First
start therefore blocks on the download — watch
`kubectl logs -c model-download deploy/<release>-docling`.

To use an external docling-serve instead of deploying one, keep
`docling.enabled: false` and set the URL directly:

```yaml
backend:
  configOverrides:
    parsers:
      docling_serve:
        base_url: "http://docling.example.internal:5001"
```

## 5. Install

### Recommended: from the published chart

Each release publishes the chart to GHCR as an OCI artifact — no repo checkout
needed, just your `values.local.yaml`:

```bash
helm upgrade --install cubeplex oci://ghcr.io/cubeplexai/charts/cubeplex \
  --version 0.2.0 \
  --namespace cubeplex --create-namespace \
  --values values.local.yaml \
  --wait --timeout 10m
```

Pick the chart version from the
[releases page](https://github.com/cubeplexai/cubeplex/releases). The published
chart bundles the infra templates and the OpenSandbox subchart, and its default
image tags match the chart version — so you only supply `values.local.yaml`
(grab the template from the repo or the release assets).

### Alternative: from a repo checkout

For a customized chart or a development build:

```bash
deploy/kubernetes/scripts/helm-install.sh
```

equivalent to:

```bash
# vendor/opensandbox has nested subcharts, so build its deps first
helm dependency update deploy/kubernetes/charts/cubeplex/vendor/opensandbox
helm dependency update deploy/kubernetes/charts/cubeplex
helm upgrade --install cubeplex deploy/kubernetes/charts/cubeplex \
  --namespace cubeplex --create-namespace \
  -f deploy/kubernetes/charts/cubeplex/values.yaml \
  -f deploy/kubernetes/charts/cubeplex/values.local.yaml \
  --wait --timeout 10m
```

### Uninstall

```bash
helm uninstall cubeplex -n cubeplex
# StatefulSet PVCs are not auto-deleted:
kubectl delete pvc -n cubeplex -l app.kubernetes.io/name=cubeplex
```

## 6. Post-install verification

### 6.1 Pods

```bash
kubectl -n cubeplex get pods
# Expected:
#   cubeplex-backend-...     1/1  Running
#   cubeplex-frontend-...    1/1  Running
#   cubeplex-postgresql-0    1/1  Running
#   cubeplex-redis-master-0  1/1  Running
#   cubeplex-rustfs-0        1/1  Running
```

### 6.2 Smoke test (deployment correctness)

```bash
INGRESS_IP=<your node IP> deploy/kubernetes/scripts/smoke-test.sh
```

Checks: rollout complete, health endpoints respond, ingress routes backend
+ frontend, Next.js renders HTML. Does **not** hit the LLM.

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

## 7. Troubleshooting

### Backend CrashLoopBackOff

```bash
kubectl -n cubeplex logs deploy/cubeplex-backend -c backend --previous
```

| Symptom | Fix |
|---|---|
| `PermissionError: '/app/logs'` | Image is older than `75da36fb`; rebuild. |
| `CUBEPLEX_AUTH__VAULT_KEY is required` | Add `backend.secrets.auth.vault_key` to `values.local.yaml`. |
| `Could not connect to 'cubeplex-postgresql:5432'` | Postgres still starting; usually self-heals. |
| `Provider 'X' not found` | `default_model: "X/..."` references a provider not in `providers`. |

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
  `cubeplex_csrf`, then pass it as `X-CSRF-Token` header on
  POST/PUT/PATCH/DELETE.

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

## 8. Values reference

Abridged tree of chart values:

```yaml
image:
  registry: "ghcr.io"
  repository: "cubeplexai"
  pullPolicy: "IfNotPresent"
  backend:  { name: "cubeplex-backend",  tag: "" }     # tag required (e.g. v0.2.0)
  frontend: { name: "cubeplex-frontend", tag: "" }     # tag required (e.g. v0.2.0)

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
  # postgres:18 + PGroonga + pgvector; required by conversation-search
  image: "cubeplex/postgresql-pgroonga-pgvector:18.2-pgroonga4.0.6-pgvector0.8.2"
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

docling:                            # optional, see §4.11
  enabled: false
  image: "ghcr.io/docling-project/docling-serve-cpu:v1.16.1"
  service: { port: 5001 }
  persistence: { storageClass, size }
  env: { }                          # e.g. HF_ENDPOINT, HF_TOKEN
  resources: { ... }

opensandbox:
  enabled: true
  opensandbox-server:     { server:     { replicaCount: 1 } }
  opensandbox-controller: { controller: { replicaCount: 1 } }
```

### Minimal `values.local.yaml`

```yaml
image:
  backend:  { tag: "v0.2.0" }
  frontend: { tag: "v0.2.0" }

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
      default_model: "openai/gpt-5.6-terra"
      providers:
        openai:               # any OpenAI-compatible endpoint
          base_url: "https://api.openai.com/v1"
          api_key: "sk-..."
          api: "openai-completions"
          models:
            - { id: "gpt-5.6-terra", name: "GPT-5.6 Terra", input: ["text", "image"],
                context_window: 128000, max_tokens: 16384 }

postgres: { auth: { password: "<openssl rand -hex 16>" } }
redis:    { auth: { password: "<openssl rand -hex 16>" } }
rustfs:   { auth: { secretKey: "<openssl rand -hex 16>" } }

opensandbox:
  enabled: false
```

A fuller annotated template lives at
`deploy/kubernetes/charts/cubeplex/values.local.yaml.example` in the repo.
