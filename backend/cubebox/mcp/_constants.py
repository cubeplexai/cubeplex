"""Internal constants and helpers shared across MCP service/runtime modules."""

import hashlib
import re

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
    """SHA-256 hex digest of an MCP connector URL.

    Stored on ``MCPConnector.server_url_hash`` so the partial unique
    indexes on URL × scope can enforce no-duplicate-URL invariants without
    indexing the full URL string.
    """
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


# Regex defining what counts as a "non-namespace character" in the cubepi
# runtime's tool slug. Kept here (rather than buried in cubepi_runtime) so
# the alembic generated-column ``Computed(...)`` expression and the
# service-layer preflight share one source of truth — the Postgres
# expression below MUST stay in sync with this Python regex byte-for-byte.
_NS_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def slugify_for_namespace(server_name: str) -> str:
    """Produce a tool-namespace slug from an MCP install's display name.

    Mirrors what the LLM sees as the tool name prefix; non-alphanumeric
    runs are collapsed to ``_`` and trimmed from both ends. An all-symbol
    name (slug ends up empty) falls back to ``mcp``.

    Same algorithm populates ``mcp_connectors.slug_name`` so the org-wide
    partial unique index can enforce uniqueness on the canonical slug rather
    than the raw display string (where ``Web Tools`` and ``Web-Tools`` would
    otherwise both squat on the slug ``Web_Tools``).
    """
    slug = _NS_SLUG_RE.sub("_", server_name).strip("_")
    return slug or "mcp"


# Postgres expression for the slug_name generated column. Kept next to
# ``slugify_for_namespace`` so the Python and SQL implementations are
# obviously paired; any change to one MUST mirror in the other.
SLUG_NAME_PG_EXPRESSION = (
    "COALESCE("
    "NULLIF(TRIM(BOTH '_' FROM regexp_replace(name, '[^a-zA-Z0-9]+', '_', 'g')), '')"
    ", 'mcp'"
    ")"
)
