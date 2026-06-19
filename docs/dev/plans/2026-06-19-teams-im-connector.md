# Microsoft Teams IM Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Microsoft Teams as the fourth IM connector platform (after Slack, Discord, Feishu), following the existing `PlatformConnector` registry pattern.

**Architecture:** Webhook delivery mode — Azure Bot Service POSTs activities to `POST /api/v1/im/teams/messages`. One `microsoft_teams.apps.App` instance per enabled account, cached in memory. Hybrid rendering: Markdown streaming via `updateActivity` + Adaptive Cards for HITL buttons.

**Tech Stack:** `microsoft-teams-apps` (Python SDK), `httpx` (Graph API), FastAPI webhook route, Adaptive Cards v1.4.

**Worktree:** `/home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector`
**Spec:** `docs/dev/specs/2026-06-19-teams-im-connector-design.md`

---

## File Map

### New files

| File | Responsibility |
|---|---|
| `backend/cubebox/im/teams/__init__.py` | `register_platform("teams", TeamsPlatform())` |
| `backend/cubebox/im/teams/_platform.py` | `TeamsPlatform` — 4-method `PlatformConnector` |
| `backend/cubebox/im/teams/connector.py` | `TeamsConnector` — parse_inbound, send/edit/resolve_email |
| `backend/cubebox/im/teams/renderer.py` | `TeamsOpDispatcher` — dispatch_create/stream/patch/finalize |
| `backend/cubebox/im/teams/interactions.py` | `handle_card_action` — Adaptive Card submit → resume |
| `backend/cubebox/im/teams/commands.py` | `/link` text command recognition |
| `backend/cubebox/im/teams/format.py` | Markdown normalization for Teams |
| `backend/cubebox/im/teams/app_manager.py` | `TeamsAppManager` — App instance lifecycle + cache |
| `backend/cubebox/im/teams/graph.py` | `TeamsGraphClient` — Graph API email lookup + token cache |
| `backend/tests/unit/im/test_teams_connector.py` | Unit tests for connector parse_inbound |
| `backend/tests/unit/im/test_teams_format.py` | Unit tests for format.py |
| `frontend/packages/web/components/im/ImConnectWizard/platforms/teams.ts` | Full `PlatformDescriptor` (replaces stub) |

### Modified files

| File | Change |
|---|---|
| `backend/cubebox/api/schemas/im_connector.py` | Add `ConnectTeamsAccountIn`, update union |
| `backend/cubebox/services/im_connector.py` | Add `connect_teams()` + `_hydrate_teams_bot_info()` |
| `backend/cubebox/api/routes/v1/ws_im.py` | Add `_connect_teams()` branch |
| `backend/cubebox/api/routes/v1/im_ingress.py` | Add `POST /im/teams/messages` route |
| `backend/cubebox/im/runtime.py` | Add `import cubebox.im.teams`, init webhook Apps on startup |
| `frontend/packages/core/src/api/im.ts` | Add `ConnectTeamsAccountIn` type + union |
| `frontend/packages/web/messages/en.json` | Add Teams wizard i18n keys |
| `frontend/packages/web/messages/zh.json` | Add Teams wizard i18n keys (Chinese) |
| `frontend/packages/web/components/im/ImConnectWizard/platforms/index.ts` | Import from `./teams` not `./teams.stub` |

---

## Task 1: Add SDK dependency + package skeleton

**Files:**
- Modify: `backend/pyproject.toml` (via `uv add`)
- Create: `backend/cubebox/im/teams/__init__.py` (empty placeholder)

- [ ] **Step 1: Add `microsoft-teams-apps` to backend deps**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/backend
uv add microsoft-teams-apps
```

- [ ] **Step 2: Verify the SDK is importable**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/backend
uv run python -c "from microsoft_teams.apps import App; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Create the empty package**

Create `backend/cubebox/im/teams/__init__.py`:

```python
"""Microsoft Teams IM connector."""
```

- [ ] **Step 4: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/cubebox/im/teams/__init__.py
git commit -m "feat(im): add microsoft-teams-apps SDK dependency + teams package skeleton"
```

---

## Task 2: Markdown format utility

**Files:**
- Create: `backend/cubebox/im/teams/format.py`
- Test: `backend/tests/unit/im/test_teams_format.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/im/test_teams_format.py`:

```python
from cubebox.im.teams.format import normalize_for_teams


def test_strikethrough_stripped() -> None:
    assert normalize_for_teams("~~removed~~") == "removed"


def test_bold_preserved() -> None:
    assert normalize_for_teams("**bold**") == "**bold**"


def test_italic_preserved() -> None:
    assert normalize_for_teams("*italic*") == "*italic*"


def test_link_preserved() -> None:
    assert normalize_for_teams("[click](https://x.com)") == "[click](https://x.com)"


def test_code_block_preserved() -> None:
    src = "```python\nprint('hi')\n```"
    assert normalize_for_teams(src) == src


def test_inline_code_preserved() -> None:
    assert normalize_for_teams("use `foo()`") == "use `foo()`"


def test_mention_tag_stripped() -> None:
    assert normalize_for_teams("<at>CubeBot</at> hello") == "hello"


def test_mention_tag_with_id_stripped() -> None:
    assert normalize_for_teams('<at id="abc">CubeBot</at> hi') == "hi"


def test_empty_after_strip() -> None:
    assert normalize_for_teams("<at>Bot</at>") == ""
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/backend
uv run pytest tests/unit/im/test_teams_format.py -v --no-cov 2>&1 | tail -5
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement format.py**

Create `backend/cubebox/im/teams/format.py`:

```python
"""Markdown normalization for Teams.

Teams supports most standard Markdown (bold, italic, code, links, lists,
tables) but NOT strikethrough (~~text~~). Also strips <at>...</at>
mention tags injected by Teams into inbound message text.
"""

from __future__ import annotations

import re

_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_RE = re.compile(r"`[^`]+`")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_AT_TAG_RE = re.compile(r"<at[^>]*>[^<]*</at>\s*")


def normalize_for_teams(text: str) -> str:
    """Strip unsupported syntax from Markdown for Teams rendering."""
    placeholders: list[str] = []

    def _protect(m: re.Match[str]) -> str:
        placeholders.append(m.group(0))
        return f"\x00PH{len(placeholders) - 1}\x00"

    protected = _FENCED_CODE_RE.sub(_protect, text)
    protected = _INLINE_CODE_RE.sub(_protect, protected)

    protected = _STRIKE_RE.sub(r"\1", protected)

    result = protected
    for i, original in enumerate(placeholders):
        result = result.replace(f"\x00PH{i}\x00", original)
    return result


def strip_mention_tags(text: str) -> str:
    """Remove Teams <at>BotName</at> tags from inbound message text."""
    return _AT_TAG_RE.sub("", text).strip()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/backend
uv run pytest tests/unit/im/test_teams_format.py -v --no-cov 2>&1 | tail -5
```

Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/im/teams/format.py backend/tests/unit/im/test_teams_format.py
git commit -m "feat(im): teams markdown format utility"
```

---

## Task 3: TeamsConnector — parse_inbound + outbound

**Files:**
- Create: `backend/cubebox/im/teams/connector.py`
- Create: `backend/cubebox/im/teams/graph.py`
- Test: `backend/tests/unit/im/test_teams_connector.py`

