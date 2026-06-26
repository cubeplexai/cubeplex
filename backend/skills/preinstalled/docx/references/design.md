# design — document design principles & choosing a set

The document's *look* is owned by the style set + the builder (which pre-balances
fonts, contrast, spacing, and the type scale so output passes the checker). Your
design job is higher-level: pick the **set** that fits the audience, choose the
right **structure** for the content, and keep **density** comfortable. Set
catalog in `themes.md`; the set system in `styles.md`; CJK in `cjk.md`.

## Principles

- **Content first.** Decide the narrative (`content.md`) before the look. A
  polished set over thin content still fails.
- **Hierarchy comes from the type scale, not decoration.** Title → H1 → H2 → H3
  → body is already sized and weighted by the set. Use real `heading()` levels;
  don't bold a `body()` line to fake a heading (the checker flags Normal-only
  documents and heading skips).
- **Spacing & proximity.** Related things sit close; a heading binds to the
  paragraph below it (the builder keeps headings with their next paragraph).
  Whitespace groups ideas — don't fight it by cramming.
- **Density ~60–70%.** A page that's comfortable to read fills roughly two-thirds
  of the text area. Past ~85% it reads bureaucratic; below ~50% it reads thin.
  The set's margins and line spacing target this — don't shrink margins or line
  spacing to pack more in.
- **One accent color.** Each set carries exactly one accent (e.g. corporate blue,
  report blue). It marks headings and the table header — let it do the
  highlighting; don't add more colors.
- **Restraint.** No per-paragraph font/size/color overrides. Consistency reads as
  care; novelty reads as noise (`styles.md` → "don't hand-format").

## Tables vs lists vs prose

Match the structure to the relationship in the content:

- **Prose** (`body`) — an argument that unfolds, with connective reasoning.
  Default for explanation.
- **Bullets** (`bullets`) — parallel, peer items with no order (features,
  reasons, criteria). Keep them short; a bullet that runs three lines wants to be
  prose.
- **Numbered** (`numbered`) — a sequence or ranked list (steps, references).
- **Table** (`table`) — values across shared dimensions (plan vs actual, options
  × criteria). If you'd describe it as "X by Y", it's a table. Don't turn a table
  into a screenshot — build it natively (`images.md`).

When in doubt between a long bullet list and prose, prefer prose for a reasoned
point and bullets only for genuinely parallel items.

## Alignment

- **Latin text: left-aligned**, ragged right. Justified Latin opens rivers of
  white space at body width; the sans/serif sets leave body left-aligned.
- **CJK text: justified.** Chinese reads cleanly justified because characters are
  monospaced; the `chinese` and `academic` sets justify the body.

You don't set this — the set does. It's here so you recognize correct output.

## Margins & density by document type

- **Business / report**: ~0.9–1.0in margins, compact-to-medium density. Skimmed
  at a desk; structure and tables carry it.
- **Academic**: 1.0in margins, generous line spacing (1.5), lower density. Long
  reading; the air aids comprehension.
- **公文**: fixed GB/T 9704 margins and 28pt line — non-negotiable, the set
  applies them.

## Profile → style-set map

Pick the set from the audience/occasion. Explicit brand/user requirements win
(use the closest set, or extend `docbuilder.THEMES`).

| Profile | Audience / occasion | Set | Density |
|---|---|---|---|
| **business** | reports, proposals, briefs, external letters | `corporate` | medium |
| **operational** | status reports, memos, internal updates | `report` | medium-high |
| **academic** | papers, theses, literature reviews, lectures | `academic` | low-medium |
| **Chinese general** | 中文报告/文稿, bilingual docs | `chinese` | medium |
| **official Chinese** | 公文, 通知, 意见, 函 | `official` | fixed (GB/T 9704) |

## Putting it together

1. Profile + audience → set (`themes.md` → read `themes/<name>.md`).
2. Mode + document type → outline & structure (`content.md`).
3. Build with `Doc(set)` and the building blocks; pick the right structure
   (table vs list vs prose) per section.
4. Decide where a figure earns its place (`images.md`); fonts come implicitly
   with the set (`styles.md` / `cjk.md`).
5. Build → **check → fix → deliver**:
   `python3 <skill>/scripts/check_doc.py file.docx`. The checker is the backstop,
   not the plan. Fix every ERROR; read the WARNs.
