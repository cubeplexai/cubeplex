"""IM-side artifact dispatcher — updates CardState, no standalone messages.

The dispatcher only mutates ``card_state.artifacts``. The tailer is
responsible for the subsequent ``patch_card`` op that re-renders the
card with the new artifact row.
"""

from __future__ import annotations

import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError
from loguru import logger

from cubebox.im.card_model import ArtifactItem, CardState
from cubebox.objectstore import get_objectstore_client
from cubebox.services.artifact_share import mint_share_token as _default_mint

MintShareToken = Callable[..., Awaitable[str]]


@dataclass(slots=True)
class IMArtifactDispatcher:
    """Bound to one run's card_state + share-link minting context."""

    connector: Any
    redis: Any
    redis_key_prefix: str
    public_base_url: str
    org_id: str
    workspace_id: str
    conversation_id: str
    card_state: CardState
    mint_share_token_fn: MintShareToken = _default_mint

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

        if atype == "image":
            await self._fill_image_key(item, artifact)
            return
        await self._fill_share_url(item, artifact)

    async def _fill_image_key(self, item: ArtifactItem, artifact: dict[str, Any]) -> None:
        version = int(artifact.get("version") or 1)
        entry = str(artifact.get("entry_file") or "")
        path = str(artifact.get("path") or "")
        filename = entry or path.rsplit("/", 1)[-1]
        key = f"artifacts/{self.conversation_id}/{item.id}/v{version}/{filename}"
        try:
            store = get_objectstore_client()
            data, _ctype = await store.download_file(key)
        except (ClientError, Exception):
            logger.opt(exception=True).warning(
                "[IM artifacts] download failed for {}; falling back to share link",
                item.id,
            )
            await self._fill_share_url(item, artifact)
            return
        suffix = Path(filename).suffix or ".png"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            image_key = await self.connector.upload_image(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        if image_key:
            item.image_key = image_key
        else:
            await self._fill_share_url(item, artifact)

    async def _fill_share_url(self, item: ArtifactItem, artifact: dict[str, Any]) -> None:
        base = self.public_base_url.rstrip("/") if self.public_base_url else ""
        if not (base.startswith("http://") or base.startswith("https://")):
            logger.warning(
                "[IM artifacts] cannot mint share link for {} — public_base_url not absolute",
                item.id,
            )
            return
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
        item.share_url = f"{base}/api/v1/public/artifacts/share/{nonce}"