The `TeamsConnector` is the core class: inbound event parsing and outbound message API. It follows the dual-role pattern of `SlackConnector` — serves as both `IdentityResolver` and `RejectionNotifier`.

- [ ] **Step 1: Write failing tests for parse_inbound**

Create `backend/tests/unit/im/test_teams_connector.py`:

```python
from cubebox.im.teams.connector import TeamsConnector
from cubebox.im.types import DM_SCOPE_KEY, make_participant_scope, make_thread_participant_scope


def _make_activity(
    *,
    conversation_type: str = "personal",
    text: str = "hello",
    from_aad: str = "aad-user-123",
    from_id: str = "29:user-id",
    from_name: str = "Test User",
    conversation_id: str = "conv-123",
    message_id: str = "msg-001",
    reply_to_id: str | None = None,
    recipient_id: str = "bot-app-id",
    at_mention: bool = False,
) -> dict:
    activity: dict = {
        "type": "message",
        "id": message_id,
        "text": text,
        "from": {
            "id": from_id,
            "aadObjectId": from_aad,
            "name": from_name,
        },
        "conversation": {
            "id": conversation_id,
            "conversationType": conversation_type,
        },
        "recipient": {
            "id": recipient_id,
            "name": "CubeBot",
        },
    }
    if reply_to_id:
        activity["replyToId"] = reply_to_id
    if at_mention:
        activity["entities"] = [
            {
                "type": "mention",
                "mentioned": {"id": recipient_id, "name": "CubeBot"},
                "text": "<at>CubeBot</at>",
            }
        ]
        activity["text"] = f"<at>CubeBot</at> {text}"
    return activity


class TestParseInbound:
    def test_dm_returns_event(self) -> None:
        c = TeamsConnector(bot_id="bot-app-id")
        ev = c.parse_inbound(_make_activity())
        assert ev is not None
        assert ev.platform == "teams"
        assert ev.scope_key == DM_SCOPE_KEY
        assert ev.scope_kind == "dm"
        assert ev.text == "hello"
        assert ev.sender_ref == "aad-user-123"
        assert ev.sender_open_id == "aad-user-123"
        assert ev.reply_to_id is None

    def test_group_chat_with_mention(self) -> None:
        c = TeamsConnector(bot_id="bot-app-id")
        ev = c.parse_inbound(
            _make_activity(conversation_type="groupChat", at_mention=True)
        )
        assert ev is not None
        assert ev.scope_key == make_participant_scope("aad-user-123")
        assert ev.scope_kind == "group"
        assert ev.text == "hello"

    def test_channel_with_mention(self) -> None:
        c = TeamsConnector(bot_id="bot-app-id")
        ev = c.parse_inbound(
            _make_activity(conversation_type="channel", at_mention=True)
        )
        assert ev is not None
        assert ev.scope_key == make_participant_scope("aad-user-123")
        assert ev.scope_kind == "channel"

    def test_channel_thread_reply(self) -> None:
        c = TeamsConnector(bot_id="bot-app-id")
        ev = c.parse_inbound(
            _make_activity(
                conversation_type="channel",
                at_mention=True,
                reply_to_id="parent-msg-id",
            )
        )
        assert ev is not None
        assert ev.scope_key == make_thread_participant_scope(
            "aad-user-123", "parent-msg-id"
        )
        assert ev.scope_kind == "thread"

    def test_group_without_mention_ignored(self) -> None:
        c = TeamsConnector(bot_id="bot-app-id")
        ev = c.parse_inbound(
            _make_activity(conversation_type="groupChat", at_mention=False)
        )
        assert ev is None

    def test_non_message_type_ignored(self) -> None:
        c = TeamsConnector(bot_id="bot-app-id")
        activity = _make_activity()
        activity["type"] = "conversationUpdate"
        assert c.parse_inbound(activity) is None

    def test_bot_own_message_ignored(self) -> None:
        c = TeamsConnector(bot_id="bot-app-id")
        ev = c.parse_inbound(
            _make_activity(from_id="bot-app-id", from_aad="")
        )
        assert ev is None

    def test_empty_text_after_strip_ignored(self) -> None:
        c = TeamsConnector(bot_id="bot-app-id")
        ev = c.parse_inbound(
            _make_activity(
                conversation_type="groupChat",
                at_mention=True,
                text="",
            )
        )
        assert ev is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/backend
uv run pytest tests/unit/im/test_teams_connector.py -v --no-cov 2>&1 | tail -5
```

- [ ] **Step 3: Implement graph.py — Graph API email resolver**

Create `backend/cubebox/im/teams/graph.py`:

```python
"""Microsoft Graph API client for Teams identity resolution.

Acquires tokens via OAuth2 client credentials flow and caches them
in memory. Used by TeamsConnector.resolve_email() to look up a user's
email from their AAD Object ID.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from loguru import logger

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class TeamsGraphClient:
    """Cached Graph API client for one Teams bot account."""

    def __init__(self, *, app_id: str, app_secret: str, tenant_id: str) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._tenant_id = tenant_id
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def _ensure_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at - 60:
            return self._token
        url = _TOKEN_URL.format(tenant_id=self._tenant_id)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._app_id,
                    "client_secret": self._app_secret,
                    "scope": _GRAPH_SCOPE,
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
        self._token = str(data["access_token"])
        self._token_expires_at = time.monotonic() + int(data.get("expires_in", 3600))
        return self._token

    async def get_user_email(self, aad_object_id: str) -> str | None:
        """Resolve AAD Object ID → email via Graph API ``GET /users/{id}``."""
        try:
            token = await self._ensure_token()
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{_GRAPH_BASE}/users/{aad_object_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"$select": "mail,userPrincipalName"},
                    timeout=10.0,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "[Teams] Graph /users/{} returned {}",
                        aad_object_id,
                        resp.status_code,
                    )
                    return None
                data: dict[str, Any] = resp.json()
                return str(data.get("mail") or data.get("userPrincipalName") or "") or None
        except Exception:
            logger.warning(
                "[Teams] Graph email lookup failed for {}",
                aad_object_id,
                exc_info=True,
            )
            return None
```

- [ ] **Step 4: Implement connector.py**

Create `backend/cubebox/im/teams/connector.py`:

