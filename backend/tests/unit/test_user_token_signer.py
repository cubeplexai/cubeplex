"""Unit tests for MCP passthrough JWT signer."""

from datetime import timedelta

import jwt
import pytest

from cubeplex.mcp.user_token import HS256Signer


@pytest.fixture
def signer() -> HS256Signer:
    return HS256Signer(secret="test-secret-please-rotate")


async def test_sign_returns_decodable_jwt(signer: HS256Signer) -> None:
    token = await signer.sign(
        user_id="u1",
        org_id="o1",
        workspace_id="w1",
        mcp_server_id="m1",
        ttl=timedelta(minutes=5),
    )

    decoded = jwt.decode(token, "test-secret-please-rotate", algorithms=["HS256"])
    assert decoded["sub"] == "u1"
    assert decoded["org"] == "o1"
    assert decoded["ws"] == "w1"
    assert decoded["mcp"] == "m1"
    assert decoded["iss"] == "cubeplex"
    assert "exp" in decoded


async def test_sign_with_wrong_secret_fails_verification(signer: HS256Signer) -> None:
    token = await signer.sign(
        user_id="u1",
        org_id="o1",
        workspace_id="w1",
        mcp_server_id="m1",
        ttl=timedelta(minutes=5),
    )

    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, "wrong-secret", algorithms=["HS256"])


async def test_sign_zero_ttl_already_expired(signer: HS256Signer) -> None:
    token = await signer.sign(
        user_id="u1",
        org_id="o1",
        workspace_id="w1",
        mcp_server_id="m1",
        ttl=timedelta(seconds=-1),
    )

    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(token, "test-secret-please-rotate", algorithms=["HS256"])
