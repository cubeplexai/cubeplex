from cubebox.middleware.skills import SkillsMiddleware, SkillSpec


def test_skills_middleware_has_no_tools():
    """Skills are exposed via prompt, not as tools."""
    mw = SkillsMiddleware(skills=[])
    assert mw.tools == []


def test_skills_middleware_with_empty_skills_passes_through():
    """With no skills, middleware should pass through without modifying system message."""
    from unittest.mock import AsyncMock, MagicMock

    from langchain.agents.middleware.types import ModelRequest, ModelResponse

    mw = SkillsMiddleware(skills=[])
    request = MagicMock(spec=ModelRequest)

    response = MagicMock(spec=ModelResponse)
    handler = AsyncMock(return_value=response)

    import asyncio

    asyncio.get_event_loop().run_until_complete(mw.awrap_model_call(request, handler))
    # With no skills, handler should be called with original request
    handler.assert_called_once_with(request)


def test_skills_middleware_lists_skills_in_prompt():
    from unittest.mock import MagicMock

    from langchain.agents.middleware.types import ModelRequest, ModelResponse

    skills = [
        SkillSpec(name="git-commit", description="Create well-formatted git commits"),
        SkillSpec(name="code-review", description="Review code for issues"),
    ]
    mw = SkillsMiddleware(skills=skills)

    request = MagicMock(spec=ModelRequest)
    request.system_message = None

    captured_requests = []

    async def capture_handler(req):
        captured_requests.append(req)
        return MagicMock(spec=ModelResponse)

    import asyncio

    asyncio.get_event_loop().run_until_complete(mw.awrap_model_call(request, capture_handler))

    # The request passed to handler should have an overridden system_message
    assert len(captured_requests) == 1
    call_args = request.override.call_args
    system_msg = call_args.kwargs.get("system_message")
    assert system_msg is not None
    content = system_msg.content if hasattr(system_msg, "content") else str(system_msg)
    assert "git-commit" in str(content)
    assert "code-review" in str(content)
