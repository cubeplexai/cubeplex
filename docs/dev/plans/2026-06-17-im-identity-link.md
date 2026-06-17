# IM Identity Link Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let IM users (Discord/Feishu) manually link their IM identity to a cubebox account via a `/link email` command + browser confirmation.

**Architecture:** The IM bot generates a signed JWT (10-min TTL) containing the IM user ID and email, replies with a confirmation URL. The frontend page calls an authenticated backend endpoint that verifies the email matches the logged-in user, checks workspace membership, and upserts the `IMIdentityLink` row.

**Tech Stack:** PyJWT (existing), FastAPI, Next.js, discord.py slash commands, Feishu message parsing

**Spec:** `docs/dev/specs/2026-06-17-im-identity-link-design.md`

---

### Task 1: Token Sign/Verify Module

**Files:**
- Create: `backend/cubebox/im/link.py`
- Test: `backend/tests/unit/im/test_link_token.py`

- [ ] **Step 1: Write tests for sign and verify**

```python
# backend/tests/unit/im/test_link_token.py
"""Token sign + verify for IM identity linking."""

from __future__ import annotations

import time

import pytest

from cubebox.im.link import LinkClaims, sign_link_token, verify_link_token


_SECRET = "test-secret-for-link-tokens"


class TestSignLinkToken:
    def test_roundtrip(self) -> None:
        token = sign_link_token(
            im_user_id="discord_123",
            email="Chris@Example.COM",
            account_id="imca_abc",
            workspace_id="ws_xyz",
            platform="discord",
            secret=_SECRET,
        )
        claims = verify_link_token(token, secret=_SECRET)
        assert claims.im_user_id == "discord_123"
        assert claims.email == "chris@example.com"  # normalized
        assert claims.account_id == "imca_abc"
        assert claims.workspace_id == "ws_xyz"
        assert claims.platform == "discord"

    def test_bad_signature_rejected(self) -> None:
        token = sign_link_token(
            im_user_id="u1",
            email="a@b.com",
            account_id="imca_1",
            workspace_id="ws_1",
            platform="feishu",
            secret=_SECRET,
        )
        with pytest.raises(ValueError, match="Invalid or expired"):
            verify_link_token(token, secret="wrong-secret")

    def test_wrong_issuer_rejected(self) -> None:
        import jwt

        token = jwt.encode(
            {"sub": "u1", "iss": "other", "exp": int(time.time()) + 600},
            _SECRET,
            algorithm="HS256",
        )
        with pytest.raises(ValueError, match="Invalid or expired"):
            verify_link_token(token, secret=_SECRET)

    def test_expired_token_rejected(self) -> None:
        import jwt

        token = jwt.encode(
            {
                "sub": "u1",
                "email": "a@b.com",
                "act": "imca_1",
                "ws": "ws_1",
                "plt": "discord",
                "iss": "cubebox:im-link",
                "exp": int(time.time()) - 10,
                "iat": int(time.time()) - 700,
            },
            _SECRET,
            algorithm="HS256",
        )
        with pytest.raises(ValueError, match="Invalid or expired"):
            verify_link_token(token, secret=_SECRET)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/im/test_link_token.py -v`
Expected: FAIL — `cubebox.im.link` does not exist.

- [ ] **Step 3: Implement sign and verify**

```python
# backend/cubebox/im/link.py
"""JWT token for IM identity linking.

The IM /link command signs a short-lived token encoding the sender's IM
identity and their claimed email. The browser confirmation endpoint
decodes it and checks the email against the logged-in cubebox user.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

_ISS = "cubebox:im-link"
_TTL = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class LinkClaims:
    im_user_id: str
    email: str
    account_id: str
    workspace_id: str
    platform: str


def sign_link_token(
    *,
    im_user_id: str,
    email: str,
    account_id: str,
    workspace_id: str,
    platform: str,
    secret: str,
) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": im_user_id,
        "email": email.strip().lower(),
        "act": account_id,
        "ws": workspace_id,
        "plt": platform,
        "exp": int((now + _TTL).timestamp()),
        "iat": int(now.timestamp()),
        "iss": _ISS,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def verify_link_token(token: str, *, secret: str) -> LinkClaims:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=_ISS,
        )
    except jwt.PyJWTError as exc:
        raise ValueError("Invalid or expired link token") from exc
    return LinkClaims(
        im_user_id=payload["sub"],
        email=payload["email"],
        account_id=payload["act"],
        workspace_id=payload["ws"],
        platform=payload["plt"],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/im/test_link_token.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/im/link.py backend/tests/unit/im/test_link_token.py
git commit -m "feat(im): add JWT sign/verify for identity link tokens"
```

