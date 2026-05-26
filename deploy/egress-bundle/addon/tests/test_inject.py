# deploy/egress-bundle/addon/tests/test_inject.py
# The addon module exposes pure helpers so it is testable without a live mitmproxy.
import inject
from inject import should_substitute_header, scan_placeholders


class _FakeReq:
    def __init__(self, scheme, host, headers):
        self.scheme = scheme
        self.host = host
        self.headers = headers


class _FakeClientConn:
    def __init__(self, sni):
        self.sni = sni


class _FakeFlow:
    def __init__(self, scheme, host, headers, sni=None):
        self.request = _FakeReq(scheme, host, headers)
        self.client_conn = _FakeClientConn(sni)


def test_request_skips_plaintext_http():
    """Security: secrets must never be substituted on plaintext HTTP. The http
    flow must return early (before any exchange call), leaving the placeholder."""
    placeholder = "cbxref_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    flow = _FakeFlow(
        "http", "api.github.com", {"Authorization": f"Bearer {placeholder}"}, sni="api.github.com"
    )
    # If request() did NOT return early it would call _exchange (no env/cert in
    # the test env) and raise — so reaching the assertion proves the http guard.
    inject.request(flow)  # type: ignore[arg-type]
    assert flow.request.headers["Authorization"] == f"Bearer {placeholder}"  # unchanged


def test_request_skips_when_no_sni():
    """Security: with no verified TLS SNI we cannot establish a cert-bound host,
    so the request must fail closed (no exchange call), leaving the placeholder.
    Guards against transparent-mode flows where flow.request.host is just the
    destination IP — only the ClientHello SNI is trustworthy."""
    placeholder = "cbxref_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    flow = _FakeFlow(
        "https", "140.82.121.6", {"Authorization": f"Bearer {placeholder}"}, sni=None
    )
    # Reaching the assertion proves the no-SNI guard returned before _exchange
    # (which would raise with no cert/env in the test environment).
    inject.request(flow)  # type: ignore[arg-type]
    assert flow.request.headers["Authorization"] == f"Bearer {placeholder}"  # unchanged


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
