# Tempo in Helm and Compose

**Status:** Draft · 2026-09-13
**Scope:** Bundle Grafana Tempo with CubePlex deployments and auto-wire the
existing OTLP write path + admin trace viewer query path. No new product
UI, no metrics, no Grafana, no collector.

**Related:** `docs/dev/specs/2026-06-11-admin-trace-viewer-design.md` (the
viewer already reads Tempo; this spec is the missing deploy half).

---

## Goal

A default Helm or Compose install runs Tempo next to the backend, ships
cubeloop spans to it, and serves `/admin/traces` from it — without the
operator standing up a separate tracing stack.

## Context

The application already knows how to talk to Tempo:

- Write: `tracing.otlp.endpoint` → OTLP HTTP `/v1/traces`
  (`backend/cubeplex/agents/tracing.py`).
- Read: `tracing.tempo.query_endpoint` → Tempo HTTP `:3200`
  (`backend/cubeplex/services/tempo_client.py`, admin `/api/v1/admin/traces`).

Production defaults leave both null. Helm and Compose bundle Postgres,
Redis, rustfs, and OpenSandbox, but not Tempo. Local-dev works only because
`config.development.local.yaml` points at an external Tempo from the e2b
infra stack. A self-hosted install therefore boots with tracing off and
the admin viewer returning 503.

This is agent-run tracing (LLM / tool / turn spans), not HTTP or cluster
metrics. Metrics stay a follow-up spec.

## Approaches considered

### 1. Document BYO Tempo only

Operator deploys Tempo themselves and fills `tracing.otlp.endpoint` /
`tracing.tempo.query_endpoint`. Zero chart surface.

Rejected: the viewer was built on the assumption that CubePlex already
exports to Tempo. Asking every self-hosted install to invent that stack
leaves the feature dark.

### 2. Bundle Grafana LGTM (Loki + Grafana + Tempo + Mimir)

Full ops console in the chart.

Rejected: heavy, duplicates the admin trace viewer, and pulls metrics/logs
into this change. Grafana is useful later; it is not required to close the
existing hole.

### 3. Bundle Tempo 2.8 / 2.10

2.8.x is EOL (patch window ended 2026-03-10). 2.10.x is the last 2.x
line and is still maintained, but 2.x → 3.x is a one-way architecture
cutover (ingesters/compactor gone, no downgrade). CubePlex has no
bundled Tempo in production yet, so shipping 2.x means every install
pays that migration later.

Rejected.

### 4. Bundle Tempo 3.x monolithic, OTLP straight to Tempo, auto-wire backend
**(selected)**

Ship Grafana Tempo **3.0.3** as a single process (`-target=all`) with a
local filesystem backend. Backend writes OTLP HTTP to Tempo `:4318` and
queries `:3200`. No otel-collector. No Grafana. No Kafka.

Tempo 3.0 Kafka is **microservices-only**. Monolithic mode pushes spans
in-process to the live-store; Grafana's own single-binary compose example
runs `grafana/tempo:3.0.0` with `-target=all` and a local backend.

Helm `tempo.enabled` and Compose include the service the same way Docling
is optional infra — except Tempo defaults on because it is small and the
admin viewer needs it.

## Design

### What gets deployed

One Tempo process, official `grafana/tempo:3.0.3`. Command:

```
-target=all -config.file=/etc/tempo.yaml
```

Storage is Tempo's `local` backend on a PVC (Helm) / named volume
(Compose) under `/var/tempo` (WAL, blocks, and the 3.x live-store).
Retention is 7 days via the 3.x per-tenant override
(`overrides.defaults.compaction.block_retention: 168h`), matching the
168h search window the admin viewer already assumes. Do **not** ship a
top-level `compactor:` or `ingester:` block — 3.0 refuses to start with
them (`field compactor not found`).

No memcached, no metrics-generator, no Kafka, no Tempo multitenancy.
CubePlex already isolates orgs in TraceQL
(`span.cubeloop.metadata.org_id`); Tempo stays a single tenant.

3.0 defaults `query_frontend.query_end_cutoff` to 30s so search skips
the very newest traces (avoids incomplete live-store results). Admin
traces are used on a run that just finished, so we set
`query_end_cutoff: 0s` and `live_store.fail_on_high_lag: false`.

### Network and security

Tempo's query API has no auth. Org isolation lives only in
`admin_traces` / `TempoClient` (TraceQL `org_id` predicate). ClusterIP
and "unpublished ports" stop *the internet*, not other processes on the
same Docker network or in the same Kubernetes namespace. A sandbox,
frontend, or debug pod that can reach `:3200` bypasses `require_org_admin`.

