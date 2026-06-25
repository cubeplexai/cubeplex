"""Inbound IM file resolution: platform file handle → cubebox attachment id.

Built as a closure in ``runtime.py`` (where the secret cache + lark client
factory live) and injected into the run-queue worker. It cannot live on the
registry connector: that connector is a stateless dispatcher with no
credentials. The worker resolves before ``start_run`` and persists the
resulting ids for re-claim idempotency.

See docs/dev/specs/2026-06-24-im-file-transfer-design.md.
"""

from __future__ import annotations

import asyncio
import mimetypes
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select

from cubebox.api.exceptions import (
    AttachmentMimeRejectedError,
    AttachmentQuotaExceededError,
    AttachmentTooLargeError,
)
from cubebox.config import config
from cubebox.im.types import InboundAttachmentRef
from cubebox.models.im_connector import IMConnectorAccount, IMRunQueueItem
from cubebox.parsers.mime import sniff_mime
from cubebox.repositories import AttachmentRepository
from cubebox.services.attachments import AttachmentService

_DOWNLOAD_TIMEOUT = 30.0

# Resolver signature: (queue item, uploader user id) -> (attachment ids, notes).
# ``notes`` are short user-facing lines for attachments that were rejected /
# skipped, prepended to the run content so the agent (and user) knows.
ResolveInboundAttachments = Callable[[IMRunQueueItem, str], Awaitable[tuple[list[str], list[str]]]]

# Per-account decrypted secrets and lark client factory, both built in runtime.py.
LoadSecrets = Callable[[IMConnectorAccount], Awaitable[dict[str, Any]]]
ClientFor = Callable[[tuple[str, str], dict[str, Any]], Any]


class DownloadError(Exception):
    """A platform file resource could not be fetched — note-and-skip."""


def _lark_type(kind: str) -> str:
    # message_resource.get's ``type`` must match the resource kind, not MIME.
    return "image" if kind == "image" else "file"


def _normalize_mime(mime: str | None) -> str | None:
    """Strip charset/parameters so the exact-match allowlist sees a bare mime
    (Discord/Slack send e.g. ``text/plain; charset=utf-8``)."""
    if not mime:
        return None
    return mime.split(";", 1)[0].strip() or None


def _effective_name_mime(ref: InboundAttachmentRef, data: bytes) -> tuple[str, str | None]:
    """Resolve the filename + mime to store.

    Images (and any ref without a declared mime) get content-sniffed via the
    project's ``sniff_mime`` (libmagic), so a Feishu JPEG/GIF/WebP isn't stored
    as image/png and an extensionless file gets a real type. The declared mime
    is otherwise normalized (charset stripped).
    """
    declared = _normalize_mime(ref.mime)
    if ref.kind != "image" and declared:
        return ref.filename, declared
    sniffed = _normalize_mime(sniff_mime(ref.filename, data))
    mime = sniffed or declared
    filename = ref.filename
    if mime:
        ext = mimetypes.guess_extension(mime)
        if ext and Path(filename).suffix.lower() != ext.lower():
            stem = Path(filename).stem
            if not stem or stem.startswith("."):
                stem = ref.kind  # "image" / "file"
            filename = f"{stem}{ext}"
    return filename, mime


async def _download_feishu(client: Any, ref: InboundAttachmentRef, message_id: str | None) -> bytes:
    if not message_id:
        raise DownloadError("feishu download needs a non-empty message_id")
    from lark_oapi.api.im.v1 import GetMessageResourceRequest

    def _do() -> Any:
        req = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(ref.handle)
            .type(_lark_type(ref.kind))
            .build()
        )
        return client.im.v1.message_resource.get(req)

    resp = await asyncio.to_thread(_do)
    if not getattr(resp, "success", lambda: False)():
        raise DownloadError(
            f"feishu message_resource.get failed: code={getattr(resp, 'code', None)}"
        )
    file_obj = getattr(resp, "file", None)
    if file_obj is None:
        raise DownloadError("feishu message_resource.get returned no file")
    return file_obj.read() if hasattr(file_obj, "read") else bytes(file_obj)


