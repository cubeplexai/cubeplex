# Worktree Parallel Dev Isolation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build helper scripts and config hooks so multiple `git worktree`s can run dev servers and full E2E suites in parallel without colliding on ports, MySQL schemas, or Redis keys.

**Architecture:** A Python script `scripts/worktree-env` allocates a stable per-worktree slot (offset 0–99) using `sha1(slug) % 100` with collision resolution via `.worktrees/registry.json`. Allocations land in a worktree-local `.worktree.env` file (gitignored). Backend config (`backend/cubeplex/config.py`), Next (`frontend/packages/web/next.config.ts`), and Playwright (`frontend/playwright.config.ts`) auto-load this file via `python-dotenv` / `dotenv`, so existing entry points (`python main.py`, `pnpm dev`, `pnpm test:e2e`) just work. Backend E2E `_flush_test_redis` is changed from `FLUSHDB` to a prefix-scoped scan-delete so two worktrees can run E2E in parallel against the same Redis.

**Tech Stack:** Python 3 stdlib (registry, slug, allocation, subprocess to git/mysql/alembic/redis), `python-dotenv` (already a backend dep), `dotenv` npm package (new frontend dep).

**Source spec:** `docs/superpowers/specs/2026-04-28-worktree-parallel-dev-isolation-design.md`

---

## File Structure

| File | Purpose | Action |
|---|---|---|
| `scripts/worktree-env` | Main allocation script (Python, executable) | Create |
| `scripts/new-worktree` | Bash wrapper for `git worktree add` + `init` | Create |
| `backend/tests/test_worktree_env.py` | Unit tests for slug / allocation / registry | Create |
| `backend/cubeplex/config.py` | Add 5-line dotenv preamble | Modify (line 26) |
| `frontend/package.json` | Add `dotenv` devDependency | Modify |
| `frontend/packages/web/package.json` | Add `dotenv` devDependency | Modify |
| `frontend/packages/web/next.config.ts` | Add 4-line dotenv preamble | Modify (top) |
| `frontend/playwright.config.ts` | Add 4-line dotenv preamble | Modify (top) |
| `backend/tests/e2e/conftest.py` | Replace `FLUSHDB` with prefix-scoped scan-delete | Modify (~line 60–72) |
| `AGENTS.md` | Add "Worktrees and parallel dev" section | Modify |

The script is a single file (~400 lines) with clearly sectioned helpers (slug, Registry class, allocation, subcommand functions, argparse main). Splitting into a package would force `./scripts/worktree-env` to be a wrapper with `python -m` plumbing — not worth it.

---

## Task 1: Bootstrap directories and frontend dotenv dep

**Files:**
- Create: `scripts/` (directory)
- Modify: `frontend/package.json`
- Modify: `frontend/packages/web/package.json`

- [ ] **Step 1: Create `scripts/` directory**

```bash
mkdir -p scripts
```

- [ ] **Step 2: Add `dotenv` to `frontend/package.json` devDependencies**

Run from `frontend/`:

```bash
cd frontend && pnpm add -D -w dotenv
```

Verify it shows up under `devDependencies` in `frontend/package.json` with a version like `^16.x.x`.

- [ ] **Step 3: Add `dotenv` to `frontend/packages/web/package.json`**

```bash
cd frontend && pnpm --filter web add -D dotenv
```

Verify `frontend/packages/web/package.json` now has `"dotenv"` under `devDependencies`.

- [ ] **Step 4: Commit**

```bash
git add scripts frontend/package.json frontend/packages/web/package.json frontend/pnpm-lock.yaml
git commit -m "chore(worktree): scaffold scripts/ dir and add dotenv frontend dep"
```

---

## Task 2: Slug helper with tests

**Files:**
- Create: `scripts/worktree-env`
- Create: `backend/tests/test_worktree_env.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_worktree_env.py`:

```python
"""Unit tests for scripts/worktree-env helper module."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "worktree-env"


def _load_script():
    spec = importlib.util.spec_from_file_location("worktree_env", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def we():
    return _load_script()


class TestSlugify:
    def test_simple_branch(self, we):
        assert we.slugify("feat/m7-file-upload") == "feat-m7-file-upload"

    def test_lowercases(self, we):
        assert we.slugify("Feat/FooBar") == "feat-foobar"

    def test_replaces_dot_underscore_slash(self, we):
        assert we.slugify("foo/bar.baz_qux") == "foo-bar-baz-qux"

    def test_collapses_repeats(self, we):
        assert we.slugify("a___b") == "a-b"

    def test_strips_leading_trailing(self, we):
        assert we.slugify("/foo/") == "foo"

    def test_main_passthrough(self, we):
        assert we.slugify("main") == "main"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_worktree_env.py -v`

