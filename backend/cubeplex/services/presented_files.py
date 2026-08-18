"""Present a sandbox file into conversation-durable ObjectStore storage."""

from __future__ import annotations

import mimetypes
import posixpath
import shlex
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
_CAPTION_MAX_LEN = 1024


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


def presented_object_key(
    *, org_id: str, workspace_id: str, conversation_id: str, file_id: str, filename: str
) -> str:
    """ObjectStore key for a presented file's original bytes."""
    return f"presented/{org_id}/{workspace_id}/{conversation_id}/{file_id}/original/{filename}"


def _build_object_key(
    *, org_id: str, workspace_id: str, conversation_id: str, file_id: str, filename: str
) -> str:
    return presented_object_key(
        org_id=org_id,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        file_id=file_id,
        filename=filename,
    )


def _build_thumbnail_key(
    *, org_id: str, workspace_id: str, conversation_id: str, file_id: str
) -> str:
    return f"presented/{org_id}/{workspace_id}/{conversation_id}/{file_id}/thumb/thumb.webp"


def _clip_caption(caption: str | None) -> str | None:
    if not caption:
        return None
    cleaned = caption.strip()
    if not cleaned:
        return None
    if len(cleaned) > _CAPTION_MAX_LEN:
        return cleaned[:_CAPTION_MAX_LEN]
    return cleaned


async def _assert_regular_file_size(sandbox: Sandbox, path: str, *, max_bytes: int) -> int:
    """Confirm *path* is a regular file under /workspace and return its size.

    Resolves symlinks and rejects escapes so we never download out-of-tree
    targets. Checks size *before* ``sandbox.download`` so multi-GB files
    cannot be fully materialised just to reject them.
    """
    q = shlex.quote(path)
    # realpath -e requires the path to exist; case rejects leave /workspace.
    script = (
        f"real=$(realpath -e {q} 2>/dev/null) || {{ echo NOT_FOUND; exit 2; }}; "
        f'case "$real" in {SANDBOX_ROOT}|{SANDBOX_ROOT}/*) ;; *) echo ESCAPE; exit 3;; esac; '
        f'if [ ! -f "$real" ]; then echo NOT_FILE; exit 4; fi; '
        f'stat -c %s "$real"'
    )
    result = await sandbox.execute(script)
    out = (result.output or "").strip()
    code = result.exit_code
    if code not in (None, 0):
        if "ESCAPE" in out:
            raise PresentedFilePathError(f"path resolves outside {SANDBOX_ROOT}")
        if "NOT_FILE" in out:
            raise PresentedFilePathError(f"path is not a regular file: {path}")
        raise PresentedFilePathError(f"Path not found in sandbox: {path}")
    try:
        size = int(out.splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise PresentedFilePathError(f"could not determine size for {path}") from exc
    if size < 0:
        raise PresentedFilePathError(f"invalid size for {path}")
    if size > max_bytes:
        raise AttachmentTooLargeError(size_bytes=size, max_bytes=max_bytes)
    return size


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
        max_bytes: int = int(config.get("attachments.max_file_bytes", 52428800))

        # Size / type guard before any full-file download.
        await _assert_regular_file_size(sandbox, normalized, max_bytes=max_bytes)

        try:
            downloaded = await sandbox.download([normalized])
        except Exception as exc:
            logger.warning("presented_file sandbox download failed for {}: {}", normalized, exc)
            raise PresentedFilePathError(f"Path not found in sandbox: {normalized}") from exc

        if not downloaded:
            raise PresentedFilePathError(f"Path not found in sandbox: {normalized}")
        _p, content = downloaded[0]

        # Re-check after download (TOCTOU / driver quirks).
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
            caption=_clip_caption(caption),
        )
        file_id = row.id

        width: int | None = None
        height: int | None = None
        thumbnail_key: str | None = None
        uploaded_keys: list[str] = []

        try:
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
                    await self.objectstore.upload_file(
                        thumbnail_key, thumb, content_type="image/webp"
                    )
                    uploaded_keys.append(thumbnail_key)
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
            uploaded_keys.append(object_key)

            row.object_key = object_key
            row.thumbnail_object_key = thumbnail_key
            row.width = width
            row.height = height
            return await self.repo.add(row)
        except Exception:
            # Compensate ObjectStore uploads so a failed DB commit (or later
            # validation) does not leave undiscoverable blobs forever.
            for key in uploaded_keys:
                try:
                    await self.objectstore.delete_file(key)
                except Exception as cleanup_exc:
                    logger.warning(
                        "presented_file compensate delete failed for {}: {}",
                        key,
                        cleanup_exc,
                    )
            raise

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
