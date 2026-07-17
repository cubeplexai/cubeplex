# Restore automatic application CI

**Goal:** Make the full application CI an automatic pull-request and `main`
merge gate again, while preserving `/ci` as an explicit rerun mechanism.

**Architecture:** One workflow continues to own all application checks. Native
`pull_request` and `push` events use GitHub's normal check reporting; the
existing custom aggregate check is limited to `/ci`, whose `issue_comment`
event is not naturally attached to the pull-request head. The disabled Layer 2
EE placeholder stays outside the enabled job graph until its repository exists.

**Tech stack:** GitHub Actions YAML, GitHub CLI/API for run inspection, existing
Makefile CI targets.

## Unit 1 — Restore automatic triggers and event-aware reporting

**Files:**

- `.github/workflows/ci.yml` — add pull-request and `main` push triggers,
  resolve the correct SHA for every supported event, and limit custom check-run
  creation/finalization to `/ci`.

**Interfaces:**

- Events: `pull_request`, `push` to `main`, `workflow_dispatch`, and
  `issue_comment`.
- Gate output `sha`: pull-request head SHA for pull-request and `/ci` runs;
  `github.sha` for push and manual runs.
- Gate output `check_run_id`: populated only for `/ci`; empty otherwise.

**Core logic:**

- The gate accepts every configured event, but only accepts issue comments that
  belong to a pull request and start with `/ci`.
- The checkout revision is always the revision being tested, never the default
  branch revision that delivered an issue comment.
- Native pull-request checks do not create or patch a duplicate custom check.
- The report job runs only for `/ci` and computes failure from every enabled CI
  job exactly as it does today.

**Tests:**

- Parse the workflow as YAML and run `actionlint` if available.
- Assert the trigger set, gate event cases, pull-request SHA expression, and
  `/ci`-only check/report conditions from the parsed workflow.

## Unit 2 — Verify the complete CI path remotely

**Files:**

- No additional source files expected. Any workflow correction must remain
  scoped to `.github/workflows/ci.yml`.

**Interfaces:**

- A pull request from `fix/2026-07-16-restore-ci` to `main`.
- Enabled jobs: gate, backend check, frontend check, backend E2E, frontend E2E,
  Layer 1 EE compatibility.

**Core logic:**

- Push the workflow branch and open a pull request so the restored
  `pull_request` trigger exercises the branch's workflow definition.
- Inspect each failed GitHub Actions job and its logs, reproduce locally where
  possible, and apply only fixes tied to the observed failure.
- Re-run failed jobs or push a focused follow-up until every enabled job is
  green.

**Tests:**

- Relevant local Makefile target for each observed failure.
- Final GitHub Actions run with every enabled job successful.
