"""IM-side artifact dispatcher.

When the run emits an ``artifact`` event the tailer hands the artifact dict
here. We decide:

- ``image`` → fetch bytes from the artifact store, upload to Feishu (native
  image message), no link needed.
- Anything else → mint a public share token (via the same service the HTTP
  route uses — no auth hop), post a short link message in the same thread.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError
from loguru import logger

from cubebox.objectstore import get_objectstore_client
from cubebox.services.artifact_share import mint_share_token


@dataclass(slots=True)
class IMArtifactDispatcher:
    """Bound to one run's outbound conversation context.

    Construction is cheap and per-run — the bound ``connector`` is the same
    FeishuConnector instance the tailer drives, so image/file/share messages
    all post into the right thread.
    """

    connector: Any
    redis: Any
    redis_key_prefix: str
    public_base_url: str
    org_id: str
    workspace_id: str
    conversation_id: str

    async def handle(self, artifact: dict[str, Any]) -> None:
        atype = str(artifact.get("artifact_type") or "")
        name = str(artifact.get("name") or "artifact")
        artifact_id = str(artifact.get("id") or "")
        if not artifact_id:
            return

        if atype == "image":
            await self._send_image(artifact)
            return
        await self._send_share_link(artifact_id, name, atype, artifact)

    async def _send_image(self, artifact: dict[str, Any]) -> None:
        """Download the image bytes from the artifact store and post a native image message."""
        version = int(artifact.get("version") or 1)
        artifact_id = str(artifact.get("id") or "")
        entry = str(artifact.get("entry_file") or "")
        path = str(artifact.get("path") or "")
        filename = entry or path.rsplit("/", 1)[-1]
        key = f"artifacts/{self.conversation_id}/{artifact_id}/v{version}/{filename}"
        try:
            store = get_objectstore_client()
            data, _ctype = await store.download_file(key)
        except (ClientError, Exception):  # broad catch — fall back to link
            logger.warning(
                "[IM artifacts] failed to download image artifact {}; falling back to share link",
                artifact_id,
                exc_info=True,
            )
            await self._send_share_link(
                artifact_id, str(artifact.get("name") or ""), "image", artifact
            )
            return

        suffix = Path(filename).suffix or ".png"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            image_key = await self.connector.upload_image(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        if not image_key:
            await self._send_share_link(
                artifact_id, str(artifact.get("name") or ""), "image", artifact
            )
            return
        # ``send_image_message`` can still fail after upload (transient
        # Feishu rejection, quota, network) and returns None / raises in
        # that case. Without this fallback the user sees a tool_call line
        # ("running image_gen…") but no rendered image and no share link —
        # the artifact effectively disappears. Mirror the upload-failure
        # branch above: post the share-link bubble so the file is still
        # reachable.
        try:
            sent = await self.connector.send_image_message(image_key)
        except Exception:
            logger.warning(
                "[IM artifacts] send_image_message raised for {}; falling back to share link",
                artifact_id,
                exc_info=True,
            )
            sent = None
        if not sent:
            await self._send_share_link(
                artifact_id, str(artifact.get("name") or ""), "image", artifact
            )

    async def _send_share_link(
        self,
        artifact_id: str,
        name: str,
        atype: str,
        artifact: dict[str, Any],
    ) -> None:
        # An absolute public URL is required: the background IM tailer has no
        # FastAPI Request to derive a base URL from, so an empty
        # ``public_base_url`` would produce a relative path the Feishu client
        # can't open. Skip the share-link entirely and log loudly so the
        # operator sees a config gap rather than a silently-broken bot.
        base = self.public_base_url.rstrip("/") if self.public_base_url else ""
        if not (base.startswith("http://") or base.startswith("https://")):
            logger.warning(
                "[IM artifacts] cannot post share link for artifact {} — "
                "api.public_url is not an absolute URL ({!r}); skipping link",
                artifact_id,
                self.public_base_url,
            )
            return
        version = int(artifact.get("version") or 1)
        nonce = await mint_share_token(
            redis=self.redis,
            key_prefix=self.redis_key_prefix,
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            conversation_id=self.conversation_id,
            artifact_id=artifact_id,
            version=version,
            name=str(artifact.get("name") or "") or None,
            artifact_type=str(artifact.get("artifact_type") or atype or "") or None,
            entry_file=str(artifact.get("entry_file") or "") or None,
        )
        share_url = f"{base}/api/v1/public/artifacts/share/{nonce}"
        label = atype or "artifact"
        await self.connector.send_text_message(f"📎 {name} · {label} · view → {share_url}")
