# Docs CI and Cloudflare Pages Deployment

**Goal:** Verify the Docusaurus site in GitHub Actions and publish main-branch
builds to the existing Cloudflare Pages project `cubeplex`.

**Architecture:** A dedicated workflow builds the static site once, uploads the
`docs/site/build/` directory on main, and a dependent deployment job downloads
that exact artifact before calling Cloudflare Pages. Pull requests stop after
the build/check job, so they get validation without mutating the hosted site.

**Tech stack:** GitHub Actions, pnpm 11.10.0, Node.js 22, Docusaurus 3, Cloudflare
Pages Direct Upload.

## Unit 1 — Add docs build and deployment workflow

**Files:** `.github/workflows/docs.yml`

**Interfaces:**

- `build-and-check` produces an artifact named `docs-site-build` containing
  `docs/site/build/` on main.
- `deploy-cloudflare` consumes `docs-site-build` and deploys it to Pages
  project `cubeplex` for `push` events targeting `main` or a manually selected
  preview branch.
- Required secrets are `CF_API_TOKEN` and `CF_ACCOUNT_ID`.

**Core logic:** Install with the docs lockfile, run `pnpm check`, then upload
the build only on main. The deploy job is gated on the build job and downloads
the artifact rather than rebuilding, so deployment cannot differ from the
tested output. Deploy with the current Cloudflare `wrangler-action@v3` Direct
Upload flow. Main pushes target the production branch; manual runs target the
selected preview branch.

**Tests:** YAML inspection plus a local Docusaurus build and type-check; the
workflow's artifact path must be non-empty after the build.

## Unit 2 — Document deployment setup and local commands

**Files:** `.github/workflows/SECRETS.md`, `docs/site/README.md`

**Interfaces:** The setup document names the secrets and Pages project; the
site README provides pnpm-based install, development, check, and serve
commands.

**Core logic:** Keep account/project provisioning as a one-time operator step
and keep secret values out of the repository. Make automatic deployment from
`main` explicit so contributors know how a docs change reaches production.

**Tests:** Markdown/link/path review against the workflow and package scripts.

## Unit 3 — Verify the change

**Files:** None.

**Checks:**

- `pnpm install --frozen-lockfile` in `docs/site`.
- `pnpm check` in `docs/site`.
- `git diff --check` and workflow structure review.