```python
"""Teams connector: inbound parse + outbound message send/edit + identity.

Inbound parsing works on raw Bot Framework activity dicts.
Outbound calls use the microsoft-teams-apps SDK App instance.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from cubebox.im.outbound import _FloodSignal
from cubebox.im.teams.format import normalize_for_teams, strip_mention_tags
from cubebox.im.types import (
    DM_SCOPE_KEY,
    InboundEvent,
    RenderState,
    make_participant_scope,
    make_thread_participant_scope,
)

_TEAMS_MSG_LIMIT = 25000


class TeamsRateLimitError(_FloodSignal):
    """Raised when Teams API returns HTTP 429."""


class TeamsConnector:
    """Connector for one Teams bot account.

    Construction:
    - Inbound parsing only needs ``bot_id``.
    - Outbound calls need ``app`` (microsoft_teams App instance)
      plus ``channel_id`` and optionally ``reply_to_id``.
    """

    def __init__(
        self,
        *,
        bot_id: str = "",
        app: Any = None,
        channel_id: str | None = None,
        reply_to_id: str | None = None,
        graph_client: Any = None,
    ) -> None:
        self._bot_id = bot_id
        self._app = app
        self._channel_id = channel_id
        self._reply_to_id = reply_to_id
        self._graph_client = graph_client

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    def parse_inbound(self, activity: dict[str, Any]) -> InboundEvent | None:
        """Normalize a Bot Framework activity dict into an InboundEvent.

        Returns None for activities we should ignore.
        """
        if activity.get("type") != "message":
            return None

        from_obj: dict[str, Any] = activity.get("from", {})
        sender_id: str = str(from_obj.get("id") or "")
        aad_object_id: str = str(from_obj.get("aadObjectId") or "")

        if sender_id == self._bot_id or (not aad_object_id and sender_id == self._bot_id):
            return None

        conversation: dict[str, Any] = activity.get("conversation", {})
        conv_type: str = str(conversation.get("conversationType") or "personal")
        conv_id: str = str(conversation.get("id") or "")
        message_id: str = str(activity.get("id") or "")
        reply_to_id: str | None = activity.get("replyToId")

        text: str = str(activity.get("text") or "")
        text = strip_mention_tags(text)

        is_dm = conv_type == "personal"
        is_mentioned = self._is_bot_mentioned(activity) if not is_dm else True

        if not is_dm and not is_mentioned:
            return None

        if not text.strip():
            return None

        sender_ref = aad_object_id or sender_id
        platform_event_id = message_id or conv_id

        if is_dm:
            return InboundEvent(
                platform="teams",
                account_external_id="",
                platform_event_id=platform_event_id,
                channel_id=conv_id,
                scope_key=DM_SCOPE_KEY,
                scope_kind="dm",
                reply_to_id=None,
                inbound_message_id=message_id,
                sender_ref=sender_ref,
                sender_open_id=aad_object_id or None,
                text=text.strip(),
            )

        if conv_type == "channel" and reply_to_id:
            return InboundEvent(
                platform="teams",
                account_external_id="",
                platform_event_id=platform_event_id,
                channel_id=conv_id,
                scope_key=make_thread_participant_scope(sender_ref, reply_to_id),
                scope_kind="thread",
                reply_to_id=reply_to_id,
                inbound_message_id=message_id,
                sender_ref=sender_ref,
                sender_open_id=aad_object_id or None,
                text=text.strip(),
            )

        scope_kind = "group" if conv_type == "groupChat" else "channel"
        return InboundEvent(
            platform="teams",
            account_external_id="",
            platform_event_id=platform_event_id,
            channel_id=conv_id,
            scope_key=make_participant_scope(sender_ref),
            scope_kind=scope_kind,
            reply_to_id=message_id,
            inbound_message_id=message_id,
            sender_ref=sender_ref,
            sender_open_id=aad_object_id or None,
            text=text.strip(),
        )

    def _is_bot_mentioned(self, activity: dict[str, Any]) -> bool:
        """Check if the bot is @mentioned in the activity entities."""
        for entity in activity.get("entities", []):
            if entity.get("type") != "mention":
                continue
            mentioned: dict[str, Any] = entity.get("mentioned", {})
            if str(mentioned.get("id") or "") == self._bot_id:
                return True
        return False

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    async def send_message(self, text: str) -> str | None:
        """Send a Markdown text message. Returns the activity ID."""
        if self._app is None or not self._channel_id:
            return None
        try:
            text = normalize_for_teams(text[:_TEAMS_MSG_LIMIT])
            result = await self._app.send(self._channel_id, text)
            return str(result.id) if result and hasattr(result, "id") else None
        except Exception:
            logger.warning("[Teams] send_message failed", exc_info=True)
            return None

    async def edit_message(self, activity_id: str, text: str) -> bool:
        """Update an existing message. Raises TeamsRateLimitError on 429."""
        if self._app is None or not self._channel_id:
            return False
        try:
            text = normalize_for_teams(text[:_TEAMS_MSG_LIMIT])
            await self._app.update_activity(
                self._channel_id, activity_id, text
            )
            return True
        except Exception as exc:
            if _is_rate_limit(exc):
                raise TeamsRateLimitError(f"edit rate limited: {exc}") from exc
            logger.warning("[Teams] edit_message failed", exc_info=True)
            return False

    async def send_typing(self) -> None:
        """Send a typing indicator."""
        if self._app is None or not self._channel_id:
            return
        try:
            from microsoft_teams.api.activities.typing import TypingActivityInput

            await self._app.send(self._channel_id, TypingActivityInput())
        except Exception:
            logger.debug("[Teams] typing indicator failed", exc_info=True)

    async def send_card(self, card: dict[str, Any]) -> str | None:
        """Send an Adaptive Card. Returns the activity ID."""
        if self._app is None or not self._channel_id:
            return None
        try:
            from microsoft_teams.api import Attachment, MessageActivityInput

            attachment = Attachment(
                content_type="application/vnd.microsoft.card.adaptive",
                content=card,
            )
            msg = MessageActivityInput(attachments=[attachment])
            result = await self._app.send(self._channel_id, msg)
            return str(result.id) if result and hasattr(result, "id") else None
        except Exception:
            logger.warning("[Teams] send_card failed", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Processing lifecycle hooks (called by OutboundRunTailer)
    # ------------------------------------------------------------------

    async def on_processing_start(self, state: RenderState) -> None:
        await self.send_typing()

    async def on_processing_complete(self, state: RenderState) -> None:
        pass

    async def on_processing_failed(self, state: RenderState) -> None:
        pass

    # ------------------------------------------------------------------
    # IdentityResolver protocol
    # ------------------------------------------------------------------

    async def resolve_email(self, open_id: str) -> str | None:
        """Resolve an AAD Object ID to email via Microsoft Graph."""
        if self._graph_client is None:
            return None
        return await self._graph_client.get_user_email(open_id)

    # ------------------------------------------------------------------
    # RejectionNotifier protocol
    # ------------------------------------------------------------------

    async def send_to_chat(
        self, chat_id: str, reply_to_id: str | None, text: str
    ) -> str | None:
        """Send plain text to a chat (for rejection notices)."""
        if self._app is None:
            return None
        try:
            result = await self._app.send(chat_id, text)
            return str(result.id) if result and hasattr(result, "id") else None
        except Exception:
            logger.warning("[Teams] send_to_chat failed", exc_info=True)
            return None


def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "rate" in msg and "limit" in msg
```

- [ ] **Step 5: Run tests**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/backend
uv run pytest tests/unit/im/test_teams_connector.py -v --no-cov 2>&1 | tail -10
```

Expected: all PASSED

- [ ] **Step 6: Run mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/backend
uv run mypy cubebox/im/teams/ 2>&1 | tail -5
```

Fix any type errors.

- [ ] **Step 7: Commit**

```bash
git add backend/cubebox/im/teams/connector.py backend/cubebox/im/teams/graph.py \
  backend/tests/unit/im/test_teams_connector.py
git commit -m "feat(im): teams connector with parse_inbound, outbound, graph identity"
```

---

## Task 4: TeamsOpDispatcher — outbound renderer

**Files:**
- Create: `backend/cubebox/im/teams/renderer.py`

Mirrors `SlackOpDispatcher` and `DiscordOpDispatcher`. Markdown streaming
via `edit_message`, Adaptive Card buttons for HITL.

