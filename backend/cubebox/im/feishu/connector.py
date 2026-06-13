"""Feishu connector: inbound parse + outbound send/edit/react (lark_oapi).

All synchronous lark_oapi SDK calls are wrapped in ``asyncio.to_thread`` so
they don't block the event loop the run-queue worker and other tailers
share. Hermes' prior art at ``~/hermes-agent/gateway/platforms/feishu.py``
established this discipline.

Outbound surface area:
- ``post_placeholder(text)`` -> message_id
- ``edit(message_id, text)``
- ``send_text_message(text)`` (non-edit send for auxiliary bubbles)
- ``upload_image(local_path)`` -> image_key
- ``send_image_message(image_key)``
- ``add_reaction(message_id, reaction_type)`` -> reaction_id
- ``remove_reaction(message_id, reaction_id)``

Plus the processing-status hooks (Task 10):
- ``on_processing_start(state)``
- ``on_processing_complete(state)``
- ``on_processing_failed(state)``

The tailer only ever calls the hooks — it never sees a Feishu emoji name
or API endpoint.
"""

import asyncio
import json
import re
from typing import Any

from loguru import logger

from cubebox.im.outbound import _FloodSignal
from cubebox.im.types import DM_SCOPE_KEY, InboundEvent, RenderState, make_participant_scope

# Matches Feishu inline mention markup: <at user_id="ou_xxx">name</at>
_AT_TAG_RE = re.compile(r"<at[^>]*>.*?</at>", re.DOTALL)

# Feishu reaction_type literals (no enum in the SDK). Names are UPPERCASE
# in Feishu's emoji_type set — mixed case (e.g. "ThumbsUp") is rejected with
# code=231001 "reaction type is invalid".
_REACTION_PROCESSING = "THUMBSUP"
_REACTION_FAILURE = "OK"  # Cross-mark variants are not universally available;
# "OK" intentionally signals "the run finished" with a neutral marker so the
# user knows something ended even on failure; the inline error message in the
# bot's reply text carries the actual error detail.

# lark_oapi response codes that mean "rate limited / flood control".
# These come from the official SDK docs / hermes prior art (1061045 = quota
# exceeded, 1061046 = qps exceeded). Any of them maps to FeishuRateLimitError
# at the call site so the tailer can adapt-backoff.
_FLOOD_CONTROL_CODES = frozenset({1061045, 1061046, 99991400, 99991401, 230020})


class FeishuRateLimitError(_FloodSignal):
    """Raised by FeishuConnector when a Feishu API responds with a flood code.

    Subclasses ``_FloodSignal`` so the tailer's adaptive-backoff branch catches
    it generically; the tailer never imports a Feishu-specific exception.
    """


# Markdown-table detection (very rough — must NOT false-positive on prose).
_MARKDOWN_TABLE_RE = re.compile(r"^\s*\|[^|\n]+\|.*\n\s*\|[-:\s|]+\|", re.MULTILINE)
_MARKDOWN_HINT_RE = re.compile(r"(?m)^\s*(#|\*|-|\d+\.)\s|`{1,3}|\*\*|__")


