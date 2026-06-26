# fonts — the type system

You don't set fonts per run. **A theme *is* the font system**: each theme fixes
a display face (headings, kickers, big numbers), a body face (prose, table
cells, captions), and a CJK face (any East-Asian text). You pick one theme for
the deck (`layout.md` for the archetype API, `themes.md` for the catalog); the
builder applies the faces at tuned sizes. This doc explains the catalog and how
to choose by typographic feel.

## Principles

- **Language matching.** CJK glyphs need a CJK face. The builder sets the
  East-Asian face (`<a:ea>`) on every run alongside the Latin face — without it,
  Chinese falls back to a Latin font that has no CJK glyphs and renders as tofu
  boxes. You get this for free by picking a theme; just don't paste CJK text and
  expect a Latin-only theme to "handle it" — the *face* comes from the theme.
- **Display vs body split.** Headings use the display face (stylized, heavier);
  prose uses the body face (high readability). The builder routes each
  automatically per archetype.
- **Readability floor.** The builder's sizes are tuned (body 12.5pt, table
  12–13pt, card label 17pt, headers 30pt, cover title 40pt, big metric 54pt).
  Never fight overflow by going below them — fix copy instead (`layout.md`).
- **Exact family names.** Names below match the installed faces exactly,
  including case and spacing. The checker measures with these same fonts.
- **No emoji.** Emoji render as tofu in these faces; the builder draws accents
  as shapes instead. Don't put emoji in copy.

## Catalog

### Latin (all SIL OFL)

| Font | Character | Good for | Used by theme(s) |
|---|---|---|---|
| Inter | Neutral grotesque, screen-optimized | Workhorse body + clean headings | midnight, daylight, slate, sage; body of all |
| Barlow | Grotesque, slightly condensed, friendly | Marketing display | bloom (display) |
| Anton | Ultra-bold condensed display | Big, loud headlines | orchid, noir (display) |
| Oranienbaum | High-contrast serif, elegant | Editorial / opinion display | ember (display) |
| Unna | Neoclassical serif, vertical rhythm | Literary/publishing display | — |
| Liter | Low-contrast modern sans | Tech/product (alt body) | — |
| Sorts Mill Goudy | Classical old-style serif, warm | Academic/long-form display | paper (display) |
| Quattrocento Sans | Gentle humanist sans, clear small | Academic/corporate body | paper (body) |

### CJK

| Font | 中文名 | Character | Good for | Theme |
|---|---|---|---|---|
| Noto Sans CJK SC | — | Neutral Hei, wide coverage | Default for any CJK; reports | all except paper |
| LXGW WenKai | 霞鹜文楷 (OFL) | Soft Kai/Fangsong, editorial | Academic/long-form Chinese | paper |
| Smiley Sans | 得意黑 (OFL) | Oblique condensed display Hei | Creative Chinese headlines | (extend a theme) |
| ZCOOL KuaiLe | 站酷快乐体 (OFL) | Rounded playful | Friendly/kids/entertainment | (extend) |
| ZCOOL XiaoWei | 站酷小薇体 (OFL) | Light Song-flavored display | Elegant Chinese display | (extend) |
| ZCOOL QingKe HuangYou | 站酷庆科黄油体 (OFL) | Bold rounded display | Punchy promo Chinese | (extend) |
| Ma Shan Zheng | 马善政 | Brush calligraphy | Traditional/cultural accents | (extend) |
| Long Cang | 龙藏 (OFL) | Running-hand brush | Handwriting/artistic | (extend) |

## Pairing logic — choosing a theme by feel

The display/body pairing sets the deck's voice. Match the pairing to the
occasion:

- **Serif display + sans body** (ember = Oranienbaum/Inter, paper = Sorts Mill
  Goudy/Quattrocento Sans) reads **editorial / academic** — the serif headline
  signals authority and considered prose. Reach for these on strategy briefs,
  papers, lectures, long-form.
- **Anton display** (orchid, noir) reads **bold / high-impact** — a heavy
  condensed headline shouts. Reach for launches, manifestos, single big ideas.
- **Barlow display** (bloom) reads **vibrant / marketing** — approachable energy
  for promo and product launches.
- **Inter everywhere** (midnight, daylight, slate, sage) reads **neutral /
  modern** — the safe workhorse for product, corporate, consulting, research.
  When in doubt, an Inter theme won't be wrong.

Pick the theme whose *blurb and pairing* match the deck's tone, not just its
colors. `themes.md` lists all nine with their one-line "use when".

## CJK guidance

- **Default to Noto Sans CJK SC** for Chinese body and most headings — neutral,
  complete, projects cleanly. Every non-paper theme already uses it.
- **For editorial / academic Chinese**, use the `paper` theme — its CJK face is
  LXGW WenKai (霞鹜文楷), whose soft Kai strokes read like a printed book.
- **For creative Chinese display** (Smiley Sans / the ZCOOL family / brush
  faces), you must use a theme whose `font_cjk` is set to that face. Since
  per-run font overrides aren't exposed, that means **extending `deckbuilder.py`
  with a theme** (copy an existing `Theme`, change `font_cjk`) rather than
  toggling a font mid-deck. Keep it honest: theme = the font system; if the
  catalog's themes don't carry the face you want, add a theme.
- Mixed CJK + Latin in one run is fine — the builder sets both the Latin and the
  East-Asian face on the same run, so each script uses its correct glyphs.
