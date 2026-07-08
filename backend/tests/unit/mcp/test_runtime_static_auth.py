"""Unit tests for the cubepi_runtime static-auth dispatch.

The runtime supports three static-auth styles:
  * ``bearer``  → ``Authorization: Bearer <token>`` (default; legacy shape).
  * ``header``  → custom request header carrying the raw token (e.g. Exa's
    ``x-api-key``).
  * ``query``   → key/value appended to the connector URL (e.g. Tavily's
    ``?tavilyApiKey=<token>``).

These tests cover the two pure helpers (``_inject_query_param`` and
``_apply_static_credential``) — the full resolve loop is exercised by the
mcp-installs E2E suite.
"""

from __future__ import annotations

from cubebox.mcp.cubepi_runtime import _apply_static_credential, _inject_query_param
from cubebox.mcp.effective import MCPRuntimeConnectorSpec


def _make_spec(
    *,
    style: str = "bearer",
    header_name: str | None = None,
    query_param: str | None = None,
    server_url: str = "https://mcp.example.com/mcp",
) -> MCPRuntimeConnectorSpec:
    return MCPRuntimeConnectorSpec(
        connector_id="mcins_test",
        name="test",
        server_url=server_url,
        transport="streamable_http",
        auth_method="static",
        grant_scope="org",
        credential_id="cred_test",
        refresh_credential_id=None,
        tool_citations={},
        static_auth_style=style,
        static_auth_header_name=header_name,
        static_auth_query_param=query_param,
    )


def test_apply_static_credential_bearer_default() -> None:
    spec = _make_spec(style="bearer")
    headers, url = _apply_static_credential(
        spec=spec, headers={}, server_url=spec.server_url, plaintext="tok_abc"
    )
    assert headers == {"Authorization": "Bearer tok_abc"}
    assert url == spec.server_url


def test_apply_static_credential_custom_header() -> None:
    """Exa-style: token goes raw into a configured header name."""
    spec = _make_spec(style="header", header_name="x-api-key")
    headers, url = _apply_static_credential(
        spec=spec, headers={}, server_url=spec.server_url, plaintext="exa_secret"
    )
    assert headers == {"x-api-key": "exa_secret"}
    assert "Authorization" not in headers
    assert url == spec.server_url


def test_apply_static_credential_custom_header_preserves_existing() -> None:
    """Existing ``spec.headers`` survive credential injection."""
    spec = _make_spec(style="header", header_name="X-Lark-MCP-UAT")
    headers, _ = _apply_static_credential(
        spec=spec,
        headers={"X-Request-Id": "abc"},
        server_url=spec.server_url,
        plaintext="lark_uat",
    )
    assert headers == {"X-Request-Id": "abc", "X-Lark-MCP-UAT": "lark_uat"}


def test_apply_static_credential_query_param_rewrites_url() -> None:
    """Tavily-style: key rides on the connector URL, headers untouched."""
    spec = _make_spec(
        style="query",
        query_param="tavilyApiKey",
        server_url="https://mcp.tavily.com/mcp/",
    )
    headers, url = _apply_static_credential(
        spec=spec, headers={}, server_url=spec.server_url, plaintext="tvly_secret"
    )
    assert headers == {}
    assert "Authorization" not in headers
    assert url == "https://mcp.tavily.com/mcp/?tavilyApiKey=tvly_secret"


def test_apply_static_credential_query_param_replaces_existing_value() -> None:
    """Re-running with a new credential should not stack ``?key=`` params."""
    spec = _make_spec(style="query", query_param="apiKey")
    _, url = _apply_static_credential(
        spec=spec,
        headers={},
        server_url="https://search.example.com/mcp?apiKey=stale&region=us",
        plaintext="fresh_key",
    )
    # ``apiKey`` replaced, ``region`` preserved.
    assert "apiKey=fresh_key" in url
    assert "apiKey=stale" not in url
    assert "region=us" in url


def test_apply_static_credential_falls_back_to_bearer_when_header_name_missing() -> None:
    """A header-style spec missing the header name must not crash —
    the runtime falls back to Bearer so the install still talks."""
    spec = _make_spec(style="header", header_name=None)
    headers, _ = _apply_static_credential(
        spec=spec, headers={}, server_url=spec.server_url, plaintext="tok"
    )
    assert headers == {"Authorization": "Bearer tok"}


def test_apply_static_credential_falls_back_to_bearer_when_query_param_missing() -> None:
    spec = _make_spec(style="query", query_param=None)
    headers, url = _apply_static_credential(
        spec=spec, headers={}, server_url=spec.server_url, plaintext="tok"
    )
    assert headers == {"Authorization": "Bearer tok"}
    assert url == spec.server_url


def test_apply_static_credential_unknown_style_falls_back_to_bearer() -> None:
    spec = _make_spec(style="wat")
    headers, _ = _apply_static_credential(
        spec=spec, headers={}, server_url=spec.server_url, plaintext="tok"
    )
    assert headers == {"Authorization": "Bearer tok"}


def test_inject_query_param_appends_when_no_existing_query() -> None:
    url = _inject_query_param("https://example.com/mcp", "key", "abc")
    assert url == "https://example.com/mcp?key=abc"


def test_inject_query_param_preserves_other_params() -> None:
    url = _inject_query_param("https://example.com/mcp?foo=1&bar=2", "key", "abc")
    # Order: existing first (foo, bar), then injected key.
    assert url == "https://example.com/mcp?foo=1&bar=2&key=abc"


def test_inject_query_param_replaces_collision() -> None:
    url = _inject_query_param("https://example.com/mcp?key=old", "key", "new")
    assert url == "https://example.com/mcp?key=new"
