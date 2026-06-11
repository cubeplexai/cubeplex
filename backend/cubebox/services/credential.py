"""Credential vault service for internal backend consumers."""

from typing import Any

from cubebox.credentials.encryption import EncryptionBackend
from cubebox.credentials.exceptions import (
    CredentialInUseError,
    CredentialKindMismatch,
    CredentialNotFound,
)
from cubebox.models import Credential
from cubebox.repositories.credential import CredentialRepository


class CredentialService:
    """Internal-only API for encrypted credential CRUD."""

    def __init__(
        self,
        repo: CredentialRepository,
        backend: EncryptionBackend,
        *,
        org_id: str | None,
        actor_user_id: str | None,
    ) -> None:
        self._repo = repo
        self._backend = backend
        self._org_id = org_id
        self._actor_user_id = actor_user_id

    async def create(
        self,
        *,
        kind: str,
        name: str,
        plaintext: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        ciphertext = await self._backend.encrypt(plaintext.encode("utf-8"))
        cred = Credential(
            org_id=self._org_id,
            kind=kind,
            name=name,
            value_encrypted=ciphertext,
            cred_metadata=metadata or {},
            created_by_user_id=self._actor_user_id,
        )
        saved = await self._repo.add(cred)
        return saved.id

    async def get_decrypted_system(self, *, credential_id: str, requesting_kind: str) -> str:
        """Decrypt a SYSTEM (org_id NULL) credential regardless of caller org scope.

        System providers' api keys live at ``org_id=NULL`` and are invisible to an
        org-scoped repo, but an org admin must be able to test/use those providers.
        The kind check is preserved.
        """
        sys_repo = CredentialRepository(self._repo.session, org_id=None)
        cred = await sys_repo.get(credential_id)
        if cred is None:
            raise CredentialNotFound(credential_id)
        if cred.kind != requesting_kind:
            raise CredentialKindMismatch(
                f"credential kind={cred.kind} but caller requested kind={requesting_kind}"
            )
        plaintext = await self._backend.decrypt(cred.value_encrypted)
        return plaintext.decode("utf-8")

    async def get_decrypted(self, *, credential_id: str, requesting_kind: str) -> str:
        cred = await self._repo.get(credential_id)
        if cred is None:
            raise CredentialNotFound(credential_id)
        if cred.kind != requesting_kind:
            raise CredentialKindMismatch(
                f"credential kind={cred.kind} but caller requested kind={requesting_kind}"
            )
        plaintext = await self._backend.decrypt(cred.value_encrypted)
        return plaintext.decode("utf-8")

    async def update(
        self,
        *,
        credential_id: str,
        plaintext: str | None = None,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        cred = await self._repo.get(credential_id)
        if cred is None:
            raise CredentialNotFound(credential_id)
        if plaintext is not None:
            cred.value_encrypted = await self._backend.encrypt(plaintext.encode("utf-8"))
        if name is not None:
            cred.name = name
        if metadata is not None:
            cred.cred_metadata = metadata
        await self._repo.update(cred)

    async def upsert_by_kind_name(
        self,
        *,
        kind: str,
        name: str,
        plaintext: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Rotate an existing ``(kind, name)`` row in scope, or create one.

        The credentials table has a partial unique constraint on
        ``(org_id, kind, name)``; on re-OAuth, blindly calling ``create``
        with the same name a second time hits ``uq_credential_org_kind_name``
        and the whole callback fails. Callers that legitimately re-issue a
        named credential (OAuth access/refresh rotation) should use this
        method instead of ``create``.
        """
        existing = await self._repo.get_by_kind_name(kind=kind, name=name)
        if existing is None:
            return await self.create(kind=kind, name=name, plaintext=plaintext, metadata=metadata)
        existing.value_encrypted = await self._backend.encrypt(plaintext.encode("utf-8"))
        if metadata is not None:
            existing.cred_metadata = metadata
        await self._repo.update(existing)
        return existing.id

    async def delete(self, *, credential_id: str) -> None:
        cred = await self._repo.get(credential_id)
        if cred is None:
            raise CredentialNotFound(credential_id)
        await self._guard_references(credential_id)
        await self._repo.delete(credential_id)

    async def _guard_references(self, credential_id: str) -> None:
        """Refuse deletion while other rows still reference the credential."""
        from sqlalchemy import or_, select

        from cubebox.models import MCPCredentialGrant
        from cubebox.models.provider import Provider

        session = self._repo.session
        if self._org_id is not None:
            grant_refs = (
                (
                    await session.execute(
                        select(MCPCredentialGrant).where(
                            MCPCredentialGrant.org_id == self._org_id,  # type: ignore[arg-type]
                            or_(
                                MCPCredentialGrant.credential_id == credential_id,  # type: ignore[arg-type]
                                MCPCredentialGrant.refresh_credential_id  # type: ignore[arg-type]
                                == credential_id,
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if grant_refs:
                reference_ids = [grant.id for grant in grant_refs]
                raise CredentialInUseError(
                    f"credential {credential_id} referenced by MCPCredentialGrant: {reference_ids}"
                )
        provider_refs = (
            (
                await session.execute(
                    select(Provider).where(
                        Provider.credential_id == credential_id  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        if provider_refs:
            raise CredentialInUseError(
                f"credential {credential_id} referenced by Provider: "
                f"{[p.id for p in provider_refs]}"
            )
        from cubebox.models import SandboxEnvVar

        env_refs = (
            (
                await session.execute(
                    select(SandboxEnvVar).where(
                        SandboxEnvVar.credential_id == credential_id  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        if env_refs:
            raise CredentialInUseError(
                f"credential {credential_id} referenced by SandboxEnvVar: {[e.id for e in env_refs]}"
            )
        from cubebox.models import IMConnectorAccount

        im_refs = (
            (
                await session.execute(
                    select(IMConnectorAccount).where(
                        IMConnectorAccount.credential_id == credential_id  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        if im_refs:
            raise CredentialInUseError(
                f"credential {credential_id} referenced by IMConnectorAccount: "
                f"{[a.id for a in im_refs]}"
            )
