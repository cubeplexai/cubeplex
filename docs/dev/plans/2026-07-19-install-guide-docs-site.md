# Installation Guide on the Docs Site — Plan

Spec: [docs/dev/specs/2026-07-19-install-guide-docs-site-design.md](../specs/2026-07-19-install-guide-docs-site-design.md)

**Goal:** Move the docker-compose and kubernetes install guides from
`deploy/*.md` into a new "Deployment" section on `docs/site`, in both `en`
and `zh-Hans`, and turn the original `deploy/*.md` files into pointers so
there's one maintained copy.

**Architecture:** Three new Docusaurus pages under
`docs/site/docs/deployment/` (`overview`, `docker-compose`, `kubernetes`),
each mirrored under `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/deployment/`.
A new sidebar category wires them in. Content is restructured (not rewritten)
from the five source files (`deploy/docker-compose/INSTALL.md`,
`deploy/docker-compose/OPENSANDBOX.md`, `deploy/kubernetes/INSTALL.md`,
`deploy/kubernetes/INSTALL.zh.md`) into the new page split, then those source
files — plus `deploy/README.md`, the two `deploy/*/README.md`, and root
`README.md` — are edited down to point at the new pages instead of holding
the full prose.

**Tech stack:** Docusaurus 3.10 (`docs/site`), markdown/MDX, no code changes.

---

## Unit 1 — `deployment/overview.md` (English)

**Files:** `docs/site/docs/deployment/overview.md` (new).

**Content mapping:**
- Comparison table: docker-compose (single host, simplest) vs. kubernetes
  (multi-node, Helm) — new content, framed from `deploy/README.md`'s existing
  "Pick a target" table plus the "Both modes share the same backend/frontend
  container images" note.
- LLM provider configuration reference — lifted from
  `deploy/kubernetes/INSTALL.md` §4.4 (`values.local.yaml` §4.4 is the fuller
  of the two near-identical copies; the docker-compose §4.4 is a strict
  subset). Keep all three provider modes (built-in preset / fully custom /
  example `arkcode`), the `default_model` / `fallback_models` format notes,
  and the minimal-viable-config example.
- Required auth secrets table (`jwt_secret`, `csrf_secret`, `vault_key` +
  generation commands) — lifted from kubernetes §4.3 (docker-compose's
  equivalent table in §4.3 is the same three fields).

**Interfaces:** frontmatter `sidebar_position: 1`, doc id `deployment/overview`.

**Tests/verification:** none code-side; covered by Unit 7's site build.

## Unit 2 — `deployment/docker-compose.md` (English)

**Files:** `docs/site/docs/deployment/docker-compose.md` (new).

