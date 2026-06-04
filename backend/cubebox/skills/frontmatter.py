"""SKILL.md YAML frontmatter parser."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(\n|$)", re.DOTALL)
_CUBEBOX_ALIASES = ("cubebox", "clawdbot", "clawdis", "openclaw")


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


def peek_skill_name(text: str) -> str | None:
    """Return the raw name value from SKILL.md frontmatter without full validation.

    Returns None if the frontmatter block is absent, unparseable, or name is missing.
    Used by the publish service to compute an auto-version before the full parse.
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return None
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def parse_skill_md(text: str, *, default_version: str | None = None) -> SkillFrontmatter:
    """Parse a SKILL.md document; return its frontmatter.

    Raises InvalidFrontmatterError if the YAML block is missing, malformed,
    or required fields are missing/invalid.

    If *default_version* is provided it is used when the version field is absent
    or blank, instead of raising InvalidFrontmatterError for that field.
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
        if default_version is not None:
            version = default_version
        else:
            raise InvalidFrontmatterError(
                field="version",
                reason="required, non-empty, must not contain whitespace",
            )

    keywords = _normalise_keywords(data.get("keywords"))

    raw_metadata: dict[str, Any] = dict(data)

    # Expand metadata.{alias} first (lower priority than top-level aliases).
    # Clawhub publishes skills with metadata.openclaw nesting; this normalises it.
    metadata_block = raw_metadata.get("metadata")
    if isinstance(metadata_block, dict):
        for alias in _CUBEBOX_ALIASES:
            nested = metadata_block.get(alias)
            if isinstance(nested, dict):
                for k, v in nested.items():
                    raw_metadata[k] = v  # overrides bare top-level keys

    # Top-level aliases override everything (including metadata.alias results above).
    for alias in _CUBEBOX_ALIASES:
        nested = raw_metadata.pop(alias, None)
        if isinstance(nested, dict):
            for k, v in nested.items():
                raw_metadata[k] = v

    return SkillFrontmatter(
        name=name.strip(),
        description=description.strip(),
        version=version,
        keywords=keywords,
        raw_metadata=raw_metadata,
    )


def extract_env_vars(raw_metadata: dict[str, Any]) -> list[str]:
    """Return required env var names from a parsed skill's raw_metadata.

    Reads raw_metadata["requires"]["env"] after alias expansion by parse_skill_md.
    Returns [] when absent or malformed.
    """
    requires = raw_metadata.get("requires")
    if not isinstance(requires, dict):
        return []
    env_list = requires.get("env")
    if not isinstance(env_list, list):
        return []
    return [str(e) for e in env_list if isinstance(e, str) and e]


def _normalise_keywords(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return []
