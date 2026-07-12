from cubeplex.api.routes.v1.admin_providers import _resolve_logo


def test_resolve_logo_by_preset_key():
    assert _resolve_logo("deepseek/cn/anthropic-messages") == "deepseek"


def test_resolve_logo_by_key_override(monkeypatch):
    # A `key:`-overridden preset_key does NOT start with the vendor, so a split("/")
    # approach would fail. Inject a catalog whose endpoint key is "pretty-id" and
    # assert the logo still resolves via the vendor — a real regression guard.
    import cubeplex.api.routes.v1.admin_providers as mod
    import cubeplex.llm.catalog as catmod  # _resolve_logo does `from cubeplex.llm.catalog import load_catalog`
    from cubeplex.llm.catalog import build_catalog

    catalog = build_catalog(
        [
            {
                "vendor": "deepseek",
                "display_name": "DeepSeek",
                "short_name": "DeepSeek",
                "logo": "deepseek",
                "category": "saas",
                "description": "d",
                "regions": {"cn": {"host": "https://api.deepseek.com"}},
                "endpoints": [
                    {
                        "region": "cn",
                        "protocol": "openai-completions",
                        "key": "pretty-id",
                        "capability": "x",
                    }
                ],
                "models": [
                    {
                        "model_id": "m",
                        "display_name": "M",
                        "context_window": 1,
                        "max_tokens": 1,
                        "input_modalities": ["text"],
                        "pricing": {"input": 1, "output": 1},
                    }
                ],
            }
        ],
        {"x": {}},
    )
    monkeypatch.setattr(catmod, "load_catalog", lambda: catalog)
    assert mod._resolve_logo("pretty-id") == "deepseek"


def test_resolve_logo_none_for_unknown():
    assert _resolve_logo("nope/x/y") is None
    assert _resolve_logo(None) is None