---

### Task 2: Confirmation Endpoint

**Files:**
- Create: `backend/cubebox/api/routes/v1/im_link.py`
- Modify: `backend/cubebox/api/app.py` (add `include_router`)
- Test: `backend/tests/unit/im/test_im_link_confirm.py`

**Context:**
- Route: `POST /api/v1/im/link/confirm` — authenticated, workspace-neutral
- Uses `current_active_user` dependency from `cubebox.auth.dependencies`
- Upserts `IMIdentityLink` (unique on `account_id + im_user_id`)
- Returns JSON with outcome + message

- [ ] **Step 1: Write tests**

```python
# backend/tests/unit/im/test_im_link_confirm.py
"""Unit tests for POST /api/v1/im/link/confirm."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from cubebox.api.routes.v1.im_link import router
from cubebox.im.link import sign_link_token
from cubebox.models.user import User

_SECRET = "test-jwt-secret"


def _make_user(email: str = "chris@example.com", user_id: str = "usr_1") -> User:
    u = User.__new__(User)
    u.id = user_id
    u.email = email
    u.is_active = True
    u.is_superuser = False
    u.is_verified = True
    u.display_name = None
    u.language = "en"
    return u


def _make_app(user: User | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    if user is not None:
        from cubebox.auth.dependencies import current_active_user

        app.dependency_overrides[current_active_user] = lambda: user

    return app


def _sign(
    email: str = "chris@example.com",
    im_user_id: str = "discord_42",
    account_id: str = "imca_abc",
    workspace_id: str = "ws_xyz",
    platform: str = "discord",
) -> str:
    return sign_link_token(
        im_user_id=im_user_id,
        email=email,
        account_id=account_id,
        workspace_id=workspace_id,
        platform=platform,
        secret=_SECRET,
    )


@pytest.mark.anyio
async def test_email_mismatch_rejected() -> None:
    user = _make_user(email="other@example.com")
    app = _make_app(user)
    token = _sign(email="chris@example.com")
    with patch("cubebox.api.routes.v1.im_link._get_jwt_secret", return_value=_SECRET):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post("/api/v1/im/link/confirm", json={"token": token})
    assert resp.status_code == 403
    assert "chris@example.com" in resp.json()["detail"]


@pytest.mark.anyio
async def test_not_workspace_member_rejected() -> None:
    user = _make_user(email="chris@example.com")
    app = _make_app(user)
    token = _sign(email="chris@example.com")
    with (
        patch("cubebox.api.routes.v1.im_link._get_jwt_secret", return_value=_SECRET),
        patch(
            "cubebox.api.routes.v1.im_link._check_membership",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post("/api/v1/im/link/confirm", json={"token": token})
    assert resp.status_code == 403
    assert "管理员" in resp.json()["detail"]


@pytest.mark.anyio
async def test_success_creates_link() -> None:
    user = _make_user(email="chris@example.com")
    app = _make_app(user)
    token = _sign(email="chris@example.com")
    with (
        patch("cubebox.api.routes.v1.im_link._get_jwt_secret", return_value=_SECRET),
        patch(
            "cubebox.api.routes.v1.im_link._check_membership",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "cubebox.api.routes.v1.im_link._upsert_identity_link",
            new_callable=AsyncMock,
        ) as mock_upsert,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post("/api/v1/im/link/confirm", json={"token": token})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_upsert.assert_awaited_once()


@pytest.mark.anyio
async def test_invalid_token_rejected() -> None:
    user = _make_user()
    app = _make_app(user)
    with patch("cubebox.api.routes.v1.im_link._get_jwt_secret", return_value=_SECRET):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post("/api/v1/im/link/confirm", json={"token": "garbage"})
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/im/test_im_link_confirm.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the endpoint**

```python
# backend/cubebox/api/routes/v1/im_link.py
"""IM identity link confirmation endpoint.

Workspace-neutral, authenticated. The workspace comes from the JWT
token (not the URL path); the user comes from the auth cookie.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.dependencies import current_active_user
from cubebox.db.session import get_session
from cubebox.im.link import LinkClaims, verify_link_token
from cubebox.models.im_connector import IMConnectorAccount, IMIdentityLink
from cubebox.models.membership import Membership
from cubebox.models.user import User

router = APIRouter(prefix="/im/link", tags=["im-link"])


class _ConfirmBody(BaseModel):
    token: str


class _ConfirmResult(BaseModel):
    ok: bool
    platform: str = ""
    account_id: str = ""


def _get_jwt_secret() -> str:
    from cubebox.config import config

    return str(config.get("auth.jwt_secret", "CHANGE_ME"))


async def _check_membership(session: AsyncSession, user_id: str, workspace_id: str) -> bool:
    row = (
        await session.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def _upsert_identity_link(
    session: AsyncSession,
    claims: LinkClaims,
    user_id: str,
) -> None:
    account = (
        await session.execute(
            select(IMConnectorAccount).where(
                IMConnectorAccount.id == claims.account_id,
            )
        )
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=400, detail="IM 账号不存在。")

    existing = (
        await session.execute(
            select(IMIdentityLink).where(
                IMIdentityLink.account_id == claims.account_id,
                IMIdentityLink.im_user_id == claims.im_user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.user_id = user_id
        session.add(existing)
    else:
        link = IMIdentityLink(
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            account_id=claims.account_id,
            im_user_id=claims.im_user_id,
            user_id=user_id,
        )
        session.add(link)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="绑定冲突，请重试。")


@router.post("/confirm")
async def confirm_im_link(
    body: Annotated[_ConfirmBody, Body()],
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> _ConfirmResult:
    secret = _get_jwt_secret()
    try:
        claims = verify_link_token(body.token, secret=secret)
    except ValueError:
        raise HTTPException(status_code=400, detail="链接无效或已过期，请重新发起绑定。")

    if user.email.strip().lower() != claims.email:
        raise HTTPException(
            status_code=403,
            detail=f"请使用 {claims.email} 登录后重试。",
        )

    is_member = await _check_membership(session, user.id, claims.workspace_id)
    if not is_member:
        raise HTTPException(
            status_code=403,
            detail="你不是该工作区的成员，请联系工作区管理员将你添加后重试。",
        )

    await _upsert_identity_link(session, claims, user.id)
    logger.info(
        "[IM link] linked im_user={} to user={} (account={})",
        claims.im_user_id,
        user.id,
        claims.account_id,
    )
    return _ConfirmResult(ok=True, platform=claims.platform, account_id=claims.account_id)
```

- [ ] **Step 4: Register the router in app.py**

In `backend/cubebox/api/app.py`, find the IM router registration block (around line 542–547):

```python
    from cubebox.api.routes.v1 import admin_im, artifact_share, im_ingress, ws_im
```

Change to:

```python
    from cubebox.api.routes.v1 import admin_im, artifact_share, im_ingress, im_link, ws_im
```

And after line 547 (`app.include_router(admin_im.router, prefix="/api/v1")`), add:

```python
    app.include_router(im_link.router, prefix="/api/v1")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/im/test_im_link_confirm.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/api/routes/v1/im_link.py backend/cubebox/api/app.py \
       backend/tests/unit/im/test_im_link_confirm.py
git commit -m "feat(im): add POST /im/link/confirm endpoint for identity binding"
```

---

### Task 3: Discord `/link` Slash Command

**Files:**
- Modify: `backend/cubebox/im/discord/commands.py`
- Test: `backend/tests/unit/im/discord/test_link_command.py`

**Context:**
- Follow the existing `/new` and `/reset` pattern in `commands.py`
- The `email` parameter is a required string on the slash command
- Reply with ephemeral message containing the confirmation URL
- The frontend URL comes from `config.get("app.base_url", "http://localhost:3000")`

- [ ] **Step 1: Write test for the link command handler**

```python
# backend/tests/unit/im/discord/test_link_command.py
"""Test Discord /link slash command handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cubebox.im.discord.commands import _initiate_link


