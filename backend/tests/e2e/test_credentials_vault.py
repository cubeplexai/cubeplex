"""Vault E2E tests for CredentialService with the real test database."""

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from cubebox.credentials.encryption import FernetBackend
from cubebox.credentials.exceptions import (
    CredentialInUseError,
    CredentialKindMismatch,
    CredentialNotFound,
)
from cubebox.models.mcp import MCPConnector, MCPConnectorTemplate, MCPCredentialGrant
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.mcp import (
    MCPConnectorRepository,
    MCPCredentialGrantRepository,
)
from cubebox.services.credential import CredentialService

# All org IDs used across vault tests. Each needs an organizations row so the
# credentials.org_id FK is satisfied. user-1/user-2 need users rows.
_VAULT_ORG_IDS = [
    "org-vault-a",
    "org-vault-kind",
    "org-vault-cross-a",
    "org-vault-cross-b",
    "org-vault-update",
    "org-vault-delete",
    "org-vault-mcp-ref",
]
_VAULT_USER_IDS = ["user-1", "user-2"]


@pytest_asyncio.fixture(autouse=True)
async def _seed_vault_deps(db_session: AsyncSession) -> None:
    """Insert minimal org and user rows required by vault tests.

    FK constraints on credentials(org_id) → organizations and
    credentials(created_by_user_id) → users were introduced with the
    short-id schema. These rows are not created by _ensure_default_user_and_membership
    because the vault tests use their own isolated org/user IDs.
    """
    for org_id in _VAULT_ORG_IDS:
        await db_session.execute(
            text(
                "INSERT INTO organizations (id, name, slug, created_at)"
                " VALUES (:id, :name, :slug, NOW())"
                " ON CONFLICT (id) DO NOTHING"
            ),
            {"id": org_id, "name": org_id, "slug": org_id},
        )
    for user_id in _VAULT_USER_IDS:
        await db_session.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, is_active, is_superuser,"
                " is_verified, created_at, language)"
                " VALUES (:id, :email, 'x', true, false, false, NOW(), 'en')"
                " ON CONFLICT (id) DO NOTHING"
            ),
            {"id": user_id, "email": f"{user_id}@vault-test.local"},
        )
    await db_session.commit()


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


async def test_delete_credential_referenced_by_mcp_grant_raises(
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
    # template_id is NOT NULL (FK); create a minimal global template first.
    tpl = MCPConnectorTemplate(
        slug=f"vault-mcp-ref-{uuid7()}",
        name="Vault MCP Ref Template",
        description="test",
        provider="test",
        server_url="https://mcp-ref",
        transport="streamable_http",
        supported_auth_methods=["static"],
        default_credential_policy="org",
        scope="global",
    )
    db_session.add(tpl)
    await db_session.flush()

    connector_repo = MCPConnectorRepository(db_session, org_id="org-vault-mcp-ref")
    connector = await connector_repo.add(
        MCPConnector(
            org_id="org-vault-mcp-ref",
            template_id=tpl.id,
            name=_name("ins"),
            server_url="https://mcp-ref",
            server_url_hash=_name("mcp-ref-hash"),
            transport="streamable_http",
            default_credential_policy="org",
            created_by_user_id="user-1",
        )
    )
    grant_repo = MCPCredentialGrantRepository(db_session, org_id="org-vault-mcp-ref")
    await grant_repo.add(
        MCPCredentialGrant(
            org_id="org-vault-mcp-ref",
            connector_id=connector.id,
            grant_scope="org",
            auth_method="static",
            workspace_id=None,
            user_id=None,
            credential_id=credential_id,
            created_by_user_id="user-1",
        )
    )

    with pytest.raises(CredentialInUseError):
        await service.delete(credential_id=credential_id)
