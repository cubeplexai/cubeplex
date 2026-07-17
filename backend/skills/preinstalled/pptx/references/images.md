# images — when and how to use photos

Photos earn their place in only two spots; everything else is built natively.
Most decks look good with **no photos at all** — the themes and archetypes carry
the design. Add a photo only when it adds information or impact.

## When an image earns its place

- **Covers / section dividers** → `hero` for a full-bleed image with a
  legibility scrim and overlaid title. Use for emotional or scene-setting impact.
- **A content point a real photo strengthens** → `image_split` (half photo, half
  text) — a person, place, product, or scene that *informs* the point.
- **Everything else** stays native: parallel points → `cards`; numbers →
  `metrics`/`chart`; option grids → `comparison` (see `layout.md`). No purely
  decorative images.
- **Business / academic-leaning themes** (slate, daylight, paper) → informational
  images only; skip mood photography.

A photo that's just "a stock image vaguely about the topic" is decoration —
leave it out and use a text/visual archetype instead.

## Acquisition ladder

Work top-down; stop at the first good result.

1. **Extract from user uploads.** If the user gave files (PDF/PPTX/Word/images),
   pull suitable photos from them first — most relevant, no licensing question.
2. **Web image search** via the `browser` preinstalled skill or `curl` to
   royalty-free sources (Unsplash, Pexels, Wikimedia Commons, Openverse). The
   sandbox has network, so `curl -L -o ...` works. Use **English keywords +
   style words that match the theme** (e.g. "dark moody data center wide shot"
   for a `midnight`/`noir` deck).
3. **`generate_image` tool** if available — try it; it may be disabled when no
   API key is configured. If it errors, fall back to step 2.
4. **Closest decent result.** If nothing perfect exists, use the best real photo
   you found rather than degrading to a gradient or placeholder.

Save into `/workspace/<deck>/images/` and pass the path as `image_path=` to
`hero` or `image_split`.

```python
deck.hero("The next decade of compute", kicker="Vision",
          image_path="/workspace/mydeck/images/cover.jpg")
deck.image_split("Built for teams", body=["Shared workspaces", "Live presence"],
                 image_path="/workspace/mydeck/images/team.jpg", image_side="right")
```

## Search discipline

- **Never search for charts, tables, icons, diagrams, or flowcharts.** Build
  those with `chart()`, `comparison()`, and native shapes (`layout.md`). A
  searched chart image is uneditable and off-brand.
- **Never use keywords like "PPT", "presentation", "slide", "infographic"** —
  they return slide screenshots, not photos.
- Want **high-resolution, watermark-free, landscape** images, especially for
  `hero` (full-bleed needs the pixels).

## Validation & fallback

- **Inspect before using.** Open candidates with `view_images` — check it's
  on-topic, high-res, not watermarked, and the right orientation.
- **Retry keywords** if the first batch is weak (different nouns, add the theme's
  mood words). Don't settle for a low-quality image just because search was hard.
- **Graceful degradation is built in.** With no `image_path`, `hero` falls back
  to a clean `cover` and `image_split` falls back to a surface panel beside the
  text. Both still look finished — so if you truly can't find a good image, drop
  it; an imageless deck is fine.
- **Crop-to-fill is automatic.** The builder fills the image box and crops the
  overflow (never stretches/distorts), so you don't size or crop manually — just
  give it a landscape source for `hero` and either orientation for `image_split`.

## License note

Prefer royalty-free / Creative Commons sources (Unsplash, Pexels, Wikimedia
Commons, Openverse). Keep it clean — don't pull watermarked or clearly
copyrighted images. When a source needs attribution, note it; when in doubt,
choose a freely licensed alternative.
