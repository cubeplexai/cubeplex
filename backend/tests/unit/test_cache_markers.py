from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from cubebox.llm.cache_markers import apply_cache_markers, detect_provider


def test_detect_provider() -> None:
    assert detect_provider("anthropic/claude-sonnet-4-6") == "anthropic"
    assert detect_provider("openai/gpt-4o") == "openai"
    assert detect_provider("vllm/some-local") == "unknown"


def test_anthropic_marks_system_and_last_assistant() -> None:
    sys_msg = SystemMessage(content="rules")
    messages: list = [
        HumanMessage(content="hi"),
        AIMessage(content="hello"),
        HumanMessage(content="next"),
    ]
    new_sys, new_msgs = apply_cache_markers(
        system_message=sys_msg, messages=messages, provider="anthropic"
    )
    assert new_sys is not None
    assert isinstance(new_sys.content, list)
    assert new_sys.content[0]["cache_control"] == {"type": "ephemeral"}
    # Last assistant marked
    last_ai = next(m for m in new_msgs if isinstance(m, AIMessage))
    assert isinstance(last_ai.content, list)
    assert last_ai.content[-1]["cache_control"] == {"type": "ephemeral"}


def test_openai_passthrough() -> None:
    sys_msg = SystemMessage(content="rules")
    messages: list = [HumanMessage(content="hi"), AIMessage(content="hello")]
    new_sys, new_msgs = apply_cache_markers(
        system_message=sys_msg, messages=messages, provider="openai"
    )
    assert new_sys is sys_msg
    assert new_msgs is messages or new_msgs == messages
