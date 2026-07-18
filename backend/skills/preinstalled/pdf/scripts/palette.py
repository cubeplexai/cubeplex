#!/usr/bin/env python3
"""
palette.py — Infer design tokens from document metadata.

Usage:
    python3 palette.py --title "AI Trends 2025" --type report --out tokens.json
    python3 palette.py --title "John Doe Resume" --type resume --out tokens.json
    python3 palette.py --meta meta.json --out tokens.json

Outputs tokens.json consumed by all downstream scripts.
Cover fonts are loaded via Google Fonts @import in the cover HTML (no local caching).
Body fonts always use ReportLab system fonts (Times-Bold / Helvetica).
Exit codes: 0 success, 1 bad args, 3 write error
"""

import argparse
import json
import os
import sys

# ── Palette library ────────────────────────────────────────────────────────────
# Each entry: cover colors + cover_pattern + mood
PALETTES = {
    "report": {
        # Charcoal blue-grey cover; muted steel blue accent — authoritative, not flashy
        "cover_bg":   "#1B2A38",
        "accent":     "#3B6D8A",
        "accent_lt":  "#E6EFF5",
        "text_light": "#EDE9E2",
        "page_bg":    "#FAFAF8",
        "dark":       "#1A1E24",
        "body_text":  "#2C2C30",
        "muted":      "#7A7A84",
        "cover_pattern": "fullbleed",
        "mood": "authoritative",
    },
    "proposal": {
        # Dark charcoal cover; slate grey-blue accent — confident, understated
        "cover_bg":   "#22272E",
        "accent":     "#4E6070",
        "accent_lt":  "#EAECEE",
        "text_light": "#EDE9E2",
        "page_bg":    "#FAFAF7",
        "dark":       "#18191E",
        "body_text":  "#28282E",
        "muted":      "#7A7870",
        "cover_pattern": "split",
        "mood": "confident",
    },
    "resume": {
        # White; deep navy accent — clean and unambiguous
        "cover_bg":   "#FFFFFF",
        "accent":     "#1C3557",
        "accent_lt":  "#E8EEF5",
        "text_light": "#FFFFFF",
        "page_bg":    "#FFFFFF",
        "dark":       "#111111",
        "body_text":  "#222222",
        "muted":      "#888888",
        "cover_pattern": "typographic",
        "mood": "clean",
    },
    "portfolio": {
        # Near-black charcoal; cool slate grey accent — subdued professional
        "cover_bg":   "#191C20",
        "accent":     "#6A7A88",
        "accent_lt":  "#EAECEE",
        "text_light": "#EDE9E4",
        "page_bg":    "#F8F8F8",
        "dark":       "#18191E",
        "body_text":  "#28282E",
        "muted":      "#8A8A96",
        "cover_pattern": "atmospheric",
        "mood": "expressive",
    },
    "academic": {
        # Warm white; classic navy accent — scholarly standard
        "cover_bg":   "#F5F4F0",
        "accent":     "#2A436A",
        "accent_lt":  "#E6EBF4",
        "text_light": "#FFFFFF",
        "page_bg":    "#F5F4F0",
        "dark":       "#1A1A28",
        "body_text":  "#1E1E2A",
        "muted":      "#686877",
        "cover_pattern": "typographic",
        "mood": "scholarly",
    },
    "general": {
        # Dark slate; muted steel accent — neutral, no-nonsense
        "cover_bg":   "#1F2329",
        "accent":     "#4A6070",
        "accent_lt":  "#E6EAEC",
        "text_light": "#EEEBE5",
        "page_bg":    "#F8F6F2",
        "dark":       "#1A1A1A",
        "body_text":  "#2C2C2C",
        "muted":      "#888888",
        "cover_pattern": "fullbleed",
        "mood": "neutral",
    },
    # ── Extended types — each uses a distinct new cover pattern ─────────────────
    "minimal": {
        # Warm off-white; dark neutral grey — truly restrained, no color signal
        "cover_bg":   "#F7F6F4",
        "accent":     "#4A4A4A",
        "accent_lt":  "#EBEBEA",
        "text_light": "#F7F6F4",
        "page_bg":    "#F7F6F4",
        "dark":       "#111111",
        "body_text":  "#222222",
        "muted":      "#999999",
        "cover_pattern": "minimal",
        "mood": "restrained",
    },
    "stripe": {
        # Near-black; charcoal slate accent — structured, no-nonsense
        "cover_bg":   "#1E222A",
        "accent":     "#4A5568",
        "accent_lt":  "#EAECEE",
        "text_light": "#FFFFFF",
        "page_bg":    "#F8F8F7",
        "dark":       "#0E1117",
        "body_text":  "#262630",
        "muted":      "#888898",
        "cover_pattern": "stripe",
        "mood": "bold",
    },
    "diagonal": {
        # Deep navy; muted slate-blue accent — dignified, controlled
        "cover_bg":   "#1A2535",
        "accent":     "#3D5A72",
        "accent_lt":  "#E4EBF0",
        "text_light": "#EEF0F5",
        "page_bg":    "#F8FAFC",
        "dark":       "#0F1A2A",
        "body_text":  "#1E2C3A",
        "muted":      "#7A8A96",
        "cover_pattern": "diagonal",
        "mood": "dynamic",
    },
    "frame": {
        # Warm parchment; dark muted brown — classical, formal
        "cover_bg":   "#F5F2EC",
        "accent":     "#5C4A38",
        "accent_lt":  "#EAE5DE",
        "text_light": "#F5F2EC",
        "page_bg":    "#F5F2EC",
        "dark":       "#2A1E14",
        "body_text":  "#2C2018",
        "muted":      "#9A8A78",
        "cover_pattern": "frame",
        "mood": "classical",
    },
    "editorial": {
        # White; deep burgundy accent — editorial weight without the shout
        "cover_bg":   "#FFFFFF",
        "accent":     "#7A2B36",
        "accent_lt":  "#EEE4E5",
        "text_light": "#FFFFFF",
        "page_bg":    "#FFFFFF",
        "dark":       "#0A0A0A",
        "body_text":  "#1A1A1A",
        "muted":      "#777777",
        "cover_pattern": "editorial",
        "mood": "editorial",
    },
    # ── New patterns (v2) ────────────────────────────────────────────────────────
    "magazine": {
        # Warm linen; deep navy accent — formal publication standard
        "cover_bg":   "#F0EEE9",
        "accent":     "#1C3557",
        "accent_lt":  "#E4EBF3",
        "text_light": "#FFFFFF",
        "page_bg":    "#F0EEE9",
        "dark":       "#0D1A2B",
        "body_text":  "#2A2A2A",
        "muted":      "#888888",
        "cover_pattern": "magazine",
        "mood": "magazine",
    },
    "darkroom": {
        # Deep navy; muted steel-blue accent — premium, controlled
        "cover_bg":   "#151C27",
        "accent":     "#3D5A7A",
        "accent_lt":  "#E2EBF2",
        "text_light": "#EDE9E2",
        "page_bg":    "#F7F7F5",
        "dark":       "#0A1018",
        "body_text":  "#2C2C2C",
        "muted":      "#8A9AB0",
        "cover_pattern": "darkroom",
        "mood": "darkroom",
    },
    "terminal": {
        # Near-black; forest green accent — technical, serious (not neon)
        "cover_bg":   "#0D1117",
        "accent":     "#3D7A5C",
        "accent_lt":  "#E2EEE8",
        "text_light": "#E6EDF3",
        "page_bg":    "#F8F8F6",
        "dark":       "#010409",
        "body_text":  "#2C2C2C",
        "muted":      "#5A7A6A",
        "cover_pattern": "terminal",
        "mood": "terminal",
    },
    "poster": {
        # White; near-black accent sidebar — stark, unambiguous
        "cover_bg":   "#FFFFFF",
        "accent":     "#0A0A0A",
        "accent_lt":  "#EBEBEA",
        "text_light": "#FFFFFF",
        "page_bg":    "#FFFFFF",
        "dark":       "#0A0A0A",
        "body_text":  "#1A1A1A",
        "muted":      "#888888",
        "cover_pattern": "poster",
        "mood": "poster",
    },
}

