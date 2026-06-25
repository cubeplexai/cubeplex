"""Unit tests for compute_skill_version_hash."""

import pytest

from cubebox.skills.content_hash import (
    _compute_skill_version_hash_sync,
    compute_skill_version_hash,
)


def test_empty_files_returns_stable_hash():
    h = _compute_skill_version_hash_sync({})
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 64


def test_same_input_same_output():
    files = {"SKILL.md": b"hello", "scripts/run.sh": b"echo 1"}
    a = _compute_skill_version_hash_sync(files)
    b = _compute_skill_version_hash_sync(dict(files))
    assert a == b


def test_dict_insertion_order_does_not_affect_hash():
    a = _compute_skill_version_hash_sync({"a": b"1", "b": b"2"})
    b = _compute_skill_version_hash_sync({"b": b"2", "a": b"1"})
    assert a == b


def test_different_content_different_hash():
    a = _compute_skill_version_hash_sync({"x": b"foo"})
    b = _compute_skill_version_hash_sync({"x": b"bar"})
    assert a != b


def test_concatenation_ambiguity_resolved():
    # {a:"foo",b:"bar"} vs {a:"foobar",b:""} must NOT collide
    a = _compute_skill_version_hash_sync({"a": b"foo", "b": b"bar"})
    b = _compute_skill_version_hash_sync({"a": b"foobar", "b": b""})
    assert a != b


def test_path_separator_ambiguity_resolved():
    # {a/b: "x"} vs {a: "", b: "x"} must NOT collide on naive concat
    a = _compute_skill_version_hash_sync({"a/b": b"x"})
    b = _compute_skill_version_hash_sync({"a": b"", "b": b"x"})
    assert a != b


def test_unicode_path():
    h = _compute_skill_version_hash_sync({"中文/SKILL.md": b"body"})
    assert h.startswith("sha256:")


def test_empty_file_body():
    h1 = _compute_skill_version_hash_sync({"a": b""})
    h2 = _compute_skill_version_hash_sync({"a": b"\0"})
    assert h1 != h2


@pytest.mark.asyncio
async def test_async_wrapper_returns_same_as_sync():
    files = {"SKILL.md": b"hello"}
    sync_h = _compute_skill_version_hash_sync(files)
    async_h = await compute_skill_version_hash(files)
    assert sync_h == async_h
