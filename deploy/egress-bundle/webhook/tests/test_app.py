"""Unit tests for the admission webhook app.

k8s_client.create_client_cert_secret is mocked — no real cluster needed.
The CA env vars point at a temporary self-generated CA so app module-level
code (which reads the files at import time via the env vars) works correctly.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import tempfile
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from webhook.cert_minter import generate_ca

EGRESS_IMAGE = "registry/opensandbox/egress:v1.0.12"
EXCHANGE_URL = "https://egress-exchange.internal/api/v1/internal/egress/exchange"

SANDBOX_POD = {
    "metadata": {
        "namespace": "opensandbox",
        "ownerReferences": [
            {
                "apiVersion": "sandbox.opensandbox.io/v1alpha1",
                "kind": "Sandbox",
                "name": "sbx-1",
                "uid": "aaaa-bbbb-cccc",
            }
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

NON_SANDBOX_POD = {
    "metadata": {"namespace": "default", "ownerReferences": [], "labels": {}},
    "spec": {
        "containers": [{"name": "web", "image": "nginx:latest"}],
        "volumes": [],
    },
}

# Passes is_sandbox_pod but has no non-egress container — degenerate case (I3).
EGRESS_ONLY_SANDBOX_POD = {
    "metadata": {
        "namespace": "opensandbox",
        "ownerReferences": [
            {
                "apiVersion": "sandbox.opensandbox.io/v1alpha1",
                "kind": "Sandbox",
                "name": "sbx-degenerate",
                "uid": "dddd-eeee-ffff",
            }
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


def _make_review(pod: dict, uid: str = "test-uid-1") -> dict:  # type: ignore[type-arg]
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": uid,
            "namespace": pod.get("metadata", {}).get("namespace", ""),
            "object": pod,
        },
    }


@pytest.fixture(scope="module")
def ca_tmp(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Write a temporary CA key+cert to files; return the directory."""
    d = tmp_path_factory.mktemp("ca")
    key_pem, cert_pem = generate_ca("test-egress-ca")
    (d / "ca.key").write_bytes(key_pem)
    (d / "ca.crt").write_bytes(cert_pem)
    # exchange CA is just a copy of the same cert for test purposes
    (d / "exchange-ca.pem").write_bytes(cert_pem)
    return d


@pytest.fixture(scope="module")
def webhook_app(ca_tmp: pathlib.Path):  # type: ignore[no-untyped-def]
    """Import webhook.app with the required env vars set."""
    env_patch = {
        "EGRESS_IMAGE": EGRESS_IMAGE,
        "EGRESS_EXCHANGE_URL": EXCHANGE_URL,
        "CA_KEY_PATH": str(ca_tmp / "ca.key"),
        "CA_CERT_PATH": str(ca_tmp / "ca.crt"),
        "EXCHANGE_CA_PATH": str(ca_tmp / "exchange-ca.pem"),
        "CLIENT_CERT_TTL_MINUTES": "60",
    }
    with patch.dict(os.environ, env_patch):
        # Import fresh — module-level code reads env vars at import time.
        import importlib
        import webhook.app as _app_module
        importlib.reload(_app_module)
        yield _app_module.app


@pytest_asyncio.fixture
async def client(webhook_app):  # type: ignore[no-untyped-def]
    async with AsyncClient(
        transport=ASGITransport(app=webhook_app), base_url="https://webhook"
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_sandbox_pod_returns_jsonpatch(client: AsyncClient) -> None:
    """A sandbox pod → allowed=True + a base64 JSONPatch."""
    with patch(
        "webhook.app.k8s_client.create_client_cert_secret",
        new_callable=AsyncMock,
    ):
        resp = await client.post("/mutate", json=_make_review(SANDBOX_POD))

    assert resp.status_code == 200
    body = resp.json()
    assert body["response"]["allowed"] is True
    assert body["response"]["patchType"] == "JSONPatch"
    # The patch is valid base64 JSON.
    patch_bytes = base64.b64decode(body["response"]["patch"])
    ops = json.loads(patch_bytes)
    assert isinstance(ops, list)
    assert len(ops) > 0
    # Must include egress env and volumes.
    paths = [op["path"] for op in ops]
    assert any("/env" in p for p in paths)
    assert any("/volumes" in p for p in paths)


@pytest.mark.asyncio
async def test_non_sandbox_pod_is_allowed_no_patch(client: AsyncClient) -> None:
    """A non-sandbox pod → allowed=True, no patchType / patch."""
    resp = await client.post("/mutate", json=_make_review(NON_SANDBOX_POD))
    assert resp.status_code == 200
    body = resp.json()
    assert body["response"]["allowed"] is True
    assert "patch" not in body["response"]
    assert "patchType" not in body["response"]


@pytest.mark.asyncio
async def test_dry_run_does_not_create_secret(client: AsyncClient) -> None:
    """sideEffects: NoneOnDryRun — a dry-run admission must NOT create the Secret,
    but still returns the patch (never applied for real on dry-run)."""
    review = _make_review(SANDBOX_POD, uid="dry-1")
    review["request"]["dryRun"] = True
    with patch(
        "webhook.app.k8s_client.create_client_cert_secret",
        new_callable=AsyncMock,
    ) as mock_create:
        resp = await client.post("/mutate", json=review)

    assert resp.status_code == 200
    assert resp.json()["response"]["allowed"] is True
    mock_create.assert_not_awaited()  # no side effect on dry-run


@pytest.mark.asyncio
async def test_healthz(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_egress_only_sandbox_pod_allowed_no_500(client: AsyncClient) -> None:
    """I3: sandbox pod with only the egress container → allowed=True, no 500.

    build_pod_patch handles the no-app-container case without raising ValueError:
    it patches the egress container and emits volumes/initContainers but skips
    the app-container ca-trust volumeMount (there are no app containers). The
    response carries a JSONPatch for the egress-side ops; the key invariant is
    that no exception propagates and the pod is admitted.
    """
    with patch(
        "webhook.app.k8s_client.create_client_cert_secret",
        new_callable=AsyncMock,
    ):
        resp = await client.post("/mutate", json=_make_review(EGRESS_ONLY_SANDBOX_POD))

    assert resp.status_code == 200
    body = resp.json()
    assert body["response"]["allowed"] is True
    # A patch is returned for the egress-side env/mounts/volumes; what must
    # NOT happen is a 500 or a ca-trust volumeMount targeting a non-existent
    # app container index.
    if "patch" in body["response"]:
        import base64, json as _json
        ops = _json.loads(base64.b64decode(body["response"]["patch"]))
        # No op should add a ca-trust mount to any container index
        # (there are no app containers — only egress at index 0).
        for op in ops:
            path = op.get("path", "")
            if "volumeMounts" in path and path.startswith("/spec/containers/"):
                mounts = op.get("value", [])
                assert not any(m.get("name") == "ca-trust" for m in mounts), (
                    f"Unexpected ca-trust mount for egress-only pod at {path}"
                )
