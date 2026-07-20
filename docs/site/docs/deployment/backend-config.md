---
sidebar_position: 4
title: Backend configuration
---

# Backend configuration reference

The backend is configured with [dynaconf](https://www.dynaconf.com/): a stack
of YAML files plus environment variables, merged in a fixed order. This page
is the full field reference. The [Docker Compose](./docker-compose.md) and
[Kubernetes](./kubernetes.md) guides only cover the handful of keys you must
set to get running and link back here for everything else.

## How configuration is layered

The active environment is chosen by `ENV_FOR_DYNACONF` (the deployment images
set it to `production`). For that environment, dynaconf loads and **deep-merges**
the following, in order — later sources win:

| Order | Source | Committed? | What goes here |
|---|---|---|---|
| 1 | `config.yaml` (`default:` block) | yes | Base defaults for every key. Don't edit. |
| 2 | `config.production.yaml` (`production:` block) | yes | Production-specific defaults (e.g. `cookie_secure: true`). Don't edit. |
| 3 | `config.production.local.yaml` | no (gitignored) | **Your non-secret overrides** — URLs, mode, tuning. |
| 4 | `config.production.secrets.yaml` | no (gitignored) | **Your secrets** — passwords, API keys, JWT/CSRF/vault material. |
| 5 | Environment variables (`CUBEPLEX_…`) | — | Highest precedence; override any key. |

You only ever author layers 3–5. The split between `local` and `secrets` is
purely organizational (safe-to-see vs sensitive) — dynaconf merges them the
same way.

:::note Env-section wrapper
The two operator files are keyed by the environment name, and set
`dynaconf_merge: true` so their values merge onto (rather than replace) the
defaults:

```yaml
dynaconf_merge: true
production:
  api:
    public_url: "https://cubeplex.example.com"
  auth:
    cookie_secure: true
```
:::

### How each deployment mode surfaces these

- **Docker Compose** mounts `config.production.local.yaml` and
  `config.production.secrets.yaml` straight into the backend container. You
  edit the files directly (see the [Compose guide](./docker-compose.md#4-configure-env--two-yaml-files)).
- **Kubernetes** renders them for you: `backend.configOverrides` in
  `values.local.yaml` becomes the local (ConfigMap) file, and
  `backend.secrets` becomes the secrets (Secret) file. You never write the YAML
  by hand — see the [Kubernetes guide](./kubernetes.md#42-backend-non-secret-config).

## Environment variables

Any key is overridable by an environment variable: prefix `CUBEPLEX_`, and join
nesting levels with a **double underscore** `__`.

| Config key | Environment variable |
|---|---|
| `auth.jwt_secret` | `CUBEPLEX_AUTH__JWT_SECRET` |
| `auth.csrf_secret` | `CUBEPLEX_AUTH__CSRF_SECRET` |
| `redis.url` | `CUBEPLEX_REDIS__URL` |
| `sandbox.domain` | `CUBEPLEX_SANDBOX__DOMAIN` |
| `parsers.docling_serve.base_url` | `CUBEPLEX_PARSERS__DOCLING_SERVE__BASE_URL` |
| `social_login.google.client_id` | `CUBEPLEX_SOCIAL_LOGIN__GOOGLE__CLIENT_ID` |

Env vars win over every file, which makes them the right place for secrets you'd
rather not write to disk.

## Required in production

The install fails fast if these are empty — set them before first boot:

| Key | Purpose |
|---|---|
| `auth.jwt_secret` | Signs session JWTs. `openssl rand -hex 32`. |
| `auth.csrf_secret` | CSRF double-submit cookie. `openssl rand -hex 32`. |
| `auth.vault_key` | Fernet key encrypting the MCP / credentials vault. |
| `database.password` | Postgres password (match your infra). |
| `redis.url` | Includes the Redis password. |
| `objectstore.access_key` / `access_secret` | S3 / rustfs credentials. |
| `llm.providers.*` | At least one working provider — see [LLM provider configuration](./overview.md#llm-provider-configuration). |

`sandbox.{domain,image,api_key}` are additionally required if you enable the
sandbox (agent tool execution).

---

## Deployment & API

```yaml
deployment:
  mode: single_tenant     # single_tenant | multi_tenant
api:
  host: "0.0.0.0"
  port: 8000
  public_url: "https://cubeplex.example.com"
public_base_url: "https://cubeplex.example.com"
frontend_base_url: "https://cubeplex.example.com"
```

| Key | Default | Notes |
|---|---|---|
| `deployment.mode` | `single_tenant` | `single_tenant` auto-creates one org on first registration (OSS). `multi_tenant` mints a per-user org (cloud). |
| `api.host` / `api.port` | `0.0.0.0` / `8000` | Bind address inside the container. |
| `api.public_url` | `""` | The URL clients reach the backend at. Behind a reverse proxy, use the **proxy's** URL. |
| `public_base_url` | `http://localhost:8000` | Used to mint absolute URLs (OAuth redirects, etc.). |
| `frontend_base_url` | `http://localhost:3000` | Where the backend redirects browsers. |

## Auth & sessions

```yaml
auth:
  jwt_secret: "…"          # required
  csrf_secret: "…"         # required
  vault_key: "…"           # required (Fernet key)
  cookie_secure: true      # MUST be false on plain HTTP
  jwt_lifetime_seconds: 86400
  cookie_samesite: "lax"
  password_policy: "high"  # high | low
  rate_limit:
    login_per_minute: 5
    register_per_minute: 3
  email_verification:
    enabled: "auto"        # auto | true | false (auto = on iff email.backend == smtp)
    code_length: 6
    code_ttl_seconds: 600
    max_attempts: 5
```

| Key | Default | Notes |
|---|---|---|
| `auth.cookie_secure` | `true` (prod) | **Set `false` on plain HTTP**, or browsers silently drop the auth cookie. |
| `auth.jwt_lifetime_seconds` | `86400` | Session lifetime (24h). |
| `auth.cookie_name` / `csrf_cookie_name` | `cubeplex_auth` / `cubeplex_csrf` | Cookie names. |
| `auth.password_policy` | `high` | `high` enforces stronger passwords; `low` relaxes it. |
| `auth.rate_limit.*` | 5 / 3 per min | Login / register throttles. |
| `auth.email_verification.enabled` | `auto` | OTP email verification; `auto` turns on only when SMTP email is configured. |

## LLM providers

The full field reference — providers, presets, `default_model` /
`fallback_models` — lives in
[LLM provider configuration](./overview.md#llm-provider-configuration). Two
extras defined at the config layer:

```yaml
llm:
  model_presets:
    tiers:
      lite: { enabled: true, primary: "provider/model-id", fallbacks: [] }
      pro:  { enabled: true, primary: "provider/model-id", fallbacks: ["provider/backup"] }
    default_preset: pro
```

`model_presets` seeds the tiered lite/flash/pro/max presets into the system
org's settings (what users pick from in the model selector); each tier is a
primary model ref plus ordered fallbacks. `default_preset` is the tier applied
when none is chosen.

## Database, Redis & object store

```yaml
database:
  host: "postgres"        # Docker/K8s service name
  port: 5432
  user: "cubeplex"
  name: "cubeplex"
  password: "…"           # required
  pool_size: 10
  max_overflow: 20
redis:
  url: "redis://:<password>@redis:6379/0"   # required
  key_prefix: "cubeplex"
objectstore:
  provider: "s3"          # s3 | oss
  endpoint: "rustfs:9000"
  bucket: "cubeplex"
  region: "us-east-2"
  access_key: "…"         # required
  access_secret: "…"      # required
```

With the bundled infra, `database.host`, `redis.url`, and
`objectstore.endpoint` point at the in-cluster service names — leave them alone
unless you renamed the services or use external backends. Postgres must be the
`pgroonga + pgvector` image (conversation search runs `CREATE EXTENSION`); the
bundled charts already use it.

## Sandbox

Controls agent tool execution. See the [sandbox guide](../guides/conversations/sandboxes.md)
for the user-facing behaviour and each deployment guide for wiring it up.

```yaml
sandbox:
  enabled: true
  domain: "…"             # OpenSandbox API host:port (no scheme)
  image: "ghcr.io/cubeplexai/cubeplex-sandbox:sandbox-v0.1.0"
  api_key: "…"
  use_server_proxy: false # true when the backend can't reach sandbox pods/ports directly
  secure_access: true     # false for docker-runtime OpenSandbox
  ttl: 1800               # idle seconds before cleanup
  ready_timeout: 300      # wait for a sandbox to become ready (covers a cold pull)
  resource:
    cpu: "2"
    memory: "4Gi"
```

| Key | Default | Notes |
|---|---|---|
| `sandbox.enabled` | `true` | When off, chat works but tool calls fail. |
| `sandbox.use_server_proxy` | `true` | Set `false` for direct pod access; `true` for Docker-bridge / isolated networks. |
| `sandbox.secure_access` | `true` | Kubernetes ingress-gateway signed URLs. **Must be `false`** on docker-runtime OpenSandbox. |
| `sandbox.ttl` | `1800` | Idle sandbox is reaped after 30 min. |
| `sandbox.resource.cpu` / `memory` | `2` / `4Gi` | Per-sandbox limits. |

## Streaming

```yaml
streaming:
  run_event_ttl_seconds: 43200   # 12h — how long a run's events are replayable
  run_stream_block_ms: 5000      # SSE heartbeat cadence; must be < redis socket timeout
  run_stream_max_events: 1000000 # DoS safety cap (trimming = silent replay loss)
```

`run_event_ttl_seconds` doubles as the upper bound on how long an in-flight run
can stay active — raise it for very long agent runs.

## Conversation context compaction

```yaml
compaction:
  enabled: true
  threshold_ratio: 0.7           # compact at context_window * ratio
  keep_tail_tokens: 8000         # recent tokens kept verbatim
  summary_provider: "deepseek"
  summary_model: "deepseek-v4-flash"
  max_summary_tokens: null       # null = cubepi dynamic budget
  fallback_context_window: 128000
```

Point `summary_provider` / `summary_model` at a provider you actually have
configured under `llm.providers`.

## Conversation search

Hybrid lexical + vector search over past conversations.

```yaml
search:
  enabled: true
  lexical:
    backend: "pgroonga"          # pgroonga | pg_bigm
  embedding:
    enabled: false               # lexical-only until you turn this on
    base_url: "https://api.openai.com/v1"
    api_key: ""                  # via CUBEPLEX_SEARCH__EMBEDDING__API_KEY
    model: "text-embedding-3-small"
    vector_dim: 1024
```

Lexical search works out of the box. Vector search stays off until you set
`embedding.enabled: true` and supply an OpenAI-compatible `/v1/embeddings`
endpoint. `vector_dim` is frozen at migration time — changing it later needs a
table rebuild.

## File parsing (docling)

```yaml
parsers:
  docling_serve:
    base_url: "http://docling-serve-cpu:5001"
    api_key: ""
    timeout_sync_seconds: 30
    async_threshold_mb: 3
```

The `file_read` tool converts PDF / office documents to markdown via a
docling-serve instance. Optional — see each guide's docling section for
deploying one.

## Attachments

```yaml
attachments:
  max_file_bytes: 52428800            # 50 MiB per file
  max_per_message: 10
  max_per_conversation_bytes: 524288000  # 500 MiB
  allowed_mime_types: [ image/png, application/pdf, … ]
```

Governs uploads. `allowed_mime_types` is an allow-list (images, PDF, office
docs, text, archives by default); `thumbnail` / `view_images` control how
images are down-scaled for the model.

## Email & social login

```yaml
email:
  backend: "log"          # log | smtp
  from_address: "noreply@cubeplex.local"
  smtp_host: "…"
  smtp_port: 587
  smtp_user: "…"          # via env / secrets
  smtp_password: "…"
social_login:
  google:
    enabled: false
    client_id: "…"        # via env / secrets
    client_secret: "…"
```

`email.backend: log` just prints emails to stdout (dev). Set `smtp` and fill
credentials (via env or the secrets file) to send verification / password-reset
mail for real. Google login is off until you enable it and supply OAuth
credentials.

## Other subsystems

| Section | Key | Default | Purpose |
|---|---|---|---|
| Memory | `memory.short_term_enabled` / `long_term_enabled` | `true` / `false` | Conversation memory features. |
| MCP | `mcp.progressive_disclosure.enabled` | `auto` | Collapse deferrable tool schemas when they crowd the context. |
| MCP | `mcp.icons.fetch_remote` | `true` | Fetch remote connector icons at discovery (set `false` air-gapped). |
| Skills | `skills.preinstalled_dir` | `skills/preinstalled` | Skills seeded into the global catalog. |
| Image gen | `image_generation.enabled` | `false` | `generate_image` tool; needs `api_key`. |
| Tracing | `tracing.enabled` | `false` | Write cubepi agent-run spans to disk / OTLP. |
| Logging | `logging.access_log` | `true` | One log line per HTTP request. |
| Logging | `logging.third_party_level` | `WARNING` | Caps noisy third-party loggers. |
| Lifecycle | `lifecycle.graceful_drain_timeout_seconds` | `3600` | Max drain time on shutdown. |
| Egress | `egress_exchange.listener.enabled` | `false` | mTLS secret-injection listener (enabled by the egress bundle). |

## Next steps

- [Docker Compose install guide](./docker-compose.md)
- [Kubernetes install guide](./kubernetes.md)
- [LLM provider configuration](./overview.md#llm-provider-configuration)
