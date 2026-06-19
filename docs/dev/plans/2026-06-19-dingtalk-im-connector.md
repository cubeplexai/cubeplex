# DingTalk IM Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add DingTalk (钉钉) as the fourth IM platform, reusing the existing connector-neutral pipeline.

**Architecture:** New `im/dingtalk/` module implementing `PlatformConnector` protocol. Stream mode gateway (WebSocket long-connection via `dingtalk-stream` SDK). Interactive card rendering for outbound. Email auto-match + manual link for identity resolution.

**Tech Stack:** `dingtalk-stream` (gateway), DingTalk OpenAPI v2 (cards, user info), httpx (REST calls), existing `im/` pipeline.

**Spec:** `docs/dev/specs/2026-06-19-dingtalk-im-connector-design.md`

**Worktree:** `/home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk`
**Ports:** 8059 (backend), 3059 (frontend)

---

## Task 1: Add `dingtalk-stream` dependency

**Files:**
- Modify: `backend/pyproject.toml` (via `uv add`)

- [ ] **Step 1: Install the SDK**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
uv add dingtalk-stream
```

- [ ] **Step 2: Verify import works**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
uv run python -c "import dingtalk_stream; print(dingtalk_stream.__version__)"
```

Expected: version string, no ImportError.

- [ ] **Step 3: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
git add pyproject.toml uv.lock
git commit -m "chore: add dingtalk-stream dependency"
```

---

## Task 2: DingTalk connector — inbound parsing + outbound API calls

The core connector class: parses raw Stream events into `InboundEvent`, provides outbound API helpers (send card, update card, send message), and implements `IdentityResolver` + `RejectionNotifier` protocols.

**Files:**
- Create: `backend/cubebox/im/dingtalk/__init__.py`
- Create: `backend/cubebox/im/dingtalk/connector.py`
- Test: `backend/tests/unit/im/test_dingtalk_connector.py`

- [ ] **Step 1: Create the package init (empty for now)**

```python
# backend/cubebox/im/dingtalk/__init__.py
```

Just create the empty `__init__.py`. Platform registration happens in Task 7.

- [ ] **Step 2: Write unit tests for parse_inbound**

```python
# backend/tests/unit/im/test_dingtalk_connector.py
"""Unit tests for DingtalkConnector.parse_inbound."""

from __future__ import annotations

from cubebox.im.dingtalk.connector import DingtalkConnector
from cubebox.im.types import DM_SCOPE_KEY


class TestParseInbound:
    def test_dm_message(self) -> None:
        raw = {
            "msgtype": "text",
            "text": {"content": "hello"},
            "msgId": "msg_001",
            "conversationId": "cid_dm_123",
            "conversationType": "1",
            "senderId": "staff_abc",
            "senderStaffId": "staff_abc",
            "chatbotUserId": "bot_999",
        }
        connector = DingtalkConnector(bot_user_id="bot_999")
        event = connector.parse_inbound(raw)
        assert event is not None
        assert event.platform == "dingtalk"
        assert event.text == "hello"
        assert event.channel_id == "cid_dm_123"
        assert event.scope_key == DM_SCOPE_KEY
        assert event.scope_kind == "dm"
        assert event.sender_ref == "staff_abc"
        assert event.platform_event_id == "msg_001"
        assert event.reply_to_id == "msg_001"

    def test_group_at_mention(self) -> None:
        # DingTalk strips the @BotName tag and delivers a leading space
        raw = {
            "msgtype": "text",
            "text": {"content": " what time is it"},
            "msgId": "msg_002",
            "conversationId": "cid_group_456",
            "conversationType": "2",
            "senderId": "staff_def",
            "senderStaffId": "staff_def",
            "chatbotUserId": "bot_999",
            "atUsers": [
                {"dingtalkId": "bot_999"},
            ],
        }
        connector = DingtalkConnector(bot_user_id="bot_999")
        event = connector.parse_inbound(raw)
        assert event is not None
        assert event.scope_key == "u:staff_def"
        assert event.scope_kind == "group"
        assert event.text == "what time is it"

    def test_non_text_ignored(self) -> None:
        raw = {
            "msgtype": "image",
            "msgId": "msg_003",
            "conversationId": "cid_dm_123",
            "conversationType": "1",
            "senderId": "staff_abc",
            "senderStaffId": "staff_abc",
            "chatbotUserId": "bot_999",
        }
        connector = DingtalkConnector(bot_user_id="bot_999")
        assert connector.parse_inbound(raw) is None

    def test_strips_at_mention_prefix(self) -> None:
        raw = {
            "msgtype": "text",
            "text": {"content": " hello there"},
            "msgId": "msg_004",
            "conversationId": "cid_group_789",
            "conversationType": "2",
            "senderId": "staff_ghi",
            "senderStaffId": "staff_ghi",
            "chatbotUserId": "bot_999",
        }
        connector = DingtalkConnector(bot_user_id="bot_999")
        event = connector.parse_inbound(raw)
        assert event is not None
        assert event.text == "hello there"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
