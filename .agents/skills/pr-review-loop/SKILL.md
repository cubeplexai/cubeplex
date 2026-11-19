---
name: pr-review-loop
description: Use when a branch or PR is ready for review and needs an iterative local subagent review loop — spawn a fresh reviewer subagent on the diff → triage P1/P2 findings → fix & run incremental tests → commit → repeat until clean. Triggers on phrases like "跑 review loop", "用子代理 review", "本地 review 循环", "review 直到 ok", "PR review 循环".
---

# PR Review Loop (Local Subagent)

An iterative, local code-review loop driven by fresh reviewer subagents.
Each round spawns an independent subagent with no author bias to inspect the
branch diff against the base branch, surface P1/P2 issues, and verify fixes
until a pass comes back clean.

## When To Use

- User asks to run a review loop before or after opening/pushing a PR
  (e.g. "用子代理 review", "跑 review loop", "review 直到 clean").
- User explicitly invokes `/pr-review-loop` or names the skill.

**Do not** auto-invoke after every commit or push — only when the loop is
requested or after asking the user.

## Prerequisites

- You are on a feature branch (never `main`), with changes committed (or
  staged) to diff against a base branch (default `origin/main`).
- You know the **absolute workspace path** (e.g. `/home/.../cubeplex` or
  worktree path). Subagents do **not** inherit your CWD, so every subagent
  prompt must pin the absolute path and remind it to read `.worktree.env`
  if working in a worktree.

## Scope Decision Belongs To The User

This skill does **not** decide whether to split a change into spec / plan /
code PRs. That decision is upstream (`feature-workflow`):

- 1 PR for tightly-coupled changes (small fix, single concern).
- N PRs (spec → plan → code) when each artifact can be reviewed
  independently, or when the spec/plan needs validation before code.

## The Loop

For each review round:

1. **Snapshot the diff state.**
   - Ensure the latest edits are committed so each round reviews a concrete
     commit range `<base>...HEAD` (default `<base>` is `origin/main`).
   - Record the current short SHA (`git rev-parse --short HEAD`) and changed
     files (`git diff --name-only <base>...HEAD`).

2. **Spawn a fresh reviewer subagent.**
   - Use a read-only / research-capable subagent (a fresh instance each round
     so it has no author bias or stale context).
   - **Choose the reviewer model tier based on change difficulty and complexity**
     (rather than using one fixed model for everything):
     - **Low complexity** (docs, copy, small isolated UI/helper tweaks, trivial
       single-file fixes): use a fast/lightweight model tier (e.g. `flash`).
     - **Medium complexity** (standard multi-file features, bug fixes, tests,
       moderate refactors): use the standard/default model tier (`inherit`).
     - **High complexity / high risk** (auth/RBAC, LLM/prompt-cache paths,
       DB migrations, concurrency/sandbox lifecycle, cross-subsystem changes):
       use the highest-capability reasoning model tier (e.g. `pro`).
   - Pin the **absolute repo/worktree path** in the prompt.
   - Instruct the reviewer to:
     - Run `git -C <abs-path> diff <base>...HEAD` and
       `git -C <abs-path> log --oneline <base>..HEAD`.
     - Read surrounding code in modified files and related docs/tests when
       context is needed — don't judge hunks in isolation.
     - Review across **three dimensions** (do not limit the review to
       surface-level code bugs):
       1. **Product design**: Does the behavior make sense for end users?
          Check UX consistency, edge-case flows, error/empty/loading states,
          API/CLI ergonomics, unclear or misleading copy, and unintended
          product regressions.
       2. **Architecture design**: Are module and layer boundaries clean
          (route vs. service vs. repo; page vs. reusable module)? Check for
          unnecessary coupling, over-engineering vs. missing abstractions,
          state-management/concurrency flaws, scalability bottlenecks, and
          alignment with existing subsystem designs.
       3. **Code implementation & invariants**:
          - `AGENTS.md` Hard Code Rules (strict types, 100-char code line limit,
            tz-aware `datetime` + `utc_isoformat()`, public ID prefixes,
            alembic autogen, scope-isolated routes & pages, no drive-by refactors).
          - `backend/docs/prompt-cache-discipline.md` (if touching LLM/prompt/tools).
          - `backend/docs/auth.md` (if touching auth/RBAC/scoping).
          - `docs/testing.md` (test placement: unit vs integration vs e2e;
            testing real invariants rather than mocks/DOM snapshots).
          - Bilingual docs (`docs/site/docs/...` and
            `docs/site/i18n/zh-Hans/...`) if user-facing behavior changed.
     - If prior rounds declined specific P2s with rationale, include those
       declined items briefly so the reviewer doesn't re-flag them without
       new evidence.
     - Output structured findings:
       - Each finding: `severity` (**P1** / **P2** / **nit**), `file:line`,
         `issue` (what is wrong and why), and `suggested fix`.
       - If there are **no P1 and no P2** findings, explicitly end with
         `VERDICT: CLEAN` (optional nits may still be listed).