@pytest.mark.anyio
async def test_link_generates_token_and_replies_ephemeral() -> None:
    interaction = MagicMock()
    interaction.user.id = 123456
    interaction.response.send_message = AsyncMock()

    bot = MagicMock()
    bot._cubebox_account_id = "imca_abc"
    bot._cubebox_workspace_id = "ws_xyz"

    with (
        patch("cubebox.im.discord.commands._get_jwt_secret", return_value="test-secret"),
        patch(
            "cubebox.im.discord.commands._get_frontend_base_url",
            return_value="http://localhost:3000",
        ),
    ):
        await _initiate_link(interaction, bot, email="chris@example.com")

    interaction.response.send_message.assert_awaited_once()
    call_kwargs = interaction.response.send_message.call_args
    msg: str = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("content", "")
    assert "http://localhost:3000/im-link?token=" in msg
    assert call_kwargs.kwargs.get("ephemeral") is True


@pytest.mark.anyio
async def test_link_missing_account_id_replies_error() -> None:
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()

    bot = MagicMock()
    bot._cubebox_account_id = None
    bot._cubebox_workspace_id = None

    await _initiate_link(interaction, bot, email="a@b.com")

    interaction.response.send_message.assert_awaited_once()
    call_kwargs = interaction.response.send_message.call_args
    assert call_kwargs.kwargs.get("ephemeral") is True
    msg = call_kwargs.args[0] if call_kwargs.args else ""
    assert "内部错误" in msg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/im/discord/test_link_command.py -v`
Expected: FAIL — `_initiate_link` not found.

- [ ] **Step 3: Implement the /link command**

Add to `backend/cubebox/im/discord/commands.py`. After the existing imports, add:

```python
from loguru import logger
```

Inside `register_commands()`, after the `cmd_reset` registration (before `bot.tree.sync()`), add:

```python
    @bot.tree.command(name="link", description="Link your Discord account to cubebox")
    @discord.app_commands.describe(email="Your cubebox account email")
    async def cmd_link(interaction: discord.Interaction, email: str) -> None:
        await _initiate_link(interaction, bot, email=email)
