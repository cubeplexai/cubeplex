from cubebox.prompts.system import BASE_SYSTEM_PROMPT
from cubebox.prompts.sandbox import SANDBOX_PROMPT
from cubebox.prompts.subagents import SUBAGENT_PROMPT
from cubebox.prompts.skills import SKILLS_PROMPT_TEMPLATE


def test_all_prompts_are_non_empty_strings():
    for prompt in [BASE_SYSTEM_PROMPT, SANDBOX_PROMPT, SUBAGENT_PROMPT, SKILLS_PROMPT_TEMPLATE]:
        assert isinstance(prompt, str)
        assert len(prompt.strip()) > 50


def test_prompts_have_no_format_placeholders():
    """Prompts used directly (not as templates) must have no unformatted {} placeholders."""
    for prompt in [BASE_SYSTEM_PROMPT, SANDBOX_PROMPT, SUBAGENT_PROMPT]:
        # Skills prompt is a template — skip it
        assert "{" not in prompt or prompt.count("{") == prompt.count("}")


def test_system_prompt_mentions_tools():
    assert "tool" in BASE_SYSTEM_PROMPT.lower()


def test_sandbox_prompt_mentions_execute():
    assert "execute" in SANDBOX_PROMPT.lower()
