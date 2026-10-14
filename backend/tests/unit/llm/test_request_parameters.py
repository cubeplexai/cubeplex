import pytest

from cubeplex.llm.request_parameters import validate_request_extensions


@pytest.mark.parametrize(
    "field",
    [
        "model",
        "messages",
        "input",
        "tools",
        "tool_choice",
        "stream",
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
    ],
)
def test_extensions_cannot_replace_call_identity_or_probe_budget(field):
    with pytest.raises(ValueError, match="reserved"):
        validate_request_extensions("openai-completions", {field: "override"}, {})


def test_capability_patch_cannot_replace_invocation_id():
    with pytest.raises(ValueError, match="reserved"):
        validate_request_extensions(
            "openai-completions",
            {},
            {"reasoning": {"mode_payloads": {"on": {"extra_body": {"model": "wrong"}}}}},
        )
