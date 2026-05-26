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
                        "cp /etc/egress-ca-pub/ca.pem /usr/local/share/ca-certificates/cubebox-egress.crt "
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