# ── Font pairs — CSS names for cover HTML, ReportLab names for body ─────────────
# cover uses Google Fonts via @import (no local disk caching needed)
# body always uses system fonts via ReportLab
FONT_PAIRS = {
    "authoritative": {
        "display_css":  "Playfair Display",
        "body_css":     "IBM Plex Sans",
        "gfonts_import": "https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=IBM+Plex+Sans:ital,wght@0,400;0,600;1,400&display=swap",
        "display_rl":   "Times-Bold",
        "body_rl":      "Helvetica",
        "body_b_rl":    "Helvetica-Bold",
    },
    "confident": {
        "display_css":  "Syne",
        "body_css":     "Nunito Sans",
        "gfonts_import": "https://fonts.googleapis.com/css2?family=Syne:wght@600;800&family=Nunito+Sans:wght@400;600;700&display=swap",
        "display_rl":   "Times-Bold",
        "body_rl":      "Helvetica",
        "body_b_rl":    "Helvetica-Bold",
    },
    "clean": {
        "display_css":  "DM Serif Display",
        "body_css":     "DM Sans",
        "gfonts_import": "https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=DM+Sans:wght@300;400;500&display=swap",
        "display_rl":   "Times-Bold",
        "body_rl":      "Helvetica",
        "body_b_rl":    "Helvetica-Bold",
    },
    "expressive": {
        "display_css":  "Fraunces",
        "body_css":     "Inter",
        "gfonts_import": "https://fonts.googleapis.com/css2?family=Fraunces:ital,wght@0,700;0,900;1,900&family=Inter:wght@300;400;500&display=swap",
        "display_rl":   "Times-Bold",
        "body_rl":      "Helvetica",
        "body_b_rl":    "Helvetica-Bold",
    },
    "scholarly": {
        "display_css":  "EB Garamond",
        "body_css":     "Source Sans 3",
        "gfonts_import": "https://fonts.googleapis.com/css2?family=EB+Garamond:ital,wght@0,400;0,700;1,400&family=Source+Sans+3:wght@400;600&display=swap",
        "display_rl":   "Times-Bold",
        "body_rl":      "Helvetica",
        "body_b_rl":    "Helvetica-Bold",
    },
    "neutral": {
        "display_css":  "Outfit",
        "body_css":     "Outfit",
        "gfonts_import": "https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;700;900&display=swap",
        "display_rl":   "Times-Bold",
        "body_rl":      "Helvetica",
        "body_b_rl":    "Helvetica-Bold",
    },
    "restrained": {
        "display_css":  "Cormorant Garamond",
        "body_css":     "Jost",
        "gfonts_import": "https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,600;1,300&family=Jost:wght@300;400;500&display=swap",
        "display_rl":   "Times-Bold",
        "body_rl":      "Helvetica",
        "body_b_rl":    "Helvetica-Bold",
    },
    "bold": {
        "display_css":  "Barlow Condensed",
        "body_css":     "Barlow",
        "gfonts_import": "https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;900&family=Barlow:wght@400;500;600&display=swap",
        "display_rl":   "Times-Bold",
        "body_rl":      "Helvetica",
        "body_b_rl":    "Helvetica-Bold",
    },
    "dynamic": {
        "display_css":  "Montserrat",
        "body_css":     "Montserrat",
        "gfonts_import": "https://fonts.googleapis.com/css2?family=Montserrat:ital,wght@0,300;0,700;0,900;1,400&display=swap",
        "display_rl":   "Times-Bold",
        "body_rl":      "Helvetica",
        "body_b_rl":    "Helvetica-Bold",
    },
    "classical": {
        "display_css":  "Cormorant",
        "body_css":     "Crimson Pro",
        "gfonts_import": "https://fonts.googleapis.com/css2?family=Cormorant:ital,wght@0,400;0,700;1,400&family=Crimson+Pro:wght@400;600&display=swap",
        "display_rl":   "Times-Bold",
        "body_rl":      "Helvetica",
        "body_b_rl":    "Helvetica-Bold",
    },
    "editorial": {
        "display_css":  "Bebas Neue",
        "body_css":     "Libre Franklin",
        "gfonts_import": (
            "https://fonts.googleapis.com/css2?family=Bebas+Neue"
            "&family=Libre+Franklin:ital,wght@0,400;0,700;1,400&display=swap"
        ),
        "display_rl":   "Times-Bold",
        "body_rl":      "Helvetica",
        "body_b_rl":    "Helvetica-Bold",
    },
    # ── New moods (v2) ───────────────────────────────────────────────────────────
    "magazine": {
        "display_css":  "Playfair Display",
        "body_css":     "EB Garamond",
        "gfonts_import": (
            "https://fonts.googleapis.com/css2?family=Playfair+Display"
            ":ital,wght@0,700;0,900;1,700"
            "&family=EB+Garamond:ital,wght@0,400;0,600;1,400&display=swap"
        ),
        "display_rl":   "Times-Bold",
        "body_rl":      "Helvetica",
        "body_b_rl":    "Helvetica-Bold",
    },
    "darkroom": {
        "display_css":  "Playfair Display",
        "body_css":     "EB Garamond",
        "gfonts_import": (
            "https://fonts.googleapis.com/css2?family=Playfair+Display"
            ":ital,wght@0,700;0,900;1,700"
            "&family=EB+Garamond:ital,wght@0,400;0,600;1,400&display=swap"
        ),
        "display_rl":   "Times-Bold",
        "body_rl":      "Helvetica",
        "body_b_rl":    "Helvetica-Bold",
    },
    "terminal": {
        "display_css":  "Space Mono",
        "body_css":     "Space Mono",
        "gfonts_import": (
            "https://fonts.googleapis.com/css2?family=Space+Mono"
            ":ital,wght@0,400;0,700;1,400&display=swap"
        ),
        "display_rl":   "Courier-Bold",
        "body_rl":      "Courier",
        "body_b_rl":    "Courier-Bold",
    },
    "poster": {
        "display_css":  "Barlow Condensed",
        "body_css":     "Courier Prime",
        "gfonts_import": (
            "https://fonts.googleapis.com/css2?family=Barlow+Condensed"
            ":wght@700;900"
            "&family=Courier+Prime:ital,wght@0,400;0,700;1,400&display=swap"
        ),
        "display_rl":   "Times-Bold",
        "body_rl":      "Courier",
        "body_b_rl":    "Courier-Bold",
    },
}

