"""Artifacts API routes."""

import mimetypes
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.db import get_session
from cubebox.repositories import ArtifactRepository

router = APIRouter(prefix="/conversations/{conversation_id}/artifacts", tags=["artifacts"])


@router.get("")
async def list_artifacts(
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """List all artifacts for a conversation."""
    repo = ArtifactRepository(session)
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
) -> dict[str, object]:
    """Get a single artifact by ID."""
    repo = ArtifactRepository(session)
    artifact = await repo.get_by_id(artifact_id)
    if not artifact or artifact.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )
    return artifact.to_dict()


@router.get("/{artifact_id}/download")
async def download_artifact(
    conversation_id: str,
    artifact_id: str,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Download an artifact file from the sandbox."""
    repo = ArtifactRepository(session)
    artifact = await repo.get_by_id(artifact_id)
    if not artifact or artifact.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )

    # Get sandbox for current user
    user_id: str = getattr(raw_request.state, "user_id", "anonymous")
    try:
        from cubebox.sandbox.manager import get_sandbox_manager

        sandbox_manager = get_sandbox_manager()
        sandbox = await sandbox_manager.get_or_create(user_id)
    except Exception as e:
        logger.warning("Cannot access sandbox for download: {}", e)
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Sandbox is not available. The file may no longer be accessible.",
        ) from None

    try:
        # Check if path is a directory
        is_dir_result = await sandbox.execute(f"test -d {artifact.path!r}")
        is_directory = is_dir_result.exit_code == 0

        if is_directory:
            # Tar the directory for download
            tar_result = await sandbox.execute(
                f"cd {artifact.path!r} && tar -cf /tmp/_artifact.tar ."
            )
            if tar_result.exit_code and tar_result.exit_code != 0:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to package artifact directory",
                )
            files = await sandbox.download(["/tmp/_artifact.tar"])
            if not files:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Artifact file not found in sandbox",
                )
            _, content = files[0]
            filename = f"{artifact.name}.tar"
            media_type = "application/x-tar"
        else:
            files = await sandbox.download([artifact.path])
            if not files:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Artifact file not found in sandbox",
                )
            _, content = files[0]
            # Extract filename from path
            filename = artifact.path.rsplit("/", 1)[-1]
            media_type = artifact.mime_type or "application/octet-stream"

        await sandbox_manager.release(sandbox.id)

        return Response(
            content=content,
            media_type=media_type,
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


@router.get("/{artifact_id}/preview/{file_path:path}")
async def preview_artifact_file(
    conversation_id: str,
    artifact_id: str,
    file_path: str,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Serve a single file from an artifact for iframe preview."""
    repo = ArtifactRepository(session)
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

    user_id: str = getattr(raw_request.state, "user_id", "anonymous")
    try:
        from cubebox.sandbox.manager import get_sandbox_manager

        sandbox_manager = get_sandbox_manager()
        sandbox = await sandbox_manager.get_or_create(user_id)
    except Exception as e:
        logger.warning("Cannot access sandbox for preview: {}", e)
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Sandbox is not available.",
        ) from None

    try:
        # If artifact.path is a file (not a directory), serve it directly
        is_dir = await sandbox.execute(f"test -d {artifact.path!r}")
        if is_dir.exit_code == 0:
            full_path = f"{artifact.path.rstrip('/')}/{file_path}"
        else:
            full_path = artifact.path

        # Verify file exists
        check = await sandbox.execute(f"test -f {full_path!r}")
        if check.exit_code != 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File not found: {file_path}",
            )

        files = await sandbox.download([full_path])
        if not files:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File not found: {file_path}",
            )
        _, content = files[0]
        await sandbox_manager.release(sandbox.id)

        mime, _ = mimetypes.guess_type(full_path)
        media_type = mime or "application/octet-stream"

        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Cache-Control": "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error serving preview file: {}", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to serve preview file",
        ) from None
