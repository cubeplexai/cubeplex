# deploy/kubernetes/egress-bundle/webhook/tests/test_patch.py
import pytest

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

# Pod with two non-egress containers to verify I4 (all app containers patched).
MULTI_APP_POD = {
    "metadata": {
        "ownerReferences": [
            {"apiVersion": "sandbox.opensandbox.io/v1alpha1", "kind": "Sandbox", "name": "sbx-2"}
        ],
        "labels": {},
    },
    "spec": {
        "containers": [
            {"name": "app-a", "image": "py:3.13"},
            {"name": "app-b", "image": "node:22"},
            {"name": "egress", "image": EGRESS_IMAGE},
        ],
        "volumes": [],
    },
}

# Pod that passes is_sandbox_pod but has only the egress container (degenerate).
EGRESS_ONLY_POD = {
    "metadata": {
        "ownerReferences": [
            {"apiVersion": "sandbox.opensandbox.io/v1alpha1", "kind": "Sandbox", "name": "sbx-3"}
        ],
        "labels": {},
    },
    "spec": {
        "containers": [
            {"name": "egress", "image": EGRESS_IMAGE},
        ],
        "volumes": [],
    },
}


def test_recognizes_sandbox_pod():
    assert is_sandbox_pod(POD, egress_image=EGRESS_IMAGE)
    assert not is_sandbox_pod({"metadata": {}, "spec": {"containers": []}}, egress_image=EGRESS_IMAGE)


def test_recognizes_batchsandbox_owned_pod():
    """Real OpenSandbox pods are owned by a BatchSandbox CR, not Sandbox."""
    pod = {
        "metadata": {
            "ownerReferences": [
                {
                    "apiVersion": "sandbox.opensandbox.io/v1alpha1",
                    "kind": "BatchSandbox",
                    "name": "54724310-91d0-4703-8062-943c312df4da",
                }
            ],
            "labels": {},
        },
        "spec": {"containers": [{"name": "egress", "image": EGRESS_IMAGE}]},
    }
    assert is_sandbox_pod(pod, egress_image=EGRESS_IMAGE)


def test_sandbox_pod_wrong_apiversion_not_recognized():
    """M2: kind=Sandbox but wrong apiVersion must not be treated as a sandbox pod."""
    pod = {
        "metadata": {
            "ownerReferences": [
                {"apiVersion": "other.io/v1", "kind": "Sandbox", "name": "sbx-x"}
            ],
            "labels": {},
        },
        "spec": {
            "containers": [{"name": "egress", "image": EGRESS_IMAGE}],
        },
    }
    assert not is_sandbox_pod(pod, egress_image=EGRESS_IMAGE)


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
    # ca-trust mount added to the non-egress app container (index 0)
    assert any(p == "/spec/containers/0/volumeMounts" for p in paths)
    # required env + addon mount + exchange URL present
    blob = str(ops)
    assert "OPENSANDBOX_EGRESS_MITMPROXY_TRANSPARENT" in blob
    assert "EGRESS_EXCHANGE_URL" in blob
    assert "egress-inject" in blob  # addon configmap mount


def test_patch_mounts_ca_trust_on_all_app_containers():
    """I4: every non-egress container gets the ca-trust volumeMount."""
    ops = build_pod_patch(
        MULTI_APP_POD, sandbox_id="sbx-2", egress_image=EGRESS_IMAGE,
        exchange_url="https://egress-exchange.internal/api/v1/internal/egress/exchange",
    )
    paths = [op["path"] for op in ops]
    # app-a is at index 0, app-b at index 1; both must get a volumeMounts op
    assert "/spec/containers/0/volumeMounts" in paths
    assert "/spec/containers/1/volumeMounts" in paths
    # egress (index 2) volumeMounts op is also present but for the egress container
    ca_trust_mounts = [
        op for op in ops
        if "volumeMounts" in op["path"] and op["path"].startswith("/spec/containers/")
        and any(m.get("name") == "ca-trust" for m in op.get("value", []))
    ]
    assert len(ca_trust_mounts) == 2  # one per app container


def test_patch_skips_app_container_already_mounting_ssl_certs():
    """P2: a container already mounting /etc/ssl/certs must NOT get a duplicate
    ca-trust mount (Kubernetes rejects duplicate mountPath)."""
    pod = {
        "metadata": {
            "ownerReferences": [
                {"apiVersion": "sandbox.opensandbox.io/v1alpha1", "kind": "Sandbox", "name": "sbx-4"}
            ],
            "labels": {},
        },
        "spec": {
            "containers": [
                {
                    "name": "sandbox",
                    "image": "py:3.13",
                    "volumeMounts": [{"name": "existing-certs", "mountPath": "/etc/ssl/certs"}],
                },
                {"name": "egress", "image": EGRESS_IMAGE},
            ],
            "volumes": [],
        },
    }
    ops = build_pod_patch(
        pod, sandbox_id="sbx-4", egress_image=EGRESS_IMAGE,
        exchange_url="https://egress-exchange.internal/api/v1/internal/egress/exchange",
    )
    # No volumeMounts op should target the app container (index 0) — it already
    # mounts /etc/ssl/certs, so adding ca-trust there would duplicate the path.
    assert not any(op["path"] == "/spec/containers/0/volumeMounts" for op in ops)


def test_patch_egress_only_pod_no_app_container_mounts():
    """I3: a sandbox pod with only the egress container produces no app volumeMount ops."""
    ops = build_pod_patch(
        EGRESS_ONLY_POD, sandbox_id="sbx-3", egress_image=EGRESS_IMAGE,
        exchange_url="https://egress-exchange.internal/api/v1/internal/egress/exchange",
    )
    paths = [op["path"] for op in ops]
    # No ValueError raised; ops still include egress env/mounts, initContainer, volumes
    assert any("/spec/initContainers" in p for p in paths)
    assert any("/spec/volumes" in p for p in paths)
    # No ca-trust mount targeting an app container (there are none)
    ca_trust_app_mounts = [
        op for op in ops
        if "volumeMounts" in op["path"] and op["path"].startswith("/spec/containers/")
        and any(m.get("name") == "ca-trust" for m in op.get("value", []))
    ]
    assert ca_trust_app_mounts == []
