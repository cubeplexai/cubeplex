import pytest

from cubebox.llm.catalog.loader import compose_base_url
from cubebox.llm.catalog.types import Endpoint, Region


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
    """
    from collections import Counter
    from pathlib import Path

    import yaml

    from cubebox.llm.catalog import load_catalog

    snapshot = Path(__file__).parent / "data" / "flat_providers_snapshot.yaml"
    flat = yaml.safe_load(snapshot.read_text("utf-8"))
    catalog = load_catalog()
    produced = Counter((e.protocol, e.base_url) for e in catalog.endpoints.values())
    expected = Counter((entry["api"], entry["base_url"]) for entry in flat if entry.get("base_url"))
    deficits = {
        pair: (cnt, produced[pair]) for pair, cnt in expected.items() if produced[pair] < cnt
    }
    assert not deficits, f"flat URLs under-reproduced (expected, got): {deficits}"
