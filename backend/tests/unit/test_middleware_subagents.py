from cubebox.middleware.subagents import SubAgent, SubAgentMiddleware


def test_subagent_middleware_registers_task_tool():
    mw = SubAgentMiddleware(subagents=[])
    tool_names = [t.name for t in mw.tools]
    assert "task" in tool_names


def test_subagent_middleware_with_no_subagents_has_task_tool():
    mw = SubAgentMiddleware(subagents=[])
    task_tool = mw.tools[0]
    assert task_tool.name == "task"


def test_subagent_spec_type():
    """SubAgent is a TypedDict with required fields."""
    agent: SubAgent = {
        "name": "test-agent",
        "description": "A test subagent",
        "system_prompt": "You are a test agent.",
    }
    assert agent["name"] == "test-agent"
