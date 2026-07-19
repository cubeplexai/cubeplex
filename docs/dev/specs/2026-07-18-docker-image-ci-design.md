# Docker Image CI/CD Design

## Goal

Build a repeatable and auditable Docker image pipeline for cubeplex: validate image
builds on pull requests, publish immutable commit images from `main`, promote the
same verified digests on formal releases, and make Kubernetes Helm and Docker Compose
use the same version-selection rules.

## Context

The repository already has backend/frontend Dockerfiles and
`deploy/kubernetes/scripts/build-and-push.sh`. The script pushes backend/frontend
images to an internal registry with a git short SHA tag and `latest`. Kubernetes
operators manually set image tags in `values.local.yaml`; Compose operators manually
set `BACKEND_TAG` and `FRONTEND_TAG` in `.env`. This works for manual deployment but
does not provide a reliable GitHub Actions build, publication, and release flow.

The sandbox image is different from the application images. It contains browsers,
fonts, Office, Python, and Node runtimes, so it is large and slow to build. Its
lifecycle is driven by sandbox Dockerfile changes, OpenSandbox/execd compatibility,
and base-image security updates. OpenSandbox server, execd, and egress are upstream
runtime images, not part of the cubeplex sandbox image. The Kubernetes egress webhook
is a separate cubeplex-owned image.

## Approaches considered

### 1. Build every image for every application release

Build backend, frontend, and sandbox in one workflow under one version.

This is simple, but rebuilds the large sandbox for unrelated application changes and
does not prove that the release uses the same image content that was previously tested.

### 2. Build on `main` and deploy `latest`

Build after every merge and have deployments pull `latest`.

This is easy to implement, but deployments are not auditable and rollback is unsafe
because a registry can move the tag.

### 3. Build application images and sandbox independently, then pin the release combination

Build backend/frontend for application commits and releases. Build sandbox only when
its context or security inputs change. A release manifest records the exact application
and sandbox digests that were tested together.

This is the selected approach because it avoids unnecessary sandbox builds while still
making each release reproducible.

## Design

### Image names and tags

GHCR is the canonical registry for GitHub Actions. Deployment configuration can point
to Harbor or the existing internal registry:

```text
ghcr.io/<owner>/cubeplex-backend
ghcr.io/<owner>/cubeplex-frontend
ghcr.io/<owner>/cubeplex-sandbox
ghcr.io/<owner>/cubeplex-egress-webhook
```

Each build produces at least:

- `sha-<full-commit-sha>` for an immutable commit selector;
- `v<semver>` for a formal release, pointing to the already verified digest.

`latest` or `edge` may remain as development convenience tags, but production must
not depend on them. Production should record and preferably consume image digests. If
the first Helm/Compose implementation only supports tags, it must use immutable SHA
or release tags.

Backend and frontend share the application release version. Sandbox has an independent
version such as `sandbox-v0.3.2`; it does not increment for every application release.
OpenSandbox server, execd, and egress versions remain managed by their own deployment
configuration.

### Workflow stages

#### Pull request

After the existing code CI, the image workflow builds affected images without publishing
production tags:

- `backend/**` or the backend Dockerfile: build backend;
- `frontend/**` or the frontend Dockerfile: build frontend;
- `deploy/images/sandbox/**`: build sandbox;
- `deploy/kubernetes/egress-bundle/**`: build the egress webhook;
- workflow, deployment-template, or shared-build changes: build all affected images.

Images use temporary PR tags or remain local to the runner. The workflow validates the
Docker build and, where runtime dependencies are available, runs backend health,
frontend startup, and basic sandbox execution smoke tests. It must not write `latest`,
release tags, or production registry namespaces.

#### `main`

After code CI succeeds, the workflow builds affected images and publishes
`sha-<full-commit-sha>`. It stores each digest, affected image, and source commit as
build metadata for the release workflow. A commit tag must never be overwritten with
different content.

#### Formal release

When a `v<semver>` Git tag is pushed, the release workflow confirms that the commit's
SHA images exist and passed validation. It then adds the release tag to those same
digests; it does not silently rebuild a different image.

The workflow also creates an auditable manifest:

```yaml
release: v0.8.0
source_commit: 6f3de2eb...
images:
  backend: ghcr.io/<owner>/cubeplex-backend@sha256:...
  frontend: ghcr.io/<owner>/cubeplex-frontend@sha256:...
  sandbox: ghcr.io/<owner>/cubeplex-sandbox@sha256:...
  egress_webhook: ghcr.io/<owner>/cubeplex-egress-webhook@sha256:...
```

If sandbox did not change in the application commit, the manifest references the most
recent sandbox digest that passed compatibility tests. If the backend/sandbox contract
changes, the release gate requires a new sandbox build and compatibility test.

### Registry authentication and publication

GitHub Actions uses repository permissions to log in to GHCR; registry passwords are
not stored in the repository. Private-registry mirroring is a later deployment concern
and must copy the same digest rather than rebuild different content.

Image names are configurable through Helm `image.registry`/`image.repository` and
Compose `IMAGE_REGISTRY`/`IMAGE_REPO`.

The local `build-and-push.sh` remains useful for operators and private registries, but
it must follow the same names and tag rules as CI. Production must not depend on a
manual `latest` update.

### Deployment version selection

`deploy/kubernetes/charts/cubeplex/values.yaml` keeps safe defaults and does not store
the production tag for a particular release. A committed release values/manifest file
contains backend, frontend, sandbox, and egress webhook versions or digests. Secrets
remain in the operator's gitignored `values.local.yaml` or a secret manager.

Helm passes default values, release image values, and operator secrets as separate
layers. Compose uses the same release result to fill `.env` values. Updating a version
means selecting a new release manifest, not modifying chart defaults or overwriting a
tag.

Chart `version` and `appVersion` describe Helm metadata. The image tag/digest and the
release manifest are the deployment lock.

### Sandbox build policy

The sandbox workflow supports three triggers:

1. automatic builds for sandbox Dockerfile, fonts, startup scripts, or build dependency changes;
2. `workflow_dispatch` for explicit rebuilds after OpenSandbox compatibility changes;
3. scheduled security rebuilds for base-image and browser dependency updates.

Successful builds publish independent `sha-*` and `sandbox-v*` tags. An application
release selects a sandbox digest that has passed compatibility tests; it does not
rebuild sandbox merely because backend/frontend changed.

## Out of scope

- Changing OpenSandbox server, execd, or upstream egress image build processes.
- Automatic Kubernetes rollout, rollback controllers, or a GitOps platform.
- Storing registry, LLM, or sandbox credentials in Git.
- Requiring digest-only Helm/Compose support in the first implementation.
- Implementing the workflows in this PR; this PR contains only design and plan documents.

## Success criteria

- PRs validate affected Docker builds without publishing production tags.
- `main` publishes backend/frontend images tagged with the full source commit and emits digests.
- A release tag promotes already verified digests instead of rebuilding different content.
- Sandbox builds occur only for sandbox changes, security rebuilds, or explicit triggers.
- One release manifest identifies the complete backend/frontend/sandbox/egress image set.
- Helm and Compose can deploy from the same version selection without relying on `latest`.
- Redeploying the same manifest produces the same image content; rollback only selects an older manifest.
