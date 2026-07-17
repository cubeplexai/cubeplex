"""§6.4 backfill-parity guard: the preset cutover must not silently drop a
provider that USED to receive capability backfill under the old name==slug rule.
"""

from pathlib import Path

import pytest
import yaml

from cubeplex.config import config as settings
from cubeplex.seeders.provider_seeder import resolve_provider_config

_SNAPSHOT = Path(__file__).parent / "llm" / "catalog" / "data" / "flat_providers_snapshot.yaml"

# Providers that DID match a flat slug (so were backfilled) but are DELIBERATELY
# downgraded to custom in the rewrite, each with a recorded §6.4 reason. A name
# here is an intentional drop, not a silent one.
DELIBERATE_CUSTOM = {
    "vllm": "self-hosted OSS framework; model is deployment-specific (not catalog data), "
    "openai-compatible so cubepi defaults suffice",
}


def _old_backfilled_provider_names() -> set[str]:
    """Provider names that matched a flat preset slug under the OLD rule."""
    flat_slugs = {e["slug"] for e in yaml.safe_load(_SNAPSHOT.read_text("utf-8"))}
    cfg_providers = dict(dict(settings.get("llm", {})).get("providers", {}))
    return {name for name in cfg_providers if name in flat_slugs}


def test_no_provider_silently_loses_capability_backfill():
    """Every old-backfilled provider still resolves a capability now, UNLESS it
    is an intentional, documented downgrade in DELIBERATE_CUSTOM."""
    cfg_providers = dict(dict(settings.get("llm", {})).get("providers", {}))
    regressed = []
    for name in _old_backfilled_provider_names():
        if name in DELIBERATE_CUSTOM:
            continue
        r = resolve_provider_config(name, dict(cfg_providers[name]))
        if r.preset_key is None or not r.capability:
            regressed.append(name)
    assert not regressed, (
        f"providers lost capability backfill in the rewrite (map to a preset, "
        f"or add to DELIBERATE_CUSTOM with a reason): {regressed}"
    )


# The §6.4 inventory of preset-mapped providers — each MUST resolve a capability
# + at least one model from the catalog.
PRESET_MAPPED = {
    "deepseek": "deepseek/cn/anthropic-messages",
    "minimax": "minimax/cn/openai-completions/general",
    "arkcode": "volcengine/cn/openai-completions/coding",
    "alicode": "aliyun/cn/openai-completions/coding",
    "volengine": "volcengine/cn/openai-completions/general",
    "openrouter": "openrouter/intl/openai-completions",
}


@pytest.mark.parametrize("name,key", list(PRESET_MAPPED.items()))
def test_preset_mapped_providers_get_capability_and_models(name, key):
    r = resolve_provider_config(name, {"preset": key, "api_key": "k"})
    assert r.preset_key == key
    assert r.capability is not None
    assert len(r.models) >= 1