- [ ] **Step 1: Implement renderer.py**

Create `backend/cubebox/im/teams/renderer.py`:

```python
"""Teams outbound renderer — Markdown messages with updateActivity streaming."""

from __future__ import annotations

from typing import Any

from loguru import logger

from cubebox.im.outbound import find_split_point, note_edit_success, note_flood_strike
from cubebox.im.teams.connector import TeamsRateLimitError
from cubebox.im.types import RenderState

_TEAMS_MSG_LIMIT = 25000
_SPLIT_THRESHOLD = 24000


class TeamsOpDispatcher:
    """Dispatches outbound ops to Teams via Markdown messages + Adaptive Cards."""

    def __init__(
        self,
        *,
        connector: Any,
        state: RenderState,
    ) -> None:
        self._connector = connector
        self._state = state
        self.sent_char_offset: int = 0
        self._pending_input_sent_id: str | None = None

    async def dispatch_create(self, state: Any) -> bool:
        s = self._state
        text = s.card_state.streaming_content
        if not text:
            text = "..."
        current_segment = text[self.sent_char_offset:]
        if len(current_segment) > _SPLIT_THRESHOLD:
            split_at = find_split_point(current_segment, _SPLIT_THRESHOLD)
            send_text = current_segment[:split_at]
            self.sent_char_offset += split_at
        else:
            send_text = current_segment
        msg_id = await self._connector.send_message(send_text)
        if msg_id is None:
            return False
        s.card_id = msg_id
        s.bot_message_id = msg_id
        return True

    async def dispatch_stream(self, state: Any, text: str) -> bool:
        s = self._state
        if s.bot_message_id is None:
            return await self.dispatch_create(state)
        full_content = s.card_state.streaming_content
        current_segment = full_content[self.sent_char_offset:]
        if len(current_segment) > _SPLIT_THRESHOLD:
            split_at = find_split_point(current_segment, _SPLIT_THRESHOLD)
            finalize_text = current_segment[:split_at]
            try:
                await self._connector.edit_message(s.bot_message_id, finalize_text)
            except TeamsRateLimitError:
                note_flood_strike(s)
                return False
            self.sent_char_offset += split_at
            remaining = full_content[self.sent_char_offset:]
            if remaining:
                msg_id = await self._connector.send_message(
                    remaining[:_SPLIT_THRESHOLD]
                )
                if msg_id:
                    s.card_id = msg_id
                    s.bot_message_id = msg_id
            note_edit_success(s)
            return True
        try:
            ok = await self._connector.edit_message(
                s.bot_message_id, current_segment
            )
        except TeamsRateLimitError:
            note_flood_strike(s)
            return False
        if ok:
            note_edit_success(s)
        return bool(ok)

    async def dispatch_patch(self, state: Any) -> bool:
        s = self._state
        pending = s.card_state.pending_input
        pending_id = (
            f"{pending.kind}:{pending.run_id}" if pending else None
        )
        if (
            pending is not None
            and pending.resolved_choice is None
            and pending.choices
            and pending_id != self._pending_input_sent_id
        ):
            await self._send_pending_input_card(pending)
            self._pending_input_sent_id = pending_id
        if pending is not None and pending.resolved_choice is not None:
            s.card_id = None
            s.bot_message_id = None
            self.sent_char_offset = len(s.card_state.streaming_content)
        return True

    async def _send_pending_input_card(self, pending: Any) -> None:
        """Send AskUser/SandboxConfirm as an Adaptive Card with Action.Submit."""
        qid = pending.question_id or ""
        akey = pending.answer_key or ""
        short_qid = qid[:8]

        body: list[dict[str, Any]] = []
        if pending.question:
            body.append({
                "type": "TextBlock",
                "text": pending.question,
                "wrap": True,
            })

        actions: list[dict[str, Any]] = []
        for label, value, btn_type in pending.choices:
            style = "destructive" if btn_type == "danger" else "default"
            action_data = (
                f"im:{pending.kind}:{pending.run_id}"
                f":{short_qid}:{akey}:{value}"
            )
            actions.append({
                "type": "Action.Submit",
                "title": label[:40],
                "style": style,
                "data": {"action": action_data},
            })

        card: dict[str, Any] = {
            "type": "AdaptiveCard",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4",
            "body": body,
            "actions": actions,
        }
        msg_id = await self._connector.send_card(card)
        if msg_id is None:
            text = pending.question or "Please choose:"
            notice = "_(Please continue in the cubebox web UI.)_"
            await self._connector.send_message(f"{text}\n\n{notice}")

    async def dispatch_finalize(self, state: Any) -> bool:
        s = self._state
        full_content = s.card_state.streaming_content
        if s.card_state.error:
            error_suffix = f"\n\n⚠️ {s.card_state.error}"
            full_content = (
                (full_content + error_suffix) if full_content else error_suffix
            )
        artifacts = s.card_state.artifacts
        if artifacts:
            links = "\n".join(
                f"📎 [{a.name}]({a.share_url})"
                for a in artifacts
                if a.share_url
            )
            if links:
                full_content = (
                    f"{full_content}\n\n{links}" if full_content else links
                )
        if not full_content:
            return True
        remaining = full_content[self.sent_char_offset:]
        if s.bot_message_id is not None and len(remaining) <= _TEAMS_MSG_LIMIT:
            try:
                await self._connector.edit_message(s.bot_message_id, remaining)
            except Exception:
                logger.warning("[Teams] finalize edit failed", exc_info=True)
                await self.emergency_text(remaining[:_TEAMS_MSG_LIMIT])
        else:
            while remaining:
                chunk = remaining[:_TEAMS_MSG_LIMIT]
                remaining = remaining[_TEAMS_MSG_LIMIT:]
                if s.bot_message_id and not self.sent_char_offset:
                    try:
                        await self._connector.edit_message(
                            s.bot_message_id, chunk
                        )
                    except Exception:
                        await self._connector.send_message(chunk)
                else:
                    msg_id = await self._connector.send_message(chunk)
                    if msg_id:
                        s.bot_message_id = msg_id
                self.sent_char_offset += len(chunk)
        return True

    async def emergency_text(self, text: str) -> None:
        try:
            await self._connector.send_message(text[:_TEAMS_MSG_LIMIT])
        except Exception:
            logger.warning("[Teams] emergency text send failed", exc_info=True)

    async def aclose(self) -> None:
        pass
```

- [ ] **Step 2: Run mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/backend
uv run mypy cubebox/im/teams/renderer.py 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git add backend/cubebox/im/teams/renderer.py
git commit -m "feat(im): teams outbound renderer with adaptive card HITL buttons"
```

---

## Task 5: Interactions + Commands

**Files:**
- Create: `backend/cubebox/im/teams/interactions.py`
- Create: `backend/cubebox/im/teams/commands.py`

- [ ] **Step 1: Implement interactions.py**

Create `backend/cubebox/im/teams/interactions.py`:

```python
"""Handle Adaptive Card Action.Submit callbacks from Teams."""

from __future__ import annotations

from typing import Any

from loguru import logger


