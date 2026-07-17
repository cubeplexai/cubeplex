# Restore automatic application CI

## Goal

Run the full application CI automatically for every pull request and every push
to `main`, while keeping `/ci` as an explicit way to rerun a pull request.

## Context

The original CI baseline requires backend checks, frontend checks, backend E2E,
frontend E2E, and the Layer 1 plugin contract suite on pull requests and pushes
to `main`. The current workflow only starts from `workflow_dispatch` or a `/ci`
pull-request comment, so ordinary pull requests can merge without a current
remote CI result.

The workflow also contains a disabled Layer 2 cross-repository EE compatibility
job. Its referenced `cubeplex-ee` repository is not currently available, so
enabling that placeholder would make every run fail before executing a test.

## Approaches considered

1. Restore all application jobs on pull requests and `main` pushes, and retain
   `/ci` for explicit reruns. This restores the intended merge gate and keeps
   the existing manual recovery path.
2. Run only backend/frontend checks automatically and leave E2E behind `/ci`.
   This is cheaper, but a pull request could pass without exercising database,
   object storage, backend startup, or browser flows.
3. Keep the workflow manual and document that developers must type `/ci`.
   This preserves the current cost profile but does not enforce CI before merge.

Approach 1 is selected because it matches the repository's CI baseline and
makes a fresh full result the default instead of an optional convention.

## Design

### Triggers and target revision

`.github/workflows/ci.yml` will listen for:

- pull requests;
- pushes to `main`;
- manual dispatch;
- new issue comments whose body starts with `/ci`.

The gate job will accept those four event types. It will check out the pull
request head SHA for `pull_request`, resolve the current pull request head for
`/ci`, and use `github.sha` for push and manual runs.

### Check reporting

Native pull-request runs already publish their jobs in the pull request Checks
panel. The custom aggregate `CI` check run remains only for `/ci`, because an
`issue_comment` workflow run is otherwise attached to the default branch rather
than the pull-request head.

Manual and push runs use their normal Actions run status. The report job updates
the custom check only for `/ci`.

### Job coverage

Automatic runs execute the existing:

- backend lint, typing, and unit tests;
- frontend lint, formatting, typing, unit tests, and build;
- backend E2E;
- frontend Playwright E2E;
- Layer 1 plugin contract tests.

The disabled Layer 2 cross-repository EE job remains disabled until its
repository and integration suite exist and can be checked out with the
configured secret.

### Concurrency

New commits to the same pull request cancel the older run. `/ci` reruns for the
same pull request share the same concurrency group.

## Out of scope

- Creating or populating the private `cubeplex-ee` repository.
- Changing product code or test contracts solely to make an unrelated failure
  disappear.
- Enabling real-LLM tests on every pull request; those remain in the dedicated
  nightly workflow.
- Changing repository branch-protection settings.

## Success criteria

- Opening or updating a pull request starts the full application CI without a
  comment.
- A push to `main` starts the same full application CI.
- A `/ci` comment still reruns the pull-request head and publishes an aggregate
  `CI` check on that SHA.
- Workflow configuration validation passes locally.
- The pull-request-triggered remote run completes with every enabled job green.
