---
name: docx
version: 2.0.0
description: >
  Create polished, fully-editable Word (.docx) documents end to end — research or
  read the source, outline the structure, pick a style set, build native editable
  content (cover / headings / body / lists / three-line tables / figures / quote /
  TOC / page numbers), then self-check before delivering. Handles English and
  Chinese (including 公文 / GB-T 9704 layout). Use whenever the user wants a
  report, paper, proposal, letter, memo, contract draft, or any Word document.
  Free fonts + open libraries only.
---

# docx — research, structure, and build self-checked Word documents

Don't jump straight to code. A good document is **content first, structure
second, formatting last**. Follow the pipeline; the depth is in the reference
docs — read the ones each step points to.

`<skill>` below = the absolute path `load_skill` returned. The builder is
`<skill>/scripts/docbuilder.py`; the checker is `<skill>/scripts/check_doc.py`;
reference docs live under `<skill>/references/`.

## Pipeline

1. **Understand the request & inputs.** Read every uploaded file in full first.
   Identify: language, audience, the document *type* (report / paper / proposal /
   letter / memo / 公文), any length/section requirements, and whether the user
   gave you a finished source, an outline, or just a topic.

2. **Choose the content mode** — where the content comes from
   (`references/content.md`):
   - **Summarize** — user gave a complete source → distill it (don't invent).
   - **Outline** — user gave structure/points → follow it, enrich each section.
   - **Research** — user gave only a topic → research the web first.

3. **Research (if needed).** Broad pass (map directions, tell the user what
   you'll dig into) → deep pass (read sources via the `browser` skill / `curl`,
   extract real data). Cite real sources; never fabricate. See `content.md`.

4. **Outline the document** — the section structure for the chosen type
   (`content.md` has skeletons): title block → (TOC) → sections (H1) with H2/H3 →
   conclusions/references/appendices as the type needs. One idea per section for
   reference docs; flowing prose for narrative ones.

5. **Pick one style set** for the whole document (`references/themes.md` catalog
   → read the chosen `references/themes/<name>.md`): `corporate` · `academic` ·
   `report` · `chinese` · `official` (公文). See `references/design.md` for
   profile→set and `references/styles.md` for the system. CJK specifics:
   `references/cjk.md`.

6. **Build** with `docbuilder.py`. One script imports `Doc`, calls the building
   blocks top to bottom, and saves to `/workspace/<doc>/document.docx`. Run with
   the sandbox python (python-docx / pillow installed). The builder handles the
   hard OOXML for you — East-Asian fonts, page-number fields, an updatable TOC,
   three-line tables, heading levels. **Don't hand-edit XML;** extend the builder
   if a block is missing. Figures: `references/images.md`.

7. **Self-check — mandatory.** Run the checker and fix **every ERROR**, then
   re-run until it prints `0 error(s)`:

   ```bash
   python3 <skill>/scripts/check_doc.py /workspace/<doc>/document.docx
   ```

   It flags CJK without a resolvable font, tables/images past the margins,
   leftover placeholder text, Normal-only documents, and heading-hierarchy skips.
   Fix by correcting content/structure — not by hand-patching XML.

8. **Deliver** the `.docx` as an artifact with a one-paragraph summary of the
   document's structure and key points. Then stop.

## Building blocks (the API)

`Doc(theme=..., lang="en-US")` then chain: `cover(title, subtitle, meta)` ·
`heading(text, level)` · `body(text)` · `bullets(items)` · `numbered(items)` ·
`table(headers, rows, caption)` (three-line, auto-sized) · `figure(image_path,
caption, width_in)` · `quote(text, attribution)` · `toc(title)` (updatable
field — Word refreshes on open) · `page_numbers()` · `page_break()` ·
`section_break(landscape)` · `save(path)`. Full reference: `references/styles.md`.

## Reference docs (read on demand)

| Read | For |
|---|---|
| `references/content.md` | content modes, document-type skeletons, outline, web research |
| `references/themes.md` + `references/themes/<name>.md` | choosing & using a style set |
| `references/styles.md` | the style-set system + full builder API |
| `references/design.md` | profile→set map, hierarchy, spacing, density |
| `references/cjk.md` | Chinese typography, fonts, 公文 / GB-T 9704 |
| `references/images.md` | when/how to source figures |

## Non-negotiables

- **One style set** for the whole document; don't hand-format per paragraph —
  extend `docbuilder.THEMES` if you need a new look.
- **Every section uses a real style** (the builder does this) — never a
  Normal-only document.
- **CJK always gets its East-Asian font** (the builder sets it) — verify with the
  checker; tofu is an error, not a warning.
- **Never deliver without a clean checker run.** No placeholder text, no empty
  headings, no `[TODO]`.
- Output is standard editable `.docx` (Word / WPS / LibreOffice). This skill is
  built for **creating** documents. If the user gives you an existing `.docx` to
  fill or lightly edit, open it directly with `python-docx` and modify it in
  place (it's installed) — `docbuilder`/the style sets are for new documents, so
  don't force an edit job through them. Heavy template-application / track-changes
  workflows are out of scope for now.