async def handle_card_action(
    *,
    data: dict[str, Any],
    run_manager: Any,
    redis_key_prefix: str,
) -> bool:
    """Parse Adaptive Card submit data and resume the paused run.

    ``data`` is the ``value`` dict from the card action, expected to
    contain an ``action`` key with the format
    ``im:{kind}:{run_id}:{short_qid}:{akey}:{value}``.

    Returns True on success, False on failure.
    """
    action_str = str(data.get("action") or "")
    if not action_str.startswith("im:"):
        return False
    parts = action_str.split(":", maxsplit=5)
    if len(parts) < 6:
        logger.warning("[Teams] malformed action_id: {}", action_str)
        return False
    _, kind, run_id, short_qid, akey, value = parts

    from cubebox.im.resume import resolve_full_question_id, resume_paused_run

    try:
        question_id = await resolve_full_question_id(run_id, short_qid)
    except Exception:
        logger.warning("[Teams] question_id resolution failed", exc_info=True)
        return False

    return await resume_paused_run(
        run_id=run_id,
        input_kind=kind,
        choice=value,
        operator_open_id="",
        question_id=question_id,
        answer_key=akey,
        run_manager=run_manager,
    )
```

- [ ] **Step 2: Implement commands.py**

Create `backend/cubebox/im/teams/commands.py`:

```python
"""Text command recognition for Teams.

Teams does not have a native slash-command registry like Slack/Discord.
Instead we recognize ``/link <email>`` (or ``link <email>``) as text
patterns in inbound messages and short-circuit before normal ingest.
"""

from __future__ import annotations

import re

_LINK_RE = re.compile(
    r"^\s*/?link\s+(\S+@\S+\.\S+)\s*$",
    re.IGNORECASE,
)


def parse_link_command(text: str) -> str | None:
    """Extract email from a /link command. Returns None if not a match."""
    m = _LINK_RE.match(text)
    return m.group(1).strip().lower() if m else None
```

- [ ] **Step 3: Run mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/backend
uv run mypy cubebox/im/teams/interactions.py cubebox/im/teams/commands.py 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/im/teams/interactions.py backend/cubebox/im/teams/commands.py
git commit -m "feat(im): teams card action handler and /link command parser"
```

---

## Task 6: TeamsAppManager — App instance lifecycle + webhook bridge

**Files:**
- Create: `backend/cubebox/im/teams/app_manager.py`

The `TeamsAppManager` manages one `microsoft_teams.apps.App` instance per
enabled Teams account. Unlike Slack/Discord gateways, there is no persistent
WebSocket — the App instance is needed for JWT validation of inbound
webhooks and for outbound API calls (send/edit/update).

- [ ] **Step 1: Implement app_manager.py**

Create `backend/cubebox/im/teams/app_manager.py`:

```python
"""Teams App instance lifecycle manager.

Manages a cache of ``microsoft_teams.apps.App`` instances, one per
enabled Teams account. The ingress webhook route looks up the App
by bot ID to validate JWT and dispatch activities.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

_app_cache: dict[str, "TeamsAppEntry"] = {}


class TeamsAppEntry:
    """One cached App instance + metadata for a Teams account."""

    def __init__(
        self,
        *,
        app: Any,
        account_id: str,
        bot_id: str,
        secrets: dict[str, Any],
    ) -> None:
        self.app = app
        self.account_id = account_id
        self.bot_id = bot_id
        self.secrets = secrets


async def init_app(
    *,
    account_id: str,
    bot_id: str,
    secrets: dict[str, Any],
) -> TeamsAppEntry:
    """Create and cache an App instance for one Teams account.

    ``secrets`` must contain ``app_id``, ``app_secret``, ``tenant_id``.
    """
    from microsoft_teams.apps import App

    app_id = str(secrets["app_id"])
    app_secret = str(secrets["app_secret"])
    tenant_id = str(secrets["tenant_id"])

    app = App(
        app_id=app_id,
        app_password=app_secret,
        tenant_id=tenant_id,
    )
    await app.initialize()

    entry = TeamsAppEntry(
        app=app,
        account_id=account_id,
        bot_id=bot_id,
        secrets=secrets,
    )
    _app_cache[bot_id] = entry
    logger.info("[Teams] app initialized for account={} bot_id={}", account_id, bot_id)
    return entry


def get_entry_by_bot_id(bot_id: str) -> TeamsAppEntry | None:
    """Look up a cached App entry by the bot's App ID."""
    return _app_cache.get(bot_id)


def remove_app(bot_id: str) -> None:
    """Remove a cached App entry."""
    entry = _app_cache.pop(bot_id, None)
    if entry:
        logger.info(
            "[Teams] app removed for account={} bot_id={}",
            entry.account_id,
            entry.bot_id,
        )


def all_entries() -> list[TeamsAppEntry]:
    """Return all cached App entries."""
    return list(_app_cache.values())
```

