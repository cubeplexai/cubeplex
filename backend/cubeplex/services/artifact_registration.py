"""Shared artifact registration helper.

Extracts the create-or-version-and-upload logic from the ``save_artifact``
middleware tool so other callers (e.g. ``generate_image``) can reuse it.
"""

from __future__ import annotations

import mimetypes
import shlex

from loguru import logger

from cubeplex.models.artifact import Artifact
from cubeplex.sandbox.base import Sandbox


async def register_artifact_from_sandbox(
    *,
    sandbox: Sandbox,
    conversation_id: str,
    org_id: str,
    workspace_id: str,
    name: str,
    artifact_type: str,
    path: str,
    entry_file: str | None = None,
    description: str | None = None,
    mime_type: str | None = None,
    artifact_id: str | None = None,
) -> Artifact:
    """Register a sandbox path as an artifact (create or version-bump).

    Raises ``FileNotFoundError`` if ``path`` does not exist in the sandbox.
    The object-storage upload is non-fatal: errors are logged and swallowed.
    """
    result = await sandbox.execute(f"test -e {shlex.quote(path)}")
    if result.exit_code is not None and result.exit_code != 0:
        raise FileNotFoundError(f"Path not found in sandbox: {path}")

    if mime_type is None:
        target = entry_file if entry_file else path
        mime_type, _ = mimetypes.guess_type(target)

    from cubeplex.db.engine import async_session_maker
    from cubeplex.repositories import ArtifactRepository, ArtifactVersionRepository

    async with async_session_maker() as session:
        repo = ArtifactRepository(session, org_id=org_id, workspace_id=workspace_id)
        version_repo = ArtifactVersionRepository(session, org_id=org_id, workspace_id=workspace_id)

        if not artifact_id:
            existing = await repo.find_by_path(conversation_id, path)
            if existing:
                artifact_id = existing.id
                logger.info(
                    "Auto-matched artifact by path: id={}, path={}",
                    artifact_id,
                    path,
                )

        if artifact_id:
            artifact = await repo.update(
                artifact_id,
                name=name,
                artifact_type=artifact_type,
                path=path,
                entry_file=entry_file,
                mime_type=mime_type,
                description=description,
            )
            if artifact is None:
                raise ValueError(f"Artifact not found: {artifact_id}")
        else:
            artifact = await repo.create(
                conversation_id=conversation_id,
                name=name,
                artifact_type=artifact_type,
                path=path,
                entry_file=entry_file,
                mime_type=mime_type,
                description=description,
            )

        await version_repo.create(
            artifact_id=artifact.id,
            version=artifact.version,
            name=name,
            description=description,
            path=path,
            entry_file=entry_file,
            mime_type=mime_type,
        )

    try:
        from cubeplex.objectstore import get_objectstore_client

        store = get_objectstore_client()
        key_prefix = f"artifacts/{conversation_id}/{artifact.id}/v{artifact.version}/"
        await store.upload_from_sandbox(sandbox, path, key_prefix)
    except Exception:
        logger.exception("Failed to upload artifact {} to object storage (non-fatal)", artifact.id)

    return artifact
