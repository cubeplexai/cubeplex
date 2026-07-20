# Installation Guide on the Docs Site — Design

Status: draft
Worktree: `feat/2026-07-19-install-guide-docs-site`

## Goal

Give operators a complete, detailed system installation guide on the public
docs site (`docs/site`, published at `cubeplex.ai/docs`), and make that guide
the single source of truth — the existing `deploy/*/INSTALL.md` files become
short pointers instead of a second copy.

## Context

`docs/site/docs` currently has no installation/deployment content at all.
`getting-started/quick-start.md` has a "Self-hosted" tab, but it starts from
"the instance already exists" and only covers registering an account.

The actual installation instructions live in `deploy/`, written for engineers
running the scripts directly out of the repo:

- `deploy/docker-compose/INSTALL.md` — single-host docker-compose guide
  (prereqs, build images, three-file config, up/down, verification,
  troubleshooting).
- `deploy/docker-compose/OPENSANDBOX.md` — optional sandbox-execution overlay
  for docker-compose mode, including a compatibility matrix of what alibaba's
  OpenSandbox can and cannot do under docker runtime.
- `deploy/kubernetes/INSTALL.md` + `INSTALL.zh.md` — Helm install guide
  (prereqs, architecture, image build/push, `values.local.yaml` authoring,
  install, verification, troubleshooting, full values reference).
- `deploy/README.md`, `deploy/docker-compose/README.md`,
  `deploy/kubernetes/README.md` — directory landing pages with short
  quickstarts and a link table.
- Root `README.md` links directly to `deploy/docker-compose/INSTALL.md` and
  `deploy/kubernetes/INSTALL.md`.

`deploy/docling-serve/` (a standalone optional document-conversion service,
install script only, no prose doc) is out of scope — it's already referenced
from `kubernetes/INSTALL.md` §4.11 for the in-cluster case, and has no
docker-compose integration today.

No CI workflow references these `INSTALL.md` files directly (only image build
context paths), so retiring them into pointers is contained to the docs
themselves.

## Approaches considered

1. **Adapt into docs/site, keep `deploy/*.md` as the parallel exact-command
   reference.** Two prose copies (site = narrative, deploy = commands),
   synced by convention. Rejected: the user explicitly asked for content
   ownership to move, and two hand-maintained copies of the same install
   steps will drift the first time either one is edited without the other.
2. **Docs site page links out to `deploy/*.md` on GitHub.** Zero duplication,
   but sends readers off-site to unstyled, non-i18n raw markdown — doesn't
   satisfy "a complete, detailed guide in the user docs."
3. **Move the content into docs/site; `deploy/*.md` become short pointer
   stubs back to the docs site (chosen).** Single source of truth. The
   `deploy/` files stay as the thing a repo-browsing engineer finds first,
   but their job becomes "here's where the real guide is," not a second copy
   to keep in sync.

## Design

### New docs/site pages

`docs/site/docs/deployment/`:

