# Releasing CubePlex

How to cut a production or self-hosted CubePlex release. This is a developer
process doc — it does not belong on the user-facing docs site.

Install guides for operators live under `docs/site/docs/deployment/` (English)
and `docs/site/i18n/zh-Hans/.../deployment/` (Chinese). Those pages must stay
in sync with the version this process publishes.

Agents: load the `release` skill, then follow this file.

## Sequence

1. Inspect the current branch, worktree, `origin/main`, and existing `v*` tags.
   Never overwrite an existing application or sandbox version tag.
2. From a feature branch off latest `origin/main`, prepare a **version-bump PR**.
3. Run `scripts/check-version-consistency.sh v<semver>` and push. Pre-push runs
   the CI-equivalent checks for any code sides in the push.
4. Merge the PR into `main`.
5. Create annotated tag `v<semver>` on that exact merged commit and push it.
6. The tag push triggers `images.yml` (build/push application images) and
   `release.yml` (wait for images, promote sandbox, publish Helm chart, write
   the GitHub Release + manifest) concurrently. If `deploy/images/sandbox/VERSION`
   changed in the bump PR, merging to `main` also triggers `sandbox-image.yml`;
   `release.yml` cannot promote the sandbox until that image exists.
7. Deploy from the release manifest's tags or digests. Do not edit chart
   defaults or use `latest` for production.

## Version sources

One application semver (example: `0.6.0`) across these committed fields.

**Checked by `scripts/check-version-consistency.sh v0.6.0`:**

- `backend/pyproject.toml` → `[project].version`
- `frontend/package.json` → `version`
- `frontend/packages/core/package.json` → `version`
- `frontend/packages/web/package.json` → `version`
- `deploy/kubernetes/charts/cubeplex/Chart.yaml` → `version` and `appVersion`
- `deploy/images/sandbox/VERSION` → the semver half of `<semver>-<YYMMDD>`

**Not checked by the script — bump by hand or the build / pre-push gate fails:**

- `backend/cubeplex/api/app.py` → FastAPI `version="..."`
- `backend/cubeplex/api/routes/v1/system.py` → `_CUBEPLEX_VERSION`
- `backend/uv.lock` → cubeplex package version. Regenerate with `uv lock` from
  `backend/` (or any `uv run`). If you skip this, pre-push `backend-check-ci`
  rewrites `uv.lock` mid-run and the push is rejected because the hook modified
  files.

**Operator template (copy-paste, so a stale value deploys the old images):**

- `deploy/docker-compose/.env.example` → `BACKEND_TAG` / `FRONTEND_TAG`

**User-facing deploy docs (English and Chinese).** Operators copy these
snippets; a stale example installs the previous release even though the page
says "pick a tag from the releases page":

| File | What to bump |
|---|---|
| `docs/site/docs/deployment/docker-compose.md` | `e.g. v<semver>`, `BACKEND_TAG` / `FRONTEND_TAG`, `cubeplex-sandbox:v<semver>` |
| `docs/site/docs/deployment/kubernetes.md` | `image.*.tag`, egress-webhook `tag`, `helm --version <semver>`, values-tree examples, minimal `values.local.yaml` |
| `docs/site/docs/deployment/backend-config.md` | `cubeplex-sandbox:v<semver>` |
| `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/deployment/docker-compose.md` | same as English |
| `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/deployment/kubernetes.md` | same as English |
| `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/deployment/backend-config.md` | same as English |

Match the prior release commit's file set, **plus** the six deploy-doc files
above. v0.6.0 bumped the package/chart fields but left the docs on `v0.2.0`;
do not repeat that.

### Do not bump

Leave these alone in the same files — they are not CubePlex's application
version:

- OpenSandbox controller notes about Docker Hub `v0.2.0` crashing (`flag
  provided but not defined`) and pinning `latest` until upstream re-cuts.
- `opensandbox-server v0.1.14`, `image-committer:v0.1.0`, egress
  `opensandbox/egress:v1.0.12`, postgres/rustfs/docling image tags.
- Helm `values.yaml` default `tag: ""` (already falls back to `v<appVersion>`).
- `compose.yaml` image selections. Those belong to the operator `.env` and the
  release manifest.

Do not put this process, or any other developer release procedure, under
`docs/site/`.

## Sandbox `VERSION`

Format: `<app semver>-<YYMMDD>`, e.g. `0.6.0-260825`.

