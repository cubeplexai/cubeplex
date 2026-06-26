"""deckbuilder — themed python-pptx helpers for clean, editable 16:9 decks.

Clean-room: built on python-pptx (open) with the layout patterns proven by the
community MIT-0 pptx-generator and create-pptx skills, but redone to (a) use
only FREE fonts present in the sandbox image, (b) avoid emoji glyphs (which
render as tofu boxes in LibreOffice/non-emoji fonts) — accents are drawn shapes,
and (c) expose richer layouts (cover / agenda / cards / comparison / metrics /
closing). Pair with check_deck.py, which flags overflow / off-canvas / low
contrast before delivery.

Themes use widely-available libre fonts so text measures and renders the same
in the sandbox and in LibreOffice export:
  - Latin: "DejaVu Sans" / "Liberation Sans"
  - CJK:   "Noto Sans CJK SC" / "WenQuanYi Zen Hei"
"""

from __future__ import annotations

from dataclasses import dataclass

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
    """Set the Latin name and the East-Asian (CJK) typeface on a run.

    python-pptx's ``run.font.name`` only sets the Latin (``<a:latin>``) face;
    CJK glyphs use the ``<a:ea>`` face. Without setting ``ea``, Chinese text
    falls back to the Latin font (which has no CJK glyphs) and renders as tofu.
    """
    run.font.name = latin  # adds <a:latin>
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        existing = rPr.find(qn(tag))
        if existing is not None:
            rPr.remove(existing)
    ea = rPr.makeelement(qn("a:ea"), {"typeface": cjk})
    # In CT_TextCharacterProperties, <a:ea> must follow <a:latin>; appending at
    # the end can land it after later-ordered children and trip strict readers.
    latin_el = rPr.find(qn("a:latin"))
    if latin_el is not None:
        latin_el.addnext(ea)
    else:
        rPr.append(ea)


def _no_fill_spPr(parent):
    spPr = parent.makeelement(qn("c:spPr"), {})
    spPr.append(spPr.makeelement(qn("a:noFill"), {}))
    ln = spPr.makeelement(qn("a:ln"), {})
    ln.append(ln.makeelement(qn("a:noFill"), {}))
    spPr.append(ln)
    return spPr


def _transparent_chart(chart) -> None:
    """Give the chart-space and plot-area a no-fill background so the slide's
    theme color shows through instead of an opaque white box (which looks
    broken on dark themes).

    Schema order matters: in CT_ChartSpace ``<c:spPr>`` must come immediately
    after ``<c:chart>`` (before ``txPr``/``externalData``); in CT_PlotArea it is
    the last styling child (before any ``extLst``). Placing it elsewhere makes
    PowerPoint flag the chart as corrupt even though LibreOffice tolerates it.
    """
    cs = chart._chartSpace
    # chartSpace: spPr right after <c:chart>
    old = cs.find(qn("c:spPr"))
    if old is not None:
        cs.remove(old)
    chart_el = cs.find(qn("c:chart"))
    if chart_el is not None:
        chart_el.addnext(_no_fill_spPr(cs))
    # plotArea: spPr last, before any extLst
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
    name: str
    bg: str  # slide background
    surface: str  # card / panel fill
    primary: str  # headings on bg
    muted: str  # secondary text
    accent: str  # brand accent (rules, key numbers)
    on_accent: str  # text on accent fill
    font_latin: str
    font_cjk: str
    dark: bool


THEMES: dict[str, Theme] = {
    # Dark navy + teal — the aesthetic that scored best in eval (create-pptx / tobewin).
    "midnight": Theme(
        name="midnight",
        bg="0F141E",
        surface="1B2433",
        primary="F4F6FB",
        muted="9AA7BD",
        accent="00D2A0",
        on_accent="06231C",
        font_latin="Liberation Sans",
        font_cjk="Noto Sans CJK SC",
        dark=True,
    ),
    # Dark + warm amber — editorial/strategic (kimi-style palette, free fonts).
    "ember": Theme(
        name="ember",
        bg="14110E",
        surface="241E18",
        primary="F7F3EC",
        muted="B7A98F",
        accent="E8A13A",
        on_accent="241600",
        font_latin="Liberation Sans",
        font_cjk="Noto Sans CJK SC",
        dark=True,
    ),
    # Clean light — corporate/report.
    "daylight": Theme(
        name="daylight",
        bg="FFFFFF",
        surface="F2F5FA",
        primary="1E293B",
        muted="52607A",
        accent="2563EB",
        on_accent="FFFFFF",
        font_latin="Liberation Sans",
        font_cjk="Noto Sans CJK SC",
        dark=False,
    ),
    # Deep plum + magenta — creative / brand.
    "orchid": Theme(
        name="orchid",
        bg="161020",
        surface="241733",
        primary="F5EFFA",
        muted="B49FCB",
        accent="C264FF",
        on_accent="1B0A2E",
        font_latin="Liberation Sans",
        font_cjk="Noto Sans CJK SC",
        dark=True,
    ),
    # Warm paper + ink — academic / editorial light.
    "paper": Theme(
        name="paper",
        bg="FBF7F0",
        surface="F1E9DC",
        primary="2A2620",
        muted="6B6253",
        accent="B4622D",
        on_accent="FFFFFF",
        font_latin="Liberation Serif",
        font_cjk="Noto Serif CJK SC",
        dark=False,
    ),
}


