# Theme: ember — editorial

Dark and warm, like reading by lamplight. A deep espresso-brown background and
a glowing amber accent give ember a considered, opinionated, almost magazine
feel — it signals a point of view, not just a data dump. The Oranienbaum serif
display lends headlines gravitas and editorial polish, while Inter keeps the
body clean and modern; the amber `E8A13A` warms every rule and pull-quote.

## Palette (defined in deckbuilder.THEMES["ember"])
- background `14110E` / surface `241E18` — warm near-black bg; surface is the
  slightly-lit card panel that holds prose blocks and card grids.
- primary `F7F3EC` (headings) / muted `B7A98F` (body) — warm off-white
  headings; the taupe-grey body reads soft and inviting, never clinical.
- accent `E8A13A` — amber kickers, rules, section background, quote marks. Adds
  warmth, confidence, and a literary glow.

## Typography
- Display: Oranienbaum — headings, kickers, big numbers
- Body: Inter — prose, tables, captions
- CJK: Noto Sans CJK SC
Serif-display-over-sans-body is the classic editorial move: the serif gives
each headline authority and voice, the sans keeps supporting text crisp.

## Use it when
- **Strategy briefs and POV decks** that argue a thesis, not just report status.
- **Narrative / storytelling** talks — vision pieces, market essays, manifestos
  with prose.
- **Thought-leadership** and conference keynotes where voice matters.
- Long-form ideas delivered in a dim room.

## Avoid it when
- The content is a **dry status report or dashboard** — go `slate` / `daylight`.
- You need a **bright, upbeat product-launch** energy — go `bloom`.
- The deck is **print-bound** (dark canvas wastes ink).

## Recommended slide flow
`cover → statement → section → cards → comparison → statement → closing`. ember
leans hard on `statement` — the centered pull-quote is where the serif voice
sings — and on `section` dividers to chapter the argument. Use `cards` for
supporting reasoning rather than `metrics`; this theme persuades with prose, not
dashboards.

## Copy tone
- Opinionated and declarative: take a stance, name the tension.
- Editorial rhythm — full sentences in statements, tight phrases in cards.
- Lead with the idea; let one strong quote carry a whole slide.

## Example
```python
import sys; sys.path.insert(0, "<skill_path>/scripts")  # path from load_skill
from deckbuilder import Deck
d = Deck(theme="ember")
d.cover("The quiet cost of speed", "Why our fastest year was also our riskiest",
        kicker="Strategy brief", meta="Office of the CTO • 2026")
d.statement("Velocity without intent is just expensive motion.",
            attribution="Internal review", kicker="Thesis")
d.cards("Where it bites", kicker="Three failure modes",
        cards=[("Tech debt", "Shortcuts compound into rework no roadmap planned for."),
               ("Burnout", "Heroics aren't a strategy; they're a warning sign."),
               ("Drift", "Shipping fast in the wrong direction is still wrong.")])
d.closing("What we change", ["Fund maintenance like a feature",
                             "Slow down to name the goal first"],
          cta="Read the full brief")
d.save("/workspace/deck/out.pptx")
```
Then: run `python3 <skill_path>/scripts/check_deck.py out.pptx` and fix any errors.
