"""Feishu connector: inbound parse + outbound CardKit init + reactions (lark_oapi).

All synchronous lark_oapi SDK calls are wrapped in ``asyncio.to_thread`` so
they don't block the event loop the run-queue worker and other tailers
share. Hermes' prior art at ``~/hermes-agent/gateway/platforms/feishu.py``
established this discipline.

Outbound surface area:
- ``send_card_init_message(card_id)`` -> message_id (CardKit init bubble)
- ``_send_emergency_text(text)`` (private; only invoked when CardKit
  ``create_entity`` fails, so the user still gets a reply)
- ``send_to_chat(chat_id, reply_to_id, text)`` (implements the
  ``RejectionNotifier`` protocol — out-of-band rejection bubbles from the
  identity gate; deliberately bypasses the card lifecycle)
- ``upload_image(local_path)`` -> image_key
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
from cubebox.im.types import (
    DM_SCOPE_KEY,
    BindingMode,
    InboundAttachmentRef,
    InboundEvent,
    RenderState,
    make_channel_scope,
    make_participant_scope,
)

# Matches Feishu inline mention markup: <at user_id="ou_xxx">name</at>
_AT_TAG_RE = re.compile(r"<at[^>]*>.*?</at>", re.DOTALL)

# Feishu message types that carry a downloadable resource.
_FEISHU_MEDIA_TYPES = frozenset({"image", "file", "audio", "media"})


def _parse_feishu_attachments(
    message_type: str, content_obj: dict[str, Any]
) -> list[InboundAttachmentRef]:
    """Build attachment refs from a Feishu media message's parsed content.

    ``handle`` is the resource id only (``image_key`` / ``file_key``); the
    message id needed by ``message_resource.get`` is read from the queue row at
    resolve time.
    """
    if message_type == "image":
        key = content_obj.get("image_key")
        if not key:
            return []
        return [
            InboundAttachmentRef(kind="image", filename="image.png", mime=None, handle=str(key))
        ]
    key = content_obj.get("file_key")
    if not key:
        return []
    kind = {"audio": "audio", "media": "video"}.get(message_type, "file")
    name = str(content_obj.get("file_name") or message_type)
    raw_size = content_obj.get("file_size")
    size_hint: int | None = None
    if isinstance(raw_size, int):
        size_hint = raw_size
    elif isinstance(raw_size, str) and raw_size.isdigit():
        size_hint = int(raw_size)
    return [
        InboundAttachmentRef(
            kind=kind, filename=name, mime=None, handle=str(key), size_hint=size_hint
        )
    ]


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

    def parse_inbound(
        self, raw: dict[str, Any], *, binding_mode: BindingMode = "isolated"
    ) -> InboundEvent | None:
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
        message_type = message.get("message_type")
        if message_type != "text" and message_type not in _FEISHU_MEDIA_TYPES:
            return None

        try:
            content_obj = json.loads(message.get("content", "{}"))
        except json.JSONDecodeError:
            return None

        attachments: list[InboundAttachmentRef] = []
        if message_type == "text":
            raw_text = content_obj.get("text", "")
            # Feishu inbound text uses ``@_user_N`` placeholders + a separate
            # ``mentions[]`` array (NOT inline ``<at>`` tags — those are the
            # outbound shape). Drop the bot's own at-mention (so the LLM sees
            # only the message body) and substitute remaining ``@_user_N`` with
            # the mention's human-readable ``name`` (e.g. "@巩向锋") so the LLM
            # gets a name it can actually reason about instead of a placeholder.
            mentions = message.get("mentions") or []
            for mention in mentions:
                key = mention.get("key") or ""
                if not key:
                    continue
                mid = (mention.get("id") or {}).get("open_id")
                name = mention.get("name") or ""
                if mid and mid == self._bot_open_id:
                    raw_text = raw_text.replace(key, "")
                elif name:
                    raw_text = raw_text.replace(key, f"@{name}")
            text = _AT_TAG_RE.sub("", raw_text).strip()
        else:
            text = ""
            attachments = _parse_feishu_attachments(message_type, content_obj)
        if not text and not attachments:
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
            if binding_mode == "shared":
                scope_key = make_channel_scope()
                scope_kind = "channel"
            else:
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
            attachments=attachments,
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

    async def resolve_email(self, open_id: str) -> str | None:
        """Look up a Feishu user's email via ``contact/v3/users/{open_id}``.

        Requires the app to have ``contact:user.email:readonly`` granted —
        otherwise the SDK returns success but the response's ``user.email``
        field is simply absent (we coerce that to None). Bot-side
        ``Authorization`` is the tenant_access_token already on
        ``self._client``.
        """
        if self._client is None or not open_id:
            return None
        from lark_oapi.api.contact.v3 import GetUserRequest

        req = GetUserRequest.builder().user_id(open_id).user_id_type("open_id").build()
        response = await asyncio.to_thread(self._client.contact.v3.user.get, req)
        if not getattr(response, "success", lambda: False)():
            logger.warning(
                "[Feishu] resolve_email failed: code={} msg={}",
                getattr(response, "code", None),
                getattr(response, "msg", None),
            )
            return None
        data = getattr(response, "data", None)
        user_obj = getattr(data, "user", None) if data is not None else None
        if user_obj is None:
            return None
        # Prefer the corporate/SSO email; fall back to the user-set personal
        # one. ``getattr`` with default works around the SDK exposing
        # missing fields as None rather than raising.
        email = getattr(user_obj, "enterprise_email", None) or getattr(user_obj, "email", None)
        return str(email) if email else None

    async def send_to_chat(self, chat_id: str, reply_to_id: str | None, text: str) -> str | None:
        """Send a one-off plain text bubble to a chat, optionally as a thread reply.

        Implements the ``cubebox.im.identity.RejectionNotifier`` protocol —
        the identity gate uses it to deliver the "not a workspace member"
        rejection out-of-band, deliberately outside the card lifecycle.
        """
        if self._client is None or not chat_id:
            return None
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )

        payload = json.dumps({"text": text}, ensure_ascii=False)
        if reply_to_id:
            body = (
                ReplyMessageRequestBody.builder()
                .content(payload)
                .msg_type("text")
                .reply_in_thread(False)
                .build()
            )
            req = ReplyMessageRequest.builder().message_id(reply_to_id).request_body(body).build()
            response = await asyncio.to_thread(self._client.im.v1.message.reply, req)
        else:
            body = (
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(payload)
                .build()
            )
            req = (
                CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
            )
            response = await asyncio.to_thread(self._client.im.v1.message.create, req)
        if not getattr(response, "success", lambda: False)():
            logger.warning(
                "[Feishu] send_to_chat failed: code={} msg={}",
                getattr(response, "code", None),
                getattr(response, "msg", None),
            )
            return None
        data = getattr(response, "data", None)
        message_id = getattr(data, "message_id", None) if data is not None else None
        return str(message_id) if message_id else None

    async def send_card_init_message(self, card_id: str) -> str | None:
        """Send the first IM message that carries the just-created CardKit card.

        Group / threaded send → ``im.v1.message.reply`` against
        ``self._reply_to_id``. DM → ``im.v1.message.create``.

        Returns the new bot message_id or None on failure.

        Built-in retry: code 230099 / 11310 "cardid is invalid" is
        intermittent — observed in real-tenant runs even when the same
        card_id is reachable via a direct SDK / curl call moments later.
        We don't yet know if it's CardKit-to-IM eventual consistency or
        Feishu-side caching. The retry pattern (sleep 200ms → 500ms → 1s)
        cushions that latency without blocking the run beyond ~2s.
        """
        if self._client is None:
            return None
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )

        payload = json.dumps(
            {"type": "card", "data": {"card_id": card_id}},
            ensure_ascii=False,
        )

        _path = "reply" if self._reply_to_id is not None else "create"

        def _do_call() -> Any:
            if self._reply_to_id is not None:
                reply_body = (
                    ReplyMessageRequestBody.builder()
                    .content(payload)
                    .msg_type("interactive")
                    .reply_in_thread(False)
                    .build()
                )
                reply_req = (
                    ReplyMessageRequest.builder()
                    .message_id(self._reply_to_id)
                    .request_body(reply_body)
                    .build()
                )
                return self._client.im.v1.message.reply(reply_req)
            create_body = (
                CreateMessageRequestBody.builder()
                .receive_id(self._channel_id or "")
                .msg_type("interactive")
                .content(payload)
                .build()
            )
            create_req = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(create_body)
                .build()
            )
            return self._client.im.v1.message.create(create_req)

        # Retry envelope:
        # - 230099 / sub-code 11310 = "cardid is invalid" — IM races ahead of
        #   CardKit propagation, recovers in ~300ms-1s.
        # - 230020 = "rate limited" / flood control — bursty incoming traffic
        #   tripped a per-bot quota; backing off gives the quota window time
        #   to roll. Without this retry, a single quota-shaped strike falls
        #   straight to _send_emergency_text (same IM-message quota path), so
        #   the user loses the rich card AND we put more load on the same
        #   throttled endpoint.
        _RETRY_DELAYS = (0.2, 0.5, 1.0)
        _RETRYABLE_CODES = {230020, 230099}
        last_code: Any = None
        last_msg: Any = None
        last_logid: Any = None
        for attempt in range(len(_RETRY_DELAYS) + 1):
            response = await asyncio.to_thread(_do_call)
            if getattr(response, "success", lambda: False)():
                data = getattr(response, "data", None)
                message_id = getattr(data, "message_id", None) if data is not None else None
                if attempt > 0:
                    logger.info(
                        "[Feishu] send_card_init_message succeeded on attempt {} after"
                        " transient cardid-invalid; card_id={!r} path={}",
                        attempt + 1,
                        card_id,
                        _path,
                    )
                return str(message_id) if message_id else None
            last_code = getattr(response, "code", None)
            last_msg = getattr(response, "msg", None)
            raw = getattr(response, "raw", None)
            last_logid = (
                (raw.headers.get("X-Tt-Logid") or raw.headers.get("x-tt-logid"))
                if raw is not None and hasattr(raw, "headers")
                else None
            )
            is_cardid_invalid = (
                last_code == 230099 and isinstance(last_msg, str) and "11310" in last_msg
            )
            is_retryable = last_code in _RETRYABLE_CODES or is_cardid_invalid
            logger.warning(
                "[Feishu] send_card_init_message attempt {} failed: code={} msg={} logid={}"
                " card_id={!r} path={} reply_to={!r} channel={!r} retryable={}",
                attempt + 1,
                last_code,
                last_msg,
                last_logid,
                card_id,
                _path,
                self._reply_to_id,
                self._channel_id,
                is_retryable,
            )
            if not is_retryable or attempt >= len(_RETRY_DELAYS):
                break
            await asyncio.sleep(_RETRY_DELAYS[attempt])
        return None

    async def _send_emergency_text(self, text: str) -> str | None:
        """Send a plain text bubble — used ONLY when CardKit create_entity fails.

        Threads when ``self._reply_to_id`` is bound (group runs triggered by
        an @mention); otherwise creates a top-level message.
        """
        if self._client is None or not self._channel_id:
            return None
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )

        payload = json.dumps({"text": text}, ensure_ascii=False)
        if self._reply_to_id:
            body = (
                ReplyMessageRequestBody.builder()
                .content(payload)
                .msg_type("text")
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
                .receive_id(self._channel_id)
                .msg_type("text")
                .content(payload)
                .build()
            )
            req = (
                CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
            )
            response = await asyncio.to_thread(self._client.im.v1.message.create, req)
        if not getattr(response, "success", lambda: False)():
            logger.warning(
                "[Feishu] _send_emergency_text failed: code={} msg={}",
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
                "[Feishu] upload_image failed: code={} msg={}",
                getattr(response, "code", None),
                getattr(response, "msg", None),
            )
            return None
        data = getattr(response, "data", None)
        image_key = getattr(data, "image_key", None) if data is not None else None
        return str(image_key) if image_key else None

    async def send_file(self, *, local_path: str, filename: str, mime: str | None) -> bool:
        """Upload a local file and send it as a native ``file`` message.

        Two-step: ``im.v1.file.create`` (file_type=stream) → ``file_key``, then a
        ``msg_type="file"`` message to the bound chat (reply when bound).
        Returns False on any failure so the caller falls back to a share-link.
        """
        del mime  # Feishu derives the type from the bytes; msg_type is "file".
        if self._client is None or not self._channel_id:
            return False
        from pathlib import Path

        from lark_oapi.api.im.v1 import (
            CreateFileRequest,
            CreateFileRequestBody,
            CreateMessageRequest,
            CreateMessageRequestBody,
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )

        def _do_upload() -> Any:
            with Path(local_path).open("rb") as fh:
                body = (
                    CreateFileRequestBody.builder()
                    .file_type("stream")
                    .file_name(filename)
                    .file(fh)
                    .build()
                )
                req = CreateFileRequest.builder().request_body(body).build()
                return self._client.im.v1.file.create(req)

        up = await asyncio.to_thread(_do_upload)
        if not getattr(up, "success", lambda: False)():
            logger.warning(
                "[Feishu] send_file upload failed: code={} msg={}",
                getattr(up, "code", None),
                getattr(up, "msg", None),
            )
            return False
        up_data = getattr(up, "data", None)
        file_key = getattr(up_data, "file_key", None) if up_data is not None else None
        if not file_key:
            return False

        payload = json.dumps({"file_key": file_key}, ensure_ascii=False)
        if self._reply_to_id:
            rbody = (
                ReplyMessageRequestBody.builder()
                .content(payload)
                .msg_type("file")
                .reply_in_thread(False)
                .build()
            )
            rreq = (
                ReplyMessageRequest.builder()
                .message_id(self._reply_to_id)
                .request_body(rbody)
                .build()
            )
            resp = await asyncio.to_thread(self._client.im.v1.message.reply, rreq)
        else:
            cbody = (
                CreateMessageRequestBody.builder()
                .receive_id(self._channel_id)
                .msg_type("file")
                .content(payload)
                .build()
            )
            creq = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(cbody)
                .build()
            )
            resp = await asyncio.to_thread(self._client.im.v1.message.create, creq)
        ok = bool(getattr(resp, "success", lambda: False)())
        if not ok:
            logger.warning(
                "[Feishu] send_file message failed: code={} msg={}",
                getattr(resp, "code", None),
                getattr(resp, "msg", None),
            )
        return ok

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
                "[Feishu] remove_reaction failed: code={} msg={}",
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
                logger.opt(exception=True).warning("[Feishu] add failure reaction raised")
        except Exception:
            logger.opt(exception=True).warning("[Feishu] on_processing_failed hook raised")


__all__ = [
    "FeishuConnector",
    "FeishuRateLimitError",
    "_FloodSignal",
]
