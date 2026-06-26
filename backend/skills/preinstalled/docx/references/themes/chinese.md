# Style set: chinese — 中文通用

中文文稿的标准排版 — 黑体标题配宋体正文。Hei headings (Noto Sans CJK SC) give
titles a sturdy, modern weight; a Song body (Noto Serif CJK SC) keeps prose
readable in the way Chinese print has for a century. First-line indent (2
characters) separates paragraphs, 1.5 line spacing gives the taller CJK glyphs
room, and the body justifies cleanly the way Chinese text should. For general
Chinese documents that are *not* formal government 公文.

## What it uses (docbuilder.THEMES["chinese"])
- CJK headings **Noto Sans CJK SC** (黑体), CJK body **Noto Serif CJK SC** (宋体);
  Latin headings Inter, Latin body Liberation Serif (for stray English).
- Scale: title 22pt → H1 16 → H2 15 → H3 14 → body 12 → caption 10.5.
- Color: headings `#1F3864` (deep navy), body `#000000`, accent `#2F5496`.
- Spacing: **1.5 line**, **first-line indent** (2 chars), 1.1in margins,
  justified body, headings not auto-numbered.

## Use it when
- **中文报告、文稿、方案、说明** — general-purpose Chinese documents.
- **Bilingual** documents with substantial Chinese prose.
- Anything Chinese that wants a clean 黑体/宋体 look but is **not** a formal 公文.

## Avoid it when
- It's a **government 公文** (通知/意见/函) needing GB/T 9704 → `official`.
- It's an **English-first** document → `corporate` / `report` / `academic`.

## Recommended structure
`cover(标题) → toc → page_numbers → heading + body per section → table/figure
where they earn it`. Pass `lang="zh-CN"`. Use `heading()` levels for structure
(not auto-numbered here — number them in the text yourself if the document wants
"一、二、"). Chinese prose runs as flowing paragraphs; reach for `bullets()` only
for genuinely parallel items.

## Copy tone
- 正式、简洁、得体 — formal but not bureaucratic.
- Full-width punctuation (，。：；) in Chinese context; emphasis via **bold**,
  never italic (Chinese has no true italic — see `cjk.md`).
- Lead each section with its point; let the body explain.

## Example
```python
import sys; sys.path.insert(0, "<skill_path>/scripts")
from docbuilder import Doc
d = Doc(theme="chinese", lang="zh-CN")
d.cover("2026 年第二季度运营报告", "营收、留存与下半年计划",
        meta="财务与运营部 · 2026 年 7 月")
d.toc("目录"); d.page_numbers()
d.heading("总体概况")
d.body("第二季度净营收同比增长 18%，达到 1240 万元，超过 1100 万元的计划目标，"
       "主要得益于企业客户续约与两个新签客户。")
d.heading("业绩明细")
d.table(["指标", "计划", "实际"],
        [["营收", "1100 万", "1240 万"], ["毛留存", "92%", "94%"]],
        caption="表 1：2026 年第二季度计划与实际对比")
d.heading("下半年重点")
d.bullets(["维持留存率在 94% 以上", "拓展企业级产品线"])
d.save("/workspace/doc/out.docx")
```
Then: `python3 <skill_path>/scripts/check_doc.py /workspace/doc/out.docx` and fix
any errors — especially the CJK-font check.
