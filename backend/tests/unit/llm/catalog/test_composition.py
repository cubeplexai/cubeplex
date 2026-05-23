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
