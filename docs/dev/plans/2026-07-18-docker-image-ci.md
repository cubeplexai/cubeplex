# Docker Image CI/CD Implementation Plan

**Goal:** Build affected cubeplex images by change, publish immutable commit images,
and use a release manifest to pin the application and sandbox image combination.

**Architecture:** GitHub Actions handles PR build validation, `main` SHA-image
publication, and Git-tag release promotion. Image digests are the release outputs;
the release values/manifest is the deployment input. Helm and Compose consume that
input instead of inferring or overriding production versions. Sandbox has an independent
workflow and version. Existing real sandbox E2E remains a separate workflow and is not
part of image publication or release gating.

**Tech stack:** GitHub Actions, Docker Buildx, GHCR, Helm, Docker Compose, shell
scripts, and the existing backend/frontend/sandbox Dockerfiles.

## Unit 1 — Define image metadata and changed-area contract

**Files**

- `.github/workflows/images.yml` — declare PR, main, release, and manual sandbox job
  boundaries, or split them into separate workflows if that matches repository style.
- `.github/scripts/` changed-area/metadata helper, if needed — calculate source commit,
  image tags, and targets without owning publication permissions.

**Interfaces**

- Inputs: GitHub event, source commit, Git tag, and changed-file list.
- Outputs: `targets` (backend/frontend/sandbox/egress-webhook), formatted image tag,
  release
  version, and build metadata for later jobs.

**Core logic**

- Extend the existing CI change detection to include Dockerfiles, deploy scripts,
  workflows, and shared build configuration.
- PR events cannot enter a formal publication job. Main publishes only
  `<YYMMDD>-<branch>-<short-sha>`. A release consumes an existing main build and
  recomputes the tag from the release commit.
- Sandbox paths are detected independently so ordinary application changes do not
  trigger the large sandbox build.

**Tests**

- Shell/unit tests cover backend, frontend, sandbox, egress webhook, shared deploy
  changes, and unrelated documentation changes.
- Static workflow validation covers pull request, main push, tag, and manual events;
  PR execution must not push release or `latest` tags.

## Unit 2 — Build and publish commit images

**Files**

- `.github/workflows/images.yml` or equivalent publication workflow — build with Buildx,
  publish SHA images to GHCR, and upload digest/build metadata.
- `deploy/kubernetes/scripts/build-and-push.sh` — align local image names, tags, targets,
  build args, and registry parameters with CI while keeping private-registry support.
- A `.dockerignore` or build metadata file only if the actual build context requires it.

**Interfaces**

- Inputs: `REGISTRY`, `REPO`, target list, image tag, and existing mirror build args.
- Outputs: image reference, digest, and source commit for every target.
- Image names remain `cubeplex-backend`, `cubeplex-frontend`, `cubeplex-sandbox`, and
  `cubeplex-egress-webhook`.

**Core logic**

- CI logs in to GHCR with GitHub Actions permissions, not a repository password.
- Build cache, SBOM/provenance, and digest output are build artifacts. A commit tag
  cannot be overwritten with different content.
- `latest`/`edge` are not production inputs; if retained, they are updated only by an
  explicit development job.
- Sandbox failures do not fail unrelated application image jobs. The release manifest
  selects an existing sandbox reference without pulling it to the deployment machine.

**Tests**

- PR workflow performs real Docker builds for affected targets.
- Backend/frontend health or startup smoke tests run after build; sandbox tests cover
  command execution, workspace file writes, and browser runtime startup.
- The workflow verifies that every output digest is non-empty and pullable.

## Unit 3 — Publish release tags and release manifest

**Files**

- `.github/workflows/release.yml` — respond to `v*` tags, verify main metadata, add the
  release tag to the same digest, and generate the manifest.
- `deploy/releases/` manifest/values template — store non-secret image combinations;
  exact naming follows the existing deployment convention during implementation.
- `deploy/README.md`, `deploy/kubernetes/INSTALL.md`, `deploy/kubernetes/INSTALL.zh.md`,
  and `deploy/docker-compose/INSTALL.md` — document registry selection, manifest usage,
  and version updates.

**Interfaces**

- Inputs: `v<semver>`, source commit, image SHA digests, and the selected sandbox digest.
- Output: a manifest containing release, source commit, backend, frontend, sandbox, and
  egress webhook references.
- Helm and Compose commands accept the manifest separately from operator secrets.

**Core logic**

- The release workflow looks up digests from the main build. If they are missing, it
  waits for the bounded publication window and then fails instead of rebuilding implicitly.
- The release tag must match backend, frontend package, and Helm chart version fields;
  version bumps happen in the release preparation PR before tagging.
- A sandbox digest may come from a recent independent sandbox release. This workflow does
  not pull it to the deployment machine or run a compatibility test.
- The manifest is the rollback unit. Reusing it cannot pull a moving `latest` tag.

**Tests**

- Generate a manifest for a release tag and verify all required image fields.
- Render Helm with the generated image values and confirm backend/frontend/egress image
  references and sandbox secret/config wiring.
- Render Compose config from the same release result and verify non-empty matching
  `BACKEND_TAG` and `FRONTEND_TAG` values.

## Unit 4 — Independent sandbox image publication

**Files**

- `.github/workflows/sandbox-image.yml` or a sandbox job in the image workflow — path,
  manual, and scheduled triggers; publish sandbox SHA/version tags.
- `deploy/images/sandbox/VERSION` — the independent semantic version for the sandbox
  image; a published version tag is never overwritten.
- `deploy/images/sandbox/` — change only when tests expose a build or startup issue;
  avoid unrelated refactoring.

**Interfaces**

- Inputs: sandbox source revision and the version in `deploy/images/sandbox/VERSION`.
- Outputs: sandbox digest and a manifest-ready reference.

**Core logic**

- Build sandbox only for sandbox changes, scheduled security rebuilds, or manual triggers.
- OpenSandbox server/execd/egress remain deployment-configured. This workflow does not
  contact the runtime or download the candidate image for a compatibility test.
- The egress webhook uses the cubeplex application commit tag and must match the chart's
  egress-image matching rule.

**Tests**

- Sandbox Docker build and image metadata smoke tests.
- Existing sandbox E2E/nightly jobs remain responsible for runtime behavior and continue
  to use their own credentials.

## Verification and rollout order

1. Add metadata/change detection and PR build-only validation; confirm PRs cannot publish production tags.
2. Add main SHA publication and digest artifacts; verify GHCR permissions and cache.
3. Add the independent sandbox image workflow and metadata validation.
4. Add release-tag promotion and manifest generation.
5. Update Helm/Compose deployment docs and operator entry points, then switch production deployment to manifests.

Each implementation unit runs only its changed-module tests during development. A code
PR containing workflows, Dockerfiles, or deployment docs uses the repository pre-push
CI-equivalent gate before publication.

## Explicit non-goals

- Automatic Kubernetes rollout or a GitOps controller.
- Changes to upstream OpenSandbox image build processes.
- Secrets in release manifests.
- Production version advancement by editing Helm default tags.
