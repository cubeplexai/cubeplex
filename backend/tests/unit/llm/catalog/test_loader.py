import pytest

from cubeplex.llm.catalog.loader import build_catalog, preset_key_for, resolve_capability
from cubeplex.llm.catalog.types import Endpoint, Vendor

PROFILES: dict[str, dict[str, object]] = {"x": {}}


def _vendor(**over):
    base = {
        "vendor": "v",
        "display_name": "V",
        "short_name": "V",
        "logo": None,
        "category": "saas",
        "description": "d",
        "regions": {"cn": {"host": "https://h"}},
        "endpoints": [{"region": "cn", "protocol": "openai-completions", "capability": "x"}],
        "models": [
            {
                "model_id": "m1",
                "display_name": "M1",
                "context_window": 1,
                "max_tokens": 1,
                "input_modalities": ["text"],
                "pricing": {"input": 1, "output": 1},
            }
        ],
    }
    base.update(over)
    return base


def test_vendor_parses_minimal():
    v = Vendor.model_validate(
        {
            "vendor": "deepseek",
            "display_name": "DeepSeek",
            "short_name": "DeepSeek",
            "logo": "deepseek",
            "category": "saas",
            "description": "DeepSeek V-series.",
            "regions": {"cn": {"host": "https://api.deepseek.com"}},
            "endpoints": [
                {
                    "region": "cn",
                    "protocol": "openai-completions",
                    "capability": "openai-compat-basic",
                }
            ],
            "models": [
                {
                    "model_id": "deepseek-v4",
                    "display_name": "DeepSeek V4",
                    "context_window": 64000,
                    "max_tokens": 8192,
                    "input_modalities": ["text"],
                    "reasoning": True,
                    "pricing": {"input": 0.27, "output": 1.10},
                }
            ],
        }
    )
    assert v.regions["cn"].host == "https://api.deepseek.com"
    assert v.endpoints[0].protocol == "openai-completions"
    assert v.endpoints[0].plan is None
    assert v.models[0].pricing.cache_read == 0.0
    assert v.models[0].plan is None


def test_preset_key_without_plan():
    ep = Endpoint(region="cn", protocol="anthropic-messages", capability="x")
    assert preset_key_for("deepseek", ep) == "deepseek/cn/anthropic-messages"


def test_preset_key_with_plan():
    ep = Endpoint(region="cn", protocol="openai-completions", plan="coding", capability="x")
    assert preset_key_for("zhipu", ep) == "zhipu/cn/openai-completions/coding"


def test_preset_key_override_wins():
    ep = Endpoint(region="cn", protocol="openai-completions", key="pretty-key", capability="x")
    assert preset_key_for("zhipu", ep) == "pretty-key"


def test_resolve_capability_named():
    profiles = {"openai-compat-basic": {"supports_tools": True, "supports_images": True}}
    cap = resolve_capability("openai-compat-basic", profiles)
    assert cap.supports_tools is True
    assert cap.supports_images is True


def test_resolve_capability_inline_dict():
    cap = resolve_capability(
        {"supports_images": True, "max_tokens_field": "max_completion_tokens"}, {}
    )
    assert cap.supports_images is True
    assert cap.max_tokens_field == "max_completion_tokens"


def test_resolve_capability_unknown_name_fails_loudly():
    with pytest.raises(ValueError, match="unknown capability profile"):
        resolve_capability("does-not-exist", {"openai-compat-basic": {}})


def test_untagged_endpoint_serves_all_models():
    cat = build_catalog([_vendor()], PROFILES)
    ep = cat.resolve("v/cn/openai-completions")
    assert [m.model_id for m in ep.models] == ["m1"]


def test_tiered_membership_by_plan_intersection():
    v = _vendor(
        endpoints=[
            {
                "region": "cn",
                "protocol": "openai-completions",
                "plan": "general",
                "capability": "x",
            },
            {
                "region": "cn",
                "protocol": "openai-completions",
                "plan": "coding",
                "path": "/coding",
                "capability": "x",
            },
        ],
        models=[
            {
                "model_id": "g",
                "display_name": "G",
                "context_window": 1,
                "max_tokens": 1,
                "input_modalities": ["text"],
                "plan": "general",
                "pricing": {"input": 1, "output": 1},
            },
            {
                "model_id": "c",
                "display_name": "C",
                "context_window": 1,
                "max_tokens": 1,
                "input_modalities": ["text"],
                "plan": "coding",
                "pricing": {"input": 1, "output": 1},
            },
        ],
    )
    cat = build_catalog([v], PROFILES)
    assert [m.model_id for m in cat.resolve("v/cn/openai-completions/general").models] == ["g"]
    assert [m.model_id for m in cat.resolve("v/cn/openai-completions/coding").models] == ["c"]


