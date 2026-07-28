"""Public sandbox file download -- nonce-gated, no auth.

The nonce IS the auth. Tokens are bound to (user_sandbox_id, file_path) and
expire after 5 minutes (see ws_sandbox.create_sandbox_preview_token).
The endpoint proxies the file from the live sandbox in real time -- no
temp storage. Binding to our own row ID rather than the opensandbox
container ID lets the fetch revive a sandbox that was paused or reaped
between minting the URL and the viewer following it.
"""

from __future__ import annotations

import mimetypes
import posixpath
from typing import Annotated

import orjson
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from loguru import logger

from cubeplex.cache import RedisHandle, redis_dep
from cubeplex.sandbox import SandboxError
from cubeplex.sandbox.manager import get_sandbox_manager
from cubeplex.sandbox.opensandbox import OpenSandbox

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
    try:
        attachment = await manager.ensure_running(user_sandbox_id)
        sandbox = attachment.sandbox
        if not isinstance(sandbox, OpenSandbox):
            raise SandboxError("file download requires OpenSandbox backend")
        stream = await sandbox._sandbox.files.read_bytes_stream(file_path)  # noqa: SLF001
    except Exception as exc:
        logger.warning("sandbox proxy download failed: {}", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sandbox unavailable",
        ) from exc

    stored_filename = posixpath.basename(file_path)
    mime, _ = mimetypes.guess_type(stored_filename)
    return StreamingResponse(
        stream,
        media_type=mime or "application/octet-stream",
        headers={
            "Content-Disposition": (f'inline; filename="{stored_filename}"'),
            "Cache-Control": "no-store",
        },
    )
