"""Canonical object-store paths for versioned artifacts."""


def artifact_root_prefix(artifact_id: str) -> str:
    return f"artifacts/{artifact_id}/"


def artifact_version_prefix(artifact_id: str, version: int) -> str:
    return f"{artifact_root_prefix(artifact_id)}v{version}/"


def artifact_file_key(artifact_id: str, version: int, file_path: str) -> str:
    return f"{artifact_version_prefix(artifact_id, version)}{file_path}"


def artifact_staging_prefix(artifact_id: str, nonce: str) -> str:
    return f"{artifact_root_prefix(artifact_id)}staging/{nonce}/"
