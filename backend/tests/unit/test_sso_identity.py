"""Unit tests for SSO identity resolution.

Uses real ``UserManager`` against an in-memory SQLite session so the full
``on_after_register`` bootstrap (org + workspace + memberships + agent
config + MCP enrollment + skill install) fires for the happy paths.
"""

from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.users import UserManager
from cubeplex.models import ExternalIdentity, Organization, User
from cubeplex.models.user import AvatarKind
from cubeplex.sso.identity import (
    EnterpriseLoginPolicy,
    SSOLoginRejected,
    SSOProvisioningDenied,
    resolve_identity,
)


@pytest.mark.asyncio
async def test_resolve_creates_user_for_social_login(
    sso_session: AsyncSession, sso_user_manager: UserManager
) -> None:
    result = await resolve_identity(
        sso_session,
        user_manager=sso_user_manager,
        provider_type="google",
        provider_id="google",
        external_id="google-sub-123",
        external_email="new@example.com",
        email_verified=True,
        claims={"name": "New User"},
    )
    assert result.created is True
    assert result.user.email == "new@example.com"
    assert result.user.display_name == "New User"
    assert result.external_identity.external_id == "google-sub-123"
    assert result.external_identity.provider_type == "google"


@pytest.mark.asyncio
async def test_resolve_rejects_unverified_email_for_new_user(
    sso_session: AsyncSession, sso_user_manager: UserManager
) -> None:
    """Account-takeover guard: cannot create or link without email_verified."""
    with pytest.raises(SSOLoginRejected) as exc_info:
        await resolve_identity(
            sso_session,
            user_manager=sso_user_manager,
            provider_type="oidc_sso",
            provider_id="acme-sso",
            external_id="oidc-sub-attack",
            external_email="victim@corp.com",
            email_verified=False,
        )
    assert exc_info.value.code == "email_not_verified"


@pytest.mark.asyncio
async def test_resolve_links_existing_user_by_verified_email(
    sso_session: AsyncSession,
    sso_user_manager: UserManager,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    _, user = await make_org_with_user(email="existing@example.com")

    result = await resolve_identity(
        sso_session,
        user_manager=sso_user_manager,
        provider_type="google",
        provider_id="google",
        external_id="google-sub-456",
        external_email="existing@example.com",
        email_verified=True,
    )
    assert result.created is False
    assert result.user.id == user.id
    assert result.external_identity.external_id == "google-sub-456"


_GOOGLE_PICTURE = "https://lh3.googleusercontent.com/a/new-picture"


@pytest.mark.asyncio
async def test_social_link_keeps_email_account_avatar(
    sso_session: AsyncSession,
    sso_user_manager: UserManager,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    """Email signup, then Google login, must not replace the account avatar.

    Registration leaves a generated picture (or none). The client draws that
    stably. The Google ``picture`` claim must not swap it.
    """
    _, user = await make_org_with_user(email="email-first@example.com")
    user.avatar_url = "avatars/email-first.png"
    user.avatar_kind = AvatarKind.generated.value
    user.avatar_seed = "kept-seed"
    sso_session.add(user)
    await sso_session.commit()

    result = await resolve_identity(
        sso_session,
        user_manager=sso_user_manager,
        provider_type="google",
        provider_id="google",
        external_id="google-sub-email-first",
        external_email="email-first@example.com",
        email_verified=True,
        avatar_url=_GOOGLE_PICTURE,
    )
    assert result.created is False
    assert result.user.id == user.id
    assert result.user.avatar_url == "avatars/email-first.png"
    assert result.user.avatar_kind == AvatarKind.generated.value
    assert result.user.avatar_seed == "kept-seed"


@pytest.mark.asyncio
async def test_social_link_keeps_unset_email_avatar(
    sso_session: AsyncSession,
    sso_user_manager: UserManager,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    """The common email-signup state is no stored avatar at all."""
    _, user = await make_org_with_user(email="plain-email@example.com")
    assert user.avatar_url is None
    assert user.avatar_kind is None

    result = await resolve_identity(
        sso_session,
        user_manager=sso_user_manager,
        provider_type="google",
        provider_id="google",
        external_id="google-sub-plain",
        external_email="plain-email@example.com",
        email_verified=True,
        avatar_url=_GOOGLE_PICTURE,
    )
    assert result.created is False
    assert result.user.avatar_url is None
    assert result.user.avatar_kind is None


@pytest.mark.asyncio
async def test_social_relogin_does_not_replace_kept_avatar(
    sso_session: AsyncSession,
    sso_user_manager: UserManager,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    """A later Google login of an already-linked email account stays put."""
    _, user = await make_org_with_user(email="relink@example.com")
    user.avatar_url = "avatars/relink.png"
    user.avatar_kind = AvatarKind.generated.value
    sso_session.add(user)
    sso_session.add(
        ExternalIdentity(
            user_id=user.id,
            provider_type="google",
            provider_id="google",
            external_id="google-sub-relink",
            external_email="relink@example.com",
        )
    )
    await sso_session.commit()

    result = await resolve_identity(
        sso_session,
        user_manager=sso_user_manager,
        provider_type="google",
        provider_id="google",
        external_id="google-sub-relink",
        external_email="relink@example.com",
        email_verified=True,
        avatar_url=_GOOGLE_PICTURE,
    )
    assert result.created is False
    assert result.user.avatar_url == "avatars/relink.png"
    assert result.user.avatar_kind == AvatarKind.generated.value


@pytest.mark.asyncio
async def test_social_login_sets_picture_for_new_user(
    sso_session: AsyncSession, sso_user_manager: UserManager
) -> None:
    """A person who first appears via Google still gets the provider picture."""
    result = await resolve_identity(
        sso_session,
        user_manager=sso_user_manager,
        provider_type="google",
        provider_id="google",
        external_id="google-sub-brand-new",
        external_email="brand-new@example.com",
        email_verified=True,
        avatar_url=_GOOGLE_PICTURE,
        claims={"name": "Brand New"},
    )
    assert result.created is True
    assert result.user.avatar_url == _GOOGLE_PICTURE
    assert result.user.avatar_kind == AvatarKind.sso.value


@pytest.mark.asyncio
async def test_social_relogin_refreshes_sso_avatar(
    sso_session: AsyncSession,
    sso_user_manager: UserManager,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    """An avatar Google already owns is updated when the picture URL changes."""
    _, user = await make_org_with_user(email="google-native@example.com")
    user.avatar_url = "https://lh3.googleusercontent.com/a/old"
    user.avatar_kind = AvatarKind.sso.value
    sso_session.add(user)
    sso_session.add(
        ExternalIdentity(
            user_id=user.id,
            provider_type="google",
            provider_id="google",
            external_id="google-sub-native",
            external_email="google-native@example.com",
        )
    )
    await sso_session.commit()

    result = await resolve_identity(
        sso_session,
        user_manager=sso_user_manager,
        provider_type="google",
        provider_id="google",
        external_id="google-sub-native",
        external_email="google-native@example.com",
        email_verified=True,
        avatar_url=_GOOGLE_PICTURE,
    )
    assert result.user.avatar_url == _GOOGLE_PICTURE
    assert result.user.avatar_kind == AvatarKind.sso.value


@pytest.mark.asyncio
async def test_enterprise_sso_still_upgrades_generated_avatar(
    sso_session: AsyncSession,
    sso_user_manager: UserManager,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    """Enterprise SSO still replaces a generated avatar with the IdP picture."""
    org, user = await make_org_with_user(email="member@corp.com")
    user.avatar_url = "avatars/member.png"
    user.avatar_kind = AvatarKind.generated.value
    sso_session.add(user)
    await sso_session.commit()

    picture = "https://idp.example/picture.png"
    result = await resolve_identity(
        sso_session,
        user_manager=sso_user_manager,
        provider_type="oidc_sso",
        provider_id="sso-corp",
        external_id="oidc-sub-member",
        external_email="member@corp.com",
        email_verified=True,
        avatar_url=picture,
        policy=EnterpriseLoginPolicy(org_id=org.id, connection_active=True, auto_provision=True),
    )
    assert result.created is False
    assert result.user.avatar_url == picture
    assert result.user.avatar_kind == AvatarKind.sso.value


@pytest.mark.asyncio
async def test_resolve_rejects_cross_org_takeover_via_email_match(
    sso_session: AsyncSession,
    sso_user_manager: UserManager,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    """Security regression: existing user with verified email is NOT
    auto-linked to an enterprise SSO of an org they don't belong to under
    invite_only provisioning. Without this guard, an attacker who runs
    any registered IdP and claims a victim's email gains access to the
    victim's account."""
    # Victim lives in Org A only.
    _, victim = await make_org_with_user(email="victim@corp.com")
    # Attacker controls SSO connection in Org B (different org, invite_only).
    org_b, _attacker_admin = await make_org_with_user(email="attacker@evil.com")

    with pytest.raises(SSOProvisioningDenied):
        await resolve_identity(
            sso_session,
            user_manager=sso_user_manager,
            provider_type="oidc_sso",
            provider_id="sso-evil",
            external_id="evil-sub-1",
            external_email="victim@corp.com",
            email_verified=True,
            policy=EnterpriseLoginPolicy(
                org_id=org_b.id, connection_active=True, auto_provision=False
            ),
        )


@pytest.mark.asyncio
async def test_resolve_returns_existing_link(
    sso_session: AsyncSession,
    sso_user_manager: UserManager,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    _, user = await make_org_with_user(email="linked@example.com")
    eid = ExternalIdentity(
        user_id=user.id,
        provider_type="google",
        provider_id="google",
        external_id="google-sub-789",
        external_email="linked@example.com",
    )
    sso_session.add(eid)
    await sso_session.commit()

    result = await resolve_identity(
        sso_session,
        user_manager=sso_user_manager,
        provider_type="google",
        provider_id="google",
        external_id="google-sub-789",
        external_email="linked@example.com",
        email_verified=True,
    )
    assert result.created is False
    assert result.user.id == user.id


@pytest.mark.asyncio
async def test_resolve_rejects_link_for_ex_member(
    sso_session: AsyncSession,
    sso_user_manager: UserManager,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    """SSO callback must not log in a user who's no longer in the org."""
    org, user = await make_org_with_user(email="alumnus@corp.com")
    sso_session.add(
        ExternalIdentity(
            user_id=user.id,
            provider_type="oidc_sso",
            provider_id="sso-alumni",
            external_id="x",
            external_email="alumnus@corp.com",
        )
    )
    await sso_session.commit()
    # Drop the org membership
    await sso_session.execute(
        text("DELETE FROM organization_memberships WHERE user_id=:u"),
        {"u": user.id},
    )
    await sso_session.commit()

    with pytest.raises(SSOLoginRejected) as exc_info:
        await resolve_identity(
            sso_session,
            user_manager=sso_user_manager,
            provider_type="oidc_sso",
            provider_id="sso-alumni",
            external_id="x",
            external_email="alumnus@corp.com",
            email_verified=True,
            policy=EnterpriseLoginPolicy(
                org_id=org.id, connection_active=True, auto_provision=True
            ),
        )
    assert exc_info.value.code == "not_org_member"


@pytest.mark.asyncio
async def test_resolve_denies_invite_only_provisioning(
    sso_session: AsyncSession, sso_user_manager: UserManager
) -> None:
    with pytest.raises(SSOProvisioningDenied):
        await resolve_identity(
            sso_session,
            user_manager=sso_user_manager,
            provider_type="oidc_sso",
            provider_id="sso-test",
            external_id="oidc-sub-new",
            external_email="noprovision@corp.com",
            email_verified=True,
            policy=EnterpriseLoginPolicy(
                org_id="org-test", connection_active=True, auto_provision=False
            ),
        )


@pytest.mark.asyncio
async def test_resolve_rejects_inactive_user_with_existing_link(
    sso_session: AsyncSession,
    sso_user_manager: UserManager,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    _, user = await make_org_with_user(email="inactive@example.com")
    user.is_active = False
    sso_session.add(user)
    sso_session.add(
        ExternalIdentity(
            user_id=user.id,
            provider_type="google",
            provider_id="google",
            external_id="google-sub-inactive",
            external_email="inactive@example.com",
        )
    )
    await sso_session.commit()

    with pytest.raises(SSOLoginRejected) as exc_info:
        await resolve_identity(
            sso_session,
            user_manager=sso_user_manager,
            provider_type="google",
            provider_id="google",
            external_id="google-sub-inactive",
            external_email="inactive@example.com",
            email_verified=True,
        )
    assert exc_info.value.code == "user_inactive"


@pytest.mark.asyncio
async def test_resolve_rejects_when_sso_connection_disabled(
    sso_session: AsyncSession,
    sso_user_manager: UserManager,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    org, user = await make_org_with_user(email="disabled@corp.com")
    sso_session.add(
        ExternalIdentity(
            user_id=user.id,
            provider_type="oidc_sso",
            provider_id="sso-disabled",
            external_id="x",
            external_email="disabled@corp.com",
        )
    )
    await sso_session.commit()

    with pytest.raises(SSOLoginRejected) as exc_info:
        await resolve_identity(
            sso_session,
            user_manager=sso_user_manager,
            provider_type="oidc_sso",
            provider_id="sso-disabled",
            external_id="x",
            external_email="disabled@corp.com",
            email_verified=True,
            policy=EnterpriseLoginPolicy(
                org_id=org.id, connection_active=False, auto_provision=True
            ),
        )
    assert exc_info.value.code == "sso_connection_inactive"
