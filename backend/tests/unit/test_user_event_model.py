"""Unit tests for the UserEvent model."""

from cubeplex.models.user_event import UserEvent, UserEventType


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


def test_user_event_workspace_id_optional() -> None:
    e = UserEvent(
        user_id="usr_abc",
        type=UserEventType.MEMORY_UPDATED,
        payload={},
    )
    assert e.workspace_id is None