SYSTEM_FALLBACK = {
    "display_css":  "Georgia",
    "body_css":     "Arial",
    "gfonts_import": "",
    "display_rl":   "Times-Bold",
    "body_rl":      "Helvetica",
    "body_b_rl":    "Helvetica-Bold",
}

# Locally installed CJK faces (fontconfig names) chained after the Latin cover
# faces — cover.py appends them to every font-family, so Chinese cover text
# gets a designed face instead of Chromium's default fallback. None of the
# Google Fonts display faces carry CJK glyphs, and the @import needs network;
# these faces cover both gaps. (display_cjk, body_cjk) per mood; serif Latin
# faces pair with the CJK Serif.
_CJK_FONT_PAIRS: dict[str, tuple[str, str]] = {
    "authoritative": ("Noto Serif CJK SC", "Noto Sans CJK SC"),
    "clean":         ("Noto Serif CJK SC", "Noto Sans CJK SC"),
    "expressive":    ("Noto Serif CJK SC", "Noto Sans CJK SC"),
    "scholarly":     ("Noto Serif CJK SC", "Noto Sans CJK SC"),
    "restrained":    ("Noto Serif CJK SC", "Noto Sans CJK SC"),
    "classical":     ("Noto Serif CJK SC", "Noto Serif CJK SC"),
    "magazine":      ("Noto Serif CJK SC", "Noto Serif CJK SC"),
    "darkroom":      ("Noto Serif CJK SC", "Noto Serif CJK SC"),
    "terminal":      ("Noto Sans Mono CJK SC", "Noto Sans Mono CJK SC"),
    "poster":        ("Noto Sans CJK SC", "Noto Sans Mono CJK SC"),
}
_CJK_DEFAULT_PAIR = ("Noto Sans CJK SC", "Noto Sans CJK SC")

