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