uv run pytest tests/unit/im/test_dingtalk_connector.py -v --no-cov 2>&1 | tail -10
```

Expected: FAIL — `DingtalkConnector` not found.

- [ ] **Step 4: Implement DingtalkConnector**

```python
# backend/cubebox/im/dingtalk/connector.py
"""DingTalk connector: inbound parse + outbound card/message API + identity."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from loguru import logger

from cubebox.im.outbound import _FloodSignal
from cubebox.im.types import (
    DM_SCOPE_KEY,
    InboundEvent,
    make_participant_scope,
)


class DingtalkRateLimitError(_FloodSignal):
    """Raised when DingTalk API returns a rate-limit error."""


class DingtalkConnector:
    """Connector for one DingTalk enterprise bot account.

    Construction:
    - Inbound parsing only needs ``bot_user_id``.
    - Outbound calls need ``access_token`` + ``conversation_id``.
    """

    def __init__(
        self,
        *,
        bot_user_id: str = "",
        access_token: str = "",
        conversation_id: str = "",
        sender_staff_id: str = "",
        is_dm: bool = False,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._bot_user_id = bot_user_id
        self._access_token = access_token
        self._conversation_id = conversation_id
        self._sender_staff_id = sender_staff_id
        self._is_dm = is_dm
        self._http_ext = http_client
        self._http_own: httpx.AsyncClient | None = None

    @property
    def _http(self) -> httpx.AsyncClient:
        """Lazy-init httpx client; avoids allocation for parse-only connectors."""
        if self._http_ext is not None:
            return self._http_ext
        if self._http_own is None:
            self._http_own = httpx.AsyncClient(timeout=10)
        return self._http_own

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    def parse_inbound(self, raw: dict[str, Any]) -> InboundEvent | None:
        """Normalize a DingTalk Stream callback dict into an InboundEvent.

        Returns None for non-text messages.
        """
        msgtype = raw.get("msgtype", "")
        if msgtype != "text":
            return None

        text_obj = raw.get("text") or {}
        text: str = text_obj.get("content", "").strip()
        msg_id: str = raw.get("msgId", "")
        conversation_id: str = raw.get("conversationId", "")
        conversation_type: str = raw.get("conversationType", "")
        sender_staff_id: str = raw.get("senderStaffId", "")

        if not msg_id or not conversation_id or not sender_staff_id:
            return None

        # DM (1:1)
        if conversation_type == "1":
            scope_key = DM_SCOPE_KEY
            scope_kind = "dm"
        else:
            # Group @mention — per-user scope isolation
            scope_key = make_participant_scope(sender_staff_id)
            scope_kind = "group"
            # Strip @mention text (DingTalk prepends the bot name or leaves
            # a leading space after the invisible @-tag)
            text = re.sub(r"^\s+", "", text)

        return InboundEvent(
            platform="dingtalk",
            account_external_id="",
            platform_event_id=msg_id,
            channel_id=conversation_id,
            scope_key=scope_key,
            scope_kind=scope_kind,
            reply_to_id=msg_id,
            inbound_message_id=msg_id,
            sender_ref=sender_staff_id,
            sender_open_id=sender_staff_id,
            text=text,
        )

    # ------------------------------------------------------------------
    # Outbound — card + message API calls
    # ------------------------------------------------------------------

    async def send_markdown(
        self, title: str, text: str, *, user_ids: list[str] | None = None
    ) -> str | None:
        """Send a proactive markdown message to specific users. Returns processQueryKey or None."""
        if not user_ids:
            logger.warning("[DingTalk] send_markdown called with no user_ids")
            return None

        url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
        headers = {
            "x-acs-dingtalk-access-token": self._access_token,
            "Content-Type": "application/json",
        }
        payload = {
            "msgParam": json.dumps({"title": title, "text": text}),
            "msgKey": "sampleMarkdown",
            "robotCode": self._bot_user_id,
            "userIds": user_ids,
        }
        try:
            resp = await self._http.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                return resp.json().get("processQueryKey")
            logger.warning("[DingTalk] send_markdown failed: {}", resp.text)
            return None
        except Exception:
            logger.warning("[DingTalk] send_markdown error", exc_info=True)
            return None

    async def reply_markdown(
        self,
        title: str,
        text: str,
        *,
        open_conversation_id: str = "",
        user_id: str = "",
    ) -> str | None:
        """Reply to a conversation with markdown.

        For group conversations, uses groupMessages/send with openConversationId.
        For DM conversations, uses oToMessages/batchSend with userIds (since
        DingTalk's group endpoint rejects single-chat conversationIds).
        Pass ``user_id`` (staffId) for DM replies.
        """
        cid = open_conversation_id or self._conversation_id
        headers = {
            "x-acs-dingtalk-access-token": self._access_token,
            "Content-Type": "application/json",
        }
        msg_param = json.dumps({"title": title, "text": text})
        # Default to stored DM user for reply routing
        effective_user_id = user_id or (self._sender_staff_id if self._is_dm else "")

        if effective_user_id:
            # DM: single-chat reply via proactive message endpoint
            url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
            payload = {
                "msgParam": msg_param,
                "msgKey": "sampleMarkdown",
                "robotCode": self._bot_user_id,
                "userIds": [effective_user_id],
            }
        else:
            # Group: reply via group messages endpoint
            url = "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
            payload = {
                "msgParam": msg_param,
                "msgKey": "sampleMarkdown",
                "robotCode": self._bot_user_id,
                "openConversationId": cid,
            }
        try:
            resp = await self._http.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                return resp.json().get("processQueryKey")
            logger.warning("[DingTalk] reply_markdown failed: {}", resp.text)
            return None
        except Exception:
            logger.warning("[DingTalk] reply_markdown error", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Interactive card operations
    # ------------------------------------------------------------------

    async def create_and_deliver_card(
        self,
        *,
        card_template_id: str,
        open_conversation_id: str,
        card_data: dict[str, Any],
        out_track_id: str,
    ) -> bool:
        """Create + deliver an interactive card instance."""
        url = "https://api.dingtalk.com/v1.0/card/instances/createAndDeliver"
        headers = {
            "x-acs-dingtalk-access-token": self._access_token,
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "cardTemplateId": card_template_id,
            "outTrackId": out_track_id,
            "openConversationId": open_conversation_id,
            "cardData": {"cardParamMap": card_data},
        }
        try:
            resp = await self._http.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                return True
            logger.warning(
                "[DingTalk] create_and_deliver_card failed: {}", resp.text
            )
            return False
        except Exception:
            logger.warning("[DingTalk] create_and_deliver_card error", exc_info=True)
            return False

    async def streaming_update_card(
        self,
        *,
        out_track_id: str,
        guid: str,
        key: str,
        content: str,
        is_final: bool = False,
        is_error: bool = False,
    ) -> bool:
        """Stream-update a card variable. DingTalk requires PUT, not POST."""
        url = "https://api.dingtalk.com/v1.0/card/streaming"
        headers = {
            "x-acs-dingtalk-access-token": self._access_token,
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "outTrackId": out_track_id,
            "guid": guid,
            "key": key,
            "content": content,
            "isFull": True,
            "isFinalize": is_final,
            "isError": is_error,
        }
        try:
            resp = await self._http.put(url, headers=headers, json=payload)
            if resp.status_code == 200:
                return True
            if resp.status_code == 429:
                raise DingtalkRateLimitError("rate limited")
            logger.warning("[DingTalk] streaming_update failed: {}", resp.text)
            return False
        except DingtalkRateLimitError:
            raise
        except Exception:
            logger.warning("[DingTalk] streaming_update error", exc_info=True)
            return False

    async def update_card_actions(
        self,
        *,
        out_track_id: str,
        card_data: dict[str, Any],
    ) -> bool:
        """Update card data (buttons, status) via PUT."""
        url = "https://api.dingtalk.com/v1.0/card/instances"
        headers = {
            "x-acs-dingtalk-access-token": self._access_token,
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "outTrackId": out_track_id,
            "cardData": {"cardParamMap": card_data},
        }
        try:
            resp = await self._http.put(url, headers=headers, json=payload)
            if resp.status_code == 200:
                return True
            logger.warning("[DingTalk] update_card_actions failed: {}", resp.text)
            return False
        except Exception:
            logger.warning("[DingTalk] update_card_actions error", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Identity resolution (IdentityResolver protocol)
    # ------------------------------------------------------------------

    async def resolve_email(self, open_id: str) -> str | None:
        """Look up a DingTalk user's email by staffId."""
        url = "https://oapi.dingtalk.com/topapi/v2/user/get"
        params = {"access_token": self._access_token}
        payload = {"userid": open_id}
        try:
            resp = await self._http.post(url, params=params, json=payload)
            data = resp.json()
            if data.get("errcode") != 0:
                logger.warning("[DingTalk] user/get failed: {}", data)
                return None
            result = data.get("result", {})
            email = result.get("email") or result.get("org_email") or ""
            return email.strip().lower() if email else None
        except Exception:
            logger.warning("[DingTalk] resolve_email error", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Rejection notifier (RejectionNotifier protocol)
    # ------------------------------------------------------------------

    async def send_to_chat(
        self, chat_id: str, reply_to_id: str | None, text: str
    ) -> str | None:
        """Send a rejection notice to the conversation."""
        return await self.reply_markdown(
            title="Notice",
            text=text,
            open_conversation_id=chat_id,
            user_id=self._sender_staff_id if self._is_dm else "",
        )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
uv run pytest tests/unit/im/test_dingtalk_connector.py -v --no-cov 2>&1 | tail -10
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
git add cubebox/im/dingtalk/__init__.py cubebox/im/dingtalk/connector.py tests/unit/im/test_dingtalk_connector.py
git commit -m "feat(im): add DingtalkConnector — inbound parse + outbound API + identity"
```

---

## Task 3: DingTalk OpDispatcher — interactive card rendering

Implements the `OpDispatcher` protocol: creates interactive cards, streams markdown content, patches buttons for AskUser/SandboxConfirm, and finalizes with artifacts.

**Files:**
- Create: `backend/cubebox/im/dingtalk/renderer.py`
- Test: `backend/tests/unit/im/test_dingtalk_renderer.py`

- [ ] **Step 1: Write unit tests for DingtalkOpDispatcher**

```python
# backend/tests/unit/im/test_dingtalk_renderer.py
"""Unit tests for DingtalkOpDispatcher."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from cubebox.im.dingtalk.renderer import DingtalkOpDispatcher
from cubebox.im.types import RenderState


@pytest.fixture()
def state() -> RenderState:
    s = RenderState(bot_name="testbot", run_id="run_001", stream_interval=1.0)
    s.reply_to_id = "msg_inbound"
    s.inbound_message_id = "msg_inbound"
    return s


@pytest.fixture()
def connector() -> AsyncMock:
    mock = AsyncMock()
    mock.create_and_deliver_card = AsyncMock(return_value=True)
    mock.streaming_update_card = AsyncMock(return_value=True)
    mock.update_card_actions = AsyncMock(return_value=True)
    mock.reply_markdown = AsyncMock(return_value="msg_reply")
    return mock


class TestDispatchCreate:
    @pytest.mark.anyio()
    async def test_creates_card(
        self, state: RenderState, connector: AsyncMock
    ) -> None:
        d = DingtalkOpDispatcher(
            connector=connector,
            state=state,
            card_template_id="tpl_001",
            open_conversation_id="cid_123",
        )
        state.card_state.streaming_content = "Hello world"
        ok = await d.dispatch_create(state)
        assert ok is True
        connector.create_and_deliver_card.assert_called_once()
        assert state.card_id is not None

    @pytest.mark.anyio()
    async def test_fallback_on_card_failure(
        self, state: RenderState, connector: AsyncMock
    ) -> None:
        connector.create_and_deliver_card = AsyncMock(return_value=False)
        d = DingtalkOpDispatcher(
            connector=connector,
            state=state,
            card_template_id="tpl_001",
            open_conversation_id="cid_123",
        )
        state.card_state.streaming_content = "Hello"
        ok = await d.dispatch_create(state)
        assert ok is True
        assert state.card_unavailable is True


class TestDispatchStream:
    @pytest.mark.anyio()
    async def test_streams_content(
        self, state: RenderState, connector: AsyncMock
    ) -> None:
        d = DingtalkOpDispatcher(
            connector=connector,
            state=state,
            card_template_id="tpl_001",
            open_conversation_id="cid_123",
        )
        state.card_id = "track_001"
        state.card_state.streaming_content = "Hello streaming"
        ok = await d.dispatch_stream(state, "Hello streaming")
        assert ok is True
        connector.streaming_update_card.assert_called_once()


class TestDispatchFinalize:
    @pytest.mark.anyio()
    async def test_finalizes_card(
        self, state: RenderState, connector: AsyncMock
    ) -> None:
        d = DingtalkOpDispatcher(
            connector=connector,
            state=state,
            card_template_id="tpl_001",
            open_conversation_id="cid_123",
        )
        state.card_id = "track_001"
        state.card_state.streaming_content = "Done"
        ok = await d.dispatch_finalize(state)
        assert ok is True
        # Should call streaming_update with is_final=True
        connector.streaming_update_card.assert_called()
        call_kwargs = connector.streaming_update_card.call_args.kwargs
        assert call_kwargs["is_final"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
uv run pytest tests/unit/im/test_dingtalk_renderer.py -v --no-cov 2>&1 | tail -10
```

Expected: FAIL — `DingtalkOpDispatcher` not found.

- [ ] **Step 3: Implement DingtalkOpDispatcher**

```python
# backend/cubebox/im/dingtalk/renderer.py
"""DingTalk outbound renderer — Interactive Card with streaming updates."""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger

from cubebox.im.outbound import note_edit_success, note_flood_strike
from cubebox.im.dingtalk.connector import DingtalkRateLimitError
from cubebox.im.types import RenderState


class DingtalkOpDispatcher:
    """Dispatches outbound ops to DingTalk via interactive cards."""

    def __init__(
        self,
        *,
        connector: Any,
        state: RenderState,
        card_template_id: str,
        open_conversation_id: str,
    ) -> None:
        self._connector = connector
        self._state = state
        self._card_template_id = card_template_id
        self._open_conversation_id = open_conversation_id
        self._pending_input_sent_id: str | None = None
        self._stream_seq: int = 0

    async def dispatch_create(self, state: Any) -> bool:
        s = self._state
        text = s.card_state.streaming_content or "..."
        out_track_id = f"cubebox-{s.run_id}-{uuid.uuid4().hex[:8]}"

        ok = await self._connector.create_and_deliver_card(
            card_template_id=self._card_template_id,
            open_conversation_id=self._open_conversation_id,
            card_data={"content": text, "status": "thinking"},
            out_track_id=out_track_id,
        )
        if ok:
            s.card_id = out_track_id
            return True
        s.card_unavailable = True
        await self._connector.reply_markdown(
            title="cubebox",
            text=text,
            open_conversation_id=self._open_conversation_id,
        )
        return True

    async def dispatch_stream(self, state: Any, text: str) -> bool:
        s = self._state
        if s.card_unavailable:
            return True
        if s.card_id is None:
            return await self.dispatch_create(state)
        full_content = s.card_state.streaming_content
        self._stream_seq += 1
        guid = f"{s.card_id}-{self._stream_seq}"
        try:
            ok = await self._connector.streaming_update_card(
                out_track_id=s.card_id,
                guid=guid,
                key="content",
                content=full_content,
            )
        except DingtalkRateLimitError:
            note_flood_strike(s)
            return False
        if ok:
            note_edit_success(s)
        return bool(ok)

    async def dispatch_patch(self, state: Any) -> bool:
        s = self._state
        pending = s.card_state.pending_input
        pending_id = f"{pending.kind}:{pending.run_id}" if pending else None

        if (
            pending is not None
            and pending.resolved_choice is None
            and pending.choices
            and pending_id != self._pending_input_sent_id
        ):
            if s.card_unavailable or s.card_id is None:
                # Card path is down — surface HITL via plain text fallback
                await self._emergency_pending_input(pending)
            else:
                await self._send_pending_input_buttons(pending)
            self._pending_input_sent_id = pending_id

        if pending is not None and pending.resolved_choice is not None:
            # HITL resolved — next card starts fresh, reset stream state
            s.card_id = None
            s.card_unavailable = False
            self._stream_seq = 0
        return True

    async def _emergency_pending_input(self, pending: Any) -> None:
        """Fallback: send HITL question as plain markdown when card is unavailable."""
        text = pending.question or "Please continue in the cubebox web UI."
        if pending.choices:
            labels = ", ".join(label for label, _, _ in pending.choices)
            text = f"{text}\n\nOptions: {labels}\n\n_(Please answer in the web UI.)_"
        await self.emergency_text(text)

    async def _send_pending_input_buttons(self, pending: Any) -> None:
        """Update the card with action buttons for AskUser/SandboxConfirm."""
        s = self._state
        if s.card_id is None or s.card_unavailable:
            return

        qid = pending.question_id or ""
        akey = pending.answer_key or ""
        short_qid = qid[:8]

        buttons: list[dict[str, Any]] = []
        for label, value, btn_type in pending.choices:
            action_id = (
                f"im:{pending.kind}:{pending.run_id}:{short_qid}:{akey}:{value}"
            )
            buttons.append({
                "label": label[:20],
                "actionId": action_id,
                "type": btn_type,
            })

        card_data: dict[str, Any] = {
            "question": pending.question or "Please choose:",
            "buttons": buttons,
        }
        await self._connector.update_card_actions(
            out_track_id=s.card_id,
            card_data=card_data,
        )

    async def dispatch_finalize(self, state: Any) -> bool:
        s = self._state
        full_content = s.card_state.streaming_content or ""

        if s.card_state.error:
            error_suffix = f"\n\n⚠️ {s.card_state.error}"
            full_content = (full_content + error_suffix) if full_content else error_suffix

        artifacts = s.card_state.artifacts
        if artifacts:
            links = "\n".join(
                f"📎 [{a.name}]({a.share_url})" for a in artifacts if a.share_url
            )
            if links:
                full_content = f"{full_content}\n\n{links}" if full_content else links

        if s.card_id and not s.card_unavailable:
            status = "error" if s.card_state.error else "done"
            self._stream_seq += 1
            await self._connector.streaming_update_card(
                out_track_id=s.card_id,
                guid=f"{s.card_id}-{self._stream_seq}",
                key="content",
                content=full_content,
                is_final=True,
                is_error=bool(s.card_state.error),
            )
            await self._connector.update_card_actions(
                out_track_id=s.card_id,
                card_data={"status": status},
            )
        elif full_content:
            await self._connector.reply_markdown(
                title="cubebox",
                text=full_content[:4000],
                open_conversation_id=self._open_conversation_id,
            )
        return True

    async def emergency_text(self, text: str) -> None:
        try:
            await self._connector.reply_markdown(
                title="cubebox",
                text=text[:4000],
                open_conversation_id=self._open_conversation_id,
            )
        except Exception:
            logger.warning("[DingTalk] emergency text send failed", exc_info=True)

    async def aclose(self) -> None:
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
uv run pytest tests/unit/im/test_dingtalk_renderer.py -v --no-cov 2>&1 | tail -10
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
git add cubebox/im/dingtalk/renderer.py tests/unit/im/test_dingtalk_renderer.py
git commit -m "feat(im): add DingtalkOpDispatcher — interactive card rendering"
```

---

## Task 4: DingTalk card action handler

Routes interactive card button clicks to `resume_paused_run`, following the same action ID format as Slack.

**Files:**
- Create: `backend/cubebox/im/dingtalk/interactions.py`
- Test: `backend/tests/unit/im/test_dingtalk_interactions.py`

- [ ] **Step 1: Write unit test**

```python
# backend/tests/unit/im/test_dingtalk_interactions.py
"""Unit tests for DingTalk card action routing."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from cubebox.im.dingtalk.interactions import handle_card_action


class TestHandleCardAction:
    @pytest.mark.anyio()
    async def test_routes_to_resume(self) -> None:
        callback = {
            "outTrackId": "cubebox-run_001-abc123",
            "content": '{"cardActionId":"im:ask_user:run_001:q1234567:answer:yes"}',
        }
        with (
            patch(
                "cubebox.im.dingtalk.interactions.resolve_full_question_id",
                new_callable=AsyncMock,
                return_value="q123456789abcdef",
            ),
            patch(
                "cubebox.im.dingtalk.interactions.resume_paused_run",
                new_callable=AsyncMock,
            ) as mock_resume,
        ):
            await handle_card_action(callback=callback, run_manager=AsyncMock())
            mock_resume.assert_called_once_with(
                run_id="run_001",
                input_kind="ask_user",
                choice="yes",
                operator_open_id="",
                question_id="q123456789abcdef",
                answer_key="answer",
                run_manager=mock_resume.call_args.kwargs["run_manager"],
            )

    @pytest.mark.anyio()
    async def test_ignores_non_im_actions(self) -> None:
        callback = {
            "outTrackId": "cubebox-run_001-abc123",
            "content": '{"cardActionId":"other:action"}',
        }
        with patch(
            "cubebox.im.dingtalk.interactions.resume_paused_run",
            new_callable=AsyncMock,
        ) as mock_resume:
            await handle_card_action(callback=callback, run_manager=AsyncMock())
            mock_resume.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
uv run pytest tests/unit/im/test_dingtalk_interactions.py -v --no-cov 2>&1 | tail -10
```

- [ ] **Step 3: Implement handle_card_action**

```python
# backend/cubebox/im/dingtalk/interactions.py
"""Handle DingTalk interactive card action callbacks (button clicks)."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from cubebox.im.resume import resolve_full_question_id, resume_paused_run


async def handle_card_action(
    *,
    callback: dict[str, Any],
    run_manager: Any,
) -> None:
    """Route a DingTalk card button click to the resume path.

    Button action_id format: ``im:{kind}:{run_id}:{short_qid}:{akey}:{value}``
    """
    content_raw = callback.get("content", "{}")
    try:
        content = json.loads(content_raw) if isinstance(content_raw, str) else content_raw
    except (json.JSONDecodeError, TypeError):
        return

    action_id: str = content.get("cardActionId", "")
    if not action_id.startswith("im:"):
        return

    parts = action_id.split(":", 5)
    if len(parts) < 6:
        return

    _, kind, run_id, short_qid, answer_key, value = parts

    question_id = await resolve_full_question_id(run_id, short_qid)

    try:
        await resume_paused_run(
            run_id=run_id,
            input_kind=kind,
            choice=value,
            operator_open_id="",
            question_id=question_id,
            answer_key=answer_key,
            run_manager=run_manager,
        )
    except Exception:
        logger.warning("[DingTalk] card action handler failed", exc_info=True)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
uv run pytest tests/unit/im/test_dingtalk_interactions.py -v --no-cov 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
git add cubebox/im/dingtalk/interactions.py tests/unit/im/test_dingtalk_interactions.py
git commit -m "feat(im): add DingTalk card action handler"
```

---

## Task 5: DingTalk gateway — Stream mode lifecycle

Manages a `dingtalk-stream` client per account. Registers chat message + card action callback handlers.

**Files:**
- Create: `backend/cubebox/im/dingtalk/gateway.py`

- [ ] **Step 1: Implement DingtalkGateway**

```python
# backend/cubebox/im/dingtalk/gateway.py
"""DingTalk Stream gateway — one long-connection per IM account."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import dingtalk_stream
from loguru import logger

from cubebox.im.dingtalk.connector import DingtalkConnector


class DingtalkGateway:
    """Manages one DingTalk Stream client per IM account."""

    def __init__(
        self,
        *,
        account: Any,
        app_key: str,
        app_secret: str,
        ingest: Any,
        session_maker: Any,
        run_manager: Any,
        redis_key_prefix: str,
    ) -> None:
        self._account = account
        self._app_key = app_key
        self._app_secret = app_secret
        self._ingest = ingest
        self._session_maker = session_maker
        self._run_manager = run_manager
        self._redis_key_prefix = redis_key_prefix
        self._client: Any = None
        self._task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._access_token: str = ""
        self.card_template_id: str = ""
        self._shared_http = httpx.AsyncClient(timeout=10)

    async def start(self) -> None:
        # Register the interactive card template (idempotent — DingTalk
        # deduplicates by template name). Store the ID for build_tailer.
        tpl_id = await self._register_card_template()
        if tpl_id:
            self.card_template_id = tpl_id

        credential = dingtalk_stream.Credential(self._app_key, self._app_secret)
        client = dingtalk_stream.DingTalkStreamClient(credential=credential)
        self._client = client

        account = self._account
        session_maker = self._session_maker
        ingest = self._ingest
        run_manager = self._run_manager
        redis_key_prefix = self._redis_key_prefix

        async def on_message(raw: dict[str, Any]) -> None:
            await self._handle_inbound(raw, account, session_maker, ingest)

        async def on_card_action(raw: dict[str, Any]) -> None:
            from cubebox.im.dingtalk.interactions import handle_card_action

            await handle_card_action(
                callback=raw,
                run_manager=run_manager,
            )

        client.register_callback_handler(
            dingtalk_stream.ChatbotMessage.TOPIC,
            _CallbackHandler(on_message),
        )
        client.register_callback_handler(
            "/v1.0/card/instances/callback",
            _CallbackHandler(on_card_action),
        )

        async def _run() -> None:
            try:
                await client.start()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "[DingTalk] Stream client crashed for {}", account.id
                )

        self._task = asyncio.create_task(_run(), name=f"dingtalk-gateway:{account.id}")

        def _on_task_done(task: asyncio.Task[None]) -> None:
            exc = task.exception() if not task.cancelled() else None
            if exc is not None:
                logger.error(
                    "[DingTalk] gateway task crashed for {}: {}",
                    account.id,
                    exc,
                    exc_info=exc,
                )

        self._task.add_done_callback(_on_task_done)

        # Periodic token refresh — DingTalk access tokens expire every 2h
        async def _token_loop() -> None:
            while True:
                await asyncio.sleep(6000)  # refresh every ~100 minutes
                try:
                    await self.refresh_access_token()
                    logger.debug("[DingTalk] token refreshed for {}", account.id)
                except Exception:
                    logger.warning(
                        "[DingTalk] token refresh failed for {}", account.id, exc_info=True
                    )

        self._refresh_task = asyncio.create_task(
            _token_loop(), name=f"dingtalk-token-refresh:{account.id}"
        )
        logger.info("[DingTalk] Gateway started for account {}", account.id)

    async def _handle_inbound(
        self,
        raw: dict[str, Any],
        account: Any,
        session_maker: Any,
        ingest: Any,
    ) -> None:
        connector = DingtalkConnector(bot_user_id=self._app_key)
        parsed = connector.parse_inbound(raw)
        if parsed is None:
            return
        parsed.account_external_id = account.external_account_id

        is_dm = parsed.scope_kind == "dm"
        gate_connector = DingtalkConnector(
            bot_user_id=self._app_key,
            access_token=self._access_token,
            conversation_id=parsed.channel_id,
            sender_staff_id=parsed.sender_ref,
            is_dm=is_dm,
            http_client=self._shared_http,
        )
        try:
            result = await ingest(
                parsed,
                account=account,
                session_maker=session_maker,
                identity_resolver=gate_connector,
                rejection_notifier=gate_connector,
            )
            logger.info(
                "[DingTalk] inbound {}: {}",
                parsed.platform_event_id,
                result.outcome,
            )
        except Exception:
            logger.exception(
                "[DingTalk] ingest failed for {}", parsed.platform_event_id
            )

    async def stop(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._client is not None:
            try:
                self._client.stop()
            except Exception:
                pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        await self._shared_http.aclose()
        logger.info(
            "[DingTalk] Gateway stopped for account {}", self._account.id
        )

    def is_open(self) -> bool:
        return self._task is not None and not self._task.done()

    async def refresh_access_token(self) -> str:
        """Refresh the access token for outbound API calls."""
        import httpx

        url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        payload = {"appKey": self._app_key, "appSecret": self._app_secret}
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(url, json=payload)
            data = resp.json()
            token = data.get("accessToken", "")
            self._access_token = token
            return token

    @property
    def access_token(self) -> str:
        return self._access_token

    async def _register_card_template(self) -> str:
        """Register the cubebox streaming card template. Returns template ID."""
        import httpx

        url = "https://api.dingtalk.com/v1.0/card/templates"
        headers = {
            "x-acs-dingtalk-access-token": self._access_token,
            "Content-Type": "application/json",
        }
        payload = {
            "cardTemplateJson": json.dumps({
                "config": {"autoLayout": True},
                "header": {},
                "cardContentList": [
                    {"id": "content", "type": "markdown", "props": {"content": "${content}"}},
                    {"id": "status", "type": "text", "props": {"content": "${status}"}},
                ],
                "cardActionList": [],
            }),
        }
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                resp = await http.post(url, headers=headers, json=payload)
                data = resp.json()
                tpl_id = data.get("cardTemplateId", "")
                if tpl_id:
                    logger.info("[DingTalk] card template registered: {}", tpl_id)
                else:
                    logger.warning("[DingTalk] card template registration returned no ID: {}", data)
                return tpl_id
        except Exception:
            logger.warning("[DingTalk] card template registration failed", exc_info=True)
            return ""


class _CallbackHandler(dingtalk_stream.CallbackHandler):
    """Subclasses the SDK's CallbackHandler to route events to our async handler."""

    def __init__(self, handler: Any) -> None:
        super().__init__()
        self._handler = handler

    async def process(self, callback: dingtalk_stream.CallbackMessage) -> tuple[str, str]:
        try:
            data = json.loads(callback.data) if isinstance(callback.data, str) else callback.data
            await self._handler(data)
        except Exception:
            logger.warning("[DingTalk] callback handler error", exc_info=True)
        return dingtalk_stream.AckMessage.STATUS_OK, "OK"
```

Note: The `dingtalk-stream` SDK's exact API surface needs to be verified at implementation time. `_CallbackHandler` subclasses the SDK's `dingtalk_stream.CallbackHandler` base class — the SDK dispatches callbacks via its `process()` method. `DingTalkStreamClient.start()` is awaitable.

- [ ] **Step 2: Verify mypy passes**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
uv run mypy cubebox/im/dingtalk/ 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
git add cubebox/im/dingtalk/gateway.py
git commit -m "feat(im): add DingtalkGateway — Stream mode lifecycle"
```

---

## Task 6: DingTalk platform connector + registration

Implements `PlatformConnector` protocol and wires everything together. Registers with the platform registry.

**Files:**
- Create: `backend/cubebox/im/dingtalk/_platform.py`
- Modify: `backend/cubebox/im/dingtalk/__init__.py`
- Modify: `backend/cubebox/im/runtime.py` (line 121–123: add dingtalk import)
- Modify: `backend/cubebox/services/im_connector.py` (add `compute_runtime` handling for `"stream"`)

- [ ] **Step 1: Implement DingtalkPlatform**

```python
# backend/cubebox/im/dingtalk/_platform.py
"""DingtalkPlatform — PlatformConnector implementation for DingTalk."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger


class DingtalkPlatform:
    """PlatformConnector for DingTalk (Stream mode only)."""

    def parse_inbound(self, raw: dict[str, Any]) -> Any:
        from cubebox.im.dingtalk.connector import DingtalkConnector

        connector = DingtalkConnector()
        return connector.parse_inbound(raw)

    async def build_tailer(
        self, *, run_id: str, queue_item: Any, account: Any, **kwargs: Any
    ) -> Any:
        from cubebox.im.dingtalk.connector import DingtalkConnector
        from cubebox.im.dingtalk.renderer import DingtalkOpDispatcher
        from cubebox.im.outbound import OutboundRunTailer
        from cubebox.im.types import RenderState

        app = kwargs["app"]
        gateways: dict[str, Any] = kwargs.get("gateways", {})
        load_secrets = kwargs.get("load_secrets")

        access_token = ""
        gw = gateways.get(account.id)
        if gw is not None:
            access_token = gw.access_token
            if not access_token:
                try:
                    access_token = await gw.refresh_access_token()
                except Exception:
                    logger.warning(
                        "[DingTalk] token refresh failed for {}", account.id
                    )

        is_dm = queue_item.scope_kind == "dm"
        connector = DingtalkConnector(
            bot_user_id=account.external_account_id,
            access_token=access_token,
            conversation_id=queue_item.channel_id,
            sender_staff_id=queue_item.sender_open_id,
            is_dm=is_dm,
        )

        cfg = account.config or {}
        state = RenderState(
            bot_name=cfg.get("bot_app_name") or "cubebox",
            run_id=run_id,
            reply_to_id=queue_item.reply_to_id,
            inbound_message_id=queue_item.inbound_message_id,
            stream_interval=1.0,
        )

        card_template_id = (gw.card_template_id if gw else "") or cfg.get("card_template_id", "")
        op_dispatcher = DingtalkOpDispatcher(
            connector=connector,
            state=state,
            card_template_id=card_template_id,
            open_conversation_id=queue_item.channel_id,
        )

        tailer = OutboundRunTailer(
            redis=app.state.redis,
            key_prefix=app.state.redis_key_prefix,
            run_id=run_id,
            connector=connector,
            state=state,
            dispatcher=op_dispatcher,
            responder_open_id=queue_item.sender_open_id,
        )
        asyncio.create_task(tailer.run(), name=f"im-tailer:{run_id}")

    async def on_account_enabled(self, account: Any, **kwargs: Any) -> None:
        from cubebox.im.dingtalk.gateway import DingtalkGateway
        from cubebox.im.inbound import ingest_inbound_event

        secrets: dict[str, Any] = kwargs.get("secrets", {})
        gateways: dict[str, Any] = kwargs.get("gateways", {})
        session_maker = kwargs.get("session_maker")
        run_manager = kwargs.get("run_manager")
        redis_key_prefix: str = kwargs.get("redis_key_prefix", "")

        app_key = str(secrets.get("app_key") or "")
        app_secret = str(secrets.get("app_secret") or "")
        if not app_key or not app_secret:
            logger.warning(
                "[DingTalk] skipping account {} — missing credentials",
                account.id,
            )
            return

        gw = DingtalkGateway(
            account=account,
            app_key=app_key,
            app_secret=app_secret,
            ingest=ingest_inbound_event,
            session_maker=session_maker,
            run_manager=run_manager,
            redis_key_prefix=redis_key_prefix,
        )
        await gw.refresh_access_token()
        await gw.start()
        gateways[account.id] = gw

    async def on_account_disabled(self, account: Any, **kwargs: Any) -> None:
        gateways: dict[str, Any] = kwargs.get("gateways", {})
        gw = gateways.pop(account.id, None)
        if gw is not None:
            await gw.stop()
```

- [ ] **Step 2: Update `__init__.py` to register the platform**

```python
# backend/cubebox/im/dingtalk/__init__.py
from cubebox.im.dingtalk._platform import DingtalkPlatform
from cubebox.im.registry import register_platform

register_platform("dingtalk", DingtalkPlatform())
```

- [ ] **Step 3: Add dingtalk import to runtime.py**

In `backend/cubebox/im/runtime.py`, add `import cubebox.im.dingtalk` alongside the other platform imports (after line 123):

```python
    import cubebox.im.discord  # noqa: F401
    import cubebox.im.feishu  # noqa: F401
    import cubebox.im.slack  # noqa: F401
    import cubebox.im.dingtalk  # noqa: F401
```

Also update the `delivery_mode.in_()` filter (lines 262–264 and the identical filter in `_sweep`) to include `"stream"`:

```python
IMConnectorAccount.delivery_mode.in_(
    ["long_connection", "gateway", "stream"]
),
```

- [ ] **Step 4: Update compute_runtime for "stream" delivery mode**

In `backend/cubebox/services/im_connector.py`, add a `"stream"` branch in `compute_runtime()` (after the `"gateway"` branch around line 53):

```python
    elif account.delivery_mode in ("gateway", "stream"):
```

Merge the `"gateway"` and `"stream"` checks since they both use the `gateways` dict.

- [ ] **Step 5: Update enable route delivery_mode check**

In `backend/cubebox/api/routes/v1/ws_im.py` line 352, update:

```python
    if updated.delivery_mode in ("long_connection", "gateway", "stream"):
```

- [ ] **Step 6: Verify mypy passes**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
uv run mypy cubebox/im/dingtalk/ cubebox/im/runtime.py cubebox/services/im_connector.py cubebox/api/routes/v1/ws_im.py 2>&1 | tail -10
```

- [ ] **Step 7: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
git add cubebox/im/dingtalk/_platform.py cubebox/im/dingtalk/__init__.py cubebox/im/runtime.py cubebox/services/im_connector.py cubebox/api/routes/v1/ws_im.py
git commit -m "feat(im): add DingtalkPlatform + wire into runtime + delivery_mode=stream"
```

---

## Task 7: Backend connect route + service

Adds `connect_dingtalk()` service method, schema, and route dispatch branch.

**Files:**
- Modify: `backend/cubebox/api/schemas/im_connector.py`
- Modify: `backend/cubebox/services/im_connector.py`
- Modify: `backend/cubebox/api/routes/v1/ws_im.py`

- [ ] **Step 1: Add ConnectDingtalkAccountIn schema**

In `backend/cubebox/api/schemas/im_connector.py`, add after `ConnectSlackAccountIn`:

```python
class ConnectDingtalkAccountIn(BaseModel):
    """Payload for ``POST /ws/{ws}/im/accounts`` when ``platform == 'dingtalk'``."""

    platform: Literal["dingtalk"] = "dingtalk"
    app_key: str = Field(min_length=1, max_length=128)
    app_secret: str = Field(min_length=1)
    acting_user_id: str = Field(default="self", min_length=1)
```

Update the `ConnectIMAccountIn` union to include it:

```python
ConnectIMAccountIn = Annotated[
    Annotated[ConnectFeishuAccountIn, Tag("feishu")]
    | Annotated[ConnectDiscordAccountIn, Tag("discord")]
    | Annotated[ConnectSlackAccountIn, Tag("slack")]
    | Annotated[ConnectDingtalkAccountIn, Tag("dingtalk")],
    Discriminator("platform"),
]
```

- [ ] **Step 2: Add connect_dingtalk service method**

In `backend/cubebox/services/im_connector.py`, add after `connect_slack`:

```python
    async def connect_dingtalk(
        self,
        *,
        workspace_id: str,
        app_key: str,
        app_secret: str,
        acting_user_id: str,
    ) -> IMConnectorAccount:
        """Bind one DingTalk enterprise bot: validate credentials, store, return account."""
        existing = (
            await self._session.execute(
                select(IMConnectorAccount).where(
                    IMConnectorAccount.org_id == self._org_id,
                    IMConnectorAccount.platform == "dingtalk",
                    IMConnectorAccount.external_account_id == app_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError(
                f"dingtalk account already exists for app_key={app_key} (id={existing.id})"
            )

        bot_name, bot_avatar_url = await self._hydrate_dingtalk_bot_info(
            app_key, app_secret
        )

        secret_payload = json.dumps(
            {
                "app_key": app_key,
                "app_secret": app_secret,
                "bot_open_id": app_key,
            }
        )
        try:
            credential_id = await self._credentials.create(
                kind="im_bot",
                name=f"dingtalk:{app_key}",
                plaintext=secret_payload,
            )
        except IntegrityError as exc:
            await self._session.rollback()
            raise ValueError(
                f"dingtalk account already exists for app_key={app_key} (credential race)"
            ) from exc
        try:
            account = IMConnectorAccount(
                org_id=self._org_id,
                workspace_id=workspace_id,
                platform="dingtalk",
                external_account_id=app_key,
                acting_user_id=acting_user_id,
                credential_id=credential_id,
                delivery_mode="stream",
                config={
                    "bot_app_name": bot_name or None,
                    "bot_avatar_url": bot_avatar_url or None,
                },
            )
            self._session.add(account)
            await self._session.commit()
            await self._session.refresh(account)
            return account
        except Exception:
            await self._session.rollback()
            try:
                await self._credentials.delete(credential_id=credential_id)
            except Exception:
                logger.warning(
                    "[IM] orphan credential {} could not be rolled back",
                    credential_id,
                    exc_info=True,
                )
            raise

    async def _hydrate_dingtalk_bot_info(
        self,
        app_key: str,
        app_secret: str,
    ) -> tuple[str, str]:
        """Validate credentials via access token exchange and return bot info."""
        import httpx

        url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        payload = {"appKey": app_key, "appSecret": app_secret}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                raise ValueError(f"DingTalk credential validation failed: {resp.text}")
            data = resp.json()
            token = data.get("accessToken")
            if not token:
                raise ValueError("DingTalk returned empty access token")

        # Best-effort bot name/avatar — not all DingTalk APIs expose this
        # reliably for enterprise internal bots. Return empty if unavailable.
        return ("", "")
```

- [ ] **Step 3: Add route dispatch branch**

In `backend/cubebox/api/routes/v1/ws_im.py`:

Add import at top:
```python
from cubebox.api.schemas.im_connector import ConnectDingtalkAccountIn
```

Add `_connect_dingtalk` handler (following the Slack pattern):
```python
async def _connect_dingtalk(
    body: ConnectDingtalkAccountIn,
    request: Request,
    ctx: RequestContext,
    session: AsyncSession,
    backend: EncryptionBackend,
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    acting = await _resolve_acting_user(body.acting_user_id, ctx, session)
    try:
        account = await svc.connect_dingtalk(
            workspace_id=ctx.workspace_id,
            app_key=body.app_key,
            app_secret=body.app_secret,
            acting_user_id=acting,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    starter = getattr(request.app.state, "im_connect_account", None)
    if starter is not None and account.enabled:
        try:
            await starter(account)
        except Exception:
            logger.warning(
                "[IM ws] dingtalk gateway startup failed for {}",
                account.id,
                exc_info=True,
            )
    return _to_out(account)
```

Add the `elif` branch in `connect_account`:
```python
    elif isinstance(body, ConnectDingtalkAccountIn):
        return await _connect_dingtalk(body, request, ctx, session, backend)
```

- [ ] **Step 4: Verify mypy passes**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
uv run mypy cubebox/api/schemas/im_connector.py cubebox/services/im_connector.py cubebox/api/routes/v1/ws_im.py 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
git add cubebox/api/schemas/im_connector.py cubebox/services/im_connector.py cubebox/api/routes/v1/ws_im.py
git commit -m "feat(im): add DingTalk connect route + service method"
```

---

## Task 8: Frontend — @cubebox/core schema + platform descriptor

Adds the `ConnectDingtalkAccountIn` type to the core package and creates the frontend platform descriptor.

**Files:**
- Modify: `frontend/packages/core/src/api/im.ts`
- Create: `frontend/packages/web/components/im/ImConnectWizard/platforms/dingtalk.ts`
- Modify: `frontend/packages/web/components/im/ImConnectWizard/platforms/types.ts`
- Modify: `frontend/packages/web/components/im/ImConnectWizard/platforms/index.ts`
- Modify: `frontend/packages/web/components/im/PlatformLogo.tsx`
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`

- [ ] **Step 1: Add ConnectDingtalkAccountIn to @cubebox/core**

In `frontend/packages/core/src/api/im.ts`, add:

```typescript
export interface ConnectDingtalkAccountIn {
  platform: 'dingtalk'
  app_key: string
  app_secret: string
  acting_user_id?: string
}
```

Update the `ConnectImAccountIn` union:

```typescript
export type ConnectImAccountIn =
  | ConnectFeishuAccountIn
  | ConnectDiscordAccountIn
  | ConnectSlackAccountIn
  | ConnectDingtalkAccountIn
```

- [ ] **Step 2: Widen PlatformDescriptor.id union**

In `frontend/packages/web/components/im/ImConnectWizard/platforms/types.ts`, add `'dingtalk'` to the `id` union:

```typescript
id: 'feishu' | 'discord' | 'slack' | 'teams' | 'dingtalk'
```

- [ ] **Step 3: Create dingtalk.ts platform descriptor**

```typescript
// frontend/packages/web/components/im/ImConnectWizard/platforms/dingtalk.ts
import type { PlatformDescriptor } from './types'

export const dingtalkDescriptor: PlatformDescriptor = {
  id: 'dingtalk',
  labelKey: 'im.platform.dingtalk.label',
  iconName: 'dingtalk',
  live: true,
  prereqs: [
    {
      key: 'app',
      labelKey: 'im.wizard.dingtalk.prereq.app',
      helpUrl: () => 'https://open.dingtalk.com/document/orgapp/create-orgapp',
    },
    {
      key: 'stream',
      labelKey: 'im.wizard.dingtalk.prereq.stream',
    },
    {
      key: 'permissions',
      labelKey: 'im.wizard.dingtalk.prereq.permissions',
      items: [
        'qyapi_chat_manage',
        'qyapi_robot_sendmsg',
        'Contact.User.Read',
      ],
    },
    {
      key: 'credentials',
      labelKey: 'im.wizard.dingtalk.prereq.credentials',
    },
  ],
  credentialFields: [
    {
      key: 'app_key',
      labelKey: 'im.wizard.dingtalk.field.appKey',
      type: 'text',
      required: true,
      placeholder: 'ding...',
    },
    {
      key: 'app_secret',
      labelKey: 'im.wizard.dingtalk.field.appSecret',
      type: 'password',
      required: true,
    },
  ],
  steps: [
    { key: 'prereqs', labelKey: 'im.wizard.step.prereqs', Component: null as any },
    { key: 'credentials', labelKey: 'im.wizard.step.credentials', Component: null as any },
    { key: 'verify', labelKey: 'im.wizard.step.verify', Component: null as any },
  ],
  buildPayload: (form) => ({
    platform: 'dingtalk' as const,
    app_key: form.app_key ?? '',
    app_secret: form.app_secret ?? '',
    acting_user_id: 'self',
  }),
  scopeConsoleUrl: () => 'https://open.dingtalk.com',
}
```

Note: The `steps` array's `Component` references should use the same step components as other platforms (StepPrereqs, StepCredentials, StepVerify). Look at how `slack.ts` imports and uses them, and follow the same pattern. The `null as any` above is a placeholder — replace with the actual component imports at implementation time.

- [ ] **Step 4: Register in platforms/index.ts**

Add import and include in `ALL_PLATFORMS`:

```typescript
import { dingtalkDescriptor } from './dingtalk'

export const ALL_PLATFORMS: PlatformDescriptor[] = [
  feishuDescriptor,
  discordDescriptor,
  slackDescriptor,
  dingtalkDescriptor,
  teamsDescriptor,
]
```

- [ ] **Step 5: Add DingTalk logo to PlatformLogo.tsx**

Add a `DingtalkLogo` SVG component (use the official DingTalk logo mark — a simple geometric shape). Add it to the `LOGOS` map:

```typescript
dingtalk: DingtalkLogo,
```

- [ ] **Step 6: Add i18n keys**

In `frontend/packages/web/messages/en.json`, add under `"im"`:

```json
"platform": {
  "dingtalk": { "label": "DingTalk" }
},
"wizard": {
  "dingtalk": {
    "field": {
      "appKey": "AppKey (ding...)",
      "appSecret": "AppSecret"
    },
    "prereq": {
      "app": "Create an enterprise internal bot at open.dingtalk.com",
      "stream": "Enable Stream Mode in the bot settings",
      "permissions": "Grant these permissions to the bot",
      "credentials": "Copy the AppKey and AppSecret from the bot settings"
    }
  }
}
```

In `frontend/packages/web/messages/zh.json`, add equivalent Chinese translations.

- [ ] **Step 7: Build core + check frontend types**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/frontend
pnpm --filter @cubebox/core build
pnpm --filter @cubebox/web tsc --noEmit 2>&1 | tail -10
```

- [ ] **Step 8: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/frontend
git add packages/core/src/api/im.ts \
  packages/web/components/im/ImConnectWizard/platforms/dingtalk.ts \
  packages/web/components/im/ImConnectWizard/platforms/types.ts \
  packages/web/components/im/ImConnectWizard/platforms/index.ts \
  packages/web/components/im/PlatformLogo.tsx \
  packages/web/messages/en.json \
  packages/web/messages/zh.json
git commit -m "feat(im): add DingTalk frontend platform descriptor + core schema"
```

---

## Task 9: Keyword-based link command

DingTalk has no slash commands. The bot recognizes the keyword "link" and generates a JWT identity-link URL.

**Files:**
- Modify: `backend/cubebox/im/dingtalk/connector.py` (add link keyword detection)
- Modify: `backend/cubebox/im/dingtalk/gateway.py` (check for link keyword before ingest)

- [ ] **Step 1: Add link keyword detection to connector**

Add a method to `DingtalkConnector`:

```python
    def is_link_command(self, raw: dict[str, Any]) -> bool:
        """Check if the message is a 'link <email>' keyword command."""
        text_obj = raw.get("text") or {}
        text: str = text_obj.get("content", "").strip().lower()
        return text.startswith("link ") or text in ("link", "/link")

    def parse_link_email(self, raw: dict[str, Any]) -> str:
        """Extract email from a 'link alice@example.com' command. Returns '' if no email."""
        text_obj = raw.get("text") or {}
        text: str = text_obj.get("content", "").strip()
        parts = text.split(maxsplit=1)
        if len(parts) == 2 and "@" in parts[1]:
            return parts[1].strip().lower()
        return ""
```

- [ ] **Step 2: Add link handling to gateway**

In `DingtalkGateway._handle_inbound`, before calling `ingest`, check for the link keyword:

```python
    connector = DingtalkConnector(bot_user_id=self._app_key)
    if connector.is_link_command(raw):
        await self._handle_link_command(raw)
        return
    # ... rest of existing parse + ingest logic
```

Add `_handle_link_command` method to `DingtalkGateway`:

```python
    async def _handle_link_command(self, raw: dict[str, Any]) -> None:
        """Handle 'link alice@example.com' by sending an identity-link URL."""
        sender_staff_id = raw.get("senderStaffId", "")
        conversation_id = raw.get("conversationId", "")
        if not sender_staff_id or not conversation_id:
            return

        connector = DingtalkConnector(bot_user_id=self._app_key)
        email = connector.parse_link_email(raw)
        if not email:
            gate_connector = DingtalkConnector(
                bot_user_id=self._app_key,
                access_token=self._access_token,
                conversation_id=conversation_id,
            )
            await gate_connector.reply_markdown(
                title="Link",
                text="Usage: `link alice@example.com`",
                open_conversation_id=conversation_id,
            )
            return

        try:
            from cubebox.im.link import get_frontend_base_url, get_jwt_secret, sign_link_token

            token = sign_link_token(
                im_user_id=sender_staff_id,
                email=email,
                account_id=self._account.id,
                workspace_id=self._account.workspace_id,
                platform="dingtalk",
                secret=get_jwt_secret(),
            )
        except Exception:
            logger.warning("[DingTalk] sign_link_token failed", exc_info=True)
            return

        base = get_frontend_base_url()
        url = f"{base}/im-link?token={token}"

        gate_connector = DingtalkConnector(
            bot_user_id=self._app_key,
            access_token=self._access_token,
            conversation_id=conversation_id,
        )
        await gate_connector.reply_markdown(
            title="Link your account",
            text=f"Click to bind your cubebox account:\n\n[Link your account]({url})",
            open_conversation_id=conversation_id,
        )
```

- [ ] **Step 3: Verify mypy passes**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
uv run mypy cubebox/im/dingtalk/ 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
git add cubebox/im/dingtalk/connector.py cubebox/im/dingtalk/gateway.py
git commit -m "feat(im): add DingTalk keyword-based link command"
```

---

## Task 10: Pre-PR sweep — mypy + ruff + unit tests

Run the full backend checks before opening a PR.

- [ ] **Step 1: Run mypy on the full backend**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
mkdir -p tmp
uv run mypy cubebox/ 2>&1 | tee tmp/mypy.log | tail -5
```

- [ ] **Step 2: Run ruff**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
uv run ruff check cubebox/im/dingtalk/ tests/unit/im/test_dingtalk_*.py 2>&1 | tail -5
```

- [ ] **Step 3: Run unit tests**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/backend
uv run pytest tests/unit/im/test_dingtalk_*.py -v --no-cov 2>&1 | tee tmp/test.log | tail -15
```

- [ ] **Step 4: Run frontend type check**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-im-dingtalk/frontend
pnpm --filter @cubebox/core build && pnpm --filter @cubebox/web tsc --noEmit 2>&1 | tail -5
```

- [ ] **Step 5: Fix any issues found, then commit**

```bash
git add -A && git commit -m "fix: pre-PR sweep fixes"
```
