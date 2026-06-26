---
name: pptx
version: 1.1.0
description: >
  Create polished, fully-editable PowerPoint (.pptx) presentations with a
  cohesive visual theme. Use when the user wants to make a slide deck,
  presentation, pitch, or PPT/PPTX. Generates native editable slides via
  python-pptx (cover, agenda, cards, comparison table, metrics, native charts, closing),
  then self-checks for text overflow / off-canvas / low-contrast before
  delivering. Free fonts only; no external services.
---

# pptx — themed, self-checked PowerPoint decks

Produce a finished-looking, **editable** PowerPoint and **never deliver it
without running the checker**. The discipline that makes decks look
professional is: pick a theme → write the content tightly → build → **check →
fix → re-check until clean** → save.

## Workflow

1. **State the design approach in one line** (theme + structure), then build.
2. **Build** with the helper library `scripts/deckbuilder.py` in this skill's
   directory (the absolute path is the `path` field returned by `load_skill`).
   Write a short Python script that imports `Deck` and composes the slides:

   ```python
   import sys; sys.path.insert(0, "<skill_path>/scripts")  # path from load_skill
   from deckbuilder import Deck
   d = Deck(theme="midnight")              # midnight | ember | daylight
   d.cover("Title", "Subtitle", kicker="SECTION LABEL", meta="Team • Q2 2026")
   d.agenda("Agenda", ["Point one", "Point two", "Point three"])
   d.cards("Why now", kicker="Four forces", cards=[("Label","Body ≤ ~18 words"), ...])
   d.comparison("A vs B", headers=["Dimension","A","B"], highlight_col=1,
                rows=[["Cost","low","high"], ...])     # ≤ 5 rows
   d.metrics("By the numbers", items=[("73%","Adoption","short sub"), ...])  # 2–4
   d.chart("Adoption by quarter", ["Q1","Q2","Q3","Q4"], [42,58,73,88],
           series_name="SLM %", caption="Share of new workloads (%)")  # native column chart
   d.closing("Takeaways", ["Point", "Point", "Point"], cta="One-line call to action")
   d.save("/workspace/<name>/deck.pptx")
   ```

   For Chinese / CJK content just pass the text — the builder sets the
   East-Asian font automatically (renders with Noto CJK in the sandbox).

   Run it with the sandbox python (python-pptx, pillow, matplotlib are
   pre-installed): `python3 build.py`.

3. **Self-check — mandatory.** Run the checker and fix every ERROR, then
   re-run until it reports `0 error(s)`:

   ```bash
   python3 <skill_path>/scripts/check_deck.py /workspace/<name>/deck.pptx
   ```

   It flags **overflow** (text that won't fit its box), **off-canvas** shapes,
   and **low-contrast** text — measured with the real fonts. Fix overflow by
   shortening the text or splitting the slide (do **not** shrink below the
   readable sizes the builder uses). Warnings are advisory; errors block.

4. **Save as an artifact** and stop.

## Helper API (`deckbuilder.Deck`)

| Method | Use for | Density limit |
|---|---|---|
| `cover(title, subtitle, kicker, meta)` | title slide | title ≤ ~9 words |
| `agenda(title, items)` | overview | 3–6 items, each a short phrase |
| `cards(title, cards, kicker)` | drivers / features (2-col grid) | 2–6 cards; label ≤ 4 words, body ≤ ~18 |
| `comparison(title, headers, rows, highlight_col)` | head-to-head table | ≤ 5 rows, 2–3 columns |
| `metrics(title, items)` | key numbers | 2–4 big numbers |
| `chart(title, categories, values, series_name, caption)` | native editable column chart | ≤ 6 categories |
| `closing(title, takeaways, cta)` | wrap-up | 2–4 takeaways, one CTA |

Themes: **midnight** (dark navy + teal, default), **ember** (dark + amber),
**orchid** (deep plum + magenta), **daylight** (clean light + blue),
**paper** (warm serif, academic). All use libre fonts present in the sandbox
(DejaVu Sans/Serif + Noto CJK Sans/Serif), so what the checker measures is
what PowerPoint/LibreOffice render — including Chinese.

## Design rules (keep it premium)

- **One idea per slide.** If a slide needs more than ~6 lines of body, split it.
- **Tight copy.** Card bodies are phrases, not paragraphs. Numbers belong on a
  `metrics` slide, not buried in prose.
- **Cohesion.** Pick one theme for the whole deck. Don't hand-edit colors/fonts
  per slide — extend the builder if you need a new archetype.
- **Vary layouts** across the deck (cover → agenda → cards → table → metrics →
  closing) rather than repeating one template.
- **Invent realistic content**; never leave placeholders.

## Notes

- Output is standard, fully-editable `.pptx` (PowerPoint / WPS / LibreOffice).
- For a new layout the helper doesn't cover, add a method to `deckbuilder.py`
  rather than hand-placing shapes ad hoc — keeps spacing/contrast consistent
  and checkable.
- This skill creates decks; it does not read or convert existing `.pptx` files.
