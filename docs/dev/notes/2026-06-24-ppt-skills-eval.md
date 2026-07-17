# PPT Skills Evaluation — picking a default-preinstall slide skill

Date: 2026-06-24
Author: agent eval (worktree `feat/2026-06-24-office-skills`)

## Goal

We had collected several third-party PPT/Excel/PDF/Word skills from the web but
only smoke-tested them. This pass deeply evaluates the **PowerPoint** skills to
decide what (if anything) cubeplex should preinstall. PPT splits into two camps:

- **Native `.pptx`** — produce an editable PowerPoint binary (python-pptx / PptxGenJS / html2pptx).
- **HTML decks** — produce a self-contained HTML slide deck.

We pick **one winner per camp** for preinstall, scored on **process** (agent
trace: steps, tool errors, robustness) and **result** (downloaded artifact:
visual quality, content, layout integrity, editability).

## Method

1. Brought up the worktree backend (8024) + frontend (3024), registered a user
   (multi_tenant → org owner), minted an API key.
2. Org admin: installed the **clawhub** and **skills.sh** registries; set the
   org sandbox `network_default_action` to `allow`.
3. Discovered candidates via `GET /ws/{ws}/skills/discover` (53 unique PPT
   candidates across both registries) + read each shortlist `SKILL.md`.
4. Shortlisted 3 per camp, installed them, and ran the **same task** (a 6-slide
   "Rise of SLMs in 2026" deck) on the `pro` model, one skill per conversation.
5. Downloaded each artifact; rendered `.pptx` via LibreOffice→PDF→PNG and HTML
   via headless Chromium; inspected per-slide screenshots + python-pptx structure.
6. Pulled per-run tool-call sequences from the messages API for the process score.

### Two install paths tested

A key methodological point emerged: **registry-installed** skills get a
canonical name with a colon (`<org>:<skill>`), while **preinstalled** skills
have a plain name. This changes the sandbox mount path and turned out to
dominate multi-file-skill behaviour (see Bug 1). So each multi-file candidate
was tested both as a registry install *and* as a clean preinstalled skill
(staged into `skills/preinstalled/` + reseeded). The preinstall path is the one
that matters for the actual decision.

## Candidates & results

Task held constant; model = `pro`; same prompt.

### Native `.pptx`

| Skill (source) | Registry install | Clean preinstall | Result quality | Process |
|---|---|---|---|---|
| **create-pptx** (clawhub/scottliu007) | ⚠ recovered after failed ref reads | ✅ clean | **Best**: dark, 156 shapes, driver cards + styled comparison, **no overlap/overflow**, fully editable | 26 tools, reads refs |
| pptx-generator (clawhub/tobewin) | ✅ clean (self-contained) | = same | Clean, real table; emoji render as □ (font), some low-contrast table labels | **Cleanest**: 12 tools, no file reads |
| pptx (Anthropic, preinstalled baseline) | ❌ fork w/ hardcoded `/skills/pptx/` paths stuck | ✅ 186KB | Richest design *intent* (18 palettes) but html2pptx produced **title overlap + clipped card text** | Heaviest: html2pptx + LibreOffice + Playwright; 26 tools |

**Native winner: `create-pptx`** — cleanest, defect-free, best-looking output;
only python-pptx + pillow. Robust runner-up: **pptx-generator** (zero external
deps, fastest, survives even the hostile registry path).

### HTML

| Skill (source) | Registry install | Result quality | Process |
|---|---|---|---|
| **openclaw-slides** (clawhub/leoyeai) | ✅ worked (inlines critical CSS/JS) | Excellent: techy dark, structured comparison table | **8 tools**, self-contained |
| frontend-slides (clawhub/ken0122) | ❌ failed (depends on reference files) | Best-looking: editorial serif, winner-highlighted table | 16 tools (clean) |
| html-slides (skills.sh, reveal.js) | ✅ worked | Stock reveal.js, sparse, CDN-dependent at runtime | 8 tools |

