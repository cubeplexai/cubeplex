"""Tests for dedup_key derivation."""

from __future__ import annotations

import hashlib

from cubebox.triggers.events import derive_dedup_key


def test_derive_dedup_key_from_event_id_header() -> None:
    """When event_id_header is present, use it as the dedup_key."""
    raw_body = b"some payload"
    result = derive_dedup_key(raw_body, event_id_header="evt-123")
    assert result == "evt-123"


def test_derive_dedup_key_from_body_hash_when_no_header() -> None:
    """When event_id_header is absent or empty, hash the raw body."""
    raw_body = b"abc"
    expected = hashlib.sha256(raw_body).hexdigest()

    # Test with None
    result_none = derive_dedup_key(raw_body, event_id_header=None)
    assert result_none == expected

    # Test with empty string (falsy)
    result_empty = derive_dedup_key(raw_body, event_id_header="")
    assert result_empty == expected


def test_dedup_key_timestamp_invariant() -> None:
    """Same body always yields same dedup_key, regardless of timestamp.

    This regression test enforces the spec rule: the fallback dedup_key
    must NOT include the signed freshness timestamp. A provider replaying
    an identical event with a fresh signature must produce the same dedup_key
    so the unique (trigger_id, dedup_key) constraint prevents duplicate runs.
    """
    raw_body = b"identical payload"

    # Call derive_dedup_key twice with the same body.
    key1 = derive_dedup_key(raw_body, event_id_header=None)
    key2 = derive_dedup_key(raw_body, event_id_header=None)

    assert key1 == key2
    assert key1 == hashlib.sha256(raw_body).hexdigest()
