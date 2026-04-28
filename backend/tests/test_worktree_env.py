"""Unit tests for scripts/worktree-env helper module."""

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "worktree-env"


def _load_script():
    loader = importlib.machinery.SourceFileLoader("worktree_env", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader("worktree_env", loader)
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
