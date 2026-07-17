# CubePlex documentation site

This site is built with [Docusaurus](https://docusaurus.io/) and published at
<https://docs.cubeplex.ai>.

## Installation

```bash
pnpm install --frozen-lockfile
```

## Local development

```bash
pnpm start
```

The local server watches the site files and reloads after changes.

## Check and build

```bash
pnpm check
```

This runs the production Docusaurus build, the TypeScript check, the generated
URL audit, and the Cloudflare Worker normalization test. The static output is
written to `build/`.

The site uses slashless URLs for every non-root page. Cloudflare Pages
redirects legacy URLs such as `/docs/getting-started/quick-start/` to
`/docs/getting-started/quick-start`; the root URL `/` remains unchanged.

To serve an already-built site locally:

```bash
pnpm serve
```

## Deployment

The `Docs` GitHub Actions workflow runs `pnpm check` for docs pull requests.
When a change lands on `main`, it uploads the verified `build/` directory to
the `cubeplex-docs` Cloudflare Pages project. The required repository secrets and
one-time Pages setup are documented in
`../../.github/workflows/SECRETS.md`.
