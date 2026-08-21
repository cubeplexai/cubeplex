"""Contract for pre-push check-ci file filters.

A docs-only or opposite-side push must not run that side's check-ci; a code
change (or a change to the hook/Makefile gate) must.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_PATH = _REPO_ROOT / ".pre-commit-config.yaml"


def _load_hooks() -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load(_CONFIG_PATH.read_text())
    found: dict[str, dict[str, Any]] = {}
    for repo in raw["repos"]:
        for hook in repo.get("hooks", []):
            hook_id = hook.get("id")
            if hook_id in {"backend-check-ci", "frontend-check-ci"}:
                found[hook_id] = hook
    return found


def _matches(hook: dict[str, Any], path: str) -> bool:
    if hook.get("always_run"):
        return True
    include = re.compile(hook.get("files") or "")
    exclude = re.compile(hook.get("exclude") or "^$")
    return bool(include.search(path) and not exclude.search(path))


def test_pre_push_check_ci_hooks_exist() -> None:
    hooks = _load_hooks()
    assert set(hooks) == {"backend-check-ci", "frontend-check-ci"}
    for hook in hooks.values():
        assert hook.get("always_run") is not True
        assert "pre-push" in hook.get("stages", [])


def test_backend_check_ci_runs_for_backend_code_not_docs() -> None:
    hook = _load_hooks()["backend-check-ci"]
    assert _matches(hook, "backend/cubeplex/middleware/sandbox.py")
    assert _matches(hook, "backend/pyproject.toml")
    assert _matches(hook, "backend/Makefile")
    assert _matches(hook, "Makefile")
    assert _matches(hook, ".pre-commit-config.yaml")
    assert not _matches(hook, "backend/README.md")
    assert not _matches(hook, "backend/docs/auth.md")
    assert not _matches(hook, "docs/testing.md")
    assert not _matches(hook, "frontend/packages/web/app/page.tsx")
    assert not _matches(hook, "scripts/new-worktree")


def test_frontend_check_ci_runs_for_frontend_code_not_docs() -> None:
    hook = _load_hooks()["frontend-check-ci"]
    assert _matches(hook, "frontend/packages/web/app/page.tsx")
    assert _matches(hook, "frontend/package.json")
    assert _matches(hook, "Makefile")
    assert _matches(hook, ".pre-commit-config.yaml")
    assert not _matches(hook, "frontend/README.md")
    assert not _matches(hook, "docs/testing.md")
    assert not _matches(hook, "backend/cubeplex/middleware/sandbox.py")
    assert not _matches(hook, "CONTRIBUTING.md")
