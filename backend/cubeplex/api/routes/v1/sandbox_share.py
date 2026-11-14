"""Public sandbox file download -- nonce-gated, no auth.

The nonce IS the auth. Tokens are bound to (user_sandbox_id, file_path) and
expire after 5 minutes (see ws_sandbox.create_sandbox_preview_token).
The endpoint proxies the file from the live sandbox in real time -- no
temp storage. Binding to our own row ID rather than the provider sandbox
ID lets the fetch revive a sandbox that was paused or reaped
between minting the URL and the viewer following it.
"""

from __future__ import annotations

import mimetypes
import posixpath
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Annotated

import orjson
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from loguru import logger
from starlette.background import BackgroundTask

from cubeplex.cache import RedisHandle, redis_dep
from cubeplex.sandbox import (
    SandboxCapabilityUnsupportedError,
    SandboxError,
    close_sandbox_handle,
)
from cubeplex.sandbox.manager import get_sandbox_manager

router = APIRouter(prefix="/public/sandbox", tags=["sandbox-share"])


@router.get("/dl/{nonce}/{filename}")
async def sandbox_file_download(
    nonce: str,
    filename: str,  # noqa: ARG001  — URL-visible; actual name comes from token
    rh: Annotated[RedisHandle, Depends(redis_dep)],
) -> StreamingResponse:
    """Proxy a sandbox file to Microsoft Office Online Viewer."""
    key = f"{rh.key_prefix}:sandbox_otk:{nonce}"
    raw = await rh.client.get(key)
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="download link expired",
        )
    payload = orjson.loads(raw)
    user_sandbox_id = str(payload["user_sandbox_id"])
    file_path = str(payload["file_path"])

    # The nonce records our stable UserSandbox.id, so a container that was
    # paused or reaped since the URL was minted is revived here rather than
    # failing the fetch.
    manager = get_sandbox_manager()
    stack = AsyncExitStack()
    streaming = False
    try:
        attachment = await manager.ensure_running(user_sandbox_id)
        sandbox = attachment.sandbox
        stack.push_async_callback(close_sandbox_handle, sandbox)
        stream = await stack.enter_async_context(sandbox.stream_file(file_path))

        async def chunks() -> AsyncIterator[bytes]:
            try:
                async for chunk in stream:
                    yield chunk
            finally:
                await stack.aclose()

        stored_filename = posixpath.basename(file_path)
        mime, _ = mimetypes.guess_type(stored_filename)
        response = StreamingResponse(
            chunks(),
            media_type=mime or "application/octet-stream",
            headers={
                "Content-Disposition": (f'inline; filename="{stored_filename}"'),
                "Cache-Control": "no-store",
            },
            background=BackgroundTask(stack.aclose),
        )
        streaming = True
        return response
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="file not found",
        ) from None
    except SandboxCapabilityUnsupportedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    except SandboxError as exc:
        logger.warning("sandbox proxy download failed: {}", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sandbox unavailable",
        ) from exc
    finally:
        if not streaming:
            await stack.aclose()
