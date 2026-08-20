"""Unit tests for pdf skill render_body.py table-width and content normalization.

These tests import render_body from the skill scripts directory. If reportlab
is not installed the whole module skips (render_body would otherwise try to
auto-install dependencies via pip).
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

if importlib.util.find_spec("reportlab") is None:
    pytest.skip("reportlab not installed", allow_module_level=True)

# Block render_body's module-level ensure_deps from pip-installing deps.
subprocess.check_call = lambda *a, **k: (_ for _ in ()).throw(  # noqa: ARG005
    AssertionError("render_body must not pip-install in unit tests")
)

_SCRIPTS = Path(__file__).parent.parent.parent / "skills/preinstalled/pdf/scripts"
sys.path.insert(0, str(_SCRIPTS))
import render_body  # noqa: E402

# ── _resolve_col_widths ────


def test_col_widths_fractions_kept():
    col_w = render_body._resolve_col_widths([0.2, 0.5, 0.3], 3, 400.0)
    assert col_w == pytest.approx([80.0, 200.0, 120.0])


def test_col_widths_absolute_normalized():
    # Pixel-style absolute widths (the #509 case) must be normalized, not
    # multiplied by usable_w (which produced ~38k/90k/90k pt and made
    # ReportLab silently drop the table).
    col_w = render_body._resolve_col_widths([90, 210, 210], 3, 432.0)
    assert col_w == pytest.approx([432 * 90 / 510, 432 * 210 / 510, 432 * 210 / 510])
    assert sum(col_w) == pytest.approx(432.0)


def test_col_widths_missing_falls_back_equal():
    col_w = render_body._resolve_col_widths(None, 3, 300.0)
    assert col_w == pytest.approx([100.0, 100.0, 100.0])


def test_col_widths_mismatch_falls_back_equal():
    col_w = render_body._resolve_col_widths([0.5, 0.5], 3, 300.0)
    assert col_w == pytest.approx([100.0, 100.0, 100.0])


def test_col_widths_non_numeric_falls_back_equal():
    col_w = render_body._resolve_col_widths(["a", "b", "c"], 3, 300.0)
    assert col_w == pytest.approx([100.0, 100.0, 100.0])


def test_col_widths_non_positive_falls_back_equal():
    col_w = render_body._resolve_col_widths([0.5, -1.0, 0.5], 3, 300.0)
    assert col_w == pytest.approx([100.0, 100.0, 100.0])


# ── normalize_content ──────────


def test_normalize_list_passthrough():
    blocks = [{"type": "body", "text": "hi"}]
    assert render_body.normalize_content(blocks) is blocks


def test_normalize_blocks_wrapper_unwrapped():
    blocks = [{"type": "body", "text": "hi"}]
    assert render_body.normalize_content({"blocks": blocks}) == blocks


def test_normalize_rejects_plain_dict():
    with pytest.raises(ValueError, match="list of block objects"):
        render_body.normalize_content({"type": "body"})


def test_normalize_rejects_embedded_string():
    with pytest.raises(ValueError, match="list of block objects"):
        render_body.normalize_content([{"type": "body"}, "oops"])
