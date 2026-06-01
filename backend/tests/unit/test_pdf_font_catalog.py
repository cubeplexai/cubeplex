"""Unit tests for pdf skill palette.py font probe + catalog logic.

These tests mock os.path.exists so no font files need to be installed locally.
"""

import sys
from pathlib import Path
from unittest.mock import patch

# Import palette from the skill scripts directory.
_SCRIPTS = Path(__file__).parent.parent.parent / "skills/preinstalled/pdf/scripts"
sys.path.insert(0, str(_SCRIPTS))
import palette  # noqa: E402

# ── probe_font_paths ──────────────────────────────────────────────────────────


def test_probe_returns_empty_when_no_fonts_found():
    with patch("os.path.exists", return_value=False):
        result = palette.probe_font_paths()
    assert result == {}


def test_probe_returns_first_matching_path():
    # WQY Micro Hei is now the first probe for CJK fonts (TrueType, reliable).
    def exists(path: str) -> bool:
        return "wqy-microhei.ttc" in path

    with patch("os.path.exists", side_effect=exists):
        result = palette.probe_font_paths()

    assert "NotoSansCJK" in result
    path, idx = result["NotoSansCJK"]
    assert "wqy-microhei.ttc" in path
    assert idx == 0


def test_probe_falls_back_to_second_path():
    def exists(path: str) -> bool:
        return "NotoSansCJK-Regular.ttc" in path

    with patch("os.path.exists", side_effect=exists):
        result = palette.probe_font_paths()

    assert "NotoSansCJK" in result
    path, idx = result["NotoSansCJK"]
    assert path.endswith("NotoSansCJK-Regular.ttc")
    assert idx == 2


def test_probe_liberation_sans():
    def exists(path: str) -> bool:
        return "liberation" in path.lower()

    with patch("os.path.exists", side_effect=exists):
        result = palette.probe_font_paths()

    assert "LiberationSans" in result
    path, idx = result["LiberationSans"]
    assert "LiberationSans-Regular.ttf" in path
    assert idx is None


# ── resolve_font ──────────────────────────────────────────────────────────────


def _all_probed() -> dict:
    """Return a probed dict that has every font the catalog needs."""
    return {name: ("/fake/" + name + ".ttf", None) for name in palette._FONT_PROBES}


def test_resolve_known_font_all_available():
    probed = _all_probed()
    entry = palette.resolve_font("noto-sans", probed)
    assert entry["body_rl"] == "NotoSansCJK"
    assert entry["body_b_rl"] == "NotoSansCJK-Bold"
    assert entry["display_rl"] == "NotoSansCJK-Bold"


def test_resolve_noto_serif():
    probed = _all_probed()
    entry = palette.resolve_font("noto-serif", probed)
    assert entry["body_rl"] == "NotoSerifCJK"


def test_resolve_monospace():
    probed = _all_probed()
    entry = palette.resolve_font("monospace", probed)
    assert entry["body_rl"] == "LiberationMono"


def test_resolve_falls_back_when_required_font_missing():
    probed = {k: v for k, v in _all_probed().items() if k != "NotoSansCJK"}
    entry = palette.resolve_font("noto-sans", probed)
    assert entry["body_rl"] == "Helvetica"


def test_resolve_unknown_font_name_falls_back():
    entry = palette.resolve_font("does-not-exist", _all_probed())
    assert entry["body_rl"] == "Helvetica"


# ── build_tokens font_paths ───────────────────────────────────────────────────


def test_build_tokens_no_fonts_available_uses_builtin():
    with patch("os.path.exists", return_value=False):
        tokens = palette.build_tokens("Test", "report")
    assert tokens["font_body_rl"] == "Helvetica"
    assert tokens["font_display_rl"] == "Times-Bold"
    assert tokens["font_paths"] == {}


def test_build_tokens_noto_sans_available_sets_font_paths():
    def exists(path: str) -> bool:
        return "NotoSansCJK" in path

    with patch("os.path.exists", side_effect=exists):
        tokens = palette.build_tokens("Test", "report")

    assert tokens["font_body_rl"] == "NotoSansCJK"
    assert tokens["font_body_b_rl"] == "NotoSansCJK-Bold"
    assert "NotoSansCJK" in tokens["font_paths"]
    spec = tokens["font_paths"]["NotoSansCJK"]
    assert "path" in spec
    assert "subfont_index" in spec


def test_build_tokens_body_font_override():
    def exists(path: str) -> bool:
        return "liberation" in path.lower()

    with patch("os.path.exists", side_effect=exists):
        tokens = palette.build_tokens("Test", "report", body_font="liberation")

    assert tokens["font_body_rl"] == "LiberationSans"
    assert tokens["font_display_rl"] == "LiberationSerif-Bold"


def test_build_tokens_terminal_mood_defaults_to_monospace():
    def exists(path: str) -> bool:
        return "liberation" in path.lower()

    with patch("os.path.exists", side_effect=exists):
        tokens = palette.build_tokens("Test", "terminal")

    assert tokens["font_body_rl"] == "LiberationMono"


def test_build_tokens_scholarly_mood_defaults_to_noto_serif():
    def exists(path: str) -> bool:
        return "NotoSerif" in path

    with patch("os.path.exists", side_effect=exists):
        tokens = palette.build_tokens("Test", "academic")

    assert tokens["font_body_rl"] == "NotoSerifCJK"
