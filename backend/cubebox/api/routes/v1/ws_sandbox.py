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
from datetime import datetime
from typing import Annotated, Any, cast
from urllib.parse import quote

import orjson
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
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


async def _assert_personal_or_conv_access(
    session: AsyncSession,
    ctx: RequestContext,
    conversation_id: str,
    creator_user_id: str,
) -> None:
    """Caller must be the creator OR a conversation_participant. 404 on miss."""
    if creator_user_id == ctx.user.id:
        return
    from cubebox.models.conversation_participant import ConversationParticipant

    cp_stmt = select(cast(Any, ConversationParticipant.user_id)).where(
        cast(Any, ConversationParticipant.conversation_id) == conversation_id,
        cast(Any, ConversationParticipant.user_id) == ctx.user.id,
    )
    if (await session.execute(cp_stmt)).scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")


async def _assert_topic_access(
    session: AsyncSession,
    ctx: RequestContext,
    topic_id: str,
    conversation_id: str,
) -> None:
    """Caller must be a topic_participant OR a conversation_participant.

    Covers two routes into a topic conv: full topic membership AND the
    single-conv invite case where only the specific conversation has the
    user as an actor.
    """
    from cubebox.models.conversation_participant import ConversationParticipant
    from cubebox.models.topic import TopicParticipant

    tp_stmt = select(cast(Any, TopicParticipant.user_id)).where(
        cast(Any, TopicParticipant.topic_id) == topic_id,
        cast(Any, TopicParticipant.user_id) == ctx.user.id,
    )
    if (await session.execute(tp_stmt)).scalar_one_or_none() is not None:
        return
    cp_stmt = select(cast(Any, ConversationParticipant.user_id)).where(
        cast(Any, ConversationParticipant.conversation_id) == conversation_id,
        cast(Any, ConversationParticipant.user_id) == ctx.user.id,
    )
    if (await session.execute(cp_stmt)).scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")


async def _resolve_sandbox_scope(
    session: AsyncSession, ctx: RequestContext, conversation_id: str | None
) -> tuple[str, str]:
    """Resolve the polymorphic ``(scope_type, scope_id)`` for a sandbox lookup.

    Frontend hooks pass the current conversation id so we can route file /
    terminal / download / preview / status requests to whichever sandbox
    the agent is actually using:

    - no conversation: caller's personal sandbox (``'user'``)
    - personal conv where caller is creator and not group_chat: caller's
      personal sandbox (``'user'``)
    - standalone group chat (no topic, is_group_chat=True): conversation-
      keyed sandbox (``'conversation'``)
    - dedicated-mode topic: topic-keyed sandbox (``'topic'``)
    - creator-mode topic (or default): topic creator's personal sandbox
      (``'user'``)

    Raises 404 when the caller passes a conversation_id they have no
    access to.
    """
    if conversation_id is None:
        return "user", ctx.user.id
    from cubebox.models.conversation import Conversation
    from cubebox.models.topic import Topic

    conv_stmt = select(
        cast(Any, Conversation.topic_id),
        cast(Any, Conversation.creator_user_id),
        cast(Any, Conversation.is_group_chat),
    ).where(
        cast(Any, Conversation.id) == conversation_id,
        cast(Any, Conversation.workspace_id) == ctx.workspace_id,
    )
    row = (await session.execute(conv_stmt)).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    topic_id, creator_user_id, is_group_chat = row
    if topic_id is None:
        await _assert_personal_or_conv_access(session, ctx, conversation_id, str(creator_user_id))
        if is_group_chat:
            return "conversation", conversation_id
        return "user", ctx.user.id

    await _assert_topic_access(session, ctx, str(topic_id), conversation_id)
    topic_stmt = select(
        cast(Any, Topic.sandbox_mode),
        cast(Any, Topic.creator_user_id),
    ).where(
        cast(Any, Topic.id) == topic_id,
        cast(Any, Topic.is_archived).is_(False),
    )
    topic_row = (await session.execute(topic_stmt)).first()
    if topic_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    mode, topic_creator_user_id = topic_row

    effective_mode = mode or "creator"
    if effective_mode == "dedicated":
        return "topic", str(topic_id)
    return "user", str(topic_creator_user_id or ctx.user.id)


