"""IM connector service used by workspace + admin routes.

Both scopes share the same CRUD plumbing here; the routes only differ in
the auth dependency they take. ``connect_feishu`` is the single place
``bot_open_id`` gets hydrated (via ``/open-apis/bot/v3/info``) so both the
webhook ingress and the long-connection startup glue can rely on reading
it back from the stored credential.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.im_connector import IMConnectorAccount
from cubebox.services.credential import CredentialService


class IMConnectorService:
    """Service backing the workspace + admin IM connector routes."""

    def __init__(
        self,
        session: AsyncSession,
        credentials: CredentialService,
        *,
        org_id: str,
    ) -> None:
        self._session = session
        self._credentials = credentials
        self._org_id = org_id

    async def connect_feishu(
        self,
        *,
        workspace_id: str,
        app_id: str,
        app_secret: str,
        encrypt_key: str,
        verification_token: str,
        domain: str,
        delivery_mode: str,
        acting_user_id: str,
    ) -> IMConnectorAccount:
        """Bind one Feishu app: hydrate the bot identity, store the credential, return the account.

        Hydration goes through ``/open-apis/bot/v3/info`` with just the
        tenant access token (no extra scopes). If the call fails we still
        proceed — the account is created with ``bot_open_id`` empty and
        the parser falls into its PoC-path passthrough; the operator can
        edit the credential later to populate it. Hydration failures are
        logged loudly so this degraded state is not silent.
        """
        bot_open_id = await self._hydrate_bot_open_id(app_id, app_secret, domain)
        secret_payload = json.dumps(
            {
                "app_id": app_id,
                "app_secret": app_secret,
                "encrypt_key": encrypt_key,
                "verification_token": verification_token,
                "domain": domain,
                "bot_open_id": bot_open_id,
            }
        )
        credential_id = await self._credentials.create(
            kind="im_bot",
            name=f"feishu:{app_id}",
            plaintext=secret_payload,
        )
        account = IMConnectorAccount(
            org_id=self._org_id,
            workspace_id=workspace_id,
            platform="feishu",
            external_account_id=app_id,
            acting_user_id=acting_user_id,
            credential_id=credential_id,
            delivery_mode=delivery_mode,
        )
        self._session.add(account)
        await self._session.commit()
        await self._session.refresh(account)
        return account

    async def list_for_workspace(self, *, workspace_id: str) -> list[IMConnectorAccount]:
        stmt = select(IMConnectorAccount).where(
            IMConnectorAccount.org_id == self._org_id,  # type: ignore[arg-type]
            IMConnectorAccount.workspace_id == workspace_id,  # type: ignore[arg-type]
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_for_org(self) -> list[IMConnectorAccount]:
        stmt = select(IMConnectorAccount).where(
            IMConnectorAccount.org_id == self._org_id  # type: ignore[arg-type]
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get(self, *, account_id: str) -> IMConnectorAccount | None:
        stmt = select(IMConnectorAccount).where(
            IMConnectorAccount.id == account_id,  # type: ignore[arg-type]
            IMConnectorAccount.org_id == self._org_id,  # type: ignore[arg-type]
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def delete(self, *, account_id: str) -> None:
        account = await self.get(account_id=account_id)
        if account is None:
            return
        credential_id = account.credential_id
        await self._session.delete(account)
        await self._session.commit()
        try:
            await self._credentials.delete(credential_id=credential_id)
        except Exception:
            logger.debug("[IM] credential delete after account removal failed", exc_info=True)

    async def set_enabled(self, *, account_id: str, enabled: bool) -> IMConnectorAccount | None:
        account = await self.get(account_id=account_id)
        if account is None:
            return None
        account.enabled = enabled
        self._session.add(account)
        await self._session.commit()
        await self._session.refresh(account)
        return account

    async def _hydrate_bot_open_id(
        self,
        app_id: str,
        app_secret: str,
        domain: str,
    ) -> str:
        """Probe ``/open-apis/bot/v3/info`` with the tenant access token.

        Returns the bot's own open_id, or an empty string on failure.
        """
        try:
            import asyncio

            import lark_oapi as lark
            from lark_oapi.core import AccessTokenType, HttpMethod
            from lark_oapi.core.const import FEISHU_DOMAIN, LARK_DOMAIN
            from lark_oapi.core.model import BaseRequest

            d = LARK_DOMAIN if domain == "lark" else FEISHU_DOMAIN
            client = (
                lark.Client.builder()
                .app_id(app_id)
                .app_secret(app_secret)
                .domain(d)
                .log_level(lark.LogLevel.WARNING)
                .build()
            )

            def _probe() -> Any:
                req = (
                    BaseRequest.builder()
                    .http_method(HttpMethod.GET)
                    .uri("/open-apis/bot/v3/info")
                    .token_types({AccessTokenType.TENANT})
                    .build()
                )
                return client.request(req)

            resp = await asyncio.to_thread(_probe)
            raw = getattr(getattr(resp, "raw", None), "content", None)
            if not raw:
                logger.warning("[IM] /bot/v3/info probe returned no content for app_id={}", app_id)
                return ""
            data = json.loads(raw)
            bot = data.get("bot") or {}
            open_id = str(bot.get("open_id") or "")
            if not open_id:
                logger.warning(
                    "[IM] /bot/v3/info probe missing bot.open_id for app_id={}: {}",
                    app_id,
                    data,
                )
            return open_id
        except Exception:
            logger.exception("[IM] /bot/v3/info probe failed for app_id={}", app_id)
            return ""
