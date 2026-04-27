"""Unit tests for SKILL.md frontmatter parsing."""

import pytest

from cubebox.skills.frontmatter import (
    InvalidFrontmatterError,
    parse_skill_md,
)


def test_minimal_valid_frontmatter() -> None:
    text = """---
name: my-skill
description: Does a thing.
version: 1.0.0
---

# My Skill
"""
    fm = parse_skill_md(text)
    assert fm.name == "my-skill"
    assert fm.description == "Does a thing."
    assert fm.version == "1.0.0"
    assert fm.keywords == []
    assert fm.raw_metadata["name"] == "my-skill"


def test_keywords_as_list() -> None:
    text = """---
name: x
description: y
version: 0.1
keywords:
  - foo
  - bar
---
"""
    fm = parse_skill_md(text)
    assert fm.keywords == ["foo", "bar"]


def test_keywords_as_csv_string_normalised() -> None:
    text = """---
name: x
description: y
version: 0.1
keywords: foo, bar, baz
---
"""
    fm = parse_skill_md(text)
    assert fm.keywords == ["foo", "bar", "baz"]


def test_openclaw_alias_merged_into_raw_metadata() -> None:
    text = """---
name: x
description: y
version: 0.1
clawdbot:
  requires:
    bins: [git]
---
"""
    fm = parse_skill_md(text)
    assert fm.raw_metadata["requires"] == {"bins": ["git"]}


def test_alias_overrides_top_level() -> None:
    text = """---
name: x
description: y
version: 0.1
requires:
  bins: [old]
openclaw:
  requires:
    bins: [new]
---
"""
    fm = parse_skill_md(text)
    assert fm.raw_metadata["requires"] == {"bins": ["new"]}


def test_unknown_fields_preserved() -> None:
    text = """---
name: x
description: y
version: 0.1
custom_field: hello
---
"""
    fm = parse_skill_md(text)
    assert fm.raw_metadata["custom_field"] == "hello"


@pytest.mark.parametrize(
    "field",
    ["name", "description", "version"],
)
def test_required_field_missing(field: str) -> None:
    fields = {"name": "x", "description": "y", "version": "0.1"}
    fields.pop(field)
    body = "\n".join(f"{k}: {v}" for k, v in fields.items())
    text = f"---\n{body}\n---\n"
    with pytest.raises(InvalidFrontmatterError) as exc:
        parse_skill_md(text)
    assert exc.value.field == field


def test_no_frontmatter_block() -> None:
    text = "# Just markdown, no YAML block\n"
    with pytest.raises(InvalidFrontmatterError):
        parse_skill_md(text)


def test_version_with_whitespace_rejected() -> None:
    text = """---
name: x
description: y
version: " 1 0 "
---
"""
    with pytest.raises(InvalidFrontmatterError) as exc:
        parse_skill_md(text)
    assert exc.value.field == "version"