def test_mixed_tagged_untagged_rejected():
    v = _vendor(
        endpoints=[
            {"region": "cn", "protocol": "openai-completions", "plan": "coding", "capability": "x"}
        ],
        models=[
            {
                "model_id": "m",
                "display_name": "M",
                "context_window": 1,
                "max_tokens": 1,
                "input_modalities": ["text"],
                "pricing": {"input": 1, "output": 1},
            }
        ],
    )
    with pytest.raises(ValueError, match="mix"):
        build_catalog([v], PROFILES)


def test_dangling_endpoint_rejected():
    v = _vendor(
        endpoints=[
            {
                "region": "cn",
                "protocol": "openai-completions",
                "plan": "general",
                "capability": "x",
            },
            {
                "region": "cn",
                "protocol": "openai-completions",
                "plan": "coding",
                "path": "/c",
                "capability": "x",
            },
        ],
        models=[
            {
                "model_id": "g",
                "display_name": "G",
                "context_window": 1,
                "max_tokens": 1,
                "input_modalities": ["text"],
                "plan": "general",
                "pricing": {"input": 1, "output": 1},
            }
        ],
    )
    with pytest.raises(ValueError, match="no model"):
        build_catalog([v], PROFILES)


def test_unreachable_model_rejected():
    v = _vendor(
        endpoints=[
            {"region": "cn", "protocol": "openai-completions", "plan": "general", "capability": "x"}
        ],
        models=[
            {
                "model_id": "c",
                "display_name": "C",
                "context_window": 1,
                "max_tokens": 1,
                "input_modalities": ["text"],
                "plan": "coding",
                "pricing": {"input": 1, "output": 1},
            }
        ],
    )
    with pytest.raises(ValueError, match="no endpoint"):
        build_catalog([v], PROFILES)


def test_duplicate_preset_key_rejected():
    v1, v2 = _vendor(), _vendor()  # same vendor name -> same composed key
    with pytest.raises(ValueError, match="duplicate preset_key"):
        build_catalog([v1, v2], PROFILES)


def test_duplicate_endpoint_tuple_rejected_even_with_distinct_key_overrides():
    v = _vendor(
        endpoints=[
            {"region": "cn", "protocol": "openai-completions", "key": "k1", "capability": "x"},
            {"region": "cn", "protocol": "openai-completions", "key": "k2", "capability": "x"},
        ]
    )
    with pytest.raises(ValueError, match="duplicate endpoint"):
        build_catalog([v], PROFILES)


def test_catalog_to_api_shape():
    from cubeplex.llm.catalog import load_catalog

    api = load_catalog().to_api()
    assert isinstance(api, list)
    v = next(x for x in api if x["vendor"] == "deepseek")
    assert {
        "vendor",
        "display_name",
        "short_name",
        "logo",
        "category",
        "description",
        "endpoints",
        "models",
    } <= v.keys()
    ep = v["endpoints"][0]
    assert {"preset_key", "region", "protocol", "plan", "base_url", "model_ids"} <= ep.keys()
    m = v["models"][0]
    assert {
        "model_id",
        "display_name",
        "plan",
        "context_window",
        "max_tokens",
        "input_modalities",
        "reasoning",
        "pricing",
    } <= m.keys()


def test_catalog_excludes_cli_subscription_presets():
    from cubeplex.llm.catalog import load_catalog

    api = load_catalog().to_api()
    vendors = {v["vendor"] for v in api}
    preset_keys = {ep["preset_key"] for v in api for ep in v["endpoints"]}

    assert "anthropic-claude-code" not in vendors
    assert "openai-codex" not in vendors
    assert "anthropic-claude-code/intl/anthropic-messages" not in preset_keys
    assert "openai-codex/intl/openai-responses" not in preset_keys


def test_catalog_capabilities_use_standard_reasoning_shape():
    from cubeplex.llm.catalog import load_catalog

    api = load_catalog().to_api()
    endpoints = [ep for vendor in api for ep in vendor["endpoints"]]

    assert endpoints, "catalog should expose provider endpoints"
    for ep in endpoints:
        capability = ep["capability"]
        assert "reasoning_off_payload" not in capability
        assert "reasoning_on_payload" not in capability
        assert "reasoning_level" not in capability

    anthropic = next(
        ep for ep in endpoints if ep["preset_key"] == "anthropic/intl/anthropic-messages"
    )
    reasoning = anthropic["capability"]["reasoning"]
    assert reasoning["mode_payloads"]["off"] == {"thinking": {"type": "disabled"}}
    assert reasoning["mode_payloads"]["on"] == {"thinking": {"type": "enabled"}}
    assert reasoning["effort_path"] == "thinking.budget_tokens"
    assert reasoning["effort_values"]["max"] == 16384
    assert reasoning["apply_effort_when_off"] is False
