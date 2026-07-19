# Cubeplex release reference

## Version sources

Application releases use one semver across these committed fields:

- `backend/pyproject.toml` → `[project].version`;
- `frontend/package.json` → `version`;
- `frontend/packages/core/package.json` → `version`;
- `frontend/packages/web/package.json` → `version`;
- `deploy/kubernetes/charts/cubeplex/Chart.yaml` → `version` and `appVersion`.

Check them with:

```bash
scripts/check-version-consistency.sh v0.3.0
```

The sandbox has an independent version in:

```text
deploy/images/sandbox/VERSION
```

If the sandbox Dockerfile, fonts, browser/runtime dependencies, or other image
inputs change, increment this value. A sandbox release publishes
`sandbox-v<version>` and never overwrites an existing version tag. An ordinary
application release keeps the existing sandbox version.

## Release preparation PR

From a feature branch:

1. Bump the application versions to the target version, for example `0.3.0`.
2. Bump `deploy/images/sandbox/VERSION` only when sandbox inputs changed.
3. Run `scripts/check-version-consistency.sh v0.3.0`.
4. Run the changed-module checks and the repository pre-push gate.
5. Merge the PR into `main`.

Do not change `BACKEND_TAG`, `FRONTEND_TAG`, Helm default image tags, or Compose
defaults in this preparation PR. Those deployment selections belong to the
release manifest and operator environment.

## Image publication order

The `main` image workflow builds affected application images and publishes:

```text
ghcr.io/cubeplexai/cubeplex-backend:sha-<main-commit>
ghcr.io/cubeplexai/cubeplex-frontend:sha-<main-commit>
```

The sandbox workflow publishes:

```text
ghcr.io/cubeplexai/cubeplex-sandbox:sha-<main-commit>
ghcr.io/cubeplexai/cubeplex-sandbox:sandbox-v<version>
```

The sandbox workflow rejects an already existing `sandbox-v<version>` tag.
PR workflows build without pushing production tags.

## Create the application release

After the version-bump commit is merged and the main image workflow succeeds:

```bash
git fetch origin main --tags
git checkout main
git pull --ff-only origin main
git tag -a v0.3.0 -m "Release v0.3.0" HEAD
git push origin v0.3.0
```

The tag must point to the same commit whose `sha-<commit>` images were built.
The release workflow accepts a tag immediately after merge and waits for the
commit images for a bounded period. If they never appear, it fails instead of
rebuilding.

## Release workflow behavior

For `v0.3.0`, the workflow:

1. checks that all package/chart versions equal `0.3.0`;
2. reads the sandbox version from `deploy/images/sandbox/VERSION`;
3. waits for backend/frontend `sha-<commit>` images;
4. adds `v0.3.0` to the same backend/frontend image manifests;
5. waits for the corresponding `sandbox-v<version>` image;
6. creates `release-manifest-v0.3.0.yaml` and uploads it to the GitHub Release.

The application release tags are aliases for the already built image manifests,
not new builds. The manifest records backend/frontend image digests and the
selected sandbox version tag.

## Deploy the release

Use the release manifest as the deployment input. For tag-based deployment:

```dotenv
BACKEND_TAG=v0.3.0
FRONTEND_TAG=v0.3.0
```

or Helm values:

```yaml
image:
  backend: {tag: v0.3.0}
  frontend: {tag: v0.3.0}
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
