# Design — principles, profiles, and choosing a look

The deck's *look* is owned by the theme + the builder (which pre-balances color,
contrast, spacing, and hierarchy so output passes the checker). Your design job
is higher-level: pick the **profile** that fits the audience, map it to a
**theme**, and choose **archetypes** and **density** that suit the content.

## Principles

- **Content first.** Decide the narrative (`content.md`) before the look. A
  beautiful template over thin content still fails.
- **Sparse content needs *more* design, not an empty slide.** If a slide has one
  idea and three words, don't leave whitespace — promote it to a `statement`, a
  `section` divider, a `metrics` figure, or a `hero` image. Never ship a
  near-empty content slide.
- **Hierarchy comes from size + weight + accent + whitespace**, not from piling
  on colors or boxes. The themes give you exactly one accent; let it do the
  highlighting (key numbers, the winning table column, rules).
- **One idea per slide. Titles are conclusions** ("Inference costs fell 12×",
  not "Costs"). See `content.md`.
- **Consistency over novelty.** One theme for the whole deck. Vary *archetypes*
  for rhythm (`layout.md`), not colors/fonts per slide.

## Profiles → themes

Pick the profile from the audience/occasion, then the theme it maps to. Profiles
are defaults; an explicit brand/user requirement wins (use the closest theme).

| Profile | Audience / occasion | Theme(s) | Density | Text : visual lean |
|---|---|---|---|---|
| **tech** | product/eng keynotes, demos, technical strategy | `midnight` | medium | balanced; charts + metrics |
| **corporate** | business reports, QBRs, status reviews | `daylight`, `slate` | medium-high | text + tables + charts |
| **consulting / data** | analysis-heavy, restrained internal decks | `slate` | high | tables + comparison, little decoration |
| **editorial / strategy** | strategy briefs, opinion, narrative talks | `ember` | medium | statements + sections, prose-led |
| **investor / pitch** | fundraising, vision, business plans | `ember`, `slate`, `midnight` | medium | big metrics + storyline + 1–2 charts |
| **creative / brand** | launches, concepts, vision | `orchid` | low-medium | image + statement-led |
| **promotion / marketing** | product launches, benefit pitches | `bloom` | low | image-dominant, punchy copy |
| **calm / trust** | sustainability, health, research orgs | `sage` | medium | balanced, measured |
| **high-impact** | a single thesis, manifesto, slogan deck | `noir` | very low | statement-dominant |
| **academic** | papers, lectures, long-form, humanities | `paper` | high | figures/tables + prose |

## Density & ratio (how full a slide should be)

- **Low** (creative/promotion/high-impact): one message, large type, generous
  whitespace or a full-bleed `hero`. Use `statement`, `hero`, `section`, single
  `metrics`.
- **Medium** (tech/editorial/calm): a title + 3–4 supporting points or a focused
  visual. `cards` (3–4), `chart`, `image_split`, `steps`.
- **High** (corporate/consulting/academic): denser tables and multi-point cards,
  but still one idea per slide. `comparison` (≤6 rows), `cards` (up to 6),
  `agenda`. Never exceed the per-archetype limits in `layout.md` — split instead.

## Color & contrast

- Themes are pre-balanced and the checker enforces WCAG-ish contrast, so you
  rarely touch color. Trust `primary` for headings, `muted` for body, `accent`
  for the one thing that matters on each slide.
- Dark themes (`midnight`/`ember`/`orchid`/`noir`) read as premium/projected;
  light themes (`daylight`/`slate`/`bloom`/`sage`/`paper`) read as
  print/read-at-desk. Match the room and medium (`themes.md`).
- If contrast warnings appear, the fix is content/structure (move text off a busy
  image area, use a `hero` scrim, pick the right theme) — not hand-edited colors.

## Putting it together

1. Profile + audience → theme (`themes.md` → read `themes/<name>.md`).
2. Narrative + mode → `outline.md` (`content.md`).
3. Map each outline page → archetype at the right density (`layout.md`).
4. Decide where an image earns its place (`images.md`); pick fonts implicitly via
   the theme (`fonts.md`).
5. Build → **check → fix → deliver**. The checker is the backstop, not the plan.
