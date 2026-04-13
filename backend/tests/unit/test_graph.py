import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from cubebox.agents.graph import create_cubebox_agent
from cubebox.sandbox.local import LocalSandbox


def _make_mock_llm(response_text: str = "hello") -> MagicMock:
    """Build a mock LLM that returns a simple AIMessage with no tool calls."""
    llm = MagicMock()
    llm.bind_tools = MagicMock(return_value=llm)
    # Return a fresh AIMessage each call to avoid LangGraph deduplicating by message id
    llm.invoke = MagicMock(side_effect=lambda *a, **kw: AIMessage(content=response_text))
    llm.ainvoke = AsyncMock(side_effect=lambda *a, **kw: AIMessage(content=response_text))
    return llm


def test_create_agent_returns_compiled_graph():
    from langgraph.graph.state import CompiledStateGraph

    llm = _make_mock_llm()
    agent = create_cubebox_agent(llm=llm, tools=[])
    assert isinstance(agent, CompiledStateGraph)


def test_create_agent_with_sandbox():
    sandbox = LocalSandbox()
    llm = _make_mock_llm()
    agent = create_cubebox_agent(llm=llm, tools=[], sandbox=sandbox)
    assert agent is not None


def test_create_agent_with_checkpointer():
    llm = _make_mock_llm()
    checkpointer = MemorySaver()
    agent = create_cubebox_agent(llm=llm, tools=[], checkpointer=checkpointer)
    assert agent is not None


@pytest.mark.asyncio
async def test_agent_responds_to_message():
    llm = _make_mock_llm("I can help with that.")
    checkpointer = MemorySaver()
    agent = create_cubebox_agent(llm=llm, tools=[], checkpointer=checkpointer)

    config = {"configurable": {"thread_id": "test-thread"}}
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content="Hello")]},
        config=config,
    )
    messages = result["messages"]
    assert any(isinstance(m, AIMessage) for m in messages)


@pytest.mark.asyncio
async def test_agent_persists_across_invocations():
    llm = _make_mock_llm("Remembered.")
    checkpointer = MemorySaver()
    agent = create_cubebox_agent(llm=llm, tools=[], checkpointer=checkpointer)

    config = {"configurable": {"thread_id": "persist-thread"}}
    await agent.ainvoke({"messages": [HumanMessage(content="First")]}, config=config)
    result = await agent.ainvoke({"messages": [HumanMessage(content="Second")]}, config=config)

    # Thread state should contain all 4 messages (2 human + 2 AI)
    assert len(result["messages"]) >= 4


@pytest.mark.asyncio
async def test_agent_persists_todos_in_checkpointer_state():
    expected_todos = [
        {"content": "Draft spec", "status": "in_progress"},
        {"content": "Review notes", "status": "completed"},
    ]
    call_count = 0

    def _next_message(*args: object, **kwargs: object) -> AIMessage:
        nonlocal call_count
        call_count += 1
        messages = args[0] if args else kwargs["input"]
        assert isinstance(messages, list)
        if call_count == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_todos",
                        "args": {"todos": expected_todos},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )

        assert any(
            isinstance(message, ToolMessage)
            and json.loads(message.content)["todos"] == expected_todos
            for message in messages
        )
        return AIMessage(content="", tool_calls=[])

    llm = MagicMock()
    llm.bind_tools = MagicMock(return_value=llm)
    llm.invoke = MagicMock(side_effect=_next_message)
    llm.ainvoke = AsyncMock(side_effect=_next_message)
    checkpointer = MemorySaver()
    agent = create_cubebox_agent(llm=llm, tools=[], checkpointer=checkpointer)

    config = {"configurable": {"thread_id": "todo-persist-thread"}}
    # Stop after the first tool step so the second invoke exercises checkpoint
    # restore without needing a synthetic terminal response from the mock model.
    await agent.ainvoke(
        {"messages": [HumanMessage(content="Plan the work")]},
        config=config,
        interrupt_after=["tools"],
    )

    await agent.ainvoke(
        {"messages": [HumanMessage(content="Continue")]},
        config=config,
    )

    state = await agent.aget_state(config)
    assert state.values["todos"] == expected_todos
