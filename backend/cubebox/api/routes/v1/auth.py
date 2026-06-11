"""Auth routes: register, login, logout (cookie-based) with rate limit."""

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.authentication import Strategy
from fastapi_users.exceptions import InvalidPasswordException, UserAlreadyExists, UserNotExists
from fastapi_users.schemas import BaseUser, BaseUserCreate
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.middleware.rate_limit import LOGIN_LIMIT, REGISTER_LIMIT, limiter
from cubebox.auth.dependencies import current_active_user
from cubebox.auth.jwt import auth_backend
from cubebox.auth.users import UserManager, fastapi_users, get_user_manager
from cubebox.db import get_session
from cubebox.i18n import get_locale, get_translator
from cubebox.models import User


class UserRead(BaseUser[str]):
    pass


class UserCreate(BaseUserCreate):
    display_name: str | None = Field(None, min_length=1, max_length=100)


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=201)
@limiter.limit(REGISTER_LIMIT)
async def register(
    request: Request,
    body: Annotated[UserCreate, Body()],
    user_manager: Annotated[UserManager, Depends(get_user_manager)],
    locale: Annotated[str, Depends(get_locale)],
) -> dict[str, str]:
    _t = get_translator(locale)
    try:
        user = await user_manager.create(body, safe=True, request=request)
    except UserAlreadyExists:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_t("register_user_already_exists"),
        ) from None
    except InvalidPasswordException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_t("register_invalid_password"),
        ) from None
    default_ws = getattr(user, "_default_workspace_id", None)
    return {
        "id": user.id,
        "email": user.email,
        "default_workspace_id": default_ws or "",
    }


@router.post("/login")
@limiter.limit(LOGIN_LIMIT)
async def login(
    request: Request,
    credentials: Annotated[OAuth2PasswordRequestForm, Depends()],
    user_manager: Annotated[UserManager, Depends(get_user_manager)],
    strategy: Annotated[Strategy[User, str], Depends(auth_backend.get_strategy)],
    locale: Annotated[str, Depends(get_locale)],
) -> Response:
    _t = get_translator(locale)
    try:
        user = await user_manager.authenticate(credentials)
    except UserNotExists:
        user = None
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_t("login_bad_credentials"),
        )
    return await auth_backend.login(strategy, user)


class UserProfileUpdate(BaseModel):
    language: Literal["en", "zh"] | None = None
    display_name: str | None = Field(None, max_length=100)


@router.get("/me")
async def me(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> dict[str, object]:
    from sqlalchemy import func, select

    from cubebox.models import Organization, OrganizationMembership

    mode = getattr(request.app.state, "deployment_mode", "single_tenant")
    needs_setup = False
    if mode == "single_tenant":
        org_count = (
            await session.execute(select(func.count()).select_from(Organization))
        ).scalar_one()
        if int(org_count) == 0:
            needs_setup = True
        else:
            has_membership = (
                await session.execute(
                    select(func.count())
                    .select_from(OrganizationMembership)
                    .where(OrganizationMembership.user_id == user.id)  # type: ignore[arg-type]
                )
            ).scalar_one()
            needs_setup = int(has_membership) == 0
    membership_rows = (
        (
            await session.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.user_id == user.id  # type: ignore[arg-type]
                )
            )
        )
        .scalars()
        .all()
    )
    org_memberships = [{"org_id": m.org_id, "role": m.role} for m in membership_rows]
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "language": user.language,
        "is_verified": user.is_verified,
        "needs_org_setup": needs_setup,
        "org_memberships": org_memberships,
    }


@router.patch("/me")
async def patch_me(
    user: Annotated[User, Depends(current_active_user)],
    body: Annotated[UserProfileUpdate, Body()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    from sqlalchemy import select

    from cubebox.models import OrganizationMembership

    if body.language is None and body.display_name is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at least one field required",
        )
    if body.language is not None:
        user.language = body.language
    if body.display_name is not None:
        user.display_name = body.display_name or None
    session.add(user)
    await session.commit()
    await session.refresh(user)
    membership_rows = (
        (
            await session.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.user_id == user.id  # type: ignore[arg-type]
                )
            )
        )
        .scalars()
        .all()
    )
    org_memberships = [{"org_id": m.org_id, "role": m.role} for m in membership_rows]
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "language": user.language,
        "needs_org_setup": False,
        "org_memberships": org_memberships,
    }


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


