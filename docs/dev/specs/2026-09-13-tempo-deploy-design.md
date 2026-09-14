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

### 3. Bundle Tempo only, OTLP straight to Tempo, auto-wire backend
**(selected)**

Ship a single-binary Tempo with a local filesystem backend. Backend writes
OTLP HTTP to Tempo `:4318` and queries `:3200`. No otel-collector (Tempo
already receives OTLP). No Grafana. Helm `tempo.enabled` and Compose
include the service the same way Docling is optional infra — except Tempo
defaults on because it is small and the admin viewer needs it.

## Design

### What gets deployed

One Tempo process, Grafana's official `grafana/tempo` image, **pinned to
2.8.3**.

Do not use Tempo 2.10+. Upstream 2.10 single-binary requires a
Kafka-compatible write path (Redpanda in their example). That is not
acceptable for the Compose/single-node Helm default.

Storage is Tempo's `local` backend on a PVC (Helm) / named volume
(Compose): WAL + compacted blocks under `/var/tempo`. Retention is 7 days
(`compactor.compaction.block_retention: 168h`), matching the 168h search
window the admin viewer already assumes.

No memcached, no metrics-generator, no Tempo multitenancy. CubePlex
already isolates orgs in TraceQL (`span.cubeloop.metadata.org_id`); Tempo
stays a single tenant.

### Network and security

Tempo is ClusterIP-only (Helm) and is not published on the host (Compose)
unless the operator opts in with an extra port mapping. It is **never**
added to the Ingress. Tempo's query API has no auth; exposing `:3200`
would leak every span, including `record_content` payloads if someone
turns that on.

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
  image: grafana/tempo:2.8.3
  retention: 168h
  recordContent: false          # tracing.record_content injected into backend
  persistence:
    storageClass: cubeplex-work-hostpath
    size: 10Gi
  resources:
    requests: { cpu: "100m", memory: "256Mi" }
    limits:   { cpu: "1",    memory: "1Gi" }
```

When `tempo.enabled` is true the chart:

1. Renders `templates/infra-tempo.yaml`: ConfigMap (Tempo's own config),
   Service (ClusterIP), StatefulSet (PVC via `volumeClaimTemplates`).
   `fsGroup: 10001` so the distroless Tempo user can write the PVC.
2. Injects into the backend ConfigMap, **replacing** any
   `backend.configOverrides.tracing` so the file cannot contain duplicate
   YAML keys:

```yaml
tracing:
  enabled: true
  record_content: <tempo.recordContent>
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

Tempo is a first-class service in `compose.yaml`, not a Docling-style
overlay. It is one small container; hiding it behind a second compose file
is how the current gap happened.

- Service name `tempo`, volume `tempo-data` at `/var/tempo`.
- Config file bind-mounted from `deploy/docker-compose/config/tempo.yaml`
  (checked in, not an operator secret).
- No healthcheck: the image is distroless and has no `wget`/`curl`.
- Backend env (dynaconf prefix wins over the mounted YAML):

```
CUBEPLEX_TRACING__ENABLED=true
CUBEPLEX_TRACING__RECORD_CONTENT=false
CUBEPLEX_TRACING__OTLP__ENDPOINT=http://tempo:4318/v1/traces
CUBEPLEX_TRACING__TEMPO__QUERY_ENDPOINT=http://tempo:3200
```

To disable: remove/comment the `tempo` service **and** those four env
vars, then either leave tracing off or set BYO endpoints in
`config.production.local.yaml`.

Optional `TEMPO_HTTP_PORT` in `.env` publishes `:3200` on the host for
debugging. Default is unpublished.

### Tempo config (shared content, two mounts)

Helm ConfigMap and Compose file carry the same single-binary config:

```yaml
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
ingester:
  max_block_duration: 5m
compactor:
  compaction:
    block_retention: 168h
storage:
  trace:
    backend: local
    wal:
      path: /var/tempo/wal
    local:
      path: /var/tempo/blocks
```

Command: `/tempo -config.file=/etc/tempo.yaml`.

### Application code

No backend/frontend code changes. The write path, query client, and admin
pages already exist. This spec only feeds them endpoints.

`tracing.enabled` stays `false` in `config.yaml` / `config.production.yaml`.
Deploy wiring turns it on. Local-dev keeps its gitignored
`config.development.local.yaml` pointer at whatever Tempo the developer
already runs.

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
- Tempo 2.10+ / Kafka WAL.
- Auth on Tempo itself (network isolation is the control).
- Cross-org / system-admin Tempo views.
- EE/OSS split of the viewer (already decided elsewhere).

## Success criteria

- `helm template` with default values emits a Tempo StatefulSet + Service
  (ClusterIP, no Ingress path) and a backend ConfigMap whose `tracing.otlp.endpoint`
  and `tracing.tempo.query_endpoint` point at that Service.
- `helm template` with `tempo.enabled: false` emits neither Tempo resources
  nor an injected `tracing:` block.
- `docker compose config` includes service `tempo` and the four
  `CUBEPLEX_TRACING__*` env vars on `backend`.
- After a real Helm or Compose boot: Tempo `/ready` is 200, backend log
  contains `Tracing OTLP exporter enabled`, and `GET /api/v1/admin/traces`
  as an org-admin is not 503-for-unconfigured (auth 401 without a session
  is fine).
- Tempo is unreachable from the Ingress host.
