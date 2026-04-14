from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from cubebox.middleware.citations.middleware import CitationMiddleware
from cubebox.middleware.sandbox import SandboxMiddleware
from cubebox.middleware.subagents import SubAgent, SubAgentMiddleware
from cubebox.sandbox.local import LocalSandbox


def test_subagent_middleware_registers_subagent_tool():
    mw = SubAgentMiddleware(subagents=[])
    tool_names = [t.name for t in mw.tools]
    assert "subagent" in tool_names


def test_subagent_middleware_with_no_subagents_has_subagent_tool():
    mw = SubAgentMiddleware(subagents=[])
    subagent_tool = mw.tools[0]
    assert subagent_tool.name == "subagent"


def test_subagent_spec_type():
    """SubAgent is a TypedDict with required fields."""
    agent: SubAgent = {
        "name": "test-agent",
        "description": "A test subagent",
        "system_prompt": "You are a test agent.",
    }
    assert agent["name"] == "test-agent"


@pytest.mark.asyncio
async def test_subagent_inherits_parent_middleware(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class _FakeAgent:
        async def ainvoke(self, _input):
            return {"messages": [AIMessage(content="subagent result")]}

    def _fake_create_agent(*, model, tools, system_prompt, middleware):
        captured["model"] = model
        captured["tools"] = tools
        captured["system_prompt"] = system_prompt
        captured["middleware"] = middleware
        return _FakeAgent()

    monkeypatch.setattr("cubebox.middleware.subagents.create_agent", _fake_create_agent)

    sandbox = LocalSandbox(workdir=str(tmp_path))
    inherited = [
        CitationMiddleware(citation_configs={}),
        SandboxMiddleware(sandbox=sandbox),
    ]
    mw = SubAgentMiddleware(
        subagents=[],
        default_model=MagicMock(),
        inherited_middleware=inherited,
    )

    result = await mw.tools[0].ainvoke(
        {
            "type": "tool_call",
            "name": "subagent",
            "id": "call-1",
            "args": {
                "name": "Scout",
                "role": "Researcher",
                "task": "Investigate the topic",
                "prompt": "Research the topic and return facts.",
            },
        }
    )

    assert isinstance(result, ToolMessage)
    assert captured["middleware"] == inherited


@pytest.mark.asyncio
async def test_subagent_appends_spec_middleware_after_inherited(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class _FakeAgent:
        async def ainvoke(self, _input):
            return {"messages": [AIMessage(content="subagent result")]}

    def _fake_create_agent(*, model, tools, system_prompt, middleware):
        captured["middleware"] = middleware
        return _FakeAgent()

    monkeypatch.setattr("cubebox.middleware.subagents.create_agent", _fake_create_agent)

    sandbox = LocalSandbox(workdir=str(tmp_path))
    inherited = [
        CitationMiddleware(citation_configs={}),
        SandboxMiddleware(sandbox=sandbox),
    ]
    extra_middleware = object()
    mw = SubAgentMiddleware(
        subagents=[
            {
                "name": "specialized",
                "description": "A specialized subagent",
                "system_prompt": "You are specialized.",
                "middleware": [extra_middleware],
            }
        ],
        default_model=MagicMock(),
        inherited_middleware=inherited,
    )

    await mw.tools[0].ainvoke(
        {
            "type": "tool_call",
            "name": "subagent",
            "id": "call-1",
            "args": {
                "name": "Scout",
                "role": "Researcher",
                "task": "Investigate the topic",
                "prompt": "Research the topic and return facts.",
                "subagent_type": "specialized",
            },
        }
    )

    assert captured["middleware"] == [*inherited, extra_middleware]
