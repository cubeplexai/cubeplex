"""Present a sandbox file into conversation-durable ObjectStore storage."""

from __future__ import annotations

import mimetypes
import posixpath
from typing import TYPE_CHECKING

from loguru import logger

from cubeplex.api.exceptions import (
    AttachmentInvalidImageError,
    AttachmentMimeRejectedError,
    AttachmentQuotaExceededError,
    AttachmentTooLargeError,
)
from cubeplex.config import config
from cubeplex.models.presented_file import PresentedFile
from cubeplex.objectstore import get_objectstore_client
from cubeplex.repositories.presented_file import PresentedFileRepository
from cubeplex.services.attachments import (
    InvalidImageError,
    _make_thumbnail,
    _safe_basename,
    classify_kind,
    decode_image_dimensions,
)

if TYPE_CHECKING:
    from cubeplex.objectstore.client import ObjectStoreClient
    from cubeplex.sandbox.base import Sandbox

SANDBOX_ROOT = "/workspace"


class PresentedFilePathError(ValueError):
    """Path is missing, escapes the sandbox root, or is not a file."""


def normalize_sandbox_path(path: str) -> str:
    """Return a normalized absolute path under ``/workspace`` or raise."""
    if not path or not path.strip():
        raise PresentedFilePathError("path is required")
    raw = path.strip()
    # Reject null bytes early
    if "\x00" in raw:
        raise PresentedFilePathError("invalid path")
    # Absolute only — agents pass /workspace/...
    if not raw.startswith("/"):
        raise PresentedFilePathError("path must be an absolute sandbox path")
    normalized = posixpath.normpath(raw)
    if normalized != SANDBOX_ROOT and not normalized.startswith(f"{SANDBOX_ROOT}/"):
        raise PresentedFilePathError(f"path must be under {SANDBOX_ROOT}")
    # Double-check no residual ..
    if ".." in normalized.split("/"):
        raise PresentedFilePathError("path must not contain '..'")
    return normalized


def _build_object_key(
    *, org_id: str, workspace_id: str, conversation_id: str, file_id: str, filename: str
) -> str:
    return f"presented/{org_id}/{workspace_id}/{conversation_id}/{file_id}/original/{filename}"


def _build_thumbnail_key(
    *, org_id: str, workspace_id: str, conversation_id: str, file_id: str
) -> str:
    return f"presented/{org_id}/{workspace_id}/{conversation_id}/{file_id}/thumb/thumb.webp"


class PresentedFileService:
    """Validate, copy from sandbox, and persist a presented file."""

    def __init__(
        self,
        *,
        repo: PresentedFileRepository,
        objectstore: ObjectStoreClient | None = None,
    ) -> None:
        self.repo = repo
        self.objectstore = objectstore or get_objectstore_client()

    async def present_from_sandbox(
        self,
        sandbox: Sandbox,
        *,
        conversation_id: str,
        path: str,
        caption: str | None = None,
        run_id: str | None = None,
    ) -> PresentedFile:
        """Read *path* from the sandbox and store it as a presented file."""
        normalized = normalize_sandbox_path(path)

        try:
            downloaded = await sandbox.download([normalized])
        except Exception as exc:
            logger.warning("presented_file sandbox download failed for {}: {}", normalized, exc)
            raise PresentedFilePathError(f"Path not found in sandbox: {normalized}") from exc

        if not downloaded:
            raise PresentedFilePathError(f"Path not found in sandbox: {normalized}")
        _p, content = downloaded[0]

        max_bytes: int = int(config.get("attachments.max_file_bytes", 52428800))
        if len(content) > max_bytes:
            raise AttachmentTooLargeError(size_bytes=len(content), max_bytes=max_bytes)

        filename = _safe_basename(posixpath.basename(normalized))
        resolved_mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        allowed: list[str] = list(config.get("attachments.allowed_mime_types", []))
        if allowed and resolved_mime not in allowed:
            raise AttachmentMimeRejectedError(resolved_mime)

        max_conv: int = int(
            config.get(
                "presented_files.max_per_conversation_bytes",
                config.get("attachments.max_per_conversation_bytes", 524288000),
            )
        )
        current = await self.repo.sum_size(conversation_id)
        if current + len(content) > max_conv:
            raise AttachmentQuotaExceededError(
                current=current,
                incoming=len(content),
                limit=max_conv,
            )

        kind = classify_kind(resolved_mime)
        # Build row first so id (pfile-…) is generated for key layout.
        row = PresentedFile(
            org_id=self.repo.org_id,
            workspace_id=self.repo.workspace_id,
            conversation_id=conversation_id,
            run_id=run_id,
            source_path=normalized,
            filename=filename,
            mime_type=resolved_mime,
            size_bytes=len(content),
            kind=kind,
            object_key="",  # filled below
            caption=(caption.strip() if caption and caption.strip() else None),
        )
        file_id = row.id

        width: int | None = None
        height: int | None = None
        thumbnail_key: str | None = None
        if kind == "image":
            try:
                width, height = decode_image_dimensions(
                    content,
                    max_long_edge=int(
                        config.get("attachments.view_images.max_decoded_long_edge", 16384)
                    ),
                )
                thumb = _make_thumbnail(
                    content,
                    max_long_edge=int(config.get("attachments.thumbnail.max_long_edge", 256)),
                    quality=int(config.get("attachments.thumbnail.quality", 80)),
                )
                thumbnail_key = _build_thumbnail_key(
                    org_id=self.repo.org_id,
                    workspace_id=self.repo.workspace_id,
                    conversation_id=conversation_id,
                    file_id=file_id,
                )
                await self.objectstore.upload_file(thumbnail_key, thumb, content_type="image/webp")
            except InvalidImageError as exc:
                raise AttachmentInvalidImageError(str(exc)) from exc

        object_key = _build_object_key(
            org_id=self.repo.org_id,
            workspace_id=self.repo.workspace_id,
            conversation_id=conversation_id,
            file_id=file_id,
            filename=filename,
        )
        await self.objectstore.upload_file(object_key, content, content_type=resolved_mime)

        row.object_key = object_key
        row.thumbnail_object_key = thumbnail_key
        row.width = width
        row.height = height
        return await self.repo.add(row)

    async def delete_for_conversation(self, *, conversation_id: str) -> None:
        """Cascade-delete every presented file + ObjectStore object for a conversation."""
        rows = await self.repo.list_by_conversation(conversation_id=conversation_id)
        for row in rows:
            try:
                await self.objectstore.delete_file(row.object_key)
                if row.thumbnail_object_key:
                    await self.objectstore.delete_file(row.thumbnail_object_key)
            except Exception as exc:
                logger.warning("presented_file ObjectStore delete failed for {}: {}", row.id, exc)
            await self.repo.session.delete(row)
            await self.repo.session.commit()
