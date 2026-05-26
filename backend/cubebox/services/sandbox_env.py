"""Sandbox Env Vault service: CRUD + validation + scope-precedence resolution."""

from __future__ import annotations

from dataclasses import dataclass

from cubebox.models import SandboxEnvVar
from cubebox.repositories.sandbox_env import SandboxEnvRepository
from cubebox.sandbox_env.host_rules import validate_hosts
from cubebox.services.credential import CredentialService

SANDBOX_ENV_KIND = "sandbox_env"
_SCOPE_RANK = {"user": 3, "workspace": 2, "org": 1}


class SandboxEnvShapeError(ValueError):
    """Raised on invalid scope shape or value shape."""


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
        if plain_value is None:
            raise SandboxEnvShapeError("plain entry requires plain_value")
        # Use ``is not None`` (not truthiness): hosts=[] is falsy but the model
        # CHECK requires hosts IS NULL for plain rows, so [] must be a 400 here,
        # not a DB integrity 500.
        if secret_value is not None or hosts is not None:
            raise SandboxEnvShapeError("plain entry forbids secret_value/hosts")


@dataclass
class ResolvedEnv:
    env_name: str
    is_secret: bool
    hosts: list[str] | None
    header_names: list[str] | None
    credential_id: str | None
    plain_value: str | None


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
        plain_value: str | None,
    ) -> str:
        _validate_scope_shape(scope, workspace_id, user_id)
        _validate_value_shape(is_secret, hosts, secret_value, plain_value)

        credential_id: str | None = None
        if is_secret:
            assert secret_value is not None  # guaranteed by value-shape validation
            credential_id = await self._credentials.create(
                kind=SANDBOX_ENV_KIND,
                name=f"{scope}:{workspace_id or '-'}:{user_id or '-'}:{env_name}",
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
            plain_value=plain_value,
            created_by_user_id=self._actor_user_id,
        )
        try:
            saved = await self._repo.add(row)
        except Exception:
            # The credential is committed before the row insert; if the insert
            # fails (duplicate partial-unique index, FK, CHECK), delete the
            # now-orphaned credential so we don't leave a dangling secret.
            if credential_id is not None:
                await self._credentials.delete(credential_id=credential_id)
            raise
        return saved.id

    async def update_secret_value(self, *, entry_id: str, secret_value: str) -> None:
        row = await self._repo.get(entry_id)
        if row is None or not row.is_secret or row.credential_id is None:
            raise SandboxEnvShapeError(f"no secret entry {entry_id}")
        await self._credentials.update(credential_id=row.credential_id, plaintext=secret_value)

    async def delete_entry(self, *, entry_id: str) -> None:
        row = await self._repo.get(entry_id)
        if row is None:
            return
        await self._repo.delete(entry_id)
        if row.is_secret and row.credential_id is not None:
            await self._credentials.delete(credential_id=row.credential_id)
