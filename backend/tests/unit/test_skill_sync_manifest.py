"""Unit tests for manifest serialization + parsing."""

import json
from dataclasses import dataclass

from cubebox.skills.sync_manifest import (
    MANIFEST_PATH,
    MANIFEST_SCHEMA_VERSION,
    build_manifest,
    parse_manifest,
)


@dataclass
class _FakeResolved:
    name: str
    version: str
    skill_version_id: str
    content_hash: str
    storage_prefix: str = ""


def test_manifest_path_constant():
    assert MANIFEST_PATH == "/workspace/.skills/manifest.json"


def test_build_manifest_empty():
    m = build_manifest([])
    assert m["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert m["skills"] == {}
    assert "synced_at" in m


def test_build_manifest_normalises_colon():
    m = build_manifest([_FakeResolved("acme:x", "1.0.0", "skv_a", "sha256:aa")])
    assert "acme__x" in m["skills"]
    assert m["skills"]["acme__x"]["version"] == "1.0.0"
    assert m["skills"]["acme__x"]["content_hash"] == "sha256:aa"
    assert m["skills"]["acme__x"]["skill_version_id"] == "skv_a"


def test_parse_manifest_round_trip():
    enabled = [_FakeResolved("docx", "1.0.0", "skv_a", "sha256:aa")]
    blob = json.dumps(build_manifest(enabled)).encode("utf-8")
    parsed = parse_manifest(blob)
    assert parsed["skills"]["docx"]["version"] == "1.0.0"


def test_parse_manifest_invalid_json_returns_empty():
    parsed = parse_manifest(b"not json")
    assert parsed == {"skills": {}}


def test_parse_manifest_wrong_shape_returns_empty():
    parsed = parse_manifest(b'["not", "an", "object"]')
    assert parsed == {"skills": {}}


def test_parse_manifest_missing_skills_key_returns_empty():
    parsed = parse_manifest(b'{"schema_version": 1}')
    assert parsed == {"skills": {}}


def test_parse_manifest_empty_bytes_returns_empty():
    parsed = parse_manifest(b"")
    assert parsed == {"skills": {}}