```

Add the helper functions at module level (after `_reset_conversation`):

```python
def _get_jwt_secret() -> str:
    from cubebox.config import config

    return str(config.get("auth.jwt_secret", "CHANGE_ME"))


def _get_frontend_base_url() -> str:
    from cubebox.config import config

    return str(config.get("app.base_url", "http://localhost:3000")).rstrip("/")


async def _initiate_link(
    interaction: discord.Interaction,
    bot: commands.Bot,
    *,
    email: str,
) -> None:
    """Generate a link token and reply with the confirmation URL."""
    account_id = getattr(bot, "_cubebox_account_id", None)
    workspace_id = getattr(bot, "_cubebox_workspace_id", None)
    if not account_id or not workspace_id:
        await interaction.response.send_message("内部错误。", ephemeral=True)
        return

    from cubebox.im.link import sign_link_token

    sender_ref = str(interaction.user.id)
    try:
        token = sign_link_token(
            im_user_id=sender_ref,
            email=email,
            account_id=account_id,
            workspace_id=workspace_id,
            platform="discord",
            secret=_get_jwt_secret(),
        )
    except Exception:
        logger.warning("[Discord] sign_link_token failed", exc_info=True)
        await interaction.response.send_message("生成绑定链接失败。", ephemeral=True)
        return

    base = _get_frontend_base_url()
    url = f"{base}/im-link?token={token}"
    await interaction.response.send_message(
        f"点击链接完成绑定：\n{url}",
        ephemeral=True,
    )
