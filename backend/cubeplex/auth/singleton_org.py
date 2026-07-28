"""Singleton-org helpers for single_tenant mode.

The advisory lock serializes the pending-owner window and the /setup write,
so concurrent /register POSTs don't both reach `org_count == 0` and try to
create the singleton.
"""

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import Organization, User

_ADVISORY_LOCK_KEY = "cubeplex-singleton-org-setup"


async def acquire_setup_lock(session: AsyncSession) -> bool:
    """Try to acquire the transaction-scoped advisory lock. Returns False if held."""
    row = await session.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:k))").bindparams(k=_ADVISORY_LOCK_KEY)
    )
    return bool(row.scalar_one())


async def get_singleton_org_id(session: AsyncSession) -> str | None:
    """Return the singleton org id, or None if no orgs exist."""
    org = (
        (await session.execute(select(Organization).order_by(text("created_at")))).scalars().first()
    )
    return org.id if org else None


async def org_count(session: AsyncSession) -> int:
    """Return the total number of organizations."""
    return int((await session.execute(select(func.count()).select_from(Organization))).scalar_one())


async def user_count(session: AsyncSession) -> int:
    """Return the total number of registered users."""
    return int((await session.execute(select(func.count()).select_from(User))).scalar_one())


class MultiOrgNotLicensedError(Exception):
    """A second org was requested but the license lacks the multi_org feature."""


class OrgSetupInProgressError(Exception):
    """Another request holds the singleton-org setup lock."""


async def ensure_additional_org_allowed(session: AsyncSession) -> None:
    """Gate org creation beyond the first. single_tenant callers only.

    OSS self-hosting is one organization; running several isolated orgs in one
    deployment is an EE feature. Bootstrapping the first org is never gated.

    Takes the same advisory lock the registration path uses, because counting and
    creating have to be one decision: two concurrent full-mode onboardings with
    different slugs would otherwise both read zero orgs and create one each, and
    the unique constraint on the slug would not catch it. The lock is
    transaction-scoped, so it is held until the org insert commits.
    """
    from cubeplex.plugins.license import FEATURE_MULTI_ORG, has_feature

    if has_feature(FEATURE_MULTI_ORG):
        return  # Unlimited orgs — nothing to serialize.
    if not await acquire_setup_lock(session):
        raise OrgSetupInProgressError
    if await org_count(session) > 0:
        raise MultiOrgNotLicensedError
