"""Mutating admission webhook: patch OpenSandbox sandbox pods for secret injection."""

from __future__ import annotations

import base64
import json
import logging
import os

from fastapi import FastAPI, Request

from webhook.cert_minter import CertMinter, load_ca
from webhook.patch import build_pod_patch, is_sandbox_pod, sandbox_id_from_owners
from webhook import k8s_client  # thin wrapper around the k8s API (create Secret, owner ref)

logger = logging.getLogger(__name__)

EGRESS_IMAGE = os.environ["EGRESS_IMAGE"]
CERT_TTL_MIN = int(os.environ.get("CLIENT_CERT_TTL_MINUTES", "720"))
EGRESS_EXCHANGE_URL = os.environ["EGRESS_EXCHANGE_URL"]  # injected into the sidecar + used by inject.py


def _read_env_file(env_var: str) -> bytes:
    path = os.environ.get(env_var)
    if not path:
        raise RuntimeError(f"Required env var {env_var!r} is not set")
    try:
        return open(path, "rb").read()
    except OSError as exc:
        raise RuntimeError(f"Cannot read file for {env_var!r} ({path!r}): {exc}") from exc


EXCHANGE_CA_PEM = _read_env_file("EXCHANGE_CA_PATH")  # CA that signs the exchange server cert

app = FastAPI()
_ca = load_ca(
    _read_env_file("CA_KEY_PATH"),
    _read_env_file("CA_CERT_PATH"),
)
_minter = CertMinter(_ca)


def _allow(uid: str, patch_ops: list | None = None) -> dict:  # type: ignore[type-arg]
    resp: dict = {"uid": uid, "allowed": True}  # type: ignore[type-arg]
    if patch_ops:
        resp["patchType"] = "JSONPatch"
        resp["patch"] = base64.b64encode(json.dumps(patch_ops).encode()).decode()
    return {"apiVersion": "admission.k8s.io/v1", "kind": "AdmissionReview", "response": resp}


@app.post("/mutate")
async def mutate(request: Request) -> dict:  # type: ignore[type-arg]
    review = await request.json()
    req = review["request"]
    uid = req["uid"]
    pod = req["object"]
    namespace = req.get("namespace") or pod.get("metadata", {}).get("namespace", "")

    if not is_sandbox_pod(pod, egress_image=EGRESS_IMAGE):
        return _allow(uid)  # fail-open for non-sandbox pods (do NOT patch)

    # Align with is_sandbox_pod: require the opensandbox.io apiVersion + a sandbox
    # owner kind so a crafted second ownerRef can't supply a foreign CN.
    sandbox_id = sandbox_id_from_owners(pod)
    key_pem, cert_pem = _minter.mint(sandbox_id=sandbox_id, ttl_minutes=CERT_TTL_MIN)
    # Honor the declared `sideEffects: NoneOnDryRun`: on a dry-run admission
    # (kubectl apply --dry-run=server) do NOT create the Secret — just return the
    # patch (which is never applied for real on dry-run). Otherwise every dry-run
    # would leave a spurious per-sandbox Secret behind.
    if not bool(req.get("dryRun", False)):
        # Per-sandbox client-cert Secret, owned by the Sandbox CR for auto-GC. It
        # carries tls.crt / tls.key (the client identity) AND exchange-ca.pem (the
        # CA the addon uses to verify the exchange server) — both land in the
        # single /etc/egress-client mount.
        await k8s_client.create_client_cert_secret(
            namespace=namespace,
            name=f"egress-client-{sandbox_id}",
            data={
                "tls.crt": cert_pem,
                "tls.key": key_pem,
                "exchange-ca.pem": EXCHANGE_CA_PEM,
            },
            owner=pod.get("metadata", {}).get("ownerReferences", []),
        )
    try:
        ops = build_pod_patch(
            pod, sandbox_id=sandbox_id, egress_image=EGRESS_IMAGE, exchange_url=EGRESS_EXCHANGE_URL
        )
    except ValueError:
        # Degenerate pod: passed is_sandbox_pod but has no non-egress container.
        # Allow without a patch rather than returning 500 (fail-open is safer
        # than blocking a pod that Kubernetes already accepted).
        logger.warning("sandbox pod %s has no app container; admitting without patch", sandbox_id)
        return _allow(uid)
    return _allow(uid, ops)


@app.get("/healthz")
async def healthz() -> dict:  # type: ignore[type-arg]
    return {"ok": True}
