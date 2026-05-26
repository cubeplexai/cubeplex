# deploy/egress-bundle/addon/tests/test_inject.py
# The addon module exposes pure helpers so it is testable without a live mitmproxy.
from inject import should_substitute_header, scan_placeholders


def test_scan_finds_tokens():
    assert scan_placeholders("Bearer cbxref_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA") == [
        "cbxref_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    ]


def test_scan_finds_no_tokens_in_plain_value():
    assert scan_placeholders("Bearer ghp_real_token") == []


def test_scan_finds_multiple_tokens():
    value = "cbxref_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA cbxref_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
    assert scan_placeholders(value) == [
        "cbxref_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "cbxref_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
    ]


def test_header_names_gate():
    assert should_substitute_header("Authorization", ["Authorization"])
    assert should_substitute_header("Authorization", None)  # null = any header
    assert not should_substitute_header("X-Other", ["Authorization"])
    # HTTP header names are case-insensitive
    assert should_substitute_header("authorization", ["Authorization"])
    assert should_substitute_header("AUTHORIZATION", ["authorization"])


def test_header_names_empty_list_blocks_all():
    # An explicit empty list means no headers are allowed.
    assert not should_substitute_header("Authorization", [])
