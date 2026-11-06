"""User-stop transcript marker.

Hard cancel does not persist ``stop_reason=aborted``. The chat chip reads
that field from history, so a stopped run gets an empty assistant with
``metadata.kind == "user_stop"``. Model context drops the marker: an empty
assistant would be an Anthropic prefill or an OpenAI null-content message,
and omitting it keeps the cacheable history byte-identical.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cubeloop.providers.base import AssistantMessage, Message

USER_STOP_KIND = "user_stop"
_CLOSED_STOP_REASONS = frozenset({"aborted", "error"})


def _is_user_stop_marker(message: Message) -> bool:
    return isinstance(message, AssistantMessage) and message.metadata.get("kind") == USER_STOP_KIND


def user_stop_message(run_id: str, *, now: datetime | None = None) -> AssistantMessage:
    """Empty assistant the history chip renders as Stopped."""
    stamp = now if now is not None else datetime.now(UTC)
    return AssistantMessage(
        content=[],
        stop_reason="aborted",
        run_id=run_id,
        metadata={"kind": USER_STOP_KIND},
        timestamp=stamp.timestamp(),
    )


def user_stop_needed(messages: list[Message], run_id: str) -> bool:
    """Whether this run still needs a user-stop marker.

    False when the run never appears in the transcript, so a fast stop
    cannot attach a chip to an older run. False when the run's last
    assistant already ended aborted or errored, or is already the marker.
    """
    saw_run = False
    last_assistant: AssistantMessage | None = None
    for message in messages:
        if message.run_id != run_id:
            continue
        saw_run = True
        if isinstance(message, AssistantMessage):
            last_assistant = message
    if not saw_run:
        return False
    if last_assistant is None:
        return True
    if _is_user_stop_marker(last_assistant):
        return False
    return last_assistant.stop_reason not in _CLOSED_STOP_REASONS


def omit_user_stop_markers(messages: list[Message]) -> list[Message]:
    """Drop user-stop markers. Return the same list when there are none."""
    if not any(_is_user_stop_marker(message) for message in messages):
        return messages
    return [message for message in messages if not _is_user_stop_marker(message)]
