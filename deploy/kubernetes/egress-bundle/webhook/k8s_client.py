"""Thin async wrapper around the Kubernetes API for the admission webhook.

Only the calls needed by app.py are here: creating the per-sandbox client-cert
Secret. The kubernetes-asyncio library is used so the webhook's FastAPI event
loop is never blocked.

The kubernetes_asyncio import is deferred to the function body so this module
is importable in unit tests (where kubernetes_asyncio is not installed) when
create_client_cert_secret is mocked before it is called.
"""

from __future__ import annotations

import base64
from typing import Any

# Tracks whether the in-cluster config has been loaded. Loading it on every
# admission call resets the kubernetes_asyncio global Configuration singleton
# and races under concurrent requests, so we do it exactly once.
_configured = False


async def _ensure_configured() -> None:
    global _configured
    if not _configured:
        from kubernetes_asyncio import config  # noqa: PLC0415

        # load_incluster_config() is synchronous in kubernetes_asyncio (it only
        # reads the SA token/cert files and sets the global config); only
        # load_kube_config() is a coroutine. Do NOT await this.
        config.load_incluster_config()
        _configured = True


async def create_client_cert_secret(
    *,
    namespace: str,
    name: str,
    data: dict[str, bytes],
    owner: list[dict[str, Any]],
) -> None:
    """Create (or replace) a Kubernetes Secret holding per-sandbox mTLS credentials.

    Args:
        namespace: Kubernetes namespace for the Secret.
        name: Secret name (e.g. ``egress-client-<sandbox_id>``).
        data: Mapping of key → raw bytes; values are base64-encoded into the
              Secret's ``data`` field automatically.
        owner: ownerReferences list from the pod metadata (used as-is so the
               Secret is GC'd when the Sandbox CR is deleted).
    """
    # Deferred import: kubernetes_asyncio is only present in the cluster image,
    # not in the unit-test environment. The function is always mocked in tests.
    from kubernetes_asyncio import client  # noqa: PLC0415
    from kubernetes_asyncio.client import ApiClient  # noqa: PLC0415

    await _ensure_configured()

    encoded = {k: base64.b64encode(v).decode() for k, v in data.items()}

    secret = client.V1Secret(
        api_version="v1",
        kind="Secret",
        metadata=client.V1ObjectMeta(
            name=name,
            namespace=namespace,
            owner_references=[
                client.V1OwnerReference(
                    api_version=ref["apiVersion"],
                    kind=ref["kind"],
                    name=ref["name"],
                    uid=ref["uid"],
                    # No block_owner_deletion: it would require the webhook SA to
                    # also have `update` on the owner's finalizers subresource,
                    # which our Role does not grant. Plain ownership still GCs the
                    # Secret when the Sandbox CR is deleted.
                    controller=False,
                )
                for ref in owner
            ],
        ),
        type="kubernetes.io/tls",
        data=encoded,
    )

    api_client = ApiClient()
    async with api_client:
        core_api = client.CoreV1Api(api_client)
        try:
            await core_api.create_namespaced_secret(namespace=namespace, body=secret)
        except client.ApiException as exc:
            if exc.status == 409:
                # Secret already exists (e.g. webhook retried); replace it so the
                # cert is always fresh for this pod admission.
                await core_api.replace_namespaced_secret(
                    name=name, namespace=namespace, body=secret
                )
            else:
                raise
