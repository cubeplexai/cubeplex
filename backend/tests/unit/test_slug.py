import pytest

from cubeplex.utils.slug import slugify


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("DeepSeek", "deepseek"),
        ("DeepSeek (Anthropic shape)", "deepseek-anthropic-shape"),
        ("  Open AI  ", "open-ai"),
        ("GPT-4o", "gpt-4o"),
        ("a__b--c", "a-b-c"),
        ("智谱 GLM", "glm"),  # non-ascii stripped, ascii kept
        ("!!!", "provider"),  # all-punctuation fallback
        ("", "provider"),
    ],
)
def test_slugify(name: str, expected: str) -> None:
    assert slugify(name) == expected
