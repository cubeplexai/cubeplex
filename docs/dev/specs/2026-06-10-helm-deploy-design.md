# Helm Deploy & Smoke Test on 192.168.1.101 — Design

Status: approved (clarifying questions answered 2026-06-10)
Worktree: `feat/helm-deploy`

## Goal

Make cubeplex deployable to an existing single-node kubeadm cluster via one
`helm upgrade --install`, alongside its required infrastructure (Postgres,
Redis, MinIO, OpenSandbox). Deliver the artifacts to 192.168.1.101 and run a
deployment-correctness smoke test (boot + auth + UI shell + ingress).

This spec is the deployment substrate. End-to-end chat with real LLM /
sandbox tooling is out of scope for the smoke test — those validations live
in the existing backend/frontend test suites; here we only verify the
**deployment** is correct.

## Target Environment

- Host: `root@192.168.1.101` (Ubuntu 22.04, single node)
- Cluster: existing kubeadm v1.27.9, Calico CNI, ingress-nginx installed
- Container runtime: docker 25.0.5; OpenEBS hostpath default StorageClass
- Local registry: `192.168.1.101:8050` (Harbor, `admin/Harbor12345`)
- Root partition: 51 G (already cleaned to 2.1 G free 2026-06-10); large
  data partition `/work` 1.8 T (~920 G free)
- Existing `cubechat` namespace runs an unrelated sibling product — **do
  not touch**, deploy cubeplex in its own namespace

## Decisions (from brainstorming Q&A)

| Q | Decision |
|---|---|
| Cluster | Reuse existing kubeadm; namespace `cubeplex` |
| Disk | Cleaned root partition; cubeplex PVCs use a new SC pinned to `/work/cubeplex` |
| Images | Local build → push to `192.168.1.101:8050/library/cubeplex-{backend,frontend}:<git-sha>` |
| Infra | All in chart: bitnami/postgresql + bitnami/redis + minio + opensandbox subcharts |
| LLM | Reuse current `backend/config.development.local.yaml` content → committed-but-private `values.local.yaml` (gitignored) |
| Sandbox | Bundle alibaba OpenSandbox umbrella chart (controller + server) as subchart, default on |
| Ingress | ingress-nginx, host `cubeplex.local`, user maps `/etc/hosts` locally |

## Repo Layout

```
deploy/
├── images/
│   ├── backend/Dockerfile                # uv → slim runtime
│   └── frontend/Dockerfile               # pnpm build → Next.js standalone
├── charts/
│   └── cubeplex/                          # umbrella chart
│       ├── Chart.yaml                    # deps: postgresql, redis, minio, opensandbox
│       ├── Chart.lock
│       ├── charts/                       # vendored subcharts (helm dep update)
│       ├── values.yaml                   # safe defaults, NO secrets
│       ├── values.local.yaml.example     # template — copy to values.local.yaml
│       └── templates/
│           ├── _helpers.tpl
│           ├── namespace.yaml
│           ├── storageclass.yaml         # cubeplex-work-hostpath → /work/cubeplex
│           ├── backend-configmap.yaml
│           ├── backend-secret.yaml
│           ├── backend-deployment.yaml
│           ├── backend-service.yaml
│           ├── backend-migrate-job.yaml  # alembic upgrade head as helm pre-upgrade hook
│           ├── frontend-deployment.yaml
│           ├── frontend-service.yaml
│           └── ingress.yaml              # /api → backend, / → frontend
└── scripts/
    ├── build-and-push.sh                 # docker buildx for backend + frontend, push
    ├── helm-install.sh                   # helm dep update + helm upgrade --install
    └── smoke-test.sh                     # see Smoke Test section
```

`deploy/kubernetes/charts/cubeplex/charts/opensandbox` is vendored from
`~/work/OpenSandbox/kubernetes/charts/opensandbox` at install time (it's not
on a public Helm repository).

## Backend Image

Multi-stage Python 3.12.

Builder stage:
- Base `python:3.12-slim`
- Install `uv` via `pip install uv`
- Copy `pyproject.toml`, `uv.lock`, then `uv sync --frozen --no-dev`

Runtime stage:
- Base `python:3.12-slim`
- Copy `.venv` from builder
- Copy `cubeplex/`, `alembic/`, `alembic.ini`, `main.py`, `config.yaml`,
  `config.development.yaml`, `config.production.yaml`
- **Do not** bake `config.*.local.yaml` — those are runtime-mounted from a
  Secret + ConfigMap
- Default env: `ENV=production`
- Default cmd: `python main.py`

Backend reads `config*.yaml` from CWD (`/app`). dynaconf merges in load
order: `config.yaml` → `config.production.yaml` → `config.production.local.yaml`
(if present). The chart writes a `config.production.local.yaml` from a
ConfigMap (non-secret bits) and a Secret (api_keys, jwt_secret, csrf_secret)
that are stitched together at pod start via a small init container that
`yq merge`s them, or via two separate mounts that dynaconf both loads.

Going with **two separate mounts** — simpler, no init container needed:

```
/app/config.production.local.yaml          ← ConfigMap (non-secret config)
/app/config.production.secrets.yaml        ← Secret (api_keys + jwt + csrf)
```

