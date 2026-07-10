"""Platform-agnostic IM transport types.

The `scope_key` contract is the load-bearing design choice: cubebox does
not interpret what's inside the string, but every connector composes it
from the same helpers so a (group × user) session in Feishu is byte-for-byte
identical to a (group × user) session in any other connector — no typos
silently forking conversations.

See docs/dev/plans/2026-06-11-im-connectors-feishu.md
("Connector-neutral session boundary") for the per-platform mapping.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from cubebox.im.card_model import CardState


@runtime_checkable
class OutboundConnector(Protocol):
    """The bound, per-run connector surface the artifact dispatcher calls.

    Distinct from the stateless ``registry.PlatformConnector``: these methods
    run against a connector instance already bound to a chat (channel + reply
    target). ``send_file`` is the explicit member that makes a missing
    implementation a type error at the dispatcher rather than a runtime
    ``AttributeError``. ``upload_image`` is included because the image path
    calls it; connectors without an inline-image API return ``None`` so the
    dispatcher falls back to a share-link.
    """

    async def send_file(self, *, local_path: str, filename: str, mime: str | None) -> bool: ...

    async def upload_image(self, local_path: str) -> str | None: ...

    async def send_to_chat(
        self, chat_id: str, reply_to_id: str | None, text: str
    ) -> str | None: ...


BindingMode = Literal["isolated", "shared"]

DM_SCOPE_KEY = "dm"


async def lookup_binding_mode(
    session_maker: Any,
    account_id: str,
    channel_id: str,
) -> BindingMode:
    """The bot's routing mode, read from account-level ``IMBotSettings``.

    Routing is uniform per bot (not per channel), so ``channel_id`` is
    ignored — kept on the signature for caller compatibility. Returns
    ``'isolated'`` if the account is missing.
    """
    del channel_id  # routing is account-level, not per-channel
    from sqlmodel import col, select

    from cubebox.im.bot_settings import load_bot_settings
    from cubebox.models.im_connector import IMConnectorAccount

    async with session_maker() as session:
        account = (
            await session.execute(
                select(IMConnectorAccount).where(col(IMConnectorAccount.id) == account_id)
            )
        ).scalar_one_or_none()
    if account is None:
        return "isolated"
    return load_bot_settings(account.config).routing_mode


async def is_shared_mode_for_tailer(
    session_maker: Any,
    account_id: str,
    channel_id: str,
    conversation_id: str,
) -> bool:
    """Whether the tailer should treat this run as a shared/group conversation.

    Read it from the RESOLVED conversation's ``is_group_chat`` rather than the
    account routing flag: ``resolve_im_conversation`` already encodes the real
    decision there, including that a DM on a shared-routing bot is NOT shared.
    Deriving it from account routing alone would make the tailer skip
    ``maybe_register_awaiting_responder`` for such a DM, so the sender's own
    HITL/ask-user card clicks would later be rejected. Falls back to the
    account routing flag only if the conversation can't be loaded.
    """
    del channel_id  # superseded by the conversation's resolved is_group_chat
    from sqlmodel import col, select

    from cubebox.models.conversation import Conversation

    async with session_maker() as session:
        conv = (
            await session.execute(
                select(Conversation).where(col(Conversation.id) == conversation_id)
            )
        ).scalar_one_or_none()
    if conv is not None:
        return bool(conv.is_group_chat)
    return (await lookup_binding_mode(session_maker, account_id, "")) == "shared"


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
class InboundAttachmentRef:
    """A platform file handle parsed inbound, resolved to bytes later by the worker.

    Connector-opaque: the same platform that produced ``handle`` resolves it.
    ``handle`` is the resource id ONLY (e.g. Feishu ``file_key``); the message id
    needed by some download APIs is read from the queue row at resolve time, not
    encoded here (encoding it would duplicate a field that can drift).
    """

    kind: str  # "image" | "file" | "audio" | "video" — observability + Feishu type
    filename: str
    mime: str | None
    handle: str
    size_hint: int | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "InboundAttachmentRef":
        return cls(
            kind=str(d.get("kind") or "file"),
            filename=str(d.get("filename") or "file"),
            mime=d.get("mime"),
            handle=str(d.get("handle") or ""),
            size_hint=d.get("size_hint"),
        )


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
    - ``attachments``: parsed file refs (empty for text-only / connectors that
      don't parse files), resolved to attachment ids by the worker.
    - ``channel_name``: human-readable group/channel title when the platform
      supplies it (DingTalk ``conversationTitle``) or after a lazy API lookup
      (Feishu ``im.v1.chats.get``). Used as the Topic title for group chats;
      DMs leave this None.
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
    attachments: list[InboundAttachmentRef] = field(default_factory=list)
    channel_name: str | None = None


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
