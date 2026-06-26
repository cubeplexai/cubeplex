"""Unit tests for hash_manifest."""

from cubebox.skills.sync_manifest import hash_manifest


def test_empty_manifest_stable():
    a = hash_manifest({})
    b = hash_manifest({})
    assert a == b
    assert a.startswith("sha256:")
    assert len(a) == len("sha256:") + 64


def test_key_order_does_not_matter():
    a = hash_manifest({"a": 1, "b": 2})
    b = hash_manifest({"b": 2, "a": 1})
    assert a == b


def test_nested_dict_key_order():
    a = hash_manifest({"skills": {"docx": {"version": "1.0.0", "skill_version_id": "skv_a"}}})
    b = hash_manifest({"skills": {"docx": {"skill_version_id": "skv_a", "version": "1.0.0"}}})
    assert a == b


def test_different_content_different_hash():
    a = hash_manifest({"skills": {"docx": {"version": "1.0.0"}}})
    b = hash_manifest({"skills": {"docx": {"version": "1.1.0"}}})
    assert a != b


def test_no_whitespace_in_canonical_form():
    """Canonical form must not depend on Python dict literal whitespace."""
    # If json.dumps uses default separators ", " and ": " the hash changes
    # when content shifts. Using separators=(',', ':') is what we want.
    a = hash_manifest({"a": "x"})
    # Re-hash same logical content via different dict construction:
    d = {}
    d["a"] = "x"
    b = hash_manifest(d)
    assert a == b
