import pytest

from cubeplex.llm.catalog.loader import compose_base_url
from cubeplex.llm.catalog.types import Endpoint, Region


@pytest.mark.parametrize(
    "regions,endpoint,expected",
    [
        # A. path differs, region host
        (
            {"cn": Region(host="https://open.bigmodel.cn")},
            Endpoint(
                region="cn",
                protocol="openai-completions",
                path="/api/coding/paas/v4",
                capability="x",
            ),
            "https://open.bigmodel.cn/api/coding/paas/v4",
        ),
        # B. host override (Alibaba coding lives on a different domain)
        (
            {"cn": Region(host="https://dashscope.aliyuncs.com")},
            Endpoint(
                region="cn",
                protocol="openai-completions",
                host="https://coding.dashscope.aliyuncs.com",
                path="/v1",
                capability="x",
            ),
            "https://coding.dashscope.aliyuncs.com/v1",
        ),
        # C. empty path (DeepSeek openai)
        (
            {"cn": Region(host="https://api.deepseek.com")},
            Endpoint(region="cn", protocol="openai-completions", capability="x"),
            "https://api.deepseek.com",
        ),
        # D. full base_url override bypasses composition
        (
            {"intl": Region(host="https://ignored")},
            Endpoint(
                region="intl",
                protocol="openai-responses",
                base_url="https://chatgpt.com/backend-api/codex",
                capability="x",
            ),
            "https://chatgpt.com/backend-api/codex",
        ),
    ],
)
def test_compose_base_url(regions, endpoint, expected):
    assert compose_base_url(regions, endpoint) == expected


def test_compose_base_url_unknown_region_raises():
    with pytest.raises(ValueError, match="unknown region"):
        compose_base_url(
            {"cn": Region(host="https://x")},
            Endpoint(region="intl", protocol="openai-completions", capability="x"),
        )


def test_every_flat_base_url_is_reproduced():
    """Each flat (api, base_url) must be reproduced with the SAME MULTIPLICITY.

    Multiplicity matters: two flat entries can share an identical (api, base_url)
    (e.g. moonshot vs moonshot-coding both openai-completions on api.moonshot.ai/v1).
    A plain set would let one new endpoint satisfy both; a Counter requires the new
    catalog to produce at least as many endpoints per (api, base_url). Flat entries
    with base_url == "" (custom-*) are excluded — they have no composed URL.

    INTENTIONAL_DIVERGENCE: a few flat coding-plan presets carried placeholder
    URLs (identical to the general plan) that the consolidated catalog replaces
    with the real, deployed coding-plan host. Those flat slugs are excluded — the
    divergence is deliberate, not a porting regression.
    """
    from collections import Counter
    from pathlib import Path

    import yaml

    from cubeplex.llm.catalog import load_catalog

    # flat slug -> reason. The consolidated catalog folds coding plans into their
    # parent vendor and uses the real coding host; these flat URLs were placeholders.
    intentional_divergence = {
        "anthropic-claude-code": "Claude Code subscription preset is intentionally not "
        "exposed in the provider catalog.",
        "openai-codex": "Codex ChatGPT subscription preset is intentionally not exposed "
        "in the provider catalog.",
        "qwen-coding-cn": "Aliyun coding plan lives on coding.dashscope.aliyuncs.com "
        "(real deployment, folded into aliyun/cn/.../coding), not the flat placeholder "
        "dashscope.aliyuncs.com/compatible-mode/v1",
    }

    snapshot = Path(__file__).parent / "data" / "flat_providers_snapshot.yaml"
    flat = yaml.safe_load(snapshot.read_text("utf-8"))
    catalog = load_catalog()
    produced = Counter((e.protocol, e.base_url) for e in catalog.endpoints.values())
    expected = Counter(
        (entry["api"], entry["base_url"])
        for entry in flat
        if entry.get("base_url") and entry["slug"] not in intentional_divergence
    )
    deficits = {
        pair: (cnt, produced[pair]) for pair, cnt in expected.items() if produced[pair] < cnt
    }
    assert not deficits, f"flat URLs under-reproduced (expected, got): {deficits}"