# ── Runtime font detection ─────────────────────────────────────────────────────
# Each entry: ordered list of (path, subfont_index).
# subfont_index is None for plain TTF; int for TTC (ReportLab subfontIndex=0).
#
# IMPORTANT: ReportLab's TTFont only supports TrueType outlines, NOT OpenType/CFF
# (.otf files that use CFF outlines). The NotoSansCJKsc-*.otf files on Ubuntu are
# CFF-flavoured OpenType and will load but then fail in Paragraph rendering via
# ps2tt(). WenQuanYi Micro Hei (wqy-microhei.ttc) is genuine TrueType and is the
# reliable choice. Noto TTC variants are also TrueType but subfont index may vary.
_FONT_PROBES: dict[str, list[tuple[str, int | None]]] = {
    "NotoSansCJK": [
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),  # TrueType, reliable
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 2),  # TTC SC subfont
    ],
    "NotoSansCJK-Bold": [
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),  # no separate bold; body bold uses same face
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 2),
    ],
    "NotoSerifCJK": [
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),  # fallback to WQY for serif
        ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", 2),
    ],
    "NotoSerifCJK-Bold": [
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
        ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc", 2),
    ],
    "NotoSans": [
        ("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", None),
    ],
    "NotoSans-Bold": [
        ("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", None),
    ],
    "NotoSerif": [
        ("/usr/share/fonts/truetype/noto/NotoSerif-Regular.ttf", None),
    ],
    "NotoSerif-Bold": [
        ("/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf", None),
    ],
    "LiberationSans": [
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", None),
    ],
    "LiberationSans-Bold": [
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", None),
    ],
    "LiberationSerif-Bold": [
        ("/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf", None),
    ],
    "LiberationMono": [
        ("/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf", None),
    ],
    "LiberationMono-Bold": [
        ("/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf", None),
    ],
}


