"""Internal constants and helpers shared across MCP service/runtime modules."""

import hashlib

# ``Credential.kind`` value used for plaintext MCP server credentials
# (Bearer tokens, API keys).
CREDENTIAL_KIND_MCP = "mcp_server"

# Per-install OAuth tokens are stored in the same credential vault but under
# distinct kinds so the references can't be cross-fetched by mistake.
# Confidential-client OAuth client secrets (returned by RFC 7591 DCR or
# seeded from catalog static config) live under their own kind too — a
# kind-mismatch guard catches cross-fetch mistakes.
CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN = "mcp_oauth_access_token"
CREDENTIAL_KIND_MCP_OAUTH_REFRESH_TOKEN = "mcp_oauth_refresh_token"
CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET = "mcp_oauth_client_secret"


def server_url_hash(url: str) -> str:
    """SHA-256 hex digest of an MCP server URL.

    Stored on ``MCPServer.server_url_hash`` so the partial unique index
    on ``(org_id, owner_workspace_id, server_url_hash)`` can enforce
    no-duplicate-URL invariants without indexing the full URL string.
    """
    return hashlib.sha256(url.encode("utf-8")).hexdigest()
