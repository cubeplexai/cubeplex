"""docbuilder — a themed python-docx toolkit for clean, well-structured .docx.

Clean-room: built on python-docx (open) plus the manual OOXML that python-docx
does not emit itself — East-Asian (<w:eastAsia>) fonts on runs, PAGE-field page
numbers, a real updatable Table of Contents, three-line tables with a correct
grid, heading outline levels, and section page setup. Pair with check_doc.py,
which validates structure/styles/tables/CJK/contrast before delivery.

Design model
------------
A *style set* (theme) fixes the whole look: a font pairing (heading/body, Latin
and CJK), a type scale (title/H1/H2/H3/body/caption), an accent color, spacing,
and margins. Pick one per document. *Building-block* methods (cover, heading,
body, bullets, table, figure, quote, toc, …) emit correctly-styled content so
the result is consistent and passes the checker.

Fonts assumed present in the sandbox image (see misc/sandbox-image): Latin —
Liberation Serif/Sans (Times/Arial-metric), Inter, Sorts Mill Goudy; CJK — Noto
Sans/Serif CJK SC, LXGW WenKai.
"""

from __future__ import annotations

from dataclasses import dataclass

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Pt, RGBColor

# ---- unit helpers ----------------------------------------------------------
EMU_PER_INCH = 914400


def _twips(pt: float) -> str:
    return str(int(round(pt * 20)))  # 1pt = 20 twips/dxa


def _hp(pt: float) -> str:
    return str(int(round(pt * 2)))  # half-points for w:sz


def _rgb(hex_str: str) -> RGBColor:
    return RGBColor.from_string(hex_str.lstrip("#").upper())


_CN = " 一二三四五六七八九十"


def _cn_num(n: int) -> str:
    if n <= 10:
        return _CN[n]
    if n < 20:
        return "十" + _CN[n - 10]
    if n < 30:
        return "二十" + (_CN[n - 20] if n > 20 else "")
    return str(n)


# ---- style set (theme) -----------------------------------------------------
@dataclass
class StyleSet:
    name: str
    profile: str  # corporate / academic / report / contract / ...
    blurb: str
    font_heading: str
    font_body: str
    font_cjk_h: str  # CJK heading face (<w:eastAsia>)
    font_cjk_b: str  # CJK body face
    sz_title: float
    sz_h1: float
    sz_h2: float
    sz_h3: float
    sz_body: float
    sz_caption: float
    color_heading: str
    color_body: str
    color_accent: str
    line: float  # body line spacing multiple
    after: float  # paragraph space-after, pt
    margin_in: float
    indent_first: bool  # first-line indent (CJK/academic) vs spacing
    number_headings: bool
    a4: bool = False  # A4 page + GB/T 9704 公文 margins instead of uniform margin_in
    line_exact_pt: float = 0.0  # fixed (exact) line spacing in pt; 0 = use `line`
    font_kai: str = "LXGW WenKai"  # 楷体 face for the 公文 second-level heading


