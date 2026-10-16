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


@pytest.mark.parametrize(
    "path,value",
    [
        ("extra_body", {"model": "wrong"}),
        ("extra_body", {"max_tokens": 99999}),
        ("extra_headers.Authorization", "another-key"),
        ("temperature", 1.9),
    ],
)
def test_reasoning_paths_cannot_bypass_owned_request_fields(path, value):
    with pytest.raises(ValueError):
        validate_request_extensions(
            "openai-completions",
            {},
            {
                "reasoning": {
                    "mode_payloads": {"on": {}},
                    "effort_path": path,
                    "effort_values": {"high": value},
                }
            },
        )