And update `backend/cubeplex/config.py` to also load
`config.{env}.secrets.yaml` if present.

## Frontend Image

Multi-stage Node 20.

Builder stage:
- Base `node:20-alpine`
- Install pnpm globally
- Copy frontend monorepo files; `pnpm install --frozen-lockfile`
- `pnpm --filter @cubeplex/core build`
- `pnpm --filter @cubeplex/web build` (produces `.next/standalone`)

Runtime stage:
- Base `node:20-alpine`
- Copy `.next/standalone`, `.next/static`, `public`
- Default env: `PORT=3000`, `HOSTNAME=0.0.0.0`,
  `CUBEPLEX_API_URL=http://cubeplex-backend.cubeplex.svc.cluster.local:8000`
- Default cmd: `node server.js`

Requires `next.config.ts` to set `output: 'standalone'` (conditionally, so
dev mode is unaffected — gated on `process.env.NEXT_OUTPUT === 'standalone'`).

## Chart Behaviour

Helm release name: `cubeplex`, namespace: `cubeplex`.

`values.yaml` contains only safe defaults. **All secrets, all model
provider api_keys, the sandbox image+domain+key, and the OSS endpoint+keys
live in `values.local.yaml`** (gitignored). The install script feeds both.

Subchart dependencies and the rationale for each:

- `postgresql` (bitnami) — primary DB
- `redis` (bitnami) — streaming Redis + cache
- `minio` (bitnami) — S3-compatible object store, bucket `cubeplex`
  auto-created via a one-shot Job
- `opensandbox` (local file path from `~/work/OpenSandbox/kubernetes/charts`)
  — sandbox runtime; default enabled

Each is gated by `<name>.enabled` so an operator can disable any individual
piece (e.g. point at external Postgres).

### Wiring chart values to backend env

`backend-secret.yaml` and `backend-configmap.yaml` together produce
`/app/config.production.local.yaml` and `/app/config.production.secrets.yaml`
mounts. The chart renders:

- DB host = `cubeplex-postgresql.cubeplex.svc.cluster.local`, password from
  the postgresql subchart's generated Secret
- Redis URL = `redis://:<pw>@cubeplex-redis-master.cubeplex.svc.cluster.local:6379/0`
- Object store = MinIO service + bucket + access keys
- Sandbox = OpenSandbox server service URL + image + api_key
- LLM providers = whatever the operator put in `values.local.yaml.llm`
- `auth.jwt_secret` / `auth.csrf_secret` = required in `values.local.yaml`,
  install fails fast if missing (helm `required`)

### Migration Job

`backend-migrate-job.yaml`: helm pre-install + pre-upgrade hook,
hook-delete-policy `before-hook-creation,hook-succeeded`. Runs
`alembic upgrade head` against the same DB the backend will use. Same image
as backend, just a different command.

### Ingress

ingress-nginx, host `cubeplex.local`, single Ingress resource:

```
/api/  → cubeplex-backend:8000
/      → cubeplex-frontend:3000
```

SSE-friendly annotations: `nginx.ingress.kubernetes.io/proxy-buffering: "off"`,
`proxy-read-timeout: "3600"`, large body limit for uploads.

## Smoke Test

`deploy/kubernetes/scripts/smoke-test.sh` runs from operator workstation against the
installed release. **No** LLM call, **no** sandbox spawn — verify the
deployment, not the agent runtime. Checks:

1. `kubectl -n cubeplex rollout status deploy/cubeplex-backend deploy/cubeplex-frontend`
   completes within 5 min
2. `kubectl -n cubeplex get pods -l app.kubernetes.io/name=postgresql` is
   Running and Ready
3. Migrate Job's last run is `Succeeded`
4. `curl -fsS http://cubeplex.local/api/v1/health` returns 200
5. `curl -fsS http://cubeplex.local/` returns 200 with HTML containing
   `<title>` (Next.js server rendered)
6. Register an org-admin via the operator CLI baked into the backend image:
   `kubectl exec deploy/cubeplex-backend -- python -m cubeplex.cli admin create-org ...`
   and verify the resulting org/workspace shows up via the admin API
7. Final: `kubectl -n cubeplex get pods` summary printed

Exit non-zero on any failure with the failing step's logs.

## Out of Scope (explicit)

- Helm chart for the existing `egress-bundle` MITM webhook — that's a
  separate concern, deployed against an existing OpenSandbox cluster
- Production-grade HA / TLS / cert-manager wiring
- Backups, monitoring, alerting
- Real LLM end-to-end chat verification in smoke test (covered by the
  backend test suite, not deployment smoke)
- Modifying the existing `openebs-hostpath` StorageClass — we add a new SC
  pinned to `/work/cubeplex` and leave the existing one alone

## Sequencing

1. Add Dockerfiles + chart skeleton + scripts
2. Build images locally, push to `192.168.1.101:8050`
3. Vendor OpenSandbox chart into `deploy/kubernetes/charts/cubeplex/charts/`
4. Author `values.local.yaml` from the operator's existing
   `backend/config.development.local.yaml`
5. `helm upgrade --install`
6. Smoke test
7. Open PR (cleanup any committed secrets; ensure `values.local.yaml` is
   gitignored)
