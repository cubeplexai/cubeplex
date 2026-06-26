# content — getting the right words onto slides

Content first, design second. This doc covers the three content modes, the
narrative frameworks per deck type, the `outline.md` you write before building,
and the web-research protocol. After this, pick a theme + archetypes
(`layout.md`), then build.

## The three content modes

Decide where slide content comes from. Read the request; pick one.

| Mode | Trigger | What you do |
|---|---|---|
| **Summarize** | User gave a *complete, self-contained* document (paper, report, long article) and wants it as a deck. | Distill it. Don't invent facts. |
| **Outline** | User gave structure — per-page points, a bullet list, a hierarchical outline. | Follow their structure; enrich each page. |
| **Research** | User gave only a topic / a thin brief. | Research the web first, then distill. |

A document that's incomplete or just a brief is *not* Summarize — drop to
Outline or Research. When unsure between Outline and Research, lean to the more
active one (research to fill gaps).

### Summarize — steps
1. **Map the structure.** Identify the source's sections, argument hierarchy,
   and reasoning chain.
2. **Extract the core.** Mark the central claims, key numbers, conclusions.
3. **Adapt to the audience.** Expert audience → keep methodology and detail;
   general audience → lead with conclusions, simplify process, add background.
4. **Distill, don't rewrite.** Each page = one information point traceable to a
   spot in the source. Condense verbose passages; never add claims that aren't
   in the document.

### Outline — steps
1. **Parse the outline.** Complete (per-page), semi-structured (chapters +
   points), or a topic tree? Note any page-count signal.
2. **Enrich each page** by default — add data, examples, brief analysis — while
   keeping the user's structure, wording, and point order intact.
3. **Stay conservative only on explicit instruction** ("use my content as-is",
   "don't add anything"). Then their text leads and supplements only support it.
   Even then, restructure visually so no page is sparse.

### Research — steps
Run the web-research protocol below, then reorganize findings into a narrative.
Each page = one clear point backed by a real fact + its source URL. Distill
after searching; never dump raw results onto slides.

## Narrative frameworks by deck type

Pick the skeleton that fits, then map sections to slides. One idea per slide.

| Deck type | Skeleton |
|---|---|
| **Research report / industry** | Current state → Trends → Opportunities & challenges → Recommendations |
| **Paper / academic** | Background → Method → Findings → Conclusions → Implications |
| **Pitch / strategy** | Problem → Analysis → Solution → Expected outcomes (+ vision, ask) |
| **Product update** | Context → What shipped → Impact (metrics) → What's next |
| **Teaching / explainer** | Hook/problem → Concept → Example/case → Summary/practice |

Use the source's own logic if it's sound; reach for a framework when it isn't.

## The outline.md you write first

Write `/workspace/<deck>/outline.md` before any build script. It's the
content contract the build step reads. A **typed page sequence**, each page with
a conclusion-style title and one core message.

```markdown
# Outline

## Page 1 [cover]
- Title: <deck title>
- Message: <subtitle / one-line framing>

## Page 2 [toc]
- Title: Executive Overview
- Message: 1. <section A>; 2. <section B>; 3. <section C>

## Page 3 [section]
- Title: 01 · <section name>
- Message: <what this section argues>

## Page 4 [content]
- Title: Q3 revenue up 23% on enterprise renewals   # a conclusion, not "Results"
- Message: <the single point this slide makes>
- Source: https://...                               # for any researched fact

## Page N [closing]
- Title: <takeaway framing>
- Message: <key takeaways / CTA>
```

Page types map to builder archetypes (see `layout.md`): `cover`→`cover`/`hero`,
`toc`→`agenda`, `section`→`section`, `content`→one of `cards`/`steps`/
`comparison`/`metrics`/`chart`/`statement`/`image_split`, `closing`→`closing`.

### Title discipline
- **Titles are conclusions, not labels.** "Churn fell to 4% after onboarding
  redesign", not "Churn". Reading only the titles should tell the whole story.
- **One core message per page.** If a page carries two ideas, split it.

### Page-count policy
- **Obey an explicit count.** If the user says "10 slides", deliver 10.
- **No count given → default 8–16**, sized to content density, not padded.
- **Short decks (≤5 pages):** drop `toc` and `section` dividers; spend every
  page on content.
- **Chapter consistency:** either every section gets a `section` divider or none
  do — never divider-some-not-others.
- **Sparse content needs more design, not an empty slide** — split it, promote
  it to a `statement`/`section`, or enrich with a chart/visual.

## Web-research protocol

Two passes. There's no dedicated web-search tool — use the `browser`
preinstalled skill or `curl` (the sandbox has network); `deep-research` for
heavier digs.

1. **Broad pass.** Scan the space, cluster results into sub-directions (trends,
   data, cases, counterpoints). Tell the user the directions you'll dig into
   before going deep.
2. **Deep pass.** Open the valuable sources via the `browser` skill or `curl`,
   read them, extract *real* numbers and quotes. Record each source URL.
3. **Distill.** Cluster across sources, drop redundancy, keep analytical
   conclusions and key data — never a pile of excerpts. Attribute key facts on
   the slide ("Per IDC 2025, ...") and keep the URL in `outline.md → Source`.

### Don't search for what's native
Don't web-search for charts, tables, diagrams, flowcharts, or icons — build
those with `chart()`, `comparison()`, and the builder's native shapes
(`layout.md`). Search the web for **facts and photos** only (photos: see
`images.md`).
