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
    assert frontmatter.version == "1.1.0"
    assert "comprehensive" in frontmatter.description.lower()
    assert "parallel" in frontmatter.description.lower()


def test_wide_research_requires_source_strategy_before_fanout() -> None:
    normalized = " ".join(_PREINSTALLED.read_text(encoding="utf-8").split())

    assert "Do not dispatch collection workers until" in normalized
    assert "If several workers would rediscover the same source or method" in normalized
    assert "Time is a valid partition only after" in normalized


def test_wide_research_uses_a_single_writer_durable_ledger() -> None:
    normalized = " ".join(_PREINSTALLED.read_text(encoding="utf-8").split())

    assert "Subagents must not concurrently edit the same ledger file" in normalized
    assert "canonical ledger" in normalized
    assert "final dataset" in normalized
    assert all(file_type in normalized for file_type in ["CSV", "JSONL", "SQLite", "Excel"])


def test_wide_research_skill_stays_focused() -> None:
    body = _PREINSTALLED.read_text(encoding="utf-8")

    assert len(body.split()) <= 1500
