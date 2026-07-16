# Docs URLs without trailing slashes

**Goal:** Generate slashless docs URLs, redirect legacy slash-suffixed
requests, and continuously verify sitemap and internal HTML URLs.

**Architecture:** Docusaurus owns canonical route generation through
`trailingSlash: false`. A Pages advanced-mode Worker at the static asset
boundary handles requests made with a legacy trailing slash, then delegates
canonical requests to `env.ASSETS`. A post-build Node audit checks the generated
HTML and sitemap files so CI protects the same invariant at build time.

**Tech stack:** Docusaurus 3, TypeScript, Cloudflare Pages advanced-mode
Worker, Node.js built-ins, GitHub Actions.

## Unit 1 — Freeze the URL policy in Docusaurus

**Files:**

- `docs/site/docusaurus.config.ts` — set the explicit slashless URL option.
- `docs/dev/specs/2026-07-16-docs-no-trailing-slash-design.md` — record the
  behavior and boundaries.

**Interface:** The generated site treats `/` as the only URL allowed to end in
`/`; every other generated route is slashless.

**Core logic:** Docusaurus generates `.html` files for document routes and
rewrites its own canonical links and sitemap entries to match the setting.

**Tests:** The production docs build must complete with broken-link checking
enabled, and the output audit in Unit 3 must find no non-root slash-suffixed
internal URLs.

## Unit 2 — Normalize legacy requests at Pages

**Files:**

- `docs/site/static/_worker.js` — add the advanced-mode Worker.

**Interface:** `fetch(request, env)` returns a 301 for a non-root pathname
ending in `/`, with the final slash removed and the query string preserved;
all other requests are passed to `env.ASSETS.fetch(request)`.

**Core logic:** Clone the request URL, inspect only the pathname, and return a
redirect before calling the asset binding. Do not store request state globally
or consume the asset response body.

**Tests:** Execute the Worker handler in a small Node harness with a mocked
asset binding for root, slash-suffixed, query-string, canonical, and asset
requests. Assert status, `Location`, and delegation behavior.

## Unit 3 — Audit generated sitemap and internal URLs

**Files:**

- `docs/site/scripts/check-url-format.mjs` — inspect generated HTML and sitemap
  output using Node built-ins.
- `docs/site/package.json` — run the audit as part of `pnpm check`.

**Interface:** `node scripts/check-url-format.mjs [build-directory]` exits 0
when all internal generated URLs are canonical and exits nonzero with the file
and offending URL when it finds a violation.

**Core logic:** Walk generated `.html` and `.xml` files, extract URL-bearing
HTML attributes/metadata and sitemap `<loc>` values, resolve same-origin
absolute URLs, and reject only non-root paths ending in `/`. Ignore fragments,
data URLs, and external origins.

**Tests:** Run the audit against the real Docusaurus build and include a small
fixture-oriented test or deterministic harness for root, canonical,
slash-suffixed, external, and fragment URLs.

## Unit 4 — Verify the CI/deployment contract

**Files:**

- `.github/workflows/docs.yml` — keep the existing build and Pages deployment
  path; no new secret or deployment mechanism is needed.

**Interface:** Pull requests run the enhanced `pnpm check`; main deployments
upload the build containing `_worker.js` to the `cubeplex-docs` Pages project.

**Core logic:** The audit runs after the build, so deployment cannot publish a
  site whose generated sitemap or internal HTML links violate the URL policy.

**Tests:** Run `pnpm install --frozen-lockfile`, `pnpm check`, inspect the build
  tree for `_worker.js`, and verify the generated sitemap and canonical links
  with the audit output.
