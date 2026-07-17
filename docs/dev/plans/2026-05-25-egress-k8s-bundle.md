# Egress K8s Bundle (Webhook + Addon) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A cubeplex-owned Kubernetes bundle that turns OpenSandbox's stock egress sidecar into a secret-injecting MITM: a mutating admission webhook patches each sandbox pod (enable transparent MITM, mount a per-sandbox mTLS identity + a fixed CA, load the `inject.py` addon, inject CA trust into the app container), and the `inject.py` addon swaps `cbxref_` placeholders for real secrets via Plan 2's exchange endpoint.

**Architecture:** OpenSandbox server + egress image stay 100% stock. A Python (FastAPI) admission webhook, deployed by us into the dedicated sandbox namespace, mints a short-lived per-sandbox client cert (CN=sandbox_id) signed by a cubeplex CA the exchange endpoint trusts, and patches the pod. The egress sidecar runs the bundled `inject.py` (from a ConfigMap) which scans outbound headers for `cbxref_`, calls the exchange endpoint over mTLS, and substitutes the secret.

**Tech Stack:** Python (FastAPI admission webhook), `cryptography` (cert minting), mitmproxy addon (Python), Kubernetes (MutatingWebhookConfiguration, Secret, ConfigMap), OpenSandbox egress image ≥ 2026-05 (mitmproxy transparent support).

**Scope:** Plan 3 of 3. Depends on Plan 2 (the exchange endpoint + `cbxref_` format + per-sandbox mTLS identity contract). This is the only plan that requires a real cluster; its E2E runs on the self-hosted `kubernetes-admin@kubernetes`. Decisions locked (spec §9): mTLS, initContainer CA trust, dedicated namespace, no CA rotation v1, cubeplex-owned bundle, token find-and-replace, host-from-vault.

**Spec:** `docs/dev/specs/2026-05-25-egress-key-injection-design.md` (§4.1 mTLS, §6.1 webhook, §6.2 addon, §6.7 CA).

---

## Prerequisites (confirm before Task 1)

- [ ] OpenSandbox server config `egress.image` points at a build with mitmproxy transparent support (`docker/egress` ≥ the 2026-05 line). Verify: the image runs mitmdump when `OPENSANDBOX_EGRESS_MITMPROXY_TRANSPARENT=true`.
- [ ] Sandboxes run in a **dedicated namespace** (e.g. `opensandbox`). The webhook is scoped to it.
- [ ] cubeplex's exchange endpoint (Plan 2) is reachable from that namespace at a stable internal host:port and is configured with `egress_exchange.auth.mode=mtls` trusting the CA this bundle creates.

---

## Backend completion tasks (carried from the Plan 2 Codex review — MUST do before production)

Plan 2 deferred two backend items that this feature is **not production-correct** without. Do these as part of Plan 3 (they gate the mTLS path and ref freshness that the cluster bundle relies on).

### Task B1 (P1): `MtlsAuthenticator` must read the real client cert from the ASGI scope

File: `cubeplex/sandbox_env/exchange_auth.py` (`MtlsAuthenticator.verify`), plus the exchange service's serving config.

Today `MtlsAuthenticator.verify` reads `request.client_cert`, which **FastAPI/Starlette never populate** — so in production (`mode=mtls`) every sidecar request hits `cert is None` → 401, and the endpoint is unusable outside the dev-token path. Wire it to the real peer certificate:

