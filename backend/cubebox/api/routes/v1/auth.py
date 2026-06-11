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
    session: Annotated[AsyncSession, Depends(get_session)],
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
    if body.display_name is not None:
        await session.refresh(user)
        user.display_name = body.display_name
        session.add(user)
        await session.commit()
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
    display_name: str | None = Field(None, min_length=1, max_length=100)


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
        user.display_name = body.display_name
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


# Include fastapi-users built-in auth routes for /logout. Must stay BELOW our
# custom /login above — FastAPI matches the first-registered route, so our
# rate-limited /login takes precedence and fastapi-users' /login is shadowed.
# If you ever move this include_router above the custom routes, rate limiting
# on login silently disappears.
router.include_router(fastapi_users.get_auth_router(auth_backend))
router.include_router(fastapi_users.get_reset_password_router(), prefix="")
