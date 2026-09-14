# Tempo in Helm and Compose — Plan

Spec: [docs/dev/specs/2026-09-13-tempo-deploy-design.md](../specs/2026-09-13-tempo-deploy-design.md)

**Goal:** Default Helm and Compose installs run Grafana Tempo 3.0.3
(monolithic `-target=all`, no Kafka) and auto-wire the existing cubeloop
OTLP write path and admin trace viewer query path.

**Architecture:** One Tempo process with a local filesystem backend.
The backend talks OTLP HTTP to `:4318` and TraceQL to `:3200`. The chart
injects those URLs into the backend ConfigMap when `tempo.enabled` (default
true); Compose does the same from `compose.tempo.yaml`. Tempo is not on
Ingress. Helm NetworkPolicy + a Compose internal network keep `:3200`
reachable only from the backend. JSONL on-disk export is off in deploy.

**Tech stack:** Helm templates (same `infra-*.yaml` pattern as Redis /
Docling), Docker Compose overlay, Grafana Tempo 3.0.3 distroless image,
one backend change (`tracing.jsonl.enabled`).

---

## Unit 0 — JSONL exporter is optional

**Files:**

- `backend/config.yaml` — `tracing.jsonl.enabled: true` (dev/local JSONL
  stays).
- `backend/cubeplex/agents/tracing.py` — append `JsonlSpanExporter` only
  when that flag is true. If tracing is enabled but both JSONL and OTLP
  are off, return `None`.
- `backend/tests/unit/test_tracing_build_tracer.py` (new or extend
  existing) — JSONL-only, OTLP-only, both, neither.

**Interfaces:** `config.get("tracing.jsonl.enabled", True)`.

**Core logic:** Deploy sets the flag false so default-on OTLP does not
fill `./cubeloop-traces` on the backend writable layer. Default true
keeps current local-dev behavior.

**Tests:** unit, no Tempo. Mock/omit the OTLP package as existing tests
do if any. Assert exporter list membership, not file contents.

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
  `fsGroup: 10001`, `httpGet /ready` probes on 3200, NetworkPolicy
  allowing those ports only from `app.kubernetes.io/component: backend`.
  `tempo.networkPolicy.enabled` (default true) gates the policy.
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
    contains `otlp.endpoint` with `:4318/v1/traces`, `query_endpoint`
    with `:3200`, `record_content: false`, `jsonl.enabled: false`;
    a NetworkPolicy exists whose `from` podSelector is backend-only.
  - `tempo.enabled: false`: no object with `app.kubernetes.io/component: tempo`;
    backend ConfigMap has no chart-injected `tracing:` mapping.
  - `tempo.enabled: false` plus `backend.configOverrides.tracing.tempo.query_endpoint: http://external:3200`:
    that BYO URL is present in the ConfigMap.

Placement: local pytest next to the chart (same grain as
`deploy/kubernetes/egress-bundle`). Not backend `make check-ci` — helm is
not in that path today. Run as `pytest deploy/kubernetes/charts/cubeplex/tests/test_tempo_chart.py`.

---

## Unit 2 — Compose Tempo overlay

**Files:**

- `deploy/docker-compose/config/tempo.yaml` — same single-binary config as
  the Helm ConfigMap.
- `deploy/docker-compose/compose.tempo.yaml` — overlay: `tempo` service,
  internal `tracing` network, backend env (`CUBEPLEX_TRACING__*` including
  `JSONL__ENABLED=false`), backend also on `tracing`. No `ports:`. No
  healthcheck. Image `grafana/tempo:3.0.3`.
- `deploy/docker-compose/compose.tempo.publish.yaml` — optional
  `127.0.0.1:3200:3200` only.
- `deploy/docker-compose/scripts/up.sh` — pass `-f compose.tempo.yaml`
  unless `TEMPO_ENABLED=false` in `.env`.
- `deploy/docker-compose/.env.example` — `TEMPO_ENABLED=true`.
- `deploy/docker-compose/config/config.production.local.yaml.example` —
  comment that tracing env comes from the overlay, not this file.
- Do **not** add a `tempo` service or `CUBEPLEX_TRACING__*` to
  `compose.yaml`.

**Interfaces:**

- Docker DNS on the internal network: `http://tempo:4318/v1/traces`,
  `http://tempo:3200`.
- Disable = omit the overlay. Then dynaconf env no longer forces tracing
  on, and Tempo is not running.

**Core logic:**

- Overlay, not an in-file `ports:` interpolation and not a comment-out
  recipe. `up.sh` is the default on-path so the current "forgot the
  overlay" gap does not recur for `scripts/up.sh` users. Docs for raw
  `docker compose` always show both `-f` flags.
- Frontend / opensandbox stay off `tracing`.

**Tests:**

- `deploy/docker-compose/tests/test_tempo_compose.py`. Skips if `docker`
  is not on PATH. Dummy env for required `${:?}` interpolations.
  - `-f compose.yaml -f compose.tempo.yaml config`: service `tempo`,
    `tempo` networks == `[tracing]`, no host ports, backend has
    `CUBEPLEX_TRACING__OTLP__ENDPOINT` and `JSONL__ENABLED=false`.
  - `-f compose.yaml config`: no `tempo` service, no `CUBEPLEX_TRACING__*`
    on backend.
- Do not extend `scripts/smoke-test.sh`. Do not boot Tempo in CI.

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

- Say Tempo query is unauthenticated; NetworkPolicy / internal compose
  network are the controls; ClusterIP is not tenant isolation.
- Say `record_content` is false unless the operator turns it on.
- Say JSONL is off in deploy (`tracing.jsonl.enabled: false`).
- Say retention is 7 days.
- Give disable: Helm `tempo.enabled: false`; Compose omit overlay or
  `TEMPO_ENABLED=false`.
- Give BYO snippet (`tempo.enabled: false` +
  `backend.configOverrides.tracing`) matching Unit 1.

**Tests:** none code-side. Site build is the existing docs CI.

---

## Spec coverage

| Spec requirement | Unit |
|---|---|
| `tracing.jsonl.enabled`; deploy turns JSONL off | 0 |
| Helm Tempo StatefulSet + ClusterIP Service | 1 |
| Helm NetworkPolicy backend-only | 1 |
| Auto-wire backend tracing URLs when enabled | 1 |
| BYO when `tempo.enabled: false` | 1 |
| No Ingress path | 1 |
| Pin Tempo 3.0.3, local backend, 168h retention, no Kafka | 1, 2 |
| Compose overlay + internal network + env auto-wire | 2 |
| No host ports in default compose files | 2 |
| One-switch disable (`TEMPO_ENABLED=false` / omit overlay) | 2 |
| Operator docs (en + zh) | 3 |

## Not in this plan

Prometheus `/metrics` (next spec). Changing cubeloop tracer construction.
Tempo microservices / Kafka. Live cluster e2e in CI.
