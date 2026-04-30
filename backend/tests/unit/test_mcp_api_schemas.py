"""Unit tests for MCP API schemas."""

from cubebox.api.schemas.mcp import MCPServerCreateAdmin, MCPServerOut


def test_admin_create_schema_accepts_org_scope_static() -> None:
    payload = MCPServerCreateAdmin(
        name="github",
        server_url="https://example.com/mcp",
        transport="streamable_http",
        auth_method="static",
        credential_scope="org",
        credential_plaintext="secret",
    )

    assert payload.credential_scope == "org"


def test_server_out_does_not_expose_plaintext_field() -> None:
    field_names = set(MCPServerOut.model_fields)

    assert "credential_plaintext" not in field_names
