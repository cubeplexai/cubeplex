# Theme: bloom — promotion

Vibrant, warm, and friendly — a light theme that smiles. A creamy off-white
background with peachy surfaces and a punchy coral-pink accent makes bloom feel
upbeat, approachable, and consumer-ready. The wide Barlow display gives headings
a cheerful, confident weight, while Inter keeps body text clean; the coral
`F0476A` pops like a "buy now" button and signals energy and delight.

## Palette (defined in deckbuilder.THEMES["bloom"])
- background `FFFBF7` / surface `FFF0E8` — warm cream bg feels inviting (not
  clinical white); the soft peach surface tints cards so they read playful.
- primary `2A1A22` (headings) / muted `7A5A66` (body) — deep plum-brown
  headings keep strong contrast on cream; the warm mauve body stays friendly.
- accent `F0476A` — coral-pink kickers, rules, section background, chart bars,
  CTA bar. Adds warmth, momentum, and consumer-marketing pop.

## Typography
- Display: Barlow — headings, kickers, big numbers
- Body: Inter — prose, tables, captions
- CJK: Noto Sans CJK SC
Barlow's friendly, slightly wide letterforms give promotional headlines warmth
and pace; Inter keeps the benefit copy clean and quick to scan.

## Use it when
- **Marketing and product-launch decks** for consumer or SMB audiences.
- **Campaign pitches, growth reviews, go-to-market** stories that need energy.
- **Webinar / sales** decks where approachability drives conversion.
- Bright rooms and shareable PDFs where light + warm reads best.

## Avoid it when
- The setting is **formal finance or legal** — go `daylight` / `slate`.
- The idea is **somber or weighty** — the cheerful coral fights the mood.
- You want **dark, premium drama** — go `orchid` / `noir`.

## Recommended slide flow
`cover → metrics → cards → steps → chart → closing`. bloom leans on `metrics`,
`cards`, and `steps` — promotional decks sell benefits and an easy path to "yes."
Lead with a headline proof number, fan out the features as cards, then make the
funnel feel effortless with `steps`. Close on the coral CTA bar.

## Copy tone
- Punchy and benefit-led: "Launch in a weekend," not "reduced setup time."
- Warm, second-person, action verbs — talk to the reader.
- Energetic but honest; back the excitement with one real number.

## Example
```python
import sys; sys.path.insert(0, "<skill_path>/scripts")  # path from load_skill
from deckbuilder import Deck
d = Deck(theme="bloom")
d.cover("Sell more, sweat less", "The storefront that sets itself up",
        kicker="Product launch", meta="Bloom Commerce • 2026")
d.metrics("Why sellers switch", items=[("1 day", "To live store", "not weeks"),
                                       ("+34%", "Conversion lift", "vs prior tool"),
                                       ("0", "Code required", "ever")])
d.cards("Everything included", kicker="Out of the box",
        cards=[("Themes", "Beautiful, mobile-first, ready to ship."),
               ("Payments", "Cards, wallets, and BNPL on day one."),
               ("Analytics", "See what sells the moment it sells.")])
d.steps("Live in three steps", kicker="Onboarding",
        items=[("Connect", "Import your catalog in one click."),
               ("Style", "Pick a theme, drop in your logo."),
               ("Launch", "Go live and start selling today.")])
d.closing("Start selling today", ["14-day free trial", "No card to start"],
          cta="Open your store now")
d.save("/workspace/deck/out.pptx")
```
Then: run `python3 <skill_path>/scripts/check_deck.py out.pptx` and fix any errors.
