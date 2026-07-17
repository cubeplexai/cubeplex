# cubeplex on docker-compose — Install Guide

`docker compose up -d` deploys cubeplex (backend + frontend + Postgres +
Redis + rustfs S3 store) on one host. Same container images as the
kubernetes mode; only the orchestration differs.

---

## Contents

1. [Prerequisites](#1-prerequisites)
2. [Architecture](#2-architecture)
3. [Build images](#3-build-images)
4. [Configure (`.env` + two YAML files)](#4-configure-env--two-yaml-files)
5. [Up / down / logs](#5-up--down--logs)
6. [Verification](#6-verification)
7. [Troubleshooting](#7-troubleshooting)

---

## 1. Prerequisites

| Item | Requirement |
|---|---|
| Linux host with docker engine | ≥ 24, with `docker compose` v2 |
| Outbound network to the image registry | the same one `build-and-push.sh` pushed to |
| LLM provider credentials | at least one (see §4.4) |
| Open ports on the host | one for frontend (default 3000), optionally one for backend (default 8000) |

No Kubernetes, no Helm.

---

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

All inter-service communication uses docker DNS (e.g. backend reaches
postgres at `postgres:5432`). The host only sees the frontend port (and
optionally the backend port for direct API access).

---

## 3. Build images

Use the kubernetes mode's build script — the images are identical:

```bash
deploy/kubernetes/scripts/build-and-push.sh
# pushes to ${REGISTRY:-192.168.1.101:8050}/${REPO:-library}/cubeplex-{backend,frontend}:<git-sha>
```

Then in `.env` set `BACKEND_TAG` and `FRONTEND_TAG` to that sha.

---

## 4. Configure (`.env` + two YAML files)

Three files, all gitignored:

| File | What it does |
|---|---|
| `.env` | image tags, host port mappings, infra passwords. Read directly by `docker compose` for variable substitution in `compose.yaml`. |
| `config/config.production.local.yaml` | Non-secret runtime config (mode, public_url, cookie_secure, sandbox toggle). Mounted into backend. |
| `config/config.production.secrets.yaml` | Secrets — jwt/csrf/vault material, infra passwords (must match `.env`), LLM provider api_keys. Mounted into backend. |

### 4.1 `.env`

```bash
cp .env.example .env
$EDITOR .env
```

Required:

```dotenv
IMAGE_REGISTRY=192.168.1.101:8050
IMAGE_REPO=library
BACKEND_TAG=<git-sha>
FRONTEND_TAG=<git-sha>

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
| `api.public_url` | `http://localhost:8000` | the URL clients reach the backend at; if you put a reverse proxy in front, use **that** URL |
| `public_base_url` | same | used by the backend for absolute URL construction |
| `frontend_base_url` | `http://localhost:3000` | where the backend redirects browsers |
| `deployment.mode` | `single_tenant` | `single_tenant` auto-creates an org on first user registration; `multi_tenant` requires explicit org bootstrap |
| `auth.cookie_secure` | `false` | ★ must stay `false` on HTTP, otherwise clients silently drop the auth cookie |
| `sandbox.enabled` | `false` | flip to `true` and fill `sandbox.{domain,image,api_key}` in `secrets.yaml` to use an external OpenSandbox |

`database.host`, `redis.url`, `objectstore.endpoint` use docker DNS names
(`postgres`, `redis`, `rustfs`) — don't change unless you renamed the
services.

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

The three secrets in `auth`:

| Field | Generate with |
|---|---|
| `jwt_secret` | `openssl rand -hex 32` |
| `csrf_secret` | `openssl rand -hex 32` |
| `vault_key` | `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` |

### 4.4 LLM providers

In `config.production.secrets.yaml`:

```yaml
production:
  llm:
    default_model: "deepseek/deepseek-v4-flash"
    fallback_models:
      - "cubeplex/qwen3.5-plus-thinking"
    providers:
      # Mode A — cubepi built-in preset (simplest)
      deepseek:
        preset: "deepseek/cn/anthropic-messages"
        api_key: "sk-..."

      # Mode B — fully custom (private gateway, self-host)
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
```

Format rules and available `preset` names match the kubernetes mode —
see [deploy/kubernetes/INSTALL.md §4.4](../kubernetes/INSTALL.md).

---

## 5. Up / down / logs

```bash
# bring up (also pulls the latest tags)
deploy/docker-compose/scripts/up.sh

# tail logs
docker compose -f deploy/docker-compose/compose.yaml logs -f backend

# stop and remove containers (volumes preserved)
docker compose -f deploy/docker-compose/compose.yaml down

# stop and DELETE volumes (postgres data, rustfs data, redis data — destructive)
docker compose -f deploy/docker-compose/compose.yaml down -v
```

`up.sh` refuses to start if `.env` or either YAML file is missing.

---

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

Both scripts default to `localhost`; override with `HOST`,
`BACKEND_PORT`, `FRONTEND_PORT` to run against a remote host.

---

## 7. Troubleshooting

### Backend keeps restarting

```bash
docker compose -f deploy/docker-compose/compose.yaml logs backend --tail=50
```

| Symptom | Fix |
|---|---|
| `CUBEPLEX_AUTH__VAULT_KEY is required` | add `auth.vault_key` in `secrets.yaml` |
| `connection refused on postgres:5432` | postgres still starting; should self-heal — check `docker compose ps` |
| `Provider 'X' not found` | `default_model: "X/..."` references a provider not in `providers` |

### Login cookie missing on HTTP

`config.production.local.yaml`'s `auth.cookie_secure` must be `false` —
otherwise the browser / curl silently drops the auth cookie because the
connection is plain HTTP.

### Frontend → backend fails (CORS / 502)

`compose.yaml` sets `CUBEPLEX_API_URL=http://backend:8000` on the frontend
container, so Next.js proxies `/api/*` server-side over the docker
network. If you changed service names, fix that env too.

### Image pull denied

If your registry is private:

```bash
docker login ${IMAGE_REGISTRY}
```

The compose stack inherits the daemon's credentials.

### Stuck `bucket-init`

```bash
docker compose -f deploy/docker-compose/compose.yaml logs bucket-init
```

If rustfs isn't reachable, check the rustfs container's healthcheck;
rustfs publishes a console on `:9001` you can hit locally to confirm it's up.
