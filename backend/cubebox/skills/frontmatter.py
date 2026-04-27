"""SKILL.md YAML frontmatter parser."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(\n|$)", re.DOTALL)
_OPENCLAW_ALIASES = ("clawdbot", "clawdis", "openclaw")


@dataclass(frozen=True)
class InvalidFrontmatterError(Exception):
    field: str
    reason: str

    def __str__(self) -> str:
        return f"invalid frontmatter field {self.field!r}: {self.reason}"


@dataclass(frozen=True)
class SkillFrontmatter:
    name: str
    description: str
    version: str
    keywords: list[str] = field(default_factory=list)
    raw_metadata: dict[str, Any] = field(default_factory=dict)


def parse_skill_md(text: str) -> SkillFrontmatter:
    """Parse a SKILL.md document; return its frontmatter.

    Raises InvalidFrontmatterError if the YAML block is missing, malformed,
    or required fields are missing/invalid.
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise InvalidFrontmatterError(
            field="<block>",
            reason="missing YAML frontmatter; expected '---\\n...\\n---' at top",
        )

    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        raise InvalidFrontmatterError(field="<block>", reason=f"YAML parse error: {e}") from e

    if not isinstance(data, dict):
        raise InvalidFrontmatterError(
            field="<block>", reason="frontmatter must be a YAML mapping"
        )

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise InvalidFrontmatterError(field="name", reason="required, non-empty string")
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise InvalidFrontmatterError(field="description", reason="required, non-empty string")
    version_raw = data.get("version")
    version = str(version_raw).strip() if version_raw is not None else ""
    if not version or any(c.isspace() for c in version):
        raise InvalidFrontmatterError(
            field="version",
            reason="required, non-empty, must not contain whitespace",
        )

    keywords = _normalise_keywords(data.get("keywords"))

    raw_metadata: dict[str, Any] = dict(data)
    for alias in _OPENCLAW_ALIASES:
        nested = raw_metadata.pop(alias, None)
        if isinstance(nested, dict):
            for k, v in nested.items():
                raw_metadata[k] = v  # alias overrides any top-level same-name field

    return SkillFrontmatter(
        name=name.strip(),
        description=description.strip(),
        version=version,
        keywords=keywords,
        raw_metadata=raw_metadata,
    )


def _normalise_keywords(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return []
