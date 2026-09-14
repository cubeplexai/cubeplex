# Tempo in Helm and Compose — Plan

Spec: [docs/dev/specs/2026-09-13-tempo-deploy-design.md](../specs/2026-09-13-tempo-deploy-design.md)

**Goal:** Default Helm and Compose installs run Grafana Tempo 3.0.3
(monolithic `-target=all`, no Kafka) and auto-wire the existing cubeloop
OTLP write path and admin trace viewer query path.

**Architecture:** One Tempo process with a local filesystem backend.
The backend talks OTLP HTTP to `:4318` and TraceQL to `:3200`. The chart
injects those URLs into the backend ConfigMap when `tempo.enabled` (default
true); Compose sets the same via `CUBEPLEX_TRACING__*` env. Tempo stays
ClusterIP / unpublished — no Ingress, no Grafana, no collector, no Kafka.

**Tech stack:** Helm templates (same `infra-*.yaml` pattern as Redis /
Docling), Docker Compose, Grafana Tempo 3.0.3 distroless image, existing
backend tracing config (no Python/TS changes).

---

## Unit 1 — Helm Tempo + backend auto-wire

**Files:**

- `deploy/kubernetes/charts/cubeplex/values.yaml` — new `tempo:` block
  (enabled true, image `grafana/tempo:3.0.3`, retention `168h`,
  `recordContent: false`, 10Gi PVC, resources as spec).
- `deploy/kubernetes/charts/cubeplex/templates/_helpers.tpl` —
  `cubeplex.tempo.host` → `{{ .Release.Name }}-tempo`.
- `deploy/kubernetes/charts/cubeplex/templates/infra-tempo.yaml` — new.
  Gated on `tempo.enabled`. ConfigMap (Tempo config from spec), ClusterIP
  Service (3200 / 4318 / 4317), StatefulSet with `volumeClaimTemplates`,
  `fsGroup: 10001`, `httpGet /ready` probes on 3200. Recreate-equivalent
  not needed: single replica StatefulSet.
- `deploy/kubernetes/charts/cubeplex/templates/backend-configmap.yaml` —
  when `tempo.enabled`, omit `tracing` from `configOverrides` and emit the
  injected `tracing:` block pointing at the in-cluster Service. When
  disabled, leave `configOverrides` untouched (BYO lives there).
- `deploy/kubernetes/charts/cubeplex/templates/NOTES.txt` — one line that
  Tempo is in-cluster and not on Ingress.
- `deploy/kubernetes/charts/cubeplex/values.local.yaml.example` — comment
  only (`tempo.enabled: false` + BYO snippet), no secrets.

**Interfaces:**

- Service DNS: `http://<release>-tempo:4318/v1/traces` (write),
  `http://<release>-tempo:3200` (query).
- Backend config keys unchanged: `tracing.enabled`,
  `tracing.record_content`, `tracing.otlp.endpoint`,
  `tracing.tempo.query_endpoint`.
- Ingress templates do not gain a Tempo path.

**Core logic:**

- Duplicate YAML keys in the backend ConfigMap are invalid. When Tempo is
  on, strip `tracing` out of `configOverrides` before `toYaml`, then write
  the chart-owned block. When Tempo is off, do not emit a chart `tracing:`
  key at all.
- Distroless image: no `command` shell, no exec probes. Args are
  `["-target=all", "-config.file=/etc/tempo.yaml"]` (ENTRYPOINT is
  `/tempo`). A 2.x-shaped config with `ingester:` / `compactor:` will
  crash-loop (`field compactor not found`); the ConfigMap must match the
  spec's 3.0 monolithic YAML.
- PVC permissions: Tempo runs as uid 10001; without `fsGroup` the WAL
  and live-store directories are not writable and the pod crash-loops.

**Tests:**

- New `deploy/kubernetes/charts/cubeplex/tests/test_tempo_chart.py`.
  Skips if `helm` is not on PATH. Feeds a stub values file whose secrets
  pass the chart `required` / placeholder checks (`jwt_secret`,
  `csrf_secret`, `vault_key`, postgres/redis/rustfs passwords, sandbox
  domain + api_key — real-looking generated strings, not `REPLACE_ME`)
  into `helm template`.
  Invariants:
  - default (`tempo.enabled` true): StatefulSet + Service named
    `<release>-tempo`; Service is ClusterIP; ports 3200 and 4318 exist;
    rendered Ingress YAML does not mention tempo; backend ConfigMap
    contains `otlp.endpoint` with `:4318/v1/traces` and `query_endpoint`
    with `:3200`; `record_content` is false.
  - `tempo.enabled: false`: no object with `app.kubernetes.io/component: tempo`;
    backend ConfigMap has no chart-injected `tracing:` mapping.
  - `tempo.enabled: false` plus `backend.configOverrides.tracing.tempo.query_endpoint: http://external:3200`:
    that BYO URL is present in the ConfigMap.