THEMES: dict[str, StyleSet] = {
    # Clean business default — sans headings + serif body, blue accent, spacing.
    "corporate": StyleSet(
        "corporate", "business", "Reports, proposals, briefs — modern & clean.",
        font_heading="Inter", font_body="Liberation Serif",
        font_cjk_h="Noto Sans CJK SC", font_cjk_b="Noto Serif CJK SC",
        sz_title=26, sz_h1=18, sz_h2=14, sz_h3=12, sz_body=11, sz_caption=9,
        color_heading="1F3864", color_body="333333", color_accent="2F5496",
        line=1.15, after=8, margin_in=1.0, indent_first=False, number_headings=False,
    ),
    # Academic paper — serif throughout, numbered headings, first-line indent.
    "academic": StyleSet(
        "academic", "academic", "Papers, theses, research — formal serif.",
        font_heading="Liberation Serif", font_body="Liberation Serif",
        font_cjk_h="Noto Serif CJK SC", font_cjk_b="Noto Serif CJK SC",
        sz_title=22, sz_h1=16, sz_h2=14, sz_h3=12, sz_body=12, sz_caption=10,
        color_heading="000000", color_body="000000", color_accent="000000",
        line=1.5, after=0, margin_in=1.0, indent_first=True, number_headings=True,
    ),
    # Internal report — all sans, accent headings, compact.
    "report": StyleSet(
        "report", "report", "Status reports, memos, internal docs — sans, compact.",
        font_heading="Inter", font_body="Liberation Sans",
        font_cjk_h="Noto Sans CJK SC", font_cjk_b="Noto Sans CJK SC",
        sz_title=22, sz_h1=15, sz_h2=12.5, sz_h3=11, sz_body=10.5, sz_caption=9,
        color_heading="1F4E79", color_body="2A2A2A", color_accent="2E75B6",
        line=1.15, after=6, margin_in=0.9, indent_first=False, number_headings=False,
    ),
    # Chinese official document (公文, GB/T 9704-2012). Free font substitutes:
    # 仿宋/小标宋 -> Noto Serif CJK SC (Song), 黑体 -> Noto Sans CJK SC,
    # 楷体 -> LXGW WenKai. Strict 仿宋_GB2312/小标宋体 are proprietary.
    "official": StyleSet(
        "official", "official", "公文 (GB/T 9704)：A4、三号仿宋正文、黑体/楷体标题层级。",
        font_heading="Noto Sans CJK SC", font_body="Noto Serif CJK SC",
        font_cjk_h="Noto Sans CJK SC", font_cjk_b="Noto Serif CJK SC",
        sz_title=22, sz_h1=16, sz_h2=16, sz_h3=16, sz_body=16, sz_caption=14,
        color_heading="000000", color_body="000000", color_accent="000000",
        line=1.0, after=0, margin_in=1.0, indent_first=True, number_headings=True,
        a4=True, line_exact_pt=28, font_kai="LXGW WenKai",
    ),
    # Chinese general — Hei headings + Song body, first-line indent, 1.5 line.
    "chinese": StyleSet(
        "chinese", "cjk", "中文报告/文稿 — 黑体标题 + 宋体正文，首行缩进。",
        font_heading="Inter", font_body="Liberation Serif",
        font_cjk_h="Noto Sans CJK SC", font_cjk_b="Noto Serif CJK SC",
        sz_title=22, sz_h1=16, sz_h2=15, sz_h3=14, sz_body=12, sz_caption=10.5,
        color_heading="1F3864", color_body="000000", color_accent="2F5496",
        line=1.5, after=0, margin_in=1.1, indent_first=True, number_headings=False,
    ),
}


def theme_catalog() -> str:
    rows = [
        f"- {t.name:10} [{t.profile:9}] {t.blurb}  (heading={t.font_heading}, body={t.font_body})"
        for t in THEMES.values()
    ]
    return "Available style sets:\n" + "\n".join(rows)


