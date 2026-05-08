"""Artifacts API routes."""

import io
import mimetypes
import tarfile
from typing import Annotated

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.db import get_session
from cubebox.objectstore import get_objectstore_client
from cubebox.repositories import ArtifactRepository, ArtifactVersionRepository

router = APIRouter(
    prefix="/ws/{workspace_id}/conversations/{conversation_id}/artifacts",
    tags=["artifacts"],
)


@router.get("")
async def list_artifacts(
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    """List all artifacts for a conversation."""
    repo = ArtifactRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    artifacts = await repo.list_by_conversation(conversation_id)
    return {
        "artifacts": [a.to_dict() for a in artifacts],
        "total": len(artifacts),
    }


@router.get("/{artifact_id}")
async def get_artifact(
    conversation_id: str,
    artifact_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    """Get a single artifact by ID."""
    repo = ArtifactRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    artifact = await repo.get_by_id(artifact_id)
    if not artifact or artifact.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )
    return artifact.to_dict()


@router.get("/{artifact_id}/versions")
async def list_artifact_versions(
    conversation_id: str,
    artifact_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    """List all versions of an artifact."""
    repo = ArtifactRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    artifact = await repo.get_by_id(artifact_id)
    if not artifact or artifact.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )

    version_repo = ArtifactVersionRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
    )
    versions = await version_repo.list_by_artifact(artifact_id)
    return {"versions": [v.to_dict() for v in versions], "total": len(versions)}


@router.get("/{artifact_id}/download")
async def download_artifact(
    conversation_id: str,
    artifact_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    version: int | None = Query(default=None),
) -> Response:
    """Download an artifact from object storage."""
    repo = ArtifactRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    artifact = await repo.get_by_id(artifact_id)
    if not artifact or artifact.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )

    target_version = version or artifact.version
    prefix = f"artifacts/{conversation_id}/{artifact_id}/v{target_version}/"

    try:
        store = get_objectstore_client()
        keys = await store.list_objects(prefix)

        if not keys:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No files found for this artifact version",
            )

        if len(keys) == 1:
            # Single file — download and return directly
            data, content_type = await store.download_file(keys[0])
            filename = keys[0].rsplit("/", 1)[-1]
            media_type = artifact.mime_type or content_type or "application/octet-stream"
            return Response(
                content=data,
                media_type=media_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        # Multiple files — create a tar archive in memory
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for key in keys:
                data, _ = await store.download_file(key)
                rel_name = key[len(prefix) :]
                info = tarfile.TarInfo(name=rel_name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        buf.seek(0)

        filename = f"{artifact.name}.tar"
        return Response(
            content=buf.getvalue(),
            media_type="application/x-tar",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error downloading artifact: {}", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to download artifact",
        ) from None


@router.get("/{artifact_id}/preview/v{version}/{file_path:path}")
async def preview_artifact_file(
    conversation_id: str,
    artifact_id: str,
    version: int,
    file_path: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> Response:
    """Serve a single file from an artifact for iframe preview.

    The version is in the URL *path* (not a query parameter) so that
    relative URLs inside the served HTML resolve to the same version.
    Query parameters are not propagated when a browser resolves a
    relative URL, but path prefixes are — so an ``index.html`` that
    references ``slides/01.html`` automatically picks up the same
    version segment.
    """
    repo = ArtifactRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    artifact = await repo.get_by_id(artifact_id)
    if not artifact or artifact.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )

    # Prevent path traversal
    if ".." in file_path or file_path.startswith("/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file path",
        )

    key = f"artifacts/{conversation_id}/{artifact_id}/v{version}/{file_path}"

    try:
        store = get_objectstore_client()
        data, stored_content_type = await store.download_file(key)
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code in ("NoSuchKey", "404"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File not found: {file_path}",
            ) from None
        logger.error("Error serving preview file: {}", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to serve preview file",
        ) from None
    except Exception as e:
        logger.error("Error serving preview file: {}", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to serve preview file",
        ) from None

    # Override content type from file extension for accuracy
    mime, _ = mimetypes.guess_type(file_path)
    media_type = mime or stored_content_type or "application/octet-stream"

    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