**HTML winner: `openclaw-slides`** — beautiful, self-contained, efficient, and
the only one robust enough to also work on the hostile registry path.
Runner-up: **frontend-slides** (marginally more refined editorial aesthetic, but
fragile — fails entirely if its reference files aren't readable).

### Also evaluated: `ppt-master` (github.com/hugohe3/ppt-master)

The most ambitious candidate by far: source docs → Markdown → hand-authored SVG
pages → PPTX, with a 7-step interactive pipeline, brand/layout/deck templates,
icon libraries, LaTeX rendering, AI image generation.

**Not recommended for preinstall.** Disqualifiers:
- **Size**: skill payload is **97 MB / 12,146 files** — over the registry 50 MB
  bundle cap (registry install timed out) and impractical to vendor as a preinstall.
- **External deps**: needs OpenAI/MiniMax API keys (images), codecogs/quicklatex
  (LaTeX web services), pixabay/pexels (image search) — network + paid keys.
- **Interactive by design**: Flask "Eight Confirmations" browser UI + live-preview
  editor with blocking gates — built for human-in-the-loop, stalls an autonomous run.
- **Output**: SVG→PPTX embeds PNG+SVG images → slides are not natively editable.

Design ambition ~9/10, but platform fit ~2/10. It's a standalone interactive
design product, not a lightweight agent skill.

## Recommendation

- Preinstall **`create-pptx`** for the native-pptx camp.
- Preinstall **`openclaw-slides`** for the HTML camp.
- Do not preinstall the Anthropic html2pptx `pptx` (conversion defects + heaviest
  toolchain), reveal.js `html-slides` (stock + runtime CDN), or `ppt-master` (too
  heavy / interactive / external-dependent).

## Platform bugs found (independent of skill choice)

### Bug 1 — registry-installed skills can't read their bundled files

Registry skills get a colon canonical name (`<org>:<skill>`). Files mount at
`/.skills/{name}/{version}/` (`cubeplex/sandbox/lazy.py`), and the system prompt
only told the agent the *pattern* `/.skills/<name>/<version>/`. The LLM
mis-renders the colon as a path separator and drops the version segment, so
reads of `scripts/`/`references/` fail. Preinstalled (plain-name) skills had 0
read errors. **Fix:** normalise `:`→`__` in a single `sandbox_skill_dir()` helper
(`cubeplex/skills/sandbox_paths.py`) used by both the file-sync and `load_skill`;
`load_skill` now returns the exact `path` and the prompt tells the agent to use
it verbatim instead of constructing it.

### Bug 2 — a missing-file read tears down the whole sandbox

`LazySandbox.download()` caught *any* exception (incl. file-not-found) as
"sandbox died" → nulled and **recreated the sandbox**, wiping `/workspace`. This
amplified Bug 1 into recreate-storms that destroyed in-progress work. **Fix:**
`download()` no longer recreates on failure — the read error surfaces to the
agent (a corrigible `file_read` error); a genuinely dead sandbox is still
recovered by the next `execute`/`upload`.

### Bug 3 — worktree backend crashes without egress mTLS certs

`config.development.local.yaml` enables the egress mTLS listener with relative
cert paths `certs/egress/*.pem`, but worktree provisioning doesn't generate
them, so the uvicorn listener's `serve()` fails to load the cert chain and takes
the backend down on teardown. Handled out-of-band (env var disables the
exchanger in worktrees); worktree `init` should generate the certs or default
the listener off when certs are absent.

> Note: the repeated backend deaths during this eval were primarily an
> environment collision — a *separate* session running the main-repo backend was
> periodically `pkill -f main.py`-ing (matching the worktree's same-named
> process) and holding egress port 9443. Worked around by running the eval
> backend as `eval_server_8024.py` with the egress listener on 9444.

---

# Addendum (2026-06-25): docx eval + PPT revisit with kimi_skills

After the PPT pass, evaluated **docx**, and revisited PPT against the local
`~/kimi_skills` set (`docx`, `pptx`, `pptx-swarm`, …).

## docx — winner: `kimi-docx`

docx doesn't split into two camps like PPT — serious contenders are all
native-programmatic; the real axis is toolchain. Head-to-head (same SLM brief
task, `pro` model), rendered via LibreOffice (note: needed `libreoffice-writer`,
not just `-impress`):

| Skill | Toolchain | Runtime | Output |
|---|---|---|---|
| **kimi-docx** (`~/kimi_skills/docx`) | Node `docx-js` + python-docx edits | **~283s** | finished doc: **populated TOC** (page numbers + leaders), proper `styles.xml`+`numbering.xml`, H1/H2 styles, shaded 6×3 table |
| minimax-docx (= **current preinstalled `docx`**) | .NET / OpenXML SDK | **~901s** (hit 15-min cap) | content-rich but **TOC unpopulated** placeholder, package **missing `styles.xml`** |

The sandbox image ships Node + the `docx` npm package pre-baked; there is no warm
.NET/nuget, so kimi-docx's path is ~3× faster and produces a better-formed file.
**Recommend replacing the current MiniMax-based preinstalled `docx` with
`kimi-docx`.** Other registry docx candidates were thin (`docx-manipulation`,
MCP-coupled), prose-only (`ivangdavila`), or off-topic (`ljg-word*` are vocabulary
tools).

## PPT revisit — kimi `pptx` changes the *native* pick (conditionally)

kimi `pptx` is a native-pptx skill using a custom **`.pptd` YAML DSL → .pptx**
compiler (`kimi_ppt_dsl.pyz`), with a centralized theme system, tables, **native
charts**, and a built-in **format/overflow checker**. Local engine probe (agent
authored a `.pptd` for the same SLM deck → `check` clean after 1 fix → `convert`):
the rendered deck is **the best native output of the whole eval** — cohesive
theming, styled comparison table, and a metrics slide with a real bar chart.

It does **not** affect the HTML camp — `openclaw-slides` stands.

The catch: the engine is a **250 MB** `kimi_ppt_dsl.pyz` (the skill *docs* are only
~260 KB). Over the registry 50 MB cap, and too heavy to sync per-sandbox. So:

- **If the engine is baked into the sandbox image** (like node/libreoffice already
  are) and only the thin skill docs ship as the bundle → **kimi `pptx` is the
  native winner** (best quality + checker). `pptx-swarm` (same engine, multi-agent)
  covers long/batch decks.
- **If it must ship as a synced skill bundle** (250 MB/sandbox) → impractical as a
  default; keep **`create-pptx`** as the lightweight native default and offer kimi
  `pptx` as an admin/opt-in premium skill.

### Revised recommendation
- **HTML deck**: `openclaw-slides` (unchanged).
- **Native pptx**: `kimi pptx` if its engine can live in the sandbox image; else
  `create-pptx` (lightweight) as default + kimi `pptx` opt-in.
- **docx**: `kimi-docx` (replaces the current MiniMax preinstall).

---

# Addendum 2 (2026-06-25): IP decision + our own `deckcraft` pptx skill

**IP:** the kimi skills are extracted from the Kimi desktop client (no OSS
license) — not shippable. Decision: learn from their *public SKILL.md
methodology* only, build clean-room on open libs + free fonts. Also: the kimi
pptx engine's 250MB is **bundled fonts**, 141MB of which are **proprietary**
(Monotype Arial/Times/Impact, MS Tahoma/YaHei, SinoType STHeiti) — would be a
license violation to ship; the staging script drops them, keeping 24 OFL/free
fonts (87MB).

**Pilot — `backend/skills/preinstalled/eval-deckcraft/` (native pptx):**
clean-room skill = python-pptx builder (`scripts/deckbuilder.py`, themed
cover/agenda/cards/comparison/metrics/closing on the MIT-0 pptx-generator +
create-pptx patterns) + an overflow/contrast/off-canvas **self-checker**
(`scripts/check_deck.py`, PIL metrics on free fonts; borrowed kimi discipline).
Free fonts only (DejaVu/Noto CJK), no emoji glyphs, no proprietary engine.

End-to-end agent run (SLM deck task, `pro` model): the agent loaded the skill,
built the deck, ran the checker → **1 error → fixed → re-checked to 0/0**, and
delivered a cohesive, defect-free 6-slide deck (dark/teal theme, accent cards,
highlight-column comparison table, big metrics) — visually on par with kimi
pptx / create-pptx, but fully open + self-owned. The "generate → self-check →
fix → deliver" loop ran autonomously, which is the key quality mechanism.

v0.1 gaps / next: add a native `chart` archetype (kimi had one), more themes,
CJK test; then promote `deckcraft` → preinstalled `pptx`, and apply the same
pattern to docx; adopt openclaw-slides (lightly enriched) for HTML.

## Addendum 3 (2026-06-25): `deckcraft` → promoted to preinstalled `pptx` v1.1.0

v1 polish done and the skill promoted to replace the (Anthropic html2pptx)
preinstalled `pptx`:
- **Native chart** archetype (`Deck.chart`) — themed column chart, data labels,
  transparent background (shows through dark themes), value axis hidden.
- **CJK** — runs set the East-Asian (`<a:ea>`) typeface (Noto CJK), so Chinese
  renders instead of tofu. Verified with a full Chinese deck.
- **5 themes** — midnight / ember / orchid (dark) + daylight / paper (light);
  daylight `muted` darkened to clear WCAG 4.5:1.
- **Fonts**: Liberation Sans/Serif + Noto CJK — all already in the sandbox
  image, so nothing new is needed there (the earlier kimi-font / .NET-removal
  image edits were reverted; `.NET` stays until the MiniMax docx is replaced).

End-to-end agent run as `pptx` (model `pro`): loaded the skill, built a 6-slide
deck **including a working native chart**, ran the checker → fixed → 0/0,
delivered. Output is cohesive and defect-free — matches kimi/create-pptx quality
with only open libs + free fonts + clean-room code we own.

Skill lives at `backend/skills/preinstalled/pptx/` (SKILL.md +
scripts/deckbuilder.py + scripts/check_deck.py). Next: same pattern for docx
(then drop .NET), and adopt+enrich openclaw-slides for the HTML camp.