- [ ] **Step 2: Run mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/backend
uv run mypy cubebox/im/teams/app_manager.py 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git add backend/cubebox/im/teams/app_manager.py
git commit -m "feat(im): teams app instance manager for webhook delivery"
```

---

## Task 7: TeamsPlatform + registration

**Files:**
- Create: `backend/cubebox/im/teams/_platform.py`
- Modify: `backend/cubebox/im/teams/__init__.py`

- [ ] **Step 1: Implement _platform.py**

Create `backend/cubebox/im/teams/_platform.py`:

```python
"""Teams platform connector — webhook delivery mode.

Implements the 4-method ``PlatformConnector`` protocol.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger


class TeamsPlatform:
    """PlatformConnector implementation for Microsoft Teams."""

    def parse_inbound(self, raw: dict[str, Any]) -> Any:
        from cubebox.im.teams.connector import TeamsConnector

        connector = TeamsConnector()
        return connector.parse_inbound(raw)

    async def build_tailer(
        self,
        *,
        run_id: str,
        queue_item: Any,
        account: Any,
        **kwargs: Any,
    ) -> Any:
        from cubebox.im.outbound import OutboundRunTailer
        from cubebox.im.teams.app_manager import get_entry_by_bot_id
        from cubebox.im.teams.connector import TeamsConnector
        from cubebox.im.teams.graph import TeamsGraphClient
        from cubebox.im.teams.renderer import TeamsOpDispatcher
        from cubebox.im.types import RenderState

        app_ref = kwargs.get("app")
        redis = app_ref.state.redis if app_ref else kwargs.get("redis")
        key_prefix = (
            app_ref.state.redis_key_prefix if app_ref else kwargs.get("key_prefix", "")
        )

        load_secrets = kwargs["load_secrets"]
        secrets = await load_secrets(account)

        bot_id = str(secrets.get("app_id") or account.external_account_id)
        entry = get_entry_by_bot_id(bot_id)
        sdk_app = entry.app if entry else None

        graph_client = TeamsGraphClient(
            app_id=str(secrets.get("app_id", "")),
            app_secret=str(secrets.get("app_secret", "")),
            tenant_id=str(secrets.get("tenant_id", "")),
        )

        channel_id = queue_item.channel_id
        reply_to_id = queue_item.reply_to_id

        connector = TeamsConnector(
            bot_id=bot_id,
            app=sdk_app,
            channel_id=channel_id,
            reply_to_id=reply_to_id,
            graph_client=graph_client,
        )

        bot_name = (account.config or {}).get("bot_app_name") or "cubebox"
        state = RenderState(
            bot_name=bot_name,
            run_id=run_id,
            stream_interval=1.5,
            reply_to_id=reply_to_id,
            inbound_message_id=queue_item.inbound_message_id,
        )

        dispatcher = TeamsOpDispatcher(connector=connector, state=state)

        sender_open_id = queue_item.sender_open_id or queue_item.sender_im_user_id
        tailer = OutboundRunTailer(
            redis=redis,
            key_prefix=key_prefix,
            run_id=run_id,
            connector=connector,
            state=state,
            dispatcher=dispatcher,
            responder_open_id=sender_open_id,
        )
        asyncio.create_task(tailer.run(), name=f"im-tailer:{run_id}")

    async def on_account_enabled(self, account: Any, **kwargs: Any) -> None:
        """Initialize the App instance so the webhook ingress can dispatch."""
        from cubebox.im.teams.app_manager import init_app

        secrets: dict[str, Any] = kwargs["secrets"]
        bot_id = str(secrets.get("app_id") or account.external_account_id)

        try:
            await init_app(
                account_id=account.id,
                bot_id=bot_id,
                secrets=secrets,
            )
        except Exception:
            logger.exception(
                "[Teams] app init failed for account {} bot_id={}",
                account.id,
                bot_id,
            )

    async def on_account_disabled(self, account: Any, **kwargs: Any) -> None:
        """Remove the App instance from cache."""
        from cubebox.im.teams.app_manager import remove_app

        load_secrets = kwargs.get("load_secrets")
        if load_secrets:
            try:
                secrets = await load_secrets(account)
                bot_id = str(secrets.get("app_id") or account.external_account_id)
            except Exception:
                bot_id = account.external_account_id
        else:
            bot_id = account.external_account_id
        remove_app(bot_id)
```

- [ ] **Step 2: Update __init__.py with registration**

Replace `backend/cubebox/im/teams/__init__.py`:

```python
"""Microsoft Teams IM connector."""

from cubebox.im.registry import register_platform
from cubebox.im.teams._platform import TeamsPlatform

register_platform("teams", TeamsPlatform())
```

- [ ] **Step 3: Run mypy on the full teams package**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/backend
uv run mypy cubebox/im/teams/ 2>&1 | tail -10
```

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/im/teams/_platform.py backend/cubebox/im/teams/__init__.py
git commit -m "feat(im): teams platform connector + registration"
```

---

## Task 8: Connect flow — Schema + Service + API route

**Files:**
- Modify: `backend/cubebox/api/schemas/im_connector.py`
- Modify: `backend/cubebox/services/im_connector.py`
- Modify: `backend/cubebox/api/routes/v1/ws_im.py`

- [ ] **Step 1: Add ConnectTeamsAccountIn schema**

In `backend/cubebox/api/schemas/im_connector.py`, add after `ConnectSlackAccountIn`:

```python
class ConnectTeamsAccountIn(BaseModel):
    """Payload for ``POST /ws/{ws}/im/accounts`` when ``platform == 'teams'``."""

    platform: Literal["teams"] = "teams"
    app_id: str = Field(min_length=1, max_length=128)
    app_secret: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1, max_length=128)
    acting_user_id: str = Field(default="self", min_length=1)
```

Update the discriminated union:

```python
ConnectIMAccountIn = Annotated[
    Annotated[ConnectFeishuAccountIn, Tag("feishu")]
    | Annotated[ConnectDiscordAccountIn, Tag("discord")]
    | Annotated[ConnectSlackAccountIn, Tag("slack")]
    | Annotated[ConnectTeamsAccountIn, Tag("teams")],
    Discriminator("platform"),
]
```

- [ ] **Step 2: Add connect_teams to IMConnectorService**

In `backend/cubebox/services/im_connector.py`, add after `connect_slack`:

```python
    async def connect_teams(
        self,
        *,
        workspace_id: str,
        app_id: str,
        app_secret: str,
        tenant_id: str,
        acting_user_id: str,
    ) -> IMConnectorAccount:
        """Bind one Teams bot: validate credentials, store credential, return account."""
        existing = (
            await self._session.execute(
                select(IMConnectorAccount).where(
                    IMConnectorAccount.org_id == self._org_id,  # type: ignore[arg-type]
                    IMConnectorAccount.platform == "teams",  # type: ignore[arg-type]
                    IMConnectorAccount.external_account_id == app_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError(
                f"teams account already exists for app_id={app_id} (id={existing.id})"
            )

        bot_name = await self._hydrate_teams_bot_info(app_id, app_secret, tenant_id)

        secret_payload = json.dumps(
            {
                "app_id": app_id,
                "app_secret": app_secret,
                "tenant_id": tenant_id,
                "bot_open_id": app_id,
            }
        )
        try:
            credential_id = await self._credentials.create(
                kind="im_bot",
                name=f"teams:{app_id}",
                plaintext=secret_payload,
            )
        except IntegrityError as exc:
            await self._session.rollback()
            raise ValueError(
                f"teams account already exists for app_id={app_id} (credential race)"
            ) from exc
        try:
            account = IMConnectorAccount(
                org_id=self._org_id,
                workspace_id=workspace_id,
                platform="teams",
                external_account_id=app_id,
                acting_user_id=acting_user_id,
                credential_id=credential_id,
                delivery_mode="webhook",
                config={
                    "bot_app_name": bot_name or None,
                    "bot_avatar_url": None,
                    "tenant_id": tenant_id,
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

    async def _hydrate_teams_bot_info(
        self,
        app_id: str,
        app_secret: str,
        tenant_id: str,
    ) -> str:
        """Validate Teams bot credentials via OAuth2 token request.

        Returns the bot display name (app_id as fallback). Raises ValueError
        on invalid credentials.
        """
        import httpx

        token_url = (
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        )
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": app_id,
                        "client_secret": app_secret,
                        "scope": "https://api.botframework.com/.default",
                    },
                    timeout=10.0,
                )
                if resp.status_code != 200:
                    raise ValueError(
                        f"Teams credential validation failed (HTTP {resp.status_code}): "
                        f"{resp.text[:200]}"
                    )
                data = resp.json()
                if "access_token" not in data:
                    raise ValueError("Teams credential validation failed: no access_token")
                return app_id
        except ValueError:
            raise
        except Exception:
            logger.exception("[IM] Teams credential validation failed")
            raise ValueError("could not validate Teams bot credentials") from None
```

- [ ] **Step 3: Add _connect_teams route handler**

In `backend/cubebox/api/routes/v1/ws_im.py`:

Add `ConnectTeamsAccountIn` to the imports:

```python
from cubebox.api.schemas.im_connector import (
    ConnectDiscordAccountIn,
    ConnectFeishuAccountIn,
    ConnectIMAccountIn,
    ConnectSlackAccountIn,
    ConnectTeamsAccountIn,  # new
    ...
)
```

Add the `_connect_teams` function after `_connect_slack`:

```python
async def _connect_teams(
    body: ConnectTeamsAccountIn,
    request: Request,
    ctx: RequestContext,
    session: AsyncSession,
    backend: EncryptionBackend,
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    acting = await _resolve_acting_user(body.acting_user_id, ctx, session)
    try:
        account = await svc.connect_teams(
            workspace_id=ctx.workspace_id,
            app_id=body.app_id,
            app_secret=body.app_secret,
            tenant_id=body.tenant_id,
            acting_user_id=acting,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    # Initialize the App instance for webhook handling
    starter = getattr(request.app.state, "im_connect_account", None)
    if starter is not None and account.enabled:
        try:
            await starter(account)
        except Exception:
            logger.warning(
                "[IM ws] teams app init failed for {}", account.id, exc_info=True
            )
    return _to_out(account)
```

Add the elif branch in `connect_account`:

```python
    elif isinstance(body, ConnectSlackAccountIn):
        return await _connect_slack(body, request, ctx, session, backend)
    elif isinstance(body, ConnectTeamsAccountIn):
        return await _connect_teams(body, request, ctx, session, backend)
    else:
```

- [ ] **Step 4: Run mypy on changed files**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/backend
uv run mypy cubebox/api/schemas/im_connector.py cubebox/services/im_connector.py \
  cubebox/api/routes/v1/ws_im.py 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/api/schemas/im_connector.py \
  backend/cubebox/services/im_connector.py \
  backend/cubebox/api/routes/v1/ws_im.py
git commit -m "feat(im): teams connect flow — schema, service, API route"
```

---

## Task 9: Webhook ingress + runtime integration

**Files:**
- Modify: `backend/cubebox/api/routes/v1/im_ingress.py`
- Modify: `backend/cubebox/im/runtime.py`

- [ ] **Step 1: Add Teams webhook ingress route**

In `backend/cubebox/api/routes/v1/im_ingress.py`, add the Teams route
after the Feishu route. Add necessary imports at the top:

```python
from cubebox.im.teams.app_manager import get_entry_by_bot_id
from cubebox.im.teams.commands import parse_link_command as parse_teams_link
from cubebox.im.teams.connector import TeamsConnector
from cubebox.im.teams.graph import TeamsGraphClient
```

Add the route function:

```python
@router.post("/teams/messages")
async def teams_messages(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> Response:
    """Receive one Teams Bot Framework activity via webhook.

    Azure Bot Service POSTs activities here. The SDK App instance
    validates the JWT Bearer token. We parse the activity and ingest.
    """
    raw_body = await request.body()
    try:
        activity: dict[str, Any] = json.loads(raw_body or b"{}")
    except json.JSONDecodeError:
        return Response(status_code=status.HTTP_400_BAD_REQUEST)
    if not isinstance(activity, dict):
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    # Route to the correct App instance by recipient (bot) ID
    recipient = activity.get("recipient") or {}
    bot_id = str(recipient.get("id") or "")
    if not bot_id:
        return Response(status_code=status.HTTP_200_OK)

    entry = get_entry_by_bot_id(bot_id)
    if entry is None:
        logger.debug("[Teams ingress] no app for bot_id={}", bot_id)
        return Response(status_code=status.HTTP_200_OK)

    # Look up the account
    account = await get_account_by_external_id_unscoped(
        session, platform="teams", external_account_id=bot_id
    )
    if account is None or not account.enabled:
        return Response(status_code=status.HTTP_200_OK)

    activity_type = str(activity.get("type") or "")

    # Handle Adaptive Card submit actions
    if activity_type == "invoke":
        value = activity.get("value") or {}
        action_data = value.get("action")
        if isinstance(action_data, str) and action_data.startswith("im:"):
            from cubebox.im.teams.interactions import handle_card_action

            ok = await handle_card_action(
                data={"action": action_data},
                run_manager=request.app.state.run_manager,
                redis_key_prefix=request.app.state.redis_key_prefix,
            )
            status_body = {"status": 200} if ok else {"status": 200}
            return Response(
                content=json.dumps(status_body),
                media_type="application/json",
            )

    # Parse message activities
    if activity_type != "message":
        return Response(status_code=status.HTTP_200_OK)

    connector = TeamsConnector(bot_id=bot_id)
    event = connector.parse_inbound(activity)
    if event is None:
        return Response(status_code=status.HTTP_200_OK)

    event.account_external_id = account.external_account_id

    # Build a connector with outbound capability for identity gating
    graph_client = TeamsGraphClient(
        app_id=str(entry.secrets.get("app_id", "")),
        app_secret=str(entry.secrets.get("app_secret", "")),
        tenant_id=str(entry.secrets.get("tenant_id", "")),
    )
    gate_connector = TeamsConnector(
        bot_id=bot_id,
        app=entry.app,
        channel_id=event.channel_id,
        graph_client=graph_client,
    )

    # Intercept /link commands
    link_email = parse_teams_link(event.text)
    if link_email is not None:
        await _handle_teams_link_command(
            email=link_email,
            event=event,
            account=account,
            connector=gate_connector,
        )
        return Response(status_code=status.HTTP_200_OK)

    maker: async_sessionmaker[AsyncSession] = async_session_maker
    result = await ingest_inbound_event(
        event,
        account=account,
        session_maker=maker,
        identity_resolver=gate_connector,
        rejection_notifier=gate_connector,
    )
    logger.info(
        "[Teams ingress] {} {}: {}",
        account.id,
        event.platform_event_id,
        result.outcome,
    )
    return Response(status_code=status.HTTP_200_OK)


async def _handle_teams_link_command(
    *,
    email: str,
    event: Any,
    account: IMConnectorAccount,
    connector: Any,
) -> None:
    """Generate an identity-link token and reply to the Teams chat."""
    from cubebox.config import config
    from cubebox.im.link import sign_link_token

    secret = str(config.get("auth.jwt_secret", "CHANGE_ME"))
    sender_ref = event.sender_ref or event.sender_open_id or ""
    if not sender_ref:
        if connector is not None:
            await connector.send_to_chat(event.channel_id, None, "Cannot identify sender.")
        return

    try:
        token = sign_link_token(
            im_user_id=sender_ref,
            email=email,
            account_id=account.id,
            workspace_id=account.workspace_id,
            platform="teams",
            secret=secret,
        )
    except Exception:
        logger.warning("[Teams] sign_link_token failed", exc_info=True)
        if connector is not None:
            await connector.send_to_chat(event.channel_id, None, "Failed to generate link.")
        return

    base = str(config.get("frontend_base_url", "http://localhost:3000")).rstrip("/")
    url = f"{base}/im-link?token={token}"
    text = f"Click to confirm your identity:\n{url}"
    if connector is not None:
        await connector.send_to_chat(event.channel_id, None, text)
```

- [ ] **Step 2: Add Teams to runtime.py startup**

In `backend/cubebox/im/runtime.py`, add the import alongside the others in `start()`:

```python
    import cubebox.im.discord  # noqa: F401
    import cubebox.im.feishu  # noqa: F401
    import cubebox.im.slack  # noqa: F401
    import cubebox.im.teams  # noqa: F401
```

Also add Teams webhook App initialization. After the `asyncio.gather` block
that connects gateway/long-connection accounts, add:

```python
    # Initialize Teams webhook App instances (no persistent connection,
    # but the App instance must exist for the ingress route to dispatch).
    async with async_session_maker() as s:
        webhook_accounts = (
            (
                await s.execute(
                    select(IMConnectorAccount).where(
                        IMConnectorAccount.enabled == True,  # type: ignore[arg-type]  # noqa: E712
                        IMConnectorAccount.platform == "teams",  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
    for wa in webhook_accounts:
        try:
            secrets = await _load_secrets(wa)
            from cubebox.im.registry import get_platform as _get_platform

            platform = _get_platform(wa.platform)
            await platform.on_account_enabled(wa, secrets=secrets, gateways=gateways)
        except Exception:
            logger.warning(
                "[IM] teams app init failed for account {} on startup",
                wa.id,
                exc_info=True,
            )
```

- [ ] **Step 3: Run mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/backend
uv run mypy cubebox/api/routes/v1/im_ingress.py cubebox/im/runtime.py 2>&1 | tail -10
```

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/api/routes/v1/im_ingress.py backend/cubebox/im/runtime.py
git commit -m "feat(im): teams webhook ingress route + runtime startup init"
```

---

## Task 10: Frontend — Teams wizard + API types + i18n

**Files:**
- Create: `frontend/packages/web/components/im/ImConnectWizard/platforms/teams.ts`
- Modify: `frontend/packages/web/components/im/ImConnectWizard/platforms/index.ts`
- Modify: `frontend/packages/core/src/api/im.ts`
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`

- [ ] **Step 1: Create the full teams.ts PlatformDescriptor**

Create `frontend/packages/web/components/im/ImConnectWizard/platforms/teams.ts`
(replaces `teams.stub.ts`):

```typescript
import { StepCredentials } from '../steps/StepCredentials'
import { StepPrereqs } from '../steps/StepPrereqs'
import { StepVerify } from '../steps/StepVerify'
import type { PlatformDescriptor } from './types'

export const teamsDescriptor: PlatformDescriptor = {
  id: 'teams',
  labelKey: 'im.platform.teams.label',
  iconName: 'MessageSquare',
  live: true,
  prereqs: [
    {
      key: 'app',
      labelKey: 'im.wizard.teams.prereq.app',
      helpUrl: () => 'https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps',
    },
    {
      key: 'graphPermission',
      labelKey: 'im.wizard.teams.prereq.graphPermission',
    },
    {
      key: 'clientSecret',
      labelKey: 'im.wizard.teams.prereq.clientSecret',
    },
    {
      key: 'endpoint',
      labelKey: 'im.wizard.teams.prereq.endpoint',
    },
  ],
  credentialFields: [
    {
      key: 'app_id',
      labelKey: 'im.wizard.teams.field.appId',
      type: 'text',
      required: true,
      placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
    },
    {
      key: 'app_secret',
      labelKey: 'im.wizard.teams.field.appSecret',
      type: 'password',
      required: true,
      placeholder: '',
    },
    {
      key: 'tenant_id',
      labelKey: 'im.wizard.teams.field.tenantId',
      type: 'text',
      required: true,
      placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
    },
  ],
  steps: [
    {
      key: 'prereqs',
      labelKey: 'im.wizard.step.prereqs',
      Component: StepPrereqs,
      canAdvance: () => true,
    },
    {
      key: 'credentials',
      labelKey: 'im.wizard.step.credentials',
      Component: StepCredentials,
      canAdvance: (f) => !!(f.app_id && f.app_secret && f.tenant_id),
    },
    {
      key: 'verify',
      labelKey: 'im.wizard.step.verify',
      Component: StepVerify,
    },
  ],
  buildPayload: (f) => ({
    platform: 'teams' as const,
    app_id: f.app_id || '',
    app_secret: f.app_secret || '',
    tenant_id: f.tenant_id || '',
    acting_user_id: 'self',
  }),
  scopeConsoleUrl: () => 'https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps',
}
```

- [ ] **Step 2: Update index.ts to import from teams.ts**

In `frontend/packages/web/components/im/ImConnectWizard/platforms/index.ts`,
change the import from `./teams.stub` to `./teams`:

```typescript
export { teamsDescriptor } from './teams'
```

- [ ] **Step 3: Delete the stub file**

```bash
rm frontend/packages/web/components/im/ImConnectWizard/platforms/teams.stub.ts
```

- [ ] **Step 4: Update StepPlatform.tsx to remove "coming soon" override**

In `frontend/packages/web/components/im/ImConnectWizard/steps/StepPlatform.tsx`,
remove the line that returns "coming soon" for teams — since `live: true`
in the descriptor, the wizard will show it as a normal platform.

- [ ] **Step 5: Add ConnectTeamsAccountIn to frontend API types**

In `frontend/packages/core/src/api/im.ts`, add:

```typescript
export type ConnectTeamsAccountIn = {
  platform: 'teams'
  app_id: string
  app_secret: string
  tenant_id: string
  acting_user_id: string
}
```

Update the union:

```typescript
export type ConnectImAccountIn =
  | ConnectFeishuAccountIn
  | ConnectDiscordAccountIn
  | ConnectSlackAccountIn
  | ConnectTeamsAccountIn
```

- [ ] **Step 6: Add i18n keys to en.json**

In `frontend/packages/web/messages/en.json`, inside `"im" → "wizard"`,
add after the `"slack"` block:

```json
"teams": {
  "prereq": {
    "app": "Create a Bot Registration in Azure Portal (or via Teams Toolkit)",
    "graphPermission": "Add User.Read.All application permission and grant admin consent",
    "clientSecret": "Create a Client Secret in Certificates & Secrets",
    "endpoint": "Set Messaging Endpoint to https://<your-domain>/api/v1/im/teams/messages"
  },
  "field": {
    "appId": "Application (Client) ID",
    "appSecret": "Client Secret",
    "tenantId": "Tenant ID"
  }
}
```

Also update `"im" → "empty" → "workspace" → "comingNote"` to remove
Teams from the "coming later" text:

```json
"comingNote": "DingTalk — coming later"
```

- [ ] **Step 7: Add i18n keys to zh.json**

Same structure in Chinese.

- [ ] **Step 8: Build frontend to verify**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/frontend
pnpm build 2>&1 | tail -10
```

Expected: no TypeScript errors.

- [ ] **Step 9: Commit**

```bash
git add frontend/packages/web/components/im/ImConnectWizard/platforms/teams.ts \
  frontend/packages/web/components/im/ImConnectWizard/platforms/index.ts \
  frontend/packages/core/src/api/im.ts \
  frontend/packages/web/messages/en.json \
  frontend/packages/web/messages/zh.json \
  frontend/packages/web/components/im/ImConnectWizard/steps/StepPlatform.tsx
git rm frontend/packages/web/components/im/ImConnectWizard/platforms/teams.stub.ts
git commit -m "feat(im): frontend teams connect wizard + API types + i18n"
```

---

## Task 11: Full type-check + pre-PR sweep

**Files:** none (verification only)

- [ ] **Step 1: Full backend mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/backend
uv run mypy cubebox/ 2>&1 | tee tmp/mypy.log | tail -5
```

Expected: `Success: no issues found`

- [ ] **Step 2: Full backend lint**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/backend
uv run ruff check cubebox/ 2>&1 | tail -5
```

- [ ] **Step 3: Run unit tests**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/backend
uv run pytest tests/unit/ -v --no-cov 2>&1 | tee tmp/unit.log | tail -10
```

- [ ] **Step 4: Frontend build**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/frontend
pnpm build 2>&1 | tee tmp/build.log | tail -10
```

- [ ] **Step 5: Frontend lint**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-19-teams-im-connector/frontend
pnpm lint 2>&1 | tail -5
```

- [ ] **Step 6: Fix any issues and commit**

Fix any type errors, lint warnings, or test failures. Then commit.
