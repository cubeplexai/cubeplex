"""Auth routes: register, login, logout (cookie-based) with rate limit."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.authentication import Strategy
from fastapi_users.exceptions import InvalidPasswordException, UserAlreadyExists, UserNotExists
from fastapi_users.schemas import BaseUser, BaseUserCreate

from cubebox.api.middleware.rate_limit import LOGIN_LIMIT, REGISTER_LIMIT, limiter
from cubebox.auth.dependencies import current_active_user
from cubebox.auth.jwt import auth_backend
from cubebox.auth.users import UserManager, fastapi_users, get_user_manager
from cubebox.models import User


class UserRead(BaseUser[str]):
    pass


class UserCreate(BaseUserCreate):
    pass


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=201)
@limiter.limit(REGISTER_LIMIT)
async def register(
    request: Request,
    body: Annotated[UserCreate, Body()],
    user_manager: Annotated[UserManager, Depends(get_user_manager)],
) -> dict[str, str]:
    try:
        user = await user_manager.create(body, safe=True, request=request)
    except UserAlreadyExists:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="REGISTER_USER_ALREADY_EXISTS"
        ) from None
    except InvalidPasswordException as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "REGISTER_INVALID_PASSWORD", "reason": exc.reason},
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
) -> Response:
    try:
        user = await user_manager.authenticate(credentials)
    except UserNotExists:
        user = None
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="LOGIN_BAD_CREDENTIALS")
    return await auth_backend.login(strategy, user)


@router.get("/me")
async def me(user: Annotated[User, Depends(current_active_user)]) -> dict[str, str]:
    return {"id": user.id, "email": user.email}


# Include fastapi-users built-in auth routes for /logout. Must stay BELOW our
# custom /login above — FastAPI matches the first-registered route, so our
# rate-limited /login takes precedence and fastapi-users' /login is shadowed.
# If you ever move this include_router above the custom routes, rate limiting
# on login silently disappears.
router.include_router(fastapi_users.get_auth_router(auth_backend))
