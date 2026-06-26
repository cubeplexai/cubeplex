# Theme: sage — calm

Soft, organic, and grounded. A pale green-tinted background with muted sage
panels gives the deck a quiet, natural calm — it signals care, balance, and
long-term thinking. A measured emerald accent feels like growth without
shouting. Inter keeps everything clean and unfussy; the green `2F9E6E` reads as
sustainable, healthy, and reassuring rather than corporate.

## Palette (defined in deckbuilder.THEMES["sage"])
- background `F6F8F4` / surface `E8EFE4` — barely-green off-white bg lowers
  visual tension; the soft sage surface tints cards and rows organically.
- primary `20291F` (headings) / muted `566355` (body) — deep forest-charcoal
  headings; the earthy grey-green body sustains calm, readable long-form.
- accent `2F9E6E` — emerald kickers, rules, section background, chart bars.
  Adds a living, "growth" signal that feels trustworthy and unforced.

## Typography
- Display: Inter — headings, kickers, big numbers
- Body: Inter — prose, tables, captions
- CJK: Noto Sans CJK SC
A single calm sans suits the theme's restraint — sage persuades by feeling
considered and credible, so the type stays quiet and the content does the work.

## Use it when
- **Sustainability, ESG, climate, and impact** reports.
- **Healthcare, wellness, and life-science** decks that need a trustworthy calm.
- **Research summaries and policy briefs** where balance signals credibility.
- Audiences who respond to care and the long view over hype.

## Avoid it when
- You need **high-energy launch excitement** — go `bloom` / `orchid`.
- The piece is a **hard-charging sales pitch** — the calm undersells it.
- The content is **pure financial reporting** — `daylight` reads more expected.

## Recommended slide flow
`cover → agenda → metrics → chart → cards → steps → closing`. sage leans on
`metrics` and `chart` to make impact tangible, then `cards`/`steps` to lay out a
considered plan. The calm palette lets you present real numbers without alarm —
ideal for "here's the progress, here's the path." Use `agenda` to frame a
measured narrative.

## Copy tone
- Measured, credible, specific: "down 22% since 2023," not "much greener."
- Long-view framing — progress, commitments, and honest gaps.
- Reassuring without overclaiming; let consistency build trust.

## Example
```python
import sys; sys.path.insert(0, "<skill_path>/scripts")  # path from load_skill
from deckbuilder import Deck
d = Deck(theme="sage")
d.cover("Toward net zero", "Our 2026 sustainability progress and the road ahead",
        kicker="Impact report", meta="Sustainability office • 2026")
d.metrics("Where we stand", items=[("-22%", "Emissions", "vs 2023 baseline"),
                                   ("68%", "Renewable energy", "+11 pts"),
                                   ("0", "Landfill waste", "at 3 of 5 sites")])
d.chart("Emissions by year", ["2022", "2023", "2024", "2025"],
        [100, 92, 81, 78], series_name="Index", caption="Scope 1+2, indexed to 2022")
d.steps("The path to 2030", kicker="Roadmap",
        items=[("Power", "Move remaining sites to renewable contracts."),
               ("Supply", "Set science-based targets with top vendors."),
               ("Verify", "Third-party audit every reporting cycle.")])
d.closing("Commitments", ["Halve Scope 1+2 by 2028",
                          "Publish progress every quarter"], cta="Read the full report")
d.save("/workspace/deck/out.pptx")
```
Then: run `python3 <skill_path>/scripts/check_deck.py out.pptx` and fix any errors.