class Deck:
    """16:9 deck builder. One method per slide archetype."""

    def __init__(self, theme: str = "midnight") -> None:
        self.theme = THEMES.get(theme, THEMES["midnight"])
        self.prs = Presentation()
        self.prs.slide_width = Emu(12192000)  # 13.333in
        self.prs.slide_height = Emu(6858000)  # 7.5in
        self.W = self.prs.slide_width
        self.H = self.prs.slide_height

    # ---- low-level helpers -------------------------------------------------

    def _blank(self):
        slide = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        bg = slide.background.fill
        bg.solid()
        bg.fore_color.rgb = _rgb(self.theme.bg)
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
        self, slide, x, y, w, h, runs, *, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, wrap=True
    ):
        """runs: list of paragraphs; each paragraph is list of (text, size_pt,
        color_hex, bold) tuples (a paragraph can have multiple styled runs)."""
        box = slide.shapes.add_textbox(x, y, w, h)
        tf = box.text_frame
        tf.word_wrap = wrap
        tf.vertical_anchor = anchor
        tf.margin_left = 0
        tf.margin_right = 0
        tf.margin_top = 0
        tf.margin_bottom = 0
        for i, para in enumerate(runs):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            for txt, size, color, bold in para:
                r = p.add_run()
                r.text = txt
                r.font.size = Pt(size)
                r.font.bold = bold
                r.font.color.rgb = _rgb(color)
                _set_run_fonts(r, self.theme.font_latin, self.theme.font_cjk)
        return box

    def _kicker(self, slide, x, y, w, text):
        # small uppercase label + short accent rule under it
        self._text(slide, x, y, w, Pt(20), [[(text.upper(), 12, self.theme.accent, True)]])
        self._rect(
            slide, x, y + Pt(22), Emu(int(0.7 * EMU_PER_INCH)), Pt(3), fill=self.theme.accent
        )

    # ---- slide archetypes --------------------------------------------------

    def cover(self, title, subtitle="", kicker="", meta=""):
        s = self._blank()
        mx = Emu(int(0.9 * EMU_PER_INCH))
        if kicker:
            self._kicker(s, mx, Emu(int(2.1 * EMU_PER_INCH)), self.W - 2 * mx, kicker)
        self._text(
            s,
            mx,
            Emu(int(2.7 * EMU_PER_INCH)),
            self.W - 2 * mx,
            Emu(int(2.2 * EMU_PER_INCH)),
            [[(title, 40, self.theme.primary, True)]],
        )
        if subtitle:
            self._text(
                s,
                mx,
                Emu(int(4.7 * EMU_PER_INCH)),
                self.W - 2 * mx,
                Emu(int(1.0 * EMU_PER_INCH)),
                [[(subtitle, 20, self.theme.muted, False)]],
            )
        if meta:
            self._text(
                s,
                mx,
                self.H - Emu(int(0.8 * EMU_PER_INCH)),
                self.W - 2 * mx,
                Pt(24),
                [[(meta, 12, self.theme.muted, False)]],
            )
        return s

    def _header(self, slide, title, kicker=""):
        mx = Emu(int(0.9 * EMU_PER_INCH))
        top = Emu(int(0.7 * EMU_PER_INCH))
        if kicker:
            self._kicker(slide, mx, top, self.W - 2 * mx, kicker)
            top = top + Emu(int(0.55 * EMU_PER_INCH))
        self._text(
            slide,
            mx,
            top,
            self.W - 2 * mx,
            Emu(int(0.9 * EMU_PER_INCH)),
            [[(title, 30, self.theme.primary, True)]],
        )
        return mx

    def agenda(self, title, items, kicker="Overview"):
        s = self._blank()
        mx = self._header(s, title, kicker)
        top = Emu(int(2.3 * EMU_PER_INCH))
        gap = Emu(int(0.95 * EMU_PER_INCH))
        for i, it in enumerate(items):
            y = top + i * gap
            self._rect(s, mx, y, Pt(34), Pt(34), fill=self.theme.surface, rounded=True)
            self._text(
                s,
                mx,
                y,
                Pt(34),
                Pt(34),
                [[(f"{i + 1:02d}", 13, self.theme.accent, True)]],
                align=PP_ALIGN.CENTER,
                anchor=MSO_ANCHOR.MIDDLE,
            )
            self._text(
                s,
                mx + Pt(48),
                y,
                self.W - 2 * mx - Pt(48),
                Pt(40),
                [[(it, 18, self.theme.primary, True)]],
                anchor=MSO_ANCHOR.MIDDLE,
            )
        return s

    def cards(self, title, cards, kicker=""):
        """cards: list of (label, body). Lays out a responsive grid (2 cols)."""
        s = self._blank()
        mx = self._header(s, title, kicker)
        cols = 2
        gut = Emu(int(0.4 * EMU_PER_INCH))
        cw = (self.W - 2 * mx - (cols - 1) * gut) // cols
        ch = Emu(int(1.9 * EMU_PER_INCH))
        top = Emu(int(2.3 * EMU_PER_INCH))
        rgap = Emu(int(0.35 * EMU_PER_INCH))
        for i, (label, body) in enumerate(cards):
            r, c = divmod(i, cols)
            x = mx + c * (cw + gut)
            y = top + r * (ch + rgap)
            self._rect(s, x, y, cw, ch, fill=self.theme.surface, rounded=True)
            self._rect(s, x, y, Pt(4), ch, fill=self.theme.accent)  # accent edge
            pad = Pt(16)
            self._text(
                s, x + pad, y + pad, cw - 2 * pad, Pt(30), [[(label, 17, self.theme.primary, True)]]
            )
            self._text(
                s,
                x + pad,
                y + pad + Pt(34),
                cw - 2 * pad,
                ch - pad - Pt(40),
                [[(body, 12.5, self.theme.muted, False)]],
            )
        return s

    def comparison(self, title, headers, rows, kicker="", highlight_col=None):
        """A real Word/PPTX table. headers: [dim, A, B]; rows: [[d,a,b],...]."""
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
                self.theme.accent if (highlight_col == ci) else self.theme.primary
            )
            self._cell(
                cell, htxt, 13, self.theme.on_accent if highlight_col == ci else self.theme.bg, True
            )
        for ri, row in enumerate(rows, start=1):
            for ci, val in enumerate(row):
                cell = tbl.cell(ri, ci)
                cell.fill.solid()
                cell.fill.fore_color.rgb = _rgb(self.theme.surface if ri % 2 else self.theme.bg)
                col = (
                    self.theme.accent
                    if (ci == highlight_col)
                    else (self.theme.primary if ci == 0 else self.theme.muted)
                )
                self._cell(cell, str(val), 12, col, ci == 0 or ci == highlight_col)
        return s

    def _cell(self, cell, text, size, color, bold):
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.margin_left = Pt(10)
        cell.margin_right = Pt(10)
        tf = cell.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = text
        for r in p.runs:
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.color.rgb = _rgb(color)
            _set_run_fonts(r, self.theme.font_latin, self.theme.font_cjk)

    def metrics(self, title, items, kicker="By the numbers"):
        """items: list of (big_number, label, sub). Big numbers in accent."""
        s = self._blank()
        mx = self._header(s, title, kicker)
        n = len(items)
        gut = Emu(int(0.5 * EMU_PER_INCH))
        cw = (self.W - 2 * mx - (n - 1) * gut) // n
        top = Emu(int(2.9 * EMU_PER_INCH))
        for i, (big, label, sub) in enumerate(items):
            x = mx + i * (cw + gut)
            self._text(s, x, top, cw, Pt(80), [[(big, 54, self.theme.accent, True)]])
            self._text(s, x, top + Pt(78), cw, Pt(34), [[(label, 16, self.theme.primary, True)]])
            if sub:
                self._text(
                    s, x, top + Pt(110), cw, Pt(60), [[(sub, 11.5, self.theme.muted, False)]]
                )
        return s

    def chart(
        self, title, categories, values, *, series_name="", kicker="", number_format="0", caption=""
    ):
        """A native, editable column chart in the theme accent color.

        categories: list[str]; values: list[number] (same length). Single
        series, data labels on, legend + value axis hidden for a clean look.
        """
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
        # bars in accent + numeric data labels (digits → Latin font is fine)
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
        # category axis: muted labels, no tick marks; value axis hidden
        cat = chart.category_axis
        cat.tick_labels.font.size = Pt(12)
        cat.tick_labels.font.color.rgb = _rgb(self.theme.muted)
        cat.major_tick_mark = XL_TICK_MARK.NONE
        cat.format.line.color.rgb = _rgb(self.theme.muted)
        val = chart.value_axis
        val.visible = False
        val.has_major_gridlines = False
        _transparent_chart(chart)  # let the theme bg show through
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
            self._rect(s, mx, y + Pt(7), Pt(10), Pt(10), fill=self.theme.accent)  # bullet mark
            self._text(
                s,
                mx + Pt(24),
                y,
                self.W - 2 * mx - Pt(24),
                Pt(70),
                [[(t, 16, self.theme.primary, False)]],
            )
        if cta:
            bar_h = Emu(int(0.95 * EMU_PER_INCH))
            y = self.H - bar_h - Emu(int(0.5 * EMU_PER_INCH))
            self._rect(s, mx, y, self.W - 2 * mx, bar_h, fill=self.theme.accent, rounded=True)
            self._text(
                s,
                mx,
                y,
                self.W - 2 * mx,
                bar_h,
                [[(cta, 18, self.theme.on_accent, True)]],
                align=PP_ALIGN.CENTER,
                anchor=MSO_ANCHOR.MIDDLE,
            )
        return s

    def save(self, path):
        self.prs.save(path)
        return path
