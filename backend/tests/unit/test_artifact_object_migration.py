"""Contracts for migrating legacy artifact object keys."""

from scripts.dev.migrate_artifact_object_keys import missing_object_copies


def test_migration_only_copies_relative_paths_missing_from_canonical_storage() -> None:
    source_prefix = "artifacts/conv-owner/art-123/"
    destination_prefix = "artifacts/art-123/"

    assert missing_object_copies(
        source_prefix,
        destination_prefix,
        [
            f"{source_prefix}v1/index.html",
            f"{source_prefix}v1/style.css",
        ],
        [f"{destination_prefix}v1/index.html"],
    ) == [
        (
            f"{source_prefix}v1/style.css",
            f"{destination_prefix}v1/style.css",
        )
    ]
