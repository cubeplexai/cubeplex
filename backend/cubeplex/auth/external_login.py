"""Finishing a login that arrived from an external identity provider.

Both Google social login (open source) and enterprise SAML/OIDC (licensed) end
the same way: check that the user isn't required to use SSO for some other org,
then issue the ordinary session cookie and send the browser to a workspace.

This lives in core because the open-source Google flow depends on it. Keeping it
in the SSO package would mean core importing the licensed distribution — and
``enforce_forced_sso_for_user`` is a security control, so that is the wrong
direction for it to point.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi_users.authentication import Strategy
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.jwt import auth_backend
from cubeplex.config import config
from cubeplex.models.membership import Membership
from cubeplex.models.organization_membership import OrganizationMembership
from cubeplex.models.sso_connection import SSOConnection
from cubeplex.models.user import User
from cubeplex.models.workspace import Workspace

logger = logging.getLogger(__name__)

# The log names a few orgs and counts the rest; the return value carries them all.
_MAX_ORGS_IN_LOG = 5


def _summarize(org_ids: list[str]) -> str:
    if len(org_ids) <= _MAX_ORGS_IN_LOG:
        return ", ".join(org_ids)
    return f"{', '.join(org_ids[:_MAX_ORGS_IN_LOG])}, and {len(org_ids) - _MAX_ORGS_IN_LOG} more"


def frontend_base_url() -> str:
    """Frontend origin — for browser redirects that land on a Next.js page
    (post-login workspace home, SSO error page). Distinct from the backend
    origin used for IdP redirect URIs: they differ whenever the backend is
    reached via a proxy or ingress that isn't the origin serving the SPA."""
    url = str(config.get("frontend_base_url", "http://localhost:3000")).rstrip("/")
    if "://" not in url:
        raise HTTPException(500, detail={"code": "app_base_url_missing_scheme"})
    return url


async def report_unserviceable_sso(session: AsyncSession) -> list[str]:
    """Log loudly when SSO is configured but nothing can serve it.

    Returns every affected org id — all of them, not the handful the log names —
    so a caller can act on the set and a test can assert about one org without
    depending on what else is in the database. Empty when all is well.

    Reports ``active`` and ``testing`` separately: only ``active`` makes
    ``enforce_forced_sso_for_user`` refuse a password, so only those users are
    locked out entirely. Conflating the two sends an operator to disable a
    connection that is costing them nothing.

    Removing the licensed package does not remove its rows — the table is core
    owned and shares the migration lineage. A deployment that downgrades with a
    live connection strands that org: ``enforce_forced_sso_for_user`` still
    refuses their password, the login page still advertises SSO because
    ``org-info`` reports it, and the routes that would serve it are gone.

    **This logs rather than refusing to start, reversing the first draft.** The
    argument for failing fast was that ignoring the row silently switches forced
    SSO off. It does not: the row is still there and ``enforce_forced_sso_for_user``
    still reads it, so the security control keeps working either way. What
    refusing to start actually buys is operator attention — which an ERROR naming
    the orgs and both fixes also buys — and what it costs is every *other* org in
    a multi-tenant deployment going down for one stranded one. A stale row must
    not be able to brick a boot.

    Unlike the check in ``cubeplex.plugins.ee``, which does refuse to start: there,
    the package is present and running unlicensed code, and there is no partial
    state to preserve.
    """
    from cubeplex.plugins.ee import is_ee_installed

    if is_ee_installed():
        return []

    rows = (
        await session.execute(
            select(SSOConnection.org_id, SSOConnection.status).where(  # type: ignore[call-overload]
                SSOConnection.status.in_(["active", "testing"])  # type: ignore[attr-defined]
            )
        )
    ).all()
    if not rows:
        return []

    # Split by status, because the two consequences differ and an operator acting
    # on this message needs to know which one they have. enforce_forced_sso_for_user
    # refuses passwords for `active` only, so a `testing` org still has working
    # password login — it has merely lost a connection it was validating, and
    # disabling it is not urgent.
    stranded = sorted({str(r[0]) for r in rows if str(r[1]) == "active"})
    testing_only = sorted({str(r[0]) for r in rows if str(r[1]) == "testing"} - set(stranded))

    if stranded:
        logger.error(
            "%d organization(s) have SSO active (%s) but the cubeplex-ee distribution "
            "is not installed. No route can serve their SSO, and password login is "
            "still refused for their members, so those users cannot sign in at all. "
            "Reinstall cubeplex-ee with a valid license key, or run "
            "`cubeplex-cli admin disable-sso <org-slug>` for each org to restore "
            "password login.",
            len(stranded),
            _summarize(stranded),
        )
    if testing_only:
        logger.error(
            "%d organization(s) have SSO in testing (%s) but the cubeplex-ee "
            "distribution is not installed, so those connections cannot be "
            "exercised. Password login for their members is unaffected.",
            len(testing_only),
            _summarize(testing_only),
        )
    return sorted(set(stranded) | set(testing_only))


