"""Identity Resolution — shared logic for all SSO and social login flows.

Given an external identity (provider_type, provider_id, external_id, email),
finds or creates the cubebox User and links the ExternalIdentity.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from fastapi_users.manager import BaseUserManager
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.routes.v1.auth import UserCreate
from cubebox.models.external_identity import ExternalIdentity
from cubebox.models.membership import Membership, Role
from cubebox.models.organization_membership import OrganizationMembership, OrgRole
from cubebox.models.sso_connection import SSOConnection
from cubebox.models.user import User
from cubebox.models.workspace import Workspace
from cubebox.repositories.external_identity import ExternalIdentityRepository


class SSOProvisioningDenied(Exception):
    """Raised when auto-provisioning is disabled and user doesn't exist."""


class SSOLoginRejected(Exception):
    """Raised when an SSO/social login is rejected for a structural reason
    (inactive user, deactivated connection, ex-member, unverified email)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ResolvedIdentity:
    user: User
    external_identity: ExternalIdentity
    created: bool  # True if user was just created


async def resolve_identity(
    session: AsyncSession,
    *,
    user_manager: BaseUserManager[User, str],
    provider_type: str,
    provider_id: str,
    external_id: str,
    external_email: str,
    email_verified: bool,
    claims: dict[str, Any] | None = None,
    sso_connection: SSOConnection | None = None,
) -> ResolvedIdentity:
    """Find or create a user for the given external identity.

    Callers must apply attribute_mapping and IdP-side email verification
    BEFORE calling this. ``email_verified`` reflects the IdP's signal
    (OIDC ``email_verified`` claim / SAML signed-assertion email).

    1. Look up ExternalIdentity by (provider_type, provider_id, external_id).
       Found → re-check the linked user is still allowed to sign in via
       this provider (org membership + connection status). Pass → return.
    2. Not found AND email_verified → look up User by email.
       a. User exists → create ExternalIdentity link.
       b. User doesn't exist →
          - Enterprise SSO: check provisioning policy, then create the
            user via UserManager.create() so the existing
            on_after_register bootstrap fires.
          - Social login (sso_connection is None): create the user via
            UserManager.create() — bootstrap creates the user's personal
            org and workspace.
    3. Not found AND NOT email_verified → reject (no auto-link, no
       auto-provision). Prevents account takeover via untrusted email.
    """
    repo = ExternalIdentityRepository(session)

    existing = await repo.find_by_external(
        provider_type=provider_type,
        provider_id=provider_id,
        external_id=external_id,
    )
    if existing is not None:
        user = (
            await session.execute(
                select(User).where(User.id == existing.user_id)  # type: ignore[arg-type]
            )
        ).scalar_one_or_none()
        if user is None or not user.is_active:
            raise SSOLoginRejected("user_inactive")

        if sso_connection is not None:
            if sso_connection.status not in {"active", "testing"}:
                raise SSOLoginRejected("sso_connection_inactive")
            still_member = (
                await session.execute(
                    select(OrganizationMembership).where(
                        OrganizationMembership.user_id == user.id,  # type: ignore[arg-type]
                        OrganizationMembership.org_id == sso_connection.org_id,  # type: ignore[arg-type]
                    )
                )
            ).scalar_one_or_none()
            if still_member is None:
                raise SSOLoginRejected("not_org_member")

        existing.external_email = external_email
        existing.metadata_ = claims or {}
        session.add(existing)
        await session.commit()
        return ResolvedIdentity(user=user, external_identity=existing, created=False)

    if not email_verified:
        raise SSOLoginRejected("email_not_verified")

    user = (
        await session.execute(
            select(User).where(User.email == external_email)  # type: ignore[arg-type]
        )
    ).scalar_one_or_none()

    created = False
    if user is None:
        if sso_connection is not None and sso_connection.provisioning == "invite_only":
            raise SSOProvisioningDenied(
                "Auto-provisioning is disabled for this organization. Contact your administrator."
            )
        display_name = (claims or {}).get("name") or external_email.split("@")[0]
        # Delegate to UserManager so on_after_register bootstraps the user
        # (orgs, workspace, agent config, audit, etc.). For SSO-managed
        # accounts the password is a server-side random — the user can never
        # use it because forced-SSO will block password login anyway.
        #
        # safe=False: this caller IS server-trusted (the IdP just attested
        # the user), so is_verified=True is allowed to land. safe=True
        # strips is_verified/is_active/is_superuser via
        # CreateUpdateDictModel.create_update_dict() and would leave every
        # SSO-provisioned user unverified.
        user = await user_manager.create(
            UserCreate(
                email=external_email,
                password=secrets.token_urlsafe(32),
                display_name=display_name,
                is_verified=True,
            ),
            safe=False,
        )
        created = True

        if sso_connection is not None:
            await _provision_org_membership(session, user, sso_connection)

    identity = ExternalIdentity(
        user_id=user.id,
        provider_type=provider_type,
        provider_id=provider_id,
        external_id=external_id,
        external_email=external_email,
        metadata_=claims or {},
    )
    session.add(identity)
    await session.commit()
    await session.refresh(user)
    return ResolvedIdentity(user=user, external_identity=identity, created=created)


async def _provision_org_membership(
    session: AsyncSession,
    user: User,
    sso_connection: SSOConnection,
) -> None:
    """Add user to the SSO connection's org with MEMBER role + first workspace."""
    org_id = sso_connection.org_id

    existing_membership = (
        await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user.id,  # type: ignore[arg-type]
                OrganizationMembership.org_id == org_id,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if existing_membership is not None:
        return

    org_membership = OrganizationMembership(
        user_id=user.id,
        org_id=org_id,
        role=OrgRole.MEMBER,
    )
    session.add(org_membership)

    first_workspace = (
        await session.execute(
            select(Workspace)
            .where(
                Workspace.org_id == org_id,  # type: ignore[arg-type]
                Workspace.archived_at.is_(None),  # type: ignore[union-attr]
            )
            .order_by(Workspace.created_at)  # type: ignore[arg-type]
            .limit(1)
        )
    ).scalar_one_or_none()

    if first_workspace is not None:
        ws_membership = Membership(
            user_id=user.id,
            workspace_id=first_workspace.id,
            role=Role.MEMBER,
        )
        session.add(ws_membership)
