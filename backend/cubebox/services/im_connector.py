"""IM connector service used by workspace + admin routes.

Both scopes share the same CRUD plumbing here; the routes only differ in
the auth dependency they take. ``connect_feishu`` is the single place
``bot_open_id`` gets hydrated (via ``/open-apis/bot/v3/info``) so both the
webhook ingress and the long-connection startup glue can rely on reading
it back from the stored credential.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.im_connector import ImRuntimeStatus
from cubebox.models.im_connector import IMConnectorAccount
from cubebox.repositories.im_connector import _RuntimeAgg
from cubebox.services.credential import CredentialService
from cubebox.utils.time import utc_isoformat

_WEBHOOK_FRESHNESS_WINDOW = timedelta(minutes=60)


def compute_runtime(
    account: IMConnectorAccount,
    *,
    long_conns: dict[str, Any],
    agg: _RuntimeAgg,
    bot_open_id: str | None,
) -> ImRuntimeStatus:
    """Derive ``ImRuntimeStatus`` from raw aggregates + in-process LC table.

    ``long_conns`` maps account_id → FeishuLongConnection (typed loosely
    to keep the service free of the SDK class import). ``bot_open_id``
    is decrypted upstream from the credential row.
    """
    state: str
    if bot_open_id is None:
        state = "never_connected"
    elif account.delivery_mode == "long_connection":
        lc = long_conns.get(account.id)
        if lc is not None and getattr(lc, "is_open", lambda: False)():
            state = "connected"
        else:
            state = "disconnected"
    else:  # webhook
        if (
            agg.last_receipt_at is not None
            and (datetime.now(UTC) - agg.last_receipt_at) < _WEBHOOK_FRESHNESS_WINDOW
        ):
            state = "connected"
        else:
            state = "disconnected"
    return ImRuntimeStatus(
        connection_state=state,  # type: ignore[arg-type]
        last_inbound_at=utc_isoformat(agg.last_receipt_at) if agg.last_receipt_at else None,
        bot_open_id=bot_open_id,
        pending_queue=agg.pending_count,
        matched_24h=agg.matched_24h,
        rejected_24h=agg.rejected_24h,
    )


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
        # Preflight: refuse early if an account already exists for this
        # platform+app_id. Without this, ``CredentialService.create`` commits
        # the credential successfully but the account INSERT then hits
        # ``uq_im_account_platform_external`` and raises — leaving an
        # orphan ``feishu:{app_id}`` credential whose unique-name constraint
        # blocks every subsequent retry. The plan's "delete-and-recreate is
        # the rotation path" workflow depends on this preflight working.
        existing = (
            await self._session.execute(
                select(IMConnectorAccount).where(
                    IMConnectorAccount.platform == "feishu",  # type: ignore[arg-type]
                    IMConnectorAccount.external_account_id == app_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError(
                f"feishu account already exists for app_id={app_id} (id={existing.id})"
            )

        bot_open_id = await self._hydrate_bot_open_id(app_id, app_secret, domain)
        if not bot_open_id:
            # Without ``bot_open_id`` the long-connection / webhook startup
            # would refuse to bind this account (the mention gate + bot-echo
            # guard need it), leaving the workspace with a connected-but-silent
            # bot. Fail loudly NOW so the operator sees a real error from
            # the API rather than discovering it weeks later when their bot
            # ignores every message.
            raise ValueError(
                f"could not hydrate bot_open_id for app_id={app_id} — check that "
                "the app_secret is correct and the bot identity is published"
            )
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
        # Best-effort atomicity: create the credential (commits inside the
        # service), then INSERT the account. If the account INSERT fails
        # (e.g. concurrent insert lost the preflight race, FK violation,
        # transient DB error), roll back the orphan credential so a retry
        # doesn't bounce off ``uq_credential_org_kind_name``.
        try:
            credential_id = await self._credentials.create(
                kind="im_bot",
                name=f"feishu:{app_id}",
                plaintext=secret_payload,
            )
        except IntegrityError as exc:
            # Same-org double-submit race: both requests passed the
            # account preflight, then the loser's credential insert hits
            # ``uq_credential_org_kind_name``. Surface as the duplicate
            # case so the route can map to 409, not 500.
            await self._session.rollback()
            raise ValueError(
                f"feishu account already exists for app_id={app_id} (credential race)"
            ) from exc
        try:
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
        except Exception:
            await self._session.rollback()
            try:
                await self._credentials.delete(credential_id=credential_id)
            except Exception:
                logger.warning(
                    "[IM] orphan credential {} could not be rolled back; manual cleanup needed",
                    credential_id,
                    exc_info=True,
                )
            raise

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

    async def get(
        self,
        *,
        account_id: str,
        workspace_id: str | None = None,
    ) -> IMConnectorAccount | None:
        """Look up an account by id.

        ``workspace_id`` is optional but **required for cross-workspace
        isolation when called from a workspace-scoped route**. Org-admin
        routes operate at org level and pass ``workspace_id=None``; the
        workspace routes MUST pass their own ``ctx.workspace_id`` so a
        member of workspace A cannot operate on an account in workspace B
        within the same org.
        """
        stmt = select(IMConnectorAccount).where(
            IMConnectorAccount.id == account_id,  # type: ignore[arg-type]
            IMConnectorAccount.org_id == self._org_id,  # type: ignore[arg-type]
        )
        if workspace_id is not None:
            stmt = stmt.where(IMConnectorAccount.workspace_id == workspace_id)  # type: ignore[arg-type]
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def delete(
        self,
        *,
        account_id: str,
        workspace_id: str | None = None,
    ) -> None:
        account = await self.get(account_id=account_id, workspace_id=workspace_id)
        if account is None:
            return
        credential_id = account.credential_id
        await self._session.delete(account)
        await self._session.commit()
        try:
            await self._credentials.delete(credential_id=credential_id)
        except Exception:
            logger.debug("[IM] credential delete after account removal failed", exc_info=True)

    async def set_enabled(
        self,
        *,
        account_id: str,
        enabled: bool,
        workspace_id: str | None = None,
    ) -> IMConnectorAccount | None:
        account = await self.get(account_id=account_id, workspace_id=workspace_id)
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
