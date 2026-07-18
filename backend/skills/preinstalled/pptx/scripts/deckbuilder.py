"""deckbuilder — a themed python-pptx toolkit for clean, editable 16:9 decks.

Clean-room: built on python-pptx (open) with layout patterns proven by the
community MIT-0 pptx-generator and create-pptx skills, redone to (a) use only
the premium libre fonts installed in the sandbox image, (b) avoid emoji glyphs
(which render as tofu in non-emoji fonts) — accents are drawn shapes, and
(c) offer a real theme system + a broad set of slide archetypes. Pair with
check_deck.py, which flags overflow / off-canvas / low-contrast before delivery.

Design model
------------
A *theme* fixes the whole look: a color system (bg / surface / primary / muted /
accent / on_accent), a font pairing (display for headings, body for prose, plus
a CJK face), and a profile that says what it's for. Pick one theme for the deck.
Each *archetype* method composes one slide with consistent spacing/contrast so
the result passes the checker.

Fonts in the image (see deploy/images/sandbox/stage-fonts.sh):
  Latin : Inter, Barlow, Anton, Oranienbaum, Unna, Liter, Sorts Mill Goudy,
          Quattrocento Sans
  CJK   : Noto Sans CJK SC, LXGW WenKai (霞鹜文楷), Smiley Sans (得意黑),
          ZCOOL KuaiLe / XiaoWei / QingKe HuangYou
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_TICK_MARK
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

EMU_PER_INCH = 914400


def _rgb(hex_str: str) -> RGBColor:
    return RGBColor.from_string(hex_str.lstrip("#").upper())


def _set_run_fonts(run, latin: str, cjk: str) -> None:
    """Set the Latin (<a:latin>) and East-Asian (<a:ea>) typefaces on a run.

    python-pptx's ``run.font.name`` only sets the Latin face; CJK glyphs use the
    ``<a:ea>`` face. Without ``ea``, Chinese falls back to the Latin font (no CJK
    glyphs) and renders as tofu. ``<a:ea>`` must follow ``<a:latin>`` per
    CT_TextCharacterProperties ordering.
    """
    run.font.name = latin
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        existing = rPr.find(qn(tag))
        if existing is not None:
            rPr.remove(existing)
    ea = rPr.makeelement(qn("a:ea"), {"typeface": cjk})
    latin_el = rPr.find(qn("a:latin"))
    if latin_el is not None:
        latin_el.addnext(ea)
    else:
        rPr.append(ea)


def _set_fill_alpha(shape, opacity_pct: float) -> None:
    """Set the opacity (0-100) of a shape's solid fill via an <a:alpha> child —
    used to make a dark scrim over a hero image so overlaid text stays legible."""
    solid = shape._element.spPr.find(qn("a:solidFill"))
    if solid is None:
        return
    srgb = solid.find(qn("a:srgbClr"))
    if srgb is None:
        return
    for old in srgb.findall(qn("a:alpha")):
        srgb.remove(old)
    srgb.append(srgb.makeelement(qn("a:alpha"), {"val": str(int(opacity_pct * 1000))}))


def _no_fill_spPr(parent):
    spPr = parent.makeelement(qn("c:spPr"), {})
    spPr.append(spPr.makeelement(qn("a:noFill"), {}))
    ln = spPr.makeelement(qn("a:ln"), {})
    ln.append(ln.makeelement(qn("a:noFill"), {}))
    spPr.append(ln)
    return spPr


def _transparent_chart(chart) -> None:
    """No-fill chart-space and plot-area so the theme bg shows through (an opaque
    white box looks broken on dark themes). Schema order: in CT_ChartSpace
    ``<c:spPr>`` comes right after ``<c:chart>``; in CT_PlotArea it is the last
    styling child (before any ``extLst``)."""
    cs = chart._chartSpace
    old = cs.find(qn("c:spPr"))
    if old is not None:
        cs.remove(old)
    chart_el = cs.find(qn("c:chart"))
    if chart_el is not None:
        chart_el.addnext(_no_fill_spPr(cs))
    pa = cs.find(f".//{qn('c:plotArea')}")
    if pa is not None:
        old = pa.find(qn("c:spPr"))
        if old is not None:
            pa.remove(old)
        ext = pa.find(qn("c:extLst"))
        sp = _no_fill_spPr(pa)
        if ext is not None:
            ext.addprevious(sp)
        else:
            pa.append(sp)


@dataclass
class Theme:
    """A complete look: palette + font pairing + selection metadata."""

    name: str
    profile: str  # what it's for, one word (tech / editorial / academic / ...)
    blurb: str  # one-line "use when ..." guidance
    bg: str  # slide background
    surface: str  # card / panel fill
    primary: str  # headings + key text on bg
    muted: str  # secondary / body text
    accent: str  # brand accent — rules, key numbers, highlight column
    on_accent: str  # text on an accent fill
    font_display: str  # headings, kickers, big numbers
    font_body: str  # prose, table cells, captions
    font_cjk: str  # East-Asian face (used for any CJK text)
    dark: bool


# A curated set spanning common deck personalities. Display/body pairings use the
# premium libre fonts baked into the image; serif-display-over-sans-body is a
# classic editorial move. All pass the contrast checker on their own surfaces.
THEMES: dict[str, Theme] = {
    "midnight": Theme(
        "midnight", "tech", "Dark, modern, product/engineering keynotes.",
        bg="0F141E", surface="1B2433", primary="F4F6FB", muted="9AA7BD",
        accent="00D2A0", on_accent="06231C",
        font_display="Inter", font_body="Inter", font_cjk="Noto Sans CJK SC", dark=True,
    ),
    "ember": Theme(
        "ember", "editorial", "Dark + warm; strategy briefs, opinionated narratives.",
        bg="14110E", surface="241E18", primary="F7F3EC", muted="B7A98F",
        accent="E8A13A", on_accent="241600",
        font_display="Oranienbaum", font_body="Inter", font_cjk="Noto Sans CJK SC", dark=True,
    ),
    "daylight": Theme(
        "daylight", "corporate", "Clean light; business reports, status reviews.",
        bg="FFFFFF", surface="F2F5FA", primary="1E293B", muted="52607A",
        accent="2563EB", on_accent="FFFFFF",
        font_display="Inter", font_body="Inter", font_cjk="Noto Sans CJK SC", dark=False,
    ),
    "slate": Theme(
        "slate", "minimal", "Neutral, restrained; consulting, data-heavy decks.",
        bg="F7F8FA", surface="ECEFF4", primary="1F2430", muted="5B6472",
        accent="4F46E5", on_accent="FFFFFF",
        font_display="Inter", font_body="Inter", font_cjk="Noto Sans CJK SC", dark=False,
    ),
    "orchid": Theme(
        "orchid", "creative", "Bold dark; launches, brand & vision decks.",
        bg="161020", surface="241733", primary="F5EFFA", muted="B49FCB",
        accent="C264FF", on_accent="1B0A2E",
        font_display="Anton", font_body="Inter", font_cjk="Noto Sans CJK SC", dark=True,
    ),
    "bloom": Theme(
        "bloom", "promotion", "Vibrant light; marketing, product launches.",
        bg="FFFBF7", surface="FFF0E8", primary="2A1A22", muted="7A5A66",
        accent="F0476A", on_accent="FFFFFF",
        font_display="Barlow", font_body="Inter", font_cjk="Noto Sans CJK SC", dark=False,
    ),
    "sage": Theme(
        "sage", "calm", "Soft, organic; sustainability, health, research.",
        bg="F6F8F4", surface="E8EFE4", primary="20291F", muted="566355",
        accent="2F9E6E", on_accent="FFFFFF",
        font_display="Inter", font_body="Inter", font_cjk="Noto Sans CJK SC", dark=False,
    ),
    "noir": Theme(
        "noir", "highimpact", "High-contrast black; single big ideas, manifestos.",
        bg="0A0A0B", surface="17181B", primary="FAFAFA", muted="9B9DA3",
        accent="F2D544", on_accent="14130A",
        font_display="Anton", font_body="Inter", font_cjk="Noto Sans CJK SC", dark=True,
    ),
    "paper": Theme(
        "paper", "academic", "Warm paper + ink; papers, lectures, long-form.",
        bg="FBF7F0", surface="F1E9DC", primary="2A2620", muted="6B6253",
        accent="B4622D", on_accent="FFFFFF",
        font_display="Sorts Mill Goudy", font_body="Quattrocento Sans",
        font_cjk="LXGW WenKai", dark=False,
    ),
}


def theme_catalog() -> str:
    """One line per theme for quick selection (used by the SKILL doc + agents)."""
    rows = [
        f"- {t.name:9} [{t.profile:10}] {t.blurb}  (display={t.font_display}, body={t.font_body})"
        for t in THEMES.values()
    ]
    return "Available themes:\n" + "\n".join(rows)


class Deck:
    """16:9 deck builder. One method per slide archetype; pick a theme once."""

    def __init__(self, theme: str = "midnight") -> None:
        self.theme = THEMES.get(theme, THEMES["midnight"])
        self.prs = Presentation()
        self.prs.slide_width = Emu(12192000)  # 13.333in
        self.prs.slide_height = Emu(6858000)  # 7.5in
        self.W = self.prs.slide_width
        self.H = self.prs.slide_height
        self.MX = Emu(int(0.9 * EMU_PER_INCH))  # standard horizontal margin

    # ---- low-level helpers -------------------------------------------------

    def _blank(self, bg=None):
        slide = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = _rgb(bg or self.theme.bg)
        return slide

    def _rect(self, slide, x, y, w, h, fill=None, line=None, rounded=False):
        shp = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE, x, y, w, h
        )
        if fill is None:
            shp.fill.background()
        else:
            shp.fill.solid()
            shp.fill.fore_color.rgb = _rgb(fill)
        if line is None:
            shp.line.fill.background()
        else:
            shp.line.color.rgb = _rgb(line)
            shp.line.width = Pt(1)
        shp.shadow.inherit = False
        return shp

    def _text(
        self, slide, x, y, w, h, runs, *, font=None, align=PP_ALIGN.LEFT,
        anchor=MSO_ANCHOR.TOP, wrap=True,
    ):
        """runs: list of paragraphs; each paragraph is a list of
        (text, size_pt, color_hex, bold) tuples. ``font`` overrides the body
        face (used to pass the display face for headings)."""
        face = font or self.theme.font_body
        box = slide.shapes.add_textbox(x, y, w, h)
        tf = box.text_frame
        tf.word_wrap = wrap
        tf.vertical_anchor = anchor
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        for i, para in enumerate(runs):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            for (txt, size, color, bold) in para:
                r = p.add_run()
                r.text = txt
                r.font.size = Pt(size)
                r.font.bold = bold
                r.font.color.rgb = _rgb(color)
                _set_run_fonts(r, face, self.theme.font_cjk)
        return box

    def _kicker(self, slide, x, y, w, text):
        self._text(
            slide, x, y, w, Pt(20), [[(text.upper(), 12, self.theme.accent, True)]],
            font=self.theme.font_display,
        )
        self._rect(slide, x, y + Pt(22), Emu(int(0.7 * EMU_PER_INCH)), Pt(3), fill=self.theme.accent)

    def _header(self, slide, title, kicker=""):
        top = Emu(int(0.7 * EMU_PER_INCH))
        if kicker:
            self._kicker(slide, self.MX, top, self.W - 2 * self.MX, kicker)
            top = top + Emu(int(0.55 * EMU_PER_INCH))
        self._text(
            slide, self.MX, top, self.W - 2 * self.MX, Emu(int(0.9 * EMU_PER_INCH)),
            [[(title, 30, self.theme.primary, True)]], font=self.theme.font_display,
        )
        return self.MX

    # ---- title / section ---------------------------------------------------

    def cover(self, title, subtitle="", kicker="", meta=""):
        s = self._blank()
        mx = self.MX
        if kicker:
            self._kicker(s, mx, Emu(int(2.1 * EMU_PER_INCH)), self.W - 2 * mx, kicker)
        self._text(
            s, mx, Emu(int(2.7 * EMU_PER_INCH)), self.W - 2 * mx, Emu(int(2.2 * EMU_PER_INCH)),
            [[(title, 40, self.theme.primary, True)]], font=self.theme.font_display,
        )
        if subtitle:
            self._text(
                s, mx, Emu(int(4.7 * EMU_PER_INCH)), self.W - 2 * mx, Emu(int(1.0 * EMU_PER_INCH)),
                [[(subtitle, 20, self.theme.muted, False)]],
            )
        if meta:
            self._text(
                s, mx, self.H - Emu(int(0.8 * EMU_PER_INCH)), self.W - 2 * mx, Pt(24),
                [[(meta, 12, self.theme.muted, False)]],
            )
        return s

    def section(self, title, subtitle="", number=""):
        """A divider slide that opens a new part of the deck (accent background)."""
        s = self._blank(bg=self.theme.accent)
        mx = self.MX
        if number:
            self._text(
                s, mx, Emu(int(2.0 * EMU_PER_INCH)), self.W - 2 * mx, Pt(40),
                [[(number, 18, self.theme.on_accent, True)]], font=self.theme.font_display,
            )
        self._text(
            s, mx, Emu(int(2.7 * EMU_PER_INCH)), self.W - 2 * mx, Emu(int(1.8 * EMU_PER_INCH)),
            [[(title, 38, self.theme.on_accent, True)]], font=self.theme.font_display,
        )
        if subtitle:
            self._text(
                s, mx, Emu(int(4.4 * EMU_PER_INCH)), self.W - 2 * mx, Emu(int(1.0 * EMU_PER_INCH)),
                [[(subtitle, 18, self.theme.on_accent, False)]],
            )
        return s

    def statement(self, text, attribution="", kicker=""):
        """One big idea / pull-quote, centered. Keep text short."""
        s = self._blank()
        mx = Emu(int(1.4 * EMU_PER_INCH))
        if kicker:
            self._kicker(s, mx, Emu(int(1.5 * EMU_PER_INCH)), self.W - 2 * mx, kicker)
        self._text(
            s, mx, Emu(int(2.3 * EMU_PER_INCH)), self.W - 2 * mx, Emu(int(2.8 * EMU_PER_INCH)),
            [[(text, 32, self.theme.primary, True)]], font=self.theme.font_display,
            anchor=MSO_ANCHOR.MIDDLE,
        )
        if attribution:
            self._text(
                s, mx, self.H - Emu(int(1.2 * EMU_PER_INCH)), self.W - 2 * mx, Pt(28),
                [[("— " + attribution, 14, self.theme.muted, False)]],
            )
        return s

    # ---- content -----------------------------------------------------------

    def agenda(self, title, items, kicker="Overview"):
        s = self._blank()
        mx = self._header(s, title, kicker)
        top = Emu(int(2.3 * EMU_PER_INCH))
        gap = Emu(int(0.95 * EMU_PER_INCH))
        for i, it in enumerate(items):
            y = top + i * gap
            self._rect(s, mx, y, Pt(34), Pt(34), fill=self.theme.surface, rounded=True)
            self._text(
                s, mx, y, Pt(34), Pt(34), [[(f"{i + 1:02d}", 13, self.theme.accent, True)]],
                font=self.theme.font_display, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE,
            )
            self._text(
                s, mx + Pt(48), y, self.W - 2 * mx - Pt(48), Pt(40),
                [[(it, 18, self.theme.primary, True)]], anchor=MSO_ANCHOR.MIDDLE,
            )
        return s

    def cards(self, title, cards, kicker=""):
        """cards: list of (label, body). 2-col grid with an accent edge."""
        s = self._blank()
        mx = self._header(s, title, kicker)
        cols, gut = 2, Emu(int(0.4 * EMU_PER_INCH))
        cw = (self.W - 2 * mx - (cols - 1) * gut) // cols
        ch = Emu(int(1.9 * EMU_PER_INCH))
        top, rgap = Emu(int(2.3 * EMU_PER_INCH)), Emu(int(0.35 * EMU_PER_INCH))
        for i, (label, body) in enumerate(cards):
            r, c = divmod(i, cols)
            x = mx + c * (cw + gut)
            y = top + r * (ch + rgap)
            self._rect(s, x, y, cw, ch, fill=self.theme.surface, rounded=True)
            self._rect(s, x, y, Pt(4), ch, fill=self.theme.accent)
            pad = Pt(16)
            self._text(
                s, x + pad, y + pad, cw - 2 * pad, Pt(30),
                [[(label, 17, self.theme.primary, True)]], font=self.theme.font_display,
            )
            self._text(
                s, x + pad, y + pad + Pt(34), cw - 2 * pad, ch - pad - Pt(40),
                [[(body, 12.5, self.theme.muted, False)]],
            )
        return s

    def steps(self, title, items, kicker=""):
        """Numbered horizontal process: items = list of (label, body). 2-4 steps."""
        s = self._blank()
        mx = self._header(s, title, kicker)
        n = max(1, len(items))
        gut = Emu(int(0.45 * EMU_PER_INCH))
        cw = (self.W - 2 * mx - (n - 1) * gut) // n
        top = Emu(int(2.5 * EMU_PER_INCH))
        for i, (label, body) in enumerate(items):
            x = mx + i * (cw + gut)
            self._rect(s, x, top, Pt(40), Pt(40), fill=self.theme.accent, rounded=True)
            self._text(
                s, x, top, Pt(40), Pt(40), [[(str(i + 1), 18, self.theme.on_accent, True)]],
                font=self.theme.font_display, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE,
            )
            self._text(
                s, x, top + Pt(54), cw, Pt(32), [[(label, 16, self.theme.primary, True)]],
                font=self.theme.font_display,
            )
            self._text(
                s, x, top + Pt(88), cw, Emu(int(1.8 * EMU_PER_INCH)),
                [[(body, 12, self.theme.muted, False)]],
            )
        return s

    def comparison(self, title, headers, rows, kicker="", highlight_col=None):
        """Real editable table. headers=[dim,A,B]; rows=[[d,a,b],...] (≤6 rows)."""
        s = self._blank()
        mx = self._header(s, title, kicker)
        top = Emu(int(2.3 * EMU_PER_INCH))
        tw = self.W - 2 * mx
        th = Emu(int(3.6 * EMU_PER_INCH))
        shp = s.shapes.add_table(len(rows) + 1, len(headers), mx, top, tw, th)
        tbl = shp.table
        tbl.first_row = False
        tbl.horz_banding = False
        for ci, htxt in enumerate(headers):
            cell = tbl.cell(0, ci)
            cell.fill.solid()
            cell.fill.fore_color.rgb = _rgb(
                self.theme.accent if highlight_col == ci else self.theme.primary
            )
            on = self.theme.on_accent if highlight_col == ci else self.theme.bg
            self._cell(cell, htxt, 13, on, True, self.theme.font_display)
        for ri, row in enumerate(rows, start=1):
            for ci, val in enumerate(row):
                cell = tbl.cell(ri, ci)
                cell.fill.solid()
                cell.fill.fore_color.rgb = _rgb(self.theme.surface if ri % 2 else self.theme.bg)
                col = (
                    self.theme.accent
                    if ci == highlight_col
                    else (self.theme.primary if ci == 0 else self.theme.muted)
                )
                self._cell(cell, str(val), 12, col, ci == 0 or ci == highlight_col)
        return s

    def _cell(self, cell, text, size, color, bold, font=None):
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.margin_left = cell.margin_right = Pt(10)
        tf = cell.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = text
        for r in p.runs:
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.color.rgb = _rgb(color)
            _set_run_fonts(r, font or self.theme.font_body, self.theme.font_cjk)

    def metrics(self, title, items, kicker="By the numbers"):
        """items: list of (big_number, label, sub). 2-4 big accent numbers."""
        s = self._blank()
        mx = self._header(s, title, kicker)
        n = len(items)
        gut = Emu(int(0.5 * EMU_PER_INCH))
        cw = (self.W - 2 * mx - (n - 1) * gut) // n
        top = Emu(int(2.9 * EMU_PER_INCH))
        for i, (big, label, sub) in enumerate(items):
            x = mx + i * (cw + gut)
            self._text(
                s, x, top, cw, Pt(80), [[(big, 54, self.theme.accent, True)]],
                font=self.theme.font_display,
            )
            self._text(
                s, x, top + Pt(78), cw, Pt(34), [[(label, 16, self.theme.primary, True)]],
                font=self.theme.font_display,
            )
            if sub:
                self._text(s, x, top + Pt(110), cw, Pt(60), [[(sub, 11.5, self.theme.muted, False)]])
        return s

    def chart(
        self, title, categories, values, *, series_name="", kicker="",
        number_format="0", caption="",
    ):
        """Native editable column chart in the accent color (≤6 categories)."""
        s = self._blank()
        mx = self._header(s, title, kicker)
        top = Emu(int(2.4 * EMU_PER_INCH))
        w = self.W - 2 * mx
        h = Emu(int(3.3 * EMU_PER_INCH))
        cd = CategoryChartData()
        cd.categories = categories
        cd.add_series(series_name or "Series 1", values, number_format=number_format)
        gframe = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, mx, top, w, h, cd)
        chart = gframe.chart
        chart.has_legend = False
        chart.has_title = False
        plot = chart.plots[0]
        plot.gap_width = 60
        plot.has_data_labels = True
        dl = plot.data_labels
        dl.number_format = number_format
        dl.number_format_is_linked = False
        dl.position = XL_LABEL_POSITION.OUTSIDE_END
        dl.font.size = Pt(12)
        dl.font.bold = True
        dl.font.color.rgb = _rgb(self.theme.primary)
        series = plot.series[0]
        series.format.fill.solid()
        series.format.fill.fore_color.rgb = _rgb(self.theme.accent)
        series.format.line.fill.background()
        cat = chart.category_axis
        cat.tick_labels.font.size = Pt(12)
        cat.tick_labels.font.color.rgb = _rgb(self.theme.muted)
        cat.major_tick_mark = XL_TICK_MARK.NONE
        cat.format.line.color.rgb = _rgb(self.theme.muted)
        val = chart.value_axis
        val.visible = False
        val.has_major_gridlines = False
        _transparent_chart(chart)
        if caption:
            self._text(
                s, mx, top + h + Pt(6), w, Pt(28), [[(caption, 11.5, self.theme.muted, False)]]
            )
        return s

    def closing(self, title, takeaways, cta="", kicker="Takeaways"):
        s = self._blank()
        mx = self._header(s, title, kicker)
        top = Emu(int(2.3 * EMU_PER_INCH))
        gap = Emu(int(0.85 * EMU_PER_INCH))
        for i, t in enumerate(takeaways):
            y = top + i * gap
            self._rect(s, mx, y + Pt(7), Pt(10), Pt(10), fill=self.theme.accent)
            self._text(
                s, mx + Pt(24), y, self.W - 2 * mx - Pt(24), Pt(70),
                [[(t, 16, self.theme.primary, False)]],
            )
        if cta:
            bar_h = Emu(int(0.95 * EMU_PER_INCH))
            y = self.H - bar_h - Emu(int(0.5 * EMU_PER_INCH))
            self._rect(s, mx, y, self.W - 2 * mx, bar_h, fill=self.theme.accent, rounded=True)
            self._text(
                s, mx, y, self.W - 2 * mx, bar_h, [[(cta, 18, self.theme.on_accent, True)]],
                font=self.theme.font_display, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE,
            )
        return s

    # ---- images ------------------------------------------------------------

    def _picture_fill(self, slide, path, x, y, w, h):
        """Place an image to *fill* the box (cover), cropping the overflow so it
        is never stretched/distorted."""
        iw, ih = Image.open(path).size
        box_ar = w / h
        img_ar = iw / ih
        pic = slide.shapes.add_picture(path, x, y, width=w, height=h)
        if img_ar > box_ar:  # source too wide → crop sides
            crop = (1 - box_ar / img_ar) / 2
            pic.crop_left = crop
            pic.crop_right = crop
        elif img_ar < box_ar:  # source too tall → crop top/bottom
            crop = (1 - img_ar / box_ar) / 2
            pic.crop_top = crop
            pic.crop_bottom = crop
        return pic

    def hero(self, title, subtitle="", kicker="", image_path=None):
        """Full-bleed image cover with a legibility scrim + overlaid title.
        Falls back to ``cover`` when no image is supplied."""
        if not image_path:
            return self.cover(title, subtitle, kicker=kicker)
        s = self._blank()
        self._picture_fill(s, image_path, 0, 0, self.W, self.H)
        # A hero always needs a DARK scrim — overlaid text is white regardless of
        # the theme's light/dark, so a light scrim would be white-on-white.
        scrim = self._rect(s, 0, int(self.H * 0.42), self.W, int(self.H * 0.58), fill="0B0B0D")
        _set_fill_alpha(scrim, 62)
        mx = self.MX
        light = "FFFFFF"
        if kicker:
            # White text (legible on the dark scrim for any theme) + an accent rule.
            ky = int(self.H * 0.55)
            self._text(
                s, mx, ky, self.W - 2 * mx, Pt(22),
                [[(kicker.upper(), 12, light, True)]], font=self.theme.font_display,
            )
            self._rect(s, mx, ky + Pt(22), Emu(int(0.7 * EMU_PER_INCH)), Pt(3), fill=self.theme.accent)
        self._text(
            s, mx, int(self.H * 0.62), self.W - 2 * mx, Emu(int(1.6 * EMU_PER_INCH)),
            [[(title, 38, light, True)]], font=self.theme.font_display,
        )
        if subtitle:
            self._text(
                s, mx, self.H - Emu(int(1.0 * EMU_PER_INCH)), self.W - 2 * mx, Pt(40),
                [[(subtitle, 18, "E8E8EC", False)]],
            )
        return s

    def image_split(self, title, body, image_path=None, kicker="", image_side="right"):
        """Half image (fill-cropped), half text (title + bullet points). ``body``
        is a list of short strings. Without an image it falls back to a card-like
        text panel so the slide still works."""
        s = self._blank()
        half = self.W // 2
        mx = self.MX
        if image_side == "left":
            img_x, txt_x = 0, half + Pt(30)
        else:
            img_x, txt_x = half, mx
        txt_w = half - mx - Pt(30)
        if image_path:
            self._picture_fill(s, image_path, img_x, 0, half, self.H)
        else:  # no image → a surface panel keeps the composition balanced
            self._rect(s, img_x, 0, half, self.H, fill=self.theme.surface)
        top = Emu(int(1.1 * EMU_PER_INCH))
        if kicker:
            self._kicker(s, txt_x, top, txt_w, kicker)
            top = top + Emu(int(0.6 * EMU_PER_INCH))
        self._text(
            s, txt_x, top, txt_w, Emu(int(1.3 * EMU_PER_INCH)),
            [[(title, 28, self.theme.primary, True)]], font=self.theme.font_display,
        )
        by = top + Emu(int(1.4 * EMU_PER_INCH))
        for i, pt in enumerate(body):
            y = by + i * Emu(int(0.8 * EMU_PER_INCH))
            self._rect(s, txt_x, y + Pt(6), Pt(9), Pt(9), fill=self.theme.accent)
            self._text(
                s, txt_x + Pt(22), y, txt_w - Pt(22), Emu(int(0.75 * EMU_PER_INCH)),
                [[(pt, 14, self.theme.muted, False)]],
            )
        return s

    def save(self, path):
        self.prs.save(path)
        return path


if __name__ == "__main__":  # `python3 deckbuilder.py` prints the theme catalog
    print(theme_catalog())
