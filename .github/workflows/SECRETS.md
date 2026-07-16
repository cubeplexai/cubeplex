# Documentation deployment secrets

The docs workflow builds `docs/site/` and deploys the verified `main` build to
Cloudflare Pages using Direct Upload.

Before the first deployment, configure these repository secrets under
`Settings → Secrets and variables → Actions`:

| Secret | Purpose |
| --- | --- |
| `CF_API_TOKEN` | Cloudflare API token with Account → Cloudflare Pages → Edit permission |
| `CF_ACCOUNT_ID` | Cloudflare account that owns the Pages project |

Create a Cloudflare Pages project named `cubeplex-docs` in Direct Upload mode before
the first deployment. The workflow publishes `docs/site/build/` to that
project. Attach `docs.cubeplex.ai` as the custom domain in the Pages project
settings after the first successful deployment.

To test a branch without changing production, run the workflow manually from
that branch and keep the default Pages preview branch. The deployment will be
available under the corresponding `*.cubeplex.pages.dev` preview URL.
