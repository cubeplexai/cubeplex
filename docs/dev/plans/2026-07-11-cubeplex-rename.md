# CubePlex Full Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename every first-party active project identifier from CubeBox to CubePlex without retaining compatibility aliases.

**Architecture:** The migration is a controlled mechanical rename: first add an audit that defines the allowed identifier set, then rename tracked paths and text using ordered, case-sensitive substitutions. Package/import changes, operational manifests, and product copy are updated as coherent groups, then builds and audits prove that no active old-name references remain.

**Tech Stack:** Python 3.13/FastAPI, uv, pnpm/Next.js, Docusaurus, Docker Compose, Helm/Kubernetes, Git.

## Global Constraints

- Rename all owned forms exactly: `cubebox` → `cubeplex`, `Cubebox` → `Cubeplex`, `CubeBox` → `CubePlex`, `CUBEBOX` → `CUBEPLEX`.
- Treat case-sensitive and case-insensitive search results as separate acceptance checks.
- Modify only tracked first-party files through `git ls-files`; exclude `.git`, `node_modules`, `.venv`, and vendored upstream source unless the repository-owned integration identifier requires the change.
- Do not retain aliases, redirect packages, or old environment-variable names.
- Document each external-state breaking change (database/volume names, Helm releases/resources, image names, environment variables) in deployment guidance.
- Work from an isolated worktree. Read `.worktree.env` before running services or tests.

---

### Task 1: Add a durable old-name audit

**Files:**
- Create: `backend/tests/test_project_name_audit.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: the repository root discovered from `Path(__file__).parents[2]` and tracked paths from `git ls-files -z`.
- Produces: `test_tracked_files_do_not_contain_old_project_name`, which fails with every path and line containing a prohibited old-name form.

- [ ] **Step 1: Write the failing audit test**

```python
from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[2]
OLD_NAME = "cubebox"
EXCLUDED_PREFIXES = ("backend/alembic/versions/",)


def test_tracked_files_do_not_contain_old_project_name() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout.decode().split("\0")
    matches: list[str] = []
    for relative in filter(None, tracked):
        if relative.startswith(EXCLUDED_PREFIXES):
            continue
        path = ROOT / relative
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if OLD_NAME in line.lower():
                matches.append(f"{relative}:{line_number}: {line}")
    assert not matches, "Old project name remains:\n" + "\n".join(matches)
