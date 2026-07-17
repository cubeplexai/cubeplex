# Theme: slate — minimal

Neutral, restrained, and grown-up. A cool off-white background with muted grey
panels keeps the page calm so dense content can breathe; a single indigo accent
adds just enough signal to guide the eye. This is the consultant's palette —
nothing decorative, everything legible. Inter throughout keeps it austere; the
indigo `4F46E5` is a quiet, intelligent highlight rather than a brand shout.

## Palette (defined in deckbuilder.THEMES["slate"])
- background `F7F8FA` / surface `ECEFF4` — soft grey-white bg reduces glare on
  text-dense slides; the slightly deeper surface separates cards and table rows
  with no harsh lines.
- primary `1F2430` (headings) / muted `5B6472` (body) — near-black headings for
  authority; balanced grey body sustains long reading without fatigue.
- accent `4F46E5` — indigo kickers, rules, highlighted column, chart bars. Adds
  a precise, analytical signal — used sparingly, on purpose.

## Typography
- Display: Inter — headings, kickers, big numbers
- Body: Inter — prose, tables, captions
- CJK: Noto Sans CJK SC
One quiet sans family is the point: minimalism means the structure and the data
carry the slide, not the type.

## Use it when
- **Consulting decks, due-diligence, analyses** with lots of structure.
- **Data-heavy comparisons** and frameworks where calm > flash.
- Audiences who distrust decoration and reward rigor.
- Decks that mix on-screen review with printed handouts.

## Avoid it when
- You need **energy or brand presence** for a launch — go `bloom` / `orchid`.
- The piece is a **single big emotional idea** — go `noir`.
- You want warmth and voice — go `paper` / `ember`.

## Recommended slide flow
`cover → agenda → comparison → steps → comparison → metrics → closing`. slate
leans on `comparison` and `steps` — structured, multi-column thinking is its
home turf, and the restrained palette keeps even busy tables readable. Use
`agenda` to set a rigorous structure up front; analytical audiences want the map.

## Copy tone
- Precise and hedged where honesty demands it: "directional," "estimated."
- Framework-led — name the dimensions, then populate them.
- No hype; let the comparison and the logic persuade.

## Example
```python
import sys; sys.path.insert(0, "<skill_path>/scripts")  # path from load_skill
from deckbuilder import Deck
d = Deck(theme="slate")
d.cover("Market entry options", "A structured read on three paths",
        kicker="Strategy analysis", meta="Advisory • Confidential")
d.comparison("Three paths, side by side",
             headers=["Dimension", "Build", "Partner", "Acquire"], highlight_col=2,
             rows=[["Time to market", "18 mo", "6 mo", "3 mo"],
                   ["Upfront cost", "Low", "Medium", "High"],
                   ["Control", "Full", "Shared", "Full"]])
d.steps("How partnering works", kicker="Path B",
        items=[("Shortlist", "Screen 12 partners against fit and reach."),
               ("Pilot", "Run a 90-day co-sell in one region."),
               ("Scale", "Expand on proven unit economics.")])
d.metrics("If we partner", items=[("6 mo", "To first revenue", ""),
                                  ("~40%", "Lower upfront cost", "vs build")])
d.closing("Recommendation", ["Partner now, revisit acquire in 12 months",
                             "Gate scale on pilot economics"], cta="Decision needed this quarter")
d.save("/workspace/deck/out.pptx")
```
Then: run `python3 <skill_path>/scripts/check_deck.py out.pptx` and fix any errors.
