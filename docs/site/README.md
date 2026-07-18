# CubePlex documentation site

This site is built with [Docusaurus](https://docusaurus.io/) and published under
the marketing domain at **<https://cubeplex.ai/docs>** (English) and
**<https://cubeplex.ai/docs/zh-Hans>** (简体中文).

Production routing:

- **Cloudflare Pages** project `cubeplex-docs` hosts the static build (origin
  `*.pages.dev`).
- **docs-proxy Worker** (`cubeplex.ai/docs*`) strips the `/docs` prefix and
  forwards to that origin so docs share the primary domain.
- The legacy host `docs.cubeplex.ai` **301s** to `cubeplex.ai/docs/*` via the
  `cubeplex-docs-subdomain-redirect` Worker (see the `website` repo
  `docs-redirect/`).

Config notes that affect every local URL:

| Setting | Value | Effect |
|---------|--------|--------|
| `url` | `https://cubeplex.ai` | Canonical / OG host |
| `baseUrl` | `/docs/` | All pages live under `/docs/...` |
| `trailingSlash` | `false` | Prefer slashless paths (e.g. `/docs/getting-started/quick-start`) |
| `i18n.locales` | `en`, `zh-Hans` | Two standalone locale builds |

## Installation

```bash
cd docs/site
pnpm install --frozen-lockfile
```

## Local preview

### Why dev mode is single-locale

`docusaurus start` compiles **one locale at a time** (fast HMR). Each locale is
its own SPA with its own route table:

| Command | Routes available | Example |
|---------|------------------|---------|
| `pnpm start` (default `en`) | `/docs/...` only | http://127.0.0.1:4301/docs/ |
| `pnpm start -- --locale zh-Hans` | `/docs/zh-Hans/...` only | http://127.0.0.1:4302/docs/zh-Hans/ |

Opening `/docs/zh-Hans/` on an English-only dev server shows **Page Not Found**
— that is expected, not a content bug.

If a previous `pnpm build` left `.docusaurus` in a bad state (e.g. stuck on
zh-Hans while you expected English), clear and restart:

```bash
pnpm exec docusaurus clear
pnpm start -- --port 4301 --host 0.0.0.0
```

### Option A — hot reload (edit content)

English (default):

```bash
pnpm start -- --port 4301 --host 0.0.0.0
# → http://127.0.0.1:4301/docs/
# → http://192.168.x.x:4301/docs/   (LAN when --host 0.0.0.0)
```

Chinese (separate process / port):

```bash
pnpm start -- --locale zh-Hans --port 4302 --host 0.0.0.0
# → http://127.0.0.1:4302/docs/zh-Hans/
```

`--host 0.0.0.0` is required to open the site from another machine on the LAN.

### Option B — production build preview (both locales, recommended)

Builds **en + zh-Hans** into `build/`, then serves the static tree on one port
(closest to production; no HMR — rebuild after edits):

```bash
pnpm build
pnpm exec docusaurus serve --dir build --port 4301 --host 0.0.0.0 --no-open
```

| Locale | URL |
|--------|-----|
| English home | http://127.0.0.1:4301/docs/ |
| English page | http://127.0.0.1:4301/docs/getting-started/quick-start |
| 简体中文 home | http://127.0.0.1:4301/docs/zh-Hans/ |
| 简体中文 page | http://127.0.0.1:4301/docs/zh-Hans/getting-started/quick-start |

`pnpm serve` alone also works after a build (default port 3000); prefer the
explicit `docusaurus serve` line above when you need a fixed port / LAN bind.

### Mode comparison

| Mode | Command | Both locales | Hot reload | Use when |
|------|---------|--------------|------------|----------|
| Dev | `pnpm start` [`--locale …`] | No (one process) | Yes | Writing / editing docs |
| Build + serve | `pnpm build` then `docusaurus serve` | **Yes** | No | Checking i18n, links, SEO, LAN demo |

Dev mode returns a thin SPA shell over HTTP; full HTML (titles, body text) only
appears in the browser. Build + serve returns real static HTML that `curl` can
verify.

## Check and build

```bash
pnpm check
```

Runs the production Docusaurus build (all locales), TypeScript, generated URL
audit, and the origin `_worker.js` normalization unit test. Output lands in
`build/` (English) and `build/zh-Hans/` (Chinese).

URL rules used in production (and enforced by `check:urls` / the origin worker):

- Non-root pages are **slashless** (`…/quick-start`, not `…/quick-start/`).
- Locale / docs homes may use a trailing slash in the router
  (`/docs/`, `/docs/zh-Hans/`); the public edge and proxy keep canonical hosts
  on `cubeplex.ai`.

## Deployment

The `Docs` GitHub Actions workflow runs `pnpm check` on docs PRs. On push to
`main` it deploys `docs/site/build/` to the **`cubeplex-docs`** Cloudflare Pages
project. Secrets and one-time Pages setup:
`../../.github/workflows/SECRETS.md`.

The marketing site and Workers that expose `/docs` on `cubeplex.ai` live in the
separate **`website`** repository (`docs-proxy/`, `docs-redirect/`).
