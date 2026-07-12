# CubePlex Full Rename Design

**Status:** Approved for planning
**Date:** 2026-07-11
**Scope:** Rename the project from `cubeplex` to `cubeplex` everywhere the
repository owns the name. The rename covers all case variants, paths,
identifiers, packages, deployment resources, documentation, and generated
repository metadata.

## Decision

Perform a full, single-cutover rename. `cubeplex`, `CubePlex`, and `CUBEPLEX`
must no longer appear in active first-party repository content. The new name
uses matching case conventions: `cubeplex`, `CubePlex`, and `CUBEPLEX`.

No compatibility alias, dual package name, or old deployment identifier will
be retained. Historical identifiers embedded in immutable external systems
(such as an already-applied database migration revision ID) are renamed only
when doing so does not invalidate the external system's state; each exception
must be explicitly documented in the implementation results.

## Rename Surfaces

1. **Repository paths and source namespaces.** Rename the top-level checkout
   directory where feasible, `backend/cubeplex/`, Python imports and entry
   points, frontend workspace package names such as `@cubeplex/core`, package
   lockfiles, scripts, and tests.
2. **Runtime configuration.** Rename application/module references,
   environment-variable prefixes, config keys, logging names, filesystem
   paths, default database names, and test fixtures.
3. **Deployment resources.** Rename Docker image/service/volume/container
   identifiers, Compose files, Helm chart directories and metadata,
   Kubernetes resource labels/selectors, release names, scripts, and
   deployment documentation.
4. **Product and developer copy.** Rename UI product text, README content,
   docs site metadata, documentation examples and links, contribution
   guidance, notices, and project references.
5. **Repository metadata.** Rename CI/worktree tooling, package-manager
   metadata, build artifacts checked into the repository, and any other
   first-party generated files that contain the old name.

## Case-Sensitive Audit Rules

- The implementation maintains a replacement table for exact case forms:
  `cubeplex` → `cubeplex`, `Cubeplex` → `Cubeplex`, `CubePlex` → `CubePlex`, and
  `CUBEPLEX` → `CUBEPLEX`.
- Before and after modifications, run both case-sensitive and
  case-insensitive repository searches, excluding dependency directories and
  `.git`.
- Rename filesystem paths before validating imports, package references, and
  deployment templates so case-only filesystem mistakes cannot be hidden on a
  case-insensitive development filesystem.
- Review every remaining case-insensitive match. A remaining match is allowed
  only for an externally immutable identifier, with a written reason.

## Migration Sequencing

1. Inventory all matches and group them by the surfaces above.
2. Rename paths and update imports/package manifests together.
3. Update configuration and deployment identifiers while preserving internal
   reference consistency.
4. Update documentation, product copy, and repository metadata.
5. Regenerate lockfiles or other derived files only through the project tools.
6. Run focused backend, frontend, docs, and deployment validation, then the
   final case-sensitive and case-insensitive audits.

## Error Handling and Validation

- A rename that changes a database name, volume name, Helm release/resource,
  environment-variable name, or external image name is a breaking operational
  change. The implementation records it in deployment documentation rather
  than silently presenting it as an in-place upgrade.
- Python imports, frontend workspace references, Helm templates, Docker
  configuration, and documentation builds are validated after their respective
  changes.
- The final audit fails the rename if active first-party `cubeplex` text or
  paths remain, regardless of case.

## Non-Goals

- Preserving backwards compatibility for the old name.
- Renaming third-party vendored source or upstream project names unless the
  repository owns and executes that identifier as part of CubePlex.
- Altering application behavior beyond identifiers and operational names
  required by the rename.