class Doc:
    """A themed .docx document. Build top to bottom; one style set per doc."""

    def __init__(self, theme: str = "corporate", lang: str = "en-US") -> None:
        self.t = THEMES.get(theme, THEMES["corporate"])
        self.lang = lang
        self.doc = Document()
        self._page_setup()
        self._doc_defaults()
        self._define_styles()
        self._has_toc = False
        self._numbered = False

    # ---- foundations -------------------------------------------------------

    def _page_setup(self) -> None:
        from docx.shared import Mm

        sec = self.doc.sections[0]
        if self.t.a4:  # GB/T 9704 公文: A4 + fixed margins
            sec.page_width, sec.page_height = Mm(210), Mm(297)
            sec.top_margin, sec.bottom_margin = Mm(37), Mm(35)
            sec.left_margin, sec.right_margin = Mm(28), Mm(26)
        else:
            m = Emu(int(self.t.margin_in * EMU_PER_INCH))
            sec.top_margin = sec.bottom_margin = m
            sec.left_margin = sec.right_margin = m
        self._text_width_emu = sec.page_width - sec.left_margin - sec.right_margin

    def _doc_defaults(self) -> None:
        """Set the 4-slot rFonts + lang in docDefaults so CJK is correct
        document-wide (python-docx only touches ascii/hAnsi)."""
        styles = self.doc.styles.element
        rpr = styles.find(qn("w:docDefaults"))
        rpr_def = rpr.find(qn("w:rPrDefault")).find(qn("w:rPr"))
        fonts = rpr_def.find(qn("w:rFonts"))
        if fonts is None:
            fonts = OxmlElement("w:rFonts")
            rpr_def.insert(0, fonts)
        fonts.set(qn("w:ascii"), self.t.font_body)
        fonts.set(qn("w:hAnsi"), self.t.font_body)
        fonts.set(qn("w:eastAsia"), self.t.font_cjk_b)
        lang = OxmlElement("w:lang")
        lang.set(qn("w:val"), self.lang)
        lang.set(qn("w:eastAsia"), "zh-CN" if self.lang.startswith("zh") else "zh-CN")
        rpr_def.append(lang)

    def _set_run_fonts(self, run, latin: str, cjk: str) -> None:
        rpr = run._element.get_or_add_rPr()
        fonts = rpr.find(qn("w:rFonts"))
        if fonts is None:
            fonts = OxmlElement("w:rFonts")
            rpr.insert(0, fonts)
        fonts.set(qn("w:ascii"), latin)
        fonts.set(qn("w:hAnsi"), latin)
        fonts.set(qn("w:eastAsia"), cjk)

    def _define_styles(self) -> None:
        t = self.t
        official = t.profile == "official"
        # 公文 title is 小标宋 (Song); other profiles use the heading face.
        self._style_para(
            "Title", t.font_body if official else t.font_heading,
            t.font_cjk_b if official else t.font_cjk_h, t.sz_title, True,
            t.color_heading, after=8, before=0, align="center",
            line_exact_pt=t.line_exact_pt,
        )
        self._style_para("Subtitle", t.font_body, t.font_cjk_b, t.sz_body + 2, False,
                         t.color_accent, after=16, before=0, align="center")
        if official:
            # 公文 ladder: 一、黑体 / （一）楷体 / 1．仿宋加粗 — all 三号, exact line.
            for lvl, cjk, bold, indent in (
                (1, t.font_cjk_h, True, False),
                (2, t.font_kai, False, True),
                (3, t.font_cjk_b, True, True),
            ):
                self._style_para(
                    f"Heading {lvl}", t.font_body, cjk, t.sz_h1, bold, t.color_heading,
                    after=0, before=0, outline=lvl - 1, keep_next=True,
                    indent_first=indent, line_exact_pt=t.line_exact_pt,
                )
        else:
            for lvl, sz in ((1, t.sz_h1), (2, t.sz_h2), (3, t.sz_h3)):
                self._style_para(
                    f"Heading {lvl}", t.font_heading, t.font_cjk_h, sz, True,
                    t.color_heading, after=6, before=14 if lvl == 1 else 10,
                    outline=lvl - 1, keep_next=True,
                )
        self._style_para("Body", t.font_body, t.font_cjk_b, t.sz_body, False,
                         t.color_body, after=t.after, before=0,
                         line=t.line, indent_first=t.indent_first, justify=t.t_cjk(),
                         line_exact_pt=t.line_exact_pt)
        # CJK has no true italic — Word fakes an ugly slant — so skip it there.
        ital = t.profile not in ("cjk", "official")
        self._style_para("Caption", t.font_body, t.font_cjk_b, t.sz_caption, False,
                         "666666", after=10, before=4, align="center", italic=ital)
        self._style_para("Quote", t.font_body, t.font_cjk_b, t.sz_body, False,
                         "555555", after=10, before=10, indent_left=28, italic=ital)
        # Match the bullet/number list styles to the body (size/color/spacing/CJK
        # font) — otherwise list items keep Word's default List Bullet look.
        for lname in ("List Bullet", "List Number"):
            self._style_para(lname, t.font_body, t.font_cjk_b, t.sz_body, False,
                             t.color_body, after=2, before=0, line=t.line,
                             line_exact_pt=t.line_exact_pt)
        # Heading-look label for the TOC itself — NO outlineLvl, so the TOC field
        # does not list "Contents" as its own first entry.
        self._style_para("TOC Label", t.font_heading, t.font_cjk_h, t.sz_h1, True,
                         t.color_heading, after=8, before=10)

    def _style_para(self, name, latin, cjk, sz, bold, color, *, after=8, before=0,
                    line=1.0, align=None, italic=False, outline=None, keep_next=False,
                    indent_first=False, indent_left=0.0, justify=False, line_exact_pt=0.0):
        styles = self.doc.styles
        try:
            st = styles[name]
        except KeyError:
            from docx.enum.style import WD_STYLE_TYPE

            st = styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        st.font.name = latin
        st.font.size = Pt(sz)
        st.font.bold = bold
        st.font.italic = italic
        st.font.color.rgb = _rgb(color)
        rpr = st.element.get_or_add_rPr()
        fonts = rpr.find(qn("w:rFonts"))
        if fonts is None:
            fonts = OxmlElement("w:rFonts")
            rpr.insert(0, fonts)
        fonts.set(qn("w:ascii"), latin)
        fonts.set(qn("w:hAnsi"), latin)
        fonts.set(qn("w:eastAsia"), cjk)
        ppr = st.element.get_or_add_pPr()

        def _reset(tag):  # built-in styles already have these singletons
            for old in ppr.findall(qn(tag)):
                ppr.remove(old)

        _reset("w:spacing")
        sp = OxmlElement("w:spacing")
        sp.set(qn("w:before"), _twips(before))
        sp.set(qn("w:after"), _twips(after))
        if line_exact_pt:
            sp.set(qn("w:line"), _twips(line_exact_pt))
            sp.set(qn("w:lineRule"), "exact")
        elif line and line != 1.0:
            sp.set(qn("w:line"), str(int(line * 240)))
            sp.set(qn("w:lineRule"), "auto")
        ppr.append(sp)
        if align == "center":
            st.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif justify:
            st.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        if indent_first or indent_left:
            _reset("w:ind")
            ind = OxmlElement("w:ind")
            if indent_first:
                ind.set(qn("w:firstLineChars"), "200")
            if indent_left:
                ind.set(qn("w:left"), _twips(indent_left))
            ppr.append(ind)
        if outline is not None:
            _reset("w:outlineLvl")
            ol = OxmlElement("w:outlineLvl")
            ol.set(qn("w:val"), str(outline))
            ppr.append(ol)
        if keep_next:
            _reset("w:keepNext")
            ppr.append(OxmlElement("w:keepNext"))
        return st

    # ---- building blocks ---------------------------------------------------

    def cover(self, title, subtitle="", meta=""):
        self.doc.add_paragraph(title, style="Title")
        if subtitle:
            self.doc.add_paragraph(subtitle, style="Subtitle")
        if meta:
            p = self.doc.add_paragraph(meta, style="Subtitle")
            p.runs[0].font.size = Pt(self.t.sz_body)
        return self

    def heading(self, text, level=1, numbered=True):
        """A heading. For numbered style sets (academic / 公文) the number is
        auto-applied; pass ``numbered=False`` for unnumbered front matter
        (Abstract, Acknowledgements, References)."""
        level = min(max(level, 1), 3)
        if numbered and self.t.profile == "official":
            text = self._gw_number(level) + text
        elif numbered and self.t.number_headings:
            text = self._west_number(level) + text
        self.doc.add_paragraph(text, style=f"Heading {level}")
        return self

    def _west_number(self, level: int) -> str:
        """Decimal heading numbering 1 / 1.1 / 1.1.1 for numbered style sets."""
        if not hasattr(self, "_wc"):
            self._wc = [0, 0, 0]
        self._wc[level - 1] += 1
        for i in range(level, 3):
            self._wc[i] = 0
        return ".".join(str(self._wc[i]) for i in range(level)) + " "

    def _gw_number(self, level: int) -> str:
        """公文 heading ladder: 一、 / （一） / 1． (auto-incrementing per level)."""
        if not hasattr(self, "_gwc"):
            self._gwc = [0, 0, 0]
        self._gwc[level - 1] += 1
        for i in range(level, 3):
            self._gwc[i] = 0
        n = self._gwc[level - 1]
        return {1: f"{_cn_num(n)}、", 2: f"（{_cn_num(n)}）", 3: f"{n}．"}[level]

    def body(self, text):
        self.doc.add_paragraph(text, style="Body")
        return self

    def bullets(self, items):
        for it in items:
            p = self.doc.add_paragraph(str(it), style="List Bullet")
            self._apply_fonts(p)
        return self

    def numbered(self, items):
        for it in items:
            p = self.doc.add_paragraph(str(it))
            p.style = self.doc.styles["List Number"]
            self._apply_fonts(p)
        return self

    def quote(self, text, attribution=""):
        self.doc.add_paragraph(text, style="Quote")
        if attribution:
            p = self.doc.add_paragraph("— " + attribution, style="Quote")
            p.runs[0].font.italic = False
        return self

    def _apply_fonts(self, paragraph):
        for r in paragraph.runs:
            self._set_run_fonts(r, self.t.font_body, self.t.font_cjk_b)

    def table(self, headers, rows, caption=""):
        """Three-line table sized to the text width, accent header."""
        tbl = self.doc.add_table(rows=1, cols=len(headers))
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
        col_w = int(self._text_width_emu / len(headers))
        # grid
        for ci, h in enumerate(headers):
            cell = tbl.rows[0].cells[ci]
            cell.width = Emu(col_w)
            self._cell_text(cell, str(h), bold=True, color="FFFFFF")
            self._cell_shade(cell, self.t.color_accent)
        for row in rows:
            cells = tbl.add_row().cells
            for ci, val in enumerate(row):
                cells[ci].width = Emu(col_w)
                self._cell_text(cells[ci], str(val), bold=ci == 0)
        self._three_line_borders(tbl)
        self._repeat_header(tbl)
        if caption:
            self.doc.add_paragraph(caption, style="Caption")
        return self

    def _cell_text(self, cell, text, *, bold=False, color=None):
        cell.text = ""
        p = cell.paragraphs[0]
        run = p.add_run(text)
        run.font.size = Pt(self.t.sz_body - 0.5)
        run.font.bold = bold
        if color:
            run.font.color.rgb = _rgb(color)
        self._set_run_fonts(run, self.t.font_body, self.t.font_cjk_b)
        tcpr = cell._tc.get_or_add_tcPr()
        mar = OxmlElement("w:tcMar")
        for side, v in (("top", 60), ("bottom", 60), ("start", 110), ("end", 110)):
            m = OxmlElement(f"w:{side}")
            m.set(qn("w:w"), str(v))
            m.set(qn("w:type"), "dxa")
            mar.append(m)
        tcpr.append(mar)

    def _cell_shade(self, cell, hex_color):
        tcpr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:fill"), hex_color.lstrip("#"))
        tcpr.append(shd)

    def _three_line_borders(self, tbl):
        tblpr = tbl._tbl.tblPr
        borders = OxmlElement("w:tblBorders")
        for edge, sz in (("top", 12), ("bottom", 12), ("insideH", 0), ("insideV", 0),
                         ("left", 0), ("right", 0)):
            b = OxmlElement(f"w:{edge}")
            b.set(qn("w:val"), "single" if sz else "nil")
            if sz:
                b.set(qn("w:sz"), str(sz))
                b.set(qn("w:color"), self.t.color_heading.lstrip("#"))
            borders.append(b)
        tblpr.append(borders)
        # thin line under the header row
        hr = tbl.rows[0]
        for cell in hr.cells:
            tcpr = cell._tc.get_or_add_tcPr()
            tcb = OxmlElement("w:tcBorders")
            bot = OxmlElement("w:bottom")
            bot.set(qn("w:val"), "single")
            bot.set(qn("w:sz"), "6")
            bot.set(qn("w:color"), self.t.color_heading.lstrip("#"))
            tcb.append(bot)
            tcpr.append(tcb)

    def _repeat_header(self, tbl):
        trpr = tbl.rows[0]._tr.get_or_add_trPr()
        trpr.append(OxmlElement("w:tblHeader"))

    def figure(self, image_path, caption="", width_in=None):
        from PIL import Image

        iw, ih = Image.open(image_path).size
        max_w = self._text_width_emu
        w = Emu(int(width_in * EMU_PER_INCH)) if width_in else Emu(min(max_w, iw * 9525))
        if w > max_w:
            w = Emu(int(max_w))
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(image_path, width=w)
        if caption:
            self.doc.add_paragraph(caption, style="Caption")
        return self

    def page_break(self):
        self.doc.add_page_break()
        return self

    def toc(self, title="Contents"):
        """Insert an updatable Table of Contents field (Word refreshes on open).
        The title uses a non-outline style so it does not list itself in the TOC."""
        if title:
            self.doc.add_paragraph(title, style="TOC Label")
        p = self.doc.add_paragraph()
        run = p.add_run()
        self._field(run, r'TOC \o "1-3" \h \z \u', cached="Right-click → Update Field")
        self._set_update_fields()
        self._has_toc = True
        return self

    def page_numbers(self, fmt="{PAGE}"):
        """Centered page number in the footer. ``fmt`` may wrap the number with
        text, e.g. ``"Page {PAGE}"`` or ``"第 {PAGE} 页"`` ; the ``{PAGE}`` token
        becomes the live field."""
        footer = self.doc.sections[0].footer
        footer.is_linked_to_previous = False
        p = footer.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER

        def _styled(run):
            self._set_run_fonts(run, self.t.font_body, self.t.font_cjk_b)
            run.font.size = Pt(self.t.sz_caption)
            run.font.color.rgb = _rgb("808080")

        before, sep, after = fmt.partition("{PAGE}")
        if before:
            _styled(p.add_run(before))
        run = p.add_run()
        self._field(run, "PAGE", cached="1")
        _styled(run)
        if sep and after:
            _styled(p.add_run(after))
        return self

    def _field(self, run, instr, cached=""):
        r = run._element
        begin = OxmlElement("w:fldChar")
        begin.set(qn("w:fldCharType"), "begin")
        instr_el = OxmlElement("w:instrText")
        instr_el.set(qn("xml:space"), "preserve")
        instr_el.text = f" {instr} "
        sep = OxmlElement("w:fldChar")
        sep.set(qn("w:fldCharType"), "separate")
        t = OxmlElement("w:t")
        t.text = cached
        end = OxmlElement("w:fldChar")
        end.set(qn("w:fldCharType"), "end")
        for el in (begin, instr_el, sep, t, end):
            r.append(el)

    def _set_update_fields(self):
        settings = self.doc.settings.element
        if settings.find(qn("w:updateFields")) is None:
            uf = OxmlElement("w:updateFields")
            uf.set(qn("w:val"), "true")
            settings.append(uf)

    def section_break(self, landscape=False):
        """Start a new page section. python-docx inherits the previous section's
        geometry, so set orientation AND ensure the page dims match it in both
        directions (a non-landscape break after a landscape one returns to
        portrait)."""
        from docx.enum.section import WD_ORIENT

        sec = self.doc.add_section(WD_SECTION.NEW_PAGE)
        w, h = sec.page_width, sec.page_height
        if landscape:
            sec.orientation = WD_ORIENT.LANDSCAPE
            if w < h:
                sec.page_width, sec.page_height = h, w
        else:
            sec.orientation = WD_ORIENT.PORTRAIT
            if w > h:
                sec.page_width, sec.page_height = h, w
        # tables/figures added after this use the new section's text width
        self._text_width_emu = sec.page_width - sec.left_margin - sec.right_margin
        return self

    def save(self, path):
        import os

        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.doc.save(path)
        return path


def _styleset_t_cjk(self):  # helper bound below
    return self.profile == "cjk" or self.profile == "academic"


StyleSet.t_cjk = _styleset_t_cjk  # justify CJK/academic body


if __name__ == "__main__":
    print(theme_catalog())
