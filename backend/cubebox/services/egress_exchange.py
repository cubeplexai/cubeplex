"""Exchange a placeholder for its real secret, for a verified sidecar only."""

from __future__ import annotations

from collections.abc import Callable

from cubebox.repositories.egress_ref import EgressRefRepository
from cubebox.sandbox_env.exchange_auth import SidecarIdentity
from cubebox.sandbox_env.host_rules import host_matches
from cubebox.sandbox_env.placeholder import hash_placeholder
from cubebox.services.credential import CredentialService
from cubebox.services.sandbox_env import SANDBOX_ENV_KIND


class EgressExchangeError(Exception):
    """Any failure to resolve a placeholder; callers must fail closed."""


class EgressExchangeService:
    def __init__(
        self,
        *,
        ref_repo: EgressRefRepository,
        credentials_factory: Callable[[str], CredentialService],
    ) -> None:
        self._refs = ref_repo
        self._credentials_factory = credentials_factory

    async def exchange(self, *, identity: SidecarIdentity, placeholder: str, host: str) -> str:
        ref = await self._refs.get_valid_by_hash(hash_placeholder(placeholder))
        if ref is None:
            raise EgressExchangeError("unknown/revoked/expired placeholder")
        if ref.sandbox_id != identity.sandbox_id:
            raise EgressExchangeError("sandbox_id mismatch")
        host_norm = host.lower().split(":", 1)[0]
        binding = next((b for b in ref.bindings if host_matches(host_norm, b["hosts"])), None)
        if binding is None:
            raise EgressExchangeError(f"host {host_norm!r} not allowed for this placeholder")
        creds = self._credentials_factory(ref.org_id)
        return await creds.get_decrypted(
            credential_id=binding["credential_id"], requesting_kind=SANDBOX_ENV_KIND
        )