def probe_font_paths() -> dict[str, tuple[str, int | None]]:
    """Probe known font install paths. Returns {name: (path, subfont_index)} for each found font."""
    result: dict[str, tuple[str, int | None]] = {}
    for name, probes in _FONT_PROBES.items():
        for path, idx in probes:
            if os.path.exists(path):
                result[name] = (path, idx)
                break
    return result


# ── Font catalog ───────────────────────────────────────────────────────────────
# Maps agent-facing font name → ReportLab font names + required probe keys.
FONT_CATALOG: dict[str, dict] = {
    "noto-sans": {
        "display_rl": "NotoSansCJK-Bold",
        "body_rl":    "NotoSansCJK",
        "body_b_rl":  "NotoSansCJK-Bold",
        "requires":   ["NotoSansCJK", "NotoSansCJK-Bold"],
        "description": "Modern sans-serif, full CJK + Latin (default for most docs)",
    },
    "noto-serif": {
        "display_rl": "NotoSerifCJK-Bold",
        "body_rl":    "NotoSerifCJK",
        "body_b_rl":  "NotoSerifCJK-Bold",
        "requires":   ["NotoSerifCJK", "NotoSerifCJK-Bold"],
        "description": "Classic serif, full CJK + Latin (academic, editorial, annual reports)",
    },
    "noto-sans-latin": {
        "display_rl": "NotoSans-Bold",
        "body_rl":    "NotoSans",
        "body_b_rl":  "NotoSans-Bold",
        "requires":   ["NotoSans", "NotoSans-Bold"],
        "description": "Clean modern sans-serif, Latin only",
    },
    "noto-serif-latin": {
        "display_rl": "NotoSerif-Bold",
        "body_rl":    "NotoSerif",
        "body_b_rl":  "NotoSerif-Bold",
        "requires":   ["NotoSerif", "NotoSerif-Bold"],
        "description": "Classic serif, Latin only",
    },
    "liberation": {
        "display_rl": "LiberationSerif-Bold",
        "body_rl":    "LiberationSans",
        "body_b_rl":  "LiberationSans-Bold",
        "requires":   ["LiberationSans", "LiberationSans-Bold", "LiberationSerif-Bold"],
        "description": "Arial/Times-compatible, formal corporate documents",
    },
    "monospace": {
        "display_rl": "LiberationMono-Bold",
        "body_rl":    "LiberationMono",
        "body_b_rl":  "LiberationMono-Bold",
        "requires":   ["LiberationMono", "LiberationMono-Bold"],
        "description": "Monospace, terminal and code-heavy documents",
    },
}

