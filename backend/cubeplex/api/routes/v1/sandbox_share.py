"""Public sandbox file download -- nonce-gated, no auth.

The nonce IS the auth. Tokens are bound to (sandbox_id, file_path) and
expire after 5 minutes (see ws_sandbox.create_sandbox_preview_token).
The endpoint proxies the file from the live sandbox in real time -- no
temp storage.
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
    sandbox_id = str(payload["sandbox_id"])
    file_path = str(payload["file_path"])

    # Reconnect to the sandbox by ID. The manager's connection
    # config carries the API key and domain -- no user context
    # needed.
    manager = get_sandbox_manager()
    conn_config = manager._build_connection_config()  # noqa: SLF001
    try:
        sandbox = await OpenSandbox.connect_or_resume(sandbox_id, conn_config=conn_config)
        raw_sdk = sandbox._sandbox  # noqa: SLF001
        stream = await raw_sdk.files.read_bytes_stream(file_path)
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