- The semver half **must** equal the application version (the consistency
  script checks this). A stale prefix fails the release.
- The date half is the image's own rev. Bump it when the sandbox Dockerfile,
  fonts, browser/runtime dependencies, or other image inputs changed since the
  last `VERSION` bump — including changes that landed on `main` after the
  previous date stamp without a `VERSION` update (v0.6.0 had to move
  `260821` → `260825` because the Node 24 Dockerfile change never got its own
  stamp).
- Any change to the `VERSION` string, even semver-half only, triggers
  `sandbox-image.yml` on merge to `main`. That workflow publishes
  `sandbox-v<version>` and **refuses to overwrite** an existing tag. The new
  tag must exist before `release.yml` can promote it to
  `cubeplex-sandbox:v<semver>` (a tag alias, no rebuild).

An ordinary application release with unchanged sandbox inputs still rewrites
the semver half, so it still triggers a new sandbox image under the new
`sandbox-v<semver>-<old date>` tag.

## Version-bump PR

From a worktree (`./scripts/new-worktree feat/YYYY-MM-DD-release-<semver>`):

1. Bump every version source above to the target (e.g. `0.6.0`).
2. Run `scripts/check-version-consistency.sh v0.6.0`.
3. Grep the six deploy-doc files for the **previous** `v<semver>` / chart
   `--version` and replace only the CubePlex application examples.
4. Push. GitHub's SSH closes the idle connection while pre-push runs (~3 min
   if both backend and frontend changed; docs-only skips both). Use keepalive:

   ```bash
   GIT_SSH_COMMAND="ssh -o ServerAliveInterval=30 -o ServerAliveCountMax=8" \
     git push origin feat/YYYY-MM-DD-release-<semver>
   ```

5. Open a PR titled `Bump version to <semver>` (no prefixes).
6. Merge with `gh pr merge <n> --squash --admin`. The `main` ruleset requires
   last-push-approval, so a PR you pushed yourself cannot self-approve.

## Tag and publish

On the exact squash-merged commit:

```bash
git fetch origin main --tags
git tag -a v0.6.0 -m "Release v0.6.0" origin/main
git push origin v0.6.0
```

Confirm `origin/main` is the merge commit before tagging. Pushing the tag
triggers:

**`images.yml`** — builds `linux/amd64` + `linux/arm64` and pushes:

```text
ghcr.io/cubeplexai/cubeplex-backend:v0.6.0
ghcr.io/cubeplexai/cubeplex-frontend:v0.6.0
ghcr.io/cubeplexai/cubeplex-egress-webhook:v0.6.0
```

There are no per-PR or per-merge application image builds. Images are pushed
only on a tag push, or on `workflow_dispatch` with `publish: true`.

**`release.yml`** (same tag push, runs concurrently):

1. Checks package/chart versions equal `0.6.0`.
2. Reads `deploy/images/sandbox/VERSION`.
3. Polls for the three application images (up to ~30 min) and records digests.
4. Waits for `sandbox-v<version>`, then promotes it to
   `cubeplex-sandbox:v0.6.0`.
5. Packages the Helm chart to `oci://ghcr.io/cubeplexai/charts/cubeplex:0.6.0`.
6. Uploads `release-manifest-v0.6.0.yaml` to the GitHub Release.

GHCR `unknown/unknown` entries are provenance attestations, not a runtime
platform. Application and sandbox tags contain `linux/amd64` and
`linux/arm64`.

If an image build fails, fix the build and re-run the workflow. Do not
manually push a replacement under the same tag.

Sandbox E2E / nightly tests stay on their own workflows. Do not run them from
image publication.

## Deploy

Use the manifest as the input
(`https://github.com/cubeplexai/cubeplex/releases/download/v0.6.0/release-manifest-v0.6.0.yaml`).
Keep registry credentials and runtime secrets out of manifests.

Docker Compose — copy `.env.example` and set:

```dotenv
BACKEND_TAG=v0.6.0
FRONTEND_TAG=v0.6.0
```

Helm — the published chart already defaults every image tag to `v<appVersion>`.
A standard install needs no image overrides:

```bash
helm upgrade --install cubeplex oci://ghcr.io/cubeplexai/charts/cubeplex \
  --version 0.6.0 \
  --namespace cubeplex --create-namespace \
  --values values.local.yaml \
  --wait --timeout 10m
```

Rollback: select an older release manifest or older immutable tags. Never move
an existing release tag to different content, and never use `latest` as a
rollback selector.
