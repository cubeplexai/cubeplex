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

DM_SCOPE_KEY = "dm"


def make_participant_scope(sender_ref: str) -> str:
    """Group session keyed by sender (Feishu groups, WeCom, future per-user rooms).

    Centralized so every connector composes the same byte-for-byte string —
    a typo (``"u :x"`` vs ``"u:x"``) would silently fork sessions because the
    unique index ``(account_id, channel_id, scope_key)`` keys on the literal
    string.
    """
    return f"u:{sender_ref}"


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
    """Per-run outbound render state.

    Held by the tailer for one run from first text_delta to terminal event.
    """

    message_id: str | None = None
    text_buffer: str = ""
    tool_lines: list[str] = field(default_factory=list)
    last_edit_monotonic: float = 0.0
    edit_interval: float = 0.8  # adaptive: doubles on flood up to 10s
    consecutive_flood_strikes: int = 0
    edits_disabled: bool = False
    reaction_in_progress_id: str | None = None
    posted_artifacts: set[str] = field(default_factory=set)
    # Bound at tailer start from IMRunQueueItem fields.
    reply_to_id: str | None = None
    # The originating user message id (NOT the bot's reply). Used by reaction
    # calls so the ⏱️ / ❌ chip attaches to the user's message.
    inbound_message_id: str | None = None
