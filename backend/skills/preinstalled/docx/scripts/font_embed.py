"""font_embed.py — embed (subset, obfuscated) fonts into a finished .docx.

Word/WPS/LibreOffice substitute a fallback font when a named font is missing
on the opening machine — CJK text then renders as tofu (□). Embedding the
actual font bytes into the package makes the document render identically
everywhere. This module post-processes a saved .docx:

  collect fonts the document uses (theme faces, regular + bold actually present)
  -> resolve each to a file via fontconfig (fc-match), extracting the right
     face from a .ttc collection
  -> subset to the characters the document contains (CJK fonts are ~16 MB whole;
     a subset is a few hundred KB)
  -> obfuscate to Word's .odttf format (XOR the first 32 bytes with the fontKey)
  -> write the font parts + fontTable.xml + rels, patch [Content_Types].xml and
     settings.xml (embedTrueTypeFonts / saveSubsetFonts), then rewrite the zip
     cleanly (no duplicate parts).

Only OFL / liberally-licensed fonts are bundled in the sandbox image
(Noto, LXGW WenKai, Inter, Liberation) — all permit embedding.
"""

from __future__ import annotations

import io
import re
import subprocess
import zipfile

from docx import Document

R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
FONT_REL = R + "/font"
ODTTF_CT = "application/vnd.openxmlformats-officedocument.obfuscatedFont"
FONTTABLE_CT = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml"
)
# Always-present punctuation so subset fonts cover structure chars too.
_ALWAYS = set("0123456789 .,:;()[]%-—…·、，。：；（）【】《》「」？！０-９")

# Deterministic fontKey GUIDs (no Math.random); one per embedded face.
_KEYS = [f"{{B0C5E1A2-{i:04X}-4A1A-9C01-D0CFFFFFFFFF}}" for i in range(256)]


# ---- obfuscation -----------------------------------------------------------
def _mask(guid: str) -> bytes:
    h = guid.replace("{", "").replace("}", "").replace("-", "")
    return bytes.fromhex(h)[::-1]


def obfuscate(font: bytes, guid: str) -> bytes:
    """Word's .odttf scheme: XOR the first 32 bytes with the 16-byte key twice."""
    out = bytearray(font)
    m = _mask(guid)
    for i in range(min(32, len(out))):
        out[i] ^= m[i % 16]
    return bytes(out)


