# Egress Key-Injection — K8s Test-Environment E2E Logbook

Live logbook for exercising the full egress secret-injection flow against the
real test cluster (`kubernetes-admin@kubernetes`). Every cluster mutation is
recorded here with its rollback. Snapshot for rollback:
`/home/chris/cubeplex/_design-explorations/egress-e2e-snapshot-20260526-143153/`.

## Goal

Deploy the egress bundle into the test cluster and, using the worktree
frontend+backend, create a sandbox, add an encrypted env var, and verify the
egress exchange swaps the `cbxref_` placeholder for the real secret at the
network boundary.

## Environment (discovered)

- Cluster: Calico CNI, nodes on `192.168.1.0/24` (e.g. `192.168.1.207`).
- Dev machine (runs worktree FE/BE): `192.168.1.150` — **same subnet** as nodes,
  so sandbox pods should reach the exchange listener at `192.168.1.150:<port>`.
- OpenSandbox: `opensandbox-system` ns runs `opensandbox-server` (NodePort
  32378), `opensandbox-ingress-gateway` (NodePort 32379), controller-manager.
  Sandboxes run in `opensandbox` ns as `<uuid>-0` pods.
- Public entry: `39.99.248.80:18080` → NodePort 32378. cubeplex uses
  `CUBEPLEX_SANDBOX__DOMAIN=39.99.248.80:18080`, `use_server_proxy=false`,
  `secure_access=True`.
- Server egress config (`opensandbox-server-config`): `egress.image =
  …/opensandbox/egress:v1.0.12`, `egress.mode = dns+nft`. The egress sidecar
  (`name: egress`) is appended **only when a network_policy is set on create**
  (`apply_egress_to_spec`). `egress:v1.0.12` supports transparent MITM via
  `OPENSANDBOX_EGRESS_MITMPROXY_{TRANSPARENT,SCRIPT,CONFDIR}` (confirmed in
  `components/egress/main.go`).

## Integration mismatches found (vs the bundle as written)

1. **Pod owner kind.** Real sandbox pods are owned by **`BatchSandbox`**, but the
   webhook's `is_sandbox_pod` only matched `kind == "Sandbox"`. → fix webhook.
2. (watch) Exchange URL the webhook injects must point at the dev-machine mTLS
   listener (`https://192.168.1.150:<port>/api/v1/internal/egress/exchange`).
3. (watch) Pod→host (192.168.1.150) reachability under Calico SNAT — to verify.

## Bugs found by the real E2E (none caught by unit tests)

1. **Webhook matched only `kind: Sandbox`** — real pods are owned by `BatchSandbox`.
   Fixed in `patch.py` (`is_sandbox_pod` + new `sandbox_id_from_owners`). Also fixed
   `app.py` which had the same `kind=="Sandbox"` filter (would 500 / StopIteration).
2. **`datetime.UTC`** in `cert_minter.py` — only exists on Python ≥3.11; the
   deploy image base had to be 3.10 (Docker Hub blocked here). Switched to
   `datetime.timezone.utc` (3.10+ safe, identical on 3.13).
3. **`await config.load_incluster_config()`** in `k8s_client.py` — that call is
   synchronous in `kubernetes_asyncio`; the `await` raised `TypeError` → webhook
   500 → pod admitted unpatched (failurePolicy: Ignore). Removed the `await`.
4. **mitmproxy confdir mounted read-only** — `patch.py` mounted the CA secret
   directly at the mitmproxy confdir `readOnly`, so mitmdump crashed writing
   `mitmproxy-dhparam.pem` ("Read-only file system") and transparent intercept
   never started → placeholder passed through. Fixed: writable `emptyDir`
   confdir seeded by an `egress-mitm-confdir` initContainer that builds the
   combined `mitmproxy-ca.pem` (key+cert) mitmproxy expects.
5. **Operational, not a bug:** OpenSandbox server waits 60s for the sandbox pod
   to be Running; first-time pull of `egress:v1.0.12` exceeded that → 504
   `POD_READY_TIMEOUT`. Mitigated with an `egress-prepull` DaemonSet caching the
   egress image on every node. (Bundle should document pre-pulling the egress image.)