Controls, all required:

1. **Never on Ingress.** No Tempo path in `ingress.yaml`.
2. **Helm NetworkPolicy** (default on): Ingress to Tempo ports 3200 / 4318
   / 4317 only from pods labeled `app.kubernetes.io/component: backend`.
   `tempo.networkPolicy.enabled: false` is the escape hatch for CNIs that
   also drop kubelet `/ready` probes (those come from the node IP, not a
   pod). If probes fail after install, turn the policy off rather than
   opening Tempo to the namespace.
3. **Compose internal network** named `tracing` (`internal: true`). Only
   `backend` and `tempo` join it. Tempo is not on the default compose
   network, so frontend / postgres / opensandbox cannot dial `:3200`.
4. **No host ports in the default Compose file.** A debug-only overlay
   `compose.tempo.publish.yaml` may publish `127.0.0.1:3200:3200`. Do not
   use `${TEMPO_HTTP_PORT}` interpolation on a `ports:` list in the base
   file — Compose cannot omit a list item, and an empty expansion publishes
   a random host port.

Ports on the Tempo service:

| Port | Protocol | Who talks to it |
|---|---|---|
| 3200 | HTTP query /ready | backend `TempoClient`; kubelet probes |
| 4318 | OTLP HTTP | backend OTLP exporter |
| 4317 | OTLP gRPC | unused by CubePlex; open for a BYO collector |

### Helm

New `tempo:` block in `values.yaml`, same shape as `docling:`:

```yaml
tempo:
  enabled: true
  image: grafana/tempo:3.0.3
  retention: 168h
  recordContent: false          # tracing.record_content injected into backend
  networkPolicy:
    enabled: true               # backend-only Ingress; off if kubelet probes die
  persistence:
    storageClass: cubeplex-work-hostpath
    size: 10Gi
  resources:
    requests: { cpu: "100m", memory: "256Mi" }
    limits:   { cpu: "1",    memory: "1Gi" }
```

When `tempo.enabled` is true the chart:

