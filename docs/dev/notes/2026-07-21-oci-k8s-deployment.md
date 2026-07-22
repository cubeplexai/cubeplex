# CubePlex on OCI Kubernetes — Deployment Report

**Date:** 2026-07-21 / 2026-07-22
**Task:** Deploy cubeplex v0.3.0 to OCI Kubernetes (cluster `cnflby7wbia`, tenancy region `us-phoenix-1`) and run a real-LLM e2e round-trip using the `arkplan` (Volcengine Ark Agent Plan) model.
**Status:** ✅ Deployed and fully verified — chat + tool-call (sandbox) e2e both pass against the live deployment. Virtual node pool removed from the cluster; only real managed nodes remain.

## Summary

Deployed cubeplex v0.3.0 via the `deploy/kubernetes/charts/cubeplex` Helm chart to an OCI
Container Engine for Kubernetes cluster. The cluster originally had **only virtual nodes**,
which cannot run this chart at all (no `initContainers`, no `subPath` support). Added a
managed (physical/VM) node pool, worked through three more real issues (CRI-O short-name image
resolution, a private GHCR package, and a chart-config validation gotcha), and finished with a
passing end-to-end conversation test against the live backend using the `arkplan` model.

## Environment

- **Kubernetes version:** v1.36.1
- **Cluster:** OCI Container Engine for Kubernetes, cluster OCID
  `...cnflby7wbia`, originally 3 virtual nodes only (`pool1` virtual node pool, size 3)
- **Added:** 1 managed node pool (`cubeplex-workload`), 2× `VM.Standard.E5.Flex`,
  Oracle Linux 9.7 OKE image, CRI-O runtime, `OCI_VCN_IP_NATIVE` pod networking
- **Ingress:** ingress-nginx (kept on the virtual node pool — plain Deployments work fine there)
- **Chart version:** 0.3.0, images `ghcr.io/cubeplexai/cubeplex-{backend,frontend}:v0.3.0`
- **Operator workstation:** has **no direct network route** into the OCI VCN's private
  subnet (10.0.10.0/24) — only the Kubernetes API server's public endpoint is reachable.
  All in-cluster verification went through `kubectl port-forward`.

## Issues Found (in the order hit) and Fixes

### 1. Virtual nodes don't support `initContainers` or `subPath` — BLOCKING

```
Error creating pod: [initContainers are not supported]
Error creating pod: [unsupported VolumeMount option: subPath: config]
```

OCI virtual nodes run a Kata-based runtime that rejects both features outright — not a
timing/config issue, a hard capability gap. The chart needs `initContainers` for the
`migrate` (alembic) step and `subPath` for mounting individual config files from a
ConfigMap/Secret. No workaround inside the chart is reasonable; the real fix is
infrastructure: **add a managed node pool** and keep cubeplex off the virtual nodes.

**Fix applied:**
1. `oci ce node-pool create` — added a real node pool (`cubeplex-workload`,
   `VM.Standard.E5.Flex` × 2, `OCI_VCN_IP_NATIVE` networking on the existing node subnet).
   First attempt with `VM.Standard.E3.Flex` hit `Out of host capacity` in `PHX-AD-2` — retried
   with `VM.Standard.E5.Flex`, which succeeded.
2. `kubectl taint node <virtual-node-ip> virtual-node=true:NoSchedule` on all 3 virtual
   nodes. This is a cluster-side, non-invasive fix (no chart edits): new pods without a
   matching toleration land only on the real nodes; already-running pods (ingress-nginx)
   are untouched by `NoSchedule`.

This is now documented as a **cloud-provider compatibility** callout at the top of
`docs/site/docs/deployment/kubernetes.md`, with a step-by-step "add a managed node pool"
section.

### 2. CRI-O rejects unqualified image names — BLOCKING (real nodes)

```
Failed to inspect image "": rpc error: ... short name mode is enforcing, but image name
cubeplex/postgresql-pgroonga-pgvector:18.2-... returns ambiguous list
```

Once pods scheduled onto the real (CRI-O) nodes, `postgres.image`
(`cubeplex/postgresql-pgroonga-pgvector:...`), `redis.image` (`redis:7-alpine`), and
`rustfs.mcImage` (`minio/mc:...`) — all unqualified, no registry host — failed to resolve.
CRI-O's short-name resolution policy refuses to guess the registry unless configured
otherwise, unlike Docker's implicit `docker.io/` default.

**Fix applied:** fully qualify all three in `values.local.yaml`:
```yaml
postgres: { image: "docker.io/cubeplex/postgresql-pgroonga-pgvector:18.2-pgroonga4.0.6-pgvector0.8.2" }
redis:    { image: "docker.io/library/redis:7-alpine" }
rustfs:   { mcImage: "docker.io/minio/mc:RELEASE.2025-04-08T15-39-49Z" }
```
Documented as a troubleshooting entry (applies to any CRI-O-based node pool, not just OCI).

### 3. GHCR `cubeplex-backend` / `cubeplex-frontend` packages were private

