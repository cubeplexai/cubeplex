"""Vault E2E tests for CredentialService with the real test database."""

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from cubebox.credentials.encryption import FernetBackend
from cubebox.credentials.exceptions import (
    CredentialInUseError,
    CredentialKindMismatch,
    CredentialNotFound,
)
from cubebox.models import MCPServer
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.mcp import MCPServerRepository
from cubebox.services.credential import CredentialService


@pytest.fixture
def backend() -> FernetBackend:
    return FernetBackend([Fernet.generate_key()])


def _name(prefix: str) -> str:
    return f"{prefix}-{uuid7()}"


async def test_roundtrip_decrypts_correct_kind(
    db_session: AsyncSession, backend: FernetBackend
) -> None:
    repo = CredentialRepository(db_session, org_id="org-vault-a")
    service = CredentialService(repo, backend, org_id="org-vault-a", actor_user_id="user-1")

    credential_id = await service.create(
        kind="mcp_server",
        name=_name("github"),
        plaintext="ghp_abcXYZ",
    )

    assert (
        await service.get_decrypted(
            credential_id=credential_id,
            requesting_kind="mcp_server",
        )
        == "ghp_abcXYZ"
    )


async def test_kind_mismatch_raises(db_session: AsyncSession, backend: FernetBackend) -> None:
    service = CredentialService(
        CredentialRepository(db_session, org_id="org-vault-kind"),
        backend,
        org_id="org-vault-kind",
        actor_user_id="user-1",
    )
    credential_id = await service.create(
        kind="mcp_server",
        name=_name("kind"),
        plaintext="secret",
    )

    with pytest.raises(CredentialKindMismatch):
        await service.get_decrypted(credential_id=credential_id, requesting_kind="skill_env")


async def test_cross_org_returns_not_found(
    db_session: AsyncSession, backend: FernetBackend
) -> None:
    service_a = CredentialService(
        CredentialRepository(db_session, org_id="org-vault-cross-a"),
        backend,
        org_id="org-vault-cross-a",
        actor_user_id="user-1",
    )
    credential_id = await service_a.create(
        kind="mcp_server",
        name=_name("cross"),
        plaintext="secret",
    )

    service_b = CredentialService(
        CredentialRepository(db_session, org_id="org-vault-cross-b"),
        backend,
        org_id="org-vault-cross-b",
        actor_user_id="user-2",
    )

    with pytest.raises(CredentialNotFound):
        await service_b.get_decrypted(credential_id=credential_id, requesting_kind="mcp_server")


async def test_update_replaces_ciphertext(db_session: AsyncSession, backend: FernetBackend) -> None:
    service = CredentialService(
        CredentialRepository(db_session, org_id="org-vault-update"),
        backend,
        org_id="org-vault-update",
        actor_user_id="user-1",
    )
    credential_id = await service.create(
        kind="mcp_server",
        name=_name("update"),
        plaintext="old",
    )

    await service.update(credential_id=credential_id, plaintext="new")

    assert (
        await service.get_decrypted(
            credential_id=credential_id,
            requesting_kind="mcp_server",
        )
        == "new"
    )


async def test_delete_removes_row(db_session: AsyncSession, backend: FernetBackend) -> None:
    service = CredentialService(
        CredentialRepository(db_session, org_id="org-vault-delete"),
        backend,
        org_id="org-vault-delete",
        actor_user_id="user-1",
    )
    credential_id = await service.create(
        kind="mcp_server",
        name=_name("delete"),
        plaintext="secret",
    )

    await service.delete(credential_id=credential_id)

    with pytest.raises(CredentialNotFound):
        await service.get_decrypted(credential_id=credential_id, requesting_kind="mcp_server")


async def test_delete_credential_referenced_by_mcp_server_raises(
    db_session: AsyncSession,
    backend: FernetBackend,
) -> None:
    service = CredentialService(
        CredentialRepository(db_session, org_id="org-vault-mcp-ref"),
        backend,
        org_id="org-vault-mcp-ref",
        actor_user_id="user-1",
    )
    credential_id = await service.create(
        kind="mcp_server",
        name=_name("mcp-ref"),
        plaintext="secret",
    )
    await MCPServerRepository(db_session, org_id="org-vault-mcp-ref").add(
        MCPServer(
            org_id="org-vault-mcp-ref",
            name=_name("srv"),
            server_url="https://mcp-ref",
            server_url_hash="mcp-ref-hash",
            transport="streamable_http",
            auth_method="static",
            credential_scope="org",
            credential_id=credential_id,
            created_by_user_id="user-1",
        )
    )

    with pytest.raises(CredentialInUseError):
        await service.delete(credential_id=credential_id)