3. **Wait for the reviewer subagent.**
   - Do not poll in a loop; stop calling tools or do unrelated read-only prep
     until the subagent replies.

4. **Triage findings.**
   Apply the actionability rules below:
   - **P1** (blocking): Must fix.
   - **P2** (important): Must fix unless clearly unjustified (if declining,
     record a concise one-line rationale).
   - **nit** (style / minor): Case by case. Fix if trivial; skip if subjective.

   **Exit check:** If the reviewer returned `VERDICT: CLEAN` (0 P1, 0 P2),
   exit the loop and proceed to **Finish**.

5. **Fix, test, and commit.**
   - Implement fixes for all accepted P1/P2 findings.
   - Run incremental tests for the changed module(s) (reserve the full
     `make check-ci` suite for the pre-push hook).
   - Commit the fixes as a new commit (`git commit`, never `--amend` mid-loop,
     and never `--no-verify` for code or test changes).
   - Loop back to **Step 1** with a fresh reviewer subagent.

**Additional exit / escalation conditions:**
- User asks to stop.
- The same finding keeps recurring after 2 fix attempts, or the loop reaches
  4 rounds without converging — stop and escalate to the user with the
  disagreement / remaining findings.

## Actionability Rules

| Marker | Meaning | Action |
|---|---|---|
| **P1** | Correctness bug, security/auth/RBAC leak, data corruption, broken types/contracts, prompt-cache regression, missing migration, naive `datetime`, or fundamentally flawed product/architecture design | **Must fix.** |
| **P2** | Edge-case gap, UX/API ergonomics issue, layer/boundary leak or unnecessary coupling, missing/misplaced test (`docs/testing.md`), duplicated logic, scope-isolation smell, missing bilingual doc update | **Must fix** unless you can justify declining in one clear sentence. |
| **nit** | Minor naming, formatting, or subjective preference | Case by case. Fix if trivial; skip if subjective. |

## Finish

Once a round returns `VERDICT: CLEAN`:

1. Ensure all fixes are committed and incremental tests passed after the
   last edit.
2. If pushing / updating a PR is part of the user's request, push to remote
   (letting the pre-push hook run `make check-ci` for the changed sides).
3. Report a concise summary to the user:
   - Number of review rounds run and commit SHAs.
   - P1/P2 findings fixed (with file references).
   - Any P2 findings declined and why.
   - Test/verification commands run and their results.

## Common Pitfalls

- **Forgetting absolute path in subagent prompt:** Subagents do not inherit
  CWD; always pass the absolute repo/worktree path.
- **Reusing the same subagent across rounds:** Spawn a fresh subagent each
  round so it reviews the current diff with fresh eyes instead of anchoring
  on its earlier comments.
- **Using `--no-verify` on code/test pushes:** Don't bypass pre-push hooks;
  investigate hook failures instead.
- **Switching branches mid-loop:** Stay on the feature branch.
- **Amending commits mid-loop:** Prefer new commits per fix round so each
  round's diff and SHA trail stay clear.
