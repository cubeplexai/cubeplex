# Theme: midnight — tech

A dark, modern look built for product and engineering keynotes. The near-black
navy background with a cool teal accent reads as precise, confident, and a
little futuristic — the default "ship it" palette. Inter carries both headings
and body, so everything feels like one clean UI; the teal `00D2A0` glows
against the dark like a status light that says "this works."

## Palette (defined in deckbuilder.THEMES["midnight"])
- background `0F141E` / surface `1B2433` — bg fills every slide; surface is the
  raised card / agenda-number chip fill that lifts content off the dark plane.
- primary `F4F6FB` (headings) / muted `9AA7BD` (body) — soft white headings sit
  bright on the navy; the cool grey body stays legible without shouting.
- accent `00D2A0` — kickers, rules, big metric numbers, the section background,
  chart bars. Adds the engineered, "live signal" energy.

## Typography
- Display: Inter — headings, kickers, big numbers
- Body: Inter — prose, tables, captions
- CJK: Noto Sans CJK SC
A single-family system pairing is deliberately neutral: it keeps attention on
the product, the diagram, the number — not the typography.

## Use it when
- Internal or external **engineering / product keynotes** and roadmap reviews.
- **Developer-tool or infra launches** where credibility comes from precision.
- **Demo-driven** talks projected in a dimmed room.
- Anything where a dark, screenshot-friendly canvas helps UI imagery pop.

## Avoid it when
- The audience is **executive-finance / formal print** — go `daylight`.
- The deck will be **printed on paper** (dark backgrounds waste toner and look
  muddy).
- You want warmth or a human, editorial voice — go `ember` or `paper`.

## Recommended slide flow
`cover → agenda → section → cards → metrics → chart → closing`. midnight leans
on `metrics` and `chart` — the teal accent was tuned to make data the hero, and
the dark canvas makes column bars and big numbers glow. Use `section` dividers
between roadmap phases to give the deck a product-launch cadence.

## Copy tone
- Declarative and concrete: "Cut p95 latency to 40ms," not "improved latency."
- Lead with capability and outcome; let numbers do the bragging.
- Short, technical, confident — no marketing fluff.

## Example
```python
import sys; sys.path.insert(0, "<skill_path>/scripts")  # path from load_skill
from deckbuilder import Deck
d = Deck(theme="midnight")
d.cover("Inference, rebuilt", "A faster path from prompt to token",
        kicker="Platform keynote", meta="Runtime team • Q2 2026")
d.metrics("By the numbers", items=[("40ms", "p95 latency", "down from 110ms"),
                                   ("3.2x", "throughput", "same hardware"),
                                   ("99.98%", "uptime", "trailing 90 days")])
d.chart("Tokens served per quarter", ["Q1", "Q2", "Q3", "Q4"],
        [42, 58, 73, 88], series_name="B tokens", caption="Billions, monthly peak")
d.closing("Takeaways", ["One runtime, every model", "Half the cost per token",
                        "GA in Q3"], cta="Join the early-access program")
d.save("/workspace/deck/out.pptx")
```
Then: run `python3 <skill_path>/scripts/check_deck.py out.pptx` and fix any errors.
