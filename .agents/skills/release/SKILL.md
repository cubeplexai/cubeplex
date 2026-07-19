---
name: release
description: Prepare and publish cubeplex releases across backend, frontend, sandbox, Helm, Docker Compose, Git tags, and container registries. Use when cutting a release, bumping release versions, publishing Docker images, promoting image digests, updating deployment values, or troubleshooting release ordering.
---

# Cubeplex release workflow

Use this skill for a production or self-hosted cubeplex release. Follow the
workflow in [references/release-workflow.md](references/release-workflow.md) for
the exact version, image, tag, and deployment contracts.

## Required sequence

1. Inspect the current branch, worktree, `origin/main`, and existing release tags.
2. Prepare a release PR that bumps the application package/chart versions and,
   only when sandbox contents change, `deploy/images/sandbox/VERSION`.
3. Run the version-consistency check and the repository CI-equivalent checks.
4. Merge the release PR into `main`; wait for the main image workflow to publish
   commit-tagged images.
5. Create `v<semver>` on that exact merged commit and push the tag.
6. Let the release workflow verify versions, wait for the commit images, promote
   the same manifests to release tags, and attach the release manifest.
7. Deploy using the manifest's release tags or digests. Do not edit chart defaults
   or use `latest` for production.

## Guardrails

- Never rebuild different image content during tag promotion.
- Never overwrite an existing application or sandbox version tag.
- Do not run sandbox runtime compatibility tests from the image release workflow;
  existing sandbox E2E/nightly workflows remain separate.
- Keep registry credentials and runtime secrets out of release manifests.
- If the main image workflow failed or the expected commit image does not appear,
  stop and fix the build; do not create a replacement image under the same tag.