```

- [ ] **Step 4: Ensure `_cubebox_workspace_id` is set on the bot**

Read `backend/cubebox/im/discord/gateway.py` to check if `_cubebox_workspace_id` is already set. If not, add it alongside `_cubebox_account_id` in the gateway initialization. The workspace_id comes from the `IMConnectorAccount.workspace_id`.

In `gateway.py`, find where `bot._cubebox_account_id` is set. Add:

```python
bot._cubebox_workspace_id = account.workspace_id
```

right after the `_cubebox_account_id` assignment.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/im/discord/test_link_command.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/im/discord/commands.py backend/cubebox/im/discord/gateway.py \
       backend/tests/unit/im/discord/test_link_command.py
git commit -m "feat(im-discord): add /link slash command for identity binding"
```

---

### Task 4: Feishu `/link` Command Interception

**Files:**
- Modify: `backend/cubebox/api/routes/v1/im_ingress.py`
- Test: `backend/tests/unit/im/test_feishu_link_intercept.py`

**Context:**
- After `connector.parse_inbound(payload)` returns an `InboundEvent`, check if the text matches `/link <email>` or `绑定 <email>`
- If matched: sign a token, reply via `gate_connector.send_to_chat()`, return 200 (skip normal ingest)
- The `gate_connector` (FeishuConnector with live lark Client) is already constructed at line 272 for the identity gate
- Move the gate_connector construction BEFORE the link-command check so it's available for the reply

- [ ] **Step 1: Write test**

```python
# backend/tests/unit/im/test_feishu_link_intercept.py
"""Test Feishu /link command interception."""

from __future__ import annotations

import re

import pytest

from cubebox.api.routes.v1.im_ingress import _parse_link_command


class TestParseLinkCommand:
    def test_link_with_email(self) -> None:
        result = _parse_link_command("/link chris@example.com")
        assert result == "chris@example.com"

    def test_link_chinese(self) -> None:
        result = _parse_link_command("绑定 test@corp.cn")
        assert result == "test@corp.cn"

    def test_link_extra_whitespace(self) -> None:
        result = _parse_link_command("  /link   user@host.com  ")
        assert result == "user@host.com"

    def test_not_a_link_command(self) -> None:
        assert _parse_link_command("hello world") is None
        assert _parse_link_command("/new") is None
        assert _parse_link_command("/link") is None  # no email
        assert _parse_link_command("绑定") is None

    def test_invalid_email_rejected(self) -> None:
        assert _parse_link_command("/link notanemail") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/im/test_feishu_link_intercept.py -v`
Expected: FAIL — `_parse_link_command` not found.

- [ ] **Step 3: Implement the link command parser and interception**

Add the parser function to `backend/cubebox/api/routes/v1/im_ingress.py` (at module level, after imports):

```python
import re as _re

_LINK_RE = _re.compile(
    r"^\s*(?:/link|绑定)\s+(\S+@\S+\.\S+)\s*$",
    _re.IGNORECASE,
)


def _parse_link_command(text: str) -> str | None:
    """Extract email from a /link or 绑定 command. Returns None if not a match."""
    m = _LINK_RE.match(text)
    return m.group(1).strip().lower() if m else None
```

In the `feishu_events()` handler, after `event = connector.parse_inbound(payload)` returns a non-None event (around line 260-263), add the interception block. The gate_connector construction (currently at line 272) must be moved BEFORE this check:

```python
    event = connector.parse_inbound(payload)
    if event is None:
        return Response(status_code=status.HTTP_200_OK)

    event.account_external_id = account.external_account_id
    gate_connector = _build_gate_connector(account, secrets, bot_open_id)

    # Intercept /link or 绑定 commands before normal ingest.
    link_email = _parse_link_command(event.text)
    if link_email is not None:
        await _handle_feishu_link_command(
            email=link_email,
            event=event,
            account=account,
            connector=gate_connector,
        )
        return Response(status_code=status.HTTP_200_OK)

    maker: async_sessionmaker[AsyncSession] = async_session_maker
    result = await ingest_inbound_event(
        event,
        account=account,
        session_maker=maker,
        identity_resolver=gate_connector,
        rejection_notifier=gate_connector,
    )
```

