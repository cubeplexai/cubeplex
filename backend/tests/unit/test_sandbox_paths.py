"""Unit tests for sandbox skill-dir path construction.

Guards the fix for the bug where registry-installed skills (canonical name
``<org>:<skill>``) mounted their bundled files at a colon-bearing path that the
agent mis-constructed, so reads of scripts/references failed. The colon must be
normalised so the path is filesystem-safe and the agent gets it verbatim.
"""

from __future__ import annotations

from cubebox.skills.sandbox_paths import safe_skill_name, sandbox_skill_dir


def test_plain_name_unchanged() -> None:
    # Preinstalled skills have plain names — path must be untouched.
    assert sandbox_skill_dir("create-pptx", "1.0.0") == "/workspace/.skills/create-pptx/1.0.0"


def test_colon_name_is_normalised() -> None:
    # Registry canonical name <org>:<skill> — the colon is the bug; it must not
    # survive into the path as a separator.
    got = sandbox_skill_dir("acme-org:my-skill", "2.1.0")
    assert ":" not in got
    assert got == "/workspace/.skills/acme-org__my-skill/2.1.0"


def test_no_trailing_slash() -> None:
    # Callers append their own separator; the dir itself has none.
    assert not sandbox_skill_dir("x", "1.0").endswith("/")


def test_safe_skill_name_plain() -> None:
    # Plain preinstalled names pass through unchanged.
    assert safe_skill_name("create-pptx") == "create-pptx"


def test_safe_skill_name_with_colon() -> None:
    # Registry canonical names <org>:<skill> have colon replaced.
    assert safe_skill_name("acme-org:my-skill") == "acme-org__my-skill"
