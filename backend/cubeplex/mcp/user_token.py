"""MCP passthrough mode: sign cubeplex identity into a short-TTL JWT."""

from datetime import UTC, datetime, timedelta
from typing import Protocol

import jwt


class MCPUserTokenSigner(Protocol):
    async def sign(
        self,
        *,
        user_id: str,
        org_id: str,
        workspace_id: str,
        mcp_server_id: str,
        ttl: timedelta,
    ) -> str: ...


class HS256Signer:
    """CE signer that uses the shared auth JWT secret for short-lived MCP tokens."""

    def __init__(self, secret: str) -> None:
        self._secret = secret

    async def sign(
        self,
        *,
        user_id: str,
        org_id: str,
        workspace_id: str,
        mcp_server_id: str,
        ttl: timedelta,
    ) -> str:
        now = datetime.now(UTC)
        claims = {
            "sub": user_id,
            "org": org_id,
            "ws": workspace_id,
            "mcp": mcp_server_id,
            "exp": int((now + ttl).timestamp()),
            "iat": int(now.timestamp()),
            "iss": "cubeplex",
        }
        return jwt.encode(claims, self._secret, algorithm="HS256")
