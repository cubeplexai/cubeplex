---
name: pptx
version: 1.2.0
description: >
  Create polished, fully-editable PowerPoint (.pptx) decks end to end — research
  or read the source, outline a narrative, pick a theme, build native editable
  slides (cover / section / agenda / cards / steps / comparison table / metrics /
  native chart / statement / closing), then self-check for overflow / off-canvas
  / low-contrast before delivering. Use whenever the user wants a slide deck,
  presentation, pitch, PPT or PPTX. Free fonts + open libraries only.
---

# pptx — research, design, and build self-checked decks

Don't jump straight to code. A good deck is **content first, design second,
mechanics last**. Follow the pipeline; the depth is in the reference docs —
read the ones each step points to.

`<skill>` below = the absolute path `load_skill` returned for this skill. The
helper library is `<skill>/scripts/deckbuilder.py`; the checker is
`<skill>/scripts/check_deck.py`; reference docs live under `<skill>/references/`.

## Pipeline

1. **Understand the request & inputs.** Read every uploaded file in full first.
   Identify: language, audience, the occasion, any explicit page count, and
   whether the user gave you a finished document, an outline, or just a topic.

2. **Choose the content mode** — decides where slide content comes from.
   See `references/content.md`.
   - **Summarize** — user gave a complete document → distill it (don't invent).
   - **Outline** — user gave structure/points → follow it, enrich each page.
   - **Research** — user gave only a topic → research the web first.

3. **Research (if Research mode, or to supplement).** Two passes: broad (map the
   space, tell the user the directions you'll dig into) → deep (read sources,
   extract real data). Attribute key facts. See `references/content.md` →
   Research. Tools: the `browser` skill or `curl` in the sandbox.

4. **Write the outline to a file** (`/workspace/<deck>/outline.md`): a typed
   page sequence (cover / toc / section / content / closing), each with a
   **conclusion-style title** ("Q3 revenue up 23%", not "Results") and one core
   message. Default 8–16 pages; obey an explicit count. See `references/content.md`.

5. **Design plan.** Pick **one theme** for the whole deck (`references/themes.md`
   catalog → read the chosen `references/themes/<name>.md`). Then map each
   outline page to a **builder archetype** and decide where images, charts, or
   tables earn their place. See `references/design.md`, `references/layout.md`,
   `references/images.md`.

6. **Build** with `deckbuilder.py`. Write one script that imports `Deck`, calls
   the archetype methods in order, and saves to `/workspace/<deck>/deck.pptx`.
   Run it with the sandbox python (python-pptx / pillow / matplotlib are
   installed). API + composition rules: `references/layout.md`. Fonts:
   `references/fonts.md`.

7. **Self-check — mandatory.** Run the checker and fix **every ERROR**, then
   re-run until it prints `0 error(s)`:

   ```bash
   python3 <skill>/scripts/check_deck.py /workspace/<deck>/deck.pptx
   ```

   It flags overflow / off-canvas / low-contrast with real font metrics. Fix
   overflow by tightening copy or splitting the slide — never by shrinking below
   the builder's sizes. Read the full output; don't grep past it.

8. **Deliver** the `.pptx` as an artifact with a one-paragraph summary of the
   deck's structure and key points. Then stop.

## The archetypes (build vocabulary)

`Deck(theme=...)` then, per slide: `cover` · `hero` (full-bleed image) ·
`section` (divider) · `agenda` · `statement` (big idea) · `cards` (2-col) ·
`steps` (numbered process) · `comparison` (table) · `metrics` (big numbers) ·
`chart` (native column) · `image_split` (image + text) · `closing`.
Signatures and density limits: `references/layout.md`.

## Reference docs (read on demand)

| Read | For |
|---|---|
| `references/content.md` | content modes, narrative frameworks, outline format, web research |
| `references/themes.md` + `references/themes/<name>.md` | choosing & using a theme |
| `references/design.md` | profile→theme map, density/ratio, visual principles |
| `references/fonts.md` | font system, pairings, sizes, CJK |
| `references/layout.md` | archetype API, grid/hierarchy, overflow math |
| `references/images.md` | when/how to source images (search vs generate), fallbacks |

## Non-negotiables

- **Titles are conclusions**, not labels. One core idea per slide.
- **Sparse content needs *more* design**, not a near-empty slide — split it, use
  a `statement`/`section`, or enrich with a chart/visual.
- **One theme** for the whole deck; don't hand-tune colors/fonts per slide —
  extend `deckbuilder.py` if you need a new archetype.
- **Never deliver without a clean checker run.** No placeholder text, no empty
  slides, no `[TODO]`.
- Output is standard editable `.pptx` (PowerPoint / WPS / LibreOffice). This
  skill creates decks; it does not read or convert existing `.pptx`.
