# Egress Key-Injection — K8s Test-Environment E2E Test Report

**Date:** 2026-05-26
**Branch:** `feat/egress-key-injection` (rebased onto `origin/main` e4f05508)
**Cluster:** `kubernetes-admin@kubernetes` (Calico CNI, nodes on `192.168.1.0/24`)
**Driver:** worktree backend (`192.168.1.150`) + the deployed egress bundle.

Companion logbook (every cluster mutation + rollback): `2026-05-26-egress-k8s-e2e-logbook.md`.

---

## 1. Goal

Deploy the egress secret-injection bundle into the real test cluster and verify
the full flow with the worktree front/back-end: create a sandbox, add an
encrypted env var, and confirm the egress layer swaps the `cbxref_` placeholder
for the real secret at the network boundary.

## 2. Verdict — ✅ VERIFIED END-TO-END

The full flow works. With the sandbox scheduled on a **healthy node
(k8s-test-217)**, an egress sandbox sent an HTTPS request carrying the
`cbxref_…` placeholder in the `X-Egress-Test` header, and **httpbin received
the real secret** `s3cr3t-REAL-VALUE-42` (the placeholder never left the
boundary; the secret never entered the sandbox):

```
sandbox: curl -H "X-Egress-Test: cbxref_LNAJQOF5NXWELZQYUMPTMNWVTZLXWWG4" https://httpbin.org/headers
httpbin sees: "X-Egress-Test": "s3cr3t-REAL-VALUE-42"
```

