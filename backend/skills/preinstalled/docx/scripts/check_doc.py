"""check_doc.py — pre-delivery checker for a generated .docx.

A flowing document reflows in Word, so this is NOT pixel-overflow checking. It
validates structural invariants a future edit (or a sloppy generation) could
quietly break:

  ERROR (blocks delivery)
    - CJK text whose East-Asian font is not resolvable (run/style/docDefaults)
      -> Word substitutes a wrong font and CJK may render as tofu.
    - a table whose column widths overflow the text area (-> Word repairs/clips).
    - leftover placeholder text (TODO / lorem ipsum / "output.docx" / an
      un-replaced field placeholder that is not inside a real field).
    - an image wider than the text area (-> spills into the margin).
  WARNING (advisory)
    - "Normal-only" document: (almost) no paragraph uses a real style.
    - heading hierarchy skips a level (H1 -> H3).
    - low-contrast colored text on white.
    - an empty heading paragraph.

Usage:  python3 check_doc.py document.docx
Exits 1 if any ERROR is found.
"""

from __future__ import annotations

import sys

from docx import Document
from docx.oxml.ns import qn
from docx.shared import RGBColor

EMU_PER_INCH = 914400
CONTRAST_MIN = 4.5
PLACEHOLDERS = ("lorem ipsum", "todo", "tbd", "xxx", "output.docx", "placeholder text",
                "your text here", "[insert", "right-click → update field")


def _has_cjk(s: str) -> bool:
    return any(
        "　" <= c <= "鿿"  # CJK ideographs, Japanese kana, CJK symbols
        or "가" <= c <= "힣"  # Korean Hangul syllables
        or "＀" <= c <= "￯"  # full/half-width forms
        for c in s
    )


def _lum(rgb: RGBColor) -> float:
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * ch(rgb[0]) + 0.7152 * ch(rgb[1]) + 0.0722 * ch(rgb[2])


def _contrast(a: RGBColor, b: RGBColor) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _doc_default_eastasia(doc) -> str | None:
    el = doc.styles.element.find(qn("w:docDefaults"))
    if el is None:
        return None
    rpr = el.find(qn("w:rPrDefault"))
    rpr = rpr.find(qn("w:rPr")) if rpr is not None else None
    fonts = rpr.find(qn("w:rFonts")) if rpr is not None else None
    return fonts.get(qn("w:eastAsia")) if fonts is not None else None


def _style_eastasia(style) -> str | None:
    """Walk the basedOn chain looking for an eastAsia font on the style."""
    seen = set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        rpr = style.element.find(qn("w:rPr"))
        fonts = rpr.find(qn("w:rFonts")) if rpr is not None else None
        if fonts is not None and fonts.get(qn("w:eastAsia")):
            return fonts.get(qn("w:eastAsia"))
        style = style.base_style
    return None


def _run_eastasia(run) -> str | None:
    rpr = run._element.find(qn("w:rPr"))
    fonts = rpr.find(qn("w:rFonts")) if rpr is not None else None
    return fonts.get(qn("w:eastAsia")) if fonts is not None else None


def _is_field_placeholder(para) -> bool:
    """True if the paragraph's text comes from a real Word field (TOC/PAGE) —
    its placeholder text is legitimate (Word replaces it on update)."""
    return para._p.find(".//" + qn("w:fldChar")) is not None or para._p.find(
        ".//" + qn("w:instrText")
    ) is not None


def _cell_bg(cell) -> RGBColor:
    """The cell's shading fill (so white text on an accent header isn't flagged
    as low-contrast); defaults to white when the cell has no fill."""
    tcpr = cell._tc.find(qn("w:tcPr"))
    shd = tcpr.find(qn("w:shd")) if tcpr is not None else None
    fill = shd.get(qn("w:fill")) if shd is not None else None
    if fill and fill != "auto" and len(fill) == 6:
        try:
            return RGBColor.from_string(fill)
        except ValueError:
            pass
    return RGBColor(0xFF, 0xFF, 0xFF)


def _content_checks(para, default_ea, errors, warnings, bg=None):
    """Placeholder / CJK-font / contrast checks for one paragraph (used for body
    paragraphs AND table-cell paragraphs — Document.paragraphs skips cells).
    ``bg`` is the paragraph's background color for contrast (white by default)."""
    bg = bg or RGBColor(0xFF, 0xFF, 0xFF)
    txt = para.text.strip()
    if not txt:
        return
    if any(ph in txt.lower() for ph in PLACEHOLDERS) and not _is_field_placeholder(para):
        errors.append(f"placeholder text left in document: {txt[:50]!r}")
    if _has_cjk(txt):
        style_ea = _style_eastasia(para.style) or default_ea
        # check every run that actually carries CJK, not just runs[0]
        cjk_runs = [r for r in para.runs if _has_cjk(r.text)] or [None]
        for r in cjk_runs:
            ea = (_run_eastasia(r) if r is not None else None) or style_ea
            if not ea:
                bad = r.text.strip()[:40] if r is not None else txt[:40]
                errors.append(f"CJK without an East-Asian font (will tofu): {bad!r}")
                break
    for r in para.runs:
        col = r.font.color
        if col is not None and col.rgb is not None and col.type is not None and r.text.strip():
            c = _contrast(col.rgb, bg)
            if c < CONTRAST_MIN:
                warnings.append(
                    f"low contrast {c:.1f}:1 text #{col.rgb} on #{bg}: {r.text.strip()[:30]!r}"
                )
                break


