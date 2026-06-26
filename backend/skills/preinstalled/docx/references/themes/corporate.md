# Style set: corporate — business

Modern and clean — the business default. A sans heading over a serif body reads
as organized and credible without feeling stiff: Inter gives headings crisp,
contemporary structure while Liberation Serif keeps prose comfortable to read at
length. A deep navy heading color with a confident blue accent signals "this is
a considered document" — the look of a well-made report or proposal.

## What it uses (docbuilder.THEMES["corporate"])
- Heading font **Inter**, body font **Liberation Serif**; CJK headings **Noto
  Sans CJK SC**, CJK body **Noto Serif CJK SC**.
- Scale: title 26pt → H1 18 → H2 14 → H3 12 → body 11 → caption 9.
- Color: headings `#1F3864` (deep navy), body `#333333`, accent `#2F5496` (blue
  — the table header fill and rules).
- Spacing: 1.15 line, 8pt after each paragraph (paragraphs separated by space,
  not indent), 1.0in margins, headings **not** numbered.

## Use it when
- **Business reports, quarterly reviews, briefs, proposals.**
- **External letters** and client-facing documents where polish matters.
- Any document that will be **printed or shared as PDF** to a business audience.

## Avoid it when
- It's an **internal status memo** that should read fast → `report` (all-sans,
  compact).
- It's an **academic paper** with numbered sections → `academic`.
- It's a **Chinese document** → `chinese` / `official`.

## Recommended structure
`cover → toc → page_numbers → heading("Executive summary") + body →
heading per section (findings → analysis → recommendations) → table/figure where
they earn it → heading("Appendix")`. Lead the summary with the conclusion; the
body sections back it. Reach for `table()` on plan-vs-actual and option grids.

## Copy tone
- Plain, measured, factual: state the finding, the number, the driver.
- Lead with results; reserve adjectives for genuine standouts.
- Neutral and complete — the document often outlives the meeting as a record.

## Example
```python
import sys; sys.path.insert(0, "<skill_path>/scripts")   # path from load_skill
from docbuilder import Doc
d = Doc(theme="corporate")
d.cover("Q2 2026 Operations Review", "Revenue, retention, and the H2 plan",
        meta="Finance & Ops · July 2026")
d.toc(); d.page_numbers()
d.heading("Executive summary")
d.body("Net revenue rose 18% YoY to $12.4M, ahead of the $11.0M plan, on "
       "enterprise renewals and two new logos.")
d.heading("Performance")
d.table(["Metric", "Plan", "Actual"],
        [["Revenue", "$11.0M", "$12.4M"], ["Gross retention", "92%", "94%"],
         ["New logos", "120", "140"]],
        caption="Table 1: Plan vs actual, Q2 2026")
d.heading("Focus for H2")
d.bullets(["Defend retention above 94%", "Open the enterprise tier"])
d.save("/workspace/doc/out.docx")
```
Then: `python3 <skill_path>/scripts/check_doc.py /workspace/doc/out.docx` and fix
any errors.
