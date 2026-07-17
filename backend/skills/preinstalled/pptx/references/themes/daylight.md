# Theme: daylight — corporate

Clean, bright, and trustworthy — the boardroom default. A pure white background
with a confident corporate blue accent reads as organized, credible, and easy
to project or print. Inter throughout keeps it crisp and neutral; the blue
`2563EB` is the classic "business slide" highlight that signals data, links, and
key figures without ever feeling loud.

## Palette (defined in deckbuilder.THEMES["daylight"])
- background `FFFFFF` / surface `F2F5FA` — white bg for maximum print fidelity;
  the faint blue-grey surface defines cards and tables without hard borders.
- primary `1E293B` (headings) / muted `52607A` (body) — deep slate headings for
  strong contrast on white; the cooler grey body stays readable in print.
- accent `2563EB` — blue kickers, rules, section background, chart bars,
  highlighted table column. Adds corporate clarity and "this is the number."

## Typography
- Display: Inter — headings, kickers, big numbers
- Body: Inter — prose, tables, captions
- CJK: Noto Sans CJK SC
A single neutral sans is exactly right for corporate reporting: it disappears,
letting the figures and structure carry the message.

## Use it when
- **Quarterly business reviews, status reports, board updates.**
- Decks that will be **printed or shared as PDF** with finance/ops audiences.
- **Cross-functional updates** where clarity beats personality.
- Any room with bright projection or a screen-share where white reads best.

## Avoid it when
- You want a **bold, memorable brand statement** — go `orchid` / `noir`.
- The talk is a **dim-room demo** with screenshots — go `midnight`.
- The content is an **opinionated essay** — go `ember`.

## Recommended slide flow
`cover → agenda → metrics → chart → comparison → cards → closing`. daylight
leans on `metrics`, `chart`, and `comparison` — the corporate trio. The blue
accent and white canvas make tables and column charts read cleanly in print, so
build the deck around quantified status: numbers, trend, then the side-by-side.

## Copy tone
- Plain, measured, factual: state the metric, the delta, the driver.
- Neutral and complete — this deck often outlives the meeting as a record.
- Lead with results; reserve adjectives for genuine standouts.

## Example
```python
import sys; sys.path.insert(0, "<skill_path>/scripts")  # path from load_skill
from deckbuilder import Deck
d = Deck(theme="daylight")
d.cover("Q2 2026 business review", "Revenue, retention, and the plan for H2",
        kicker="Quarterly review", meta="Finance & Ops • July 2026")
d.metrics("Quarter at a glance", items=[("$12.4M", "Net revenue", "+18% YoY"),
                                        ("94%", "Gross retention", "+2 pts"),
                                        ("1,280", "Active accounts", "+140 net")])
d.chart("Revenue by quarter", ["Q1", "Q2", "Q3", "Q4"], [9.1, 10.3, 11.5, 12.4],
        series_name="$M", number_format="0.0", caption="Net revenue, $ millions")
d.comparison("Plan vs actual", headers=["Metric", "Plan", "Actual"], highlight_col=2,
             rows=[["Revenue", "$11.0M", "$12.4M"], ["Retention", "92%", "94%"],
                   ["New logos", "120", "140"]])
d.closing("Focus for H2", ["Defend retention above 94%",
                           "Open the enterprise tier"], cta="Full deck in the data room")
d.save("/workspace/deck/out.pptx")
```
Then: run `python3 <skill_path>/scripts/check_deck.py out.pptx` and fix any errors.
