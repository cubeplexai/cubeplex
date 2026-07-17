from cubeplex.agents.schemas import InjectedMessageEvent
from cubeplex.streams.run_manager import cubepi_dict_to_agent_event


def test_injected_message_dict_becomes_typed_event():
    evt = cubepi_dict_to_agent_event(
        {"type": "injected_message", "content": "do X", "steer_id": "s1"},
        "2026-05-25T00:00:00+00:00",
    )
    assert isinstance(evt, InjectedMessageEvent)
    assert evt.data == {"content": "do X", "steer_id": "s1"}


def test_injected_message_dict_forwards_group_chat_sender():
    evt = cubepi_dict_to_agent_event(
        {
            "type": "injected_message",
            "content": "do X",
            "steer_id": "s1",
            "sender_user_id": "user_abc",
            "sender_display_name": "Alice",
        },
        "2026-05-25T00:00:00+00:00",
    )
    assert isinstance(evt, InjectedMessageEvent)
    assert evt.data == {
        "content": "do X",
        "steer_id": "s1",
        "sender_user_id": "user_abc",
        "sender_display_name": "Alice",
    }
