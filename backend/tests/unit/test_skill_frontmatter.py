"""Unit tests for SKILL.md frontmatter parsing."""

import pytest

from cubebox.skills.frontmatter import (
    InvalidFrontmatterError,
    extract_env_vars,
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


def test_cubebox_alias_merged_into_raw_metadata() -> None:
    text = """---
name: x
description: y
version: 0.1
cubebox:
  requires:
    env: [MY_KEY]
---
"""
    fm = parse_skill_md(text)
    assert fm.raw_metadata["requires"] == {"env": ["MY_KEY"]}


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


def test_metadata_openclaw_promoted_to_raw_metadata() -> None:
    """metadata.openclaw keys are promoted, same as top-level openclaw."""
    text = """---
name: x
description: y
version: 0.1
metadata:
  openclaw:
    requires:
      bins: [node]
      env: [MY_API_KEY]
    primaryEnv: MY_API_KEY
---
"""
    fm = parse_skill_md(text)
    assert fm.raw_metadata["requires"] == {"bins": ["node"], "env": ["MY_API_KEY"]}
    assert fm.raw_metadata["primaryEnv"] == "MY_API_KEY"


def test_metadata_alias_does_not_override_top_level_alias() -> None:
    """Top-level openclaw wins over metadata.openclaw."""
    text = """---
name: x
description: y
version: 0.1
metadata:
  openclaw:
    requires:
      env: [FROM_METADATA]
openclaw:
  requires:
    env: [FROM_TOPLEVEL]
---
"""
    fm = parse_skill_md(text)
    assert fm.raw_metadata["requires"] == {"env": ["FROM_TOPLEVEL"]}


def test_metadata_alias_overrides_bare_top_level() -> None:
    """metadata.openclaw wins over bare (non-alias) top-level keys."""
    text = """---
name: x
description: y
version: 0.1
requires:
  env: [BARE]
metadata:
  openclaw:
    requires:
      env: [FROM_METADATA]
---
"""
    fm = parse_skill_md(text)
    assert fm.raw_metadata["requires"] == {"env": ["FROM_METADATA"]}


def test_extract_env_vars_from_metadata_openclaw() -> None:
    text = """---
name: cuecue-deep-research
description: desc
version: 1.0.0
metadata:
  openclaw:
    requires:
      env: [CUECUE_API_KEY]
---
"""
    fm = parse_skill_md(text)
    assert extract_env_vars(fm.raw_metadata) == ["CUECUE_API_KEY"]


def test_extract_env_vars_from_toplevel_openclaw() -> None:
    text = """---
name: x
description: y
version: 1.0.0
openclaw:
  requires:
    env: [MY_KEY, OTHER_KEY]
---
"""
    fm = parse_skill_md(text)
    assert extract_env_vars(fm.raw_metadata) == ["MY_KEY", "OTHER_KEY"]


def test_extract_env_vars_empty_when_no_requires() -> None:
    fm = parse_skill_md("---\nname: x\ndescription: y\nversion: 1.0.0\n---\n")
    assert extract_env_vars(fm.raw_metadata) == []


def test_extract_env_vars_empty_when_requires_has_no_env() -> None:
    text = """---
name: x
description: y
version: 1.0.0
openclaw:
  requires:
    bins: [node]
---
"""
    fm = parse_skill_md(text)
    assert extract_env_vars(fm.raw_metadata) == []


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