```

- [ ] **Step 2: Run the audit to verify it fails on the current repository**

Run: `cd backend && uv run pytest tests/test_project_name_audit.py -v | tee ../tmp/cubeplex-audit-red.log`

Expected: FAIL with tracked `cubebox` references, including `pyproject.toml` and source imports.

- [ ] **Step 3: Add an explicit temporary migration exemption for historical Alembic revisions**

Update `EXCLUDED_PREFIXES` only with `backend/alembic/versions/`, and add a comment explaining that applied revision metadata is immutable unless an isolated database migration proves it can change safely. Do not exempt source, configuration, documentation, or deployment files.

- [ ] **Step 4: Commit the audit contract**

```bash
git add backend/tests/test_project_name_audit.py
git commit -m "test: enforce CubePlex project naming"
```

### Task 2: Rename tracked filesystem paths and backend Python namespace

**Files:**
- Rename: `backend/cubebox/` → `backend/cubeplex/`
- Rename: `deploy/kubernetes/charts/cubebox/` → `deploy/kubernetes/charts/cubeplex/`
- Modify: `backend/pyproject.toml`, `backend/uv.lock`, `backend/main.py`, `backend/alembic.ini`, `backend/alembic/env.py`, `backend/tests/**`, `backend/scripts/**`, `backend/config*.yaml`, `Makefile`, `scripts/**`

**Interfaces:**
- Consumes: the Task 1 audit.
- Produces: importable `cubeplex` Python package and `cubeplex` CLI entry point; no `cubebox` package directory remains.

- [ ] **Step 1: Rename tracked directories with Git**

```bash
git mv backend/cubebox backend/cubeplex
git mv deploy/kubernetes/charts/cubebox deploy/kubernetes/charts/cubeplex
```

- [ ] **Step 2: Apply ordered case-sensitive substitutions to tracked non-migration text files**

Run this from the repository root; it operates only on Git-tracked UTF-8 files and preserves the deliberately excluded Alembic revision directory:

```bash
git ls-files -z \
  | grep -zv '^backend/alembic/versions/' \
  | xargs -0 -r perl -pi -e 's/CUBEBOX/CUBEPLEX/g; s/CubeBox/CubePlex/g; s/Cubebox/Cubeplex/g; s/cubebox/cubeplex/g'
```

- [ ] **Step 3: Update backend packaging deliberately**

Confirm these `backend/pyproject.toml` values all use `cubeplex`: project name,
description, script name/target, entry-point group and targets, coverage target,
package list, Ruff first-party list, mypy files/modules, and coverage source.
Regenerate the lockfile through uv instead of editing it manually:

```bash
cd backend && uv lock
```

- [ ] **Step 4: Run targeted import and audit checks**

```bash
cd backend && uv run python -c 'import cubeplex; print(cubeplex.__name__)'
cd backend && uv run pytest tests/test_project_name_audit.py -v | tee ../tmp/cubeplex-backend-audit.log
```

Expected: import prints `cubeplex`; the audit may still report frontend, documentation, and deployment references until later tasks, but must not report backend source or packaging references.

- [ ] **Step 5: Commit the namespace rename**

```bash
git add backend Makefile scripts deploy/kubernetes/charts
git commit -m "refactor: rename backend package to CubePlex"
```

### Task 3: Rename frontend workspaces, docs site metadata, and product copy

**Files:**
- Modify: `frontend/package.json`, `frontend/pnpm-workspace.yaml`, `frontend/pnpm-lock.yaml`, `frontend/packages/core/package.json`, `frontend/packages/**`, `docs/site/package.json`, `docs/site/pnpm-lock.yaml`, `docs/site/docusaurus.config.ts`, `docs/site/docs/**`, `README*`, `CONTRIBUTING.md`, `AGENTS.md`, `THIRD_PARTY_NOTICES.md`

**Interfaces:**
- Consumes: package names and import paths produced by Task 2.
- Produces: `@cubeplex/core`, CubePlex frontend/docs package metadata, and product copy without the old name.

- [ ] **Step 1: Update workspace package identifiers and imports**

Ensure `frontend/packages/core/package.json` is named `@cubeplex/core`, then replace every `@cubebox/core` import/reference with `@cubeplex/core`. Rename frontend and docs workspace package names from `cubebox-*` to `cubeplex-*`.

- [ ] **Step 2: Regenerate pnpm lockfiles from the owning workspaces**

```bash
cd frontend && pnpm install --lockfile-only
cd ../docs/site && pnpm install --lockfile-only
```

- [ ] **Step 3: Run frontend package and documentation checks**

```bash
cd frontend && pnpm --filter @cubeplex/core build | tee ../tmp/cubeplex-core-build.log
cd docs/site && pnpm build | tee ../../tmp/cubeplex-docs-build.log
```

Expected: both commands exit 0; package resolution must not mention `@cubebox/core`.

- [ ] **Step 4: Commit frontend and documentation rename**

```bash
git add frontend docs/site README.md CONTRIBUTING.md AGENTS.md THIRD_PARTY_NOTICES.md
git commit -m "refactor: rename frontend and docs to CubePlex"
```

### Task 4: Rename operational identifiers and document breaking deployment changes

**Files:**
- Modify: `deploy/docker-compose/**`, `deploy/images/**`, `deploy/kubernetes/**`, `deploy/README.md`, `backend/config*.yaml`, `backend/README.md`, `frontend/docs/auth-and-sse.md`, `frontend/docs/quick-reference.md`

**Interfaces:**
- Consumes: `cubeplex` module/package names from Tasks 2–3.
- Produces: operational manifests that consistently deploy CubePlex and documentation that explicitly calls out incompatible old resource names.

- [ ] **Step 1: Audit all deployment values before editing**

```bash
rg -n -i 'cubebox' deploy backend/config*.yaml backend/README.md frontend/docs
```

Classify each result as Docker/Compose, Kubernetes/Helm, configuration/environment variable, or prose/example. Do not change a Kubernetes selector without changing every matching deployment/service/template selector in the same commit.

- [ ] **Step 2: Apply the exact ordered substitutions from Task 2**

Use the tracked-files command from Task 2, scoped to `deploy/`, `backend/config*.yaml`, and the listed deployment docs. Confirm Docker images, Compose service/volume/container names, Helm chart metadata/template labels, Kubernetes selectors, environment variable names, and scripts all agree on `cubeplex`.

- [ ] **Step 3: Add explicit upgrade notes**

In `deploy/README.md`, add a CubeBox-to-CubePlex migration section stating that changed image names, Compose volumes, Kubernetes/Helm resource names, release names, environment-variable prefixes, and database names are breaking changes. State that operators must migrate data/values intentionally rather than expecting an in-place rename.

- [ ] **Step 4: Render and validate deployment definitions**

```bash
helm lint deploy/kubernetes/charts/cubeplex
helm template cubeplex deploy/kubernetes/charts/cubeplex > tmp/cubeplex-helm.yaml
docker compose -f deploy/docker-compose/compose.yaml config --quiet
```

Expected: all commands exit 0; `tmp/cubeplex-helm.yaml` has consistent CubePlex resource references.

- [ ] **Step 5: Commit operational rename**

```bash
git add deploy backend/config*.yaml backend/README.md frontend/docs
git commit -m "chore: rename deployment identifiers to CubePlex"
```

### Task 5: Complete case-sensitive verification and final repository cutover

**Files:**
- Modify: all tracked first-party files reported by the audit
- Test: `backend/tests/test_project_name_audit.py`

**Interfaces:**
- Consumes: all prior task changes.
- Produces: a repository whose active tracked first-party content and paths contain only CubePlex forms.

- [ ] **Step 1: List remaining text matches with both search modes**

```bash
rg -n 'cubebox|Cubebox|CubeBox|CUBEBOX' -g '!backend/alembic/versions/**' -g '!**/node_modules/**' -g '!**/.venv/**' -g '!**/.git/**' .
rg -n -i 'cubebox' -g '!backend/alembic/versions/**' -g '!**/node_modules/**' -g '!**/.venv/**' -g '!**/.git/**' .
```

Expected: no output from either command. Inspect every remaining match, including immutable historical migration metadata, and either safely rename it or record its external-state justification in `deploy/README.md`.

- [ ] **Step 2: List remaining filesystem-path matches with both search modes**

```bash
find . -path './.git' -prune -o -path '*/node_modules' -prune -o -path '*/.venv' -prune -o -name '*cubebox*' -print
find . -path './.git' -prune -o -path '*/node_modules' -prune -o -path '*/.venv' -prune -o -iname '*cubebox*' -print
```

Expected: no first-party paths. Remove/recreate generated local artifacts such as old virtual environments only after ensuring they are untracked.

- [ ] **Step 3: Run the full validation sweep**

```bash
cd backend && uv run pytest tests/test_project_name_audit.py -v | tee ../tmp/cubeplex-final-audit.log
cd frontend && pnpm --filter @cubeplex/core build | tee ../tmp/cubeplex-final-core.log
cd docs/site && pnpm build | tee ../../tmp/cubeplex-final-docs.log
helm lint ../../deploy/kubernetes/charts/cubeplex
docker compose -f ../../deploy/docker-compose/compose.yaml config --quiet
```

Expected: every command exits 0. Capture and review the final lines of each `tmp/cubeplex-*.log` file before claiming completion.

- [ ] **Step 4: Review the complete diff and commit the final audit resolution**

```bash
git diff --check
git status --short
git add -A
git commit -m "chore: complete CubePlex rename"
```

Confirm that `git add -A` contains only intended rename work and does not include pre-existing untracked planning documents from another task.
