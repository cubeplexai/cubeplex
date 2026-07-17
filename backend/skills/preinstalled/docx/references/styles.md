# styles — the style-set system

You don't format paragraphs by hand. **A style set fixes the whole look**: the
font pairing (Latin + CJK, heading + body), the type scale, line spacing,
paragraph spacing, margins, an accent color, and whether headings are numbered.
You pick **one** set per document in `Doc(theme=...)`; every building block
emits correctly-styled content. This doc explains the system and the API; pick a
set from `themes.md`, design density from `design.md`, CJK specifics from
`cjk.md`.

## The five style sets

| Set | Profile | Fonts (heading / body, CJK) | When to use |
|---|---|---|---|
| **corporate** | business | Inter / Liberation Serif · Noto Sans + Serif CJK SC | Reports, proposals, briefs — modern, clean, blue accent, spacing-separated paragraphs. |
| **academic** | academic | Liberation Serif throughout · Noto Serif CJK SC | Papers, theses, research — formal serif, **numbered headings**, first-line indent, 1.5 line. |
| **report** | report | Inter / Liberation Sans · Noto Sans CJK SC | Status reports, memos, internal docs — all-sans, compact, accent headings. |
| **chinese** | cjk | Hei heading / Song body (Noto Sans + Serif CJK SC) | 中文通用 — 黑体标题 + 宋体正文, first-line indent, 1.5 line. |
| **official** | official | 黑体/楷体/仿宋 ladder (Noto Sans + Serif CJK SC, LXGW WenKai) | 公文 (GB/T 9704) — A4, 三号 Song body, auto-numbered 一、/（一）/1． heading ladder, fixed 28pt line. |

The hex values, exact sizes, and faces are the single source of truth in
`docbuilder.THEMES`. Run `python3 <skill>/scripts/docbuilder.py` to print the
catalog live.

## The API

One script: `from docbuilder import Doc`, construct with a set, chain methods top
to bottom, `save()`.

```python
d = Doc(theme="corporate", lang="en-US")    # lang="zh-CN" for Chinese docs
```

| Method | For |
|---|---|
| `cover(title, subtitle="", meta="")` | Title block (centered title + subtitle + meta line). |
| `heading(text, level=1, numbered=True)` | Section heading, level 1–3. In `academic`/`official` it auto-numbers; pass `numbered=False` for unnumbered front/back matter (Abstract, References). |
| `body(text)` | A prose paragraph in the body style. |
| `bullets(items)` | Unordered list. |
| `numbered(items)` | Ordered list. |
| `table(headers, rows, caption="")` | Three-line table, accent header row, auto-sized to the text width. |
| `figure(image_path, caption="", width_in=None)` | Centered image, capped to the text width, with a caption. |
| `quote(text, attribution="")` | Indented pull-quote. |
| `toc(title="Contents")` | Updatable Table-of-Contents field (Word refreshes on open). |
| `page_numbers()` | Centered PAGE field in the footer. |
| `page_break()` | Hard page break. |
| `section_break(landscape=False)` | New section; `landscape=True` rotates the page. |
| `save(path)` | Write the .docx; returns the path. |

`table` rows must each match the header length. `figure` reads real pixel
dimensions and caps width at the text area (you rarely pass `width_in`).

## Font-pairing logic

The pairing sets the document's voice; each set picks the right one for its job:

- **Sans heading + serif body** (`corporate`: Inter / Liberation Serif) reads
  **modern-but-readable** — crisp headings, comfortable prose. The default
  business move.
- **All-serif** (`academic`: Liberation Serif throughout) reads
  **scholarly/authoritative** — a single serif across headings and body is the
  long-form paper convention.
- **All-sans** (`report`: Inter / Liberation Sans) reads **clean/operational** —
  no serifs, compact spacing, for internal status docs that get skimmed.
- **Hei heading + Song body** (`chinese`: 黑体 / 宋体) is the standard Chinese
  pairing — a sturdy sans-like Hei title over a readable Song body.
- **公文 ladder** (`official`: 黑体 → 楷体 → 仿宋) follows GB/T 9704's
  prescribed face-per-level. See `cjk.md`.

You don't choose faces individually — picking the set picks the pairing.

## The type scale & spacing (owned by the builder)

Each set defines a tuned scale so hierarchy reads without you sizing anything.
For example `corporate` runs title 26pt → H1 18 → H2 14 → H3 12 → body 11 →
caption 9, headings in deep blue `#1F3864`, accent `#2F5496`, 1.15 line,
8pt after each paragraph, 1.0in margins. `academic` uses a larger body (12pt),
1.5 line, no inter-paragraph space (first-line indent separates paragraphs
instead), and numbered headings. `official` fixes a 28pt exact line and 三号
(16pt) body per the standard. Exact numbers live in `docbuilder.THEMES`.

## Don't hand-format per paragraph

The whole point is consistency. **Don't** override a paragraph's font, size,
color, or spacing to make one bit "pop" — that breaks the scale and the checker
catches the drift (low contrast, Normal-only document, heading skips). The
accent color does the highlighting; the heading levels do the structure.

If the document genuinely needs a *new* look — a different font pairing, a brand
accent, a different scale — **add a style set to `docbuilder.THEMES`** (copy an
existing `StyleSet`, change the faces/colors/sizes) rather than hand-styling
paragraphs. The set is the unit of reuse; one document, one set.

See `design.md` for choosing a set by audience, and `cjk.md` for the East-Asian
font and 公文 details.
