"""Workspace-scope sandbox endpoints.

- ``/status`` — read-only sandbox status for the workspace sandbox page.
- ``/files`` — list direct children of a sandbox directory.
- ``/files/content`` — read a text file for inline preview.
- ``/files/download`` — stream a file as a download.
- ``/files/preview-token`` — issue nonce for Office Online Viewer.
- ``/terminal`` — start ttyd and return a signed URL.

Scope-isolated: no admin counterpart — admins see fleet-wide info via a
different surface.
"""

import mimetypes
import posixpath
import secrets
from typing import Annotated, cast
from urllib.parse import quote

import orjson
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
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
from cubebox.cache import RedisHandle, redis_dep
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
    if not (normalized == "/workspace" or normalized.startswith("/workspace/")):
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
        if not isinstance(sandbox, OpenSandbox):
            raise SandboxError("filesystem operations require OpenSandbox backend")
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
    if not (normalized == "/workspace" or normalized.startswith("/workspace/")):
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
        if not isinstance(sandbox, OpenSandbox):
            raise SandboxError("filesystem operations require OpenSandbox backend")
        raw = sandbox._sandbox  # noqa: SLF001
        info_map = await raw.files.get_file_info([normalized])
        info = info_map.get(normalized)
        if info and info.size > MAX_PREVIEW_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="file too large for preview; use download instead",
            )
        content = await raw.files.read_file(normalized)
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

    mime, _ = mimetypes.guess_type(normalized)
    return SandboxFileContent(content=content, mime_type=mime or "text/plain")


@router.get("/files/download")
async def download_sandbox_file(
    ctx: Annotated[RequestContext, Depends(require_member)],
    path: str = Query(...),
) -> StreamingResponse:
    """Stream a file from the sandbox as a download."""
    normalized = posixpath.normpath(path)
    if not (normalized == "/workspace" or normalized.startswith("/workspace/")):
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
        if not isinstance(sandbox, OpenSandbox):
            raise SandboxError("filesystem operations require OpenSandbox backend")
        raw = sandbox._sandbox  # noqa: SLF001
        stream = await raw.files.read_bytes_stream(normalized)
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

    filename = posixpath.basename(normalized)
    mime, _ = mimetypes.guess_type(filename)
    return StreamingResponse(
        stream,
        media_type=mime or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ── Office preview token ───────────────────────────────────────────

SANDBOX_OTK_TTL_SECONDS = 300  # 5 minutes
OFFICE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}


class SandboxPreviewTokenResponse(BaseModel):
    download_url: str
    viewer_url: str


@router.post(
    "/files/preview-token",
    response_model=SandboxPreviewTokenResponse,
)
async def create_sandbox_preview_token(
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
    rh: Annotated[RedisHandle, Depends(redis_dep)],
    path: str = Query(...),
) -> SandboxPreviewTokenResponse:
    """Issue a one-time nonce for Office Online Viewer."""
    filename = posixpath.basename(path)
    ext = filename[filename.rfind(".") :].lower() if "." in filename else ""
    if ext not in OFFICE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"Office preview not supported for extension '{ext}'"),
        )

    manager = get_sandbox_manager()
    try:
        sandbox = await manager.get_or_create(
            ctx.user.id,
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
        )
    except SandboxError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sandbox unavailable",
        ) from exc

    nonce = secrets.token_hex(32)
    payload = orjson.dumps(
        {
            "sandbox_id": sandbox.id,
            "file_path": path,
            "org_id": ctx.org_id,
            "workspace_id": ctx.workspace_id,
            "user_id": str(ctx.user.id),
        }
    )
    key = f"{rh.key_prefix}:sandbox_otk:{nonce}"
    await rh.client.set(key, payload, ex=SANDBOX_OTK_TTL_SECONDS)

    from cubebox.config import config

    public_url = config.get("api.public_url", "")
    base = str(public_url).rstrip("/") if public_url else str(request.base_url).rstrip("/")
    dl_url = f"{base}/api/v1/public/sandbox/dl/{nonce}/{filename}"
    viewer_url = f"https://view.officeapps.live.com/op/embed.aspx?src={quote(dl_url, safe='')}"
    return SandboxPreviewTokenResponse(download_url=dl_url, viewer_url=viewer_url)


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
