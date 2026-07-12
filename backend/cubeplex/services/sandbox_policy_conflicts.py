"""OQ-6 credential-host conflict detection.

Shared by the admin policy PUT and the credential editor POST/PATCH routes
so both surfaces emit the same warning shape. Pure-ish: takes a list of
credential rows + a list of network rules; returns warning strings. The
tiny query that fetches the credential rows lives here too so callers
don't reimplement it.
"""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import SandboxEnvVar
from cubeplex.sandbox_env.host_rules import canon_host


async def list_org_credentials_with_hosts(
    session: AsyncSession, *, org_id: str
) -> list[SandboxEnvVar]:
    """Return every SandboxEnvVar in this org whose ``hosts`` is non-null."""
    stmt = select(SandboxEnvVar).where(
        SandboxEnvVar.org_id == org_id,  # type: ignore[arg-type]
        SandboxEnvVar.hosts.is_not(None),  # type: ignore[union-attr]
    )
    return list((await session.execute(stmt)).scalars().all())


def _deny_targets(network_rules: list[dict[str, Any]] | None) -> list[str]:
    return [
        str(r.get("target", ""))
        for r in (network_rules or [])
        if r.get("action") == "deny" and str(r.get("target", ""))
    ]


def _globs_overlap(a: str, b: str) -> bool:
    """True iff ``a`` and ``b`` describe overlapping host sets.

    Both sides can carry wildcards: a credential may declare
    ``*.github.com`` while an admin saves an exact ``api.github.com``
    deny (or vice versa). The runtime blocks any host that either glob
    covers, so the warning has to fire whenever the two patterns can
    name the same host — not just when the credential host fnmatches
    the deny pattern. ``fnmatchcase`` is asymmetric, so we check both
    directions; we also treat exact equality as overlap to cover the
    no-wildcard case fast.
    """
    a = canon_host(a)
    b = canon_host(b)
    if a == b:
        return True
    # Either side can be the pattern; the other side is then a concrete
    # (or already-glob-expressed) value the pattern might cover. If
    # either direction matches, the host sets overlap at runtime.
    if fnmatchcase(a, b):
        return True
    return fnmatchcase(b, a)


def _host_blocked_by(host: str, deny_targets: list[str]) -> str | None:
    """Return the first deny target whose host set overlaps ``host``.

    Wildcard-aware in BOTH directions (see ``_globs_overlap``): admin
    deny ``*.github.com`` covers credential host ``api.github.com``, AND
    admin deny ``api.github.com`` covers credential host ``*.github.com``
    — both shapes must produce a warning because the runtime blocks
    egress in either case.
    """
    for target in deny_targets:
        if _globs_overlap(host, target):
            return target
    return None


def credential_conflict_warnings(
    network_rules: list[dict[str, Any]] | None,
    installed_creds: Iterable[SandboxEnvVar],
) -> list[str]:
    """Warn (do NOT reject) when a deny rule covers a host that an installed
    credential declares as required. One warning per credential×host match.

    Wildcard-aware: ``*.github.com`` denies ``api.github.com``."""
    out: list[str] = []
    deny_targets = _deny_targets(network_rules)
    if not deny_targets:
        return out
    for cred in installed_creds:
        for host in cred.hosts or []:
            matched = _host_blocked_by(host, deny_targets)
            if matched is None:
                continue
            via = "" if matched == host else f" (via deny rule {matched!r})"
            out.append(
                f"credential {cred.id} ({cred.env_name}) requires host "
                f"{host} which is denied by the policy{via}; outbound "
                f"calls will be blocked"
            )
    return out


def deny_targets_for_cred(
    cred_hosts: list[str] | None,
    policy_network_rules: list[dict[str, Any]] | None,
) -> list[str]:
    """Symmetric direction (used by the credential editor route): given a
    credential's hosts and the org's current network_rules, return the subset
    of the cred's hosts that the policy denies (exact or wildcard match)."""
    deny_targets = _deny_targets(policy_network_rules)
    return [h for h in (cred_hosts or []) if _host_blocked_by(h, deny_targets) is not None]
