# Docs CI and Cloudflare Pages Deployment

## Goal

Build the Docusaurus documentation site in GitHub Actions for pull requests and
main-branch pushes, then publish the verified main-branch build to the
Cloudflare Pages project `cubeplex-docs`.

## Context

The repository already contains a static Docusaurus site at `docs/site/`, and
its public URL is configured as `https://docs.cubeplex.ai`. The site has local
build and type-check commands, but no repository workflow currently builds or
publishes it. The `cubepi` repository demonstrates the required split between a
build/check job and a main-only Cloudflare Pages deployment job. Cloudflare's
current Direct Upload CI documentation uses `cloudflare/wrangler-action@v3`
for deployment, so this workflow uses that action rather than the older
Pages-specific action used by the reference repository.

## Approaches considered

1. Add docs steps to the existing application CI workflow. This would couple a
   static-site build to the custom `/ci` check-run workflow and make deployment
   logic harder to see and maintain.
2. Create a dedicated docs workflow with a build job and a deploy job. This
   matches the existing Docusaurus/Cloudflare pattern, lets docs-only changes
   run without backend services, and makes the deployment gate explicit.
3. Deploy directly from Cloudflare's Git integration. This would remove the
   repository's build/test visibility and duplicate the build configuration in
   Cloudflare.

The dedicated workflow is the chosen approach.

## Design

### Workflow triggers and build

`.github/workflows/docs.yml` runs on pull requests, on pushes to `main` when
documentation-site inputs or the workflow itself change, and manually for a
preview deployment. The build job will:

- check out the target revision;
- install Node.js 22 and pnpm 11.10.0;
- install `docs/site` dependencies from its frozen lockfile;
- run the site's `check` command, which builds Docusaurus and type-checks the
  generated/configuration TypeScript;
- on `main`, upload `docs/site/build/` as a workflow artifact.

The build job is the required status for pull requests. It does not start
backend services because the docs site is a static, self-contained build.

### Deployment

The deploy job runs for a push to `main` after the build job succeeds, or for a
manual workflow run. Manual runs deploy to the requested Pages preview branch;
main pushes deploy to the production branch. It downloads the exact artifact
produced by that build and uses
`cloudflare/wrangler-action@v3` with:

- `projectName: cubeplex-docs`;
- manual preview: `pages deploy docs/site/build --project-name=cubeplex-docs --branch=<target>`;
- main production: `pages deploy docs/site/build --project-name=cubeplex-docs`;
- `CF_API_TOKEN` and `CF_ACCOUNT_ID` repository secrets;
- the GitHub token for deployment metadata.

The workflow requests only `contents: read` and `deployments: write`.

### Setup documentation

`.github/workflows/SECRETS.md` documents the two required secrets and the
one-time Cloudflare Pages project setup. `docs/site/README.md` uses pnpm and
documents the local check plus the fact that main pushes deploy automatically.

## Out of scope

- Creating the Cloudflare account, Pages project, DNS record, or custom domain.
- Adding automatic preview deployments for pull requests.
- Changing Docusaurus content, theme, or site configuration.
- Replacing the existing application CI workflow.

## Success criteria

- A docs-only pull request runs a build/type-check workflow without
  backend services.
- A successful main push produces a non-empty `docs/site/build/` artifact.
- A successful main push invokes Cloudflare Pages deployment using the
  `cubeplex-docs` project and the artifact from that same commit.
- The setup and local commands are documented, and the workflow does not expose
  secret values.
