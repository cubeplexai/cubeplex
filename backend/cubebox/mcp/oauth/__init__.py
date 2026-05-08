"""MCP OAuth utility modules.

# Why no E2E tests for OAuth flows

OAuth flows depend on a third-party authorization server. A locally-mocked AS
cannot reproduce real IdP behavior for DCR / token endpoints / refresh /
revocation. E2E tests passing against a mock cannot give production confidence.

OAuth coverage is therefore unit-test only. Production verification depends on
staging environment with real Notion / GitHub / Linear / Asana / Atlassian /
Sentry / Intercom / Cloudflare / Slack / Google Workspace accounts (recorded as
manual test plan in Phase 8). See spec ``docs/superpowers/specs/
2026-05-08-mcp-catalog-oauth-design.md`` §11.3.

If you are tempted to add a fake-AS E2E test "for completeness", DON'T — that
class of test was explicitly rejected during design review.
"""

from cubebox.mcp.oauth.callback import CallbackResult, OAuthCallbackHandler
from cubebox.mcp.oauth.dcr import DCRClient, DCRRequest, DCRResponse
from cubebox.mcp.oauth.metadata import (
    AuthorizationServerMetadata,
    OAuthMetadataDiscovery,
    ProtectedResourceMetadata,
)
from cubebox.mcp.oauth.pkce import PKCEChallenge, generate_pkce, verify_pkce_pair
from cubebox.mcp.oauth.state import OAuthStatePayload, OAuthStateStore
from cubebox.mcp.oauth.token_manager import OAuthTokenManager

__all__ = [
    "AuthorizationServerMetadata",
    "CallbackResult",
    "DCRClient",
    "DCRRequest",
    "DCRResponse",
    "OAuthCallbackHandler",
    "OAuthMetadataDiscovery",
    "OAuthStatePayload",
    "OAuthStateStore",
    "OAuthTokenManager",
    "PKCEChallenge",
    "ProtectedResourceMetadata",
    "generate_pkce",
    "verify_pkce_pair",
]