# Built-in ReportLab fallback (no CJK support, always available).
_BUILTIN_FALLBACK: dict = {
    "display_rl": "Times-Bold",
    "body_rl":    "Helvetica",
    "body_b_rl":  "Helvetica-Bold",
    "requires":   [],
}

# Default font catalog name by mood; all other moods use _MOOD_DEFAULT_FONT.
_MOOD_FONT_OVERRIDES: dict[str, str] = {
    "scholarly":  "noto-serif",
    "restrained": "noto-serif",
    "classical":  "noto-serif",
    "magazine":   "noto-serif",
    "darkroom":   "noto-serif",
    "terminal":   "monospace",
}
_MOOD_DEFAULT_FONT = "noto-sans"


def resolve_font(
    font_name: str,
    probed: dict[str, tuple[str, int | None]],
) -> dict:
    """Return the catalog entry for font_name if all required fonts are probed; else built-in fallback."""
    entry = FONT_CATALOG.get(font_name)
    if entry and all(f in probed for f in entry["requires"]):
        return entry
    return _BUILTIN_FALLBACK


# ── Colour helpers ──────────────────────────────────────────────────────────────
def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lighten(hex_color: str, factor: float = 0.09) -> str:
    """Blend hex_color toward white (factor = accent weight, 0=white, 1=full color)."""
    r, g, b = _hex_to_rgb(hex_color)
    return "#{:02X}{:02X}{:02X}".format(
        round(r * factor + 255 * (1 - factor)),
        round(g * factor + 255 * (1 - factor)),
        round(b * factor + 255 * (1 - factor)),
    )


