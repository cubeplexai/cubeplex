"""Exchange a placeholder for its real secret, for a verified sidecar only."""

from __future__ import annotations

from collections.abc import Callable

from cubeplex.credentials.exceptions import CredentialKindMismatch, CredentialNotFound
from cubeplex.repositories.egress_ref import EgressRefRepository
from cubeplex.repositories.sandbox_env import SandboxEnvRepository
from cubeplex.sandbox_env.exchange_auth import SidecarIdentity
from cubeplex.sandbox_env.host_rules import host_matches
from cubeplex.sandbox_env.placeholder import hash_placeholder
from cubeplex.services.credential import CredentialService
from cubeplex.services.sandbox_env import SANDBOX_ENV_KIND


class EgressExchangeError(Exception):
    """Any failure to resolve a placeholder; callers must fail closed."""


class EgressExchangeService:
    def __init__(
        self,
        *,
        ref_repo: EgressRefRepository,
        credentials_factory: Callable[[str], CredentialService],
        env_var_repo_factory: Callable[[str], SandboxEnvRepository] | None = None,
    ) -> None:
        self._refs = ref_repo
        self._credentials_factory = credentials_factory
        self._env_var_repo_factory = env_var_repo_factory

    async def exchange(
        self, *, identity: SidecarIdentity, placeholder: str, host: str
    ) -> tuple[str, list[str] | None]:
        """Return (secret, header_names) for the matched binding.

        When the binding carries an ``env_var_id`` the service does a live DB
        lookup so that user edits to hosts/header_names take effect immediately
        without requiring a sandbox restart or a new ``_apply_egress`` call.
        Bindings created before this change (no ``env_var_id``) fall back to
        the snapshot embedded in the EgressRef.
        """
        ref = await self._refs.get_valid_by_hash(hash_placeholder(placeholder))
        if ref is None:
            raise EgressExchangeError("unknown/revoked/expired placeholder")
        if ref.sandbox_id != identity.sandbox_id:
            raise EgressExchangeError("sandbox_id mismatch")
        host_norm = host.lower().split(":", 1)[0]

        matched_binding = None
        matched_credential_id: str | None = None
        matched_header_names: list[str] | None = None

        for b in ref.bindings:
            env_var_id: str | None = b.get("env_var_id")
            if env_var_id and self._env_var_repo_factory is not None:
                # Live lookup: use current hosts/header_names/credential_id from DB.
                repo = self._env_var_repo_factory(ref.org_id)
                env_var = await repo.get(env_var_id)
                if env_var is None:
                    # Entry deleted while sandbox was running — fail closed for this binding.
                    continue
                if not host_matches(host_norm, env_var.hosts or []):
                    continue
                matched_binding = b
                matched_credential_id = env_var.credential_id
                matched_header_names = env_var.header_names
            else:
                # Backward compat: snapshot-based lookup for old EgressRefs.
                if not host_matches(host_norm, b.get("hosts") or []):
                    continue
                matched_binding = b
                matched_credential_id = b.get("credential_id")
                matched_header_names = b.get("header_names")
            break

        if matched_binding is None:
            raise EgressExchangeError(f"host {host_norm!r} not allowed for this placeholder")
        if matched_credential_id is None:
            raise EgressExchangeError("bound credential is gone")

        creds = self._credentials_factory(ref.org_id)
        try:
            secret = await creds.get_decrypted(
                credential_id=matched_credential_id, requesting_kind=SANDBOX_ENV_KIND
            )
        except (CredentialNotFound, CredentialKindMismatch) as exc:
            raise EgressExchangeError("bound credential is gone") from exc
        return secret, matched_header_names