1. Renders `templates/infra-tempo.yaml`: ConfigMap (Tempo's own config),
   Service (ClusterIP), StatefulSet (PVC via `volumeClaimTemplates`),
   and the NetworkPolicy above. `fsGroup: 10001` so the distroless Tempo
   user can write the PVC.
2. Injects into the backend ConfigMap, **replacing** any
   `backend.configOverrides.tracing` so the file cannot contain duplicate
   YAML keys:

```yaml
tracing:
  enabled: true
  record_content: <tempo.recordContent>
  jsonl:
    enabled: false
  otlp:
    endpoint: "http://<release>-tempo:4318/v1/traces"
  tempo:
    query_endpoint: "http://<release>-tempo:3200"
```

Probes are kubelet `httpGet /ready` on 3200. Distroless: no shell
exec probes.

Backend does **not** wait for Tempo before becoming Ready. Tracing is
best-effort (`tracing.py` already swallows exporter faults). Spans during
Tempo's first seconds may drop; that is accepted.

When `tempo.enabled` is false: no Tempo resources, no injected `tracing:`
block. Operator points at an external Tempo with
`backend.configOverrides.tracing` (same BYO pattern as Docling).

### Compose

Default install uses two files, matching how `up.sh` already wraps compose:

```
docker compose -f compose.yaml -f compose.tempo.yaml up -d
```

`compose.tempo.yaml` is the overlay that adds:

- service `tempo` (image `grafana/tempo:3.0.3`, volume `tempo-data` at
  `/var/tempo`, config bind-mounted from `config/tempo.yaml`)
- network `tracing` (`internal: true`); `tempo` is **only** on `tracing`;
  `backend` joins `default` + `tracing`
- backend env (dynaconf prefix wins over the mounted YAML):

```
CUBEPLEX_TRACING__ENABLED=true
CUBEPLEX_TRACING__RECORD_CONTENT=false
CUBEPLEX_TRACING__JSONL__ENABLED=false
CUBEPLEX_TRACING__OTLP__ENDPOINT=http://tempo:4318/v1/traces
CUBEPLEX_TRACING__TEMPO__QUERY_ENDPOINT=http://tempo:3200
```

`scripts/up.sh` includes `-f compose.tempo.yaml` unless `.env` has
`TEMPO_ENABLED=false`. Docs show the two-file command as the default
`docker compose up`. Disable is one switch: omit the overlay (or set
`TEMPO_ENABLED=false`). That drops both the Tempo container and the
`CUBEPLEX_TRACING__*` env, so an operator's `tracing.enabled: false` in
local yaml is not overridden.

No healthcheck (distroless, no `wget`/`curl`). No `ports:` on Tempo in
`compose.yaml` or `compose.tempo.yaml`.

Optional `compose.tempo.publish.yaml` publishes
`127.0.0.1:3200:3200` for local debugging. Never interpolate an optional
host port onto a `ports:` list in the default files.

### Tempo config (shared content, two mounts)

Helm ConfigMap and Compose file carry the same single-binary config:

```yaml
stream_over_http_enabled: true
server:
  http_listen_port: 3200
distributor:
  receivers:
    otlp:
      protocols:
        http:
          endpoint: 0.0.0.0:4318
        grpc:
          endpoint: 0.0.0.0:4317
query_frontend:
  query_end_cutoff: 0s
live_store:
  fail_on_high_lag: false
storage:
  trace:
    backend: local
    wal:
      path: /var/tempo/wal
    local:
      path: /var/tempo/blocks
overrides:
  defaults:
    compaction:
      block_retention: 168h
usage_report:
  reporting_enabled: false
```

Command: `/tempo -target=all -config.file=/etc/tempo.yaml`.

Do not copy `ingest`, `block_builder`, or Kafka settings from
microservices examples — they do not apply to `-target=all`.

### Application code

One small backend change. `build_tracer()` today always constructs a
`JsonlSpanExporter` writing `./cubeloop-traces`. Turning tracing on in
Helm/Compose without a mounted, rotated volume fills the backend
container's writable layer (and Kubernetes node ephemeral disk). Tempo's
7-day retention does not delete those files.

Add `tracing.jsonl.enabled` (default **true**, so local-dev JSONL stays).
When false, skip the JSONL exporter. If tracing is enabled but neither
JSONL nor OTLP is configured, `build_tracer()` returns `None`.

Helm/Compose set `jsonl.enabled: false` and OTLP on. Frontend unchanged.
Admin routes unchanged.

`tracing.enabled` stays `false` in `config.yaml` / `config.production.yaml`.
Deploy wiring turns it on. Local-dev keeps JSONL (and its gitignored
OTLP pointer) as today.

### Docs (implementation PR, not this spec PR)

Same-PR updates, English + `zh-Hans`:

- `docs/site/docs/deployment/kubernetes.md` — new optional-infra section
  (Tempo, default on), architecture diagram, values reference.
- `docs/site/docs/deployment/docker-compose.md` — architecture + how to
  disable / BYO.
- `docs/site/docs/deployment/backend-config.md` — tracing block notes that
  Helm/Compose fill the endpoints when Tempo is bundled.
- `docs/site/docs/deployment/overview.md` — "infra included" table.
- Pointers in `deploy/README.md` and the two `INSTALL.md` stubs.

State plainly: Tempo's query port is internal; `record_content` is off;
retention is 7 days; admin traces 503 means Tempo is disabled or
`query_endpoint` is still null.

## Out of scope

- Prometheus `/metrics`, Grafana, Loki, otel-collector.
- FastAPI request spans.
- Changing cubeloop tracer construction (JSONL-on-disk stays).
- Tempo microservices mode / Kafka / object-store backends.
- Auth on Tempo itself (network isolation is the control).
- Cross-org / system-admin Tempo views.
- EE/OSS split of the viewer (already decided elsewhere).

## Success criteria

- `helm template` with default values emits a Tempo StatefulSet + Service
  (ClusterIP, no Ingress path) and a backend ConfigMap whose `tracing.otlp.endpoint`
  and `tracing.tempo.query_endpoint` point at that Service.
- `helm template` with `tempo.enabled: false` emits neither Tempo resources
  nor an injected `tracing:` block.
- `docker compose -f compose.yaml -f compose.tempo.yaml config` includes
  service `tempo` on the internal `tracing` network only, no host `ports:`,
  and `CUBEPLEX_TRACING__*` (including `JSONL__ENABLED=false`) on `backend`.
- `docker compose -f compose.yaml config` has no `tempo` service and no
  `CUBEPLEX_TRACING__*` env.
- After a real Helm or Compose boot: Tempo `/ready` is 200, backend log
  contains `Tracing OTLP exporter enabled`, and `GET /api/v1/admin/traces`
  as an org-admin is not 503-for-unconfigured (auth 401 without a session
  is fine).
- Tempo is unreachable from the Ingress host. Helm template emits a
  NetworkPolicy that does not allow the frontend component to `:3200`.