def _block_section_widths(doc):
    """Text-area width of the section each table / inline image belongs to, as
    two lists parallel to doc.tables and doc.inline_shapes (document order). A
    doc may mix portrait + landscape sections; each block is checked against its
    OWN section, not a global max."""
    sec_w = [s.page_width - s.left_margin - s.right_margin for s in doc.sections]
    last = len(sec_w) - 1
    idx = 0
    table_w, image_w = [], []
    for child in doc.element.body.iterchildren():
        cur = sec_w[min(idx, last)]
        if child.tag == qn("w:tbl"):
            table_w.append(cur)
        elif child.tag == qn("w:p"):
            for _ in child.iter(qn("wp:inline")):
                image_w.append(cur)
            ppr = child.find(qn("w:pPr"))
            if ppr is not None and ppr.find(qn("w:sectPr")) is not None:
                idx += 1  # this paragraph closes the current section
    return table_w, image_w


def check(path: str):
    doc = Document(path)
    errors: list[str] = []
    warnings: list[str] = []

    table_w, image_w = _block_section_widths(doc)
    default_ea = _doc_default_eastasia(doc)

    # ---- paragraphs: styles, hierarchy, CJK, contrast, placeholders ----
    styled = 0
    total = 0
    last_heading_lvl = 0
    for p in doc.paragraphs:
        txt = p.text.strip()
        sname = p.style.name if p.style else "Normal"
        if txt:
            total += 1
            if sname != "Normal":
                styled += 1
        # heading hierarchy + empty heading
        if sname.startswith("Heading "):
            try:
                lvl = int(sname.split()[-1])
            except ValueError:
                lvl = 1
            if not txt:
                errors.append(f"empty heading ({sname}) — blank outline/TOC entry")
            if last_heading_lvl and lvl > last_heading_lvl + 1:
                warnings.append(
                    f"heading hierarchy skips H{last_heading_lvl}->H{lvl}: {txt[:40]!r}"
                )
            last_heading_lvl = lvl
        _content_checks(p, default_ea, errors, warnings)

    if total >= 5 and styled / max(total, 1) < 0.25:
        warnings.append(
            f"'Normal-only' document: only {styled}/{total} paragraphs use a real style "
            "(no headings/structure?)"
        )

    # ---- tables: width within ITS section's text area, non-empty ----
    for ti, tbl in enumerate(doc.tables, 1):
        limit = table_w[ti - 1] if ti - 1 < len(table_w) else max(table_w or [0])
        widths = []
        for cell in tbl.rows[0].cells:
            w = cell.width
            widths.append(int(w) if w is not None else 0)
        tw = sum(widths)
        if tw and limit and tw > limit * 1.02:
            errors.append(
                f"table {ti} width {tw / EMU_PER_INCH:.1f}in exceeds its section's "
                f"text area {limit / EMU_PER_INCH:.1f}in"
            )
        empties = sum(1 for row in tbl.rows for c in row.cells if not c.text.strip())
        if empties:
            warnings.append(f"table {ti} has {empties} empty cell(s)")
        # placeholder / CJK / contrast inside cells (Document.paragraphs skips them)
        for row in tbl.rows:
            for c in row.cells:
                bg = _cell_bg(c)
                for cp in c.paragraphs:
                    _content_checks(cp, default_ea, errors, warnings, bg=bg)

    # ---- images within ITS section's text area ----
    for i, shp in enumerate(doc.inline_shapes, 1):
        limit = image_w[i - 1] if i - 1 < len(image_w) else max(image_w or [0])
        if shp.width and limit and shp.width > limit * 1.02:
            errors.append(
                f"image {i} width {shp.width / EMU_PER_INCH:.1f}in exceeds its section's "
                f"text area {limit / EMU_PER_INCH:.1f}in"
            )

    return errors, warnings


def main(argv):
    if len(argv) < 2:
        print("usage: python3 check_doc.py document.docx")
        return 2
    errors, warnings = check(argv[1])
    n = len(Document(argv[1]).paragraphs)
    for w in warnings:
        print(f"  warn  {w}")
    for e in errors:
        print(f"  ERROR {e}")
    print(f"Checked {n} paragraphs: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
