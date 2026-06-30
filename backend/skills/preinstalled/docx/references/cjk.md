# cjk — Chinese / Japanese / Korean typography

CJK text needs a CJK font, the right line spacing, and conventions Latin text
doesn't (first-line indent, no italic, the 公文 standard). The builder handles
the hard parts; this doc explains what it does and the rules you still own. The
relevant sets are `chinese` (中文通用) and `official` (公文) — `styles.md` for
the system, `themes/chinese.md` / `themes/official.md` for the deep dives.

## The East-Asian font requirement (the builder sets it)

OpenXML resolves fonts per script via four slots on every run: `ascii`/`hAnsi`
for Latin, **`eastAsia` for CJK**, `cs` for complex scripts. Setting only the
Latin face leaves CJK pointed at a font with no Chinese glyphs — Word renders
**tofu boxes** (□□□).

The builder sets `<w:eastAsia>` everywhere it matters — in `docDefaults`, on
every style, and on every run it emits — so any CJK set picks up a real face for
free. You get this by choosing a set with a CJK pairing (`chinese`, `official`,
or any set on Chinese text); you do **not** hand-edit run XML. `check_doc.py`
emits an **ERROR** for any CJK run whose East-Asian font isn't resolvable, so a
tofu document never ships.

## Font roles → our free substitutes

The classic Chinese print roles and what we map them to (all OFL/open):

| Role | 中文 | Our substitute | Character |
|---|---|---|---|
| Sans / 黑体 | 黑体 | **Noto Sans CJK SC** | Neutral Hei — headings, screens. |
| Serif / 宋体 | 宋体 | **Noto Serif CJK SC** | Song — body text, print. |
| 仿宋 | 仿宋 | **Noto Serif CJK SC** | Substitute (see note). |
| 楷体 | 楷体 | **LXGW WenKai (霞鹜文楷)** | Brush-like Kai — 公文 second-level. |

**Honest substitution note.** Strict **仿宋_GB2312** and **小标宋体** are
proprietary fonts not present in the sandbox image. We substitute **Noto Serif
CJK SC** for both. The result is GB/T 9704-*shaped* and renders cleanly, but a
ministry requiring the exact licensed faces would re-embed them in Word. State
this if a user asks for a pixel-exact 公文.

## 字号 — Chinese size names → points

Chinese sizing is named, not numeric. The common ladder:

| 字号 | pt | 字号 | pt |
|---|---|---|---|
| 初号 | 42 | 三号 | 16 |
| 小初 | 36 | 小三 | 15 |
| 一号 | 26 | 四号 | 14 |
| 二号 | 22 | 小四 | 12 |
| 小二 | 18 | 五号 | 10.5 |

The sets already pick the right 字号 (e.g. `official` body is 三号 / 16pt, title
二号 / 22pt). You name sizes for the user, not in code — the set owns them.

## Line spacing for CJK

CJK glyphs are taller and denser than Latin at the same point size, so single
spacing reads cramped. The sets compensate:

- `chinese` and `academic` use **1.5** line spacing.
- `official` uses a **fixed 28pt exact** line (GB/T 9704), not a multiple.

Don't reduce these to fit a page — a thin section is a structure problem
(`content.md`), not a spacing one.

## First-line indent (2 characters)

Chinese body paragraphs conventionally indent the first line by **2 characters**
(not a blank line between paragraphs). The builder sets this via
`firstLineChars="200"`, which scales with the font size, for the `chinese`,
`academic`, and `official` sets. So Chinese prose uses indent-to-separate, not
space-after-to-separate.

## No synthetic italic — use bold

**Chinese has no true italic.** Word fakes one by slanting glyphs, which looks
broken. For emphasis in CJK text, use **bold** (or 着重号 emphasis dots if you
later extend the builder for them). Never italicize Chinese runs.

## 公文 — GB/T 9704 official documents (`official` set)

The `official` set targets the Chinese government document standard
(党政机关公文格式). The builder applies it automatically:

- **Page setup**: A4 (210×297mm), margins top 37 / bottom 35 / left 28 /
  right 26 mm.
- **Body**: 三号 (16pt) Song, **fixed 28pt** line.
- **Heading ladder** (auto-numbered, all 三号, exact line):
  - level 1 → `一、` in **黑体** (Noto Sans CJK SC)
  - level 2 → `（一）` in **楷体** (LXGW WenKai), indented
  - level 3 → `1．` in **仿宋 bold** (Noto Serif CJK SC), indented

  Just call `heading(text, level=n)` — the builder prepends the right numeral
  and auto-increments per level (resetting deeper levels).
- **Title**: 二号 (22pt) Song, centered (the standard's 小标宋 role, substituted).

Build it with `Doc("official", lang="zh-CN")`. Page numbers via
`page_numbers()` (centered footer). The substitution note above applies — say so
if exactness is required. Full worked example in `themes/official.md`.

## Fonts travel with the document

Noto CJK SC / LXGW WenKai are **not installed on a stock Mac or Windows box**.
Without embedding, Word silently substitutes (宋体 → 宋体/PingFang, 黑体 → a
fallback) and the carefully chosen 黑/楷/仿宋 ladder collapses — or, if no CJK
fallback resolves, you get tofu (□). `save()` solves this: it subsets each used
font to just the glyphs in the document and embeds the obfuscated `.odttf` parts
into the package, so the file renders the same everywhere. This is on by default;
see SKILL.md "Non-negotiables". Subsetting keeps the size sane (a few hundred KB
per CJK face rather than ~16 MB).
