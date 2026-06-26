# Theme: noir — highimpact

Maximum contrast, maximum drama. A true near-black background with a single
electric-yellow accent makes noir feel like a manifesto — stark, bold, and
impossible to ignore. The condensed Anton display turns a few words into a
billboard, while Inter handles any supporting text; the yellow `F2D544` cuts
through the black like a highlighter on the one thing that matters.

## Palette (defined in deckbuilder.THEMES["noir"])
- background `0A0A0B` / surface `17181B` — true black bg for absolute drama; the
  near-black surface gives cards just enough separation to exist.
- primary `FAFAFA` (headings) / muted `9B9DA3` (body) — pure-white headings hit
  hardest on black; the neutral grey body recedes so the headline dominates.
- accent `F2D544` — electric-yellow kickers, rules, section background, big
  numbers. Adds urgency and singular focus — use it on one thing per slide.

## Typography
- Display: Anton — headings, kickers, big numbers
- Body: Inter — prose, tables, captions
- CJK: Noto Sans CJK SC
Anton on black is a poster engine: keep words few and large so each slide is one
unforgettable frame. Inter is there only for the rare line of support copy.

## Use it when
- **Single big ideas, manifestos, and rally-the-room** keynotes.
- **Founder / vision talks** that need to feel bold and decisive.
- **Campaign teasers and brand statements** with one line to land.
- On-stage moments in a dark room where contrast is the whole point.

## Avoid it when
- The deck is **information-dense** (tables, multi-metric) — go `slate`/`daylight`.
- The audience needs **nuance and prose** — noir flattens everything to a slogan.
- It will be **printed** — solid black backgrounds are impractical and dull.

## Recommended slide flow
`cover → statement → statement → metrics → statement → closing`. noir leans
almost entirely on `statement` — it's a string of poster frames, each carrying
one idea, punctuated by a single huge `metrics` proof point. Resist `cards` and
`comparison` here; detail dilutes the impact. Fewer slides, bigger words.

## Copy tone
- Slogan-short and declarative: one line, no qualifiers.
- Provocative and certain — state it like it's already true.
- Cut every word you can; the yellow accent rewards brevity.

## Example
```python
import sys; sys.path.insert(0, "<skill_path>/scripts")  # path from load_skill
from deckbuilder import Deck
d = Deck(theme="noir")
d.cover("Stop optimizing the wrong thing", kicker="Manifesto",
        meta="Founders' note • 2026")
d.statement("Most teams are world-class at building products nobody asked for.",
            kicker="The trap")
d.statement("Speed is not the metric. Learning is.", kicker="The shift")
d.metrics("The cost of guessing", items=[("70%", "Of features", "go unused")])
d.closing("Build to learn", ["Ship the smallest test that can fail",
                             "Kill it fast, or double down"], cta="Pick one bet this week")
d.save("/workspace/deck/out.pptx")
```
Then: run `python3 <skill_path>/scripts/check_deck.py out.pptx` and fix any errors.
