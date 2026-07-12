"""Sandbox Env Vault service: CRUD + validation + scope-precedence resolution."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from cubeplex.models import SandboxEnvVar
from cubeplex.repositories.sandbox_env import SandboxEnvRepository
from cubeplex.sandbox_env.host_rules import validate_hosts
from cubeplex.services.credential import CredentialService

SANDBOX_ENV_KIND = "sandbox_env"
_SCOPE_RANK = {"user": 3, "workspace": 2, "org": 1}


class SandboxEnvShapeError(ValueError):
    """Raised on invalid scope shape or value shape."""


class SandboxEnvConflictError(SandboxEnvShapeError):
    """Raised when an entry with the same scope+env_name already exists."""


def _validate_scope_shape(scope: str, workspace_id: str | None, user_id: str | None) -> None:
    if scope == "org":
        if workspace_id is not None or user_id is not None:
            raise SandboxEnvShapeError("scope='org' requires workspace_id=None, user_id=None")
    elif scope == "workspace":
        if workspace_id is None or user_id is not None:
            raise SandboxEnvShapeError("scope='workspace' requires workspace_id, forbids user_id")
    elif scope == "user":
        if workspace_id is None or user_id is None:
            raise SandboxEnvShapeError("scope='user' requires workspace_id and user_id")
    else:
        raise SandboxEnvShapeError(f"unknown scope: {scope!r}")


def _validate_value_shape(
    is_secret: bool, hosts: list[str] | None, secret_value: str | None, plain_value: str | None
) -> None:
    if is_secret:
        if not hosts:
            raise SandboxEnvShapeError("secret entry requires non-empty hosts")
        if secret_value is None:
            raise SandboxEnvShapeError("secret entry requires secret_value")
        if plain_value is not None:
            raise SandboxEnvShapeError("secret entry forbids plain_value")
        validate_hosts(hosts)  # raises HostPatternError (incl. regex-only rejection)
    else:
        if secret_value is None:
            raise SandboxEnvShapeError("plain entry requires secret_value")
        if plain_value is not None:
            raise SandboxEnvShapeError("plain entry forbids plain_value")
        if hosts is not None:
            raise SandboxEnvShapeError("plain entry forbids hosts")


@dataclass
class ResolvedEnv:
    id: str
    env_name: str
    is_secret: bool
    hosts: list[str] | None
    header_names: list[str] | None
    credential_id: str | None
    value: str | None = None  # decrypted at inject time by manager; None until then


class SandboxEnvService:
    def __init__(
        self,
        *,
        repo: SandboxEnvRepository,
        credentials: CredentialService,
        org_id: str,
        actor_user_id: str | None,
    ) -> None:
        self._repo = repo
        self._credentials = credentials
        self._org_id = org_id
        self._actor_user_id = actor_user_id

    async def create_entry(
        self,
        *,
        env_name: str,
        is_secret: bool,
        scope: str,
        workspace_id: str | None,
        user_id: str | None,
        hosts: list[str] | None,
        header_names: list[str] | None,
        secret_value: str | None,
    ) -> str:
        _validate_scope_shape(scope, workspace_id, user_id)
        _validate_value_shape(is_secret, hosts, secret_value, None)

        # Preflight conflict check — before creating any credential so there is
        # nothing to roll back on a name collision.
        existing = await self._repo.get_in_scope(
            scope=scope,
            env_name=env_name,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        if existing is not None:
            raise SandboxEnvConflictError(
                f"env entry {env_name!r} already exists in scope={scope!r}"
            )

        assert secret_value is not None  # guaranteed by value-shape validation for both types
        _ident = f"{scope}:{workspace_id or '-'}:{user_id or '-'}:{env_name}"
        _cred_name = f"sandbox_env:{hashlib.sha256(_ident.encode()).hexdigest()}"  # 76 chars
        credential_id = await self._credentials.create(
            kind=SANDBOX_ENV_KIND,
            name=_cred_name,
            plaintext=secret_value,
        )

        row = SandboxEnvVar(
            org_id=self._org_id,
            env_name=env_name,
            is_secret=is_secret,
            scope=scope,
            workspace_id=workspace_id,
            user_id=user_id,
            hosts=hosts,
            header_names=header_names,
            credential_id=credential_id,
            created_by_user_id=self._actor_user_id,
        )
        try:
            saved = await self._repo.add(row)
        except Exception:
            # The credential is committed before the row insert; if the insert
            # fails (e.g. FK, CHECK constraint), roll back the broken session
            # first then delete the now-orphaned credential.
            if credential_id is not None:
                await self._repo.session.rollback()
                await self._credentials.delete(credential_id=credential_id)
            raise
        return saved.id

    async def update_value(self, *, entry_id: str, secret_value: str) -> None:
        row = await self._repo.get(entry_id)
        if row is None or row.credential_id is None:
            raise SandboxEnvShapeError(f"no entry with credential {entry_id}")
        await self._credentials.update(credential_id=row.credential_id, plaintext=secret_value)

    async def update_entry(
        self,
        *,
        entry_id: str,
        hosts: list[str] | None = None,
        header_names: list[str] | None = None,
        update_header_names: bool = False,
        secret_value: str | None = None,
    ) -> None:
        """Update hosts/header_names and/or rotate the credential value.

        ``hosts`` and ``header_names`` are only applicable to secret entries.
        ``update_header_names`` must be True to write ``header_names``; this
        separates "explicitly set to None (clear restriction)" from "omitted
        (leave unchanged)".  Callers should pass
        ``update_header_names='header_names' in body.model_fields_set``.
        """
        if hosts is None and not update_header_names and secret_value is None:
            raise SandboxEnvShapeError("update_entry: at least one field must be provided")
        row = await self._repo.get(entry_id)
        if row is None:
            raise SandboxEnvShapeError(f"entry {entry_id!r} not found")

        if hosts is not None or update_header_names:
            if not row.is_secret:
                raise SandboxEnvShapeError(
                    "hosts/header_names are only applicable to secret entries"
                )
            if hosts is not None:
                validate_hosts(hosts)
                row.hosts = hosts
            if update_header_names:
                # None means "allow any header"; an empty list is normalised to None.
                row.header_names = header_names or None
            await self._repo.update(row)

        if secret_value is not None:
            if row.credential_id is None:
                raise SandboxEnvShapeError(f"entry {entry_id!r} has no credential")
            await self._credentials.update(credential_id=row.credential_id, plaintext=secret_value)

    async def delete_entry(self, *, entry_id: str) -> None:
        row = await self._repo.get(entry_id)
        if row is None:
            return
        await self._repo.delete(entry_id)
        if row.credential_id is not None:
            await self._credentials.delete(credential_id=row.credential_id)


class SandboxEnvResolver:
    """Resolve the effective env set for (workspace, user) by scope precedence."""

    def __init__(self, repo: SandboxEnvRepository) -> None:
        self._repo = repo

    async def resolve(self, *, workspace_id: str, user_id: str) -> list[ResolvedEnv]:
        rows = await self._repo.list_for_resolution(workspace_id=workspace_id, user_id=user_id)
        best: dict[str, SandboxEnvVar] = {}
        for row in rows:
            cur = best.get(row.env_name)
            if cur is None or _SCOPE_RANK[row.scope] > _SCOPE_RANK[cur.scope]:
                best[row.env_name] = row
        return [
            ResolvedEnv(
                id=r.id,
                env_name=r.env_name,
                is_secret=r.is_secret,
                hosts=r.hosts,
                header_names=r.header_names,
                credential_id=r.credential_id,
            )
            for r in best.values()
        ]
