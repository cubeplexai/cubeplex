"""E2E tests for Provider <-> Credential vault integration.

Covers what unit-level mocking can't: real DB, real Fernet encryption,
real snapshot-loader wiring, and the seeder running against the live engine.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.credentials.encryption import FernetBackend
from cubeplex.llm.snapshot import load_llm_snapshot
from cubeplex.models import Credential
from cubeplex.models.provider import Provider
from cubeplex.seeders.provider_seeder import seed_system_providers_from_config

pytestmark = pytest.mark.e2e


async def _get_provider_by_id(session: AsyncSession, pid: str) -> Provider:
    return (
        await session.execute(select(Provider).where(Provider.id == pid))  # type: ignore[arg-type]
    ).scalar_one()


async def _get_credential(session: AsyncSession, cred_id: str) -> Credential:
    return (
        await session.execute(
            select(Credential).where(Credential.id == cred_id)  # type: ignore[arg-type]
        )
    ).scalar_one()


async def test_create_provider_writes_encrypted_credential(
    admin_client: tuple[AsyncClient, str],
    db_session: AsyncSession,
) -> None:
    """POST /admin/providers stores api_key as a Credential row, never plaintext."""
    client, _ = admin_client
    secret = "sk-vault-roundtrip-XYZ"

    res = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "vault-roundtrip",
            "base_url": "https://example.com",
            "auth_type": "api_key",
            "api_key": secret,
        },
    )
    assert res.status_code == 201
    pid = res.json()["id"]
    assert res.json()["has_api_key"] is True

    provider = await _get_provider_by_id(db_session, pid)
    assert provider.credential_id is not None
    cred = await _get_credential(db_session, provider.credential_id)
    assert cred.kind == "provider_api_key"
    assert cred.value_encrypted != secret.encode()  # ciphertext, not plaintext
    assert secret.encode() not in cred.value_encrypted

    await client.delete(f"/api/v1/admin/providers/{pid}")


async def test_update_api_key_replaces_ciphertext_keeps_credential_id(
    admin_client: tuple[AsyncClient, str],
    db_session: AsyncSession,
) -> None:
    """Updating api_key rotates ciphertext but keeps the same credential row."""
    client, _ = admin_client

    res = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "vault-update",
            "base_url": "https://example.com",
            "auth_type": "api_key",
            "api_key": "sk-old",
        },
    )
    assert res.status_code == 201
    pid = res.json()["id"]
    p_old = await _get_provider_by_id(db_session, pid)
    cred_old = await _get_credential(db_session, p_old.credential_id or "")
    cipher_old = cred_old.value_encrypted

    res = await client.patch(
        f"/api/v1/admin/providers/{pid}",
        json={"api_key": "sk-new"},
    )
    assert res.status_code == 200

    db_session.expire_all()
    p_new = await _get_provider_by_id(db_session, pid)
    assert p_new.credential_id == p_old.credential_id
    cred_new = await _get_credential(db_session, p_new.credential_id or "")
    assert cred_new.value_encrypted != cipher_old

    await client.delete(f"/api/v1/admin/providers/{pid}")


async def test_delete_provider_removes_credential(
    admin_client: tuple[AsyncClient, str],
    db_session: AsyncSession,
) -> None:
    """DELETE provider also removes its credential vault entry."""
    client, _ = admin_client
    res = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "vault-delete",
            "base_url": "https://example.com",
            "auth_type": "api_key",
            "api_key": "sk-doomed",
        },
    )
    assert res.status_code == 201
    pid = res.json()["id"]
    cred_id = (await _get_provider_by_id(db_session, pid)).credential_id
    assert cred_id is not None

    res = await client.delete(f"/api/v1/admin/providers/{pid}")
    assert res.status_code == 204

    cred = (
        await db_session.execute(
            select(Credential).where(Credential.id == cred_id)  # type: ignore[arg-type]
        )
    ).scalar_one_or_none()
    assert cred is None


async def test_factory_decrypts_provider_api_key(
    admin_client: tuple[AsyncClient, str],
    db_session: AsyncSession,
) -> None:
    """load_llm_snapshot loads ProviderConfig.api_key by decrypting the credential."""
    client, _ = admin_client

    res = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "vault-factory",
            "base_url": "https://example.com",
            "auth_type": "api_key",
            "api_key": "sk-factory-XYZ",
        },
    )
    assert res.status_code == 201
    pid = res.json()["id"]

    # Use the same backend the API used (lifespan-built, on app.state).
    transport = client._transport  # type: ignore[attr-defined]
    backend = transport.app.state.encryption_backend
    provider = await _get_provider_by_id(db_session, pid)

    snap = await load_llm_snapshot(db_session, provider.org_id or "", backend)
    assert snap.providers["vault-factory"].api_key == "sk-factory-XYZ"

    await client.delete(f"/api/v1/admin/providers/{pid}")


async def test_factory_decrypts_with_rotated_keys(
    db_session: AsyncSession,
) -> None:
    """Encrypt with key1, then load with backend([key2, key1]) — still decrypts."""
    secret = "sk-rotation-direct"
    key1 = Fernet.generate_key()
    key2 = Fernet.generate_key()
    backend_old = FernetBackend([key1])
    backend_rotated = FernetBackend([key2, key1])  # key2 encrypts, key1 still decrypts

    # Seed a fake org + provider + credential using only key1 ciphertext.
    org_id = "org-vault-rotate"
    user_id = "user-vault-rotate"
    await db_session.execute(
        text(
            "INSERT INTO organizations (id, name, slug, created_at)"
            " VALUES (:id, :id, :id, NOW()) ON CONFLICT (id) DO NOTHING"
        ),
        {"id": org_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO users (id, email, hashed_password, is_active, is_superuser,"
            " is_verified, created_at, language) VALUES"
            " (:id, :email, 'x', true, false, false, NOW(), 'en')"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": user_id, "email": f"{user_id}@vault.local"},
    )
    ciphertext = await backend_old.encrypt(secret.encode())
    cred = Credential(
        org_id=org_id,
        kind="provider_api_key",
        name="rotation-target",
        value_encrypted=ciphertext,
        created_by_user_id=user_id,
    )
    db_session.add(cred)
    await db_session.flush()
    provider = Provider(
        org_id=org_id,
        name="rotation-target",
        slug="rotation-target",
        provider_type="openai-completions",
        base_url="https://example.com",
        auth_type="api_key",
        credential_id=cred.id,
        created_by_user_id=user_id,
    )
    db_session.add(provider)
    await db_session.commit()

    try:
        snap = await load_llm_snapshot(db_session, org_id, backend_rotated)
        assert snap.providers["rotation-target"].api_key == secret
    finally:
        await db_session.execute(
            text("DELETE FROM providers WHERE id = :pid"), {"pid": provider.id}
        )
        await db_session.execute(text("DELETE FROM credentials WHERE id = :cid"), {"cid": cred.id})
        await db_session.commit()


async def test_seeder_idempotent_with_changed_key(
    db_session: AsyncSession,
) -> None:
    """Re-running the seeder with a different api_key updates ciphertext, keeps cred id."""
    backend = FernetBackend([Fernet.generate_key()])
    # Seed once with a sentinel api_key by patching config.llm.providers temporarily.
    from cubeplex.config import config as settings

    # Snapshot original LLM providers, replace with a single test entry.
    original_llm = dict(settings.get("llm", {}))
    settings.set(
        "llm.providers",
        {
            "vault-seeder-test": {
                "base_url": "https://seeder.example.com",
                "api_key": "seed-1",
                "models": [
                    {
                        "id": "mtest",
                        "name": "M",
                        "context_window": 1000,
                        "max_tokens": 100,
                    }
                ],
            }
        },
    )
    try:
        await seed_system_providers_from_config(db_session, backend)
        await db_session.commit()
        provider1 = (
            await db_session.execute(
                select(Provider).where(
                    Provider.org_id.is_(None),  # type: ignore[union-attr]
                    Provider.name == "vault-seeder-test",  # type: ignore[arg-type]
                )
            )
        ).scalar_one()
        cred_id_1 = provider1.credential_id
        cipher_1 = (await _get_credential(db_session, cred_id_1 or "")).value_encrypted
        assert cred_id_1 is not None

        settings.set(
            "llm.providers",
            {
                "vault-seeder-test": {
                    "base_url": "https://seeder.example.com",
                    "api_key": "seed-2-rotated",
                    "models": [
                        {
                            "id": "mtest",
                            "name": "M",
                            "context_window": 1000,
                            "max_tokens": 100,
                        }
                    ],
                }
            },
        )
        await seed_system_providers_from_config(db_session, backend)
        await db_session.commit()

        db_session.expire_all()
        provider2 = (
            await db_session.execute(
                select(Provider).where(
                    Provider.org_id.is_(None),  # type: ignore[union-attr]
                    Provider.name == "vault-seeder-test",  # type: ignore[arg-type]
                )
            )
        ).scalar_one()
        assert provider2.credential_id == cred_id_1
        cipher_2 = (await _get_credential(db_session, cred_id_1 or "")).value_encrypted
        assert cipher_2 != cipher_1
        plaintext = (await backend.decrypt(cipher_2)).decode()
        assert plaintext == "seed-2-rotated"
    finally:
        # Restore providers and clean up the test row.
        settings.set("llm.providers", original_llm.get("providers", {}))
        await db_session.execute(
            text(
                "DELETE FROM models WHERE provider_id IN ("
                "SELECT id FROM providers WHERE name = 'vault-seeder-test')"
            )
        )
        await db_session.execute(
            text("UPDATE providers SET credential_id = NULL WHERE name = 'vault-seeder-test'")
        )
        await db_session.execute(text("DELETE FROM providers WHERE name = 'vault-seeder-test'"))
        await db_session.execute(
            text(
                "DELETE FROM credentials WHERE name = 'vault-seeder-test'"
                " AND kind = 'provider_api_key'"
            )
        )
        await db_session.commit()
