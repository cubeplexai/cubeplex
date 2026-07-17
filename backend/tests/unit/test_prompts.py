from cubeplex.prompts.sandbox import SANDBOX_PROMPT_TEMPLATE
from cubeplex.prompts.skills import SKILLS_PROMPT_TEMPLATE
from cubeplex.prompts.subagents import SUBAGENT_PROMPT
from cubeplex.prompts.system import BASE_SYSTEM_PROMPT


def test_all_prompts_are_non_empty_strings():
    for prompt in [
        BASE_SYSTEM_PROMPT,
        SANDBOX_PROMPT_TEMPLATE,
        SUBAGENT_PROMPT,
        SKILLS_PROMPT_TEMPLATE,
    ]:
        assert isinstance(prompt, str)
        assert len(prompt.strip()) > 50


def test_template_prompts_have_expected_placeholders():
    """Template prompts must contain their expected placeholders."""
    assert "{workdir}" in SANDBOX_PROMPT_TEMPLATE
    assert "{skills_list}" in SKILLS_PROMPT_TEMPLATE


def test_non_template_prompts_have_no_placeholders():
    """Prompts used directly (not as templates) must have no unformatted {} placeholders."""
    for prompt in [BASE_SYSTEM_PROMPT, SUBAGENT_PROMPT]:
        assert "{" not in prompt


def test_system_prompt_mentions_tools():
    assert "tool" in BASE_SYSTEM_PROMPT.lower()


def test_sandbox_prompt_mentions_execute():
    rendered = SANDBOX_PROMPT_TEMPLATE.format(workdir="/root")
    assert "execute" in rendered.lower()


def test_sandbox_prompt_includes_workdir():
    rendered = SANDBOX_PROMPT_TEMPLATE.format(workdir="/my/workdir")
    assert "/my/workdir" in rendered
