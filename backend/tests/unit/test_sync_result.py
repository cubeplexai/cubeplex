"""Unit tests for SyncResult dataclass."""

from datetime import UTC, datetime

import pytest

from cubebox.sandbox.sync_result import SyncResult


def test_noop_default_values():
    now = datetime.now(UTC)
    r = SyncResult(started_at=now, finished_at=now, status="noop")
    assert r.status == "noop"
    assert r.n_pushed == 0
    assert r.n_removed == 0
    assert r.tar_size_bytes is None
    assert r.manifest is None
    assert r.manifest_hash is None
    assert r.skills_count == 0
    assert r.error_type is None
    assert r.error_message is None


def test_success_with_manifest():
    now = datetime.now(UTC)
    manifest = {"schema_version": 1, "skills": {"docx": {"version": "1.0.0"}}}
    r = SyncResult(
        started_at=now,
        finished_at=now,
        status="success",
        n_pushed=1,
        n_removed=0,
        tar_size_bytes=1024,
        manifest=manifest,
        manifest_hash="sha256:abc",
        skills_count=1,
    )
    assert r.status == "success"
    assert r.n_pushed == 1
    assert r.manifest is manifest
    assert r.skills_count == 1


def test_failed_with_error():
    now = datetime.now(UTC)
    r = SyncResult(
        started_at=now,
        finished_at=now,
        status="failed",
        error_type="SandboxError",
        error_message="tar -xzf exited 1",
    )
    assert r.status == "failed"
    assert r.error_type == "SandboxError"
    assert r.error_message == "tar -xzf exited 1"


def test_frozen():
    from dataclasses import FrozenInstanceError

    now = datetime.now(UTC)
    r = SyncResult(started_at=now, finished_at=now, status="noop")
    with pytest.raises(FrozenInstanceError):
        r.status = "success"  # type: ignore[misc]
