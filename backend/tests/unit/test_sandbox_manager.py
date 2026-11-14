from typing import Any
from unittest.mock import MagicMock

import pytest

from cubeplex.sandbox.manager import SandboxManager


@pytest.fixture(autouse=True)
def _sandbox_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give manager-only tests the required OpenSandbox startup settings."""
    from cubeplex.config import config

    original_get = config.get
    required_values = {
        "sandbox.domain": "opensandbox.example:8090",
        "sandbox.image": "registry.example/cubeplex-sandbox:latest",
        "sandbox.api_key": "test-key",
    }

    def get(key: str, default: object = None, **kwargs: object) -> object:
        if key.startswith("sandbox."):
            return required_values.get(key, default)
        return original_get(key, default, **kwargs)

    monkeypatch.setattr(config, "get", get)


def test_build_user_volume_uses_stable_non_colliding_name(mock_encryption_backend):
    from cubeplex.sandbox.provider import RunIdentity, SandboxCreateSpec
    from cubeplex.sandbox.providers.opensandbox import OpenSandboxProvider

    manager = SandboxManager(MagicMock(), mock_encryption_backend)
    provider = OpenSandboxProvider()

    def _pvc_for(user_id: str, ws: str) -> Any:
        key = manager._build_user_volume_key(ws, "user", user_id)
        spec = SandboxCreateSpec(
            volume_key=key,
            volume_mount_path=manager._volume_mount_path,
            storage=None,
            image="ubuntu:22.04",
            workdir="/workspace",
            ttl_seconds=600,
            create_timeout_seconds=300,
            ready_timeout_seconds=60,
            request_timeout_seconds=1860,
            run_identity=RunIdentity(),
            env={},
            resource_cpu="100m",
            resource_memory="100Mi",
            network_policy=None,
        )
        vols = provider._build_volumes(spec)
        assert vols is not None
        return vols[0]

    first = _pvc_for("019d85a5-5bb7-7130-a4d2-a73734fa2dc6", "ws-A")
    second = _pvc_for("019d85a5-be69-76e2-b8cb-814a9440e4b0", "ws-A")
    repeated = _pvc_for("019d85a5-5bb7-7130-a4d2-a73734fa2dc6", "ws-A")
    other_ws = _pvc_for("019d85a5-5bb7-7130-a4d2-a73734fa2dc6", "ws-B")

    assert first.pvc is not None
    assert second.pvc is not None
    assert repeated.pvc is not None
    assert other_ws.pvc is not None
    assert first.pvc.claim_name == repeated.pvc.claim_name
    assert first.pvc.claim_name != second.pvc.claim_name
    # Same user in a different workspace gets a distinct PVC — the ownership
    # boundary the partial unique index enforces in the DB.
    assert first.pvc.claim_name != other_ws.pvc.claim_name
    assert first.pvc.claim_name.startswith("cubeplex-user-")


def test_create_timeout_overrides_request_timeout_for_create_only(mock_encryption_backend):
    from cubeplex.sandbox.providers.opensandbox import OpenSandboxProvider

    manager = SandboxManager(MagicMock(), mock_encryption_backend)
    provider = OpenSandboxProvider()

    default = provider._build_connection_config(request_timeout_seconds=manager._request_timeout)
    create = provider._build_connection_config(request_timeout_seconds=manager._create_timeout)

    # Ordinary commands use request_timeout (must cover max execute 1800s).
    # Create still passes create_timeout so a cold image pull has its own budget.
    assert default.request_timeout.total_seconds() == manager._request_timeout
    assert create.request_timeout.total_seconds() == manager._create_timeout
    assert manager._request_timeout >= 1800
    assert manager._create_timeout != manager._request_timeout