6. **Missing step in deploy:** the `egress-inject-addon` ConfigMap must be
   applied (it wasn't initially) — pod failed to mount it. Added to deploy order.
7. **Addon `import httpx`** — the addon runs inside mitmproxy's bundled Python,
   which has NO httpx → `ModuleNotFoundError` → mitmdump crashloops → no
   interception (and DNS breaks). Rewrote `inject.py` to use only stdlib
   (`http.client` + `ssl` for the mTLS exchange call). **Important bundle fix.**
8. **Real cause of the "504 / slow pod":** NOT the egress sidecar — the
   **cubeplex-sandbox image is 4.36 GB and took 2m2s to pull** on nodes that
   hadn't cached it, exceeding OpenSandbox's 60s pod-ready window. The init
   containers + mitmproxy were fast. Mitigation: pre-pull the sandbox image on
   all nodes (DaemonSet), OR raise `kubernetes.sandbox_create_timeout_seconds`.
   This is why item #11 (server timeout bump) appeared necessary — it was a
   misdiagnosis; image pre-pull is the right fix and needs no server change.

## Cluster mutations (with rollback)

All cluster objects created carry label `app.kubernetes.io/part-of: cubeplex-egress`
(except the per-sandbox `egress-client-*` secrets and the test sandboxes).
Applied manifests kept in `_design-explorations/egress-e2e-applied/`. Local certs
in `/home/chris/cubeplex-egress-certs/` (CA, exchange server cert, webhook cert).

| # | Action | Rollback |
|---|--------|----------|
| 0 | snapshot dumped MWC/VWC, server config, ns state | n/a |
| 1 | `secret/egress-mitm-ca` in `opensandbox` (gen-ca.sh) | `kubectl -n opensandbox delete secret egress-mitm-ca` |
| 2 | `secret/egress-webhook-tls` in `opensandbox` | `kubectl -n opensandbox delete secret egress-webhook-tls` |
| 3 | SA+Role+RoleBinding `egress-webhook` in `opensandbox` | `kubectl -n opensandbox delete sa/egress-webhook role/egress-webhook rolebinding/egress-webhook` |
| 4 | pushed `hub.sensedeal.vip/library/cubeplex-egress-webhook:20260526` | registry image (harmless; leave or delete in registry UI) |
| 5 | `deploy/egress-webhook` + `svc/egress-webhook` in `opensandbox` | `kubectl -n opensandbox delete deploy/egress-webhook svc/egress-webhook` |
| 6 | `mutatingwebhookconfiguration/egress-inject` | `kubectl delete mutatingwebhookconfiguration egress-inject` |
| 7 | `configmap/egress-inject-addon` in `opensandbox` | `kubectl -n opensandbox delete cm egress-inject-addon` |
| 8 | `daemonset/egress-prepull` in `opensandbox` (egress image cache) | `kubectl -n opensandbox delete ds egress-prepull` |
| 9 | webhook re-tagged through `:20260526e` (bug fixes) | registry images; harmless |
| 10 | test user/workspace + `senv-*` vault entry in worktree DB | DB is the per-slot worktree DB; reset anytime |
| 11 | **opensandbox-server-config**: added `kubernetes.sandbox_create_timeout_seconds = 240` (default 60 too short for egress-MITM sandbox startup) + server rollout-restart | restore `opensandbox-server-config.before.yaml` from snapshot via `create cm --from-file=config.toml=<orig> --dry-run \| apply`, then rollout-restart. **NOTE:** this is the ONE OpenSandbox-server change (otherwise stock); needed only because the egress sidecar adds startup time. |

> ⚠️ Incident during the exercise: a first attempt to edit this configmap used
> `jsonpath={.data.config.toml}` (unescaped dot → empty), which wrote an empty
> config and was applied, briefly breaking the server config. Restored from the
> snapshot (good config = 846 chars) and re-applied correctly. Lesson: extract
> configmap keys with a dot in the name via `-o json` + Python, not jsonpath.

**Full teardown (one shot):**
```
kubectl delete mutatingwebhookconfiguration egress-inject
kubectl -n opensandbox delete deploy/egress-webhook svc/egress-webhook \
  sa/egress-webhook role/egress-webhook rolebinding/egress-webhook \
  secret/egress-mitm-ca secret/egress-webhook-tls \
  cm/egress-inject-addon ds/egress-prepull \
  $(kubectl -n opensandbox get secret -o name | grep egress-client- | tr '\n' ' ')
# delete any test sandboxes (BatchSandbox CRs) created during the exercise
```
OpenSandbox server config is NOT modified (bundle keeps it stock), so no restore
needed there. `failurePolicy: Ignore` on the webhook means even a broken webhook
never blocks sandbox pod creation.

## 2026-05-27 — Node 207 Calico cert-expiry fix (cluster mutation, user-directed)

Root cause of the earlier "egress broken on 207" symptom: `tigera-operator` was
scaled to `0/0`, so the Calico Typha mTLS certs (`typha-certs` CN=typha-server,
`node-certs` CN=typha-client) — both expired `Apr 30 14:14:16 2026 GMT` — were
never rotated. Felix on 207 (`calico-node-jpqcs`, 0/1) couldn't connect to Typha
(`x509: certificate has expired`), so its `cali40all-hosts-net` ipset was empty
(count=0) and the IPIP input firewall DROPped all cross-node pod traffic →
pods on 207 had no DNS / no internet.

Mutations applied (user said "恢复 tigera operator 然后检查机器状态"):

1. **Recorded rollback point:** `tigera-operator` was `spec.replicas: 0`
   (image `quay.io/tigera/operator:v1.28.15`). To revert:
   `kubectl -n tigera-operator scale deploy tigera-operator --replicas=0`.
2. `kubectl -n tigera-operator scale deploy tigera-operator --replicas=1`
   → operator acquired the leader lease and reconciled. It **rotated** the certs:
   `typha-certs`/`node-certs` now `notBefore May 27 02:03:15 2026`,
   `notAfter Aug 29 02:03:16 2028`.
3. `kubectl -n calico-system rollout restart deploy/calico-typha` (serve new
   server cert) + `kubectl -n calico-system delete pod calico-node-jpqcs`
   (207 picks up new client cert). New 207 node pod `calico-node-sbfnf` came up
   `1/1`, Felix logged `Connected to Typha … 192.168.1.208:5473`, no x509 errors.

Verification (root cause cleared):
- `cali40all-hosts-net` ipset on 207 went from empty → 3 entries (`.207/.208/.217`).
- Plain probe pod pinned to 207 (`net-probe-207`, since deleted): cluster DNS
  resolves (`kubernetes.default → 10.2.0.1`), external DNS resolves
  (`httpbin.org`), and HTTPS egress works (`GET https://httpbin.org/ip → 200`,
  origin `106.38.109.10`). Previously all three timed out.
- `kubectl get nodes` all `Ready`; all `calico-system` pods Ready;
  `tigera-operator` `1/1`.

Note: re-enabling the operator means it will now keep reconciling Calico config
(it was deliberately at 0 before). If the cluster owner wants it back at 0 after
the certs are valid, use the revert command in step 1 — but the certs will then
drift toward expiry again in ~2 years with no rotation.

## Rollback procedure (summary)

1. `kubectl delete mutatingwebhookconfiguration egress-inject-webhook` (the one
   this exercise adds — confirm name against snapshot; none existed for egress
   before).
2. `kubectl -n opensandbox delete deploy/svc` for the webhook; delete the
   `egress-inject-addon` cm, `egress-mitm-ca` secret, any `egress-client-*`
   secrets, webhook TLS secret.
3. Restore `opensandbox-server-config` from
   `opensandbox-server-config.before.yaml` if it was changed (it should NOT be —
   the bundle keeps OpenSandbox stock).
4. Delete any test sandboxes created during the exercise.
5. Stop the worktree backend/frontend processes.
