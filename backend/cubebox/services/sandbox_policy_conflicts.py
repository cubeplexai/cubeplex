"""OQ-6 credential-host conflict detection.

Shared by the admin policy PUT and the credential editor POST/PATCH routes
so both surfaces emit the same warning shape. Pure-ish: takes a list of
credential rows + a list of network rules; returns warning strings. The
tiny query that fetches the credential rows lives here too so callers
don't reimplement it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import SandboxEnvVar


async def list_org_credentials_with_hosts(
    session: AsyncSession, *, org_id: str
) -> list[SandboxEnvVar]:
    """Return every SandboxEnvVar in this org whose ``hosts`` is non-null."""
    stmt = select(SandboxEnvVar).where(
        SandboxEnvVar.org_id == org_id,  # type: ignore[arg-type]
        SandboxEnvVar.hosts.is_not(None),  # type: ignore[union-attr]
    )
    return list((await session.execute(stmt)).scalars().all())


def credential_conflict_warnings(
    network_rules: list[dict[str, Any]] | None,
    installed_creds: Iterable[SandboxEnvVar],
) -> list[str]:
    """Warn (do NOT reject) when a deny rule covers a host that an installed
    credential declares as required. One warning per credential×host match."""
    out: list[str] = []
    deny_targets = {
        str(r.get("target", "")) for r in (network_rules or []) if r.get("action") == "deny"
    }
    if not deny_targets:
        return out
    for cred in installed_creds:
        for host in cred.hosts or []:
            if host in deny_targets:
                out.append(
                    f"credential {cred.id} ({cred.env_name}) requires host "
                    f"{host} which is denied by the policy; outbound calls "
                    f"will be blocked"
                )
    return out


def deny_targets_for_cred(
    cred_hosts: list[str] | None,
    policy_network_rules: list[dict[str, Any]] | None,
) -> list[str]:
    """Symmetric direction (used by the credential editor route): given a
    credential's hosts and the org's current network_rules, return the subset
    of the cred's hosts that the policy explicitly denies."""
    deny_targets = {
        str(r.get("target", "")) for r in (policy_network_rules or []) if r.get("action") == "deny"
    }
    return [h for h in (cred_hosts or []) if h in deny_targets]
