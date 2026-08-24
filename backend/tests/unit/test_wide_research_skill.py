"""Contract tests for the preinstalled wide-research skill."""

from pathlib import Path

from cubeplex.skills.frontmatter import parse_skill_md

_PREINSTALLED = (
    Path(__file__).resolve().parents[2] / "skills" / "preinstalled" / "wide-research" / "SKILL.md"
)


def test_preinstalled_wide_research_skill_exists_and_parses() -> None:
    """The catalog must discover wide research as a distinct preinstalled skill."""
    assert _PREINSTALLED.is_file()

    frontmatter = parse_skill_md(_PREINSTALLED.read_text(encoding="utf-8"))

    assert frontmatter.name == "wide-research"
    assert frontmatter.version == "1.0.0"
    assert "comprehensive" in frontmatter.description.lower()
    assert "parallel" in frontmatter.description.lower()