Expected: FAIL with `FileNotFoundError` or `AssertionError` on the import (script doesn't exist yet).

- [ ] **Step 3: Create the script with shebang and slugify**

Create `scripts/worktree-env`:

```python
#!/usr/bin/env python3
"""worktree-env — allocate ports, MySQL schemas, and Redis prefixes per
git worktree so multiple worktrees can run dev servers and E2E in parallel.

See docs/superpowers/specs/2026-04-28-worktree-parallel-dev-isolation-design.md
"""

from __future__ import annotations

import re

# ------------------------------------------------------------------ slug

_SLUG_PUNCT = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Normalize a branch name into a slug usable in DNS, DB, Redis."""
    lowered = name.lower()
    replaced = _SLUG_PUNCT.sub("-", lowered)
    return replaced.strip("-")
```

- [ ] **Step 4: Make it executable**

```bash
chmod +x scripts/worktree-env
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd backend && uv run pytest tests/test_worktree_env.py -v`

Expected: all 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/worktree-env backend/tests/test_worktree_env.py
git commit -m "feat(worktree): add slugify helper with tests"
```

---

## Task 3: Registry I/O with atomic write

**Files:**
- Modify: `scripts/worktree-env`
- Modify: `backend/tests/test_worktree_env.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_worktree_env.py`:

```python
class TestRegistry:
    def test_load_missing_returns_empty(self, we, tmp_path):
        path = tmp_path / "registry.json"
        reg = we.Registry.load(path)
        assert reg.entries == {}

    def test_save_then_load_roundtrip(self, we, tmp_path):
        path = tmp_path / "registry.json"
        reg = we.Registry.load(path)
        reg.entries["feat-foo"] = {
            "offset": 7,
            "branch": "feat/foo",
            "path": "/x/y",
            "created_at": "2026-04-28T00:00:00Z",
        }
        reg.save()
        again = we.Registry.load(path)
        assert again.entries == reg.entries

    def test_save_is_atomic(self, we, tmp_path):
        path = tmp_path / "registry.json"
        reg = we.Registry.load(path)
        reg.entries["a"] = {"offset": 1, "branch": "a", "path": "/p", "created_at": "t"}
        reg.save()
        # No leftover .tmp file
        assert not (tmp_path / "registry.json.tmp").exists()
        assert path.exists()

    def test_load_corrupt_raises(self, we, tmp_path):
        path = tmp_path / "registry.json"
        path.write_text("not json")
        with pytest.raises(ValueError):
            we.Registry.load(path)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd backend && uv run pytest tests/test_worktree_env.py::TestRegistry -v`

Expected: FAIL with `AttributeError: module 'worktree_env' has no attribute 'Registry'`.

- [ ] **Step 3: Implement `Registry` class**

Append to `scripts/worktree-env` (after slugify):

```python
# ---------------------------------------------------------------- registry

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Registry:
    """Per-main-repo allocation registry.

    Stored at <main_repo>/.worktrees/registry.json. Maps slug -> entry dict
    with keys: offset, branch, path, created_at.
    """

    path: Path
    entries: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Registry":
        if not path.exists():
            return cls(path=path, entries={})
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise ValueError(f"registry at {path} is not valid JSON: {e}") from e
        if not isinstance(data, dict):
            raise ValueError(f"registry at {path} must be an object, got {type(data).__name__}")
        return cls(path=path, entries=data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.entries, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, self.path)
```

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/test_worktree_env.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/worktree-env backend/tests/test_worktree_env.py
git commit -m "feat(worktree): add Registry with atomic save"
```

---

## Task 4: Slot allocation algorithm with tests

**Files:**
- Modify: `scripts/worktree-env`
- Modify: `backend/tests/test_worktree_env.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_worktree_env.py`:

```python
class TestAllocate:
    def test_main_special_case_returns_zero(self, we, tmp_path):
        reg = we.Registry(path=tmp_path / "r.json")
        offset = we.allocate_offset(slug="main", registry=reg, is_main_worktree=True)
        assert offset == 0

    def test_deterministic_from_slug(self, we, tmp_path):
        reg = we.Registry(path=tmp_path / "r.json")
        offset1 = we.allocate_offset(slug="feat-foo", registry=reg, is_main_worktree=False)
        # Same slug, fresh registry → same offset
        reg2 = we.Registry(path=tmp_path / "r2.json")
        offset2 = we.allocate_offset(slug="feat-foo", registry=reg2, is_main_worktree=False)
        assert offset1 == offset2
        assert 0 < offset1 < 100  # never 0 for non-main

    def test_returns_existing_entry(self, we, tmp_path):
        reg = we.Registry(path=tmp_path / "r.json")
        reg.entries["feat-foo"] = {
            "offset": 42,
            "branch": "feat/foo",
            "path": "/p",
            "created_at": "t",
        }
        offset = we.allocate_offset(slug="feat-foo", registry=reg, is_main_worktree=False)
        assert offset == 42

    def test_collision_resolves_to_next_free(self, we, tmp_path):
        reg = we.Registry(path=tmp_path / "r.json")
        # Pre-fill the slot that "feat-foo" would naturally hash to
        natural = we._hash_slot("feat-foo")
        reg.entries["other-slug"] = {
            "offset": natural,
            "branch": "x",
            "path": "/y",
            "created_at": "t",
        }
        offset = we.allocate_offset(slug="feat-foo", registry=reg, is_main_worktree=False)
        assert offset == (natural + 1) % 100
        assert offset != natural

    def test_full_registry_raises(self, we, tmp_path):
        reg = we.Registry(path=tmp_path / "r.json")
        for i in range(100):
            reg.entries[f"s{i}"] = {"offset": i, "branch": "x", "path": "/y", "created_at": "t"}
        with pytest.raises(RuntimeError, match="all 100 slots taken"):
            we.allocate_offset(slug="another", registry=reg, is_main_worktree=False)

    def test_non_main_skips_zero(self, we, tmp_path):
        # Even if a non-main slug hashes to 0, it must be bumped — slot 0 is reserved
        reg = we.Registry(path=tmp_path / "r.json")
        # Find a slug that hashes to 0, simulate by pre-populating non-zero slots
        # and using the public path: just pre-fill 0 with main
        reg.entries["main"] = {"offset": 0, "branch": "main", "path": "/m", "created_at": "t"}
        offset = we.allocate_offset(slug="feat-foo", registry=reg, is_main_worktree=False)
        assert offset != 0
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd backend && uv run pytest tests/test_worktree_env.py::TestAllocate -v`

Expected: FAIL with `AttributeError: ... 'allocate_offset'`.

- [ ] **Step 3: Implement allocation**

Append to `scripts/worktree-env`:

```python
# -------------------------------------------------------------- allocation

import hashlib
from datetime import datetime, timezone

SLOT_RANGE = 100  # offsets 0..99


def _hash_slot(slug: str) -> int:
    """Stable hash of slug into 0..SLOT_RANGE-1."""
    digest = hashlib.sha1(slug.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % SLOT_RANGE


def allocate_offset(
    slug: str,
    registry: Registry,
    is_main_worktree: bool,
) -> int:
    """Return offset for slug, allocating a new slot if needed.

    - Main worktree always gets 0.
    - Existing slug returns its existing offset (idempotent).
    - New slug: hash to a starting slot, then linear-probe for the first
      free slot. Slot 0 is reserved for main.
    """
    if is_main_worktree:
        return 0

    if slug in registry.entries:
        return int(registry.entries[slug]["offset"])

    used = {int(e["offset"]) for e in registry.entries.values()}
    initial = _hash_slot(slug)
    if initial == 0:
        initial = 1  # never auto-pick reserved main slot

    slot = initial
    for _ in range(SLOT_RANGE):
        if slot != 0 and slot not in used:
            return slot
        slot = (slot + 1) % SLOT_RANGE
        if slot == 0:
            slot = 1

    raise RuntimeError(
        "all 100 slots taken; run `worktree-env clean-orphans` to reclaim"
    )


def record_allocation(
    registry: Registry,
    slug: str,
    branch: str,
    offset: int,
    worktree_path: Path,
) -> None:
    registry.entries[slug] = {
        "offset": offset,
        "branch": branch,
        "path": str(worktree_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    registry.save()
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd backend && uv run pytest tests/test_worktree_env.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/worktree-env backend/tests/test_worktree_env.py
git commit -m "feat(worktree): add slot allocation with collision probe"
```

---

## Task 5: Worktree discovery helpers

**Files:**
- Modify: `scripts/worktree-env`
- Modify: `backend/tests/test_worktree_env.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_worktree_env.py`:

```python
import subprocess


class TestWorktreeDiscovery:
    def test_find_main_repo_from_main(self, we, tmp_path):
        # Initialize a real git repo as the "main"
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init", "-q"],
            check=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
        )
        main = we.find_main_repo(start_dir=tmp_path)
        assert main == tmp_path.resolve()

    def test_is_main_worktree_true_in_main(self, we, tmp_path):
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init", "-q"],
            check=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
        )
        info = we.current_worktree_info(start_dir=tmp_path)
        assert info.is_main is True
        assert info.branch in ("master", "main")
```

(`os` is already imported at top of the test file as part of registry tests via `we`. Add `import os` at the top of the test file if not already imported.)

- [ ] **Step 2: Run tests to verify failure**

Run: `cd backend && uv run pytest tests/test_worktree_env.py::TestWorktreeDiscovery -v`

Expected: FAIL with `AttributeError: ... 'find_main_repo'`.

- [ ] **Step 3: Implement discovery helpers**

Append to `scripts/worktree-env`:

```python
# --------------------------------------------------------------- discovery

import subprocess
from dataclasses import dataclass


@dataclass
class WorktreeInfo:
    path: Path        # absolute path to current worktree
    main_path: Path   # absolute path to main repo
    branch: str       # current branch name
    is_main: bool


def _git(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def find_main_repo(start_dir: Path | None = None) -> Path:
    """Return the absolute path to the main repo (first entry of git worktree list)."""
    cwd = start_dir or Path.cwd()
    output = _git(["worktree", "list", "--porcelain"], cwd=cwd)
    for line in output.splitlines():
        if line.startswith("worktree "):
            return Path(line.removeprefix("worktree ")).resolve()
    raise RuntimeError(f"no worktrees found from {cwd}")


def current_worktree_info(start_dir: Path | None = None) -> WorktreeInfo:
    """Inspect the current working directory's worktree."""
    cwd = (start_dir or Path.cwd()).resolve()
    main = find_main_repo(cwd)
    here = Path(_git(["rev-parse", "--show-toplevel"], cwd=cwd)).resolve()
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    return WorktreeInfo(
        path=here,
        main_path=main,
        branch=branch,
        is_main=(here == main),
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd backend && uv run pytest tests/test_worktree_env.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/worktree-env backend/tests/test_worktree_env.py
git commit -m "feat(worktree): add main-repo and worktree discovery helpers"
```

---

## Task 6: Argparse skeleton with stub subcommands

**Files:**
- Modify: `scripts/worktree-env`

- [ ] **Step 1: Append argparse main and subcommand stubs**

Append to `scripts/worktree-env`:

```python
# ---------------------------------------------------------- env file write

ENV_FILE_NAME = ".worktree.env"


def env_file_path(worktree_root: Path) -> Path:
    return worktree_root / ENV_FILE_NAME


def db_dev_schema(slug: str) -> str:
    return f"cubeplex_{slug.replace('-', '_')}"


def db_test_schema(slug: str) -> str:
    return f"cubeplex_test_{slug.replace('-', '_')}"


def redis_prefix(slug: str) -> str:
    return f"cubeplex-{slug}"


def write_worktree_env(
    worktree_root: Path,
    slug: str,
    offset: int,
) -> None:
    """Write .worktree.env to worktree root with all derived values."""
    fe = 3000 + offset
    be = 8000 + offset
    content = (
        f"# Worktree: {slug} (slot {offset})\n"
        f"# Auto-generated by scripts/worktree-env init. Do not edit by hand.\n"
        f"CUBEPLEX_WORKTREE_NAME={slug}\n"
        f"CUBEPLEX_WORKTREE_SLOT={offset}\n"
        f"\n"
        f"# Backend\n"
        f"CUBEPLEX_API__HOST=127.0.0.1\n"
        f"CUBEPLEX_API__PORT={be}\n"
        f"CUBEPLEX_DATABASE__NAME={db_dev_schema(slug)}\n"
        f"CUBEPLEX_REDIS__KEY_PREFIX={redis_prefix(slug)}\n"
        f"\n"
        f"# Frontend (Next dev / SSR rewrite / Playwright)\n"
        f"CUBEPLEX_API_URL=http://localhost:{be}\n"
        f"PORT={fe}\n"
        f"BASE_URL=http://localhost:{fe}\n"
    )
    env_file_path(worktree_root).write_text(content)


# --------------------------------------------------------------- subcommands

def cmd_init(args) -> int:
    raise NotImplementedError("cmd_init in next task")


def cmd_show(args) -> int:
    raise NotImplementedError("cmd_show in later task")


def cmd_destroy(args) -> int:
    raise NotImplementedError("cmd_destroy in later task")


def cmd_doctor(args) -> int:
    raise NotImplementedError("cmd_doctor in later task")


def cmd_clean_orphans(args) -> int:
    raise NotImplementedError("cmd_clean_orphans in later task")


# --------------------------------------------------------------------- main

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="worktree-env",
        description="Allocate ports / DB schemas / Redis prefix per worktree.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="Allocate slot and provision the current worktree")
    sub.add_parser("show", help="Print current worktree's allocations")
    sub.add_parser("doctor", help="Verify schema, ports, services")
    sub.add_parser("destroy", help="Drop schemas, clear Redis prefix, remove .worktree.env")
    sub.add_parser("clean-orphans", help="Reclaim slots from removed worktrees")

    args = parser.parse_args(argv)
    handlers = {
        "init": cmd_init,
        "show": cmd_show,
        "doctor": cmd_doctor,
        "destroy": cmd_destroy,
        "clean-orphans": cmd_clean_orphans,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verify script parses**

```bash
./scripts/worktree-env --help
```

Expected: prints help with 5 subcommands. Exit 0.

```bash
./scripts/worktree-env show
```

Expected: traceback with `NotImplementedError: cmd_show in later task`. (We're filling these in.)

- [ ] **Step 3: Make sure unit tests still pass**

Run: `cd backend && uv run pytest tests/test_worktree_env.py -v`

Expected: all tests PASS (no regressions).

- [ ] **Step 4: Commit**

```bash
git add scripts/worktree-env
git commit -m "feat(worktree): scaffold argparse + .worktree.env writer"
```

---

## Task 7: `show` subcommand

**Files:**
- Modify: `scripts/worktree-env`

- [ ] **Step 1: Replace `cmd_show` stub**

In `scripts/worktree-env`, replace `cmd_show`:

```python
def cmd_show(args) -> int:
    info = current_worktree_info()
    env_file = env_file_path(info.path)
    if not env_file.exists():
        print(f"No {ENV_FILE_NAME} at {info.path}.", file=sys.stderr)
        print("Run `./scripts/worktree-env init` from this worktree to allocate.", file=sys.stderr)
        return 1
    print(f"# Worktree: {info.path}")
    print(f"# Branch:   {info.branch}")
    print(f"# Main:     {info.main_path}")
    print(f"# is_main:  {info.is_main}")
    print()
    print(env_file.read_text(), end="")
    return 0
```

- [ ] **Step 2: Smoke test (optional, no .worktree.env yet)**

```bash
./scripts/worktree-env show
```

Expected: prints `No .worktree.env at <main repo path>` on stderr, exits 1.

- [ ] **Step 3: Commit**

```bash
git add scripts/worktree-env
git commit -m "feat(worktree): implement show subcommand"
```

---

## Task 8: `init` subcommand — allocation + .worktree.env (no MySQL yet)

**Files:**
- Modify: `scripts/worktree-env`

- [ ] **Step 1: Replace `cmd_init` stub**

In `scripts/worktree-env`, replace `cmd_init`:

```python
def cmd_init(args) -> int:
    info = current_worktree_info()
    slug = slugify(info.branch)

    registry_path = info.main_path / ".worktrees" / "registry.json"
    registry = Registry.load(registry_path)
    offset = allocate_offset(slug=slug, registry=registry, is_main_worktree=info.is_main)
    record_allocation(
        registry=registry,
        slug=slug,
        branch=info.branch,
        offset=offset,
        worktree_path=info.path,
    )

    print(f"→ slug={slug} slot={offset} branch={info.branch}")

    # 1. Copy backend secrets from main if missing
    _copy_if_missing(
        info.main_path / "backend" / ".env",
        info.path / "backend" / ".env",
    )
    _copy_if_missing(
        info.main_path / "backend" / "config.development.local.yaml",
        info.path / "backend" / "config.development.local.yaml",
    )

    # 2. Write .worktree.env BEFORE alembic so config picks up the new schema name
    write_worktree_env(info.path, slug, offset)
    print(f"→ wrote {env_file_path(info.path)}")

    # 3. (MySQL + alembic added in next task)
    # 4. Show summary
    return cmd_show(args)


def _copy_if_missing(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    if not src.exists():
        print(f"⚠ missing source {src}; skipping copy", file=sys.stderr)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    print(f"→ copied {src.name} from main")
```

- [ ] **Step 2: Smoke test in the main repo**

```bash
./scripts/worktree-env init
```

Expected:
- Prints `→ slug=main slot=0 branch=main`
- Writes `/home/chris/cubeplex/.worktree.env` with `PORT=3000`, `CUBEPLEX_API__PORT=8000`, `CUBEPLEX_DATABASE__NAME=cubeplex_main`, etc.
- Updates `.worktrees/registry.json`
- Final `show` output prints the file
- Exit 0

- [ ] **Step 3: Verify idempotency**

Run again:

```bash
./scripts/worktree-env init
```

Expected: identical output. registry.json unchanged in offset; `created_at` may refresh.

- [ ] **Step 4: Clean up the smoke test artifact**

```bash
rm /home/chris/cubeplex/.worktree.env
```

(We'll regenerate at end-to-end smoke test in Task 19.)

Note: leaving the registry entry in `registry.json` is fine.

- [ ] **Step 5: Commit**

```bash
git add scripts/worktree-env
git commit -m "feat(worktree): init writes registry, .worktree.env, copies backend secrets"
```

---

## Task 9: `init` subcommand — MySQL schema + alembic upgrade

**Files:**
- Modify: `scripts/worktree-env`

- [ ] **Step 1: Add MySQL provisioning helpers**

In `scripts/worktree-env`, add **before** `cmd_init`:

```python
# ----------------------------------------------------------- mysql + alembic

def _mysql_creds_from_env() -> dict[str, str]:
    """Read DB host/port/user/password from process env after .worktree.env load.

    We re-read from os.environ because `init` already loaded .worktree.env
    earlier in this run; we want CUBEPLEX_DATABASE__HOST etc. that come from
    backend/.env (the secrets file).
    """
    return {
        "host": os.environ.get("CUBEPLEX_DATABASE__HOST", "127.0.0.1"),
        "port": os.environ.get("CUBEPLEX_DATABASE__PORT", "3306"),
        "user": os.environ.get("CUBEPLEX_DATABASE__USER", "root"),
        "password": os.environ.get("CUBEPLEX_DATABASE__PASSWORD", ""),
    }


def _mysql_exec(sql: str) -> None:
    """Run a single SQL statement via the `mysql` CLI."""
    creds = _mysql_creds_from_env()
    cmd = [
        "mysql",
        f"--host={creds['host']}",
        f"--port={creds['port']}",
        f"--user={creds['user']}",
        f"--password={creds['password']}",
        "-e",
        sql,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"mysql command failed (exit {result.returncode}):\n"
            f"  cmd: {' '.join(cmd[:-2])} -e <hidden>\n"
            f"  stderr: {result.stderr}"
        )


def _create_schemas(slug: str) -> None:
    dev = db_dev_schema(slug)
    test = db_test_schema(slug)
    _mysql_exec(f"CREATE DATABASE IF NOT EXISTS `{dev}` CHARACTER SET utf8mb4;")
    _mysql_exec(f"CREATE DATABASE IF NOT EXISTS `{test}` CHARACTER SET utf8mb4;")
    print(f"→ ensured MySQL schemas: {dev}, {test}")


def _drop_schemas(slug: str) -> None:
    dev = db_dev_schema(slug)
    test = db_test_schema(slug)
    _mysql_exec(f"DROP DATABASE IF EXISTS `{dev}`;")
    _mysql_exec(f"DROP DATABASE IF EXISTS `{test}`;")
    print(f"→ dropped MySQL schemas: {dev}, {test}")


def _alembic_upgrade(worktree_path: Path) -> None:
    """Run `alembic upgrade head` in the worktree's backend dir.

    The subprocess inherits this process's os.environ, which already has
    .worktree.env loaded — so alembic targets the worktree's dev schema.
    """
    backend = worktree_path / "backend"
    print(f"→ running alembic upgrade head in {backend}")
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=backend,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head failed (exit {result.returncode}). "
            "Schema is left as-is per design (no rollback)."
        )
```

- [ ] **Step 2: Wire MySQL + alembic into `cmd_init`**

Modify `cmd_init` — replace the comment `# 3. (MySQL + alembic added in next task)` with actual calls. The full updated `cmd_init`:

```python
def cmd_init(args) -> int:
    info = current_worktree_info()
    slug = slugify(info.branch)

    registry_path = info.main_path / ".worktrees" / "registry.json"
    registry = Registry.load(registry_path)
    offset = allocate_offset(slug=slug, registry=registry, is_main_worktree=info.is_main)
    record_allocation(
        registry=registry,
        slug=slug,
        branch=info.branch,
        offset=offset,
        worktree_path=info.path,
    )

    print(f"→ slug={slug} slot={offset} branch={info.branch}")

    _copy_if_missing(
        info.main_path / "backend" / ".env",
        info.path / "backend" / ".env",
    )
    _copy_if_missing(
        info.main_path / "backend" / "config.development.local.yaml",
        info.path / "backend" / "config.development.local.yaml",
    )

    write_worktree_env(info.path, slug, offset)
    print(f"→ wrote {env_file_path(info.path)}")

    # Load .worktree.env into our own os.environ so alembic subprocess inherits
    # the worktree's CUBEPLEX_DATABASE__NAME. Backend's config.py will reload it
    # again at import time inside the subprocess — same values, idempotent.
    _load_dotenv_into_environ(env_file_path(info.path))

    _create_schemas(slug)
    _alembic_upgrade(info.path)

    return cmd_show(args)


def _load_dotenv_into_environ(path: Path) -> None:
    """Minimal dotenv loader (stdlib only) — set keys not already present."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())
```

- [ ] **Step 3: Smoke test (use main repo)**

```bash
./scripts/worktree-env init
```

Expected:
- Allocates slot 0 for main
- Writes `.worktree.env`
- Loads it (sets `CUBEPLEX_DATABASE__NAME=cubeplex_main` in the script's env)
- Creates `cubeplex_main` and `cubeplex_test_main` schemas in MySQL
- Runs `alembic upgrade head` against `cubeplex_main` — should succeed and apply all migrations
- Prints final `show` summary

Verify the schemas exist:

```bash
mysql -h "$(grep CUBEPLEX_DATABASE__HOST backend/.env | cut -d= -f2)" \
      -u "$(grep CUBEPLEX_DATABASE__USER backend/.env | cut -d= -f2)" \
      -p"$(grep CUBEPLEX_DATABASE__PASSWORD backend/.env | cut -d= -f2)" \
      -P "$(grep CUBEPLEX_DATABASE__PORT backend/.env | cut -d= -f2)" \
      -e "SHOW DATABASES LIKE 'cubeplex_%main%';"
```

Expected: lists `cubeplex_main` and `cubeplex_test_main`.

- [ ] **Step 4: Idempotency check**

```bash
./scripts/worktree-env init
```

Expected: succeeds again — `CREATE IF NOT EXISTS` is no-op, alembic head is no-op, `.worktree.env` rewritten with same values.

- [ ] **Step 5: Commit**

```bash
git add scripts/worktree-env
git commit -m "feat(worktree): init creates MySQL schemas and runs alembic upgrade"
```

---

## Task 10: `destroy` subcommand

**Files:**
- Modify: `scripts/worktree-env`

- [ ] **Step 1: Implement Redis prefix delete + `cmd_destroy`**

In `scripts/worktree-env`, add helper before subcommand block, and replace `cmd_destroy`:

```python
def _redis_delete_prefix(prefix: str) -> int:
    """Delete every key matching `<prefix>:*` from the configured Redis URL.

    Returns the number of keys deleted. Uses redis-cli with --scan + xargs DEL.
    """
    redis_url = os.environ.get("CUBEPLEX_REDIS__URL") or _read_redis_url_from_env_file()
    if not redis_url:
        print("⚠ no CUBEPLEX_REDIS__URL set; skipping Redis cleanup", file=sys.stderr)
        return 0

    pattern = f"{prefix}:*"
    # SCAN keys
    scan = subprocess.run(
        ["redis-cli", "-u", redis_url, "--scan", "--pattern", pattern],
        capture_output=True,
        text=True,
        check=True,
    )
    keys = [k for k in scan.stdout.splitlines() if k]
    if not keys:
        return 0
    # DEL in chunks
    deleted = 0
    chunk_size = 500
    for i in range(0, len(keys), chunk_size):
        chunk = keys[i:i + chunk_size]
        result = subprocess.run(
            ["redis-cli", "-u", redis_url, "DEL", *chunk],
            capture_output=True,
            text=True,
            check=True,
        )
        deleted += int(result.stdout.strip() or 0)
    return deleted


def _read_redis_url_from_env_file() -> str | None:
    """Fallback: read CUBEPLEX_REDIS__URL from backend/.env if not in os.environ."""
    info = current_worktree_info()
    env_file = info.path / "backend" / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        if line.startswith("CUBEPLEX_REDIS__URL="):
            return line.split("=", 1)[1].strip()
    return None


def cmd_destroy(args) -> int:
    info = current_worktree_info()
    if info.is_main:
        print("Refusing to destroy main worktree allocation.", file=sys.stderr)
        return 1

    slug = slugify(info.branch)
    registry = Registry.load(info.main_path / ".worktrees" / "registry.json")

    # Load env so DB creds are in os.environ
    _load_dotenv_into_environ(info.path / "backend" / ".env")
    _load_dotenv_into_environ(env_file_path(info.path))

    _drop_schemas(slug)

    deleted = _redis_delete_prefix(redis_prefix(slug))
    print(f"→ deleted {deleted} Redis keys under prefix {redis_prefix(slug)}:*")

    if slug in registry.entries:
        del registry.entries[slug]
        registry.save()
        print(f"→ removed {slug} from registry")

    env_file = env_file_path(info.path)
    if env_file.exists():
        env_file.unlink()
        print(f"→ removed {env_file}")

    print(
        "Done. Run `git worktree remove <path>` separately to delete the worktree filesystem."
    )
    return 0
```

- [ ] **Step 2: No smoke test yet** — destroy is destructive and we don't have a throwaway worktree. Will be exercised in Task 19.

- [ ] **Step 3: Commit**

```bash
git add scripts/worktree-env
git commit -m "feat(worktree): destroy drops schemas, clears redis prefix, prunes registry"
```

---

## Task 11: `doctor` subcommand

**Files:**
- Modify: `scripts/worktree-env`

- [ ] **Step 1: Replace `cmd_doctor` stub**

```python
def cmd_doctor(args) -> int:
    info = current_worktree_info()
    slug = slugify(info.branch)
    issues: list[str] = []

    # 1. .worktree.env exists
    env_file = env_file_path(info.path)
    if not env_file.exists():
        issues.append(f"missing {env_file} — run `worktree-env init`")
        # Print and bail; rest of checks need the env file
        for issue in issues:
            print(f"✗ {issue}", file=sys.stderr)
        return 1
    print(f"✓ {env_file} present")

    # Load env so we can probe MySQL / Redis with current creds
    _load_dotenv_into_environ(info.path / "backend" / ".env")
    _load_dotenv_into_environ(env_file)

    # 2. Registry entry consistent
    registry = Registry.load(info.main_path / ".worktrees" / "registry.json")
    entry = registry.entries.get(slug)
    if entry is None:
        issues.append(f"slug {slug} missing from registry")
    elif Path(entry["path"]).resolve() != info.path:
        issues.append(
            f"registry path drift: registry={entry['path']} actual={info.path}"
        )
    else:
        print(f"✓ registry entry slot={entry['offset']}")

    # 3. MySQL schemas exist
    try:
        _mysql_exec(f"USE `{db_dev_schema(slug)}`;")
        print(f"✓ MySQL schema {db_dev_schema(slug)} reachable")
    except RuntimeError as e:
        issues.append(f"dev schema unusable: {e}")
    try:
        _mysql_exec(f"USE `{db_test_schema(slug)}`;")
        print(f"✓ MySQL schema {db_test_schema(slug)} reachable")
    except RuntimeError as e:
        issues.append(f"test schema unusable: {e}")

    # 4. Redis reachable
    redis_url = os.environ.get("CUBEPLEX_REDIS__URL")
    if redis_url:
        result = subprocess.run(
            ["redis-cli", "-u", redis_url, "PING"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip() == "PONG":
            print("✓ Redis reachable")
        else:
            issues.append(f"Redis not reachable: {result.stderr or result.stdout}")
    else:
        issues.append("CUBEPLEX_REDIS__URL not set")

    # 5. Alembic head matches latest migration
    backend = info.path / "backend"
    cur = subprocess.run(
        ["uv", "run", "alembic", "current"],
        cwd=backend,
        capture_output=True,
        text=True,
    )
    head = subprocess.run(
        ["uv", "run", "alembic", "heads"],
        cwd=backend,
        capture_output=True,
        text=True,
    )
    if cur.returncode == 0 and head.returncode == 0:
        cur_rev = (cur.stdout.strip().split() or [""])[0]
        head_rev = (head.stdout.strip().split() or [""])[0]
        if cur_rev and cur_rev == head_rev:
            print(f"✓ alembic at head ({head_rev})")
        else:
            issues.append(f"alembic not at head: current={cur_rev} head={head_rev}")
    else:
        issues.append("alembic check failed")

    if issues:
        print()
        for issue in issues:
            print(f"✗ {issue}", file=sys.stderr)
        return 1
    return 0
```

- [ ] **Step 2: Smoke test against main worktree**

```bash
./scripts/worktree-env doctor
```

Expected: all `✓` lines, exit 0. (Assumes you ran `init` for main earlier in Task 9.)

- [ ] **Step 3: Commit**

```bash
git add scripts/worktree-env
git commit -m "feat(worktree): doctor checks env file, registry, MySQL, Redis, alembic"
```

---

## Task 12: `clean-orphans` subcommand

**Files:**
- Modify: `scripts/worktree-env`

- [ ] **Step 1: Replace `cmd_clean_orphans` stub**

```python
def cmd_clean_orphans(args) -> int:
    info = current_worktree_info()

    # 1. Find live worktree paths according to git
    live_paths: set[Path] = set()
    output = _git(["worktree", "list", "--porcelain"], cwd=info.main_path)
    for line in output.splitlines():
        if line.startswith("worktree "):
            live_paths.add(Path(line.removeprefix("worktree ")).resolve())

    # 2. Orphan registry entries
    registry = Registry.load(info.main_path / ".worktrees" / "registry.json")
    orphan_slugs: list[str] = []
    for slug, entry in registry.entries.items():
        entry_path = Path(entry["path"]).resolve()
        if entry_path not in live_paths:
            orphan_slugs.append(slug)

    # 3. Orphan MySQL schemas (cubeplex_* not referenced by any live entry)
    referenced_dev = {db_dev_schema(s) for s in registry.entries if s not in orphan_slugs}
    referenced_test = {db_test_schema(s) for s in registry.entries if s not in orphan_slugs}
    referenced = referenced_dev | referenced_test | {"cubeplex", "cubeplex_test"}

    # Load main's backend/.env for MySQL creds
    _load_dotenv_into_environ(info.main_path / "backend" / ".env")

    creds = _mysql_creds_from_env()
    list_result = subprocess.run(
        [
            "mysql",
            f"--host={creds['host']}",
            f"--port={creds['port']}",
            f"--user={creds['user']}",
            f"--password={creds['password']}",
            "--batch",
            "--skip-column-names",
            "-e",
            "SHOW DATABASES LIKE 'cubeplex_%';",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    all_schemas = {s.strip() for s in list_result.stdout.splitlines() if s.strip()}
    orphan_schemas = sorted(all_schemas - referenced)

    if not orphan_slugs and not orphan_schemas:
        print("No orphans found.")
        return 0

    print("The following orphans will be cleaned:")
    for slug in orphan_slugs:
        print(f"  registry: {slug} -> {registry.entries[slug]['path']}")
    for schema in orphan_schemas:
        print(f"  schema:   {schema}")
    print()

    answer = input("Proceed? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted.")
        return 1

    for slug in orphan_slugs:
        del registry.entries[slug]
    registry.save()

    for schema in orphan_schemas:
        _mysql_exec(f"DROP DATABASE IF EXISTS `{schema}`;")

    print(f"→ pruned {len(orphan_slugs)} registry entries, dropped {len(orphan_schemas)} schemas")
    return 0
```

- [ ] **Step 2: Smoke test**

```bash
./scripts/worktree-env clean-orphans
```

Expected: prints `No orphans found.` (assuming all worktrees in registry are live). Exit 0.

- [ ] **Step 3: Commit**

```bash
git add scripts/worktree-env
git commit -m "feat(worktree): clean-orphans reclaims slots and drops residual schemas"
```

---

## Task 13: `new-worktree` wrapper

**Files:**
- Create: `scripts/new-worktree`

- [ ] **Step 1: Create the wrapper**

Create `scripts/new-worktree`:

```bash
#!/usr/bin/env bash
# Create a new worktree branched from latest origin/main and run `worktree-env init`.
#
# Usage:  scripts/new-worktree <branch-name> [extra git worktree add args...]
# Example: scripts/new-worktree feat/some-thing
#
# The branch is created from latest origin/main. Worktree path is
# .worktrees/<branch-name>. Extra args are passed to `git worktree add`.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <branch-name> [extra git worktree add args...]" >&2
  exit 2
fi

BRANCH="$1"
shift || true

REPO_ROOT="$(git rev-parse --show-toplevel)"
WORKTREE_PATH=".worktrees/${BRANCH}"

if [[ -e "${REPO_ROOT}/${WORKTREE_PATH}" ]]; then
  echo "Path ${REPO_ROOT}/${WORKTREE_PATH} already exists." >&2
  exit 1
fi

cd "${REPO_ROOT}"

echo "→ fetching origin/main..."
git fetch origin main

echo "→ creating worktree at ${WORKTREE_PATH} from origin/main..."
git worktree add "${WORKTREE_PATH}" -b "${BRANCH}" origin/main "$@"

echo "→ running worktree-env init..."
cd "${WORKTREE_PATH}"
"${REPO_ROOT}/scripts/worktree-env" init

echo
echo "✓ worktree ready at ${REPO_ROOT}/${WORKTREE_PATH}"
echo "  cd ${REPO_ROOT}/${WORKTREE_PATH}"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/new-worktree
```

- [ ] **Step 3: Verify --help-style invocation**

```bash
./scripts/new-worktree
```

Expected: prints usage to stderr, exit 2.

- [ ] **Step 4: Commit**

```bash
git add scripts/new-worktree
git commit -m "feat(worktree): add new-worktree wrapper that branches from origin/main"
```

---

## Task 14: Backend config — load `.worktree.env`

**Files:**
- Modify: `backend/cubeplex/config.py`

- [ ] **Step 1: Insert dotenv preamble**

In `backend/cubeplex/config.py`, find lines 26–31:

```python
config = dynaconf.Dynaconf(
    environments=True,
    dotenv_path=str(backend_dir / ".env"),
    envvar_prefix="CUBEPLEX",
    settings_files=settings_files,
    load_dotenv=True,
)
```

Insert **before** `config = dynaconf.Dynaconf(...)`:

```python
# Load worktree-specific allocations (ports, DB schema, Redis prefix) from
# .worktree.env at the worktree root, BEFORE dynaconf reads. override=False
# means real shell exports still win. See
# docs/superpowers/specs/2026-04-28-worktree-parallel-dev-isolation-design.md
from dotenv import load_dotenv as _load_worktree_dotenv

_worktree_env_path = backend_dir.parent / ".worktree.env"
if _worktree_env_path.exists():
    _load_worktree_dotenv(_worktree_env_path, override=False)
```

- [ ] **Step 2: Verify config loads cleanly**

```bash
cd backend && uv run python -c "from cubeplex.config import config; print(config.api.port, config.database.name, config.redis.key_prefix)"
```

Expected (depends on whether you have a `.worktree.env` in repo root from earlier smoke tests):
- If `.worktree.env` exists with `CUBEPLEX_API__PORT=8000 CUBEPLEX_DATABASE__NAME=cubeplex_main CUBEPLEX_REDIS__KEY_PREFIX=cubeplex-main`: prints `8000 cubeplex_main cubeplex-main`.
- If no `.worktree.env`: prints `8000 cubeplex cubeplex` (defaults).

- [ ] **Step 3: Run backend unit tests as a regression check**

```bash
cd backend && uv run pytest tests/ -m "not e2e" -x
```

Expected: all unit tests pass (no regressions).

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/config.py
git commit -m "feat(worktree): backend config loads .worktree.env before dynaconf"
```

---

## Task 15: Frontend configs — load `.worktree.env`

**Files:**
- Modify: `frontend/packages/web/next.config.ts`
- Modify: `frontend/playwright.config.ts`

- [ ] **Step 1: Read current `frontend/packages/web/next.config.ts`**

Open the file. Note the existing structure (likely starts with `import` then `export default`).

- [ ] **Step 2: Add dotenv preamble at the very top**

Insert at the top of `frontend/packages/web/next.config.ts`, before all existing imports:

```typescript
// Worktree-specific overrides (ports, DB schema, Redis prefix). Loaded before
// next.config so the rewrite rule below picks up CUBEPLEX_API_URL from
// .worktree.env when running inside a worktree. See
// docs/superpowers/specs/2026-04-28-worktree-parallel-dev-isolation-design.md
import dotenv from 'dotenv'
import path from 'path'
dotenv.config({
  path: path.resolve(__dirname, '../../../.worktree.env'),
  override: false,
})
```

- [ ] **Step 3: Add dotenv preamble to `frontend/playwright.config.ts`**

Insert at the top of `frontend/playwright.config.ts`, before all existing imports:

```typescript
// Worktree-specific overrides — see next.config.ts for rationale.
import dotenv from 'dotenv'
import path from 'path'
dotenv.config({
  path: path.resolve(__dirname, '../.worktree.env'),
  override: false,
})
```

- [ ] **Step 4: Verify type-check and build still work**

```bash
cd frontend && pnpm type-check
```

Expected: passes with no new errors.

```bash
cd frontend && pnpm --filter web build
```

Expected: builds successfully. (If you have a `.worktree.env` from earlier smoke tests, the build uses its values.)

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/next.config.ts frontend/playwright.config.ts
git commit -m "feat(worktree): next + playwright configs load .worktree.env"
```

---

## Task 16: E2E `_flush_test_redis` → prefix-scoped scan-delete

**Files:**
- Modify: `backend/tests/e2e/conftest.py`

- [ ] **Step 1: Locate `_flush_test_redis`**

Open `backend/tests/e2e/conftest.py`. Find the fixture (around line 50–73). Current body:

```python
client: Redis = Redis.from_url(
    _cubeplex_config.get("redis.url", "redis://127.0.0.1:6379/0"),
    decode_responses=True,
)
try:
    await client.flushdb()
finally:
    await client.aclose()
yield
```

- [ ] **Step 2: Replace `flushdb()` with prefix-scoped scan-delete**

Replace the `try` block with:

```python
client: Redis = Redis.from_url(
    _cubeplex_config.get("redis.url", "redis://127.0.0.1:6379/0"),
    decode_responses=True,
)
try:
    # Delete only keys belonging to this worktree's prefix so parallel E2E
    # in other worktrees isn't clobbered. Prefix matches what app.py builds
    # at startup: f"{base_prefix}:{env}".
    base_prefix = _cubeplex_config.get("redis.key_prefix", "cubeplex")
    env_name = _cubeplex_config.get("env", "test")
    pattern = f"{base_prefix}:{env_name}:*"
    deleted_keys: list[str] = []
    async for key in client.scan_iter(match=pattern, count=500):
        deleted_keys.append(key)
        if len(deleted_keys) >= 500:
            await client.delete(*deleted_keys)
            deleted_keys.clear()
    if deleted_keys:
        await client.delete(*deleted_keys)
finally:
    await client.aclose()
yield
```

- [ ] **Step 3: Update the docstring**

Replace the function's existing docstring:

```python
"""Delete this worktree's Redis keys before each e2e-marked test.

Uses a prefix-scoped SCAN + DEL instead of FLUSHDB so two worktrees can
run E2E in parallel against the same Redis without clobbering each other.
The prefix is `{redis.key_prefix}:{env}` matching what app.py builds at
startup; outside a worktree (CI) the key_prefix defaults to "cubeplex" and
env to "test", so behavior matches the previous FLUSHDB for a single
isolated runner.
"""
```

- [ ] **Step 4: Run a representative E2E test as a regression check**

```bash
cd backend && uv run pytest tests/e2e/test_auth.py -v
```

Expected: passes (or fails for a pre-existing reason — confirm against the same test on main).

- [ ] **Step 5: Commit**

```bash
git add backend/tests/e2e/conftest.py
git commit -m "fix(e2e): scope test redis cleanup by key prefix for parallel worktrees"
```

---

## Task 17: AGENTS.md — Worktree section

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Open and review existing structure**

`AGENTS.md` is symlinked from `CLAUDE.md`. Read its end to find a sensible insertion point — likely after `## Frontend essentials`.

- [ ] **Step 2: Append the worktree section**

Add the following section after `## Frontend essentials`:

```markdown
## Worktrees and parallel dev

This repo uses `git worktree` for parallel feature development. Each
worktree gets its own allocated ports, MySQL schemas, and Redis prefix to
avoid collisions when multiple worktrees run dev servers or E2E suites at
the same time. All allocations land in `<worktree_root>/.worktree.env`
(gitignored).

### Creating a new worktree

Always run from the main repo root. The wrapper branches from latest
`origin/main`:

    ./scripts/new-worktree feat/<branch-name>

This: fetches origin/main, creates the worktree, allocates a slot,
provisions MySQL schemas, runs `alembic upgrade head`, copies
`backend/.env` and `config.development.local.yaml` from main if missing,
and writes `.worktree.env`.

### Working inside a worktree

**First thing on entry: read the allocated values.**

    ./scripts/worktree-env show
    # or just: cat .worktree.env

`.worktree.env` declares values like:

    CUBEPLEX_WORKTREE_NAME=feat-m7-file-upload
    CUBEPLEX_WORKTREE_SLOT=37
    CUBEPLEX_API__PORT=8037
    CUBEPLEX_DATABASE__NAME=cubeplex_feat_m7_file_upload
    CUBEPLEX_REDIS__KEY_PREFIX=cubeplex-feat-m7-file-upload
    CUBEPLEX_API_URL=http://localhost:8037
    PORT=3037
    BASE_URL=http://localhost:3037

Backend (`backend/cubeplex/config.py`), Next (`next.config.ts`), and
Playwright (`playwright.config.ts`) all auto-load this file. So
`python main.py`, `pnpm dev`, and `pnpm test:e2e` just work with the
allocated ports — but **never assume 3000 / 8000** when checking
manually with `curl` or `lsof`.

### Other subcommands

- `./scripts/worktree-env doctor` — verify schemas exist, ports free, MySQL/Redis reachable, alembic at head
- `./scripts/worktree-env destroy` — drop schemas, clear redis prefix, delete `.worktree.env` (run **before** `git worktree remove`)
- `./scripts/worktree-env clean-orphans` — interactive cleanup of registry entries and MySQL schemas left behind by removed worktrees

### Notes for AI agents

- Subagents do not inherit `cwd`. When dispatching work into a worktree,
  pin the absolute path AND tell the agent to `cat .worktree.env` first.
- Default ports (3000 / 8000) only apply in the **main** worktree. Inside
  any other worktree they are wrong.
- CI runs in the main checkout (no `.worktree.env`); all the dotenv
  loaders no-op there, so CI behavior is unchanged.
```

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "docs(worktree): add parallel dev section to AGENTS.md"
```

---

## Task 18: Verify imports + run full test suite

**Files:**
- (None — pure verification)

- [ ] **Step 1: Run the full unit-test suite for `worktree-env`**

```bash
cd backend && uv run pytest tests/test_worktree_env.py -v
```

Expected: all tests pass.

- [ ] **Step 2: Run all backend non-E2E tests**

```bash
cd backend && uv run pytest tests/ -m "not e2e" -v
```

Expected: pass with no regressions.

- [ ] **Step 3: Run a single backend E2E test against the main schema**

```bash
cd backend && uv run pytest tests/e2e/test_auth.py -v
```

Expected: pass (parity with the same test on main; the prefix-scoped delete is functionally equivalent for a single tenant).

- [ ] **Step 4: Frontend type-check**

```bash
cd frontend && pnpm type-check
```

Expected: pass.

- [ ] **Step 5: No commit** (verification only)

---

## Task 19: End-to-end smoke test with two parallel worktrees

**Files:**
- (None — manual verification with cleanup at the end)

- [ ] **Step 1: From main repo root, create test worktree A**

```bash
./scripts/new-worktree test/wt-isolation-a
```

Expected:
- Branch `test/wt-isolation-a` created from `origin/main`
- Worktree at `.worktrees/test/wt-isolation-a`
- Slot allocated (some non-zero offset, say `K`)
- `.worktree.env` written with `PORT=300K`, `CUBEPLEX_API__PORT=800K`
- Schemas `cubeplex_test_wt_isolation_a` and `cubeplex_test_test_wt_isolation_a` created
- `alembic upgrade head` succeeded

- [ ] **Step 2: Create test worktree B**

```bash
./scripts/new-worktree test/wt-isolation-b
```

Expected: a different non-zero slot `M`, distinct ports `300M` and `800M`, distinct schemas.

- [ ] **Step 3: Confirm allocations are distinct**

```bash
cat .worktrees/test/wt-isolation-a/.worktree.env | grep -E 'PORT|NAME|PREFIX'
cat .worktrees/test/wt-isolation-b/.worktree.env | grep -E 'PORT|NAME|PREFIX'
```

Expected: ports, schema names, and Redis prefixes are all different between A and B.

- [ ] **Step 4: Run a single E2E test in each worktree concurrently**

In two terminals:

```bash
# Terminal 1
cd .worktrees/test/wt-isolation-a/backend
uv run pytest tests/e2e/test_auth.py::TestAuthFlow -v

# Terminal 2
cd .worktrees/test/wt-isolation-b/backend
uv run pytest tests/e2e/test_auth.py::TestAuthFlow -v
```

Expected: both pass without interfering. (Before this design, running both at once would fail intermittently due to shared `cubeplex_test` schema and `FLUSHDB`.)

- [ ] **Step 5: Run `doctor` in each**

```bash
(cd .worktrees/test/wt-isolation-a && ./scripts/worktree-env doctor)
(cd .worktrees/test/wt-isolation-b && ./scripts/worktree-env doctor)
```

Expected: all `✓` lines, exit 0.

- [ ] **Step 6: Destroy both worktrees**

```bash
(cd .worktrees/test/wt-isolation-a && ../../../scripts/worktree-env destroy)
git worktree remove .worktrees/test/wt-isolation-a
git branch -D test/wt-isolation-a

(cd .worktrees/test/wt-isolation-b && ../../../scripts/worktree-env destroy)
git worktree remove .worktrees/test/wt-isolation-b
git branch -D test/wt-isolation-b
```

Expected:
- Schemas dropped
- Redis keys cleared (prints count of keys deleted)
- `.worktree.env` removed
- Registry entries pruned
- `git worktree remove` succeeds

- [ ] **Step 7: Verify cleanliness**

```bash
./scripts/worktree-env clean-orphans
```

Expected: `No orphans found.`

```bash
cat .worktrees/registry.json
```

Expected: no entries for `test-wt-isolation-a` or `test-wt-isolation-b`.

- [ ] **Step 8: No commit** (smoke test only — no artifacts left)

---

## Self-Review

**Spec coverage:**

| Spec section | Implemented in |
|---|---|
| Helper script (`worktree-env`) | Tasks 2–12 |
| `new-worktree` wrapper, branches from latest main | Task 13 |
| `.worktree.env` schema | Task 6 (`write_worktree_env`) |
| Slot allocation (hash + collision via registry JSON) | Task 4 |
| MySQL schema create + alembic upgrade | Task 9 |
| Backend config loader hook | Task 14 |
| Frontend config loader hooks (Next + Playwright) | Task 15 |
| `_flush_test_redis` prefix-scoped delete | Task 16 |
| AGENTS.md doc section | Task 17 |
| `destroy` and `clean-orphans` | Tasks 10, 12 |
| `doctor` | Task 11 |
| Verification: parallel E2E in two worktrees | Task 19 |

No gaps.

**Placeholder scan:** No "TBD", "TODO", "implement later", or vague directives. Every code-changing step contains the actual code.

**Type consistency:**
- `slugify`, `Registry`, `allocate_offset`, `record_allocation`, `WorktreeInfo`, `find_main_repo`, `current_worktree_info`, `env_file_path`, `db_dev_schema`, `db_test_schema`, `redis_prefix`, `write_worktree_env`, `_load_dotenv_into_environ`, `_mysql_creds_from_env`, `_mysql_exec`, `_create_schemas`, `_drop_schemas`, `_alembic_upgrade`, `_redis_delete_prefix`, `_read_redis_url_from_env_file`, `_copy_if_missing`, `_git`, `_hash_slot`, `cmd_init`, `cmd_show`, `cmd_destroy`, `cmd_doctor`, `cmd_clean_orphans`, `main`, `SLOT_RANGE`, `ENV_FILE_NAME` — all names referenced in later tasks match earlier definitions.
- `WorktreeInfo` fields used: `path`, `main_path`, `branch`, `is_main` — consistent throughout.
- Registry entry shape: `{"offset": int, "branch": str, "path": str, "created_at": str}` — consistent across allocate, record, destroy, doctor, clean-orphans.

No issues found.
