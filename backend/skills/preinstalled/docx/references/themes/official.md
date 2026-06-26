# Style set: official — 公文 (GB/T 9704)

党政机关公文格式 — the Chinese government document standard, applied
automatically. A4 page with the standard's exact margins, 三号 (16pt) Song body
on a **fixed 28pt line**, and the prescribed three-level heading ladder
(黑体 → 楷体 → 仿宋) that the builder **auto-numbers** 一、/（一）/1． as you go.
Pure black ink, centered 小标宋-style title. This set exists to make a document
that *looks like real 公文* without you touching a single XML attribute.

## What it uses (docbuilder.THEMES["official"])
- Body **Noto Serif CJK SC** (宋/仿宋 substitute), title same centered at 二号.
- Heading ladder, all 三号, fixed line:
  - level 1 → `一、` in **黑体** (Noto Sans CJK SC)
  - level 2 → `（一）` in **楷体** (LXGW WenKai), indented
  - level 3 → `1．` in **仿宋 bold** (Noto Serif CJK SC), indented
- Scale: title 22pt (二号) · body & headings 16pt (三号) · caption 14pt (四号).
- Color: all black `#000000`.
- Page: **A4**, margins top 37 / bottom 35 / left 28 / right 26 mm; **fixed 28pt
  exact** line; first-line indent; headings **auto-numbered**.

## Use it when
- **通知、意见、决定、函、报告** — formal government / institutional documents.
- Any document that must follow **GB/T 9704** layout and the 一、/（一）/1． ladder.

## Avoid it when
- It's a **general Chinese report** that doesn't need the standard → `chinese`
  (more flexible, accent color, navy headings).
- It's **English** or **academic** → the English sets.

## Proprietary-font note
Strict **仿宋_GB2312** and **小标宋体** are proprietary and absent from the
sandbox image. This set substitutes **Noto Serif CJK SC** for both. The result
is GB/T 9704-*shaped* and renders cleanly, but a body requiring the exact
licensed faces would re-embed them in Word. Say so if a user needs a pixel-exact
公文.

## Recommended structure
`cover(标题) → body(主送机关，如 "各有关单位：") → heading + body per 部分 →
附件 (body) → 署名 / 成文日期 (body)`. Pass `lang="zh-CN"`. Call `heading(text,
level=n)` and let the builder prepend the numeral and increment per level —
don't type "一、" yourself. `page_numbers()` for the centered footer.

## Copy tone
- 严谨、规范、庄重 — the register of an official notice.
- Full-width punctuation; no italics, no accent color, no decoration.
- Short declarative clauses; the structure (the ladder) carries the logic.

## Example
```python
import sys; sys.path.insert(0, "<skill_path>/scripts")
from docbuilder import Doc
d = Doc(theme="official", lang="zh-CN")
d.cover("关于推进政务数据共享的实施意见")
d.body("各区县人民政府，市政府各部门：")
d.heading("总体要求")               # -> 一、总体要求
d.body("坚持统筹规划、分步实施，依法依规推进政务数据有序共享。")
d.heading("主要任务")               # -> 二、主要任务
d.heading("数据归集", level=2)      # -> （一）数据归集
d.body("各部门按照统一标准归集本领域政务数据。")
d.heading("平台建设", level=2)      # -> （二）平台建设
d.heading("保障措施")               # -> 三、保障措施
d.body("各单位应于本意见印发之日起三十日内制定落实方案。")
d.page_numbers()
d.save("/workspace/doc/out.docx")
```
Then: `python3 <skill_path>/scripts/check_doc.py /workspace/doc/out.docx` and fix
any errors — especially the CJK-font check.
