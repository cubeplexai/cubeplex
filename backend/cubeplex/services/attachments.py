"""Attachment service: validate / persist / process uploaded files."""

from __future__ import annotations

import io
import mimetypes
import posixpath
import re
from typing import TYPE_CHECKING, Literal

from loguru import logger
from PIL import Image, UnidentifiedImageError

from cubeplex.api.exceptions import (
    AttachmentInvalidImageError,
    AttachmentMimeRejectedError,
    AttachmentQuotaExceededError,
    AttachmentTooLargeError,
)
from cubeplex.config import config
from cubeplex.models import Attachment
from cubeplex.models.public_id import generate_public_id
from cubeplex.objectstore import get_objectstore_client
from cubeplex.repositories import AttachmentRepository
from cubeplex.utils.time import utc_isoformat

if TYPE_CHECKING:
    from cubeplex.objectstore.client import ObjectStoreClient


class InvalidImageError(ValueError):
    """Raised when an uploaded image is invalid or too large to decode safely."""


def decode_image_dimensions(data: bytes, *, max_long_edge: int = 16384) -> tuple[int, int]:
    """Open *data* with PIL, return (width, height). Reject if larger than limit."""
    try:
        img = Image.open(io.BytesIO(data))
        w, h = img.size
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidImageError(f"cannot decode image: {exc}") from exc
    if max(w, h) > max_long_edge:
        raise InvalidImageError(
            f"image too large to process ({w}x{h}, max long edge {max_long_edge})"
        )
    return w, h


def resize_to_long_edge(data: bytes, *, target: int, jpeg_quality: int) -> bytes:
    """Resize so max(w, h) <= target, preserving aspect ratio. Output JPEG bytes.

    If image is already smaller than target, returns the original encoded back to JPEG
    (so callers always get a normalized JPEG output).
    """
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    if max(w, h) > target:
        if w >= h:
            new_w = target
            new_h = max(1, round(h * target / w))
        else:
            new_h = target
            new_w = max(1, round(w * target / h))
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=jpeg_quality)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Kind classification
# ---------------------------------------------------------------------------

AttachmentKind = Literal["image", "document", "other"]
_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def classify_kind(mime: str) -> AttachmentKind:
    if mime in _IMAGE_MIMES:
        return "image"
    if mime in {
        "application/pdf",
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/json",
        "application/x-yaml",
    }:
        return "document"
    return "other"


# ---------------------------------------------------------------------------
# Key / path builders
# ---------------------------------------------------------------------------


_MAX_FILENAME_LEN = 255
# Strip NUL + ASCII control bytes and characters Windows/S3 disallow in keys.
_UNSAFE_FILENAME_RE = re.compile(r'[\x00-\x1f<>:"|?*]')


def _safe_basename(raw: str) -> str:
    """Sanitize a user-provided filename into a safe basename.

    Strips directory components (POSIX and Windows separators), removes NUL +
    control bytes, rejects "." / ".." / empty, and clamps to filesystem name
    length. Falls back to "upload" when nothing usable remains. The same
    sanitized value is used for the ObjectStore key, the sandbox path, and
    the persisted Attachment.filename so the row matches what's actually
    stored.
    """
    if not raw:
        return "upload"
    candidate = posixpath.basename(raw.replace("\\", "/"))
    candidate = _UNSAFE_FILENAME_RE.sub("", candidate)
    candidate = candidate.strip(" .")
    if not candidate or candidate in {".", ".."}:
        return "upload"
    if len(candidate) > _MAX_FILENAME_LEN:
        stem, dot, ext = candidate.rpartition(".")
        if dot and 0 < len(ext) <= 16:
            candidate = stem[: _MAX_FILENAME_LEN - len(ext) - 1] + dot + ext
        else:
            candidate = candidate[:_MAX_FILENAME_LEN]
    return candidate


