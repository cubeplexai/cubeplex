# Themes — catalog & selection

A theme fixes the **whole** look: color system + font pairing + personality.
Pick **one** theme for the entire deck in the design-plan step, then read its
deep-dive `references/themes/<name>.md` for palette rationale, the slide flow it
favours, and copy tone. The exact hex values + fonts are the single source of
truth in `deckbuilder.THEMES` (run `python3 <skill>/scripts/deckbuilder.py` to
print this catalog live).

## Pick by profile

| Theme | Profile | Mode | Accent | Display / Body | Use when |
|---|---|---|---|---|---|
| **midnight** | tech | dark | teal `#00D2A0` | Inter / Inter | product & engineering keynotes, demos, technical strategy |
| **ember** | editorial | dark | amber `#E8A13A` | Oranienbaum / Inter | strategy briefs, opinion pieces, narrative-led talks |
| **daylight** | corporate | light | blue `#2563EB` | Inter / Inter | business reports, status reviews, QBRs |
| **slate** | minimal | light | indigo `#4F46E5` | Inter / Inter | consulting, data-heavy, restrained internal decks |
| **orchid** | creative | dark | magenta `#C264FF` | Anton / Inter | launches, brand & vision decks, bold concepts |
| **bloom** | promotion | light | coral `#F0476A` | Barlow / Inter | marketing, product launches, benefit-led pitches |
| **sage** | calm | light | emerald `#2F9E6E` | Inter / Inter | sustainability, health, research, trust-building |
| **noir** | high-impact | dark | yellow `#F2D544` | Anton / Inter | single big ideas, manifestos, slogan-driven decks |
| **paper** | academic | light | terracotta `#B4622D` | Sorts Mill Goudy / Quattrocento Sans | papers, lectures, long-form, humanities (CJK: LXGW WenKai) |

## How to choose

1. **Audience & occasion drive it.** Boardroom report → `daylight`/`slate`.
   Conference keynote → `midnight`/`orchid`. Investor/strategy → `ember`/`slate`.
   Marketing → `bloom`. Academic → `paper`. A bold single thesis → `noir`.
2. **Light vs dark.** Dark themes (`midnight`, `ember`, `orchid`, `noir`) read as
   modern/premium and suit projected keynotes; light themes
   (`daylight`, `slate`, `bloom`, `sage`, `paper`) suit printed/read-at-desk
   reports and bright rooms.
3. **Serif vs sans display.** `ember` (Oranienbaum) and `paper` (Sorts Mill
   Goudy) signal editorial/authoritative; `orchid`/`noir` (Anton) signal bold;
   the Inter themes signal clean/neutral.
4. **One theme only.** Don't mix. If the brand needs a specific accent, the
   closest theme + a custom accent is better than hand-tuning every slide.

After picking, **read `references/themes/<name>.md`** — it tells you the slide
flow and tone that make that theme sing. CJK content: all themes set Noto Sans
CJK by default; `paper` uses LXGW WenKai (霞鹜文楷) for an editorial Chinese look.
For Chinese display fonts (得意黑/站酷 etc.) see `references/fonts.md`.
