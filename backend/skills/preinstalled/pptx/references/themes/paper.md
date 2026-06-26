# Theme: paper — academic

Warm paper and ink — the look of a well-set book or journal. A cream paper
background with a soft beige surface and a muted terracotta accent gives paper a
scholarly, unhurried character built for long-form reading. The Sorts Mill Goudy
serif display lends headings classic authority, Quattrocento Sans keeps body
text humane and readable, and LXGW WenKai (霞鹜文楷) handles CJK with the same
handwritten warmth; the terracotta `B4622D` underlines like a careful annotation.

## Palette (defined in deckbuilder.THEMES["paper"])
- background `FBF7F0` / surface `F1E9DC` — warm cream "paper" bg eases long
  reading; the beige surface frames quotes and tables like inset blocks.
- primary `2A2620` (headings) / muted `6B6253` (body) — warm near-black "ink"
  headings; the soft sepia body reads like print, gentle over many lines.
- accent `B4622D` — terracotta kickers, rules, highlighted column, chart bars.
  Adds a restrained scholarly highlight, like a margin note in red-brown.

## Typography
- Display: Sorts Mill Goudy — headings, kickers, big numbers
- Body: Quattrocento Sans — prose, tables, captions
- CJK: LXGW WenKai
A true serif display over a humanist sans body is the long-form move: Goudy
gives headings the gravity of a printed title page, Quattrocento Sans keeps
dense paragraphs comfortable. WenKai matches that warmth for Chinese text — the
only theme with a non-Noto CJK face.

## Use it when
- **Academic papers, lectures, seminars, and literature reviews.**
- **Long-form / prose-heavy** decks where paragraphs and citations live.
- **Humanities, policy, and research** talks that value a considered tone.
- Bilingual / CJK decks wanting a warm, literary Chinese face.

## Avoid it when
- The deck needs **modern product energy** — go `midnight` / `orchid`.
- It's a **dashboard or quick status** — the serif gravity feels too formal.
- You want **high-contrast punch** — go `noir`.

## Recommended slide flow
`cover → agenda → statement → comparison → cards → comparison → closing`. paper
leans on prose and `comparison` — academic arguments unfold in measured text and
structured side-by-sides (methods, results, prior work). Use `statement` for a
key claim or quotation, and let `cards` hold definitions or findings. Charts are
welcome but secondary to the written argument.

## Copy tone
- Precise, measured, and qualified: "suggests," "consistent with," "within CI."
- Full sentences and proper terms — write as you would for a reader, not a room.
- Cite and define; let rigor, not adjectives, carry authority.

## Example
```python
import sys; sys.path.insert(0, "<skill_path>/scripts")  # path from load_skill
from deckbuilder import Deck
d = Deck(theme="paper")
d.cover("Reading between the lines", "Attention patterns in long-context models",
        kicker="Working paper", meta="NLP Lab • Draft, 2026")
d.statement("Longer context does not guarantee better recall.",
            attribution="Section 4, this work", kicker="Central claim")
d.comparison("Recall by context length",
             headers=["Length", "Baseline", "Ours"], highlight_col=2,
             rows=[["4k tokens", "0.91", "0.93"], ["32k tokens", "0.74", "0.88"],
                   ["128k tokens", "0.52", "0.81"]])
d.cards("Why it holds", kicker="Mechanism",
        cards=[("Positional decay", "Distant tokens lose weight under standard encodings."),
               ("Retrieval heads", "A small set of heads does most long-range work.")])
d.closing("Contributions", ["A measure of effective context length",
                            "A training signal that extends it"],
          cta="Preprint and code in the appendix")
d.save("/workspace/deck/out.pptx")
```
Then: run `python3 <skill_path>/scripts/check_deck.py out.pptx` and fix any errors.
