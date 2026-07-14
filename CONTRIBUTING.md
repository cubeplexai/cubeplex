# Contributing to cubeplex

Thanks for your interest! This doc covers how to set up your local environment so commits and pushes pass CI on the first try.

## Prerequisites

- Python 3.12+
- Node.js 20+
- pnpm 10+
- Docker (for running MySQL / Redis / RustFS locally, optional)

## First-time setup

```bash
git clone https://github.com/xfgong/cubeplex.git
cd cubeplex

# Agent skills — restore vendored skills from skills-lock.json into .agents/skills/
npx skills experimental_install

# Backend
cd backend
make dev-install
make pre-commit-install-all   # installs both pre-commit and pre-push hooks
cd ..

# Frontend
cd frontend
pnpm install
npx playwright install   # only if you plan to run e2e locally
cd ..
```

## Agent skills

Skills live in `.agents/skills/` and are symlinked into `.claude/skills/`
(and other agent dirs). There are two kinds, tracked differently:

- **Vendored skills** — installed from a GitHub source via `npx skills`. Only
  their entry in `skills-lock.json` is committed; their *content* is
  gitignored and restored with `npx skills experimental_install` (part of
  first-time setup above). Add one to the repo with:

  ```bash
  npx skills add <owner/repo> -s <skill-name>   # updates skills-lock.json
  ```

  Then add its dir under `.agents/skills/` to `.gitignore` (one line, next to
  the other vendored entries) so its content stays out of git.

- **Native skills** — hand-authored, cubeplex-specific (e.g.
  `feature-workflow`, `cubeplex-tdd`, `debug-cubeplex`, `pr-codex-review-loop`).
  These are **committed** — their `.agents/skills/<name>/` content is the source
  of truth. Create one with `npx skills init <name>` or by hand, and just commit
  it (no `.gitignore` entry, not in `skills-lock.json`).

Remove either kind with `npx skills remove <name>` (also updates the lock).

## Hook behavior

We use pre-commit with **two stages** and a strict **no-auto-fix** policy — hooks only _check_, they never rewrite your files:

- **pre-commit** (runs on `git commit`, ~10 seconds):
  - File hygiene checks: large file (>500 KB), YAML / JSON / TOML validity, merge conflict markers, accidentally-committed private keys, stray `pdb`/`breakpoint()` calls
  - Ruff `check --no-fix` and `ruff format --check` on staged Python files
  - ESLint (no `--fix`) and Prettier `--check` on staged frontend files

- **pre-push** (runs on `git push`, ~3 minutes):
  - `cd backend && make check-ci` (ruff + ruff-format + mypy + pytest unit)
  - `pnpm -r type-check && pnpm -r lint && pnpm -r format:check && pnpm -r test`

**If a hook fails, the hook does NOT modify your files.** Run the appropriate formatter manually and re-stage:

```bash
# Backend format issues
cd backend && make format && git add -u

# Frontend format issues
cd frontend && pnpm format && git add -u
```

## CI expectations

Every PR runs 4 jobs: `backend-check`, `frontend-check`, `e2e`, `test-ee-compat`. All must pass before merge. Full spec: [docs/dev/specs/2026-04-21-ci-baseline-design.md](docs/dev/specs/2026-04-21-ci-baseline-design.md).

## Code style

- Line length: 100 chars (Python and TS)
- Python: ruff format (double quotes), mypy strict
- TS: Prettier (single quote, no semi, 100 width), ESLint

## Running things locally

```bash
# Backend dev server
cd backend && python main.py

# Frontend dev server
cd frontend && pnpm dev

# Run backend unit tests
cd backend && make check-ci

# Run frontend tests
cd frontend && pnpm -r test
```
