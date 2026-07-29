"""Canonical object-store paths for versioned artifacts."""

from typing import Protocol


class ObjectLister(Protocol):
    async def list_objects(self, prefix: str) -> list[str]: ...


def artifact_root_prefix(artifact_id: str) -> str:
    return f"artifacts/{artifact_id}/"


def artifact_version_prefix(artifact_id: str, version: int) -> str:
    return f"{artifact_root_prefix(artifact_id)}v{version}/"


def artifact_file_key(artifact_id: str, version: int, file_path: str) -> str:
    return f"{artifact_version_prefix(artifact_id, version)}{file_path}"


def artifact_version_prefix_candidates(
    artifact_id: str, version: int, legacy_conversation_id: str | None
) -> tuple[str, ...]:
    canonical = artifact_version_prefix(artifact_id, version)
    if not legacy_conversation_id:
        return (canonical,)
    return (canonical, f"artifacts/{legacy_conversation_id}/{artifact_id}/v{version}/")


def artifact_file_key_candidates(
    artifact_id: str,
    version: int,
    file_path: str,
    legacy_conversation_id: str | None,
) -> tuple[str, ...]:
    return tuple(
        f"{prefix}{file_path}"
        for prefix in artifact_version_prefix_candidates(
            artifact_id, version, legacy_conversation_id
        )
    )


async def list_artifact_version_objects(
    store: ObjectLister,
    artifact_id: str,
    version: int,
    legacy_conversation_id: str | None,
) -> list[tuple[str, str]]:
    """Return ``(relative_path, object_key)`` pairs for one version."""
    objects: dict[str, str] = {}
    for prefix in artifact_version_prefix_candidates(artifact_id, version, legacy_conversation_id):
        for key in await store.list_objects(prefix):
            objects.setdefault(key[len(prefix) :], key)
    return list(objects.items())


def artifact_staging_prefix(artifact_id: str, nonce: str) -> str:
    return f"{artifact_root_prefix(artifact_id)}staging/{nonce}/"
