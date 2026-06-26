# images — when and how to use figures

Most documents need **no images at all** — the set's typography, headings, and
tables carry the structure. A figure earns its place only when it adds
information or evidence a reader can't get from words. Add one with `figure()`;
build everything tabular natively with `table()`.

## When a figure earns its place

- **A photo that *is* the evidence** — a site photo, a product shot, a scanned
  artifact, a map. Something the reader needs to see, not just read about.
- **A diagram the user supplied** — an architecture diagram, a process flow they
  gave you as an image file. Place it; don't redraw it as prose.
- **Everything else stays text or tables.** Parallel points → `bullets`; values
  across dimensions → `table`; a sequence → `numbered`. No purely decorative
  images — a document is not a slide deck, and a stock photo "about the topic" is
  clutter that dilutes a serious report.

A figure that's just mood or filler is decoration — leave it out.

## Acquisition ladder

Work top-down; stop at the first good result.

1. **Extract from user uploads.** If the user gave files (PDF/DOCX/PPTX/images),
   pull the relevant figure from them first — most relevant, no licensing
   question.
2. **Web image search** via the `browser` preinstalled skill or `curl` to
   royalty-free sources (Unsplash, Pexels, Wikimedia Commons, Openverse). The
   sandbox has network, so `curl -L -o ...` works. Use **English keywords** that
   describe the subject precisely.
3. **`generate_image` tool** if available — try it; it may be disabled when no
   API key is configured. If it errors, fall back to step 2.
4. **Closest decent result.** If nothing perfect exists, use the best real image
   you found — or drop the figure entirely. An imageless document is fine.

Save into `/workspace/<doc>/images/` and pass the path to `figure()`.

```python
d.figure("/workspace/report/images/site.jpg",
         caption="Figure 1: The north field installation, May 2026")
```

## Keep figures within the margins

`figure()` reads the real pixel dimensions and **caps the width at the text
area** (page width minus margins), centered — so an oversized image is shrunk to
fit, never spilled into the margin. You rarely pass `width_in`; only set it to
make a figure *smaller* than full width. `check_doc.py` emits an **ERROR** for
any image wider than the text area, so an overflowing figure never ships.

## Search discipline — don't search for what you build

- **Never search for charts, tables, diagrams-of-data, flowcharts, or
  screenshots of numbers.** A searched chart image is uneditable, off-brand, and
  often wrong. Build the data into a `table()`, or describe the trend in `body`
  prose. If the user needs a real plotted chart, generate the PNG yourself (e.g.
  matplotlib in the sandbox) and place it with `figure()` — don't web-search one.
- **Never use keywords like "PPT", "infographic", "slide", "template"** — they
  return slide screenshots, not usable photos.
- Want **high-resolution, watermark-free** images sized for print.

## Validation

- **Inspect before using.** Open candidates with `view_images` — confirm it's
  on-topic, high-res, not watermarked, right orientation.
- **Retry keywords** if the first batch is weak; don't settle for a low-quality
  image because search was hard.

## Captions & licensing

- **Caption every figure**: `"Figure N: <what it shows>"`. The caption is the
  argument; a figure with no caption makes the reader guess. Number them in
  order; reference them in the prose ("see Figure 1").
- **Prefer royalty-free / Creative Commons** sources (Unsplash, Pexels, Wikimedia
  Commons, Openverse). Don't pull watermarked or clearly copyrighted images. When
  a source needs attribution, note it in the caption or a references section;
  when in doubt, choose a freely-licensed alternative.
