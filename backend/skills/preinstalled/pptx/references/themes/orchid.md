# Theme: orchid — creative

Bold, dark, and electric. A deep plum background with a vivid magenta accent
makes orchid feel like a brand reveal — premium, confident, a little nocturnal.
The condensed Anton display turns headlines into poster-sized statements, while
Inter keeps body text legible; the magenta `C264FF` glows against the plum like
neon and signals creativity, ambition, and "watch this."

## Palette (defined in deckbuilder.THEMES["orchid"])
- background `161020` / surface `241733` — deep plum bg sets a premium,
  after-hours mood; the violet surface lifts cards and chips without breaking
  the dark spell.
- primary `F5EFFA` (headings) / muted `B49FCB` (body) — bright lilac-white
  headings; the soft mauve body reads as styled, not corporate.
- accent `C264FF` — magenta kickers, rules, section background, big numbers.
  Adds vibrancy, edge, and brand-launch electricity.

## Typography
- Display: Anton — headings, kickers, big numbers
- Body: Inter — prose, tables, captions
- CJK: Noto Sans CJK SC
Anton is a heavy condensed display face: pair it with quiet Inter so the big
poster headlines land hard and the supporting copy stays out of the way.

## Use it when
- **Product / brand launches** and vision decks meant to feel like an event.
- **Creative pitches, agency work, marketing keynotes** with attitude.
- **On-stage reveals** in a dark room where the magenta can glow.
- Anything where the deck itself is part of the brand impression.

## Avoid it when
- The audience expects **sober financial reporting** — go `daylight`/`slate`.
- The deck is **dense with tables** (Anton headlines fight detail) — go `slate`.
- It will be **printed** (the dark plum wastes ink and dulls the accent).

## Recommended slide flow
`cover → statement → section → metrics → cards → statement → closing`. orchid
leans on `statement` and `section` — big condensed type wants poster moments and
hard chapter breaks, not dense tables. Use `metrics` for a single punchy proof
point, then close on a magenta CTA bar. Keep headlines short so Anton can shout.

## Copy tone
- Punchy, brand-forward, a little audacious: short lines, big claims you back up.
- Benefit-led and emotional — sell the feeling, then the fact.
- One idea per slide; let the type and the magenta do the rest.

## Example
```python
import sys; sys.path.insert(0, "<skill_path>/scripts")  # path from load_skill
from deckbuilder import Deck
d = Deck(theme="orchid")
d.cover("Meet Aurora", "The brand studio that ships in days, not months",
        kicker="Launch", meta="Aurora • Spring 2026")
d.statement("Your brand shouldn't wait on a six-week deck.",
            attribution="Aurora", kicker="The problem")
d.metrics("Why it lands", items=[("72h", "Brand to launch", "average"),
                                 ("1", "Subscription", "no agency retainer"),
                                 ("∞", "Revisions", "until it's right")])
d.cards("What you get", kicker="The kit",
        cards=[("Identity", "Logo, type, and color in a living system."),
               ("Decks", "On-brand templates your team can actually edit."),
               ("Site", "A launch page that matches, day one.")])
d.closing("Ready when you are", ["Start today", "Cancel anytime"],
          cta="Claim your studio")
d.save("/workspace/deck/out.pptx")
```
Then: run `python3 <skill_path>/scripts/check_deck.py out.pptx` and fix any errors.
