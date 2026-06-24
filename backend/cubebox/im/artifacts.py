"""IM-side artifact dispatcher.

Two responsibilities:

- During the run, fold each artifact event into ``card_state.artifacts`` —
  inline image (where supported) or share-link, never a standalone message.
- At run terminal, deliver file-kind artifacts as **native file messages**
  (``deliver_terminal_files``), falling back to a share-link message on
  oversize / upload failure.

See docs/dev/specs/2026-06-24-im-file-transfer-design.md.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError
from loguru import logger

from cubebox.config import config
from cubebox.im.artifact_delivery import artifact_outbound_kind, outbound_size_cap
from cubebox.im.card_model import ArtifactItem, CardState
from cubebox.im.types import OutboundConnector
from cubebox.objectstore import get_objectstore_client
from cubebox.services.artifact_share import mint_share_token as _default_mint

MintShareToken = Callable[..., Awaitable[str]]

# Per-file native-send timeout. A hung platform upload can't stall terminal
# teardown longer than this (delivery runs after the run is marked succeeded).
_SEND_TIMEOUT = 60.0


async def download_artifact_to_tempfile(
    conversation_id: str, artifact: dict[str, Any]
) -> Path | None:
    """Download an artifact's bytes from the object store to a temp file.

    Shared by the inline-image path and the native-file path. Returns the temp
    Path (caller unlinks) or None if the object can't be fetched.
    """
    artifact_id = str(artifact.get("id") or "")
    version = int(artifact.get("version") or 1)
    entry = str(artifact.get("entry_file") or "")
    path = str(artifact.get("path") or "")
    filename = entry or path.rsplit("/", 1)[-1]
    if not filename:
        logger.warning(
            "[IM artifacts] artifact {} has no entry_file/path; cannot build key", artifact_id
        )
        return None
    # Build the key OUTSIDE the try so a key-construction bug propagates rather
    # than masquerading as a benign 'object missing → share-link' fallback.
    key = f"artifacts/{conversation_id}/{artifact_id}/v{version}/{filename}"
    try:
        store = get_objectstore_client()
        data, _ctype = await store.download_file(key)
    except (ClientError, OSError, ValueError):
        logger.opt(exception=True).warning(
            "[IM artifacts] objectstore download failed for {}", artifact_id
        )
        return None
    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        return Path(tmp.name)


@dataclass(slots=True)
class IMArtifactDispatcher:
    """Bound to one run's card_state + share-link minting + native-file context."""

    connector: OutboundConnector
    redis: Any
    redis_key_prefix: str
    public_base_url: str
    org_id: str
    workspace_id: str
    conversation_id: str
    card_state: CardState
    mint_share_token_fn: MintShareToken = _default_mint
    run_id: str = ""
    platform: str = ""
    chat_id: str = ""
    reply_to_id: str | None = None
    supports_inline_image: bool = False
    # Raw artifact payloads captured at handle() time for terminal native-file
    # delivery (ArtifactItem on card_state lacks version/entry_file).
    _file_artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def handle(self, artifact: dict[str, Any]) -> None:
        artifact_id = str(artifact.get("id") or "")
        if not artifact_id:
            return
        atype = str(artifact.get("artifact_type") or "")
        name = str(artifact.get("name") or "artifact")
        item = next((a for a in self.card_state.artifacts if a.id == artifact_id), None)
        if item is None:
            item = ArtifactItem(id=artifact_id, artifact_type=atype, name=name)
            self.card_state.artifacts.append(item)

        kind = artifact_outbound_kind(atype)
        if kind == "file":
            # Capture for terminal native delivery; do NOT mint a share-link now
            # (that would double-deliver: in-card link + native file bubble).
            self._file_artifacts[artifact_id] = artifact
            return
        if kind == "image" and self.supports_inline_image:
            await self._fill_image_key(item, artifact)
            return
        await self._fill_share_url(item, artifact)

    async def _fill_image_key(self, item: ArtifactItem, artifact: dict[str, Any]) -> None:
        tmp_path = await download_artifact_to_tempfile(self.conversation_id, artifact)
        if tmp_path is None:
            await self._fill_share_url(item, artifact)
            return
        try:
            image_key = await self.connector.upload_image(str(tmp_path))
        finally:
            tmp_path.unlink(missing_ok=True)
        if image_key:
            item.image_key = image_key
        else:
            await self._fill_share_url(item, artifact)

    async def _mint_share_url(self, item: ArtifactItem, artifact: dict[str, Any]) -> str | None:
        base = self.public_base_url.rstrip("/") if self.public_base_url else ""
        if not (base.startswith("http://") or base.startswith("https://")):
            logger.warning(
                "[IM artifacts] cannot mint share link for {} — public_base_url not absolute",
                item.id,
            )
            return None
        version = int(artifact.get("version") or 1)
        nonce = await self.mint_share_token_fn(
            redis=self.redis,
            key_prefix=self.redis_key_prefix,
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            conversation_id=self.conversation_id,
            artifact_id=item.id,
            version=version,
            name=item.name,
            artifact_type=item.artifact_type,
            entry_file=str(artifact.get("entry_file") or "") or None,
        )
        return f"{base}/api/v1/public/artifacts/share/{nonce}"

    async def _fill_share_url(self, item: ArtifactItem, artifact: dict[str, Any]) -> None:
        url = await self._mint_share_url(item, artifact)
        if url:
            item.share_url = url

    async def deliver_terminal_files(self) -> None:
        """Send captured file-kind artifacts as native messages, concurrently.

        Runs at run terminal (after the card is finalized + the run marked
        succeeded). Each send is idempotency-guarded by a Redis ``SET NX`` so a
        tailer restart / replay never double-sends.
        """
        if not self._file_artifacts:
            return
        await asyncio.gather(
            *(self._deliver_one(aid, art) for aid, art in self._file_artifacts.items()),
            return_exceptions=True,
        )

    async def _deliver_one(self, artifact_id: str, artifact: dict[str, Any]) -> None:
        if not await self._claim_send(artifact_id):
            return
        delivered = await self._try_deliver(artifact_id, artifact)
        if not delivered:
            # Nothing reached the user (native send AND share-link both failed).
            # Release the claim so a replay (tailer restart) can retry, instead
            # of the burned claim silently losing the artifact forever.
            await self._release_claim(artifact_id)

    async def _try_deliver(self, artifact_id: str, artifact: dict[str, Any]) -> bool:
        # fold_event normally appends the row; reconstruct from the payload if a
        # terminal replay rebuilt card_state without it, so the share-link path
        # always has a name to mint against.
        item = next((a for a in self.card_state.artifacts if a.id == artifact_id), None)
        if item is None:
            item = ArtifactItem(
                id=artifact_id,
                artifact_type=str(artifact.get("artifact_type") or ""),
                name=str(artifact.get("name") or "file"),
            )
        tmp_path = await download_artifact_to_tempfile(self.conversation_id, artifact)
        if tmp_path is None:
            return await self._fallback_link(item, artifact)
        ok = False
        try:
            if tmp_path.stat().st_size <= outbound_size_cap(self.platform):
                ok = bool(
                    await asyncio.wait_for(
                        self.connector.send_file(
                            local_path=str(tmp_path), filename=item.name, mime=None
                        ),
                        timeout=_SEND_TIMEOUT,
                    )
                )
        except Exception:
            logger.opt(exception=True).warning(
                "[IM artifacts] send_file failed for {}", artifact_id
            )
            ok = False
        finally:
            tmp_path.unlink(missing_ok=True)
        if ok:
            return True
        return await self._fallback_link(item, artifact)

    async def _claim_send(self, artifact_id: str) -> bool:
        """Atomic per-(run, artifact) send claim. True iff we won the right to send."""
        if self.redis is None or not self.run_id:
            return True
        ttl = int(config.get("streaming.run_event_ttl_seconds", 43200))
        return bool(await self.redis.set(self._claim_key(artifact_id), "1", nx=True, ex=ttl))

    async def _release_claim(self, artifact_id: str) -> None:
        if self.redis is None or not self.run_id:
            return
        try:
            await self.redis.delete(self._claim_key(artifact_id))
        except Exception:
            logger.opt(exception=True).warning("[IM artifacts] claim release failed")

    def _claim_key(self, artifact_id: str) -> str:
        return f"{self.redis_key_prefix}:im:artifact_sent:{self.run_id}:{artifact_id}"

    async def _fallback_link(self, item: ArtifactItem, artifact: dict[str, Any]) -> bool:
        """Send the artifact's share-link as a standalone message. Returns True
        iff the message was delivered. The card is already finalized, so we do
        NOT mutate ``item.share_url`` (dead write) — the message is the delivery.
        """
        url = await self._mint_share_url(item, artifact)
        if not url:
            return False
        try:
            sent = await self.connector.send_to_chat(
                self.chat_id, self.reply_to_id, f"📎 {item.name}: {url}"
            )
            return sent is not None
        except Exception:
            logger.opt(exception=True).warning("[IM artifacts] fallback send_to_chat failed")
            return False
