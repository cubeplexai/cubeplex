"""MCP OAuth domain exceptions."""

import httpx


def is_unauthorized_error(exc: BaseException) -> bool:
    """True when ``exc`` is (or wraps) an httpx 401 response error.

    The MCP SDK opens sessions inside asyncio TaskGroups, so an auth
    rejection reaches callers as one or more ``ExceptionGroup`` layers
    around the underlying ``httpx.HTTPStatusError``. Every leaf is
    inspected — connection-cleanup noise can precede the real cause.
    """
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if isinstance(current, BaseExceptionGroup):
            stack.extend(current.exceptions)
            continue
        if isinstance(current, httpx.HTTPStatusError) and current.response.status_code == 401:
            return True
    return False


class OAuthError(Exception):
    """Base class for MCP OAuth-related failures."""


class OAuthStateInvalid(OAuthError):
    """OAuth state token failed format/HMAC validation (likely tampered)."""


class OAuthStateExpired(OAuthError):
    """OAuth state token TTL elapsed or has already been consumed."""


class OAuthMetadataNotFound(OAuthError):
    """Well-known metadata endpoint 404 or missing required field."""


class OAuthMetadataFetchError(OAuthError):
    """HTTP error while fetching OAuth metadata from a well-known endpoint."""

    def __init__(self, url: str, status: int) -> None:
        super().__init__(f"OAuth metadata fetch failed for {url}: HTTP {status}")
        self.url = url
        self.status = status


class DCRError(OAuthError):
    """Dynamic Client Registration (RFC 7591) request failed."""

    def __init__(
        self,
        status: int,
        error: str | None = None,
        error_description: str | None = None,
    ) -> None:
        msg_parts = [f"DCR failed: HTTP {status}"]
        if error:
            msg_parts.append(f"error={error}")
        if error_description:
            msg_parts.append(f"error_description={error_description}")
        super().__init__("; ".join(msg_parts))
        self.status = status
        self.error = error
        self.error_description = error_description


class OAuthRefreshFailed(OAuthError):
    """Refresh token grant returned a terminal error (caller must reauthorize)."""

    def __init__(
        self,
        status: int,
        error: str | None = None,
        error_description: str | None = None,
    ) -> None:
        msg_parts = [f"OAuth refresh failed: HTTP {status}"]
        if error:
            msg_parts.append(f"error={error}")
        if error_description:
            msg_parts.append(f"error_description={error_description}")
        super().__init__("; ".join(msg_parts))
        self.status = status
        self.error = error
        self.error_description = error_description


class OAuthRefreshContention(OAuthError):
    """Another worker is refreshing the same token and didn't finish in time."""


class OAuthInvalidServerState(OAuthError):
    """Install row is not in a state where the OAuth operation makes sense."""


class OAuthCallbackError(OAuthError):
    """Authorization-code token exchange returned a non-2xx response."""

    def __init__(
        self,
        status: int,
        error: str | None = None,
        error_description: str | None = None,
    ) -> None:
        msg_parts = [f"OAuth callback exchange failed: HTTP {status}"]
        if error:
            msg_parts.append(f"error={error}")
        if error_description:
            msg_parts.append(f"error_description={error_description}")
        super().__init__("; ".join(msg_parts))
        self.status = status
        self.error = error
        self.error_description = error_description


class OAuthPKCEMissing(OAuthError):
    """PKCE verifier was not found in redis (expired / never written)."""


class MCPDiscoveryFailed(RuntimeError):
    """Raised when refresh-discovery cannot resolve a usable grant."""


class MCPInvokeFailed(RuntimeError):
    """Raised when Try It cannot resolve a usable grant or the tool errors."""


class MCPInvokeRateLimited(RuntimeError):
    """Raised when the Try It rate limit is exceeded."""
