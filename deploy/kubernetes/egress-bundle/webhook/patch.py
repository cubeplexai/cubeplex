# deploy/kubernetes/egress-bundle/webhook/patch.py
"""Build the JSON Patch applied to a sandbox pod at admission.

Narrow match (spec §6.1): ownerReference to an OpenSandbox CR (BatchSandbox or
Sandbox) + an `egress` container with the expected image. Anything else is not
patched (fail closed).
"""

from __future__ import annotations

from typing import Any

_MITM_CONFDIR = "/var/lib/mitmproxy/.mitmproxy"
_ADDON_PATH = "/etc/egress-inject/inject.py"

# OpenSandbox owns sandbox pods via a BatchSandbox CR (the per-sandbox path also
# uses Sandbox); both live under the sandbox.opensandbox.io API group.
_SANDBOX_OWNER_KINDS = frozenset({"BatchSandbox", "Sandbox"})


def _is_sandbox_owner(o: dict[str, Any]) -> bool:
    return o.get("apiVersion", "").startswith("sandbox.opensandbox.io/") and (
        o.get("kind") in _SANDBOX_OWNER_KINDS
    )


def sandbox_id_from_owners(pod: dict[str, Any]) -> str:
    """The sandbox id is the name of the owning OpenSandbox CR.

    Matches the same owner predicate as is_sandbox_pod so a crafted extra
    ownerRef can't supply a foreign CN. Raises StopIteration only if called on a
    pod that is_sandbox_pod already rejected.
    """
    return next(o["name"] for o in pod["metadata"]["ownerReferences"] if _is_sandbox_owner(o))


def is_sandbox_pod(pod: dict[str, Any], *, egress_image: str) -> bool:
    owners = pod.get("metadata", {}).get("ownerReferences", [])
    owned_by_sandbox = any(_is_sandbox_owner(o) for o in owners)
    containers = pod.get("spec", {}).get("containers", [])
    has_egress = any(c.get("name") == "egress" and c.get("image") == egress_image for c in containers)
    return owned_by_sandbox and has_egress


def _egress_index(pod: dict[str, Any]) -> int:
    for i, c in enumerate(pod["spec"]["containers"]):
        if c.get("name") == "egress":
            return i
    raise ValueError("no egress container")


def _app_indices(pod: dict[str, Any]) -> list[int]:
    """Return indices of all non-egress containers (may be empty)."""
    return [i for i, c in enumerate(pod["spec"]["containers"]) if c.get("name") != "egress"]


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

    # 2) volumeMounts on the egress container.
    # The mitmproxy confdir must be WRITABLE: mitmproxy generates dhparam and
    # cached leaf certs into it at startup, so mounting the CA secret there
    # read-only makes mitmdump crash ("Read-only file system: mitmproxy-dhparam.pem")
    # and transparent intercept never comes up. Use a writable emptyDir confdir
    # seeded by an initContainer (op 3) instead.
    mounts = egress.get("volumeMounts", []) + [
        {"name": "egress-inject", "mountPath": "/etc/egress-inject", "readOnly": True},
        {"name": "egress-mitm-confdir", "mountPath": _MITM_CONFDIR},
        {"name": "egress-client-cert", "mountPath": "/etc/egress-client", "readOnly": True},
    ]
    ops.append({"op": "add", "path": f"/spec/containers/{eidx}/volumeMounts", "value": mounts})

    # 3) initContainers:
    #   a) egress-mitm-confdir — seed the writable confdir with the CA. mitmproxy
    #      expects mitmproxy-ca.pem to be the private key + CA cert concatenated;
    #      the secret stores them split (mitmproxy-ca.pem=key, mitmproxy-ca-cert.pem=cert).
    #   b) egress-ca-trust — install the CA public cert into the app system trust.
    init = pod["spec"].get("initContainers", []) + [
        {
            "name": "egress-mitm-confdir",
            "image": egress_image,
            "command": ["/bin/sh", "-c",
                        "cat /etc/egress-ca-src/mitmproxy-ca.pem /etc/egress-ca-src/mitmproxy-ca-cert.pem "
                        f"> {_MITM_CONFDIR}/mitmproxy-ca.pem "
                        f"&& cp /etc/egress-ca-src/mitmproxy-ca-cert.pem {_MITM_CONFDIR}/mitmproxy-ca-cert.pem"],
            "volumeMounts": [
                {"name": "egress-ca", "mountPath": "/etc/egress-ca-src", "readOnly": True},
                {"name": "egress-mitm-confdir", "mountPath": _MITM_CONFDIR},
            ],
        },
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
        },
    ]
    ops.append({"op": "add", "path": "/spec/initContainers", "value": init})

    # 4) ALL non-egress containers mount the shared trust dir (so the updated
    # bundle is visible in each app container). Pods with no app containers are
    # handled gracefully: this loop simply emits no ops.
    #
    # Skip any container that already mounts /etc/ssl/certs: Kubernetes rejects a
    # pod with duplicate volumeMounts.mountPath, so re-adding it would make
    # admission succeed but pod creation fail (Codex P2).
    for aidx in _app_indices(pod):
        app_c = pod["spec"]["containers"][aidx]
        existing = app_c.get("volumeMounts", [])
        if any(m.get("mountPath") == "/etc/ssl/certs" for m in existing):
            continue
        app_mounts = existing + [{"name": "ca-trust", "mountPath": "/etc/ssl/certs"}]
        ops.append({"op": "add", "path": f"/spec/containers/{aidx}/volumeMounts", "value": app_mounts})

    # 5) pod-level volumes
    volumes = pod["spec"].get("volumes", []) + [
        {"name": "egress-inject", "configMap": {"name": "egress-inject-addon"}},
        {"name": "egress-ca", "secret": {"secretName": "egress-mitm-ca"}},  # CA key+cert source (seeds confdir)
        {"name": "egress-mitm-confdir", "emptyDir": {}},  # writable mitmproxy confdir
        {"name": "egress-ca-pub", "secret": {"secretName": "egress-mitm-ca",
                                             "items": [{"key": "ca-cert.pem", "path": "ca.pem"}]}},
        {"name": "egress-client-cert", "secret": {"secretName": f"egress-client-{sandbox_id}"}},
        {"name": "ca-trust", "emptyDir": {}},
    ]
    ops.append({"op": "add", "path": "/spec/volumes", "value": volumes})
    return ops