Add the handler function at module level:

```python
async def _handle_feishu_link_command(
    *,
    email: str,
    event: Any,
    account: IMConnectorAccount,
    connector: Any,
) -> None:
    """Generate an identity-link token and reply to the Feishu chat."""
    from cubebox.config import config
    from cubebox.im.link import sign_link_token

    secret = str(config.get("auth.jwt_secret", "CHANGE_ME"))
    sender_ref = event.sender_ref or event.sender_open_id or ""
    if not sender_ref:
        if connector is not None:
            await connector.send_to_chat(event.channel_id, event.reply_to_id, "无法识别发送者。")
        return

    try:
        token = sign_link_token(
            im_user_id=sender_ref,
            email=email,
            account_id=account.id,
            workspace_id=account.workspace_id,
            platform="feishu",
            secret=secret,
        )
    except Exception:
        logger.warning("[Feishu] sign_link_token failed", exc_info=True)
        if connector is not None:
            await connector.send_to_chat(event.channel_id, event.reply_to_id, "生成绑定链接失败。")
        return

    base = str(config.get("app.base_url", "http://localhost:3000")).rstrip("/")
    url = f"{base}/im-link?token={token}"
    text = f"点击链接完成绑定：\n{url}"
    if connector is not None:
        await connector.send_to_chat(event.channel_id, event.reply_to_id, text)
    else:
        logger.warning("[Feishu] no connector to reply with link URL")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/im/test_feishu_link_intercept.py -v`
Expected: 5 passed.

- [ ] **Step 5: Run mypy on modified files**

Run: `cd backend && uv run mypy cubebox/api/routes/v1/im_ingress.py cubebox/im/link.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/api/routes/v1/im_ingress.py \
       backend/tests/unit/im/test_feishu_link_intercept.py
git commit -m "feat(im-feishu): intercept /link and 绑定 commands for identity binding"
```

---

### Task 5: Frontend Confirmation Page + I18n

**Files:**
- Create: `frontend/packages/web/app/(auth)/im-link/page.tsx`
- Create: `frontend/packages/web/components/auth/ImLinkPage.tsx`
- Modify: `frontend/packages/core/src/api/im.ts` (add `confirmImLink`)
- Modify: `frontend/packages/core/src/index.ts` (export new function)
- Modify: `frontend/packages/web/messages/en.json` (add keys)
- Modify: `frontend/packages/web/messages/zh.json` (add keys)

**Context:**
- Follow the `VerifyEmailPage` pattern: `(auth)` layout, read token from search params, call API, show result
- The confirm endpoint requires auth cookie. If the user is not logged in, the API returns 401. On 401, redirect to `/login?redirect=/im-link?token=xxx`

- [ ] **Step 1: Add API function in core**

In `frontend/packages/core/src/api/im.ts`, add at the end:

```ts
export interface ImLinkConfirmResult {
  ok: boolean
  platform: string
  account_id: string
}

export async function confirmImLink(
  client: ApiClient,
  token: string,
): Promise<ImLinkConfirmResult> {
  const res = await client.post('/api/v1/im/link/confirm', { token })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as ImLinkConfirmResult
}
```

- [ ] **Step 2: Export from core index**

Check `frontend/packages/core/src/index.ts` for the existing `im.ts` exports. Add `confirmImLink` to the re-export line. If im.ts functions are re-exported individually, add:

```ts
export { confirmImLink } from './api/im'
```

If they're re-exported via a wildcard, no change needed.

- [ ] **Step 3: Add i18n keys**

In `frontend/packages/web/messages/en.json`, inside the `"im"` object, add:

```json
    "link": {
      "title": "Link IM Account",
      "verifying": "Verifying your identity link...",
      "success": "Your {platform} account has been linked to cubebox successfully.",
      "invalidToken": "This link is invalid or has expired. Please run /link again in your IM.",
      "emailMismatch": "Please log in with the email specified in the /link command.",
      "notMember": "You are not a member of this workspace. Ask the workspace admin to add you.",
      "error": "Linking failed. Please try again.",
      "goToApp": "Go to cubebox"
    }
```

