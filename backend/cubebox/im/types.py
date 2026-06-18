"""Platform-agnostic IM transport types.

The `scope_key` contract is the load-bearing design choice: cubebox does
not interpret what's inside the string, but every connector composes it
from the same helpers so a (group × user) session in Feishu is byte-for-byte
identical to a (group × user) session in any other connector — no typos
silently forking conversations.

See docs/dev/plans/2026-06-11-im-connectors-feishu.md
("Connector-neutral session boundary") for the per-platform mapping.
"""

from dataclasses import dataclass, field
from typing import Literal

from cubebox.im.card_model import CardState

BindingMode = Literal["isolated", "shared"]

DM_SCOPE_KEY = "dm"


def make_participant_scope(sender_ref: str) -> str:
    """Group session keyed by sender (Feishu groups, WeCom, future per-user rooms).

    Centralized so every connector composes the same byte-for-byte string —
    a typo (``"u :x"`` vs ``"u:x"``) would silently fork sessions because the
    unique index ``(account_id, channel_id, scope_key)`` keys on the literal
    string.
    """
    return f"u:{sender_ref}"


def make_channel_scope() -> str:
    """Channel-shared session (Discord guild channels, future public rooms).

    All users in the same channel share one conversation. The channel_id
    column already distinguishes channels, so scope_key only differentiates
    session types within the same channel (regular vs thread).
    """
    return "ch"


def make_thread_scope(thread_id: str) -> str:
    """Thread/topic-scoped session (Slack threads, Discord threads, Telegram forum topics)."""
    return f"t:{thread_id}"


def make_thread_participant_scope(sender_ref: str, thread_id: str) -> str:
    """Combined scope: thread sub-divided per participant (rare overlay)."""
    return f"u:{sender_ref}|t:{thread_id}"


@dataclass(slots=True)
class InboundEvent:
    """Normalized inbound IM message ready for binding / scope / identity resolution.

    Field roles:
    - ``scope_key``: connector-owned session-boundary key (opaque, non-NULL).
    - ``scope_kind``: observability label for the chosen scope.
    - ``reply_to_id``: the real platform message id to reply against, or None.
    - ``inbound_message_id``: the originating user message id (for reactions).
    - ``sender_ref``: most stable sender id available (Feishu: union_id).
    - ``sender_open_id``: app-scoped id (mention gating only).
    """

    platform: str
    account_external_id: str
    platform_event_id: str
    channel_id: str
    scope_key: str
    scope_kind: str
    reply_to_id: str | None
    inbound_message_id: str
    sender_ref: str
    sender_open_id: str | None
    text: str


@dataclass(slots=True)
class RenderState:
    """Per-run outbound render state, projected into a CardKit card.

    Held by the tailer for one run from first event to terminal event.
    """

    bot_name: str
    run_id: str
    card_state: CardState = field(init=False)
    card_id: str | None = None
    card_unavailable: bool = False
    last_stream_monotonic: float = 0.0
    last_patch_monotonic: float = 0.0
    stream_interval: float = 0.1
    patch_interval: float = 1.5
    consecutive_flood_strikes: int = 0
    edits_disabled: bool = False
    reaction_in_progress_id: str | None = None
    # Bound at tailer start from IMRunQueueItem fields.
    reply_to_id: str | None = None
    # The originating user message id (NOT the bot's reply). Used by reaction
    # calls so the ⏱️ / ❌ chip attaches to the user's message.
    inbound_message_id: str | None = None
    bot_message_id: str | None = None
    """Feishu message_id of the bubble that carries the card."""
    pending_prompt_emergency_sent_qid: str | None = None
    """When patch_card fails for a pending_input event we surface the HITL
    question via emergency text so the Feishu user isn't stranded (paused
    HITL ``done`` is non-terminal — there's no finalize fallback). Tracks
    the question_id we already surfaced so a long patch-throttled HITL
    pause doesn't spam the same prompt on every event."""

    def __post_init__(self) -> None:
        self.card_state = CardState(bot_name=self.bot_name, run_id=self.run_id)
