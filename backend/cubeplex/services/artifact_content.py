"""User-driven markdown artifact content updates (versioned object store).

Unlike agent ``register_artifact_from_sandbox``, this path:
1. Uploads bytes to a **request-unique staging** key (durable mid-flight copy).
2. Locks the artifact row (FOR UPDATE), checks ``expected_version``.
3. Writes the **final** version key, then CAS-bumps + inserts version row in the
   same transaction (DB never advances without a durable object).
4. GC staging; best-effort sandbox write-back (never fails the save).
"""

from __future__ import annotations

import mimetypes
import posixpath
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.artifact import Artifact
from cubeplex.models.artifact_version import ArtifactVersion
from cubeplex.objectstore import get_objectstore_client
from cubeplex.repositories import ArtifactRepository

MAX_CONTENT_BYTES = 2_000_000
_MD_EXT = re.compile(r"\.(md|markdown|mdx)$", re.IGNORECASE)
_MD_MIME = frozenset({"text/markdown", "text/x-markdown"})
_WORKSPACE_ROOT = "/workspace"


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


def _under_workspace(abs_path: str) -> bool:
    """True if *abs_path* is ``/workspace`` or a path strictly under it."""
    if abs_path == _WORKSPACE_ROOT:
        return True
    return abs_path.startswith(_WORKSPACE_ROOT + "/")


def resolve_sandbox_write_path(artifact: Artifact) -> tuple[str | None, str | None]:
    """Return ``(abs_path, error_reason)`` for best-effort sandbox write.

    Writes are restricted to ``/workspace`` (and children) after normalization.
    """
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
    if not target.startswith("/") or not _under_workspace(target):
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
    except Exception as exc:
        logger.exception("Failed listing artifact objects for {}", artifact_id)
        raise ArtifactContentError(
            "list_failed",
            "Could not verify artifact contents; retry later",
        ) from exc
    # Multi-file version: reject (v1) so we do not shrink the version tree.
    if len(existing) > 1:
        raise ArtifactContentError(
            "multi_file",
            "Editing multi-file directory artifacts is not supported",
        )

    next_version = expected_version + 1
    staging_key = (
        f"artifacts/{conversation_id}/{artifact_id}/staging/{secrets.token_hex(16)}/{filename}"
    )
    final_key = f"artifacts/{conversation_id}/{artifact_id}/v{next_version}/{filename}"
    mime = artifact.mime_type or mimetypes.guess_type(filename)[0] or "text/markdown"

    # Staging first: durable copy until final key is written under the row lock.
    # Concurrent losers never share a final version key.
    await store.upload_file(staging_key, content_bytes, content_type=mime)

    try:
        bumped = await _cas_promote_and_commit(
            session,
            store=store,
            org_id=org_id,
            workspace_id=workspace_id,
            artifact_id=artifact_id,
            expected_version=expected_version,
            mime=mime,
            final_key=final_key,
            content_bytes=content_bytes,
        )
        if bumped is None:
            raise ArtifactContentError(
                "version_conflict",
                f"Version conflict: expected {expected_version}",
            )
    except ArtifactContentError:
        raise
    except Exception:
        logger.exception("Failed CAS/promote for artifact {}", artifact_id)
        raise
    finally:
        # Safe after success (final exists) or failure (no DB bump).
        try:
            await store.delete_file(staging_key)
        except Exception:
            logger.warning("Failed to GC staging object: {}", staging_key)

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


async def _cas_promote_and_commit(
    session: AsyncSession,
    *,
    store: Any,
    org_id: str,
    workspace_id: str,
    artifact_id: str,
    expected_version: int,
    mime: str,
    final_key: str,
    content_bytes: bytes,
) -> Artifact | None:
    """Under row lock: write final object, then CAS bump + version row, commit.

    Order guarantees the published version always has a durable object: the DB
    is only advanced after ``final_key`` upload succeeds. Concurrent losers
    block on FOR UPDATE, then see a version mismatch and leave without writing
    the final key.
    """
    stmt = (
        select(Artifact)
        .where(
            cast(Any, Artifact.id) == artifact_id,
            cast(Any, Artifact.org_id) == org_id,
            cast(Any, Artifact.workspace_id) == workspace_id,
        )
        .with_for_update()
    )
    result = await session.execute(stmt)
    artifact = result.scalar_one_or_none()
    if artifact is None or artifact.version != expected_version:
        await session.rollback()
        return None

    # Publish object before DB so a crash never leaves version pointing at void.
    await store.upload_file(final_key, content_bytes, content_type=mime)

    artifact.version = expected_version + 1
    artifact.updated_at = datetime.now(UTC)
    session.add(
        ArtifactVersion(
            org_id=org_id,
            workspace_id=workspace_id,
            artifact_id=artifact.id,
            version=artifact.version,
            name=artifact.name,
            description=artifact.description,
            path=artifact.path,
            entry_file=artifact.entry_file,
            mime_type=artifact.mime_type or mime,
        )
    )
    await session.commit()
    await session.refresh(artifact)
    return artifact


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