@router.post("/change-password")
async def change_password(
    body: Annotated[ChangePasswordRequest, Body()],
    user: Annotated[User, Depends(current_active_user)],
    user_manager: Annotated[UserManager, Depends(get_user_manager)],
    request: Request,
) -> dict[str, bool]:
    verified, _ = user_manager.password_helper.verify_and_update(
        body.current_password, user.hashed_password
    )
    if not verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="incorrect_password",
        )
    try:
        await user_manager.validate_password(body.new_password, user)
    except InvalidPasswordException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_password",
        ) from None
    user.hashed_password = user_manager.password_helper.hash(body.new_password)
    session = user_manager.user_db.session  # type: ignore[attr-defined]
    session.add(user)
    await session.commit()

    from cubebox.plugins.audit import audit_log

    await audit_log(
        action="auth.password_changed",
        user_id=user.id,
        ip=request.client.host if request.client else None,
    )
    return {"ok": True}


class DeleteAccountRequest(BaseModel):
    password: str


@router.post("/delete-account")
async def delete_account(
    body: Annotated[DeleteAccountRequest, Body()],
    user: Annotated[User, Depends(current_active_user)],
    user_manager: Annotated[UserManager, Depends(get_user_manager)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> Response:
    verified, _ = user_manager.password_helper.verify_and_update(
        body.password, user.hashed_password
    )
    if not verified:
        raise HTTPException(status_code=400, detail="incorrect_password")

    from sqlalchemy import select

    from cubebox.models import OrganizationMembership, OrgRole

    owner_rows = (
        (
            await session.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.user_id == user.id,  # type: ignore[arg-type]
                    OrganizationMembership.role == OrgRole.OWNER.value,  # type: ignore[arg-type]
                )
            )
        )
        .scalars()
        .all()
    )
    if owner_rows:
        raise HTTPException(status_code=400, detail="transfer_ownership_first")

    from cubebox.plugins.audit import audit_log

    await audit_log(
        action="auth.account_deleted",
        user_id=user.id,
        ip=request.client.host if request.client else None,
    )

    from sqlalchemy import delete as sa_delete
    from sqlalchemy import update as sa_update

    from cubebox.models import Membership
    from cubebox.models import User as UserModel
    from cubebox.models.artifact import Artifact
    from cubebox.models.artifact_version import ArtifactVersion
    from cubebox.models.attachment import Attachment
    from cubebox.models.billing import BillingEvent, LlmBillingEvent
    from cubebox.models.conversation import Conversation
    from cubebox.models.credential import Credential
    from cubebox.models.egress_ref import EgressRef
    from cubebox.models.invite_token import InviteToken
    from cubebox.models.mcp import (
        MCPConnectorInstall,
        MCPCredentialGrant,
        MCPWorkspaceConnectorState,
    )
    from cubebox.models.memory import MemoryItem
    from cubebox.models.provider import Provider
    from cubebox.models.sandbox_env import SandboxEnvVar
    from cubebox.models.scheduled_task import ScheduledTask, ScheduledTaskRun
    from cubebox.models.skill import OrgPreinstalledTombstone, OrgSkillInstall, SkillVersion
    from cubebox.models.skill_registry import SkillRegistry
    from cubebox.models.trigger import Trigger, TriggerEvent
    from cubebox.models.user_event import UserEvent
    from cubebox.models.user_sandbox import UserSandbox

    # NULL out nullable user-FK columns so org resources survive account deletion.
    for null_model, null_col in [
        (MemoryItem, "updated_by_user_id"),
        (MemoryItem, "created_by_user_id"),
        (Credential, "created_by_user_id"),
        (Provider, "created_by_user_id"),
        (SkillRegistry, "created_by_user_id"),
        (SkillVersion, "uploaded_by_user_id"),
        (MCPConnectorInstall, "created_by_user_id"),
        (MCPWorkspaceConnectorState, "updated_by_user_id"),
        (MCPCredentialGrant, "created_by_user_id"),
        (SandboxEnvVar, "created_by_user_id"),
        (OrgSkillInstall, "installed_by_user_id"),
        (OrgPreinstalledTombstone, "hidden_by_user_id"),
    ]:
        await session.execute(
            sa_update(null_model)
            .where(getattr(null_model, null_col) == user.id)
            .values(**{null_col: None})
        )

    # User-scoped credential grants have a check constraint requiring user_id NOT NULL,
    # so we delete them rather than nulling user_id.
    await session.execute(
        sa_delete(MCPCredentialGrant).where(
            MCPCredentialGrant.user_id == user.id  # type: ignore[arg-type]
        )
    )

    # Invite tokens created by the user are no longer redeemable — delete them.
    await session.execute(
        sa_delete(InviteToken).where(InviteToken.created_by == user.id)  # type: ignore[arg-type]
    )

    # Subquery deletes for child tables that lack a direct user FK.
    billing_tbl = BillingEvent.__table__  # type: ignore[attr-defined]
    user_billing_ids = select(billing_tbl.c.id).where(billing_tbl.c.user_id == user.id)
    await session.execute(
        sa_delete(LlmBillingEvent).where(
            LlmBillingEvent.billing_event_id.in_(user_billing_ids)  # type: ignore[attr-defined]
        )
    )
    task_tbl = ScheduledTask.__table__  # type: ignore[attr-defined]
    user_task_ids = select(task_tbl.c.id).where(task_tbl.c.owner_user_id == user.id)
    await session.execute(
        sa_delete(ScheduledTaskRun).where(
            ScheduledTaskRun.scheduled_task_id.in_(user_task_ids)  # type: ignore[attr-defined]
        )
    )
    conv_tbl = Conversation.__table__  # type: ignore[attr-defined]
    user_conv_ids = select(conv_tbl.c.id).where(conv_tbl.c.creator_user_id == user.id)
    art_tbl = Artifact.__table__  # type: ignore[attr-defined]
    user_artifact_ids = select(art_tbl.c.id).where(art_tbl.c.conversation_id.in_(user_conv_ids))
    await session.execute(
        sa_delete(ArtifactVersion).where(
            ArtifactVersion.artifact_id.in_(user_artifact_ids)  # type: ignore[attr-defined]
        )
    )
    await session.execute(
        sa_delete(Artifact).where(
            Artifact.conversation_id.in_(user_conv_ids)  # type: ignore[attr-defined]
        )
    )

    # TriggerEvent has no user FK — delete via trigger parent.
    trigger_tbl = Trigger.__table__  # type: ignore[attr-defined]
    user_trigger_ids = select(trigger_tbl.c.id).where(trigger_tbl.c.run_as_user_id == user.id)
    await session.execute(
        sa_delete(TriggerEvent).where(
            TriggerEvent.trigger_id.in_(user_trigger_ids)  # type: ignore[attr-defined]
        )
    )

    # Delete user-owned rows (deepest FK dependents first).
    for model, col in [
        (EgressRef, EgressRef.user_id),
        (MemoryItem, MemoryItem.owner_user_id),
        (SandboxEnvVar, SandboxEnvVar.user_id),
        (UserSandbox, UserSandbox.user_id),
        (Attachment, Attachment.uploader_user_id),
        (BillingEvent, BillingEvent.user_id),
        (UserEvent, UserEvent.user_id),
        (ScheduledTask, ScheduledTask.owner_user_id),
        (Trigger, Trigger.run_as_user_id),
        (Conversation, Conversation.creator_user_id),
        (Membership, Membership.user_id),
        (OrganizationMembership, OrganizationMembership.user_id),
    ]:
        await session.execute(
            sa_delete(model).where(col == user.id)  # type: ignore[arg-type]
        )

    await session.execute(
        sa_delete(UserModel).where(UserModel.id == user.id)  # type: ignore[arg-type]
    )
    await session.commit()

    from cubebox.config import config

    cookie_name = config.get("auth.cookie_name", "cubebox_auth")
    response = Response(
        content='{"deleted": true}',
        media_type="application/json",
    )
    response.delete_cookie(cookie_name)
    return response


# Include fastapi-users built-in auth routes for /logout. Must stay BELOW our
# custom /login above — FastAPI matches the first-registered route, so our
# rate-limited /login takes precedence and fastapi-users' /login is shadowed.
# If you ever move this include_router above the custom routes, rate limiting
# on login silently disappears.
router.include_router(fastapi_users.get_auth_router(auth_backend))
router.include_router(fastapi_users.get_reset_password_router(), prefix="")
router.include_router(fastapi_users.get_verify_router(UserRead), prefix="")
