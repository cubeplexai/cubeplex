"""Personal-access API key management.

Routes scoped to the authenticated user (no workspace path). The plaintext
token is returned exactly once on creation; thereafter only ``prefix`` and
``last_used_at`` are exposed.
"""

import hashlib
import secrets
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.dependencies import current_active_user
from cubebox.db import get_session
from cubebox.models import ApiKey, User
from cubebox.repositories import ApiKeyRepository

router = APIRouter(prefix="/me/api-keys", tags=["api-keys"])

# Quota: prevent runaway scripts; users delete to make room.
MAX_KEYS_PER_USER = 10

# Token layout: `sk-` + 22 url-safe base62 chars. ~132 bits of entropy.
# Matches the OpenAI-style convention many users already recognise as
# "secret key" and that secret-scanning tools (gh, gitleaks) flag by default.
_TOKEN_PREFIX = "sk-"
_TOKEN_BODY_LEN = 22
# What we show in list views — enough to disambiguate, not enough to brute.
_DISPLAY_PREFIX_LEN = 12


def _generate_token() -> tuple[str, str, str]:
    """Return ``(plaintext, display_prefix, sha256_hex)``."""
    body = secrets.token_urlsafe(32)[:_TOKEN_BODY_LEN]
    plaintext = f"{_TOKEN_PREFIX}{body}"
    display_prefix = plaintext[:_DISPLAY_PREFIX_LEN]
    hashed = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return plaintext, display_prefix, hashed


class CreateApiKeyRequest(BaseModel):
    label: str = Field(min_length=1, max_length=100)


class ApiKeyListItem(BaseModel):
    id: str
    label: str
    prefix: str
    last_used_at: datetime | None
    created_at: datetime


class ApiKeyCreatedResponse(BaseModel):
    id: str
    label: str
    prefix: str
    created_at: datetime
    # Plaintext token. Shown ONLY on create — never again.
    token: str


@router.get("", response_model=list[ApiKeyListItem])
async def list_api_keys(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ApiKeyListItem]:
    repo = ApiKeyRepository(session)
    keys = await repo.list_by_user(user.id)
    return [
        ApiKeyListItem(
            id=k.id,
            label=k.label,
            prefix=k.prefix,
            last_used_at=k.last_used_at,
            created_at=k.created_at,
        )
        for k in keys
    ]


@router.post("", response_model=ApiKeyCreatedResponse, status_code=201)
async def create_api_key(
    user: Annotated[User, Depends(current_active_user)],
    body: Annotated[CreateApiKeyRequest, Body()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApiKeyCreatedResponse:
    repo = ApiKeyRepository(session)
    existing = await repo.count_by_user(user.id)
    if existing >= MAX_KEYS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Maximum {MAX_KEYS_PER_USER} API keys per user; delete one to create another",
        )
    plaintext, display_prefix, hashed = _generate_token()
    key = ApiKey(
        user_id=user.id,
        label=body.label,
        prefix=display_prefix,
        hashed_key=hashed,
    )
    key = await repo.add(key)
    return ApiKeyCreatedResponse(
        id=key.id,
        label=key.label,
        prefix=key.prefix,
        created_at=key.created_at,
        token=plaintext,
    )


@router.delete("/{key_id}", status_code=204)
async def delete_api_key(
    user: Annotated[User, Depends(current_active_user)],
    key_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    repo = ApiKeyRepository(session)
    deleted = await repo.delete(key_id=key_id, user_id=user.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )
