"""Artifacts API routes."""

import io
import mimetypes
import secrets
import tarfile
from typing import Annotated
from urllib.parse import quote

import orjson
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import require_member
from cubeplex.cache import RedisHandle, redis_dep
from cubeplex.config import config
from cubeplex.db import get_session
from cubeplex.objectstore import get_objectstore_client
from cubeplex.repositories import (
    ArtifactRepository,
    ArtifactVersionRepository,
    ConversationRepository,
)
from cubeplex.utils.http import content_disposition

IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"})

router = APIRouter(
    prefix="/ws/{workspace_id}/conversations/{conversation_id}/artifacts",
    tags=["artifacts"],
)


async def _require_conversation(
    session: AsyncSession, ctx: RequestContext, conversation_id: str
) -> None:
    """Raise 404 if the parent conversation is missing, foreign, or soft-deleted.

    Mirrors the same check used by ``attachments.py``. Without this, a
    client holding a stale conversation URL could keep listing or
    downloading artifacts after the conversation was soft-deleted —
    ``ArtifactRepository`` only scopes by org/workspace, not by parent
    conversation state.
    """
    conv_repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    if (await conv_repo.get_by_id(conversation_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )


@router.get("")
async def list_artifacts(
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    """List all artifacts for a conversation."""
    await _require_conversation(session, ctx, conversation_id)
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
    await _require_conversation(session, ctx, conversation_id)
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
    await _require_conversation(session, ctx, conversation_id)
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


class ArtifactContentUpdateIn(BaseModel):
    content: str
    expected_version: int


@router.put("/{artifact_id}/content")
async def update_artifact_content_route(
    conversation_id: str,
    artifact_id: str,
    body: ArtifactContentUpdateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    """Save a new markdown version from the browser editor."""
    from cubeplex.services.artifact_content import (
        ArtifactContentError,
        update_artifact_content,
    )

    await _require_conversation(session, ctx, conversation_id)
    try:
        result = await update_artifact_content(
            session,
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
            conversation_id=conversation_id,
            artifact_id=artifact_id,
            content=body.content,
            expected_version=body.expected_version,
            caller_user_id=ctx.user.id,
        )
    except ArtifactContentError as exc:
        if exc.code == "not_found":
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
        if exc.code == "version_conflict":
            raise HTTPException(status.HTTP_409_CONFLICT, detail=exc.message) from exc
        if exc.code in {
            "not_markdown",
            "no_entry",
            "multi_file",
            "too_large",
            "bad_version",
        }:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc

    return {
        "artifact": result.artifact.to_dict(),
        "sandbox_synced": result.sandbox_synced,
        "sandbox_sync_reason": result.sandbox_sync_reason,
    }


@router.get("/{artifact_id}/download")
async def download_artifact(
    conversation_id: str,
    artifact_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    version: int | None = Query(default=None),
) -> Response:
    """Download an artifact from object storage."""
    await _require_conversation(session, ctx, conversation_id)
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
                headers={"Content-Disposition": content_disposition(filename)},
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
            headers={"Content-Disposition": content_disposition(filename)},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error downloading artifact: {}", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to download artifact",
        ) from None


class ArtifactFilesOut(BaseModel):
    version: int
    files: list[str]


@router.get("/{artifact_id}/files", response_model=ArtifactFilesOut)
async def list_artifact_files(
    conversation_id: str,
    artifact_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    version: int | None = Query(default=None),
    filter: str | None = Query(default=None),
) -> ArtifactFilesOut:
    """List the files stored for an artifact version.

    ``filter=image`` restricts to image extensions (sorted ascending by
    filename); without ``filter`` all files are returned sorted. Used by the
    preview panel to render a multi-image directory as a carousel.
    """
    await _require_conversation(session, ctx, conversation_id)
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
    except Exception as e:
        logger.error("Error listing artifact files: {}", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list artifact files",
        ) from None

    rel_files = [k[len(prefix) :] for k in keys]
    if filter == "image":
        rel_files = [
            f for f in rel_files if "." in f and f.rsplit(".", 1)[-1].lower() in IMAGE_EXTENSIONS
        ]
    rel_files.sort()

    return ArtifactFilesOut(version=target_version, files=rel_files)


OFFICE_EXTENSIONS = frozenset({".docx", ".xlsx", ".pptx"})
OTK_TTL_SECONDS = 300
SHARE_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days


@router.post("/{artifact_id}/share-token")
async def create_share_token(
    conversation_id: str,
    artifact_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    rh: Annotated[RedisHandle, Depends(redis_dep)],
) -> dict[str, str]:
    """Issue a public, time-limited share URL for any artifact_type.

    Used by the IM artifact dispatcher (Task 11 of the IM-connectors plan)
    and any future "share this artifact" flow. The same Redis nonce + TTL
    pattern as ``create_preview_token`` above, but generalized to all
    artifact types — the dedicated Office-Viewer flow stays separate.
    """
    from cubeplex.services.artifact_share import mint_share_token

    await _require_conversation(session, ctx, conversation_id)
    repo = ArtifactRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    artifact = await repo.get_by_id(artifact_id)
    if not artifact or artifact.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )
    nonce = await mint_share_token(
        redis=rh.client,
        key_prefix=rh.key_prefix,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        conversation_id=conversation_id,
        artifact_id=artifact_id,
        version=artifact.version,
        name=artifact.name,
        artifact_type=artifact.artifact_type,
        entry_file=artifact.entry_file,
        ttl_seconds=SHARE_TTL_SECONDS,
    )
    public_url = config.get("api.public_url", "")
    base = str(public_url).rstrip("/") if public_url else str(request.base_url).rstrip("/")
    return {"share_url": f"{base}/api/v1/public/artifacts/share/{nonce}"}


@router.post("/{artifact_id}/preview-token")
async def create_preview_token(
    conversation_id: str,
    artifact_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    rh: Annotated[RedisHandle, Depends(redis_dep)],
    version: int | None = Query(default=None),
) -> dict[str, str]:
    """Issue a one-time public download token for Office Online Viewer."""
    await _require_conversation(session, ctx, conversation_id)
    repo = ArtifactRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    artifact = await repo.get_by_id(artifact_id)
    if not artifact or artifact.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )

    target_version = version or artifact.version

    if version is not None and version != artifact.version:
        version_repo = ArtifactVersionRepository(
            session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        av = await version_repo.get_version(artifact_id, version)
        if not av:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Version {version} not found",
            )
        filename = av.entry_file or av.path.rsplit("/", 1)[-1]
    else:
        filename = artifact.entry_file or artifact.path.rsplit("/", 1)[-1]
    ext = filename[filename.rfind(".") :].lower() if "." in filename else ""
    if ext not in OFFICE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Office preview not supported for extension '{ext}'",
        )

    nonce = secrets.token_hex(32)
    payload = orjson.dumps(
        {
            "conversation_id": conversation_id,
            "artifact_id": artifact_id,
            "version": target_version,
            "filename": filename,
        }
    )
    key = f"{rh.key_prefix}:otk:{nonce}"
    await rh.client.set(key, payload, ex=OTK_TTL_SECONDS)

    public_url = config.get("api.public_url", "")
    if public_url:
        base = str(public_url).rstrip("/")
    else:
        base = str(request.base_url).rstrip("/")

    download_url = f"{base}/api/v1/public/artifacts/dl/{nonce}/{filename}"
    viewer_url = (
        f"https://view.officeapps.live.com/op/embed.aspx?src={quote(download_url, safe='')}"
    )

    return {"download_url": download_url, "viewer_url": viewer_url}


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
    await _require_conversation(session, ctx, conversation_id)
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
