"""Unit tests for the shared 401 classifier.

The MCP SDK opens sessions inside asyncio TaskGroups, so an auth
rejection reaches callers as ``ExceptionGroup`` layers wrapping the
underlying ``httpx.HTTPStatusError``. Both the discovery service and
the runtime tool-call retry key their refresh-and-retry behavior on
``is_unauthorized_error`` — misclassifying would either retry on
non-auth failures (wasted refresh-token rotations) or never recover
from a revoked token.
"""

from __future__ import annotations

import httpx

from cubeplex.mcp.exceptions import is_unauthorized_error


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://mcp.example.com/mcp")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


def test_bare_401_is_unauthorized() -> None:
    assert is_unauthorized_error(_http_status_error(401))


def test_group_wrapped_401_is_unauthorized() -> None:
    group = ExceptionGroup("unhandled errors in a TaskGroup", [_http_status_error(401)])
    assert is_unauthorized_error(group)


def test_nested_group_with_leading_noise_is_unauthorized() -> None:
    # httpx connection-cleanup noise can precede the real cause; every
    # leaf must be inspected, not just the first.
    inner = ExceptionGroup(
        "transport",
        [RuntimeError("connection reset"), _http_status_error(401)],
    )
    group = ExceptionGroup("unhandled errors in a TaskGroup", [inner])
    assert is_unauthorized_error(group)


def test_non_401_http_error_is_not_unauthorized() -> None:
    assert not is_unauthorized_error(_http_status_error(500))
    assert not is_unauthorized_error(ExceptionGroup("group", [_http_status_error(403)]))


def test_unrelated_exception_is_not_unauthorized() -> None:
    assert not is_unauthorized_error(RuntimeError("boom"))