| File | Content |
|---|---|
| `overview.md` | Choosing a deployment target (comparison table: docker-compose vs. kubernetes), shared architecture note (same backend/frontend images either way), the **LLM provider configuration reference** (provider block YAML shape — identical in both modes today, so it's documented once and linked from both mode pages), and the three required auth secrets (`jwt_secret`, `csrf_secret`, `vault_key`) with their generation commands. |
| `docker-compose.md` | Full docker-compose guide, restructured from `deploy/docker-compose/INSTALL.md`: prerequisites, architecture, build images, configure (`.env` + two YAML files), up/down/logs, verification, troubleshooting, and an "Optional: sandbox execution (OpenSandbox)" section carrying the operational parts of `OPENSANDBOX.md` (what it deploys, quickstart, compatibility matrix, verifying, tearing down) rewritten in the guide's voice — the empirical "verified against opensandbox-server v0.1.14 by reading the source" investigation framing is dropped in favor of stating the resulting facts directly. |
| `kubernetes.md` | Full Helm guide, restructured from `deploy/kubernetes/INSTALL.md`: prerequisites, architecture, build & push images, authoring `values.local.yaml` (image tags, backend config, secrets, LLM providers via a link to `overview.md`, sandbox, bundled infra, ingress, storage class, OpenSandbox subchart, egress secret-injection, docling), install, post-install verification, troubleshooting, values reference. |

Each page keeps the source `INSTALL.md`'s section structure and exact
commands/config field tables — this is a restructuring and voice pass for a
public audience (Docusaurus `Tabs`/admonitions, no links into internal
`docs/dev/specs/`, no repo-relative links), not a content rewrite that risks
losing operational accuracy. Screenshot placeholders are not needed here —
this is a CLI/config-file guide with no UI to capture (the existing ASCII
architecture diagrams are kept as fenced code blocks, matching how the source
docs already render them).

### Sidebar

New top-level category in `sidebars.ts`, positioned after **Guides** and
before **Administration** (install-then-administer ordering, and it keeps the
existing end-user content — Getting Started, Guides — first for the more
common cloud-user reader):

```ts
{
  type: 'category',
  label: 'Deployment',
  items: [
    'deployment/overview',
    'deployment/docker-compose',
    'deployment/kubernetes',
  ],
},
```

### i18n (zh-Hans)

All three pages get a zh-Hans translation under
`docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/deployment/`,
matching the existing pattern where every page in the site is maintained as a
translated pair. `deploy/kubernetes/INSTALL.zh.md` is the primary source to
translate from for `kubernetes.md`'s Chinese version (it's already a human
translation of the English `INSTALL.md`, just needs restructuring to match
the new page split); `docker-compose.md` and `overview.md` get fresh
translations following the same terminology.

### Cross-links from existing pages

- `docs/site/docs/intro.mdx` "Deployment options" → Self-hosted tab gets a
  link to `deployment/overview.md`.
- `docs/site/docs/getting-started/quick-start.md` → the "Self-hosted" tab's
  step 1 gets a leading sentence: if the instance isn't installed yet, see
  the Deployment guide first, with a link.

### `deploy/` becomes pointers, not a second copy

- `deploy/docker-compose/INSTALL.md`, `deploy/kubernetes/INSTALL.md`,
  `deploy/kubernetes/INSTALL.zh.md`, `deploy/docker-compose/OPENSANDBOX.md`
  are replaced with a short stub: one paragraph restating what the target is,
  and a link to the corresponding docs-site page as the canonical guide.
  These files are **not deleted** — they're still what a repo-browsing
  engineer finds via the existing `deploy/README.md` table and the two
  `deploy/*/README.md` landing pages, so the pointer needs to be exact and
  the target needs to be `cubeplex.ai/docs/deployment/...` (public, versioned
  independently of any given branch).
- `deploy/README.md`'s target table, `deploy/docker-compose/README.md`, and
  `deploy/kubernetes/README.md` keep their short quickstart command blocks
  (low drift risk — they're already terse, and match the scripts directly)
  but their "full guide" links point at the docs site instead of the local
  `INSTALL.md`.
- Root `README.md`'s two "installation guide" links point at the docs site
  pages instead of `deploy/docker-compose/INSTALL.md` /
  `deploy/kubernetes/INSTALL.md`.

## Out of scope

- `deploy/docling-serve/` — no prose doc exists today; not touched.
- Real screenshots (moot here — no UI content in this guide).
- Restructuring `deploy/kubernetes/charts/cubeplex/values.local.yaml.example`
  or any chart/script content — docs only.
- Translating any *other* docs-site page beyond the three new ones.

## Success criteria

- `cubeplex.ai/docs/deployment/{overview,docker-compose,kubernetes}` render
  in both `en` and `zh-Hans`, reachable from the sidebar.
- Every command, config field, and troubleshooting entry currently in
  `deploy/docker-compose/INSTALL.md`, `deploy/docker-compose/OPENSANDBOX.md`,
  and `deploy/kubernetes/INSTALL.md`/`INSTALL.zh.md` has a home in the new
  pages — nothing operationally load-bearing is dropped in the move.
  (Verified by no gaps found in the plan's self-review; execution-time by a
  side-by-side pass in the plan's diffing step, spelled out in the plan.)
- `deploy/*/INSTALL.md`, `OPENSANDBOX.md`, `deploy/README.md`, and root
  `README.md` no longer contain the full prose — only pointers/quickstarts —
  and no link in the repo points at content that no longer exists there.
