# content — getting the right words into the document

Content first, format second. This doc covers the three content modes, the
document-type structures you build to, the outline you sketch before writing,
length sense, and the web-research protocol. After this, pick a style set
(`styles.md`) and design density (`design.md`), then build with `docbuilder`.

## The three content modes

Decide where the document's content comes from. Read the request; pick one.

| Mode | Trigger | What you do |
|---|---|---|
| **Summarize** | User gave a *complete, self-contained* source (paper, report, transcript, long article) and wants it as a document. | Distill it. Don't invent facts. |
| **Outline** | User gave structure — section list, a bullet outline, a hierarchical brief. | Follow their structure; enrich each section. |
| **Research** | User gave only a topic / a thin brief. | Research the web first, then distill. |

A source that's incomplete or just a brief is *not* Summarize — drop to Outline
or Research. When unsure between Outline and Research, lean to the more active
one (research to fill gaps).

### Summarize — steps
1. **Map the structure.** Identify the source's sections, argument hierarchy,
   and reasoning chain.
2. **Extract the core.** Mark the central claims, key numbers, conclusions.
3. **Adapt to the audience.** Expert reader → keep methodology and detail;
   general reader → lead with conclusions, simplify process, add background.
4. **Distill, don't transcribe.** Each section makes a point traceable to the
   source. Condense verbose passages; never add claims that aren't in it.

### Outline — steps
1. **Parse the outline.** Complete (section-by-section), semi-structured
   (chapters + points), or a topic tree? Note any length signal.
2. **Enrich each section** by default — add data, examples, brief analysis —
   while keeping the user's structure, order, and wording intact.
3. **Stay conservative only on explicit instruction** ("use my content as-is",
   "don't add anything"). Then their text leads and supplements only support it.

### Research — steps
Run the web-research protocol below, then reorganize findings into the
document's narrative. Each section makes one clear point backed by a real fact
and its source. Distill after searching; never paste raw results.

## Document-type structures

Pick the skeleton that fits, then map it to building blocks. The blocks are the
`Doc` methods (`styles.md` lists the full API): `cover` · `toc` · `heading` ·
`body` · `bullets` · `numbered` · `table` · `figure` · `quote` ·
`page_numbers` · `page_break` · `section_break`.

### Business report
`cover → toc → heading("Executive summary") → body → heading per section
(findings, analysis, recommendations) → table/figure where they earn it →
heading("Appendix")`. Lead the summary with the conclusion; the body backs it.

```python
d = Doc("corporate")
d.cover("Q2 2026 Operations Review", "Revenue, retention, and the H2 plan",
        meta="Finance & Ops · July 2026")
d.toc(); d.page_numbers()
d.heading("Executive summary"); d.body("Net revenue rose 18% YoY to $12.4M...")
d.heading("Revenue"); d.table(["Metric", "Plan", "Actual"],
    [["Revenue", "$11.0M", "$12.4M"], ["Retention", "92%", "94%"]],
    caption="Table 1: Plan vs actual, Q2 2026")
```

### Academic paper
`cover → heading("Abstract") → body → numbered sections (1 Introduction,
2 Method, 3 Results, 4 Discussion) → figures/tables inside Results →
heading("References") → numbered`. Use the `academic` set (serif, numbered
headings, first-line indent). Cite as you go; let `figure()`/`table()` carry the
data with "Figure N:" / "Table N:" captions.

```python
d = Doc("academic")
d.cover("Effective Context Length in Long-Context Models", meta="NLP Lab · 2026")
d.heading("Abstract", numbered=False); d.body("We measure recall vs context...")
d.heading("Introduction"); d.body("...")           # auto-numbered "1 Introduction"
d.heading("Method"); d.body("...")                 # "2 Method"
d.figure("/workspace/doc/images/recall.png", "Figure 1: Recall vs context length")
d.heading("References", numbered=False)            # front/back matter: unnumbered
```

### Proposal
`cover → heading("Problem") → heading("Approach") → heading("Scope &
deliverables") → table(timeline/cost) → heading("Why us") → closing body`.
Open with the problem in the reader's words; end with a concrete ask.

### Letter / memo
Short, no `toc`. `body` for the date/recipient block, a `heading` for the
subject line (or skip it), then prose paragraphs. `report` set (compact, sans)
suits an internal memo; `corporate` suits an external letter.

### 公文 (official Chinese document)
Use the `official` set. The builder auto-numbers the heading ladder
(一、/（一）/1．) and applies GB/T 9704 page setup. Structure:
`cover(标题) → 主送机关 (body) → 正文 with headings → 附件 → 署名/日期 (body)`.
See `cjk.md` → 公文 and `themes/official.md`.

```python
d = Doc("official", lang="zh-CN")
d.cover("关于推进政务数据共享的实施意见")
d.body("各有关单位：")
d.heading("总体要求")           # → 一、总体要求
d.body("坚持统筹规划、分步实施……")
d.heading("主要任务")           # → 二、主要任务
d.heading("数据归集", level=2)  # → （一）数据归集
```

## The outline you write first

Sketch the section sequence before any build script — a list of section titles,
each with the one point it makes. Two disciplines:

- **Section titles are conclusions, not labels.** "Onboarding redesign cut churn
  to 4%", not "Results". Reading only the headings should tell the whole story.
- **One idea per section.** A section that carries two arguments wants splitting.
  Use flowing prose within a section; use the headings to separate ideas.

For any researched fact, note the source URL beside the section so it lands in
the prose ("Per IDC 2025, ...") and you can cite it.

## Length sense

- **Obey an explicit length.** "Two pages", "around 1,500 words" → deliver it.
- **No length given → size to content, don't pad.** A status memo is 1–2 pages;
  a report 4–10; a paper as long as the argument needs.
- **A thin section is a structure problem, not a spacing one.** Merge it up,
  promote a `quote`/`table`, or cut it — never stretch line spacing to fill a
  page. The style set already owns spacing (`styles.md`).
- The `toc` and `page_numbers` earn their place once a document runs past ~3
  pages or has 4+ headings; skip both on a one-page letter or memo.

## Web-research protocol

Two passes. There's no dedicated web-search tool — use the `browser`
preinstalled skill or `curl` (the sandbox has network); `deep-research` for
heavier digs.

1. **Broad pass.** Scan the space, cluster results into sub-directions (trends,
   data, cases, counterpoints). Tell the user the directions you'll dig into
   before going deep.
2. **Deep pass.** Open the valuable sources via `browser` or `curl`, read them,
   extract *real* numbers and quotes. Record each source URL.
3. **Distill.** Cluster across sources, drop redundancy, keep the analytical
   conclusions and key data — never a pile of excerpts. Attribute key facts in
   the prose and keep a real **References** section (`heading` + `numbered`).

**Don't fabricate.** No invented statistics, citations, or quotes. If you can't
find a fact, say less rather than making one up. Don't web-search for things you
build natively — tables, comparisons, and figures-of-data are `table()` and
charts described in text, not searched screenshots (see `images.md`).
