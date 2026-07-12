import pytest

from cubeplex.seeders.provider_seeder import _merge_cost, resolve_provider_config


def test_merge_cost_partial_override_inherits_other_legs():
    catalog = {"input": 0.27, "output": 1.10, "cache_read": 0.07, "cache_write": 0.0}
    override = {"input": 0.5}
    assert _merge_cost(catalog, override) == {
        "input": 0.5,
        "output": 1.10,
        "cache_read": 0.07,
        "cache_write": 0.0,
    }


def test_merge_cost_no_override_returns_catalog():
    catalog = {"input": 1.0, "output": 2.0, "cache_read": 0.0, "cache_write": 0.0}
    assert _merge_cost(catalog, None) == catalog


def test_resolve_with_preset_inherits_base_url_models_capability():
    cfg = {"preset": "deepseek/cn/anthropic-messages", "api_key": "k"}
    r = resolve_provider_config("deepseek", cfg)
    assert r.base_url == "https://api.deepseek.com/anthropic"
    assert r.provider_type == "anthropic-messages"
    assert r.preset_key == "deepseek/cn/anthropic-messages"
    assert r.capability  # non-empty descriptor dict
    assert len(r.models) >= 1
    assert all("cost" in m and "input" in m["cost"] for m in r.models)


def test_resolve_models_subset_filter():
    cfg = {
        "preset": "deepseek/cn/anthropic-messages",
        "api_key": "k",
        "models": ["deepseek-v4-flash"],
    }
    r = resolve_provider_config("deepseek", cfg)
    assert [m["id"] for m in r.models] == ["deepseek-v4-flash"]


def test_resolve_unknown_preset_fails_loudly():
    with pytest.raises(ValueError, match="unknown preset"):
        resolve_provider_config("x", {"preset": "no/such/key", "api_key": "k"})


def test_resolve_unknown_subset_model_fails_loudly():
    with pytest.raises(ValueError, match="not in preset"):
        resolve_provider_config(
            "deepseek",
            {"preset": "deepseek/cn/anthropic-messages", "api_key": "k", "models": ["ghost"]},
        )


def test_resolve_api_override_with_preset_rejected():
    with pytest.raises(ValueError, match="api.*not overridable"):
        resolve_provider_config(
            "deepseek",
            {
                "preset": "deepseek/cn/anthropic-messages",
                "api_key": "k",
                "api": "openai-completions",
            },
        )


def test_resolve_capability_override_with_preset_rejected():
    with pytest.raises(ValueError, match="capability.*not overridable"):
        resolve_provider_config(
            "deepseek",
            {
                "preset": "deepseek/cn/anthropic-messages",
                "api_key": "k",
                "capability": {"supports_tools": False},
            },
        )


def test_resolve_without_preset_requires_base_url_and_models():
    # base_url + non-empty models are required; api defaults to openai-completions.
    for bad in (
        {"api": "openai-completions", "models": [{"id": "m"}]},  # missing base_url
        {"base_url": "http://x/v1"},  # missing models
        {"base_url": "http://x/v1", "models": []},  # empty models
    ):
        with pytest.raises(ValueError, match="custom provider.*requires"):
            resolve_provider_config("custom", bad)


def test_resolve_without_preset_defaults_api_to_openai_completions():
    r = resolve_provider_config("custom", {"base_url": "http://x/v1", "models": [{"id": "m"}]})
    assert r.provider_type == "openai-completions"


def test_resolve_base_url_override_allowed():
    cfg = {
        "preset": "deepseek/cn/anthropic-messages",
        "api_key": "k",
        "base_url": "https://proxy.internal/anthropic",
    }
    r = resolve_provider_config("deepseek", cfg)
    assert r.base_url == "https://proxy.internal/anthropic"


def test_resolve_without_preset_uses_config_verbatim():
    cfg = {
        "base_url": "http://localhost:8000/v1",
        "api": "openai-completions",
        "models": [
            {"id": "m", "name": "M", "context_window": 1, "max_tokens": 1, "input": ["text"]}
        ],
    }
    r = resolve_provider_config("vllm", cfg)
    assert r.base_url == "http://localhost:8000/v1"
    assert r.preset_key is None
