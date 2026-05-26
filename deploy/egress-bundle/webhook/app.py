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

    sandbox_id = next(
        o["name"] for o in pod["metadata"]["ownerReferences"] if o.get("kind") == "Sandbox"
    )
    key_pem, cert_pem = _minter.mint(sandbox_id=sandbox_id, ttl_minutes=CERT_TTL_MIN)
    # Per-sandbox client-cert Secret, owned by the Sandbox CR for auto-GC. It
    # carries tls.crt / tls.key (the client identity) AND exchange-ca.pem (the
    # CA the addon uses to verify the exchange server) — both land in the single
    # /etc/egress-client mount.
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
    ops = build_pod_patch(
        pod, sandbox_id=sandbox_id, egress_image=EGRESS_IMAGE, exchange_url=EGRESS_EXCHANGE_URL
    )
    return _allow(uid, ops)


@app.get("/healthz")
async def healthz() -> dict:  # type: ignore[type-arg]
    return {"ok": True}
