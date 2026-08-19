"""Unit tests for sandbox browser tab-follow helper."""

from __future__ import annotations

from cubeplex.sandbox.browser_tab_follow import (
    FOCUS_SCRIPT,
    WRAPPER_SCRIPT,
    choose_target_id,
    tab_follow_install_command,
)


def test_choose_target_id_unique_url() -> None:
    pages = [
        {"id": "a", "type": "page", "url": "https://a.example/"},
        {"id": "b", "type": "page", "url": "https://b.example/"},
    ]
    assert choose_target_id(pages, "https://b.example/") == "b"


def test_choose_target_id_prefers_exact_match() -> None:
    pages = [
        {"id": "short", "type": "page", "url": "https://ex.com"},
        {"id": "long", "type": "page", "url": "https://ex.com/path"},
    ]
    assert choose_target_id(pages, "https://ex.com/path") == "long"


def test_choose_target_id_does_not_prefix_match_a_parent_url() -> None:
    pages = [
        {"id": "home", "type": "page", "url": "https://ex.com"},
        {"id": "login", "type": "page", "url": "https://ex.com/login"},
    ]
    assert choose_target_id(pages, "https://ex.com/login") == "login"
    assert choose_target_id(pages, "https://ex.com/other") is None


def test_choose_target_id_treats_trailing_slash_as_same_page() -> None:
    pages = [{"id": "home", "type": "page", "url": "https://ex.com/"}]
    assert choose_target_id(pages, "https://ex.com") == "home"


def test_choose_target_id_ignores_empty_page_url() -> None:
    pages = [{"id": "blank", "type": "page", "url": ""}]
    assert choose_target_id(pages, "https://x.example/") is None


def test_choose_target_id_none_without_url_or_match() -> None:
    pages = [{"id": "a", "type": "page", "url": "https://x.example/"}]
    assert choose_target_id(pages, None) is None
    assert choose_target_id(pages, "https://other.example/") is None
    assert choose_target_id([], "https://x.example/") is None


def test_install_command_embeds_wrapper_and_focus_helper() -> None:
    cmd = tab_follow_install_command()
    assert cmd.startswith("python3 -c ")
    assert "cubeplex-focus-browser-tab" in cmd
    assert "CUBEPLEX_TAB_FOLLOW=1" in WRAPPER_SCRIPT
    assert "/json/activate/" in FOCUS_SCRIPT
    assert "__REAL_DEFAULT__" in WRAPPER_SCRIPT
    assert "wrap_path.replace" in cmd
    assert "_norm" in FOCUS_SCRIPT
