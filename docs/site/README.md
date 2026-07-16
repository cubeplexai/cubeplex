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

This runs the production Docusaurus build and the TypeScript check. The static
output is written to `build/`.

To serve an already-built site locally:

```bash
pnpm serve
```

## Deployment

The `Docs` GitHub Actions workflow runs `pnpm check` for docs pull requests.
When a change lands on `main`, it uploads the verified `build/` directory to
the `cubeplex` Cloudflare Pages project. The required repository secrets and
one-time Pages setup are documented in
`../../.github/workflows/SECRETS.md`.
