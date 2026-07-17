---
name: feature-workflow
description: Use at the START of a large, unclear, or multi-subsystem cubeplex feature — before writing code — to isolate work, resolve the design, and write a spec/plan under docs/dev. Clearly specified, small, single-concern tasks skip spec, plan, clarification, and review and execute directly. Covers worktree provisioning (./scripts/new-worktree), reading .worktree.env for allocated ports/DBs, brainstorming requirements, and one-concern-per-PR planning. Triggers on phrases like "做个新功能", "开个 worktree", "先写 spec/plan", "怎么开始这个 feature", "plan before code", "brainstorm 一下需求".
---

# Feature Workflow (cubeplex)

The front half of cubeplex's workflow for changes that need design work:
**worktree → clarify → spec/plan → execute**. This replaces the generic
brainstorm/plan/execute skills with the repo's actual commands and paths. Read
[docs/worktrees.md](../../../docs/worktrees.md) and AGENTS.md → "Workflow
Discipline" for the authoritative rules; this skill is the operating loop.

## Choose the workflow

Use the full workflow when the task is a large feature, spans independent
subsystems, has unresolved requirements, or requires a meaningful design
decision. Use it when the user explicitly requests a spec, plan, brainstorming,
or review.

Directly execute a clearly specified, small, single-concern task. Do not create
a spec or plan, ask for requirement clarification, or wait for design review
just because the change is user-facing or touches documentation, a route, or UI.
Inspect the affected files, make the surgical change, and verify it.

## 1. Worktree before spec/code for the full workflow

Always from the **main repo root** (not inside another worktree):

```bash
./scripts/new-worktree feat/YYYY-MM-DD-<name>
```

Branches from `origin/main`, allocates ports/DBs, runs migrations, pushes the
branch (details: [docs/worktrees.md](../../../docs/worktrees.md)).

**First thing on entry** — read the allocated values; never assume ports
8000/3000. Use `CUBEPLEX_API__PORT` etc. from it for every dev-server / curl /
Playwright command:

```bash
cat .worktree.env
```

## 2. Clarify intent, then write the spec

Explore first — the relevant files, `docs/`, and recent commits — so questions
are informed, not generic. Then resolve ambiguity **one question at a time**
(purpose, constraints, success criteria); don't batch five questions or invent
requirements to fill gaps. Genuine forks worth asking:

- What business invariant or user-visible contract does this protect?
- Which scope — workspace (`/api/v1/ws/{ws}/...`), org-admin
  (`/api/v1/admin/...`), or both? They are **separate handlers** (AGENTS.md
  "Scope-isolated APIs"). Decide now; it shapes the whole design.
- What's explicitly out of scope?

**Scope check.** If the request spans multiple independent subsystems, stop and
decompose — one spec → plan → PR cycle per subsystem. A spec that tries to
cover everything produces a plan nobody can review.

**Propose 2-3 approaches** with trade-offs and your recommendation before
committing to one. Then write the spec to
`docs/dev/specs/YYYY-MM-DD-<slug>-design.md`:

- **Goal** — one sentence: what this builds and why.
- **Context** — current behavior and why it's changing.
- **Approaches considered** — the options + why you chose this one.
- **Design** — what literally happens, in sections scaled to complexity: data
  model / migrations, API surface (routes, request/response shapes), scope &
  RBAC, UI flow, edge cases.
- **Out of scope** — what this deliberately does not do.
- **Success criteria** — the observable invariants that prove it works.

Plain language (AGENTS.md rule 12): say what literally happens + why, in
concrete words — no invented jargon.

**Spec self-review** (a checklist you run yourself — not a review gate): skim
for placeholders (`TBD`, "handle edge cases"), internal contradictions,
ambiguity, and scope creep. Fix inline. Then have the user glance at the spec
before you turn it into a plan.

## 3. Turn the spec into a plan

Write the plan to `docs/dev/plans/YYYY-MM-DD-<slug>.md` **before code**.
Investigations / decisions go to `docs/dev/notes/`. `docs/dev` files are frozen
snapshots — rebase the content, don't rewrite history.