# ---- font file resolution --------------------------------------------------
def _fc_match(name: str, bold: bool = False) -> str | None:
    """Resolve a font family name to a file path via fontconfig."""
    pat = f"{name}:bold" if bold else name
    try:
        out = subprocess.run(
            ["fc-match", "-f", "%{file}", pat],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def _face_index_for(path: str, family: str) -> int | None:
    """For a .ttc collection, find the index of the face matching `family`.
    Returns None for a single-face .ttf/.otf."""
    if not path.lower().endswith(".ttc"):
        return None
    from fontTools.ttLib import TTCollection

    coll = TTCollection(path, lazy=True)
    want = family.lower()
    for i, f in enumerate(coll.fonts):
        names = {
            (f["name"].getDebugName(nid) or "").lower() for nid in (1, 4, 16)
        }
        if any(want == n or want in n for n in names):
            return i
    return 0  # fall back to first face


# ---- subsetting ------------------------------------------------------------
def _subset(path: str, face_index: int | None, text: set, bold: bool) -> bytes:
    from fontTools import subset
    from fontTools.ttLib import TTFont

    kw = {"fontNumber": face_index} if face_index is not None else {}
    f = TTFont(path, lazy=True, **kw)
    opts = subset.Options(
        layout_features="*", glyph_names=False, notdef_outline=True,
        recalc_bounds=False, recalc_timestamp=False, drop_tables=["FFTM"],
    )
    ss = subset.Subsetter(options=opts)
    chars = (text | _ALWAYS)
    ss.populate(unicodes=[ord(c) for c in chars if ord(c) > 0])
    ss.subset(f)
    f.flags = 0  # write a standalone TTF/OTF, never a collection
    buf = io.BytesIO()
    f.save(buf)
    return buf.getvalue()


# ---- collect what the document uses ----------------------------------------
def _has_cjk(s: str) -> bool:
    return any(
        "　" <= c <= "鿿" or "가" <= c <= "힣" or "＀" <= c <= "￯" for c in s
    )


def _collect(path: str, theme) -> tuple[set, dict]:
    """Return (all_text, {family: {'bold': bool}}) for fonts actually used.
    We embed the theme's declared faces; bold is requested only if the document
    contains bold runs (headings/Title are bold)."""
    doc = Document(path)
    text = set()
    bold_seen = False

    def scan(paras):
        nonlocal bold_seen
        for p in paras:
            text.update(p.text)
            for r in p.runs:
                if r.bold or (r.style and "Heading" in (r.style.name or "")):
                    bold_seen = True
            if p.style and ("Heading" in (p.style.name or "")
                            or p.style.name == "Title"):
                bold_seen = True

    scan(doc.paragraphs)
    for t in doc.tables:
        for row in t.rows:
            for c in row.cells:
                scan(c.paragraphs)

    fams: dict[str, dict] = {}
    # Latin faces + CJK faces declared by the theme.
    for fam in {theme.font_heading, theme.font_body}:
        fams.setdefault(fam, {"bold": bold_seen})
    for fam in {theme.font_cjk_h, theme.font_cjk_b, getattr(theme, "font_kai", "")}:
        if fam:
            fams.setdefault(fam, {"bold": bold_seen and _has_cjk("".join(text))})
    return text, fams


# ---- main entry ------------------------------------------------------------
def embed_used_fonts(path: str, theme) -> str:
    text, fams = _collect(path, theme)

    parts = []  # (zipname, odttf_bytes, fontkey, family, kind)  kind: regular|bold
    ki = 0
    for fam, info in fams.items():
        for bold in ([False, True] if info.get("bold") else [False]):
            fpath = _fc_match(fam, bold=bold)
            if not fpath:
                continue
            try:
                idx = _face_index_for(fpath, fam)
                ttf = _subset(fpath, idx, text, bold)
            except Exception:
                continue
            key = _KEYS[ki]
            ki += 1
            n = ki
            parts.append((
                f"word/fonts/font{n}.odttf", obfuscate(ttf, key), key, fam,
                "bold" if bold else "regular",
            ))

    if not parts:
        return path

    # group parts per family so each <w:font> carries embedRegular/embedBold
    by_fam: dict[str, dict] = {}
    rels = []
    for i, (_zp, _ob, key, fam, kind) in enumerate(parts):
        rid = f"rIdFont{i + 1}"
        by_fam.setdefault(fam, {})[kind] = (rid, key)
        rels.append(
            f'<Relationship Id="{rid}" Type="{FONT_REL}" '
            f'Target="fonts/font{i + 1}.odttf"/>'
        )
    rows = []
    for fam, kinds in by_fam.items():
        embeds = ""
        if "regular" in kinds:
            rid, key = kinds["regular"]
            embeds += (f'<w:embedRegular r:id="{rid}" w:fontKey="{key}" '
                       f'w:subsetted="true"/>')
        if "bold" in kinds:
            rid, key = kinds["bold"]
            embeds += (f'<w:embedBold r:id="{rid}" w:fontKey="{key}" '
                       f'w:subsetted="true"/>')
        rows.append(f'<w:font w:name="{fam}">{embeds}</w:font>')

    font_table = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:fonts xmlns:w="{W}" xmlns:r="{R}">{"".join(rows)}</w:fonts>'
    )
    font_table_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{REL}">{"".join(rels)}</Relationships>'
    )

    # Read the whole package, patch, rewrite cleanly (avoids duplicate parts).
    data = {}
    with zipfile.ZipFile(path) as z:
        for nm in z.namelist():
            data[nm] = z.read(nm)

    for zp, ob, _key, _fam, _kind in parts:
        data[zp] = ob
    data["word/fontTable.xml"] = font_table.encode("utf-8")
    data["word/_rels/fontTable.xml.rels"] = font_table_rels.encode("utf-8")

    ct = data["[Content_Types].xml"].decode("utf-8")
    if "obfuscatedFont" not in ct:
        ct = ct.replace(
            "</Types>",
            f'<Default Extension="odttf" ContentType="{ODTTF_CT}"/></Types>',
        )
    if "/word/fontTable.xml" not in ct:
        ct = ct.replace(
            "</Types>",
            f'<Override PartName="/word/fontTable.xml" '
            f'ContentType="{FONTTABLE_CT}"/></Types>',
        )
    data["[Content_Types].xml"] = ct.encode("utf-8")

    st = data.get("word/settings.xml", b"").decode("utf-8")
    if st and "embedTrueTypeFonts" not in st:
        st = re.sub(
            r"(<w:settings[^>]*>)",
            r"\1<w:embedTrueTypeFonts/><w:saveSubsetFonts/>",
            st, count=1,
        )
        data["word/settings.xml"] = st.encode("utf-8")

    drels = data.get("word/_rels/document.xml.rels", b"").decode("utf-8")
    if "fontTable.xml" not in drels:
        drels = drels.replace(
            "</Relationships>",
            f'<Relationship Id="rIdFontTable" Type="{R}/fontTable" '
            f'Target="fontTable.xml"/></Relationships>',
        )
        data["word/_rels/document.xml.rels"] = drels.encode("utf-8")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for nm, b in data.items():
            z.writestr(nm, b)
    return path