# ── Token assembly ─────────────────────────────────────────────────────────────
def build_tokens(
    title: str,
    doc_type: str,
    author: str = "",
    date: str = "",
    accent_override: str = "",
    cover_bg_override: str = "",
    body_font: str = "",
    display_font: str = "",
) -> dict:
    palette   = PALETTES.get(doc_type, PALETTES["general"]).copy()
    mood      = palette["mood"]
    font_pair = FONT_PAIRS.get(mood, SYSTEM_FALLBACK)

    if accent_override:
        palette["accent"]    = accent_override
        palette["accent_lt"] = _lighten(accent_override, 0.09)
    if cover_bg_override:
        palette["cover_bg"] = cover_bg_override

    # Resolve body/display fonts — probe system paths, fall back to built-ins.
    probed = probe_font_paths()
    _default_catalog = _MOOD_FONT_OVERRIDES.get(mood, _MOOD_DEFAULT_FONT)
    body_entry    = resolve_font(body_font or _default_catalog, probed)
    display_entry = resolve_font(display_font or body_font or _default_catalog, probed)

    # Collect font_paths for all required fonts (body + display, deduplicated).
    required_names: list[str] = list(dict.fromkeys(
        body_entry["requires"] + display_entry["requires"]
    ))
    font_paths: dict[str, dict] = {}
    for name in required_names:
        if name in probed:
            path, idx = probed[name]
            font_paths[name] = {"path": path, "subfont_index": idx}

    tokens = {
        # Identity
        "title":    title,
        "author":   author,
        "date":     date,
        "doc_type": doc_type,

        # Palette
        "cover_bg":      palette["cover_bg"],
        "accent":        palette["accent"],
        "accent_lt":     palette["accent_lt"],
        "text_light":    palette["text_light"],
        "page_bg":       palette["page_bg"],
        "dark":          palette["dark"],
        "body_text":     palette["body_text"],
        "muted":         palette["muted"],
        "cover_pattern": palette["cover_pattern"],
        "mood":          mood,

        # Typography — CSS names for cover HTML (Google Fonts @import)
        "font_display":  font_pair["display_css"],
        "font_body":     font_pair["body_css"],
        "gfonts_import": font_pair["gfonts_import"],

        # Typography — locally installed CJK faces for the cover font chain
        "font_display_cjk": _CJK_FONT_PAIRS.get(mood, _CJK_DEFAULT_PAIR)[0],
        "font_body_cjk":    _CJK_FONT_PAIRS.get(mood, _CJK_DEFAULT_PAIR)[1],

        # Typography — ReportLab names for body pages
        "font_display_rl": display_entry["display_rl"],
        "font_body_rl":    body_entry["body_rl"],
        "font_body_b_rl":  body_entry["body_b_rl"],

        # Legacy keys
        "font_heading": display_entry["display_rl"],
        "font_body_b":  body_entry["body_b_rl"],
        "font_paths":   font_paths,

        # Type scale (pt)
        "size_display": 54,
        "size_h1":      22,
        "size_h2":      15,
        "size_h3":      11.5,
        "size_body":    10.5,
        "size_caption": 8.5,
        "size_meta":    8,

        # Layout (pt)
        "margin_left":   79,
        "margin_right":  79,
        "margin_top":    79,
        "margin_bottom": 71,
        "section_gap":   26,
        "para_gap":      8,
        "line_gap":      17,
    }
    return tokens


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Generate design tokens from document metadata")
    parser.add_argument("--title",  default="Untitled Document")
    parser.add_argument("--type",   default="general",
                        choices=list(PALETTES.keys()),
                        help="Document type: " + ", ".join(PALETTES.keys()))
    parser.add_argument("--author", default="")
    parser.add_argument("--date",   default="")
    parser.add_argument("--meta",     help="JSON file with title/type/author/date keys")
    parser.add_argument("--accent",   default="",
                        help="Override accent colour (hex, e.g. #2D6A8F). "
                             "accent_lt is auto-derived by lightening toward white.")
    parser.add_argument("--cover-bg", default="",
                        help="Override cover background colour (hex).")
    parser.add_argument("--body-font",    default="",
                        choices=list(FONT_CATALOG.keys()) + [""],
                        help="Body font: " + ", ".join(FONT_CATALOG.keys()))
    parser.add_argument("--display-font", default="",
                        choices=list(FONT_CATALOG.keys()) + [""],
                        help="Display/heading font (defaults to --body-font)")
    parser.add_argument("--out",    default="tokens.json")
    args = parser.parse_args()

    if args.meta:
        try:
            with open(args.meta) as f:
                meta = json.load(f)
            args.title  = meta.get("title",  args.title)
            args.type   = meta.get("type",   args.type)
            args.author = meta.get("author", args.author)
            args.date   = meta.get("date",   args.date)
        except Exception as e:
            print(json.dumps({"status": "error", "error": str(e)}), file=sys.stderr)
            sys.exit(1)

    tokens = build_tokens(
        args.title, args.type, args.author, args.date,
        accent_override=args.accent,
        cover_bg_override=getattr(args, "cover_bg", ""),
        body_font=getattr(args, "body_font", ""),
        display_font=getattr(args, "display_font", ""),
    )

    try:
        with open(args.out, "w") as f:
            json.dump(tokens, f, indent=2)
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}), file=sys.stderr)
        sys.exit(3)

    print(json.dumps({
        "status":  "ok",
        "out":     args.out,
        "mood":    tokens["mood"],
        "pattern": tokens["cover_pattern"],
        "fonts":   f'{tokens["font_display"]} / {tokens["font_body"]}',
    }))


if __name__ == "__main__":
    main()
