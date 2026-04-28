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
