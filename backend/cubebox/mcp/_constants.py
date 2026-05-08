"""Internal constants and helpers shared across MCP service/runtime modules."""

import hashlib

# ``Credential.kind`` value used for plaintext MCP server credentials
# (Bearer tokens, API keys). System OAuth client secrets use a different
# kind (see ``catalog_seed._OAUTH_CLIENT_SECRET_KIND``).
CREDENTIAL_KIND_MCP = "mcp_server"


def server_url_hash(url: str) -> str:
    """SHA-256 hex digest of an MCP server URL.

    Stored on ``MCPServer.server_url_hash`` so the partial unique index
    on ``(org_id, owner_workspace_id, server_url_hash)`` can enforce
    no-duplicate-URL invariants without indexing the full URL string.
    """
    return hashlib.sha256(url.encode("utf-8")).hexdigest()