@router.get("/status", response_model=SandboxStatusOut)
async def get_sandbox_status(
    workspace_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    conversation_id: str | None = Query(default=None),
) -> SandboxStatusOut:
    """Return the active sandbox row for this scope, or absent.

    With ``conversation_id`` set to a dedicated-mode topic conversation,
    returns the topic-keyed sandbox row instead of the caller's personal
    one so the panel reflects what the agent is actually using.
    """
    repo = UserSandboxRepository(session, org_id=ctx.org_id, workspace_id=workspace_id)
    scope_type, scope_id = await _resolve_sandbox_scope(session, ctx, conversation_id)
    row = await repo.get_active_by_scope(scope_type=scope_type, scope_id=scope_id)
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
    session: Annotated[AsyncSession, Depends(get_session)],
    path: str = Query(default="/workspace"),
    pattern: str = Query(default="*"),
    conversation_id: str | None = Query(default=None),
) -> list[SandboxFileEntry]:
    """List direct children of a directory in the sandbox."""
    normalized = posixpath.normpath(path)
    if not (normalized == "/workspace" or normalized.startswith("/workspace/")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path outside workspace",
        )
    manager = get_sandbox_manager()
    scope_type, scope_id = await _resolve_sandbox_scope(session, ctx, conversation_id)
    try:
        sandbox = await manager.get_or_create(
            scope_type=scope_type,
            scope_id=scope_id,
            user_id=ctx.user.id,
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

    # The search API returns only file entries (never directories), so we
    # derive immediate child directories from the paths of deeper entries.
    direct_files: list[SandboxFileEntry] = []
    child_dirs: dict[str, datetime] = {}  # dir_name → latest modified_at

    for e in entries:
        entry_path = posixpath.normpath(e.path)
        parent = posixpath.dirname(entry_path)
        if parent == normalized:
            name = posixpath.basename(entry_path)
            if name and not name.startswith("."):
                direct_files.append(
                    SandboxFileEntry(
                        path=entry_path,
                        name=name,
                        is_dir=False,
                        size=e.size,
                        modified_at=utc_isoformat(e.modified_at),
                    )
                )
        else:
            # Entry is deeper — extract the immediate child directory name.
            rel = posixpath.relpath(entry_path, normalized)
            dir_name = rel.split("/")[0]
            if not dir_name or dir_name.startswith("."):
                continue
            if dir_name not in child_dirs:
                child_dirs[dir_name] = e.modified_at
            elif dir_name and e.modified_at > child_dirs[dir_name]:
                child_dirs[dir_name] = e.modified_at

    dir_entries = [
        SandboxFileEntry(
            path=posixpath.join(normalized, name),
            name=name,
            is_dir=True,
            size=0,
            modified_at=utc_isoformat(ts),
        )
        for name, ts in child_dirs.items()
    ]

    result = dir_entries + direct_files
    result.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return result


# ── File content & download ────────────────────────────────────────

MAX_PREVIEW_BYTES = 1_048_576  # 1 MB


class SandboxFileContent(BaseModel):
    content: str
    mime_type: str


@router.get("/files/content", response_model=SandboxFileContent)
async def get_sandbox_file_content(
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    path: str = Query(...),
    conversation_id: str | None = Query(default=None),
) -> SandboxFileContent:
    """Read a text file from the sandbox for inline preview."""
    normalized = posixpath.normpath(path)
    if not (normalized == "/workspace" or normalized.startswith("/workspace/")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path outside workspace",
        )
    manager = get_sandbox_manager()
    scope_type, scope_id = await _resolve_sandbox_scope(session, ctx, conversation_id)
    try:
        sandbox = await manager.get_or_create(
            scope_type=scope_type,
            scope_id=scope_id,
            user_id=ctx.user.id,
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
    session: Annotated[AsyncSession, Depends(get_session)],
    path: str = Query(...),
    conversation_id: str | None = Query(default=None),
) -> StreamingResponse:
    """Stream a file from the sandbox as a download."""
    normalized = posixpath.normpath(path)
    if not (normalized == "/workspace" or normalized.startswith("/workspace/")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path outside workspace",
        )
    manager = get_sandbox_manager()
    scope_type, scope_id = await _resolve_sandbox_scope(session, ctx, conversation_id)
    try:
        sandbox = await manager.get_or_create(
            scope_type=scope_type,
            scope_id=scope_id,
            user_id=ctx.user.id,
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
    session: Annotated[AsyncSession, Depends(get_session)],
    rh: Annotated[RedisHandle, Depends(redis_dep)],
    path: str = Query(...),
    conversation_id: str | None = Query(default=None),
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
    scope_type, scope_id = await _resolve_sandbox_scope(session, ctx, conversation_id)
    try:
        sandbox = await manager.get_or_create(
            scope_type=scope_type,
            scope_id=scope_id,
            user_id=ctx.user.id,
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
    session: Annotated[AsyncSession, Depends(get_session)],
    conversation_id: str | None = Query(default=None),
) -> SandboxTerminalResponse:
    """Start ttyd in the sandbox and return a signed URL."""
    manager = get_sandbox_manager()
    scope_type, scope_id = await _resolve_sandbox_scope(session, ctx, conversation_id)
    try:
        sandbox = await manager.get_or_create(
            scope_type=scope_type,
            scope_id=scope_id,
            user_id=ctx.user.id,
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