def _build_object_key(
    *, org_id: str, workspace_id: str, conversation_id: str, file_id: str, filename: str
) -> str:
    return f"attachments/{org_id}/{workspace_id}/{conversation_id}/{file_id}/original/{filename}"


def _build_thumbnail_key(
    *, org_id: str, workspace_id: str, conversation_id: str, file_id: str
) -> str:
    return f"attachments/{org_id}/{workspace_id}/{conversation_id}/{file_id}/thumb/thumb.webp"


def _build_sandbox_path(*, conversation_id: str, file_id: str, filename: str) -> str:
    return f"/workspace/uploads/{conversation_id}/{file_id}/{filename}"


# ---------------------------------------------------------------------------
# Thumbnail helper
# ---------------------------------------------------------------------------


def _make_thumbnail(data: bytes, *, max_long_edge: int, quality: int) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((max_long_edge, max_long_edge), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=quality)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# AttachmentService
# ---------------------------------------------------------------------------


class AttachmentService:
    """Validate, persist, and lifecycle-manage conversation attachments."""

    def __init__(
        self,
        *,
        repo: AttachmentRepository,
        objectstore: ObjectStoreClient | None = None,
    ) -> None:
        self.repo = repo
        self.objectstore = objectstore or get_objectstore_client()

    async def upload(
        self,
        *,
        conversation_id: str,
        uploader_user_id: str,
        filename: str,
        content: bytes,
        mime_type: str | None,
    ) -> Attachment:
        """Validate + store + record a new attachment. Returns the persisted row."""
        max_bytes: int = int(config.get("attachments.max_file_bytes", 52428800))
        if len(content) > max_bytes:
            raise AttachmentTooLargeError(size_bytes=len(content), max_bytes=max_bytes)

        # Sanitize the multipart filename before it ever touches a path/key.
        # Attacker-controlled values like "../../etc/passwd" or "C:\\foo\\bar"
        # would otherwise escape /workspace/uploads/{conv}/{fid}/ and the
        # ObjectStore key prefix.
        safe_filename = _safe_basename(filename)
        resolved_mime = (
            mime_type or mimetypes.guess_type(safe_filename)[0] or "application/octet-stream"
        )
        allowed: list[str] = list(config.get("attachments.allowed_mime_types", []))
        if resolved_mime not in allowed:
            raise AttachmentMimeRejectedError(resolved_mime)

        max_conv: int = int(config.get("attachments.max_per_conversation_bytes", 524288000))
        current = await self.repo.sum_active_size(conversation_id)
        if current + len(content) > max_conv:
            raise AttachmentQuotaExceededError(
                current=current,
                incoming=len(content),
                limit=max_conv,
            )

        kind = classify_kind(resolved_mime)
        file_id = generate_public_id("atch")

        width: int | None = None
        height: int | None = None
        thumbnail_bytes: bytes | None = None

        if kind == "image":
            try:
                width, height = decode_image_dimensions(
                    content,
                    max_long_edge=int(
                        config.get("attachments.view_images.max_decoded_long_edge", 16384)
                    ),
                )
                thumbnail_bytes = _make_thumbnail(
                    content,
                    max_long_edge=int(config.get("attachments.thumbnail.max_long_edge", 256)),
                    quality=int(config.get("attachments.thumbnail.quality", 80)),
                )
            except InvalidImageError as exc:
                raise AttachmentInvalidImageError(str(exc)) from exc

        object_key = _build_object_key(
            org_id=self.repo.org_id,
            workspace_id=self.repo.workspace_id,
            conversation_id=conversation_id,
            file_id=file_id,
            filename=safe_filename,
        )
        sandbox_path = _build_sandbox_path(
            conversation_id=conversation_id,
            file_id=file_id,
            filename=safe_filename,
        )
        thumbnail_key: str | None = None

        await self.objectstore.upload_file(object_key, content, content_type=resolved_mime)
        if thumbnail_bytes is not None:
            thumbnail_key = _build_thumbnail_key(
                org_id=self.repo.org_id,
                workspace_id=self.repo.workspace_id,
                conversation_id=conversation_id,
                file_id=file_id,
            )
            await self.objectstore.upload_file(
                thumbnail_key,
                thumbnail_bytes,
                content_type="image/webp",
            )

        row = Attachment(
            id=file_id,
            conversation_id=conversation_id,
            uploader_user_id=uploader_user_id,
            filename=safe_filename,
            mime_type=resolved_mime,
            size_bytes=len(content),
            kind=kind,
            object_key=object_key,
            sandbox_path=sandbox_path,
            thumbnail_object_key=thumbnail_key,
            width=width,
            height=height,
        )
        return await self.repo.add(row)

    async def delete_pending(self, *, conversation_id: str, attachment_id: str) -> None:
        """Delete a pending attachment row + ObjectStore objects.

        Caller validates state (must be pending) before calling this.
        """
        row = await self.repo.get_in_conversation(
            conversation_id=conversation_id,
            attachment_id=attachment_id,
        )
        if row is None:
            return
        try:
            await self.objectstore.delete_file(row.object_key)
            if row.thumbnail_object_key:
                await self.objectstore.delete_file(row.thumbnail_object_key)
        except Exception as exc:  # noqa: BLE001 — non-fatal
            logger.warning("ObjectStore delete failed for {}: {}", row.id, exc)
        await self.repo.delete(row.id)

    async def delete_for_conversation(self, *, conversation_id: str) -> None:
        """Cascade-delete every attachment row + ObjectStore object for a conversation."""
        rows = await self.repo.list_by_conversation(conversation_id=conversation_id)
        for row in rows:
            try:
                await self.objectstore.delete_file(row.object_key)
                if row.thumbnail_object_key:
                    await self.objectstore.delete_file(row.thumbnail_object_key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ObjectStore delete failed for {}: {}", row.id, exc)
            await self.repo.delete(row.id)

    @staticmethod
    def attachment_to_api_dto(att: Attachment, *, base_url: str) -> dict[str, object]:
        """Render attachment metadata for API responses."""
        return {
            "id": att.id,
            "filename": att.filename,
            "kind": att.kind,
            "mime_type": att.mime_type,
            "size_bytes": att.size_bytes,
            "width": att.width,
            "height": att.height,
            "status": att.status,
            "thumbnail_url": (
                f"{base_url}/{att.id}/thumbnail" if att.thumbnail_object_key else None
            ),
            "download_url": f"{base_url}/{att.id}/content",
            "created_at": utc_isoformat(att.created_at),
        }


async def cleanup_orphan_attachments() -> int:
    """Sweep all orgs/workspaces and physically delete pending attachments older than TTL.

    Returns the number of rows removed. Safe to call concurrently — DB rows
    are deleted under the same scope each time and ObjectStore deletes are
    idempotent.
    """
    from datetime import UTC as _UTC
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from sqlalchemy import select as sa_select

    from cubeplex.db.engine import async_session_maker
    from cubeplex.models import Attachment as _Attachment

    ttl = int(config.get("attachments.orphan_ttl_seconds", 3600))
    objectstore = get_objectstore_client()
    removed = 0

    async with async_session_maker() as session:
        cutoff = _dt.now(_UTC) - _td(seconds=ttl)
        stmt = sa_select(_Attachment).where(
            _Attachment.status == "pending",  # type: ignore[arg-type]
            _Attachment.created_at < cutoff,  # type: ignore[arg-type]
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        for row in rows:
            try:
                await objectstore.delete_file(row.object_key)
                if row.thumbnail_object_key:
                    await objectstore.delete_file(row.thumbnail_object_key)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "orphan cleanup: ObjectStore delete failed for {}: {}",
                    row.id,
                    exc,
                )
            await session.delete(row)
            removed += 1
        await session.commit()
    if removed:
        logger.info("Cleaned {} orphan attachment(s)", removed)
    return removed