**Content mapping** (source: `deploy/docker-compose/INSTALL.md` §1–7, plus
`deploy/docker-compose/OPENSANDBOX.md` folded in as a new "Optional: sandbox
execution (OpenSandbox)" section):
- §1 Prerequisites, §2 Architecture (keep the ASCII diagram), §3 Build images,
  §4 Configure (`.env` + two YAML files, all four subsections 4.1–4.4 — 4.4
  becomes "see [Deployment overview → LLM providers](./overview.md)" instead
  of repeating the table), §5 Up/down/logs, §6 Verification, §7
  Troubleshooting — carried over section-for-section, commands unchanged.
- OpenSandbox section: what the overlay deploys, quickstart config steps,
  compatibility matrix (✅/⚠/🚫 tables), verifying, tearing down — all facts
  from `OPENSANDBOX.md` kept; drop only the meta-narration ("verified
  empirically against opensandbox-server v0.1.14 by issuing the requests
  cubeplex would make" becomes a plain statement of the resulting behavior).

**Interfaces:** frontmatter `sidebar_position: 2`, doc id `deployment/docker-compose`.

**Tests/verification:** none code-side; covered by Unit 7.

## Unit 3 — `deployment/kubernetes.md` (English)

**Files:** `docs/site/docs/deployment/kubernetes.md` (new).

**Content mapping** (source: `deploy/kubernetes/INSTALL.md` §1–8):
- §1 Prerequisites, §2 Architecture (ASCII diagram), §3 Build & push images
  (including the mirror-knob table and release/sandbox-version notes), §4
  Author `values.local.yaml` (4.1 image tags, 4.2 backend non-secret config,
  4.3 secrets, 4.4 LLM providers → link to `overview.md` instead of
  repeating, 4.5 sandbox, 4.6 bundled infra passwords, 4.7 ingress, 4.8
  storage class, 4.9 OpenSandbox subchart, 4.10 egress secret-injection, 4.11
  docling), §5 Install/uninstall, §6 Post-install verification, §7
  Troubleshooting, §8 Values reference + minimal `values.local.yaml` example
  — carried over section-for-section.
- Drop the repo-relative link to `docs/dev/specs/2026-06-10-helm-deploy-design.md`
  (internal engineering doc, not part of the public site).

**Interfaces:** frontmatter `sidebar_position: 3`, doc id `deployment/kubernetes`.

**Tests/verification:** none code-side; covered by Unit 7.

## Unit 4 — Sidebar wiring and cross-links

**Files:**
- `docs/site/sidebars.ts` — add the `Deployment` category (items:
  `deployment/overview`, `deployment/docker-compose`, `deployment/kubernetes`)
  between the existing `Guides` and `Administration` categories.
- `docs/site/docs/intro.mdx` — in the "Deployment options" Tabs, the
  self-hosted `TabItem` gets one added sentence linking to
  `./deployment/overview.md`.
- `docs/site/docs/getting-started/quick-start.md` — the self-hosted `TabItem`
  in step 1 gets a leading sentence ("If CubePlex isn't installed yet, see
  the [Deployment guide](../deployment/overview.md) first.") before its
  existing numbered steps.

**Tests/verification:** covered by Unit 7 (broken-link check catches any
typo'd doc id).

## Unit 5 — zh-Hans translations

**Files:**
- `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/deployment/overview.md`
- `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/deployment/docker-compose.md`
- `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/deployment/kubernetes.md`
- Corresponding zh-Hans edits to `intro.mdx` and `getting-started/quick-start.md`
  (the sentences added in Unit 4), matching how those files' existing
  zh-Hans copies mirror the English structure 1:1.

**Content mapping:** `kubernetes.md`'s translation starts from
`deploy/kubernetes/INSTALL.zh.md`, restructured to match the English page's
new section split (same restructuring Unit 3 did to the English source).
`docker-compose.md` and `overview.md` have no existing Chinese source —
translate fresh, reusing the terminology `INSTALL.zh.md` already established
(e.g. how it renders "沙箱", "密钥", "存储类" etc.) for consistency across the
three pages.

**Tests/verification:** covered by Unit 7 (Docusaurus builds both locales
from the same build; broken links in zh-Hans pages fail the same way).

## Unit 6 — Retire `deploy/*.md` prose into pointers

**Files:**
- `deploy/docker-compose/INSTALL.md` — replace body with a short stub:
  what this covers, link to `docs/site` `deployment/docker-compose.md` on
  `cubeplex.ai/docs` as the canonical guide.
- `deploy/kubernetes/INSTALL.md` — same, linking to `deployment/kubernetes.md`.
- `deploy/kubernetes/INSTALL.zh.md` — same, linking to the zh-Hans
  `deployment/kubernetes.md`.
- `deploy/docker-compose/OPENSANDBOX.md` — same, linking to the "Optional:
  sandbox execution" section of `deployment/docker-compose.md`.
- `deploy/README.md` — "Pick a target" table's Doc column points at the docs
  site URLs instead of the local `INSTALL.md` paths.
- `deploy/docker-compose/README.md`, `deploy/kubernetes/README.md` — keep
  their existing quickstart command blocks; change "See INSTALL.md for..."
  references to point at the docs site.
- Root `README.md` — the two "installation guide" links point at the docs
  site `deployment/docker-compose` and `deployment/kubernetes` pages.

**Core logic:** each stub keeps enough text that someone landing on the file
via GitHub search still understands what it's for and where to go — not a
bare "moved" one-liner. No content in these files should still be the thing
someone actually follows step-by-step; that's the new docs-site pages' job.

**Tests/verification:** `grep -rn "INSTALL.md\|INSTALL.zh.md" --include=*.md .`
from repo root should show only the (now-stub) files themselves and any
frozen `docs/dev/` snapshots (left alone per the "don't rewrite history"
rule) — no other file should still point at removed content.

## Unit 7 — Build verification

**Files:** none (verification only).

**Steps:**
```bash
cd docs/site
pnpm build        # onBrokenLinks: 'throw' — catches bad doc ids/links in both locales
pnpm check:urls   # existing URL-format check
```
Green build output is the evidence pasted into the PR description per
AGENTS.md rule 9. Also spot-check in a local `pnpm start` that the
Deployment category renders in the sidebar and both locales load.

---

## Plan self-review

1. **Spec coverage** — every spec section (new pages, sidebar, i18n,
   cross-links, `deploy/` stubs) maps to a unit above (Units 1–6); the
   spec's success criteria map to Unit 7's build check + Unit 6's grep.
2. **Interface consistency** — doc ids used in Unit 4's sidebar entries
   (`deployment/overview`, `deployment/docker-compose`,
   `deployment/kubernetes`) match the file names created in Units 1–3.
3. **Vagueness scan** — content mapping for each page cites the exact source
   section numbers being carried over, so "restructure the install guide"
   isn't a hand-wave at execution time.