Placement: local pytest next to the chart (same grain as
`deploy/kubernetes/egress-bundle`). Not backend `make check-ci` — helm is
not in that path today. Run as `pytest deploy/kubernetes/charts/cubeplex/tests/test_tempo_chart.py`.

---

## Unit 2 — Compose Tempo + backend env

**Files:**

- `deploy/docker-compose/config/tempo.yaml` — same single-binary config as
  the Helm ConfigMap (retention 168h, local WAL/blocks).
- `deploy/docker-compose/compose.yaml` — `tempo` service (image
  `grafana/tempo:3.0.3`, volume `tempo-data`, mount config at
  `/etc/tempo.yaml`, command `-target=all -config.file=/etc/tempo.yaml`).
  No healthcheck (distroless). Do not publish 3200/4318 unless
  `TEMPO_HTTP_PORT` is set. Backend `environment` gains the four
  `CUBEPLEX_TRACING__*` vars from the spec. Backend does not
  `depends_on: tempo` as a readiness gate.
- `deploy/docker-compose/.env.example` — optional `TEMPO_HTTP_PORT` commented.
- `deploy/docker-compose/config/config.production.local.yaml.example` —
  short comment that tracing endpoints come from compose env, not this file.

**Interfaces:**

- Docker DNS: `http://tempo:4318/v1/traces`, `http://tempo:3200`.
- Dynaconf env prefix `CUBEPLEX_` overrides the mounted production YAML,
  so an operator's leftover `tracing.enabled: false` in local yaml does
  not silently disable the bundled Tempo.

**Core logic:**

- Disable path is subtractive: drop the service and the four env vars.
  There is no compose profile. Overlay files stay reserved for heavy
  optionals (Docling).
- Publishing `:3200` is opt-in because the query API is unauthenticated.

**Tests:**

- `deploy/docker-compose/tests/test_tempo_compose.py`. Skips if
  `docker` is not on PATH. Runs `docker compose -f compose.yaml config`
  with dummy env for the required `${:?}` interpolations (`BACKEND_TAG`,
  `FRONTEND_TAG`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`,
  `RUSTFS_SECRET_KEY`). Asserts the rendered YAML has service `tempo` and
  that `backend.environment` contains
  `CUBEPLEX_TRACING__OTLP__ENDPOINT=http://tempo:4318/v1/traces` and
  `CUBEPLEX_TRACING__TEMPO__QUERY_ENDPOINT=http://tempo:3200`.
- Do not extend `scripts/smoke-test.sh` — that file is a live post-up HTTP
  probe, not a config linter. Do not boot Tempo in CI.

---

## Unit 3 — Deployment docs

**Files (English + `zh-Hans` mirrors):**

- `docs/site/docs/deployment/kubernetes.md` — architecture diagram gains
  Tempo; new §4.12 "Tempo tracing (default on)"; values reference table.
- `docs/site/docs/deployment/docker-compose.md` — architecture + disable/BYO
  + unpublished ports.
- `docs/site/docs/deployment/backend-config.md` — tracing section: Helm /
  Compose fill endpoints when bundled; `query_endpoint` null still means
  admin traces 503.
- `docs/site/docs/deployment/overview.md` — infra-included table.
- `deploy/README.md`, `deploy/kubernetes/INSTALL.md`,
  `deploy/docker-compose/INSTALL.md` — one-line pointers, no second copy.

**Interfaces:** docs-site pages are the operator contract. `deploy/*.md`
stay stubs.

**Core logic:**

- Say Tempo query is internal and unauthenticated.
- Say `record_content` is false unless the operator turns it on.
- Say retention is 7 days.
- Give the BYO snippet (`tempo.enabled: false` +
  `backend.configOverrides.tracing`) matching Unit 1.

**Tests:** none code-side. Site build is the existing docs CI.

---

## Spec coverage

| Spec requirement | Unit |
|---|---|
| Helm Tempo StatefulSet + ClusterIP Service | 1 |
| Auto-wire backend tracing URLs when enabled | 1 |
| BYO when `tempo.enabled: false` | 1 |
| No Ingress path | 1 |
| Pin Tempo 3.0.3, local backend, 168h retention, no Kafka | 1, 2 |
| Compose service + env auto-wire | 2 |
| Unpublished query port by default | 2 |
| Operator docs (en + zh) | 3 |
| No app code / no Grafana / no collector / no metrics | all (omitted) |

## Not in this plan

Prometheus `/metrics` (next spec). Changing cubeloop tracer construction.
Tempo microservices / Kafka. Live cluster e2e in CI.
