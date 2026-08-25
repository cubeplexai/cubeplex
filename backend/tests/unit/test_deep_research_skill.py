"""Contract tests for the preinstalled deep-research skill."""

from pathlib import Path

from cubeplex.skills.frontmatter import parse_skill_md

_PREINSTALLED = (
    Path(__file__).resolve().parents[2] / "skills" / "preinstalled" / "deep-research" / "SKILL.md"
)


def test_preinstalled_deep_research_skill_exists_and_parses() -> None:
    assert _PREINSTALLED.is_file()

    frontmatter = parse_skill_md(_PREINSTALLED.read_text(encoding="utf-8"))

    assert frontmatter.name == "deep-research"
    assert frontmatter.version == "3.3.0"
    assert "multi-source" in frontmatter.description.lower()


def test_deep_research_orders_dependent_work_into_waves() -> None:
    normalized = " ".join(_PREINSTALLED.read_text(encoding="utf-8").split())

    assert "Different angles are not automatically independent" in normalized
    assert "Tasks may run in the same wave only when" in normalized
    assert "Do not write later-wave prompts before their required inputs exist" in normalized
    assert "candidate discovery → candidate verification" in normalized


def test_deep_research_requires_review_between_waves() -> None:
    normalized = " ".join(_PREINSTALLED.read_text(encoding="utf-8").split())

    assert "Wave 1: independent reconnaissance or evidence discovery" in normalized
    assert "Review: consolidate evidence, gaps, conflicts, and dependencies" in normalized
    assert "Wave 2: targeted verification using reviewed Wave 1 outputs" in normalized
    assert "Synthesis: answer only from the reviewed evidence set" in normalized


def test_deep_research_skill_stays_focused() -> None:
    body = _PREINSTALLED.read_text(encoding="utf-8")

    assert len(body.split()) <= 1600
