"""Contracts for conversation-independent artifact object keys."""

from cubeplex.objectstore.artifact_paths import (
    artifact_file_key,
    artifact_file_key_candidates,
    artifact_root_prefix,
    artifact_staging_prefix,
    artifact_version_prefix,
    artifact_version_prefix_candidates,
)


def test_artifact_paths_depend_only_on_artifact_and_version() -> None:
    assert artifact_root_prefix("art-123") == "artifacts/art-123/"
    assert artifact_version_prefix("art-123", 4) == "artifacts/art-123/v4/"
    assert artifact_file_key("art-123", 4, "slides/index.html") == (
        "artifacts/art-123/v4/slides/index.html"
    )
    assert artifact_staging_prefix("art-123", "nonce") == "artifacts/art-123/staging/nonce/"


def test_read_candidates_fall_back_to_the_owner_conversation_legacy_key() -> None:
    assert artifact_version_prefix_candidates("art-123", 4, "conv-owner") == (
        "artifacts/art-123/v4/",
        "artifacts/conv-owner/art-123/v4/",
    )
    assert artifact_file_key_candidates("art-123", 4, "slides/index.html", "conv-owner") == (
        "artifacts/art-123/v4/slides/index.html",
        "artifacts/conv-owner/art-123/v4/slides/index.html",
    )
