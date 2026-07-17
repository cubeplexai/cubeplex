# layout — composition & the archetype API

The builder owns geometry. You own **which archetype, how much copy, and how
dense**. Write one script that does `Deck(theme=...)`, calls one method per
slide in order, then `save("/workspace/<deck>/deck.pptx")`. Pick the theme from
`themes.md`; type comes from `fonts.md`; images from `images.md`. Then run the
checker (bottom of this doc).

## Archetype table

All methods are on `Deck`. Keyword args have defaults shown. "Density limits"
are what passes the checker with the builder's tuned sizes — exceed them and
text overflows.

| Method | For | Signature | Density limits |
|---|---|---|---|
| `cover` | Opening title slide | `cover(title, subtitle="", kicker="", meta="")` | title ≤ ~8 words; subtitle one line |
| `section` | Divider opening a part (accent bg) | `section(title, subtitle="", number="")` | title short; subtitle one line |
| `statement` | One big idea / pull-quote | `statement(text, attribution="", kicker="")` | one short sentence (≤ ~14 words) |
| `agenda` | Table of contents | `agenda(title, items, kicker="Overview")` | 3–6 items, each ≤ ~8 words |
| `cards` | Parallel points (2-col grid) | `cards(title, cards=[(label, body)], kicker="")` | 2–6 cards; label ≤ 4 words; body ≤ ~18 words |
| `steps` | Numbered process | `steps(title, items=[(label, body)], kicker="")` | 2–4 steps; label ≤ 3 words; body ≤ ~14 words |
| `comparison` | Editable table | `comparison(title, headers, rows, kicker="", highlight_col=None)` | ≤ 6 rows, 2–3 cols; cells terse |
| `metrics` | Big numbers | `metrics(title, items=[(big, label, sub)], kicker="By the numbers")` | 2–4 items; big ≤ 5 chars; label ≤ 4 words |
| `chart` | Native column chart | `chart(title, categories, values, series_name="", kicker="", number_format="0", caption="")` | ≤ 6 categories; short labels |
| `closing` | Takeaways / CTA | `closing(title, takeaways, cta="", kicker="Takeaways")` | 2–4 takeaways, each ≤ ~16 words |
| `hero` | Full-bleed image cover | `hero(title, subtitle="", kicker="", image_path=None)` | title short; falls back to `cover` w/o image |
| `image_split` | Half photo, half text | `image_split(title, body=[str], image_path=None, kicker="", image_side="right")` | 2–4 bullets, each ≤ ~12 words; falls back to a panel |

`cards`/`metrics`/`steps` take a `list` of tuples — match the tuple arity
exactly. `comparison` headers and each row must have the same length.

## Choosing the right archetype

Match the relationship in your content, not the look:

- **Parallel, peer items** (features, themes, reasons) → `cards`.
- **Sequence / pipeline / phases** → `steps`. (Ordered; `cards` is unordered.)
- **Option-vs-option on shared dimensions** → `comparison` (use
  `highlight_col` to mark the recommended option).
- **Headline quantities** (KPIs, results) → `metrics`. **Quantities over
  categories** (trend, distribution) → `chart`. Metrics = standalone numbers;
  chart = numbers you want compared visually.
- **A single forceful idea / quote / transition beat** → `statement`.
- **A point that a real photo strengthens** → `image_split` (a person, place,
  product, scene). If the photo is just decoration, use a text archetype.
- **A new part of the deck** → `section`; **the whole table of contents** →
  `agenda`.

If content won't fit one archetype's limits, **split into two slides** — two
clear slides beat one crammed one.

## Composition — why you can trust the builder

The builder enforces these so you don't hand-place shapes (don't — it breaks
alignment and the checker's assumptions):

- **Grid & margins.** A fixed 0.9in horizontal margin and a consistent header
  block anchor every slide; columns are computed from that grid, so cards,
  steps, metrics, and tables all line up edge-to-edge.
- **Hierarchy via size + weight + accent.** Kicker (small, accent) → title
  (large, display, bold) → body (muted). The eye lands in the right order
  without you tuning anything.
- **Whitespace is structural.** Tuned top offsets and gutters give each
  archetype breathing room; a near-empty slide looks *worse*, so enrich or split
  rather than leaving a slide thin.
- **Theme contrast is pre-checked.** Each theme's text/surface combos clear the
  contrast bar on their own surfaces — so as long as you use archetypes (not
  custom colored boxes) contrast warnings won't appear.

## Overflow discipline

The checker measures **real text** with the actual deck fonts (height *and*
width), plus off-canvas shapes and low contrast. It exits 1 on any error.

- **Fix overflow by tightening copy or splitting the slide — never by shrinking
  fonts** below the builder's sizes. Shrinking trades an overflow error for an
  ugly, unreadable slide.
- Density limits above are your guardrail: stay inside them and the checker
  stays green. If a `cards` body is too long, cut words or move to fewer, denser
  cards across two slides.

### The loop
```bash
python3 <skill>/scripts/check_deck.py /workspace/<deck>/deck.pptx
```
Generate → check → read the **full** output → fix every ERROR → re-run until it
prints `0 error(s)`. Don't grep past the output; warnings (low contrast) are
worth a look too. Only deliver on a clean run.

## Slide-flow patterns

A good deck **varies archetypes** — repeating one layout reads as a template
dump. A typical arc:

```
cover → agenda → section → cards → comparison → metrics → chart → statement → closing
```

- Open with `cover`/`hero`, set the map with `agenda`.
- Use `section` dividers to chunk longer decks (consistently — all or none).
- Alternate text-dense (`cards`, `comparison`) with visual (`chart`, `metrics`,
  `image_split`) and breather (`statement`) slides.
- Land it with `closing` (takeaways + CTA).
- Short decks (≤5): skip `agenda`/`section`, lead with content archetypes.