async def _download_url(
    url: str,
    headers: dict[str, str] | None = None,
    *,
    expected_mime: str | None = None,
    reject_unexpected_html: bool = False,
) -> bytes:
    """Stream a URL to bytes with a hard size cap.

    The cap (``attachments.max_file_bytes``) bounds memory so an unbounded
    Slack/Discord file (no reliable ``size_hint``) can't OOM the worker. When
    ``reject_unexpected_html`` is set and the response is ``text/html`` but the
    file's declared mime is NOT html, treat it as a failure (Slack serves a 200
    sign-in page when the token can't read the file) — but a genuine ``.html``
    upload (declared ``text/html``) is allowed through.
    """
    max_bytes = int(config.get("attachments.max_file_bytes", 52428800))
    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT) as http:
        async with http.stream("GET", url, headers=headers, follow_redirects=True) as resp:
            if resp.status_code != 200:
                raise DownloadError(f"download {url[:64]} → HTTP {resp.status_code}")
            ctype = resp.headers.get("content-type", "").lower()
            if (
                reject_unexpected_html
                and ctype.startswith("text/html")
                and not (expected_mime or "").lower().startswith("text/html")
            ):
                raise DownloadError(f"download {url[:64]} returned an HTML page (auth/scope?)")
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise DownloadError(f"download {url[:64]} exceeds {max_bytes} bytes")
                chunks.append(chunk)
    return b"".join(chunks)


async def download_for(
    platform: str, client: Any, ref: InboundAttachmentRef, *, message_id: str | None
) -> bytes:
    """Resolve one platform file handle to bytes using the per-platform client."""
    if platform == "feishu":
        return await _download_feishu(client, ref, message_id)
    if platform == "slack":
        token = str(client or "")
        if not token:
            raise DownloadError("slack download needs a bot token")
        return await _download_url(
            ref.handle,
            {"Authorization": f"Bearer {token}"},
            expected_mime=ref.mime,
            reject_unexpected_html=True,
        )
    if platform == "discord":
        # Discord CDN URLs are pre-signed; no auth header.
        return await _download_url(ref.handle)
    raise DownloadError(f"unsupported platform for inbound download: {platform}")


def _client_for_download(
    account: IMConnectorAccount, secrets: dict[str, Any], client_for: ClientFor
) -> Any:
    """Pick the right client per platform. ``client_for`` is Feishu-only."""
    if account.platform == "feishu":
        return client_for((account.id, account.credential_id), secrets)
    if account.platform == "slack":
        return str(secrets.get("bot_token") or "")
    return None  # discord: CDN download, no client


def make_resolver(
    *,
    session_maker: async_sessionmaker[Any],
    load_secrets: LoadSecrets,
    client_for: ClientFor,
) -> ResolveInboundAttachments:
    """Build the closure injected into ``IMRunQueueWorker``."""

    async def resolve(item: IMRunQueueItem, uploader_user_id: str) -> tuple[list[str], list[str]]:
        refs = [InboundAttachmentRef.from_json(r) for r in (item.attachment_refs or [])]
        if not refs:
            return [], []
        max_bytes = int(config.get("attachments.max_file_bytes", 52428800))
        ids: list[str] = []
        notes: list[str] = []
        async with session_maker() as session:
            account = (
                await session.execute(
                    select(IMConnectorAccount).where(col(IMConnectorAccount.id) == item.account_id)
                )
            ).scalar_one()
            secrets = await load_secrets(account)
            client = _client_for_download(account, secrets, client_for)
            repo = AttachmentRepository(
                session, org_id=account.org_id, workspace_id=account.workspace_id
            )
            service = AttachmentService(repo=repo)
            for ref in refs:
                if ref.size_hint is not None and ref.size_hint > max_bytes:
                    notes.append(f"[附件 {ref.filename} 已忽略：超过大小限制]")
                    continue
                try:
                    data = await download_for(
                        account.platform, client, ref, message_id=item.inbound_message_id
                    )
                    filename, mime = _effective_name_mime(ref, data)
                    att = await service.upload(
                        conversation_id=item.conversation_id,
                        uploader_user_id=uploader_user_id,
                        filename=filename,
                        content=data,
                        mime_type=mime,
                    )
                    ids.append(att.id)
                except Exception as exc:
                    # Broad on purpose: an unexpected error on ONE ref must not
                    # abort resolve() and leave attachment_ids unpersisted — that
                    # would re-upload the already-stored refs on the next
                    # re-claim. Note-and-skip; surface via the logged traceback.
                    if not isinstance(
                        exc,
                        (
                            AttachmentTooLargeError,
                            AttachmentMimeRejectedError,
                            AttachmentQuotaExceededError,
                            DownloadError,
                        ),
                    ):
                        logger.opt(exception=True).warning(
                            "[IM inbound] unexpected error on attachment {} ({})",
                            ref.filename,
                            account.platform,
                        )
                    else:
                        logger.warning(
                            "[IM inbound] dropping attachment {} ({}): {}",
                            ref.filename,
                            account.platform,
                            exc,
                        )
                    notes.append(f"[附件 {ref.filename} 已忽略]")
        return ids, notes

    return resolve
