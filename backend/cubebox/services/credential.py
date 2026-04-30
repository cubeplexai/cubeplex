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
        org_id: str,
        actor_user_id: str,
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

    async def delete(self, *, credential_id: str) -> None:
        cred = await self._repo.get(credential_id)
        if cred is None:
            raise CredentialNotFound(credential_id)
        await self._guard_references(credential_id)
        await self._repo.delete(credential_id)

    async def _guard_references(self, credential_id: str) -> None:
        """Refuse deletion while MCP tables still reference the credential."""
        from cubebox.repositories.mcp import (
            MCPServerRepository,
            UserMCPCredentialRepository,
            WorkspaceMCPCredentialRepository,
        )

        session = self._repo.session
        for repo_class in (
            MCPServerRepository,
            WorkspaceMCPCredentialRepository,
            UserMCPCredentialRepository,
        ):
            repo = repo_class(session, org_id=self._org_id)
            references = await repo.find_by_credential_id(credential_id)
            if references:
                reference_ids = [getattr(reference, "id", "?") for reference in references]
                raise CredentialInUseError(
                    f"credential {credential_id} referenced by "
                    f"{repo_class.__name__}: {reference_ids}"
                )