In `frontend/packages/web/messages/zh.json`, inside the `"im"` object, add:

```json
    "link": {
      "title": "绑定 IM 账号",
      "verifying": "正在验证绑定链接...",
      "success": "你的 {platform} 账号已成功绑定到 cubebox。",
      "invalidToken": "链接无效或已过期，请在 IM 中重新发送 /link 命令。",
      "emailMismatch": "请使用 /link 命令中指定的邮箱登录。",
      "notMember": "你不是该工作区的成员，请联系工作区管理员将你添加后重试。",
      "error": "绑定失败，请重试。",
      "goToApp": "进入 cubebox"
    }
```

- [ ] **Step 4: Create the page component**

```tsx
// frontend/packages/web/components/auth/ImLinkPage.tsx
'use client'

import { useEffect, useMemo, useState } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import Link from 'next/link'
import { createApiClient, confirmImLink } from '@cubebox/core'

type Status = 'verifying' | 'success' | 'error'

export function ImLinkPage() {
  const t = useTranslations('im.link')
  const searchParams = useSearchParams()
  const router = useRouter()
  const token = searchParams.get('token')
  const client = useMemo(() => createApiClient(''), [])

  const [status, setStatus] = useState<Status>('verifying')
  const [errorMsg, setErrorMsg] = useState('')
  const [platform, setPlatform] = useState('')

  useEffect(() => {
    if (!token) {
      setStatus('error')
      setErrorMsg(t('invalidToken'))
      return
    }
    confirmImLink(client, token)
      .then((result) => {
        setStatus('success')
        setPlatform(result.platform)
      })
      .catch((err) => {
        if (err?.status === 401) {
          const returnUrl = `/im-link?token=${encodeURIComponent(token)}`
          router.replace(`/login?redirect=${encodeURIComponent(returnUrl)}`)
          return
        }
        setStatus('error')
        setErrorMsg(err?.detail || t('error'))
      })
  }, [client, token, router, t])

  if (status === 'verifying') {
    return <p className="text-center text-sm text-muted-foreground">{t('verifying')}</p>
  }

  if (status === 'success') {
    return (
      <div className="text-center space-y-3">
        <p className="text-sm font-medium">{t('success', { platform })}</p>
        <Link href="/" className="text-sm text-primary underline">
          {t('goToApp')}
        </Link>
      </div>
    )
  }

  return (
    <div className="text-center space-y-3">
      <p className="text-sm text-destructive">{errorMsg}</p>
    </div>
  )
}
```

- [ ] **Step 5: Create the page route**

```tsx
// frontend/packages/web/app/(auth)/im-link/page.tsx
import { ImLinkPage } from '@/components/auth/ImLinkPage'

export default function Page() {
  return <ImLinkPage />
}
```

- [ ] **Step 6: Build and verify**

Run: `cd frontend && pnpm build-core && pnpm --filter web build`
Expected: Build succeeds with no type errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/core/src/api/im.ts \
       frontend/packages/core/src/index.ts \
       frontend/packages/web/messages/en.json \
       frontend/packages/web/messages/zh.json \
       frontend/packages/web/components/auth/ImLinkPage.tsx \
       frontend/packages/web/app/\(auth\)/im-link/page.tsx
git commit -m "feat(im-fe): add identity link confirmation page"
```

---

### Task 6: Smoke Test

**Files:** none (manual verification)

- [ ] **Step 1: Run all unit tests for the IM module**

Run: `cd backend && uv run pytest tests/unit/im/ -v`
Expected: All pass (including existing tests + new tests from tasks 1–4).

- [ ] **Step 2: Run mypy on the full backend**

Run: `cd backend && uv run mypy cubebox/`
Expected: No errors.

- [ ] **Step 3: Run frontend build**

Run: `cd frontend && pnpm build-core && pnpm --filter web build`
Expected: No errors.

- [ ] **Step 4: Final commit (if any fixups needed)**

If any fixes were needed, commit them:

```bash
git commit -m "fix(im): address smoke test findings for identity link"
```
