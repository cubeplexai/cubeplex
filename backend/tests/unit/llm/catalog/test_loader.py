from cubebox.llm.catalog.types import Vendor


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
