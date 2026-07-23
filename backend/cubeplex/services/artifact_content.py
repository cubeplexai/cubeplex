"""User-driven markdown artifact content updates (versioned object store).

Unlike agent ``register_artifact_from_sandbox``, this path uploads the next
version object first, then CAS-bumps the DB so the current version never points
at a missing object.
"""

from __future__ import annotations

import mimetypes
import posixpath
import re
from dataclasses import dataclass
from typing import Any, cast

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.artifact import Artifact
from cubeplex.objectstore import get_objectstore_client
from cubeplex.repositories import ArtifactRepository, ArtifactVersionRepository

MAX_CONTENT_BYTES = 2_000_000
_MD_EXT = re.compile(r"\.(md|markdown|mdx)$", re.IGNORECASE)
_MD_MIME = frozenset({"text/markdown", "text/x-markdown"})


class ArtifactContentError(Exception):
    """Domain error for content update; map to HTTP in the route layer."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ContentUpdateResult:
    artifact: Artifact
    sandbox_synced: bool
    sandbox_sync_reason: str | None


def artifact_basename(path: str | None) -> str:
    if not path:
        return ""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    return parts[-1] if parts else ""


def markdown_filename(artifact: Artifact) -> str | None:
    """Return the single-file markdown target name, or None if unsafe/missing."""
    entry = (artifact.entry_file or "").strip()
    if entry:
        if entry.startswith("/") or ".." in entry.split("/"):
            return None
        parts = [p for p in entry.split("/") if p]
        return parts[-1] if parts else None
    base = artifact_basename(artifact.path)
    return base or None


def is_markdown_eligible(artifact: Artifact) -> bool:
    mime = (artifact.mime_type or "").split(";")[0].strip().lower()
    if mime in _MD_MIME:
        return True
    if artifact.artifact_type != "document":
        return False
    name = markdown_filename(artifact)
    return name is not None and bool(_MD_EXT.search(name))


def resolve_sandbox_write_path(artifact: Artifact) -> tuple[str | None, str | None]:
    """Return ``(abs_path, error_reason)`` for best-effort sandbox write."""
    path = (artifact.path or "").strip()
    if not path:
        return None, "no_path"

    entry = (artifact.entry_file or "").strip()
    # Directory-like path: need a safe relative entry_file.
    looks_dir = path.endswith("/") or (
        entry != "" and not path.rstrip("/").endswith(entry.split("/")[-1])
    )
    if entry:
        if entry.startswith("/") or ".." in entry.split("/"):
            return None, "path_escape"
        target = posixpath.normpath(posixpath.join(path.rstrip("/"), entry))
    else:
        target = posixpath.normpath(path)

    if ".." in target.split("/"):
        return None, "path_escape"
    if not target.startswith("/workspace"):
        # Soft rule aligned with sandbox file API; still allow other workdirs
        # when already absolute under a single root without escape.
        if not target.startswith("/"):
            return None, "path_escape"

    if looks_dir and not entry:
        return None, "path_is_directory"

    return target, None


async def update_artifact_content(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str,
    conversation_id: str,
    artifact_id: str,
    content: str,
    expected_version: int,
    caller_user_id: str,
) -> ContentUpdateResult:
    """Upload new markdown bytes, CAS-bump version, best-effort sandbox write."""
    if expected_version < 1:
        raise ArtifactContentError("bad_version", "expected_version must be >= 1")

    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_CONTENT_BYTES:
        raise ArtifactContentError(
            "too_large",
            f"Content exceeds maximum of {MAX_CONTENT_BYTES} bytes",
        )

    repo = ArtifactRepository(session, org_id=org_id, workspace_id=workspace_id)
    artifact = await repo.get_by_id(artifact_id)
    if artifact is None or artifact.conversation_id != conversation_id:
        raise ArtifactContentError("not_found", f"Artifact {artifact_id} not found")

    if not is_markdown_eligible(artifact):
        raise ArtifactContentError("not_markdown", "Artifact is not a markdown document")

    filename = markdown_filename(artifact)
    if filename is None or not _MD_EXT.search(filename):
        raise ArtifactContentError(
            "no_entry",
            "Markdown edit requires a clear single-file target (entry_file or .md path)",
        )

    store = get_objectstore_client()
    current_prefix = f"artifacts/{conversation_id}/{artifact_id}/v{artifact.version}/"
    try:
        existing = await store.list_objects(current_prefix)
    except Exception:
        logger.exception("Failed listing artifact objects for {}", artifact_id)
        existing = []
    # Multi-file version: reject (v1) so we do not shrink the version tree.
    if len(existing) > 1:
        raise ArtifactContentError(
            "multi_file",
            "Editing multi-file directory artifacts is not supported",
        )

    next_version = expected_version + 1
    key = f"artifacts/{conversation_id}/{artifact_id}/v{next_version}/{filename}"
    mime = artifact.mime_type or mimetypes.guess_type(filename)[0] or "text/markdown"

    # Upload first so DB never points at a missing object.
    await store.upload_file(key, content_bytes, content_type=mime)

    bumped = await repo.cas_bump_version(
        artifact_id,
        expected_version=expected_version,
    )
    if bumped is None:
        try:
            await store.delete_file(key)
        except Exception:
            logger.warning("Failed to GC orphan object after CAS miss: {}", key)
        raise ArtifactContentError(
            "version_conflict",
            f"Version conflict: expected {expected_version}",
        )

    version_repo = ArtifactVersionRepository(session, org_id=org_id, workspace_id=workspace_id)
    try:
        await version_repo.create(
            artifact_id=bumped.id,
            version=bumped.version,
            name=bumped.name,
            description=bumped.description,
            path=bumped.path,
            entry_file=bumped.entry_file,
            mime_type=bumped.mime_type or mime,
        )
    except Exception:
        # Compensate: best-effort leave orphan object; do not claim success.
        logger.exception("Failed to insert artifact version row for {}", artifact_id)
        raise

    sandbox_synced, sandbox_reason = await _best_effort_sandbox_write(
        session,
        org_id=org_id,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        caller_user_id=caller_user_id,
        artifact=bumped,
        content_bytes=content_bytes,
    )
    return ContentUpdateResult(
        artifact=bumped,
        sandbox_synced=sandbox_synced,
        sandbox_sync_reason=sandbox_reason,
    )


async def _best_effort_sandbox_write(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str,
    conversation_id: str,
    caller_user_id: str,
    artifact: Artifact,
    content_bytes: bytes,
) -> tuple[bool, str | None]:
    write_path, path_reason = resolve_sandbox_write_path(artifact)
    if path_reason:
        return False, path_reason
    assert write_path is not None

    try:
        from cubeplex.api.routes.v1.ws_sandbox import _resolve_sandbox_scope
        from cubeplex.repositories.user_sandbox import UserSandboxRepository
        from cubeplex.sandbox.manager import get_sandbox_manager
    except Exception:
        logger.exception("Sandbox imports failed for artifact content sync")
        return False, "sandbox_error"

    # Minimal context-like object for scope resolution (duck-typed).
    from types import SimpleNamespace

    ctx = SimpleNamespace(
        user=SimpleNamespace(id=caller_user_id),
        org_id=org_id,
        workspace_id=workspace_id,
    )
    try:
        scope_type, scope_id, owner_user_id = await _resolve_sandbox_scope(
            session, cast(Any, ctx), conversation_id
        )
    except Exception:
        logger.warning("Could not resolve sandbox scope for conv {}", conversation_id)
        return False, "no_sandbox"

    sb_repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
    record = await sb_repo.get_active_by_scope(scope_type=scope_type, scope_id=scope_id)
    if record is None:
        return False, "no_sandbox"

    status = getattr(record, "status", None) or ""
    # Only touch sandboxes that are already usable; never provision on save.
    if status not in ("running", "paused"):
        return False, "no_sandbox"

    try:
        manager = get_sandbox_manager()
        attachment = await manager.get_or_create(
            scope_type=scope_type,
            scope_id=scope_id,
            user_id=owner_user_id,
            org_id=org_id,
            workspace_id=workspace_id,
        )
        await attachment.sandbox.upload([(write_path, content_bytes)])
        return True, None
    except Exception:
        logger.warning(
            "Best-effort sandbox write failed for artifact {} path {}",
            artifact.id,
            write_path,
        )
        return False, "sandbox_error"