Anonymous token requests for both packages returned `UNAUTHORIZED` (not `DENIED` —
confirmed via a known-public GHCR package returning a valid token from the same network
path, and via a known-nonexistent-but-public-org repo returning `DENIED` instead of
`UNAUTHORIZED`, which is the real signal that distinguishes "private" from "doesn't
exist"). The org owner changed both packages' visibility to Public via
`github.com/orgs/cubeplexai/packages` → package → **Package settings** (a link easy to
miss at the bottom of the right sidebar) → **Danger Zone** → **Change visibility**.
Docs claim these are "public GHCR releases" (§3) — that was true in intent but the
packages had not actually been flipped to public after today's v0.3.0 release cut.
No doc fix needed here (it's a one-time repo/release-process step, not a chart/doc bug) —
flagging for the release checklist instead.

### 4. `model_presets.tiers` requires all four tier names — silent seed failure

Chat sends 500'd at runtime with:
```
no_default_preset — no preset is marked is_default; admin must configure one
```
even though the backend Pod was `Running` and had picked up the arkplan config correctly.
Root cause: `ModelPresetsConfig` Pydantic validation requires `tiers` to contain **exactly**
`lite`, `flash`, `pro`, `max` — `values.local.yaml` only defined `flash`. Validation failure
is caught and logged as a *warning* by `seed_system_providers_from_config`
(`Failed to seed system providers: ... tiers must contain exactly: lite, flash, pro, max`),
not surfaced anywhere in pod status/readiness — the app boots fine, health checks pass,
only the very first chat message reveals the problem.

**Fix applied:** define all four tiers, disabling unused ones with
`enabled: false, primary: null` instead of omitting them:
```yaml
model_presets:
  tiers:
    lite:  { enabled: true,  primary: "arkplan/doubao-seed-2.0-lite", fallbacks: [...] }
    flash: { enabled: true,  primary: "arkplan/deepseek-v4-flash",   fallbacks: [...] }
    pro:   { enabled: true,  primary: "arkplan/glm-5.2",             fallbacks: [...] }
    max:   { enabled: false, primary: null, fallbacks: [] }
  default_preset: flash
```
Also discovered the **example file itself was stale**: `values.local.yaml.example` and
`docs/site/docs/deployment/kubernetes.md` (§4.4, §8, §9, and the zh-Hans translation) all
showed a `default_model` / `fallback_models` top-level schema that no longer exists in
`backend/config.yaml` — the real schema has been `model_presets.tiers` + `default_preset`
since the preset-tiers redesign. Fixed all of these in the same pass.

## Final Working Configuration

`deploy/kubernetes/charts/cubeplex/values.local.yaml` (secrets redacted below, real values
used during the run):

```yaml
image:
  backend:  { tag: "v0.3.0" }
  frontend: { tag: "v0.3.0" }

backend:
  configOverrides:
    api: { public_url: "http://cubeplex.oci.local" }
    public_base_url: "http://cubeplex.oci.local"
    frontend_base_url: "http://cubeplex.oci.local"
    deployment: { mode: "single_tenant" }
    auth: { cookie_secure: false }
  secrets:
    auth: { jwt_secret: "...", csrf_secret: "...", vault_key: "..." }
    llm:
      model_presets:
        tiers:
          lite:  { enabled: true, primary: "arkplan/doubao-seed-2.0-lite", fallbacks: ["arkplan/doubao-seed-2.0-mini", "alicode/qwen3.6-plus"] }
          flash: { enabled: true, primary: "arkplan/deepseek-v4-flash", fallbacks: ["arkplan/minimax-m3", "alicode/qwen3.6-plus"] }
          pro:   { enabled: true, primary: "arkplan/glm-5.2", fallbacks: ["arkplan/deepseek-v4-pro", "alicode/qwen3.6-plus"] }
          max:   { enabled: false, primary: null, fallbacks: [] }
        default_preset: flash
      providers:
        arkplan: { preset: "volcengine/cn/openai-completions/agent", api_key: "ark-..." }
        alicode: { preset: "aliyun/cn/openai-completions/coding", api_key: "sk-sp-..." }
    sandbox:
      domain: "cubeplex-opensandbox-server.cubeplex.svc.cluster.local:8090"
      api_key: "..."

postgres:
  auth: { password: "..." }
  image: "docker.io/cubeplex/postgresql-pgroonga-pgvector:18.2-pgroonga4.0.6-pgvector0.8.2"
  persistence: { storageClass: "oci-bv" }

redis:
  auth: { password: "..." }
  image: "docker.io/library/redis:7-alpine"
  persistence: { storageClass: "oci-bv" }

rustfs:
  auth: { secretKey: "..." }
  image: "docker.io/rustfs/rustfs:1.0.0-beta.4"
  mcImage: "docker.io/minio/mc:RELEASE.2025-04-08T15-39-49Z"
  persistence: { storageClass: "oci-bv" }

ingress:
  enabled: true
  className: "nginx"
  host: "cubeplex.oci.local"
  tls: { enabled: false }

storageClass:
  create: false     # using OCI's own `oci-bv` CSI StorageClass instead of the
                     # chart's OpenEBS-hostpath default (OpenEBS isn't installed
                     # on this cluster and oci-bv is already there and default)

opensandbox:
  enabled: false     # chat-only; sandbox/tool-calls not exercised in this run
```

The `arkplan` provider's `api_key` and the rest of the local LLM provider config were
pulled from `backend/config.development.local.yaml` on the team's dev workstation
(192.168.1.215) per the user's instruction to use that config, not a placeholder.

## Deployment Steps (final, working sequence)

```bash
# 1. Managed node pool (see issue #1)
oci ce node-pool create --cluster-id <id> --compartment-id <id> \
  --name cubeplex-workload --kubernetes-version v1.36.1 \
  --cni-type OCI_VCN_IP_NATIVE --node-shape VM.Standard.E5.Flex \
  --node-shape-config '{"ocpus":2,"memoryInGBs":16}' \
  --node-source-details '{"sourceType":"IMAGE","imageId":"<OL9.7 OKE 1.36.1 image>","bootVolumeSizeInGBs":50}' \
  --placement-configs '[{"availabilityDomain":"psHl:PHX-AD-2","subnetId":"<node subnet>"}]' \
  --pod-subnet-ids '["<node subnet>"]' --size 2 --ssh-public-key "$(cat ~/.ssh/id_rsa.pub)"

kubectl taint node <virtual-node-1> <virtual-node-2> <virtual-node-3> virtual-node=true:NoSchedule

# 2. Ingress (stays on the virtual node pool — plain Deployment, no init containers)
helm install ingress-nginx ingress-nginx/ingress-nginx -n ingress-nginx --create-namespace \
  --set controller.service.type=NodePort

# 3. Author values.local.yaml (see above), then:
helm dependency update deploy/kubernetes/charts/cubeplex/vendor/opensandbox
helm dependency update deploy/kubernetes/charts/cubeplex
helm upgrade --install cubeplex deploy/kubernetes/charts/cubeplex \
  --namespace cubeplex --create-namespace \
  -f deploy/kubernetes/charts/cubeplex/values.yaml \
  -f deploy/kubernetes/charts/cubeplex/values.local.yaml \
  --wait --timeout 15m
```

Result: all 5 pods `Running`/`1/1` (`cubeplex-backend`, `cubeplex-frontend`,
`cubeplex-postgresql-0`, `cubeplex-redis-master-0`, `cubeplex-rustfs-0`); `helm status`
→ `deployed`; alembic migrations ran clean in the `migrate` init container; backend
`/health/live` and `/health/ready` both `200`.

## Verification — Real E2E Against the Live Deployment

Since the operator workstation cannot reach the cluster's private VCN subnet directly
(no VPN/peering — only the K8s API server's public endpoint is reachable), tested via
`kubectl port-forward` tunnels into the live Services rather than the ingress NodePort:

```bash
kubectl port-forward -n cubeplex svc/cubeplex-backend 18000:8000
```

Wrote a standalone httpx-based script mirroring the assertions in
`backend/tests/e2e/test_conversation_flow.py` (which normally run in-process against a
throwaway test DB and don't touch any deployment) but pointed at the port-forwarded
**live backend Service**, so it actually exercises the deployed instance end to end:

1. `GET /api/v1/system/info` → `single_tenant`, `v0.3.0`
2. Register + login (single-tenant auto-org) + fetch CSRF cookie
3. Create conversation
4. `POST .../messages` with `Accept: text/event-stream`, collect SSE inline
5. Assert: events non-empty, last event `type == "done"`, `text_delta` events present with
   non-empty combined text
6. `GET .../messages` → assert `>= 2` messages, first `role=user`, last `role=assistant`
7. Negative cases: nonexistent conversation → `404`; empty content → `400`

**Result: PASSED.** The `arkplan` model (`deepseek-v4-flash` via Volcengine Ark's Agent
Plan endpoint) responded to "Say the word 'hello' and nothing else." with SSE event
sequence `status × 3 → text_delta → usage → done`, assistant text `"hello"`, and the
message history correctly recorded `[user, assistant]`. Both negative-path checks passed.

This is a **real LLM round-trip through the deployed OCI cluster** — not the simple
curl-based `deploy/kubernetes/scripts/e2e.sh`, and not the in-process pytest suite (which
never touches a deployment) — but a script written in the same spirit/assertions as the
pytest e2e tests, executed against the live deployment.

## Follow-up: Enabling OpenSandbox (tool-call path)

After the chat-only e2e passed, went back and enabled `opensandbox.enabled: true` to also
verify the tool-call/sandbox path. Found and fixed three more issues getting the
**server + controller** pods running, then hit a fourth that's an **infrastructure
decision, not a config fix** — left unresolved, flagged for the user.

1. **Wrong Service DNS name/port in the docs and example** — `values.local.yaml.example`
   and INSTALL.md say the bundled subchart's sandbox domain is
   `cubeplex-opensandbox-server.cubeplex.svc.cluster.local:8090`. Rendered the chart and
   checked: the vendored `opensandbox-server` subchart hardcodes
   `fullnameOverride: "opensandbox-server"` and `namespaceOverride` defaults to
   `opensandbox-system` — **not** prefixed with the release name or deployed into the
   parent chart's namespace. Actual Service is `opensandbox-server.opensandbox-system.svc.cluster.local`,
   port **80** (not 8090). Used the corrected value; **doc fix still needed** (not yet
   applied — flagging here since it wasn't part of the original doc-fix pass).

2. **Controller image `v0.2.0` on Docker Hub doesn't match the chart's own template** —
   crashed instantly with `flag provided but not defined: -containerd-socket-path`. The
   vendored chart template (also versioned 0.2.0) unconditionally passes this flag, but
   the published `opensandbox/controller:v0.2.0` Docker Hub image predates it (confirmed
   via differing manifest digests between `v0.2.0` and `latest`). Pinned
   `opensandbox-controller.controller.image.tag: "latest"` as a workaround — not a
   permanent fix; someone should either re-tag a correct `v0.2.0` on Docker Hub or bump
   the vendored chart to match whatever `latest` actually is.

3. **Server needs its own `api_key`, separate from the backend's** — crashed with
   `server.api_key is empty in non-interactive mode`. The server chart's `configToml` is
   a raw TOML blob (not individually keyed values); had to override the whole block to
   set `[server] api_key = "..."` (same value as `backend.secrets.sandbox.api_key`, since
   that's what the backend presents when calling in). While at it, also fully-qualified
   `execd_image` and `[egress] image` in the same TOML (same CRI-O short-name issue as
   #2 in the main report — `opensandbox/execd:...` and `opensandbox/egress:...` needed
   `docker.io/` prefixes too).

4. **BLOCKING, unresolved: per-conversation sandbox pods get scheduled onto the (now
   cordoned) virtual nodes and fail at PVC provisioning** — every real chat request that
   triggers a tool call gets `Create sandbox failed: HTTP 504` after ~2 minutes. Root
   cause, confirmed via `kubectl get events -n opensandbox`:
   ```
   failed to provision volume with StorageClass "oci-bv": error generating
   accessibility requirements: error getting CSINode for selected node
   "10.0.10.141": csinode.storage.k8s.io "10.0.10.141" not found
   ```
   `oci-bv` uses `volumeBindingMode: WaitForFirstConsumer` (topology-aware): the volume
   binder picks a candidate node **before** the PVC is provisioned, then asks the CSI
   driver for that node's topology via its `CSINode` object. Virtual nodes never run the
   CSI node plugin, so no `CSINode` object exists for them — the provisioning call errors
   outright and the whole scheduling attempt for that pod aborts (it does not fall
   through to try a different, valid node in the same cycle). The BatchSandbox controller
   then just creates a **brand-new** pod (new UUID) on the next retry, which repeats the
   same failure — an infinite loop, not a one-off flake.
   - `kubectl taint node <virtual> virtual-node=true:NoSchedule` did **not** help: the
     BatchSandbox pod template ships a blanket `tolerations: [{operator: "Exists"}]`,
     which tolerates any taint.
   - `kubectl cordon <virtual-node>` did **not** help either — cordoned nodes are still
     apparently being offered as scheduling candidates during this topology-lookup phase
     (or the failure happens before cordon status is consulted; either way, observed the
     same `CSINode ... not found` error against a cordoned node on a fresh, post-cordon
     attempt).
   - Chat-only (no sandbox) continued to work perfectly regardless of this — it's fully
     isolated to the tool-call path.

   **Resolved.** Confirmed with the user, then: (1) deleted `ingress-nginx`'s pod so it
   rescheduled onto a real node (the 3 virtual nodes were already cordoned from earlier
   troubleshooting, so it landed on `10.0.10.185` automatically); (2)
   `oci ce virtual-node-pool delete --virtual-node-pool-id
   ocid1.virtualnodepool.oc1.phx.amaaaaaaf4havgaagyjjgvgxtobmjtatqsy4qeriidjp6oahbvxyhxql55ea
   --force`, waited for the work request to reach `SUCCEEDED`; (3) `kubectl get nodes`
   confirmed only the 2 real nodes remained. Re-ran the tool-call e2e test: the very next
   sandbox pod scheduled straight onto a real node, ran its `execd-installer` init
   container normally, and completed a real `ls -la /workspace` round-trip. **Sandbox
   tool-call path fully verified working** — see the "Final Verification" section below.

   One transient side-effect from the transition: a PVC from a pre-deletion failed
   attempt had a stale `VolumeAttachment` pointing at a node that no longer mattered,
   causing one `Multi-Attach error` on the very next retry — self-resolved within
   seconds once Kubernetes' attach-detach controller caught up with the topology change.
   Also saw brief `Insufficient cpu`/`Insufficient memory` scheduling warnings when
   running 2 sandbox pods concurrently on the 2× `VM.Standard.E5.Flex` (2 OCPU/16GB
   each) node pool — expected capacity pressure for this test-sized pool, not a
   correctness issue; both pods still reached `2/2 Running` once the other's resources
   freed up.

**Net status:** `opensandbox-server` and `opensandbox-controller` pods `Running`,
`GET /api/v1/system/info` reports `sandbox_enabled: true`, and — after removing the
virtual node pool — a real per-conversation sandbox container provisions successfully
and executes real shell commands (`ls -la /workspace`) with correct results streamed
back through SSE (`tool_call` → `tool_result` → `text_delta` → `done`).

### Chart change made (not just values/docs)

`deploy/kubernetes/charts/cubeplex/templates/backend-configmap.yaml` — added a
`secure_access` passthrough to the `sandbox:` block (previously only `enabled` and
`use_server_proxy` were templated), needed because `backend.configOverrides.sandbox.*`
collides with the chart's own hardcoded `sandbox:` key in the same rendered YAML file
(dynaconf's YAML parser rejects the resulting duplicate key outright). New field, wired
the same way as the two existing ones:
```yaml
secure_access: {{ dig "sandbox" "secure_access" true .Values.backend }}
```
Set via `backend.sandbox.secure_access: false` in `values.local.yaml` (sibling to
`configOverrides`/`secrets`, not nested under `configOverrides`) — needed because the
OpenSandbox `gateway`/`ingress` component (which `secureAccess=true` requires) wasn't
deployed in this run.

## Backend Bug Fix: model_presets Seed Failure Was Silently Swallowed

The `no_default_preset` failure mode above (§ "model_presets.tiers requires all four
tier names") isn't just a deployment-config gotcha — it's a backend bug. Traced it to
`backend/cubeplex/api/app.py`'s `lifespan()`:

```python
try:
    async with async_session_maker() as seed_session:
        await seed_system_providers_from_config(seed_session, _app.state.encryption_backend)
        await seed_model_presets_from_config(seed_session)   # raises pydantic.ValidationError
    logger.info("System provider seed step completed")
except Exception as e:
    logger.warning("Failed to seed system providers: {}", str(e))   # ← swallowed here
```

`seed_model_presets_from_config` calls `ModelPresetsConfig.model_validate(raw)`
directly — it always raised correctly on a partial `tiers` map. The bug was the
**caller**: it shared the same broad `try/except Exception → warning` as system-provider
seeding, which is a legitimately optional, safe-to-degrade step. Model presets are not
optional — without them, the app is healthy per every health check but cannot serve a
single chat message. Conflating the two meant a config typo produced a `WARNING` log
line and a fully healthy, silently broken deployment, with the real error surfacing only
when an end user sent a message and got a generic `no_default_preset` 500 with no link
back to the actual cause.

**Fix** (confirmed with the user before touching backend code, since this is a behavior
change beyond deploy/chart/docs): pulled `seed_model_presets_from_config` out of the
system-providers try/except into its own, unguarded step — modeled on the existing
fail-fast pattern already used elsewhere in the same `lifespan()` for other
must-not-be-wrong startup config (e.g. `_build_encryption_backend()` raises
`RuntimeError` directly if `CUBEPLEX_AUTH__VAULT_KEY` is missing, no try/except). An
invalid `model_presets` config now crashes the pod at startup (`CrashLoopBackOff`,
visible immediately in `kubectl get pods`) instead of degrading silently.

Added a regression test, `backend/tests/e2e/test_startup_model_presets_validation.py`,
modeled on the existing `test_startup_mode_consistency.py` pattern (construct the real
app, run `lifespan(app)` directly, assert it raises). Verified the fix doesn't break
existing behavior: ran `test_seeder_presets.py` (unit), `test_preset_errors_e2e.py`,
`test_group_chat.py`, and `test_startup_mode_consistency.py` — all 18 tests green,
including the two that specifically exercise `no_default_preset` at the HTTP-response
level (those engineer the "no presets" state via direct DB deletion *after* a
successful lifespan run with the test suite's always-valid `config.test.yaml`, so they
were never exercising the buggy code path in the first place and are unaffected by the
fix).

Test infra note: this machine's local Docker daemon has a broken `HTTPS_PROXY` (dead
`127.0.0.1:7892`), which blocks pulling the `cubeplex/postgresql-pgroonga-pgvector`
image needed for a proper local Postgres + pgvector/pgroonga test instance. Rather than
touch the shared build machine's system-level Docker daemon config without
authorization, ran the test suite over SSH against a throwaway Postgres/Redis pair on
the team's dev workstation (192.168.1.215), which already has the image cached from its
own `infra-postgresql` container. Copied `app.py` and the new test file over
temporarily, ran the suite, then reverted both and removed the throwaway containers —
left that machine exactly as found.

### Follow-up: relaxed "all four tiers" to "at least one tier"

The fail-fast fix above still required `tiers` to name all four tiers by key (just
made the *failure mode* for a partial map correct — crash instead of silently warn).
User asked whether that "all four" requirement itself should really be "at least one" —
checked, and it should. Traced `set(self.tiers.keys()) != set(ModelTier)` in
`ModelPresetsConfig._invariants` (`backend/cubeplex/llm/snapshot_schema.py`) back to its
one actual caller with a hard dependency: `_load_presets()` in `backend/cubeplex/llm/snapshot.py`
did `s = cfg.tiers[tier]` — an **unconditional** dict subscript for all four `ModelTier`
values, which would `KeyError` on any missing key. The "exactly four" schema constraint
existed purely to satisfy that unconditional access, not because a partial tiers map is
actually meaningless.

**Fixed both sides:**
- `ModelPresetsConfig._invariants`: `if set(self.tiers.keys()) != set(ModelTier)` →
  `if not self.tiers` (reject only a genuinely empty map).
- `_load_presets()`: `cfg.tiers[tier]` → `cfg.tiers.get(tier, TierSetting())` — a missing
  tier now resolves to the same disabled default (`enabled=False, primary=None`) that an
  explicit `{enabled: false, primary: null}` entry would.

Updated the tests that directly asserted the old "exactly four" behavior (they'd have
been silently-wrong regression gaps otherwise, still green but testing the wrong
invariant): `test_model_presets_schema.py::test_missing_tier_key_rejected` →
`test_partial_tiers_accepted` (+ new `test_empty_tiers_rejected`);
`test_admin_model_presets_schemas.py::test_admin_body_requires_all_four_tiers` →
`test_admin_body_accepts_partial_tiers` (+ new `test_admin_body_rejects_empty_tiers`,
since the admin PUT endpoint's request schema is a re-export of `ModelPresetsConfig` and
inherits the same rule automatically). Added
`test_snapshot_loader.py::test_snapshot_loads_preset_with_partial_tiers` (only `pro`
present, no `lite`/`flash`/`max` keys at all — confirms `_load_presets` no longer
`KeyError`s) and updated `test_startup_model_presets_validation.py` to test the
genuinely-still-invalid case (`tiers: {}`) plus a new positive case confirming a
single-tier config now boots cleanly. Ran the full preset/tier-related surface again on
192.168.1.215 (same throwaway-container, copy-over-revert-cleanup approach): 61/61 then
40/40 (a broader `-k 'preset or tier'` sweep across `tests/unit/llm/` and
`tests/unit/api/`) all green.

**Docs updated to match**: `values.local.yaml.example` and both `kubernetes.md`
(English + zh-Hans) now say "at least one tier, omitted tiers are just disabled" instead
of "all four tiers required, disable unused ones explicitly" — the troubleshooting
table entry changed from "chat 500s at runtime" (the old bug's symptom) to
"`CrashLoopBackOff` with `tiers must contain at least one tier`" (the new, correct,
fail-fast symptom for the one config that's genuinely still invalid: an empty map).

## Documentation Changes Made

- `docs/site/docs/deployment/kubernetes.md` (+ zh-Hans translation):
  - OCI virtual-node incompatibility warning at the top, with a cloud-provider
    compatibility matrix and step-by-step managed-node-pool instructions (§9)
  - `model_presets.tiers` documented as needing at least one tier (final state, after
    the backend fix below), with a troubleshooting entry for the `CrashLoopBackOff` /
    `tiers must contain at least one tier` failure mode
  - Fixed the stale `default_model`/`fallback_models` schema (didn't match current code)
    in §4.4, §8 (abridged values tree), and the minimal `values.local.yaml` example
  - New troubleshooting entry for the CRI-O short-name `ImageInspectError`
- `deploy/kubernetes/charts/cubeplex/values.local.yaml.example`: same `model_presets`
  schema fix, with an inline comment explaining the at-least-one-tier requirement

## Final Verification — Sandbox Tool-Call E2E

Same port-forward-based approach as the chat e2e, but with a prompt that requires a tool
call: "List the contents of /workspace (run `ls -la /workspace`)."

Observed SSE sequence: `status × 3 → tool_call_delta × N → tool_call → usage →
tool_result → text_delta × N → usage → done`. The `tool_result` payload contained the
sandbox's actual `ls -la` output (`.skills/`, `lost+found/`), and the assistant's final
text was an accurate natural-language summary of that real output — not a
hallucination masking a failure. Ran this twice, including once with two conversations'
sandbox pods running concurrently (both reached `2/2 Running`, both tool calls
succeeded); one early request in each run hit a transient `HTTP 504` from
resource/volume-attachment settling right after the node-pool transition, and the
agent's own retry logic recovered cleanly both times without any user-visible failure
in the final response.

## Follow-up: Egress Secret-Injection (MITM Webhook) — 3 More Bugs, Then a Real PASS

User asked whether the egress secret-injection feature (§4.10 — mitmproxy addon in each
sandbox swaps `cbxref_<id>` placeholders for real secrets over an mTLS exchange with the
backend) had been tested. It hadn't — separate opt-in feature, `egress.enabled: false`
default, never touched during the deployment above. Enabled it and worked through three
more real bugs before landing a genuine pass.

### Bug 1: `sandboxNamespace` doc/example default is wrong for this topology

Set `egress.sandboxNamespace: "opensandbox-system"` per the (existing, unfixed-until-now)
doc default — but that's where `opensandbox-server`/`-controller` themselves run, not
where actual per-conversation sandbox pods land (`"opensandbox"`, confirmed repeatedly
earlier in this same session via `kubectl get pods -n opensandbox`). Traced
`egress-mutatingwebhookconfiguration.yaml`: the `MutatingWebhookConfiguration`'s
`namespaceSelector.matchLabels` is set to `kubernetes.io/metadata.name: <sandboxNamespace>`
— wrong value here means the webhook **never fires for real sandbox pods**, and
`failurePolicy: Ignore` means this produces zero errors anywhere; it just silently does
nothing. Fixed by setting `sandboxNamespace: "opensandbox"` instead, confirmed via
`helm template` that the CA/addon Secrets and the webhook's `namespaceSelector` all
correctly target `opensandbox` before applying.

### Bug 2: Helm's `genCA` generates RSA; the webhook hard-requires EC

Webhook pod crashed instantly: `TypeError: CA key must be an EC private key`. Root cause:
`_egress-helpers.tpl`'s `cubeplex.egress.certs` helper calls Helm/Sprig's built-in
`genCA`/`genSignedCert` — which only ever produce RSA keys, Sprig has no EC option — while
`deploy/kubernetes/egress-bundle/webhook/cert_minter.py`'s `load_ca()` explicitly requires
`ec.EllipticCurvePrivateKey` and raises otherwise. The chart's own cert-generation
mechanism cannot produce output its own application will accept.

No clean values.yaml-level fix exists (Sprig genuinely can't do EC). Worked around it for
this verification by generating a proper EC CA + two leaf certs manually, using the exact
same `generate_ca()`-style logic as `cert_minter.py` itself (SECP256R1, same PEM/Secret-key
shapes: `mitmproxy-ca.pem`/`mitmproxy-ca-cert.pem` for the CA Secret, `tls.crt`/`tls.key`
for the webhook and backend mTLS Secrets), then created/overwrote all four Secrets
(`cubeplex-egress-mitm-ca`, `egress-mitm-ca` in the sandbox ns, `cubeplex-egress-webhook-tls`,
`cubeplex-backend-mtls`) via `kubectl` *before* the next `helm upgrade` — the chart's
`lookup`-or-mint logic finds all three cert Secrets present and reuses them rather than
re-minting via the broken RSA path. Deleted the crashing pod to force a re-read; webhook
came up clean afterward. **This is a real chart bug, not fixed in the chart itself** —
flagging for a proper fix (either shell out to a cert-generation Job using the same EC
logic as `cert_minter.py`, or relax the webhook to accept RSA too).

### Bug 3: `sandbox.egress_exchange_host` is required but never set by any chart template

Webhook + mTLS listener both came up healthy, `egress.enabled: true` looked fully wired —
but a real conversation's tool call still got the placeholder literally: sandbox env var
`$TEST_EGRESS_TOKEN` was empty (`TOKEN length: 0`). Traced `backend/cubeplex/sandbox/manager.py`:
`self._exchange_host: str = config.get("sandbox.egress_exchange_host", "")`, and **every**
injection code path — building placeholder envs, force-allowing the exchange host in the
sandbox's network policy — is gated on `if self._exchange_host:`. Grepped the entire chart:
no template sets this key anywhere. Enabling `egress.enabled` deploys all the
webhook/mTLS/CA/ConfigMap infrastructure but never flips the one backend config switch that
actually turns on its use — the feature is a structural no-op out of the box even with a
fully-correct `values.local.yaml` per the (until-now) documented fields.

**Fixed in the chart** (`backend-configmap.yaml`), not just values — added a fourth
`dig`-based passthrough in the existing `sandbox:` block, defaulting to the same host the
sandbox-side mitmproxy addon already connects to (mirrors `egress-webhook-deployment.yaml`'s
own `exchangeURL` default computation) when `egress.enabled` is true:
```yaml
egress_exchange_host: {{ dig "sandbox" "egress_exchange_host" (printf "%s.%s.svc.cluster.local" (include "cubeplex.backend.fullname" .) .Release.Namespace) .Values.backend }}
```
Verified the auto-default resolves correctly via `helm template --namespace cubeplex`
(→ `cubeplex-backend.cubeplex.svc.cluster.local`) before applying — a first `helm template`
run without `--namespace` produced `cubeplex-backend.default.svc.cluster.local`, a reminder
that `.Release.Namespace` needs the real `--namespace` flag even for template-only dry runs.

### Full round-trip verification

With all three fixed: created a workspace-scoped sandbox-env secret
(`hosts: ["httpbin.org"]`, `header_names: ["Authorization"]`, a random `secret_value`),
allow-listed `httpbin.org` in the org's sandbox network policy (network policy and
credential-substitution eligibility are two independent gates — `hosts` on the secret
alone does not open network egress; needed both), then had the agent run:
```bash
curl -s -H "Authorization: Bearer $TEST_EGRESS_TOKEN" https://httpbin.org/headers
```
`httpbin.org` echoed back what it actually received:
```json
"Authorization": "Bearer REAL-SECRET-2e76fb892746"
```
— the real secret value, not the `cbxref_<32 chars>` placeholder the sandbox env var
actually held. Confirms the full chain: backend mints placeholder → sandbox env holds
placeholder → agent sends placeholder in the Authorization header → sandbox-side
mitmproxy addon keys off the TLS SNI (`httpbin.org`), calls the backend's mTLS exchange
endpoint, gets the real value back, substitutes it in-flight before the request leaves
the sandbox → destination sees only the real secret, never the placeholder, and the
value never touched the LLM prompt or conversation history.

Side notes from this run:
- Needed org-admin (not just workspace-admin) to `PUT /api/v1/admin/sandbox-policy` —
  single_tenant mode's first-ever-registered-user-is-owner bootstrap only applies to a
  truly fresh org; by this point in the session the org already had 11 members from
  earlier test runs, none of them owner/admin. Used
  `python -m cubeplex.cli admin grant-admin <email> --org-slug default` (run via
  `kubectl exec` into the backend pod) to promote a test user — this is the documented
  operator CLI path, not a workaround.
- Hit `HTTP 504` a few times purely from **resource contention**, not from these bugs:
  the 2× `VM.Standard.E5.Flex` node pool struggles with more than ~1-2 concurrent sandbox
  pods (see the CPU-pressure note in the tool-call section above). `kubectl delete
  batchsandboxes.sandbox.opensandbox.io -n opensandbox --all` cleans up leftover
  test sandboxes and frees room immediately (deleting just the Pod respawns it — the
  BatchSandbox controller reconciles it right back; delete the CR instead).
- **Network policy is structural, set only at sandbox creation time** (confirmed via
  `_apply_egress`'s own docstring in `manager.py`) — adding a new allowed host to the
  org's sandbox policy does **not** retroactively apply to an already-running sandbox
  pod reused across conversations. Had to `kubectl delete batchsandboxes -n opensandbox
  --all` to force a fresh sandbox before a newly-allow-listed host (`postman-echo.com`,
  swapped in after `httpbin.org` started 503ing) actually became reachable.
- **A regenerated CA does NOT auto-propagate everywhere** — after running
  `gen-egress-certs.sh` (below) with `FORCE=true` to replace the certs, `kubectl delete
  pod`-ing the webhook and `rollout restart`-ing the backend was **not enough**: the
  cluster-scoped `MutatingWebhookConfiguration`'s `caBundle` field is a separate,
  independently-templated value that only gets refreshed by re-running `helm upgrade`
  (confirmed by diffing the CA cert embedded in `caBundle` against the CA cert actually
  in the Secret — they didn't match until the next `helm upgrade`). Until that happens,
  the API server's calls to the webhook fail TLS verification silently
  (`failurePolicy: Ignore`) and every sandbox pod is created unpatched — no error
  anywhere, injection just doesn't happen. **Rule of thumb: any time you touch the
  egress cert Secrets directly, always follow with a `helm upgrade` before testing.**
- **A freshly-created sandbox's very first outbound request can race past mitmproxy's
  own startup** — confirmed via `docker/egress` container logs: transparent-intercept
  iptables rules (`iptables transparent rules installed successfully`) went in ~600ms
  *after* the sandbox process's first outbound connection attempt was already logged.
  That first request leaked the literal `cbxref_...` placeholder (network policy was
  already active and let it through; MITM redirection just wasn't wired up yet). A
  second request against the same, now-warm sandbox worked correctly and substituted
  the real secret. This is a startup-ordering issue inside the vendored/third-party
  `docker/egress` binary itself (`opensandbox/egress:v1.0.12`), not something fixable
  from the cubeplex chart side — flagging as a known gap for anyone relying on a brand
  new sandbox's first tool call to have secrets substituted.

## `gen-egress-certs.sh` — a Proper Fix for the RSA/EC CA Bug

The chart's own `genCA`-based cert generation (Bug 2 above) has no values.yaml-level
fix, and hand-crafting Secrets with `kubectl create` + inline Python each time isn't
something to leave as tribal knowledge. Added
`deploy/kubernetes/scripts/gen-egress-certs.sh`:

- Reuses `cert_minter.py`'s own `generate_ca()` — the exact function the webhook already
  trusts — rather than reimplementing the crypto. Added a new `mint_server_cert()`
  function to `cert_minter.py` itself (with a unit test,
  `deploy/kubernetes/egress-bundle/webhook/tests/test_cert_minter.py`) since the
  existing `CertMinter.mint()` only produces CN-only client certs (for per-sandbox mTLS
  auth); the webhook's own serving cert and the backend's mTLS listener cert both need
  SANs for TLS hostname verification, which nothing in the module produced before.
- Generates all three cert pairs (CA + webhook leaf + backend leaf) in one Python
  subprocess and writes all four Kubernetes Secrets
  (`<release>-egress-mitm-ca`, `egress-mitm-ca` in the sandbox namespace,
  `<release>-egress-webhook-tls`, `<release>-backend-mtls`) via `kubectl apply`.
- Idempotency-safe like the existing (pre-Helm, disconnected) `gen-ca.sh`: refuses to
  overwrite existing Secrets unless `FORCE=true`, since replacing the CA invalidates
  every already-signed cert.
- **Verified end-to-end on this cluster**: ran with `FORCE=true` to replace my earlier
  hand-crafted certs, confirmed the idempotency guard correctly refuses a second run
  without `FORCE`, restarted the webhook + backend, re-ran `helm upgrade` (see the
  caBundle note above — required), and reproduced the full egress PASS from scratch on
  the script's own output.

## Wiring `gen-egress-certs.sh` into `helm-install.sh` (Automatic Pre-Check)

User asked whether this should be a Helm hook Job instead, so the fix works
regardless of install path (OCI chart pull vs. repo checkout) with zero
operator awareness needed. Investigated and talked through it before writing
anything:

**Why a Job doesn't cleanly work the way it initially sounds like it would.**
Helm renders *all* templates — hooks and main manifests alike — in a single
pass at the start of `helm upgrade`/`install`; `lookup()` calls are evaluated
during that render, which happens *before* any hook has actually run against
the cluster. A `pre-install`/`pre-upgrade` Job that creates the cert Secrets
would not be visible to the `lookup()` calls in `_egress-helpers.tpl` and
`egress-mutatingwebhookconfiguration.yaml` in the *same* `helm upgrade`
invocation — those already rendered (with the old/absent state) before the
hook's Pod even starts. Making a Job work for real would require flipping it
to `post-install`/`post-upgrade` and having it imperatively `kubectl patch`
the already-applied Secrets *and* the cluster-scoped
`MutatingWebhookConfiguration`'s `caBundle` directly (bypassing Helm's own
rendered output for those resources entirely), plus new cross-namespace RBAC
(Secret read/write in two namespaces, patch on a cluster-scoped resource,
Deployment restart) and adding `kubectl` to the webhook image's Dockerfile
(which currently only has Python + `cryptography`, no `kubectl` binary) so
the Job's container could actually do the patching. That's a real feature —
new chart resources, new RBAC surface, an image change — not a small
integration, and per the repo's own `AGENTS.md` workflow rules it would
warrant a worktree + spec/plan rather than being dashed off directly on
`main`.

**What we did instead**, after weighing that against the actual ask (smoother
first-install UX): reframed the problem. The published-OCI-chart install path
*already* requires the operator to hand-generate several secrets
(`jwt_secret`, `csrf_secret`, `vault_key`, DB passwords) via `openssl rand`
before running the one-line `helm upgrade --install ... oci://...` — "zero
pre-steps" was never actually true for that path. Adding "run
`gen-egress-certs.sh` first if you're enabling egress" is consistent with
that existing pattern rather than a new regression, so it's now documented
as one more required pre-step for the OCI path (§4.10 above).

For the **repo-checkout path** (`deploy/kubernetes/scripts/helm-install.sh`),
we *can* remove the pre-step entirely without any Job/RBAC/Dockerfile work,
since that script already runs client-side before `helm upgrade`. Added:

1. `gen-egress-certs.sh`: creates `$NAMESPACE`/`$SANDBOX_NAMESPACE` if either
   is missing (a true first install won't have them yet — `--create-namespace`
   only creates `$NAMESPACE`, and only as part of the `helm upgrade` call this
   script now runs *before*). Reworked the idempotency guard from
   "any Secret exists → hard error, require `FORCE=true`" to "all 4 exist →
   silent no-op exit 0" (the expected steady state on every rerun) vs.
   "partial state → error, needs manual inspection" vs. "none exist → proceed"
   — the old all-or-nothing guard would have made every subsequent
   `helm-install.sh` run after the first one fail outright.
2. `helm-install.sh`: renders just `templates/egress-secrets.yaml` via
   `helm template --show-only` to ask Helm itself whether egress is
   *effectively* enabled (correctly accounts for the values.yaml default +
   values.local.yaml override, unlike grepping the raw YAML) and, if so, runs
   `gen-egress-certs.sh` before the real `helm upgrade --install`.

**Verified end-to-end, twice:**
- With all 4 Secrets already present (the common case) — detection correctly
  printed the no-op message and proceeded straight to a normal
  `helm upgrade --install` (came back `STATUS: deployed`, all pods `Running`).
- Deleted all 4 Secrets to simulate a true first install with egress enabled
  — `helm-install.sh` correctly detected the gap, minted fresh EC certs,
  wrote all 4 Secrets, then a real `helm upgrade` synced the
  `MutatingWebhookConfiguration`'s `caBundle` automatically as part of the
  same run (confirmed by diffing the CA cert in `caBundle` against the CA
  cert in the Secret — matched without any manual intervention). Restarted
  the webhook/backend pods afterward (needed here only because this was a
  live, already-running deployment being reset for the test — a genuine
  first install has no pre-existing pods to go stale, so this step isn't
  needed in the real first-install case).

## Known Limitations / Follow-ups

- The virtual node pool is gone; the cluster is now 2× `VM.Standard.E5.Flex` real nodes
  only. That's adequate for this smoke-test-sized deployment but tight — saw
  `Insufficient cpu`/`Insufficient memory` scheduling warnings when 2 sandbox pods ran
  concurrently alongside the core cubeplex + OpenSandbox infra pods. Size the node pool
  (replica count and/or shape) to the real expected concurrent-sandbox load before using
  this cluster for anything beyond validation.
- GHCR package visibility is a one-time release-process step, not fixed in the repo —
  worth adding to the release checklist so it doesn't block the next version's first
  external deployer.
- The OpenSandbox `secure_access: false` workaround (§ above) disables OSEP-0011 signed
  route tokens between the server and (undeployed) ingress gateway. Fine for this
  chart-bundled, cluster-internal setup where the backend talks to the sandbox server
  directly; revisit if the `gateway.enabled: true` component is ever deployed for
  external sandbox access.
