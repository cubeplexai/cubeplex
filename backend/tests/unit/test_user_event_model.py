"""Unit tests for the UserEvent model."""

from cubebox.models.user_event import UserEvent, UserEventType


def test_user_event_construct() -> None:
    e = UserEvent(
        user_id="usr_abc",
        workspace_id="ws_def",
        type=UserEventType.MEMORY_UPDATED,
        payload={"items": [{"op": "save", "memory_id": "mem_x"}]},
    )
    assert e.id.startswith("uev-")
    assert e.read_at is None
    assert e.type == UserEventType.MEMORY_UPDATED