class FeishuConnector:
    """Connector for one Feishu account.

    Construction comes in two stages:
    - Inbound parsing only needs ``bot_open_id`` (for mention gating).
    - Outbound calls need a bound ``lark_oapi.Client`` plus ``channel_id``
      and (optional) ``reply_to_id``, set via ``bind_outbound()`` before use.
    """

    def __init__(
        self,
        *,
        bot_open_id: str | None = None,
        client: Any = None,
        channel_id: str | None = None,
        reply_to_id: str | None = None,
    ) -> None:
        self._bot_open_id = bot_open_id
        self._client = client
        self._channel_id = channel_id
        self._reply_to_id = reply_to_id

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    def parse_inbound(self, raw: dict[str, Any]) -> InboundEvent | None:
        """Normalize one Feishu im.message.receive_v1 payload into InboundEvent.

        Returns ``None`` for events we ignore: bot's own messages, non-text
        message types in v1, empty-after-strip text, group messages that do
        not mention the bot (defense-in-depth — Feishu subscription is the
        primary gate).
        """
        header = raw.get("header") or {}
        event = raw.get("event") or {}
        sender = event.get("sender") or {}
        message = event.get("message") or {}

        if header.get("event_type") != "im.message.receive_v1":
            return None
        sender_id = sender.get("sender_id") or {}
        sender_open_id = sender_id.get("open_id")
        if sender.get("sender_type") == "app":
            return None
        if self._bot_open_id is not None and sender_open_id == self._bot_open_id:
            return None
        if message.get("message_type") != "text":
            return None

        try:
            content_obj = json.loads(message.get("content", "{}"))
        except json.JSONDecodeError:
            return None
        text = _AT_TAG_RE.sub("", content_obj.get("text", "")).strip()
        if not text:
            return None

        chat_id = message.get("chat_id", "")
        message_id = message.get("message_id", "")
        chat_type = message.get("chat_type", "")

        sender_ref = sender_id.get("union_id") or sender_open_id or ""

        if chat_type == "p2p":
            scope_key = DM_SCOPE_KEY
            scope_kind = "dm"
            reply_target: str | None = None
        else:
            if not self._group_message_mentions_bot(message):
                return None
            if not sender_ref:
                return None
            scope_key = make_participant_scope(sender_ref)
            scope_kind = "participant"
            reply_target = message_id

        return InboundEvent(
            platform="feishu",
            account_external_id="",  # ingress fills this from account lookup
            platform_event_id=header.get("event_id", ""),
            channel_id=chat_id,
            scope_key=scope_key,
            scope_kind=scope_kind,
            reply_to_id=reply_target,
            inbound_message_id=message_id,
            sender_ref=sender_ref,
            sender_open_id=sender_open_id,
            text=text,
        )

    def _group_message_mentions_bot(self, message: dict[str, Any]) -> bool:
        """Defense-in-depth gate for group messages.

        If the Feishu subscription is misconfigured to ``group_msg`` instead
        of ``group_at_msg``, we still drop everything that does not @ the
        bot.

        When ``bot_open_id`` has not been hydrated (None), this method
        returns ``False`` — i.e. DROP every group message. An earlier draft
        passed through to support a PoC dev path, but in production the
        only way ``bot_open_id`` ends up None is hydration failure at
        connect time; passing through then would let the bot reply to every
        group message in the workspace (and worse, fail to recognize its
        own echoes). Long-connection startup refuses to bind such accounts,
        and the webhook ingress will simply drop group traffic until
        ``connect_feishu`` is re-run.
        """
        if self._bot_open_id is None:
            return False
        for mention in message.get("mentions") or []:
            mid = (mention.get("id") or {}).get("open_id")
            if mid and mid == self._bot_open_id:
                return True
        return False

    # ------------------------------------------------------------------
    # Outbound binding
    # ------------------------------------------------------------------

    def bind_outbound(
        self,
        *,
        client: Any,
        channel_id: str,
        reply_to_id: str | None,
    ) -> None:
        """Bind the SDK client + target channel/thread for outbound calls.

        Called by the worker's ``on_run_started`` callback before the tailer
        starts emitting ops. The same instance is reused for the run's
        lifetime.
        """
        self._client = client
        self._channel_id = channel_id
        self._reply_to_id = reply_to_id

    # ------------------------------------------------------------------
    # Outbound primitives (lark_oapi)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_payload(content: str) -> tuple[str, str]:
        """Choose msg_type + payload for outbound text.

        v1 always emits ``text`` type — most reliable across Feishu clients,
        no markdown-rendering quirks (Feishu ``post`` type does NOT render
        markdown tables, which would silently blank the message). When a
        future connector adds richer ``post`` rendering it MUST branch the
        message type BEFORE this method or detect tables inside it; the
        ``_MARKDOWN_TABLE_RE`` constant is kept for that future branch.
        """
        return "text", json.dumps({"text": content}, ensure_ascii=False)

    @staticmethod
    def _response_code(response: Any) -> int | None:
        code = getattr(response, "code", None)
        if code is None:
            return None
        try:
            return int(code)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _raise_for_flood(cls, response: Any, op: str) -> None:
        code = cls._response_code(response)
        if code in _FLOOD_CONTROL_CODES:
            raise FeishuRateLimitError(f"{op}: flood control (code={code})")

    async def post_placeholder(self, text: str) -> str | None:
        """Post the streaming reply's first message.

        - Group / threaded send → ``im.v1.message.reply`` against
          ``self._reply_to_id``.
        - DM (or unthreaded) send → ``im.v1.message.create`` with
          ``receive_id=self._channel_id``.

        Returns the new message id, or None if Feishu rejected the call
        (logged; the tailer will treat None as "no placeholder yet" and
        the next text_delta will retry as a post).
        """
        if self._client is None:
            logger.warning("[Feishu] post_placeholder called without a bound client")
            return None
        msg_type, payload = self._build_payload(text)

        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )

        if self._reply_to_id is not None:
            body = (
                ReplyMessageRequestBody.builder()
                .content(payload)
                .msg_type(msg_type)
                .reply_in_thread(False)
                .build()
            )
            req = (
                ReplyMessageRequest.builder()
                .message_id(self._reply_to_id)
                .request_body(body)
                .build()
            )
            response = await asyncio.to_thread(self._client.im.v1.message.reply, req)
        else:
            body = (
                CreateMessageRequestBody.builder()
                .receive_id(self._channel_id or "")
                .msg_type(msg_type)
                .content(payload)
                .build()
            )
            req = (
                CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
            )
            response = await asyncio.to_thread(self._client.im.v1.message.create, req)

        self._raise_for_flood(response, "post_placeholder")
        if not getattr(response, "success", lambda: False)():
            logger.warning(
                "[Feishu] post_placeholder failed: code=%s msg=%s",
                getattr(response, "code", None),
                getattr(response, "msg", None),
            )
            return None
        data = getattr(response, "data", None)
        message_id = getattr(data, "message_id", None) if data is not None else None
        return str(message_id) if message_id else None

    async def edit(self, message_id: str | None, text: str) -> None:
        """Update an already-posted message's content."""
        if self._client is None or not message_id:
            return
        from lark_oapi.api.im.v1 import UpdateMessageRequest, UpdateMessageRequestBody

        msg_type, payload = self._build_payload(text)
        body = UpdateMessageRequestBody.builder().msg_type(msg_type).content(payload).build()
        req = UpdateMessageRequest.builder().message_id(message_id).request_body(body).build()
        response = await asyncio.to_thread(self._client.im.v1.message.update, req)
        self._raise_for_flood(response, "edit")
        if not getattr(response, "success", lambda: False)():
            logger.warning(
                "[Feishu] edit failed: code=%s msg=%s",
                getattr(response, "code", None),
                getattr(response, "msg", None),
            )

    async def send_text_message(self, text: str) -> str | None:
        """Post a new (non-edit) bubble — used for share-link / artifact
        captions that should be a separate message from the streaming reply."""
        if self._client is None:
            return None
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        msg_type, payload = self._build_payload(text)
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(self._channel_id or "")
            .msg_type(msg_type)
            .content(payload)
            .build()
        )
        req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
        response = await asyncio.to_thread(self._client.im.v1.message.create, req)
        self._raise_for_flood(response, "send_text_message")
        if not getattr(response, "success", lambda: False)():
            logger.warning(
                "[Feishu] send_text_message failed: code=%s msg=%s",
                getattr(response, "code", None),
                getattr(response, "msg", None),
            )
            return None
        data = getattr(response, "data", None)
        message_id = getattr(data, "message_id", None) if data is not None else None
        return str(message_id) if message_id else None

    async def upload_image(self, local_path: str) -> str | None:
        """Upload an image to Feishu; return the resulting ``image_key``."""
        if self._client is None:
            return None
        from pathlib import Path

        from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody

        def _do_upload() -> Any:
            with Path(local_path).open("rb") as fh:
                body = CreateImageRequestBody.builder().image_type("message").image(fh).build()
                req = CreateImageRequest.builder().request_body(body).build()
                return self._client.im.v1.image.create(req)

        response = await asyncio.to_thread(_do_upload)
        if not getattr(response, "success", lambda: False)():
            logger.warning(
                "[Feishu] upload_image failed: code=%s msg=%s",
                getattr(response, "code", None),
                getattr(response, "msg", None),
            )
            return None
        data = getattr(response, "data", None)
        image_key = getattr(data, "image_key", None) if data is not None else None
        return str(image_key) if image_key else None

    async def send_image_message(self, image_key: str) -> str | None:
        """Send an image message in the bound channel."""
        if self._client is None:
            return None
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        payload = json.dumps({"image_key": image_key}, ensure_ascii=False)
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(self._channel_id or "")
            .msg_type("image")
            .content(payload)
            .build()
        )
        req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
        response = await asyncio.to_thread(self._client.im.v1.message.create, req)
        if not getattr(response, "success", lambda: False)():
            logger.warning(
                "[Feishu] send_image_message failed: code=%s msg=%s",
                getattr(response, "code", None),
                getattr(response, "msg", None),
            )
            return None
        data = getattr(response, "data", None)
        message_id = getattr(data, "message_id", None) if data is not None else None
        return str(message_id) if message_id else None

    # ------------------------------------------------------------------
    # Reactions (Task 10)
    # ------------------------------------------------------------------

    async def add_reaction(self, message_id: str, reaction_type: str) -> str | None:
        """Add a reaction; return the resulting reaction_id (or None on failure).

        Failures are logged and swallowed — a missing reaction is a UX
        regression, not a run-breaking error.
        """
        if self._client is None or not message_id:
            return None
        from lark_oapi.api.im.v1 import (
            CreateMessageReactionRequest,
            CreateMessageReactionRequestBody,
            Emoji,
        )

        body = (
            CreateMessageReactionRequestBody.builder()
            .reaction_type(Emoji.builder().emoji_type(reaction_type).build())
            .build()
        )
        req = (
            CreateMessageReactionRequest.builder().message_id(message_id).request_body(body).build()
        )
        response = await asyncio.to_thread(self._client.im.v1.message_reaction.create, req)
        if not getattr(response, "success", lambda: False)():
            logger.warning(
                "[Feishu] add_reaction failed: code={} msg={}",
                getattr(response, "code", None),
                getattr(response, "msg", None),
            )
            return None
        data = getattr(response, "data", None)
        reaction_id = getattr(data, "reaction_id", None) if data is not None else None
        return str(reaction_id) if reaction_id else None

    async def remove_reaction(self, message_id: str, reaction_id: str | None) -> None:
        """Remove a previously-added reaction.

        No-ops when ``reaction_id`` is None — covers the case where the
        ``add_reaction`` for processing-start failed and the tailer is now
        completing/failing the run; without this guard the call would raise
        with a meaningless None argument and mask the real run outcome.
        """
        if self._client is None or not message_id or not reaction_id:
            return
        from lark_oapi.api.im.v1 import DeleteMessageReactionRequest

        req = (
            DeleteMessageReactionRequest.builder()
            .message_id(message_id)
            .reaction_id(reaction_id)
            .build()
        )
        response = await asyncio.to_thread(self._client.im.v1.message_reaction.delete, req)
        if not getattr(response, "success", lambda: False)():
            logger.warning(
                "[Feishu] remove_reaction failed: code=%s msg=%s",
                getattr(response, "code", None),
                getattr(response, "msg", None),
            )

    # ------------------------------------------------------------------
    # Processing-status hooks (Task 10) — tailer-facing platform-agnostic API
    # ------------------------------------------------------------------

    async def on_processing_start(self, state: RenderState) -> None:
        """Mark the run as processing on the user's inbound message."""
        target = state.inbound_message_id
        if not target:
            return
        reaction_id = await self.add_reaction(target, _REACTION_PROCESSING)
        state.reaction_in_progress_id = reaction_id

    async def on_processing_complete(self, state: RenderState) -> None:
        """Clear the processing reaction on success."""
        target = state.inbound_message_id
        if not target:
            return
        try:
            await self.remove_reaction(target, state.reaction_in_progress_id)
        finally:
            state.reaction_in_progress_id = None

    async def on_processing_failed(self, state: RenderState) -> None:
        """Clear the processing reaction and stamp a failure marker."""
        target = state.inbound_message_id
        if not target:
            return
        try:
            try:
                await self.remove_reaction(target, state.reaction_in_progress_id)
            finally:
                state.reaction_in_progress_id = None
            try:
                await self.add_reaction(target, _REACTION_FAILURE)
            except Exception:
                logger.warning("[Feishu] add failure reaction raised", exc_info=True)
        except Exception:
            logger.warning("[Feishu] on_processing_failed hook raised", exc_info=True)


__all__ = [
    "FeishuConnector",
    "FeishuRateLimitError",
    "_FloodSignal",
]