- Serve the exchange endpoint with client-cert verification enabled. With uvicorn: `ssl_certfile`/`ssl_keyfile` for the server cert + `ssl_ca_certs=<egress CA>` + `ssl_cert_reqs=ssl.CERT_REQUIRED`. Confirm how cubeplex's exchange service is served (its own uvicorn process / a sidecar TLS terminator) and enable mTLS there. If TLS is terminated by an ingress/mesh instead, have it pass the verified client cert (e.g. `X-Forwarded-Client-Cert` / SPIFFE) and read that header instead — but only trust it from the terminator, never from the sandbox.
- In `MtlsAuthenticator.verify`, read the verified peer cert from the ASGI scope (uvicorn exposes the peercert via `request.scope["transport"].get_extra_info("peercert")` / the `extensions` transport info, depending on version) and extract `sandbox_id` from its CN (matching what Task 1's `CertMinter` puts in the CN). Verify CHAIN validation is done by the TLS layer (CERT_REQUIRED + the egress CA), not in Python.
- Test: a unit/integration test that constructs a request scope carrying a peer cert with `CN=sbx-1` and asserts `verify(...)` returns `SidecarIdentity(sandbox_id="sbx-1")`; and that a missing/invalid cert → `PermissionError` → 401. Add a cluster-E2E assertion (Task 6) that a real sidecar mTLS call succeeds and a call without the client cert is rejected.

### Task B2 (P2): inject env at **execute-time** (supersedes creation-time env injection)

**Decision:** the OpenSandbox SDK has **no live env-update** for a running sandbox (only `patch_metadata`), but `commands.run` accepts **per-command env** via `RunCommandOpts(envs=...)`. So env injection moves from `Sandbox.create(env=...)` to **every command execution**. This makes env always-fresh for both new and reused sandboxes and resolves the reuse-staleness problem without recreate. This **supersedes** Plan 2 Task 8's creation-time `env=injection.env` (network_policy + `EgressRef` stay at create/run time).

What stays vs moves:
- **Env (placeholders + plain) → execute-time.** The sandbox backend attaches a per-run env dict to every `commands.run`.
- **`network_policy` → still creation-time only.** The egress allow-list is per-sandbox and cannot change on a live sandbox; structural **host** additions therefore still require a sandbox recreate (document this — it's a reachability limit, not an env-value limit). Secret **value** changes and newly-added env **names** are picked up at execute with no recreate.
- **`EgressRef` → refreshed per run** (mint/refresh at `get_or_create`, expiry bounded + extended, revoked on sandbox death — already wired on the unhealthy + cleanup paths).

Implementation:
1. `cubeplex/sandbox/base.py` + `cubeplex/sandbox/opensandbox.py`: add `envs: dict[str, str] | None = None` to `execute(...)`; `OpenSandbox.execute` passes `opts=RunCommandOpts(envs=<merged>, working_directory=self._workdir)` to `self._sandbox.commands.run(...)`. The backend holds a `self._run_env: dict[str,str]` (default `{}`) and a `set_run_env(env)` setter; `execute` merges `self._run_env` with any per-call `envs` (per-call wins).
2. `cubeplex/sandbox/manager.py` `get_or_create`: on **both** the reuse and create-new branches, when `self._exchange_host` is set: resolve the vault (`SandboxEnvResolver`), build the injection (`SandboxEnvInjector`), call `backend.set_run_env(injection.env)`, and **refresh the EgressRef set** for that `sandbox_id` (revoke prior refs for the sandbox, then persist fresh `EgressRef`(s) with `expires_at = now + ttl`). Keep `network_policy` at `Sandbox.create` (create-new branch only). Drop the creation-time `env=injection.env` kwarg (env now flows via execute).
3. Tests: (a) `OpenSandbox.execute` passes the run env into `commands.run` opts; (b) `get_or_create` reuse path sets a fresh run env + refreshes/revokes refs; (c) a changed vault value is reflected on the next execute (the placeholder→credential mapping resolves the current credential, and a rotated value decrypts fresh); (d) egress-disabled path (`_exchange_host=""`) passes no env and behaves as today.

---

## File Structure (new directory: `deploy/egress-bundle/` + webhook app)

- Create `deploy/egress-bundle/webhook/app.py` — FastAPI admission webhook.
- Create `deploy/egress-bundle/webhook/cert_minter.py` — per-sandbox mTLS cert minting.
- Create `deploy/egress-bundle/webhook/patch.py` — builds the JSON Patch for a sandbox pod.
- Create `deploy/egress-bundle/webhook/Dockerfile` — webhook image.
- Create `deploy/egress-bundle/addon/inject.py` — mitmproxy addon (token scan + header_names + exchange call).
- Create `deploy/egress-bundle/k8s/` — manifests: `namespace.yaml` (if needed), `ca-secret.yaml` (generated), `addon-configmap.yaml`, `webhook-deployment.yaml`, `webhook-service.yaml`, `mutatingwebhookconfiguration.yaml`, `webhook-tls.yaml`.
- Create `deploy/egress-bundle/scripts/gen-ca.sh` — one-time fixed-CA generation → Secret.
- Modify (cross-plan) `cubeplex/api/routes/internal_egress.py` — `ExchangeOut` gains `header_names` so the addon can enforce it (see Task 4).
- Tests: `deploy/egress-bundle/webhook/tests/test_patch.py`, `test_cert_minter.py`, `deploy/egress-bundle/addon/tests/test_inject.py`, and `tests/e2e_cluster/test_egress_injection_e2e.md` (runbook + assertions for the real-cluster check).

---

## Task 1: Per-sandbox mTLS cert minter

**Files:**
- Create: `deploy/egress-bundle/webhook/cert_minter.py`
- Test: `deploy/egress-bundle/webhook/tests/test_cert_minter.py`

- [ ] **Step 1: Write the failing test**

```python
# deploy/egress-bundle/webhook/tests/test_cert_minter.py
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from webhook.cert_minter import CertMinter, load_ca


def _make_ca(tmp_path):
    # produce a throwaway CA key+cert for the test
    from webhook.cert_minter import generate_ca
    key_pem, cert_pem = generate_ca("cubeplex-egress-test-ca")
    return load_ca(key_pem, cert_pem)


def test_minted_cert_has_sandbox_id_cn_and_chains_to_ca(tmp_path):
    ca = _make_ca(tmp_path)
    minter = CertMinter(ca)
    key_pem, cert_pem = minter.mint(sandbox_id="sbx-123", ttl_minutes=60)
    cert = x509.load_pem_x509_certificate(cert_pem)
    cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    assert cn == "sbx-123"
    # verify signature chains to the CA public key
    ca.cert.public_key().verify(
        cert.signature, cert.tbs_certificate_bytes,
        ec.ECDSA(cert.signature_hash_algorithm),
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd deploy/egress-bundle/webhook && uv run --with cryptography pytest tests/test_cert_minter.py -v`
Expected: FAIL `ModuleNotFoundError: webhook.cert_minter`

- [ ] **Step 3: Implement**

```python
# deploy/egress-bundle/webhook/cert_minter.py
"""Mint short-lived per-sandbox client certs (CN=sandbox_id) signed by a fixed CA."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


@dataclass
class CA:
    key: ec.EllipticCurvePrivateKey
    cert: x509.Certificate


def generate_ca(common_name: str) -> tuple[bytes, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return key_pem, cert.public_bytes(serialization.Encoding.PEM)


def load_ca(key_pem: bytes, cert_pem: bytes) -> CA:
    key = serialization.load_pem_private_key(key_pem, password=None)
    cert = x509.load_pem_x509_certificate(cert_pem)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    return CA(key=key, cert=cert)


class CertMinter:
    def __init__(self, ca: CA) -> None:
        self._ca = ca

    def mint(self, *, sandbox_id: str, ttl_minutes: int) -> tuple[bytes, bytes]:
        key = ec.generate_private_key(ec.SECP256R1())
        now = dt.datetime.now(dt.UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, sandbox_id)]))
            .issuer_name(self._ca.cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=2))
            .not_valid_after(now + dt.timedelta(minutes=ttl_minutes))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(self._ca.key, hashes.SHA256())
        )
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        return key_pem, cert.public_bytes(serialization.Encoding.PEM)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd deploy/egress-bundle/webhook && uv run --with cryptography pytest tests/test_cert_minter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add deploy/egress-bundle/webhook/cert_minter.py deploy/egress-bundle/webhook/tests/test_cert_minter.py
git commit -m "feat(egress-bundle): per-sandbox mTLS cert minter"
```

---

## Task 2: Pod patch builder

Given an admission review for a sandbox pod, produce the JSON Patch that: (a) on the `egress` container — sets MITM env + mounts addon ConfigMap + fixed CA + the freshly-minted per-sandbox client cert; (b) on the app pod — adds an initContainer that installs the CA into the system trust store; (c) adds the pod-level volumes.

**Files:**
- Create: `deploy/egress-bundle/webhook/patch.py`
- Test: `deploy/egress-bundle/webhook/tests/test_patch.py`

- [ ] **Step 1: Write the failing test**

```python
# deploy/egress-bundle/webhook/tests/test_patch.py
from webhook.patch import build_pod_patch, is_sandbox_pod

EGRESS_IMAGE = "registry/opensandbox/egress:v1.0.12"

POD = {
    "metadata": {
        "ownerReferences": [
            {"apiVersion": "sandbox.opensandbox.io/v1alpha1", "kind": "Sandbox", "name": "sbx-1"}
        ],
        "labels": {},
    },
    "spec": {
        "containers": [
            {"name": "sandbox", "image": "py:3.13"},
            {"name": "egress", "image": EGRESS_IMAGE},
        ],
        "initContainers": [{"name": "execd-installer", "image": "execd:1"}],
        "volumes": [{"name": "opensandbox-bin", "emptyDir": {}}],
    },
}


def test_recognizes_sandbox_pod():
    assert is_sandbox_pod(POD, egress_image=EGRESS_IMAGE)
    assert not is_sandbox_pod({"metadata": {}, "spec": {"containers": []}}, egress_image=EGRESS_IMAGE)


def test_patch_sets_mitm_env_and_mounts_and_initcontainer():
    ops = build_pod_patch(
        POD, sandbox_id="sbx-1", egress_image=EGRESS_IMAGE,
        exchange_url="https://egress-exchange.internal/api/v1/internal/egress/exchange",
    )
    paths = [op["path"] for op in ops]
    # env appended to the egress container (index 1)
    assert any(p.startswith("/spec/containers/1/env") for p in paths)
    # volumes added
    assert any(p.startswith("/spec/volumes") for p in paths)
    # an initContainer added for CA trust on the app container
    assert any(p.startswith("/spec/initContainers") for p in paths)
    # required env + addon mount + exchange URL present
    blob = str(ops)
    assert "OPENSANDBOX_EGRESS_MITMPROXY_TRANSPARENT" in blob
    assert "EGRESS_EXCHANGE_URL" in blob
    assert "egress-inject" in blob  # addon configmap mount
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd deploy/egress-bundle/webhook && uv run pytest tests/test_patch.py -v`
Expected: FAIL `ModuleNotFoundError: webhook.patch`

- [ ] **Step 3: Implement**

```python
# deploy/egress-bundle/webhook/patch.py
"""Build the JSON Patch applied to a sandbox pod at admission.

Narrow match (spec §6.1): ownerReference Sandbox CR + an `egress` container with
the expected image. Anything else is not patched (fail closed).
"""

from __future__ import annotations

from typing import Any

_MITM_CONFDIR = "/var/lib/mitmproxy/.mitmproxy"
_ADDON_PATH = "/etc/egress-inject/inject.py"


def is_sandbox_pod(pod: dict[str, Any], *, egress_image: str) -> bool:
    owners = pod.get("metadata", {}).get("ownerReferences", [])
    owned_by_sandbox = any(
        o.get("apiVersion", "").startswith("sandbox.opensandbox.io/")
        and o.get("kind") == "Sandbox"
        for o in owners
    )
    containers = pod.get("spec", {}).get("containers", [])
    has_egress = any(c.get("name") == "egress" and c.get("image") == egress_image for c in containers)
    return owned_by_sandbox and has_egress


def _egress_index(pod: dict[str, Any]) -> int:
    for i, c in enumerate(pod["spec"]["containers"]):
        if c.get("name") == "egress":
            return i
    raise ValueError("no egress container")


def _app_index(pod: dict[str, Any]) -> int:
    for i, c in enumerate(pod["spec"]["containers"]):
        if c.get("name") != "egress":
            return i
    raise ValueError("no app container")


def build_pod_patch(
    pod: dict[str, Any], *, sandbox_id: str, egress_image: str, exchange_url: str
) -> list[dict[str, Any]]:
    eidx = _egress_index(pod)
    ops: list[dict[str, Any]] = []

    # 1) env on the egress container (append; create the array if absent).
    # EGRESS_EXCHANGE_URL is required by inject.py — the stock egress image does
    # not define it, so it MUST be injected here or mitmproxy fails to load the
    # addon with KeyError.
    egress = pod["spec"]["containers"][eidx]
    env = egress.get("env", [])
    new_env = env + [
        {"name": "OPENSANDBOX_EGRESS_MITMPROXY_TRANSPARENT", "value": "true"},
        {"name": "OPENSANDBOX_EGRESS_MITMPROXY_SCRIPT", "value": _ADDON_PATH},
        {"name": "OPENSANDBOX_EGRESS_MITMPROXY_CONFDIR", "value": _MITM_CONFDIR},
        {"name": "EGRESS_EXCHANGE_URL", "value": exchange_url},
    ]
    ops.append({"op": "add", "path": f"/spec/containers/{eidx}/env", "value": new_env})

    # 2) volumeMounts on the egress container
    mounts = egress.get("volumeMounts", []) + [
        {"name": "egress-inject", "mountPath": "/etc/egress-inject", "readOnly": True},
        {"name": "egress-ca", "mountPath": _MITM_CONFDIR, "readOnly": True},
        {"name": "egress-client-cert", "mountPath": "/etc/egress-client", "readOnly": True},
    ]
    ops.append({"op": "add", "path": f"/spec/containers/{eidx}/volumeMounts", "value": mounts})

    # 3) initContainer on the app pod: install CA public cert into system trust
    init = pod["spec"].get("initContainers", []) + [
        {
            "name": "egress-ca-trust",
            "image": egress_image,  # has update-ca-certificates + the tools
            "command": ["/bin/sh", "-c",
                        "cp /etc/egress-ca-pub/ca.pem /usr/local/share/ca-certificates/cubeplex-egress.crt "
                        "&& update-ca-certificates"],
            "volumeMounts": [
                {"name": "egress-ca-pub", "mountPath": "/etc/egress-ca-pub", "readOnly": True},
                {"name": "ca-trust", "mountPath": "/etc/ssl/certs"},
            ],
        }
    ]
    ops.append({"op": "add", "path": "/spec/initContainers", "value": init})

    # 4) app container mounts the shared trust dir (so the updated bundle is visible)
    aidx = _app_index(pod)
    app = pod["spec"]["containers"][aidx]
    app_mounts = app.get("volumeMounts", []) + [
        {"name": "ca-trust", "mountPath": "/etc/ssl/certs"},
    ]
    ops.append({"op": "add", "path": f"/spec/containers/{aidx}/volumeMounts", "value": app_mounts})

    # 5) pod-level volumes
    volumes = pod["spec"].get("volumes", []) + [
        {"name": "egress-inject", "configMap": {"name": "egress-inject-addon"}},
        {"name": "egress-ca", "secret": {"secretName": "egress-mitm-ca"}},      # cert+key for mitm confdir
        {"name": "egress-ca-pub", "secret": {"secretName": "egress-mitm-ca",
                                             "items": [{"key": "ca-cert.pem", "path": "ca.pem"}]}},
        {"name": "egress-client-cert", "secret": {"secretName": f"egress-client-{sandbox_id}"}},
        {"name": "ca-trust", "emptyDir": {}},
    ]
    ops.append({"op": "add", "path": "/spec/volumes", "value": volumes})
    return ops
```

> NOTE for implementer: the exact mitmproxy confdir filenames the egress image expects (`mitmproxy-ca.pem` private, `mitmproxy-ca-cert.pem` public) are documented in `components/egress/docs/mitmproxy-transparent.md`; map the `egress-mitm-ca` Secret keys to those paths via `items:` if the defaults differ. The per-sandbox client cert Secret (`egress-client-<sandbox_id>`) is created by the webhook in Task 3 before returning the patch.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd deploy/egress-bundle/webhook && uv run pytest tests/test_patch.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add deploy/egress-bundle/webhook/patch.py deploy/egress-bundle/webhook/tests/test_patch.py
git commit -m "feat(egress-bundle): sandbox pod JSON patch builder"
```

---

## Task 3: Admission webhook app

Ties Tasks 1+2 together: on `CREATE` pod, if `is_sandbox_pod`, mint a per-sandbox client cert, create the `egress-client-<sandbox_id>` Secret (owned by the pod for GC), and return the patch.

**Files:**
- Create: `deploy/egress-bundle/webhook/app.py`
- Create: `deploy/egress-bundle/webhook/Dockerfile`
- Test: covered by Task 1/2 unit tests + the cluster E2E (Task 6). Add a unit test that feeds a fake AdmissionReview and asserts an `AdmissionReview` response with a base64 patch when `is_sandbox_pod`, and `allowed: true` with no patch otherwise.

- [ ] **Step 1: Implement the webhook**

```python
# deploy/egress-bundle/webhook/app.py
"""Mutating admission webhook: patch OpenSandbox sandbox pods for secret injection."""

from __future__ import annotations

import base64
import json
import os

from fastapi import FastAPI, Request

from webhook.cert_minter import CertMinter, load_ca
from webhook.patch import build_pod_patch, is_sandbox_pod
from webhook import k8s_client  # thin wrapper around the k8s API (create Secret, owner ref)

EGRESS_IMAGE = os.environ["EGRESS_IMAGE"]
CERT_TTL_MIN = int(os.environ.get("CLIENT_CERT_TTL_MINUTES", "720"))
EGRESS_EXCHANGE_URL = os.environ["EGRESS_EXCHANGE_URL"]  # injected into the sidecar + used by inject.py
EXCHANGE_CA_PEM = open(os.environ["EXCHANGE_CA_PATH"], "rb").read()  # CA that signs the exchange server cert

app = FastAPI()
_ca = load_ca(
    open(os.environ["CA_KEY_PATH"], "rb").read(),
    open(os.environ["CA_CERT_PATH"], "rb").read(),
)
_minter = CertMinter(_ca)


def _allow(uid: str, patch_ops: list | None = None) -> dict:
    resp: dict = {"uid": uid, "allowed": True}
    if patch_ops:
        resp["patchType"] = "JSONPatch"
        resp["patch"] = base64.b64encode(json.dumps(patch_ops).encode()).decode()
    return {"apiVersion": "admission.k8s.io/v1", "kind": "AdmissionReview", "response": resp}


@app.post("/mutate")
async def mutate(request: Request) -> dict:
    review = await request.json()
    req = review["request"]
    uid = req["uid"]
    pod = req["object"]
    namespace = req.get("namespace") or pod.get("metadata", {}).get("namespace", "")

    if not is_sandbox_pod(pod, egress_image=EGRESS_IMAGE):
        return _allow(uid)  # fail-open for non-sandbox pods (do NOT patch)

    sandbox_id = next(
        o["name"] for o in pod["metadata"]["ownerReferences"] if o.get("kind") == "Sandbox"
    )
    key_pem, cert_pem = _minter.mint(sandbox_id=sandbox_id, ttl_minutes=CERT_TTL_MIN)
    # Per-sandbox client-cert Secret, owned by the Sandbox CR for auto-GC. It
    # carries tls.crt / tls.key (the client identity) AND exchange-ca.pem (the
    # CA the addon uses to verify the exchange server) — both land in the single
    # /etc/egress-client mount.
    k8s_client.create_client_cert_secret(
        namespace=namespace,
        name=f"egress-client-{sandbox_id}",
        data={
            "tls.crt": cert_pem,
            "tls.key": key_pem,
            "exchange-ca.pem": EXCHANGE_CA_PEM,
        },
        owner=pod.get("metadata", {}).get("ownerReferences", []),
    )
    ops = build_pod_patch(
        pod, sandbox_id=sandbox_id, egress_image=EGRESS_IMAGE, exchange_url=EGRESS_EXCHANGE_URL
    )
    return _allow(uid, ops)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
```

> NOTE for implementer: write the thin `webhook/k8s_client.py`
> (`create_client_cert_secret(*, namespace, name, data: dict[str, bytes], owner)`)
> using the official `kubernetes` async client; base64-encode each value into the
> Secret's `data`, and set `ownerReferences` to the Sandbox CR so the Secret is
> GC'd with the sandbox. Add a unit test feeding a synthetic AdmissionReview
> (mock `k8s_client.create_client_cert_secret`, and set `EGRESS_EXCHANGE_URL` /
> `EXCHANGE_CA_PATH` env in the test) asserting the response shape.

- [ ] **Step 2: Dockerfile** — minimal FastAPI image (uvicorn with TLS for the webhook server cert; webhook server TLS is separate from the mTLS-to-exchange concern and is the standard admission-webhook serving cert, provisioned in Task 5).

- [ ] **Step 3: Commit**

```bash
git add deploy/egress-bundle/webhook/app.py deploy/egress-bundle/webhook/Dockerfile deploy/egress-bundle/webhook/k8s_client.py deploy/egress-bundle/webhook/tests/
git commit -m "feat(egress-bundle): mutating admission webhook app"
```

---

## Task 4: `inject.py` mitmproxy addon

Runs in the sidecar after the stock system addon. Scans outbound header values for `cbxref_`, calls the exchange endpoint over mTLS (client cert mounted at `/etc/egress-client`), enforces `header_names`, substitutes, caches.

**Cross-plan change:** Plan 2's `ExchangeOut` must also return `header_names` so the addon can enforce it. Add `header_names: list[str] | None` to `ExchangeOut` and to `EgressExchangeService.exchange` return (return the matched binding's `header_names` alongside the secret).

**Files:**
- Modify (Plan 2): `cubeplex/api/routes/internal_egress.py`, `cubeplex/services/egress_exchange.py` — return `header_names`.
- Create: `deploy/egress-bundle/addon/inject.py`
- Test: `deploy/egress-bundle/addon/tests/test_inject.py`

- [ ] **Step 1: Make the exchange return header_names (Plan 2 files)**

Change `EgressExchangeService.exchange` to return `tuple[str, list[str] | None]` (secret, header_names of the matched binding); update `ExchangeOut` to `{secret: str, header_names: list[str] | None}` and the route to pass it through. Update the Plan 2 exchange-service tests to unpack the tuple.

- [ ] **Step 2: Write the failing addon test**

```python
# deploy/egress-bundle/addon/tests/test_inject.py
# The addon module exposes pure helpers so it is testable without a live mitmproxy.
from inject import should_substitute_header, scan_placeholders


def test_scan_finds_tokens():
    assert scan_placeholders("Bearer cbxref_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA") == [
        "cbxref_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    ]


def test_header_names_gate():
    assert should_substitute_header("Authorization", ["Authorization"])
    assert should_substitute_header("Authorization", None)  # null = any header
    assert not should_substitute_header("X-Other", ["Authorization"])
    # HTTP header names are case-insensitive
    assert should_substitute_header("authorization", ["Authorization"])
    assert should_substitute_header("AUTHORIZATION", ["authorization"])
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd deploy/egress-bundle/addon && uv run pytest tests/test_inject.py -v`
Expected: FAIL `ModuleNotFoundError: inject`

- [ ] **Step 4: Implement the addon**

```python
# deploy/egress-bundle/addon/inject.py
"""OpenSandbox egress addon: swap cbxref_ placeholders for real secrets.

Loaded after the bundled system addon. For each outbound request, scan header
values for cbxref_ tokens; for each, call the cubeplex exchange endpoint over
mTLS using the per-sandbox client cert, and replace the token with the returned
secret (only in headers allowed by the binding's header_names). Fails closed.
"""

from __future__ import annotations

import os
import re
import time

import httpx
from mitmproxy import http

PLACEHOLDER_RE = re.compile(r"cbxref_[A-Z2-7]{32}")
CLIENT_CERT = ("/etc/egress-client/tls.crt", "/etc/egress-client/tls.key")
EXCHANGE_CA = "/etc/egress-client/exchange-ca.pem"  # CA that signed the exchange server cert
_CACHE_TTL = 120.0

# cache: (placeholder, host) -> (secret, header_names, expires_at)
_cache: dict[tuple[str, str], tuple[str, list[str] | None, float]] = {}

# Lazily built so the pure helpers (scan/should_substitute) import without
# cluster env vars or mounted cert files (unit-testable).
_client: httpx.Client | None = None
_exchange_url: str | None = None


def _client_and_url() -> tuple[httpx.Client, str]:
    global _client, _exchange_url
    if _client is None:
        _exchange_url = os.environ["EGRESS_EXCHANGE_URL"]
        _client = httpx.Client(cert=CLIENT_CERT, verify=EXCHANGE_CA, timeout=5.0)
    assert _exchange_url is not None
    return _client, _exchange_url


def scan_placeholders(value: str) -> list[str]:
    return PLACEHOLDER_RE.findall(value)


def should_substitute_header(header_name: str, header_names: list[str] | None) -> bool:
    if header_names is None:
        return True
    # HTTP header names are case-insensitive; normalize both sides.
    return header_name.lower() in {h.lower() for h in header_names}


def _exchange(placeholder: str, host: str) -> tuple[str, list[str] | None] | None:
    now = time.monotonic()
    hit = _cache.get((placeholder, host))
    if hit and hit[2] > now:
        return hit[0], hit[1]
    client, url = _client_and_url()
    try:
        resp = client.post(url, json={"placeholder": placeholder, "host": host})
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None  # fail closed (denied / unknown / wrong host)
    data = resp.json()
    secret, header_names = data["secret"], data.get("header_names")
    _cache[(placeholder, host)] = (secret, header_names, now + _CACHE_TTL)
    return secret, header_names


def request(flow: http.HTTPFlow) -> None:
    host = (flow.request.host or "").lower()  # mitmproxy: verified upstream host
    for name in list(flow.request.headers.keys()):
        value = flow.request.headers[name]
        tokens = scan_placeholders(value)
        if not tokens:
            continue
        for token in tokens:
            result = _exchange(token, host)
            if result is None:
                continue  # fail closed: leave placeholder, do not guess
            secret, header_names = result
            if not should_substitute_header(name, header_names):
                continue
            value = value.replace(token, secret)
        flow.request.headers[name] = value
```

> NOTE for implementer: confirm `flow.request.host` is the TLS-verified upstream host in transparent mode (mitmproxy resolves original destination); if SNI/host handling differs, use the verified host attribute mitmproxy exposes (do NOT trust the `Host` header). Mount the exchange server's CA at `/etc/egress-client/exchange-ca.pem` (add to the client-cert Secret or a sibling). Never log header values (spec §6.2/§7); disable flow persistence in the launcher config.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd deploy/egress-bundle/addon && uv run pytest tests/test_inject.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add cubeplex/api/routes/internal_egress.py cubeplex/services/egress_exchange.py tests/unit/test_egress_exchange_service.py deploy/egress-bundle/addon/
git commit -m "feat(egress-bundle): inject.py addon + header_names in exchange response"
```

---

## Task 5: K8s manifests + fixed CA

**Files:**
- Create: `deploy/egress-bundle/scripts/gen-ca.sh`, `deploy/egress-bundle/k8s/*.yaml`

- [ ] **Step 1: CA generation script** — `gen-ca.sh` runs `generate_ca` once, writes the `egress-mitm-ca` Secret (`mitmproxy-ca.pem` private + `mitmproxy-ca-cert.pem`/`ca-cert.pem` public) in the sandbox namespace. Idempotent: refuses to overwrite an existing Secret. Document that the **same CA public cert** must be the trust the exchange endpoint serves its server cert under, and the client-cert CA the exchange trusts (see Plan 2 config).

- [ ] **Step 2: `addon-configmap.yaml`** — `egress-inject-addon` ConfigMap with `inject.py` from Task 4 (generate from the file; document the `kubectl create configmap --from-file` command so the ConfigMap stays in sync with the source).

- [ ] **Step 3: webhook Deployment + Service + MutatingWebhookConfiguration** — `mutatingwebhookconfiguration.yaml` scoped to the dedicated namespace via `namespaceSelector`, `rules` for `CREATE pods`, `failurePolicy: Ignore` (spec §6.1), `clientConfig.caBundle` = the webhook serving CA. Webhook serving TLS via cert-manager or a bootstrap cert (`webhook-tls.yaml`). The webhook Deployment must set env: `EGRESS_IMAGE` (the configured egress image to match on + use for the CA-trust initContainer), `EGRESS_EXCHANGE_URL` (the full exchange endpoint URL injected into each sidecar), `EXCHANGE_CA_PATH` + `CA_KEY_PATH` + `CA_CERT_PATH` (mounted from the `egress-mitm-ca` Secret and the exchange-server CA), and `CLIENT_CERT_TTL_MINUTES`. Mount the `egress-mitm-ca` Secret (for `CA_KEY_PATH`/`CA_CERT_PATH`) and the exchange-server CA (for `EXCHANGE_CA_PATH`) into the webhook pod.

- [ ] **Step 4: Validate manifests render**

Run: `kubectl apply --dry-run=client -f deploy/egress-bundle/k8s/`
Expected: all manifests validate.

- [ ] **Step 5: Commit**

```bash
git add deploy/egress-bundle/scripts/ deploy/egress-bundle/k8s/
git commit -m "feat(egress-bundle): CA gen + k8s manifests (webhook, configmap, mutatingwebhookconfig)"
```

---

## Task 6: Real-cluster E2E

The only cluster-dependent verification. Document as a runbook with explicit assertions (per the project's "real E2E, no fake sidecar" rule).

**Files:**
- Create: `tests/e2e_cluster/test_egress_injection_e2e.md`

- [ ] **Step 1: Deploy the bundle** to a test namespace on `kubernetes-admin@kubernetes`: CA Secret, addon ConfigMap, webhook (Deployment+Service+MutatingWebhookConfiguration), and point `EGRESS_EXCHANGE_URL` at cubeplex's exchange endpoint configured with `mode=mtls` trusting the egress CA.

- [ ] **Step 2: Seed** an org/workspace/user with a secret env-vault entry (`GITHUB_TOKEN` → `api.github.com`) via Plan 1's routes, and ensure the sandbox is created with the egress feature enabled (`sandbox.egress_exchange_host` set, Plan 2).

- [ ] **Step 3: Assertions (each a checkbox the operator verifies):**
  - [ ] (a) From inside the sandbox, `echo $GITHUB_TOKEN` shows a `cbxref_...` placeholder, **not** a real token.
  - [ ] (b) A tool call to `https://api.github.com` authenticated via `$GITHUB_TOKEN` **succeeds** (the addon swapped it).
  - [ ] (c) The real token is **absent** from the sandbox env/fs/process (`grep -r ghp_ /proc/*/environ` etc. find nothing).
  - [ ] (d) Sandbox code calling the exchange endpoint directly with the placeholder (no client cert) is **rejected** (401/403).
  - [ ] (e) A request to a **non-declared host** carrying the placeholder is **not** substituted.
  - [ ] (f) Killing the webhook (failurePolicy Ignore) still lets a new sandbox create; its tool calls fail auth (placeholder unsubstituted) — **no leak, no blocked creation**.
  - [ ] (g) Sidecar + app logs contain **no** plaintext token.

- [ ] **Step 4: Commit the runbook**

```bash
git add tests/e2e_cluster/test_egress_injection_e2e.md
git commit -m "docs(egress-bundle): real-cluster E2E runbook + assertions"
```

---

## Self-Review Checklist (completed by plan author)

- **Spec coverage:** §6.1 webhook narrow match + Ignore failurePolicy + per-sandbox mTLS mount (Tasks 2/3/5); §4.1/§6.6 mTLS identity carrying sandbox_id (Task 1, consumed by Plan 2's `MtlsAuthenticator`); §6.2 addon token scan + header_names enforcement + verified-host + fail-closed + no-log (Task 4); §6.7 fixed CA + initContainer trust install (Tasks 2/5); §6.8 cubeplex-owned bundle, OpenSandbox stock (whole plan).
- **Cross-plan consistency:** the addon calls Plan 2's `/api/v1/internal/egress/exchange` with `{placeholder, host}`; Task 4 Step 1 extends Plan 2's `ExchangeOut`/service to return `header_names` (the one backward edit into Plan 2 code). `cbxref_` regex matches Plan 2/Plan 1 (`cbxref_[A-Z2-7]{32}`). `MtlsAuthenticator` reads CN=sandbox_id, which Task 1 mints.
- **Flagged implementer confirmations (lookups, not gaps):** exact mitm confdir filenames the egress image expects; how mitmproxy exposes the verified upstream host in transparent mode; webhook serving-TLS provisioning (cert-manager vs bootstrap); the `kubernetes` async client calls in `k8s_client.py`.
- **Cluster-only:** Task 6 is a runbook, not automated CI — by design (no fake sidecar; real cluster per project discipline).

## Done

All three plans now cover the full feature: Plan 1 (env vault foundation) → Plan 2 (injection + exchange) → Plan 3 (K8s webhook + addon). Suggested build order is 1 → 2 → 3; Plans 1 and 2 are fully bare-testable, Plan 3 needs the cluster.
