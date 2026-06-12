"""Public artifact preview page + file-content endpoint (no auth; nonce-gated).

The nonce IS the auth. Tokens are bound to ``(org_id, workspace_id,
conversation_id, artifact_id, version)`` and expire after 7 days (see
``services/artifact_share.py``). A leaked link exposes one artifact for
≤ 1 week — bounded blast radius.
"""

from __future__ import annotations

import html
import mimetypes
from typing import Annotated
from urllib.parse import quote

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, Response
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.cache import RedisHandle, redis_dep
from cubebox.db import get_session
from cubebox.objectstore import get_objectstore_client
from cubebox.repositories import ArtifactRepository
from cubebox.services.artifact_share import resolve_share_token

router = APIRouter(prefix="/public/artifacts", tags=["artifact-share"])


def _expired_html(title: str = "Link expired") -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font-family:system-ui;color:#374151;background:#f9fafb;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{background:white;padding:32px 40px;border-radius:12px;
box-shadow:0 1px 3px rgba(0,0,0,0.08);max-width:420px;text-align:center}}
h1{{margin:0 0 8px;font-size:18px}}p{{margin:0;color:#6b7280;font-size:14px}}
</style></head><body><div class="card">
<h1>{html.escape(title)}</h1>
<p>This share link has expired or is invalid.</p>
</div></body></html>"""


@router.get("/share/{nonce}", response_class=HTMLResponse)
async def share_page(
    nonce: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    rh: Annotated[RedisHandle, Depends(redis_dep)],
) -> HTMLResponse:
    """Render a minimal read-only preview page for the artifact behind ``nonce``."""
    payload = await resolve_share_token(redis=rh.client, key_prefix=rh.key_prefix, nonce=nonce)
    if payload is None:
        return HTMLResponse(_expired_html(), status_code=status.HTTP_404_NOT_FOUND)
    org_id = str(payload["org_id"])
    workspace_id = str(payload["workspace_id"])
    conversation_id = str(payload["conversation_id"])
    artifact_id = str(payload["artifact_id"])
    version = int(str(payload["version"]))

    repo = ArtifactRepository(session, org_id=org_id, workspace_id=workspace_id)
    artifact = await repo.get_by_id(artifact_id)
    if artifact is None or artifact.conversation_id != conversation_id:
        return HTMLResponse(
            _expired_html("Artifact not found"), status_code=status.HTTP_404_NOT_FOUND
        )

    name = artifact.name or "artifact"
    artifact_type = artifact.artifact_type or "file"
    entry_file = artifact.entry_file or artifact.path.rsplit("/", 1)[-1]

    body = _render_body(
        nonce=nonce,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        entry_file=entry_file,
        version=version,
    )
    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(name)}</title>
<style>
:root{{color-scheme:light dark}}
body{{font-family:system-ui;color:#1f2937;background:#f9fafb;margin:0;padding:24px}}
header{{margin-bottom:16px}}
h1{{margin:0;font-size:18px}}
.badge{{display:inline-block;font-size:12px;background:#e5e7eb;color:#374151;
border-radius:9999px;padding:2px 10px;margin-left:8px;vertical-align:middle}}
main{{background:white;border:1px solid #e5e7eb;border-radius:12px;
overflow:hidden}}
main img{{display:block;max-width:100%;height:auto}}
main pre{{margin:0;padding:16px;overflow:auto;font-size:13px}}
iframe{{display:block;width:100%;height:80vh;border:0}}
.empty{{padding:32px;text-align:center;color:#6b7280}}
.empty a{{color:#2563eb;text-decoration:none}}
</style></head><body>
<header><h1>{html.escape(name)}<span class="badge">{html.escape(artifact_type)}</span></h1></header>
<main>{body}</main>
</body></html>"""
    return HTMLResponse(page)


def _render_body(
    *,
    nonce: str,
    artifact_id: str,
    artifact_type: str,
    entry_file: str,
    version: int,
) -> str:
    """Pick the best inline renderer for the artifact type.

    ``entry_file`` is percent-encoded for URL path use (``urllib.parse.quote``
    with ``safe='/'``) — NOT html-escaped. ``html.escape`` would turn ``&``
    into ``&amp;`` and leave ``?``/``#``/whitespace intact, producing
    URL-significant characters that the iframe browser would mis-parse
    (querystring/fragment truncation, mis-routed requests, blank previews).
    """
    safe_entry = quote(entry_file, safe="/")
    file_url = f"/api/v1/public/artifacts/share/{nonce}/file/{safe_entry}"
    if artifact_type == "image":
        return f'<img src="{file_url}" alt="">'
    if artifact_type == "website":
        # Scripts allowed (websites are interactive) but NOT same-origin —
        # MDN explicitly notes that `allow-scripts allow-same-origin`
        # together is equivalent to no sandbox at all. Letting attacker /
        # agent-authored HTML read same-origin cookies (including the
        # non-HttpOnly CSRF cookie of a logged-in cubebox session that
        # happens to open the share link) is a real exfiltration path.
        return f'<iframe src="{file_url}" sandbox="allow-scripts"></iframe>'
    if artifact_type in {"code", "document", "data", "skill"}:
        return f'<iframe src="{file_url}" sandbox></iframe>'
    # Visible link text IS user-facing HTML — keep html.escape there.
    return f'<div class="empty"><a href="{file_url}">Download {html.escape(entry_file)}</a></div>'


@router.get("/share/{nonce}/file/{file_path:path}")
async def share_file(
    nonce: str,
    file_path: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    rh: Annotated[RedisHandle, Depends(redis_dep)],
) -> Response:
    """Serve a single file from the artifact behind ``nonce``.

    Auth is the nonce alone — the bound (org, workspace, conv, artifact,
    version) is what the request was signed for. Path-traversal is rejected
    inline; only files under the artifact's object-store key prefix are
    reachable, even with a tampered ``file_path``.
    """
    payload = await resolve_share_token(redis=rh.client, key_prefix=rh.key_prefix, nonce=nonce)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="share link expired")
    if ".." in file_path or file_path.startswith("/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid file path")
    org_id = str(payload["org_id"])
    workspace_id = str(payload["workspace_id"])
    conversation_id = str(payload["conversation_id"])
    artifact_id = str(payload["artifact_id"])
    version = int(str(payload["version"]))

    repo = ArtifactRepository(session, org_id=org_id, workspace_id=workspace_id)
    artifact = await repo.get_by_id(artifact_id)
    if artifact is None or artifact.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="artifact not found")

    # If the caller asked for the entry_file by name, accept that; otherwise
    # treat as a relative path inside the artifact directory.
    target = file_path
    if not target:
        target = artifact.entry_file or artifact.path.rsplit("/", 1)[-1]
    key = f"artifacts/{conversation_id}/{artifact_id}/v{version}/{target}"

    try:
        store = get_objectstore_client()
        data, stored_content_type = await store.download_file(key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="file not found"
            ) from None
        logger.error("share_file ClientError: {}", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="failed to serve file"
        ) from None
    except Exception as exc:
        logger.error("share_file error: {}", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="failed to serve file"
        ) from None

    mime, _ = mimetypes.guess_type(target)
    media_type = mime or stored_content_type or "application/octet-stream"
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )
