#!/usr/bin/env python3
"""check_deck.py — pre-delivery self-check for a generated .pptx.

Borrowed discipline (clean-room) from kimi's pptd checker: never deliver a deck
without verifying it. Flags the three defects that make programmatic decks look
broken:
  - OVERFLOW   text that doesn't fit its box (measured with real font metrics)
  - OFFCANVAS  shapes extending past the slide edge
  - CONTRAST   text too low-contrast against its background to read

Usage:  python3 check_deck.py deck.pptx
Exit 0 = clean (warnings allowed), 1 = errors found, 2 = bad input.
Measures with PIL using the actual (free) font resolved via matplotlib, so the
numbers match what LibreOffice/PowerPoint will render in the sandbox.
"""

from __future__ import annotations

import sys
from functools import lru_cache

from matplotlib import font_manager
from PIL import ImageFont
from pptx import Presentation

EMU_PER_PT = 12700
LINE_HEIGHT = 1.3  # matches typical PPTX single-line spacing
OVERFLOW_TOLERANCE = 1.06  # allow 6% slack before calling it overflow


@lru_cache(maxsize=64)
def _font(name: str, size_pt: float) -> ImageFont.FreeTypeFont:
    try:
        path = font_manager.findfont(
            font_manager.FontProperties(family=name), fallback_to_default=True
        )
    except Exception:
        path = font_manager.findfont(font_manager.FontProperties())
    # work in point-space: 1pt -> 1px so widths/heights compare to EMU->pt boxes
    return ImageFont.truetype(path, max(1, int(round(size_pt))))


def _text_width_pt(text: str, name: str, size_pt: float) -> float:
    if not text:
        return 0.0
    f = _font(name, size_pt)
    return f.getlength(text)


def _lum(hexstr: str) -> float:
    h = hexstr.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(fg: str, bg: str) -> float:
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _hex(color) -> str | None:
    try:
        if color and color.type is not None and color.rgb is not None:
            return str(color.rgb)
    except Exception:
        return None
    return None


def check(path: str) -> int:
    prs = Presentation(path)
    sw_pt = prs.slide_width / EMU_PER_PT
    sh_pt = prs.slide_height / EMU_PER_PT
    # slide background hex (default white if unset)
    errors: list[str] = []
    warns: list[str] = []

    for si, slide in enumerate(prs.slides, 1):
        try:
            bg = _hex(slide.background.fill.fore_color) or "FFFFFF"
        except Exception:
            bg = "FFFFFF"
        # Pre-collect solid-filled shapes (in z-order) so a transparent textbox
        # drawn over a colored panel/bar is contrast-checked against that panel,
        # not the slide bg — which is what actually renders.
        panels: list[tuple[float, float, float, float, str]] = []
        for sh in slide.shapes:
            try:
                if sh.has_text_frame and sh.fill.type != 1:
                    continue  # plain textbox, not a backdrop
                if sh.fill.type == 1 and None not in (sh.left, sh.top, sh.width, sh.height):
                    fh = _hex(sh.fill.fore_color)
                    if fh:
                        panels.append(
                            (
                                sh.left / EMU_PER_PT,
                                sh.top / EMU_PER_PT,
                                sh.width / EMU_PER_PT,
                                sh.height / EMU_PER_PT,
                                fh,
                            )
                        )
            except Exception:
                pass

        def backdrop_at(cx: float, cy: float, _panels=panels, _bg=bg) -> str:
            for px, py, pw, ph, fh in reversed(_panels):  # topmost first
                if px - 1 <= cx <= px + pw + 1 and py - 1 <= cy <= py + ph + 1:
                    return fh
            return _bg

        for shp in slide.shapes:
            # --- off-canvas (skip shapes with no explicit geometry) ---
            try:
                sl, st, sw, sh_ = shp.left, shp.top, shp.width, shp.height
                if None not in (sl, st, sw, sh_):
                    x0, y0 = sl / EMU_PER_PT, st / EMU_PER_PT
                    w, h = sw / EMU_PER_PT, sh_ / EMU_PER_PT
                    if x0 < -2 or y0 < -2 or x0 + w > sw_pt + 2 or y0 + h > sh_pt + 2:
                        errors.append(
                            f"slide {si}: shape off-canvas "
                            f"(x={x0:.0f},y={y0:.0f},w={w:.0f},h={h:.0f} vs {sw_pt:.0f}x{sh_pt:.0f}pt)"
                        )
            except Exception:
                pass

            if not shp.has_text_frame:
                continue
            box_w = (shp.width / EMU_PER_PT) if shp.width else sw_pt
            box_h = (shp.height / EMU_PER_PT) if shp.height else sh_pt
            # fill for contrast: own solid fill, else the panel it sits on,
            # else slide bg.
            try:
                fill_hex = _hex(shp.fill.fore_color) if shp.fill.type == 1 else None
            except Exception:
                fill_hex = None
            if fill_hex:
                backdrop = fill_hex
            elif None not in (shp.left, shp.top, shp.width, shp.height):
                cx = (shp.left + shp.width / 2) / EMU_PER_PT
                cy = (shp.top + shp.height / 2) / EMU_PER_PT
                backdrop = backdrop_at(cx, cy)
            else:
                backdrop = bg

            total_h = 0.0
            for para in shp.text_frame.paragraphs:
                runs = para.runs
                if not runs:
                    continue
                ptxt = "".join(r.text for r in runs)
                size = next((r.font.size.pt for r in runs if r.font.size), 18.0)
                name = next((r.font.name for r in runs if r.font.name), "DejaVu Sans")
                wpt = _text_width_pt(ptxt, name, size)
                lines = (
                    max(1, -(-int(wpt) // max(1, int(box_w)))) if shp.text_frame.word_wrap else 1
                )
                total_h += lines * size * LINE_HEIGHT
                # single unwrapped line wider than the box
                if not shp.text_frame.word_wrap and wpt > box_w * OVERFLOW_TOLERANCE:
                    errors.append(
                        f"slide {si}: text overflows width "
                        f'("{ptxt[:30]}" needs {wpt:.0f}pt, box {box_w:.0f}pt)'
                    )
                # contrast
                fg = _hex(runs[0].font.color)
                if fg:
                    ratio = _contrast(fg, backdrop)
                    thresh = 3.0 if size >= 24 else 4.5
                    if ratio < thresh:
                        warns.append(
                            f"slide {si}: low contrast {ratio:.1f}:1 "
                            f'(text #{fg} on #{backdrop}, "{ptxt[:24]}")'
                        )
            if total_h > box_h * OVERFLOW_TOLERANCE:
                errors.append(
                    f"slide {si}: text overflows height (needs ~{total_h:.0f}pt, box {box_h:.0f}pt)"
                )

    print(f"Checked {len(prs.slides)} slides: {len(errors)} error(s), {len(warns)} warning(s)")
    for e in errors:
        print("  ERROR  ", e)
    for w in warns:
        print("  warn   ", w)
    return 1 if errors else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python3 check_deck.py <deck.pptx>", file=sys.stderr)
        sys.exit(2)
    sys.exit(check(sys.argv[1]))
