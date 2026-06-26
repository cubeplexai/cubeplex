# Style set: academic — papers & research

Formal serif, the look of a journal article. A single serif across headings and
body — Liberation Serif (a Times-metric face) — gives the document the gravity
of published scholarship, and the conventions that go with it: **numbered
headings**, a **first-line indent** that separates paragraphs without blank
lines, and generous **1.5 line spacing** for dense reading. Pure black ink, no
accent color — rigor carries the page, not decoration.

## What it uses (docbuilder.THEMES["academic"])
- Heading and body both **Liberation Serif**; CJK both **Noto Serif CJK SC**.
- Scale: title 22pt → H1 16 → H2 14 → H3 12 → **body 12** → caption 10.
- Color: everything black `#000000` — no accent.
- Spacing: 1.5 line, **0pt after** (first-line indent separates paragraphs),
  1.0in margins, **numbered headings**, justified body.

## Use it when
- **Papers, theses, dissertations, literature reviews, lectures.**
- **Long-form, prose-heavy** documents where paragraphs and citations live.
- **Humanities, policy, and research** writing that values a considered tone.

## Avoid it when
- It's a **quick status or memo** — the serif gravity feels too formal → `report`.
- It's a **business deck-style brief** with lots of tables → `corporate`.
- It's a **Chinese paper** wanting Song/Hei → `chinese`.

## Recommended structure
`cover → heading("Abstract", numbered=False) + body → numbered sections
(Introduction → Method → Results → Discussion → Conclusion) → figures/tables
inside Results → heading("References", numbered=False)`. Body headings
auto-number ("1 Introduction", "2 Method", ...); front/back matter (Abstract,
References) passes `numbered=False`. Cite as you go; let
`figure()`/`table()` carry the data with "Figure N:" / "Table N:" captions.

## Copy tone
- Precise, measured, qualified: "suggests", "consistent with", "within CI".
- Full sentences and proper terms — write for a reader, not a room.
- Cite and define; let rigor, not adjectives, carry the authority.

## Example
```python
import sys; sys.path.insert(0, "<skill_path>/scripts")
from docbuilder import Doc
d = Doc(theme="academic")
d.cover("Effective Context Length in Long-Context Models",
        subtitle="A measurement study", meta="NLP Lab · Draft, 2026")
d.heading("Abstract", numbered=False)   # front matter: unnumbered
d.body("We measure how retrieval accuracy degrades as context length grows, "
       "and propose a training signal that extends the usable window.")
d.heading("Introduction")             # → "1 Introduction"
d.body("Recent models advertise long context, yet recall often falls well "
       "before the advertised limit...")
d.heading("Method")                   # → "2 Method"
d.figure("/workspace/paper/images/recall.png",
         caption="Figure 1: Recall vs context length, baseline vs ours")
d.heading("References", numbered=False)
d.numbered(["Vaswani et al., Attention Is All You Need, 2017.",
            "Press et al., Train Short, Test Long, 2022."])
d.save("/workspace/paper/out.docx")
```
Then: `python3 <skill_path>/scripts/check_doc.py /workspace/paper/out.docx` and
fix any errors.
