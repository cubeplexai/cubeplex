"""Canonical object-store paths for versioned artifacts."""


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


def artifact_staging_prefix(artifact_id: str, nonce: str) -> str:
    return f"{artifact_root_prefix(artifact_id)}staging/{nonce}/"