async def enforce_forced_sso_for_user(
    session: AsyncSession,
    user: User,
    *,
    allowed_org_id: str | None,
) -> None:
    """Reject login when the user belongs to any org with active forced SSO
    and the current login flow didn't use SSO for one of those orgs.

    Policy:
    - Password login and Google social login pass ``allowed_org_id=None``;
      if the user belongs to any forced-SSO org, reject.
    - Enterprise SSO callbacks pass ``allowed_org_id=conn.org_id``; if
      that org is one of the user's forced-SSO orgs, this login satisfies
      enforcement and is allowed. (Without this, a user in TWO forced-SSO
      orgs could never log in — strict per-org enforcement is impossible
      because the JWT cookie is global.)
    - If the SSO callback is for an org the user is NOT a forced-SSO
      member of (cross-org), enforcement still blocks the login so that
      a user in forced-SSO Org A cannot authenticate through some
      unrelated Org B's SSO.

    Reads ``sso_connection`` directly rather than going through the licensed
    package: the table is core-owned, and this guard has to work even when that
    package is not installed. On a default install the query finds nothing, and
    :func:`report_unserviceable_sso` is what rules out the case where rows exist
    without the package that serves them.
    """
    rows = (
        await session.execute(
            select(SSOConnection.org_id)  # type: ignore[call-overload]
            .join(
                OrganizationMembership,
                OrganizationMembership.org_id == SSOConnection.org_id,
            )
            .where(
                OrganizationMembership.user_id == user.id,
                SSOConnection.status == "active",
            )
        )
    ).all()
    forced_orgs = {row[0] for row in rows}
    if not forced_orgs:
        return
    if allowed_org_id is not None and allowed_org_id in forced_orgs:
        return
    raise HTTPException(
        403,
        detail={
            "code": "sso_required",
            "message": "Your organization requires SSO login.",
        },
    )


async def login_and_redirect(request: Request, session: AsyncSession, user: User) -> Response:
    """Issue the JWT cookie and redirect to the frontend workspace home."""
    strategy: Strategy[User, str] = auth_backend.get_strategy()  # type: ignore[assignment]
    login_response = await auth_backend.login(strategy, user)

    # Pick a workspace the user is actually a member of. Filtering by
    # Membership.user_id is critical — picking any workspace in the org
    # would land just-provisioned SSO users into an unrelated workspace.
    ws = (
        await session.execute(
            select(Workspace)
            .join(Membership, Membership.workspace_id == Workspace.id)  # type: ignore[arg-type]
            .where(
                Membership.user_id == user.id,  # type: ignore[arg-type]
                Workspace.archived_at.is_(None),  # type: ignore[union-attr]
            )
            .order_by(Workspace.created_at)  # type: ignore[arg-type]
            .limit(1)
        )
    ).scalar_one_or_none()

    base = frontend_base_url()
    redirect_to = f"{base}/w/{ws.id}" if ws else base

    redirect_resp = RedirectResponse(url=redirect_to, status_code=302)
    for header_name in ("set-cookie",):
        values = login_response.headers.getlist(header_name)
        for v in values:
            redirect_resp.headers.append(header_name, v)
    return redirect_resp