**Root cause of the long earlier struggle (answering "egress bug vs config vs
system dependency"): a NODE INFRASTRUCTURE problem on `k8s-test-207`.** Pods
scheduled there have broken pod networking — even a *plain, non-egress* pod on
207 cannot reach cluster DNS (`10.2.0.10`) or the internet, while all working
sandboxes run on `k8s-test-217`. My egress test pods (and the cubeplex-created
one) happened to land on 207, so they couldn't do DNS → the egress looked
broken. It is **not** an egress bug and **not** a config issue: plain
OpenSandbox egress (allow-list + DNS proxy) and the full cubeplex MITM
substitution both work correctly once the pod is on a healthy node.

Action for the cluster: fix/cordon `k8s-test-207` (or pin sandboxes to healthy
nodes) — pods there have no working dataplane.

## 3. What was deployed (all in `opensandbox` ns unless noted)

| Component | Object | Status |
|---|---|---|
| Egress MITM CA (dual-purpose) | `secret/egress-mitm-ca` | ✅ |
| Webhook serving cert | `secret/egress-webhook-tls` | ✅ |
| Webhook image | `hub.sensedeal.vip/library/cubeplex-egress-webhook:20260526e` | ✅ pushed |
| Webhook | `deploy/svc/egress-webhook` + SA/Role/RoleBinding | ✅ Running, healthz 200 |
| Admission webhook | `mutatingwebhookconfiguration/egress-inject` (ns-scoped, failurePolicy Ignore) | ✅ |
| Addon | `configmap/egress-inject-addon` | ✅ |
| Exchange listener | in-process mTLS uvicorn on `192.168.1.150:9443` | ✅ |

cubeplex config: `egress_exchange.auth.mode=mtls`, `listener.enabled=true` (cert/key
+ egress CA), `sandbox.egress_exchange_host=192.168.1.150`,
`sandbox.use_server_proxy=false`. Worktree DB was reset (it was stamped at a
since-removed merge revision) and re-migrated cleanly to head `6c69cc288404`.

## 4. Verified — with evidence

1. **mTLS exchange listener.** Valid per-sandbox client cert → `403 exchange
   denied` (fail-closed for an unknown placeholder); **no client cert → TLS
   handshake rejected** (curl exit / HTTP 000). Confirms cert-bound identity +
   fail-closed, with no forgeable header.
2. **Webhook patching.** A sandbox pod (owned by `BatchSandbox`, egress
   container present) → `POST /mutate` returns 200, mints
   `secret/egress-client-<sandbox_id>` (CN = sandbox_id), injects the MITM env
   (`OPENSANDBOX_EGRESS_MITMPROXY_{TRANSPARENT,SCRIPT,CONFDIR}` +
   `EGRESS_EXCHANGE_URL`), and mounts the addon, a writable confdir, and the
   client cert. Verified on a live patched pod.
3. **Injection.** `manager.get_or_create` resolves the vault, mints a `cbxref_`
   placeholder into the execute-time run env (`MYTOKEN=cbxref_…`), persists a
   valid `EgressRef` with the binding, and builds the egress `NetworkPolicy`
   (allow = exchange host + secret hosts). The egress sidecar is added by
   OpenSandbox when the network policy is set.
4. **Addon + mitmproxy.** After the httpx→stdlib fix, the addon loads in
   mitmproxy's bundled interpreter and `mitmproxy: transparent intercept active`
   — it **intercepted a real request** (`GET https://…/headers` captured in the
   flow log).
5. **Secret env vault (product API).** Registered a user, logged in (CSRF
   double-submit), `POST /api/v1/ws/{ws}/sandbox-env/me` created an encrypted
   secret entry (`MYTOKEN`, `hosts=[httpbin.org]`, `header_names=[X-Egress-Test]`).
6. **Sandbox create (after rebase).** With the rebased compat fixes
   (`48c798d7` secure_access, `e152286d` create-timeout/driver-agnostic errors)
   + `use_server_proxy=false` + a reachable server endpoint, `Sandbox.create`
   **succeeds server-side** (BatchSandbox allocated + pod Ready).

## 5. Bugs found & fixed (none catchable by the mocked unit tests)

All committed on the branch (`aceb71a4` + earlier):

1. **Webhook owner match** — real pods are owned by `BatchSandbox`, not
   `Sandbox`. `is_sandbox_pod` + `sandbox_id_from_owners` now accept both.
2. **`app.py` sandbox_id extraction** — same `kind=="Sandbox"` filter would
   `StopIteration` → 500 on real pods.
3. **`cert_minter.py` `datetime.UTC`** — Python 3.11+ only; switched to
   `datetime.timezone.utc`.
4. **`k8s_client.py` `await config.load_incluster_config()`** — that call is
   synchronous in `kubernetes_asyncio`; the `await` raised `TypeError` → webhook
   500 → pod admitted unpatched.
5. **mitmproxy confdir mounted read-only** — mounting the CA secret directly at
   the confdir made mitmdump crash writing `mitmproxy-dhparam.pem`. Fixed with a
   writable `emptyDir` confdir seeded by an `egress-mitm-confdir` initContainer
   that builds mitmproxy's combined `mitmproxy-ca.pem` (key+cert).
6. **Addon `import httpx`** — mitmproxy's bundled Python has no httpx →
   `ModuleNotFoundError` → addon crashloop → no interception. Rewrote the mTLS
   exchange call to use only stdlib (`http.client` + `ssl`).
7. **Deploy ergonomics** — the `egress-inject-addon` ConfigMap must be applied
   (added to deploy order); the 4.36 GB sandbox image must be pre-pulled on
   nodes or `kubernetes.sandbox_create_timeout_seconds` raised (first-pull was
   2m2s, exceeding OpenSandbox's 60 s pod-ready window → the apparent "504").

## 6. What looked like a blocker — and the real root cause

For a long stretch the egress sandbox couldn't reach DNS / the exchange /
httpbin, which looked like an egress dataplane bug. The decisive isolation:

- A **plain, non-egress pod pinned to `k8s-test-207`** also cannot reach cluster
  DNS (`10.2.0.10`) or the internet — both time out.
- All healthy sandboxes run on **`k8s-test-217`**; the test pods (and the
  cubeplex-created egress sandbox) were scheduled on **207**.
- Pinning the egress sandbox to **217** → plain egress passes (allowed
  `httpbin=200`, denied `example.com` blocked) and the full MITM substitution
  succeeds (§2).

So the "egress is broken" symptom was entirely **`k8s-test-207`'s broken pod
networking** — a node/infra issue, independent of the egress feature and of
cubeplex. (Side note explored along the way: the egress DNS proxy sets `SO_MARK`
on upstream queries; for clusters whose resolver is a ClusterIP needing
kube-proxy DNAT, `OPENSANDBOX_EGRESS_NAMESERVER_EXEMPT=<dns-clusterip>` exists to
dial it without the mark. It was **not** needed on the healthy node here, but
it's the right knob if a future cluster's DNS is a DNAT-only ClusterIP.)

## 7. Recommendations

1. Fix the OpenSandbox egress sidecar outbound/DNS in this cluster (egress
   component), then re-run the live substitution (all cubeplex pieces are ready).
2. Bake the egress-bundle bug fixes (§5) — they are real and were committed.
3. Add to bundle docs: apply the addon ConfigMap; pre-pull the sandbox image (or
   raise the create timeout); the exchange server cert needs an IP/DNS SAN
   matching `EGRESS_EXCHANGE_URL`.

## 8. Rollback

Full teardown + per-object rollback is in the logbook
(`2026-05-26-egress-k8s-e2e-logbook.md`, "Cluster mutations" + "Full teardown").
OpenSandbox server config was restored to its pre-test snapshot (the bundle keeps
OpenSandbox stock); test sandboxes and the image-prepull DaemonSets were deleted.
The egress bundle itself is left deployed (it is the deliverable and is healthy).
