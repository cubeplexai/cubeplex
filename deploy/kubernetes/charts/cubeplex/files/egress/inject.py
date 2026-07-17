"""OpenSandbox egress addon: swap cbxref_ placeholders for real secrets.

Loaded after the bundled system addon. For each outbound request, scan header
values for cbxref_ tokens; for each, call the cubeplex exchange endpoint over
mTLS using the per-sandbox client cert, and replace the token with the returned
secret (only in headers allowed by the binding's header_names). Fails closed.

The mitmproxy import is guarded so the pure helpers (scan_placeholders,
should_substitute_header) can be imported and unit-tested without a live
mitmproxy installation.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import ssl
import urllib.parse
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from mitmproxy import http

PLACEHOLDER_RE = re.compile(r"cbxref_[A-Z2-7]{32}")
CLIENT_CERT = ("/etc/egress-client/tls.crt", "/etc/egress-client/tls.key")
EXCHANGE_CA = "/etc/egress-client/exchange-ca.pem"  # CA that signed the exchange server cert
# No response caching: every substitution must call the exchange so that ref
# revocation (sandbox recycle/cleanup) and expiry take effect immediately. A
# cache here would let a revoked/expired placeholder still be substituted for
# the cache window, weakening the fail-closed guarantee (Codex P1).
#
# Only the Python stdlib is used here: the addon runs inside mitmproxy's bundled
# interpreter, which does NOT ship third-party packages like httpx. The mTLS
# call to the exchange uses http.client + an ssl context that presents the
# per-sandbox client cert and verifies the exchange server against EXCHANGE_CA.

_ssl_ctx: ssl.SSLContext | None = None
_exchange_url: str | None = None


def _ctx_and_url() -> tuple[ssl.SSLContext, str]:
    global _ssl_ctx, _exchange_url
    if _ssl_ctx is None:
        _exchange_url = os.environ["EGRESS_EXCHANGE_URL"]
        ctx = ssl.create_default_context(cafile=EXCHANGE_CA)
        ctx.load_cert_chain(certfile=CLIENT_CERT[0], keyfile=CLIENT_CERT[1])
        _ssl_ctx = ctx
    assert _exchange_url is not None
    return _ssl_ctx, _exchange_url


def scan_placeholders(value: str) -> list[str]:
    return PLACEHOLDER_RE.findall(value)


def should_substitute_header(header_name: str, header_names: list[str] | None) -> bool:
    if header_names is None:
        return True
    # HTTP header names are case-insensitive; normalize both sides.
    return header_name.lower() in {h.lower() for h in header_names}


_upstream_proxy: tuple[str, tuple[str, int]] | None = None
_upstream_proxy_resolved = False


def parse_proxy_url(url: str | None) -> tuple[str, tuple[str, int]] | None:
    """Parse a proxy URL into a (scheme, (host, port)) tuple for mitmproxy's via."""
    if not url or not url.strip():
        return None
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname or not parts.port:
        return None
    return (parts.scheme, (parts.hostname, parts.port))


def apply_upstream_proxy(
    flow: "http.HTTPFlow",
    proxy: tuple[str, tuple[str, int]] | None,
) -> None:
    """Set flow.server_conn.via if a proxy is configured and destination isn't the proxy itself."""
    if proxy is None:
        return
    dest = getattr(flow.server_conn, "address", None)
    if dest and dest[0] == proxy[1][0] and dest[1] == proxy[1][1]:
        return
    flow.server_conn.via = proxy  # type: ignore[assignment]


def _ensure_upstream_proxy() -> None:
    """Lazy-resolve the upstream proxy on first call. Retries on failure."""
    global _upstream_proxy, _upstream_proxy_resolved
    if _upstream_proxy_resolved:
        return
    try:
        ctx, url = _ctx_and_url()
        parts = urllib.parse.urlsplit(url)
        base_path = parts.path.rsplit("/exchange", 1)[0]
        conn = http.client.HTTPSConnection(
            parts.hostname, parts.port or 443, context=ctx, timeout=5.0
        )
        try:
            conn.request("GET", f"{base_path}/proxy-config")
            resp = conn.getresponse()
            if resp.status != 200:
                return  # leave unresolved → retry next flow
            data = json.loads(resp.read())
        finally:
            conn.close()
        _upstream_proxy = parse_proxy_url(data.get("proxy"))
        _upstream_proxy_resolved = True  # success (even if proxy is None = not configured)
    except (OSError, ValueError, KeyError):
        pass  # leave unresolved → retry next flow


def _exchange(placeholder: str, host: str) -> tuple[str, list[str] | None] | None:
    ctx, url = _ctx_and_url()
    parts = urllib.parse.urlsplit(url)
    conn = http.client.HTTPSConnection(
        parts.hostname, parts.port or 443, context=ctx, timeout=5.0
    )
    try:
        body = json.dumps({"placeholder": placeholder, "host": host})
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        if resp.status != 200:
            return None  # fail closed (denied / unknown / wrong host / revoked / expired)
        data = json.loads(resp.read())
    except (OSError, ValueError):
        return None  # network / TLS / JSON error → fail closed
    finally:
        conn.close()
    return data["secret"], data.get("header_names")


def tls_clienthello(data: Any) -> None:
    """Set the upstream proxy BEFORE the server TLS connection is established.

    In transparent mode with connection_strategy=lazy, mitmproxy still opens an
    upstream connection during TLS ClientHello (establish_server_tls_first) to
    fetch the server certificate for the MITM cert. Setting via in the request
    hook would be too late — the direct connection is already open. This hook
    fires before that, so via is in place when the connection opens.
    """
    _ensure_upstream_proxy()
    if _upstream_proxy is None:
        return
    server = data.context.server
    dest = (getattr(server, "address", None) or (None, None))
    if dest[0] == _upstream_proxy[1][0] and dest[1] == _upstream_proxy[1][1]:
        return
    server.via = _upstream_proxy


def request(flow: "http.HTTPFlow") -> None:
    # For plain HTTP flows tls_clienthello never fires, so set via here as well.
    # For HTTPS flows tls_clienthello already set it; apply_upstream_proxy is a
    # no-op when via is already assigned (server_conn.via is already the proxy).
    _ensure_upstream_proxy()
    apply_upstream_proxy(flow, _upstream_proxy)
    # Only substitute on HTTPS. Two reasons:
    #  1. A real secret must never traverse plaintext.
    #  2. We key the exchange on the TLS SNI from the ClientHello
    #     (flow.client_conn.sni), NOT flow.request.host. In mitmproxy transparent
    #     mode flow.request.host is the original destination IP, and request
    #     .pretty_host can fall back to the client-controlled Host header — neither
    #     is the name the egress sidecar verifies the upstream cert against. The
    #     SNI is what upstream-cert verification is performed against (ssl_insecure
    #     must stay OFF — do NOT set OPENSANDBOX_EGRESS_MITMPROXY_SSL_INSECURE), so
    #     keying on it makes the host cert-verified. No SNI → fail closed.
    if flow.request.scheme != "https":
        return
    sni = getattr(flow.client_conn, "sni", None)
    if not sni:
        return  # no verified SNI → cannot establish a cert-bound host; fail closed
    host = sni.lower()
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
