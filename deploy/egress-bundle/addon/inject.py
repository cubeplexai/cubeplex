"""OpenSandbox egress addon: swap cbxref_ placeholders for real secrets.

Loaded after the bundled system addon. For each outbound request, scan header
values for cbxref_ tokens; for each, call the cubebox exchange endpoint over
mTLS using the per-sandbox client cert, and replace the token with the returned
secret (only in headers allowed by the binding's header_names). Fails closed.

The mitmproxy import is guarded so the pure helpers (scan_placeholders,
should_substitute_header) can be imported and unit-tested without a live
mitmproxy installation.
"""

from __future__ import annotations

import os
import re
import time
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from mitmproxy import http

PLACEHOLDER_RE = re.compile(r"cbxref_[A-Z2-7]{32}")
CLIENT_CERT = ("/etc/egress-client/tls.crt", "/etc/egress-client/tls.key")
EXCHANGE_CA = "/etc/egress-client/exchange-ca.pem"  # CA that signed the exchange server cert
_CACHE_TTL = 120.0

# cache: (placeholder, host) -> (secret, header_names, expires_at)
_cache: dict[tuple[str, str], tuple[str, list[str] | None, float]] = {}

# Lazily built so the pure helpers (scan/should_substitute) import without
# cluster env vars or mounted cert files (unit-testable).
_client: httpx.Client | None = None
_exchange_url: str | None = None


def _client_and_url() -> tuple[httpx.Client, str]:
    global _client, _exchange_url
    if _client is None:
        _exchange_url = os.environ["EGRESS_EXCHANGE_URL"]
        _client = httpx.Client(cert=CLIENT_CERT, verify=EXCHANGE_CA, timeout=5.0)
    assert _exchange_url is not None
    return _client, _exchange_url


def scan_placeholders(value: str) -> list[str]:
    return PLACEHOLDER_RE.findall(value)


def should_substitute_header(header_name: str, header_names: list[str] | None) -> bool:
    if header_names is None:
        return True
    # HTTP header names are case-insensitive; normalize both sides.
    return header_name.lower() in {h.lower() for h in header_names}


def _exchange(placeholder: str, host: str) -> tuple[str, list[str] | None] | None:
    now = time.monotonic()
    hit = _cache.get((placeholder, host))
    if hit and hit[2] > now:
        return hit[0], hit[1]
    client, url = _client_and_url()
    try:
        resp = client.post(url, json={"placeholder": placeholder, "host": host})
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None  # fail closed (denied / unknown / wrong host)
    data = resp.json()
    secret, header_names = data["secret"], data.get("header_names")
    _cache[(placeholder, host)] = (secret, header_names, now + _CACHE_TTL)
    return secret, header_names


def request(flow: "http.HTTPFlow") -> None:
    host = (flow.request.host or "").lower()  # mitmproxy: verified upstream host
    for name in list(flow.request.headers.keys()):
        value = flow.request.headers[name]
        tokens = scan_placeholders(value)
        if not tokens:
            continue
        for token in tokens:
            result = _exchange(token, host)
            if result is None:
                continue  # fail closed: leave placeholder, do not guess
            secret, header_names = result
            if not should_substitute_header(name, header_names):
                continue
            value = value.replace(token, secret)
        flow.request.headers[name] = value
