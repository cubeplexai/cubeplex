# themes — style-set catalog & selection

A style set fixes the **whole** look: font pairing (Latin + CJK, heading + body)
+ type scale + spacing + margins + accent color + numbering. Pick **one** for the
whole document in `Doc(theme=...)`, then read its deep-dive
`references/themes/<name>.md` for the structure it favours and copy tone. The
exact sizes, hex values, and faces are the single source of truth in
`docbuilder.THEMES` — run `python3 <skill>/scripts/docbuilder.py` to print this
catalog live.

## Pick by profile

| Set | Profile | Fonts (heading / body, CJK) | Use when |
|---|---|---|---|
| **corporate** | business | Inter / Liberation Serif · Noto Sans + Serif CJK SC | Reports, proposals, briefs, external letters — modern and clean, blue accent, spacing-separated paragraphs. |
| **academic** | academic | Liberation Serif throughout · Noto Serif CJK SC | Papers, theses, research, lectures — formal serif, numbered headings, first-line indent, 1.5 line. |
| **report** | report | Inter / Liberation Sans · Noto Sans CJK SC | Status reports, memos, internal docs — all-sans, compact, accent headings. |
| **chinese** | cjk | 黑体 / 宋体 (Noto Sans + Serif CJK SC) | 中文报告/文稿, bilingual docs — Hei headings, Song body, first-line indent, 1.5 line. |
| **official** | official | 黑体/楷体/仿宋 ladder (Noto Sans + Serif CJK SC, LXGW WenKai) | 公文 (GB/T 9704) — A4, 三号 Song body, auto-numbered 一、/（一）/1． ladder, fixed 28pt line. |

## How to choose

1. **Audience & occasion drive it.** Boardroom report → `corporate`. Internal
   status/memo → `report`. Paper/thesis → `academic`. 中文文稿 → `chinese`.
   政府公文 → `official`.
2. **Serif vs sans.** A serif body (`corporate`, `academic`) reads
   considered/long-form; an all-sans body (`report`) reads operational/skimmable.
   Match the reading mode.
3. **English vs Chinese.** `corporate`/`academic`/`report` are English-first (but
   set the CJK face too, so a Chinese sentence won't tofu). For a Chinese
   document, use `chinese`; for a government document, `official` — and pass
   `lang="zh-CN"`.
4. **One set only.** Don't mix sets in a document. If the brand needs a specific
   look, the closest set + a custom accent (or a new `StyleSet` in
   `docbuilder.THEMES`) beats hand-formatting every paragraph (`styles.md`).

After picking, **read `references/themes/<name>.md`** — it gives the structure
and tone that make that set sing. CJK content: `chinese`/`official` carry the
right East-Asian faces; the English sets still set a CJK face for stray Chinese.
For the East-Asian font and 公文 details, see `cjk.md`.