**The plan's job is to be reviewable for soundness — not to pre-write the
code.** You can't judge whether unwritten, non-TDD'd code is "right", so don't
fill the plan with literal implementation; it just makes the plan un-reviewable
and it'll change anyway. Capture the decisions a reviewer actually needs to
sanity-check. The red→green→commit detail belongs to execution
(`/cubeplex-tdd`), not here.

**Header:** *Goal* (one sentence) · *Architecture* (2-3 sentences: how the
pieces fit, the key data flow, the load-bearing decision) · *Tech stack*.

**For each unit of work, write down:**

- **Files** — which to create/modify (`path`) and each one's responsibility /
  what changes in it. Files that change together live together; follow existing
  patterns, don't unilaterally restructure.
- **Interfaces** — the contracts that lock the decomposition: API routes with
  request/response shapes, function/type signatures, DB schema / migration
  changes, event shapes. Concrete enough that the next unit can call into it.
- **Core logic** — the non-obvious control flow, algorithm, or invariant, in
  prose or short pseudocode. Say *what happens and why*, not the final
  Python/TS line-for-line.
- **Tests** — which invariant each unit must protect and where it lands (unit /
  integration / e2e per `/cubeplex-tdd`) — the intent, not the test body.

**Right altitude:** a teammate can read it and tell you "that interface is
wrong" or "this misses a case" *without the code existing yet*. If you're
pasting what will become the actual implementation, you've gone too fine.

**Concrete, not vague — the fix for "too detailed" is not hand-waving.**
"Handle edge cases" / "add validation" / "TBD" are non-answers: name the actual
edge cases and the actual rule, then stop there.

**Plan self-review** (a self-checklist, **not** a per-step review gate):

1. **Spec coverage** — point each spec requirement to a unit of work; add for gaps.
2. **Interface consistency** — a type / route / signature named one way in one
   unit and differently in another is a bug. Reconcile.
3. **Vagueness scan** — any "handle edge cases"-class hand-wave → make it concrete.

Fix inline and move on.

## 4. Push the spec/plan PR first — before any code

Spec + plan are the first reviewable artifact. **Commit them and push a PR
before writing implementation code** — get the design reviewed while it's still
cheap to change. Implementation lands as follow-up commits / PR(s) (one concern
per PR, rule 6).

```bash
git add docs/dev/specs/<slug>-design.md docs/dev/plans/<slug>.md
git commit -m "spec+plan: <feature>"
git push --no-verify        # pre-push checks are too slow to run every push
gh pr create --title "<feature> — spec & plan" --body "..."
```

**`--no-verify` rule:** OK **only** when the push has no code and no test
changes (spec/plan/notes, config) — the hooks exist to protect code/tests, so
there's nothing to catch. Any push with code or tests keeps the hooks: the
pre-push hook *is* the `make check-ci` gate, so don't run check-ci by hand.
(Skipping here also matches `new-worktree`, which pushes with `--no-verify`.)

## 5. Execute

For the full workflow, work the plan's units of work top to bottom. For a direct
task, make the scoped change immediately. In both cases, stay on the feature
branch, never auto-switch to main or start a merge mid-execution (rule 8), and
verify before claiming completion. **No review gate between steps or units** —
the per-step verifications plus the pre-push hook are the checkpoints.

- **Simplest thing that works.** Write the minimum code for the requirement —
  no speculative abstractions, config, or flexibility nobody asked for, no
  handling for impossible cases. If 200 lines could be 50, rewrite. The test:
  "would a senior call this overcomplicated?" → if yes, simplify. (Keep changes
  surgical too — AGENTS.md "Authoring Conventions".)
- Test-first loop → **`/cubeplex-tdd`**. Stuck on a bug → **`/debug-cubeplex`**.
- Per task, run only the changed-module tests (rule 11); the pre-push hook runs
  check-ci on the code push.
- Before the PR: verify the feature with a real command, paste the evidence,
  then push and open the PR.
- After the PR is open, **ask the user whether to start
  `/pr-codex-review-loop`** — don't kick it off automatically.

Stop and ask only on a real blocker (missing dependency, unclear instruction, a
verification that keeps failing) — don't guess through it.

## Guardrails

All AGENTS.md hard rules apply. The one most missed mid-feature: **docs ship
with the code in the same PR** (rule 13).
