"""Unit tests for the org-name → slug helper."""

import pytest

from cubeplex.auth.users import _slugify_org_name


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Acme Inc", "acme-inc"),
        ("Foo's Org", "foo-s-org"),
        ("  Multiple   Spaces  ", "multiple-spaces"),
        ("UPPER CASE", "upper-case"),
        ("Unicode 公司", "unicode"),  # non-ASCII stripped
        ("---leading-dashes---", "leading-dashes"),
        ("a" * 50, "a" * 31),  # truncated to 31
    ],
)
def test_slugify_org_name(name: str, expected: str) -> None:
    assert _slugify_org_name(name) == expected


def test_slugify_empty_falls_back() -> None:
    assert _slugify_org_name("") == "org"
    assert _slugify_org_name("公司") == "org"
