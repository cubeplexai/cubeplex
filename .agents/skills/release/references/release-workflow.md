# Cubeplex release reference

## Version sources

Application releases use one semver across these committed fields:

- `backend/pyproject.toml` → `[project].version`;
- `frontend/package.json` → `version`;
- `frontend/packages/core/package.json` → `version`;
- `frontend/packages/web/package.json` → `version`;
- `deploy/kubernetes/charts/cubeplex/Chart.yaml` → `version` and `appVersion`.

`scripts/check-version-consistency.sh v0.3.0` verifies the six fields above.
Two more code-level refs are NOT checked by the script and must be bumped by
hand (the build or the pre-push gate fails if they are missed):

- `backend/cubeplex/api/app.py` -> the FastAPI app `version="..."`;
- `backend/cubeplex/api/routes/v1/system.py` -> `_CUBEPLEX_VERSION` (commented
  "bump on release; kept in sync with backend/pyproject.toml").

`backend/uv.lock` also records the cubeplex package version; regenerate it with
`uv lock` (or any `uv run`) and commit the result. If you forget, the pre-push
`backend-check-ci` hook rewrites `uv.lock` mid-run, pre-commit flags "files
were modified by this hook", and the push is rejected.

The sandbox has an independent version in:

```text
deploy/images/sandbox/VERSION
```

If the sandbox Dockerfile, fonts, browser/runtime dependencies, or other image
inputs change, increment this value. A sandbox build publishes
`sandbox-v<version>` and never overwrites an existing version tag. At release,
`release.yml` promotes that image to `cubeplex-sandbox:v<semver>` (a tag alias,
no rebuild) so every service image shares the one application version; an
ordinary application release reuses the existing sandbox build.

## Release preparation PR

From a feature branch:

1. Bump the application version to the target (e.g. `0.3.0`) in every version
   source listed above - the six checked fields PLUS `app.py`, `system.py`, and
   the regenerated `uv.lock`. Match the prior release commit's file set.
2. Bump `deploy/images/sandbox/VERSION` only when sandbox inputs changed. If you
   bump it, merging this PR to `main` triggers `sandbox-image.yml` to build the
   new `sandbox-v<version>`; that build must appear before `release.yml` can
   promote it (release.yml polls, but only after the build is triggered).
3. Run `scripts/check-version-consistency.sh v0.3.0` (checks the six fields).
4. Push. The pre-push hook runs the full CI-equivalent `backend-check-ci` +
   `frontend-check-ci` (~3 min). GitHub's SSH closes the idle connection while
   the hook runs, so push with keepalive:
   `GIT_SSH_COMMAND="ssh -o ServerAliveInterval=30 -o ServerAliveCountMax=8" git push origin <branch>`.
5. Merge the PR into `main`. The `main` branch ruleset requires
   last-push-approval, so a release PR you pushed yourself cannot self-approve;
   merge with `gh pr merge <n> --squash --admin` (you are the repo admin).

Do not change Helm default image tags (`values.yaml` `tag: ""`, which already
default to `v<appVersion>`) or `compose.yaml` in this PR - those deployment
selections belong to the release manifest and operator environment. Do update
`deploy/docker-compose/.env.example`'s `BACKEND_TAG`/`FRONTEND_TAG` to the new
release tag: it is a tracked template operators copy, and `compose.yaml`
requires `BACKEND_TAG` (no default), so a stale example deploys old images.

## Image publication order

Application images are built on a release tag push or a manual
`workflow_dispatch`. They are pushed only on a tag push, or on a
`workflow_dispatch` run with its `publish` input set to `true` (default
`false`). There are no per-PR or per-merge image builds.

When a `v<semver>` tag is pushed, `images.yml` publishes:

```text
ghcr.io/cubeplexai/cubeplex-backend:v<semver>
ghcr.io/cubeplexai/cubeplex-frontend:v<semver>
ghcr.io/cubeplexai/cubeplex-egress-webhook:v<semver>
```

The sandbox workflow publishes:

```text
ghcr.io/cubeplexai/cubeplex-sandbox:<YYMMDD>-main-<short-sha>
ghcr.io/cubeplexai/cubeplex-sandbox:sandbox-v<version>
```

The sandbox workflow rejects an already existing `sandbox-v<version>` tag.

`release.yml` also packages and publishes the Helm chart as an OCI artifact:

```text
oci://ghcr.io/cubeplexai/charts/cubeplex:<semver>
```

The chart version equals the release semver (enforced by
`check-version-consistency.sh`), and the chart's default image tag is
`v<appVersion>`, so a published chart points at the matching application images
with no operator overrides.

## Create the application release

After the version-bump commit is merged:

```bash
git fetch origin main --tags
git checkout main
git pull --ff-only origin main
git tag -a v0.3.0 -m "Release v0.3.0" HEAD
git push origin v0.3.0
```

Pushing the tag triggers `images.yml` (builds and pushes version-tagged images)
and `release.yml` (waits for those images, then creates the manifest and GitHub
Release) concurrently. The image build takes up to ~30 minutes; the release
workflow polls until the images appear or times out.

## Release workflow behavior

For `v0.3.0`, the two triggered workflows do:

**`images.yml`** (triggered by the tag push):
1. builds backend, frontend, and egress-webhook images for `linux/amd64` and `linux/arm64`;
2. pushes them to GHCR with the `v0.3.0` tag.

**`release.yml`** (triggered by the same tag push, runs concurrently):
1. checks that all package/chart versions equal `0.3.0`;
2. reads the sandbox version from `deploy/images/sandbox/VERSION`;
3. polls for `ghcr.io/.../cubeplex-{backend,frontend,egress-webhook}:v0.3.0` (up to ~30 min);
4. records their digests in the manifest;
5. waits for the `sandbox-v<version>` image, then promotes it to `cubeplex-sandbox:v0.3.0` (tag alias, no rebuild);
6. packages and pushes the Helm chart to `oci://ghcr.io/.../charts/cubeplex:0.3.0`;
7. creates `release-manifest-v0.3.0.yaml` and uploads it to the GitHub Release.

The application release tags are aliases for the already built image manifests,
not new builds — the sandbox is likewise promoted, not rebuilt. The manifest
records the backend/frontend/egress-webhook/sandbox image digests.

Published application and sandbox tags contain `linux/amd64` and `linux/arm64`
manifests. Any `unknown/unknown` entry shown by GHCR is a provenance attestation,
not a runtime platform.

## Deploy the release

Use the release manifest as the deployment input. For docker-compose,
`compose.yaml` requires these in `.env` (copy from `.env.example`):

```dotenv
BACKEND_TAG=v0.3.0
FRONTEND_TAG=v0.3.0
```

For Helm, the chart already defaults every image tag to `v<appVersion>`, so a
standard release needs no image overrides. Pin only to use a different version:

```yaml
image:
  backend: {tag: v0.3.0}   # optional; defaults to v<appVersion>
  frontend: {tag: v0.3.0}  # optional; defaults to v<appVersion>
```

For a private registry, mirror the same digests and override the registry and
repository settings. Do not rebuild the images under a new content hash.

Sandbox E2E/nightly tests use their own runtime configuration and credentials.
They are not run by image publication because the deployment machine may have
slow access to GHCR.

## Rollback

Select an older release manifest or older immutable release tags. Never move an
existing release tag to different content and never use `latest` as a rollback
selector.
