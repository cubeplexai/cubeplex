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
from typing import Any, Literal

from cubebox.im.card_model import CardState

BindingMode = Literal["isolated", "shared"]

DM_SCOPE_KEY = "dm"


async def lookup_binding_mode(
    session_maker: Any,
    account_id: str,
    channel_id: str,
) -> BindingMode:
    """Look up the binding mode for a (account, channel) pair.

    Returns ``'isolated'`` if no binding row exists.
    """
    from sqlmodel import col, select

    from cubebox.models.im_channel_binding import IMChannelBinding

    stmt = select(IMChannelBinding).where(
        col(IMChannelBinding.account_id) == account_id,
        col(IMChannelBinding.channel_id) == channel_id,
    )
    async with session_maker() as session:
        binding = (await session.execute(stmt)).scalar_one_or_none()
    if binding is not None and binding.mode == "shared":
        return "shared"
    return "isolated"


async def is_shared_mode_for_tailer(
    session_maker: Any,
    account_id: str,
    channel_id: str,
    conversation_id: str,
) -> bool:
    """Determine shared mode for an outbound tailer.

    Primary: (account_id, channel_id) binding lookup.
    Fallback: if the conversation has a topic_id, check binding by topic_id.
    Handles Discord threads where channel_id is the thread ID, not the
    parent channel that carries the binding.
    """
    bm = await lookup_binding_mode(session_maker, account_id, channel_id)
    if bm == "shared":
        return True

    from sqlmodel import col, select

    from cubebox.models.conversation import Conversation
    from cubebox.models.im_channel_binding import IMChannelBinding

    async with session_maker() as session:
        conv = (
            await session.execute(
                select(Conversation).where(col(Conversation.id) == conversation_id)
            )
        ).scalar_one_or_none()
        if conv is not None and conv.topic_id is not None:
            binding = (
                await session.execute(
                    select(IMChannelBinding).where(
                        col(IMChannelBinding.topic_id) == conv.topic_id,
                    )
                )
            ).scalar_one_or_none()
            return binding is not None and binding.mode == "shared"
    return False


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
