"""Workspace-scope sandbox endpoints.

- ``/status`` — read-only sandbox status for the workspace sandbox page.
- ``/files`` — list direct children of a sandbox directory.
- ``/files/content`` — read a text file for inline preview.
- ``/files/download`` — stream a file as a download.
- ``/terminal`` — start ttyd and return a signed URL.

Scope-isolated: no admin counterpart — admins see fleet-wide info via a
different surface.
"""

import mimetypes
import posixpath
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.sandbox_policy import (
    SandboxStatusOut,
    SandboxStatusValue,
)
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.db.session import get_session
from cubebox.repositories.user_sandbox import UserSandboxRepository
from cubebox.sandbox import SandboxError
from cubebox.sandbox.manager import get_sandbox_manager
from cubebox.sandbox.opensandbox import OpenSandbox
from cubebox.utils.time import utc_isoformat

router = APIRouter(prefix="/ws/{workspace_id}/sandbox", tags=["ws-sandbox"])


@router.get("/status", response_model=SandboxStatusOut)
async def get_sandbox_status(
    workspace_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> SandboxStatusOut:
    """Return the caller's active sandbox row in this workspace, or absent."""
    repo = UserSandboxRepository(session, org_id=ctx.org_id, workspace_id=workspace_id)
    row = await repo.get_active_by_user(ctx.user.id)
    if row is None:
        return SandboxStatusOut(
            status="absent",
            default_image=None,
            last_activity_at=None,
            browser_url=None,
        )
    return SandboxStatusOut(
        status=cast(SandboxStatusValue, row.status),
        default_image=row.image,
        last_activity_at=utc_isoformat(row.last_activity_at),
        browser_url=None,
    )


# ── File listing ────────────────────────────────────────────────────


class SandboxFileEntry(BaseModel):
    path: str
    name: str
    is_dir: bool
    size: int
    modified_at: str


@router.get("/files", response_model=list[SandboxFileEntry])
async def list_sandbox_files(
    ctx: Annotated[RequestContext, Depends(require_member)],
    path: str = Query(default="/workspace"),
    pattern: str = Query(default="*"),
) -> list[SandboxFileEntry]:
    """List direct children of a directory in the sandbox."""
    normalized = posixpath.normpath(path)
    if not normalized.startswith("/workspace"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path outside workspace",
        )
    manager = get_sandbox_manager()
    try:
        sandbox = await manager.get_or_create(
            ctx.user.id,
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
        )
        await manager.touch(
            sandbox.id,
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
        )
        # Direct SDK access for filesystem operations
        assert isinstance(sandbox, OpenSandbox)
        raw = sandbox._sandbox  # noqa: SLF001
        from opensandbox.models.filesystem import SearchEntry

        entries = await raw.files.search(SearchEntry(path=normalized, pattern=pattern))
    except SandboxError as exc:
        logger.warning(
            "sandbox file listing failed for ws {}: {}",
            ctx.workspace_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sandbox unavailable; please retry",
        ) from exc

    # Filter to direct children (SDK search is recursive)
    children = [e for e in entries if posixpath.dirname(posixpath.normpath(e.path)) == normalized]
    # Sort: directories first, then alphabetical
    children.sort(
        key=lambda e: (
            (e.mode & 0o40000) == 0,
            posixpath.basename(e.path).lower(),
        )
    )

    result: list[SandboxFileEntry] = []
    for e in children:
        name = posixpath.basename(e.path)
        if not name:
            continue
        result.append(
            SandboxFileEntry(
                path=e.path,
                name=name,
                is_dir=(e.mode & 0o40000) != 0,
                size=e.size,
                modified_at=utc_isoformat(e.modified_at),
            )
        )
    return result


# ── File content & download ────────────────────────────────────────

MAX_PREVIEW_BYTES = 1_048_576  # 1 MB


class SandboxFileContent(BaseModel):
    content: str
    mime_type: str


@router.get("/files/content", response_model=SandboxFileContent)
async def get_sandbox_file_content(
    ctx: Annotated[RequestContext, Depends(require_member)],
    path: str = Query(...),
) -> SandboxFileContent:
    """Read a text file from the sandbox for inline preview."""
    normalized = posixpath.normpath(path)
    if not normalized.startswith("/workspace"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path outside workspace",
        )
    manager = get_sandbox_manager()
    try:
        sandbox = await manager.get_or_create(
            ctx.user.id,
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
        )
        assert isinstance(sandbox, OpenSandbox)
        raw = sandbox._sandbox  # noqa: SLF001
        info_map = await raw.files.get_file_info([path])
        info = info_map.get(path)
        if info and info.size > MAX_PREVIEW_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="file too large for preview; use download instead",
            )
        content = await raw.files.read_file(path)
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="file not found",
        ) from None
    except SandboxError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sandbox unavailable",
        ) from exc

    mime, _ = mimetypes.guess_type(path)
    return SandboxFileContent(content=content, mime_type=mime or "text/plain")


@router.get("/files/download")
async def download_sandbox_file(
    ctx: Annotated[RequestContext, Depends(require_member)],
    path: str = Query(...),
) -> StreamingResponse:
    """Stream a file from the sandbox as a download."""
    normalized = posixpath.normpath(path)
    if not normalized.startswith("/workspace"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path outside workspace",
        )
    manager = get_sandbox_manager()
    try:
        sandbox = await manager.get_or_create(
            ctx.user.id,
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
        )
        assert isinstance(sandbox, OpenSandbox)
        raw = sandbox._sandbox  # noqa: SLF001
        stream = await raw.files.read_bytes_stream(path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="file not found",
        ) from None
    except SandboxError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sandbox unavailable",
        ) from exc

    filename = posixpath.basename(path)
    mime, _ = mimetypes.guess_type(filename)
    return StreamingResponse(
        stream,
        media_type=mime or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ── Terminal ─────────────────────────────────────────────────────────


class SandboxTerminalResponse(BaseModel):
    url: str


@router.get("/terminal", response_model=SandboxTerminalResponse)
async def get_terminal(
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> SandboxTerminalResponse:
    """Start ttyd in the sandbox and return a signed URL."""
    manager = get_sandbox_manager()
    try:
        sandbox = await manager.get_or_create(
            ctx.user.id,
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
        )
        await sandbox.start_terminal()
        await manager.touch(
            sandbox.id,
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
        )
        await manager.renew_lease(
            sandbox.id,
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
        )
        endpoint = await sandbox.get_terminal_endpoint()
    except SandboxError as exc:
        logger.warning(
            "terminal unavailable for workspace {}: {}",
            ctx.workspace_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sandbox unavailable; please retry",
        ) from exc
    if endpoint.headers:
        raise HTTPException(
            status_code=501,
            detail=("terminal endpoint requires header auth; not yet supported"),
        )
    return SandboxTerminalResponse(url=endpoint.url)
