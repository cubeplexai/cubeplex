"""IM connector service used by workspace + admin routes.

Both scopes share the same CRUD plumbing here; the routes only differ in
the auth dependency they take. ``connect_feishu`` is the single place
bot metadata gets hydrated (via ``/open-apis/bot/v3/info``) so both the
webhook ingress and the long-connection startup glue can rely on reading
it back from the stored credential.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cubebox.im.bot_settings import IMBotSettings

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
    gateways: dict[str, Any] | None = None,
    agg: _RuntimeAgg,
    bot_open_id: str | None,
) -> ImRuntimeStatus:
    """Derive ``ImRuntimeStatus`` from raw aggregates + in-process state.

    ``long_conns`` maps account_id → FeishuLongConnection.
    ``gateways`` maps account_id → DiscordGateway.
    ``bot_open_id`` is decrypted upstream from the credential row.
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
    elif account.delivery_mode in ("gateway", "stream"):
        gws = gateways or {}
        gw = gws.get(account.id)
        if gw is not None and getattr(gw, "is_open", lambda: False)():
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


async def _load_bot_open_id_via_credentials(
    creds: CredentialService, *, credential_id: str
) -> str | None:
    """Decrypt the IM bot's credential row and return ``bot_open_id``.

    Returns None on any error — the runtime status path uses None as
    the "never_connected" signal so a transient decrypt failure doesn't
    flap pills.
    """
    try:
        plaintext = await creds.get_decrypted(credential_id=credential_id, requesting_kind="im_bot")
        return str(json.loads(plaintext).get("bot_open_id") or "") or None
    except Exception:
        logger.opt(exception=True).warning(
            "[IM] could not load bot_open_id for credential {}; runtime shows never_connected",
            credential_id,
        )
        return None


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

        bot_open_id, bot_app_name, bot_avatar_url = await self._hydrate_bot_info(
            app_id, app_secret, domain
        )
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
                config={
                    "bot_app_name": bot_app_name or None,
                    "bot_avatar_url": bot_avatar_url or None,
                },
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
                logger.opt(exception=True).warning(
                    "[IM] orphan credential {} could not be rolled back; manual cleanup needed",
                    credential_id,
                )
            raise

    async def connect_discord(
        self,
        *,
        workspace_id: str,
        bot_token: str,
        application_id: str,
        acting_user_id: str,
    ) -> IMConnectorAccount:
        """Bind one Discord bot: validate token, store credential, return account."""
        existing = (
            await self._session.execute(
                select(IMConnectorAccount).where(
                    IMConnectorAccount.org_id == self._org_id,  # type: ignore[arg-type]
                    IMConnectorAccount.platform == "discord",  # type: ignore[arg-type]
                    IMConnectorAccount.external_account_id == application_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError(
                f"discord account already exists for application_id={application_id}"
                f" (id={existing.id})"
            )

        bot_user_id, bot_username, bot_avatar_url = await self._hydrate_discord_bot_info(bot_token)

        secret_payload = json.dumps(
            {
                "bot_token": bot_token,
                "application_id": application_id,
                "bot_open_id": bot_user_id,
            }
        )
        try:
            credential_id = await self._credentials.create(
                kind="im_bot",
                name=f"discord:{application_id}",
                plaintext=secret_payload,
            )
        except IntegrityError as exc:
            await self._session.rollback()
            raise ValueError(
                f"discord account already exists for application_id={application_id}"
                " (credential race)"
            ) from exc
        try:
            account = IMConnectorAccount(
                org_id=self._org_id,
                workspace_id=workspace_id,
                platform="discord",
                external_account_id=application_id,
                acting_user_id=acting_user_id,
                credential_id=credential_id,
                delivery_mode="gateway",
                config={
                    "bot_app_name": bot_username or None,
                    "bot_avatar_url": bot_avatar_url or None,
                },
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
                logger.opt(exception=True).warning(
                    "[IM] orphan credential {} could not be rolled back",
                    credential_id,
                )
            raise

    async def connect_slack(
        self,
        *,
        workspace_id: str,
        bot_token: str,
        app_token: str,
        acting_user_id: str,
    ) -> IMConnectorAccount:
        """Bind one Slack bot: validate token, store credential, return account."""
        team_id, bot_user_id, bot_name, bot_avatar_url = await self._hydrate_slack_bot_info(
            bot_token
        )

        existing = (
            await self._session.execute(
                select(IMConnectorAccount).where(
                    IMConnectorAccount.org_id == self._org_id,  # type: ignore[arg-type]
                    IMConnectorAccount.platform == "slack",  # type: ignore[arg-type]
                    IMConnectorAccount.external_account_id == team_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError(
                f"slack account already exists for team_id={team_id} (id={existing.id})"
            )

        secret_payload = json.dumps(
            {
                "bot_token": bot_token,
                "app_token": app_token,
                "bot_open_id": bot_user_id,
            }
        )
        try:
            credential_id = await self._credentials.create(
                kind="im_bot",
                name=f"slack:{team_id}",
                plaintext=secret_payload,
            )
        except IntegrityError as exc:
            await self._session.rollback()
            raise ValueError(
                f"slack account already exists for team_id={team_id} (credential race)"
            ) from exc
        try:
            account = IMConnectorAccount(
                org_id=self._org_id,
                workspace_id=workspace_id,
                platform="slack",
                external_account_id=team_id,
                acting_user_id=acting_user_id,
                credential_id=credential_id,
                delivery_mode="gateway",
                config={
                    "bot_app_name": bot_name or None,
                    "bot_avatar_url": bot_avatar_url or None,
                },
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
                logger.opt(exception=True).warning(
                    "[IM] orphan credential {} could not be rolled back",
                    credential_id,
                )
            raise

    async def connect_teams(
        self,
        *,
        workspace_id: str,
        app_id: str,
        app_secret: str,
        tenant_id: str,
        acting_user_id: str,
    ) -> IMConnectorAccount:
        """Bind one Teams bot: validate credentials, store credential, return account."""
        existing = (
            await self._session.execute(
                select(IMConnectorAccount).where(
                    IMConnectorAccount.org_id == self._org_id,  # type: ignore[arg-type]
                    IMConnectorAccount.platform == "teams",  # type: ignore[arg-type]
                    IMConnectorAccount.external_account_id == app_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError(f"teams account already exists for app_id={app_id} (id={existing.id})")

        bot_name = await self._hydrate_teams_bot_info(app_id, app_secret, tenant_id)

        secret_payload = json.dumps(
            {
                "app_id": app_id,
                "app_secret": app_secret,
                "tenant_id": tenant_id,
                "bot_open_id": app_id,
            }
        )
        try:
            credential_id = await self._credentials.create(
                kind="im_bot",
                name=f"teams:{app_id}",
                plaintext=secret_payload,
            )
        except IntegrityError as exc:
            await self._session.rollback()
            raise ValueError(
                f"teams account already exists for app_id={app_id} (credential race)"
            ) from exc
        try:
            account = IMConnectorAccount(
                org_id=self._org_id,
                workspace_id=workspace_id,
                platform="teams",
                external_account_id=app_id,
                acting_user_id=acting_user_id,
                credential_id=credential_id,
                delivery_mode="webhook",
                config={
                    "bot_app_name": bot_name or None,
                    "bot_avatar_url": None,
                    "tenant_id": tenant_id,
                },
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
                logger.opt(exception=True).warning(
                    "[IM] orphan credential {} could not be rolled back",
                    credential_id,
                )
            raise

    async def _hydrate_teams_bot_info(
        self,
        app_id: str,
        app_secret: str,
        tenant_id: str,
    ) -> str:
        """Validate Teams bot credentials via OAuth2 token request.

        Returns the bot display name (app_id as fallback). Raises ValueError
        on invalid credentials.
        """
        import httpx

        token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": app_id,
                        "client_secret": app_secret,
                        "scope": "https://api.botframework.com/.default",
                    },
                    timeout=10.0,
                )
                if resp.status_code != 200:
                    raise ValueError(
                        f"Teams credential validation failed (HTTP {resp.status_code}): "
                        f"{resp.text[:200]}"
                    )
                data = resp.json()
                if "access_token" not in data:
                    raise ValueError("Teams credential validation failed: no access_token")
                return app_id
        except ValueError:
            raise
        except Exception:
            logger.exception("[IM] Teams credential validation failed")
            raise ValueError("could not validate Teams bot credentials") from None

    async def _hydrate_slack_bot_info(
        self,
        bot_token: str,
    ) -> tuple[str, str, str, str]:
        """Validate bot token via Slack ``auth.test`` + ``users.info``.

        Returns ``(team_id, bot_user_id, bot_name, avatar_url)``.
        """
        import httpx

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://slack.com/api/auth.test",
                    headers={"Authorization": f"Bearer {bot_token}"},
                    timeout=10.0,
                )
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    raise ValueError(f"Slack auth.test failed: {data.get('error', 'unknown')}")
                team_id = str(data["team_id"])
                bot_user_id = str(data["user_id"])

                resp2 = await client.get(
                    "https://slack.com/api/users.info",
                    params={"user": bot_user_id},
                    headers={"Authorization": f"Bearer {bot_token}"},
                    timeout=10.0,
                )
                resp2.raise_for_status()
                user_data = resp2.json()
                bot_name = ""
                bot_avatar_url = ""
                if user_data.get("ok"):
                    profile = user_data.get("user", {}).get("profile", {})
                    bot_name = (
                        user_data.get("user", {}).get("real_name")
                        or profile.get("display_name")
                        or ""
                    )
                    bot_avatar_url = profile.get("image_72", "")
                return team_id, bot_user_id, bot_name, bot_avatar_url
        except ValueError:
            raise
        except Exception:
            logger.exception("[IM] Slack auth.test probe failed")
            raise ValueError("could not validate Slack bot token") from None

    async def connect_dingtalk(
        self,
        *,
        workspace_id: str,
        app_key: str,
        app_secret: str,
        acting_user_id: str,
    ) -> IMConnectorAccount:
        """Bind one DingTalk enterprise bot: validate credentials, store, return account."""
        existing = (
            await self._session.execute(
                select(IMConnectorAccount).where(
                    IMConnectorAccount.org_id == self._org_id,  # type: ignore[arg-type]
                    IMConnectorAccount.platform == "dingtalk",  # type: ignore[arg-type]
                    IMConnectorAccount.external_account_id == app_key,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError(
                f"dingtalk account already exists for app_key={app_key} (id={existing.id})"
            )

        await self._validate_dingtalk_credentials(app_key, app_secret)

        secret_payload = json.dumps(
            {
                "app_key": app_key,
                "app_secret": app_secret,
                "bot_open_id": app_key,
            }
        )
        try:
            credential_id = await self._credentials.create(
                kind="im_bot",
                name=f"dingtalk:{app_key}",
                plaintext=secret_payload,
            )
        except IntegrityError as exc:
            await self._session.rollback()
            raise ValueError(
                f"dingtalk account already exists for app_key={app_key} (credential race)"
            ) from exc
        try:
            account = IMConnectorAccount(
                org_id=self._org_id,
                workspace_id=workspace_id,
                platform="dingtalk",
                external_account_id=app_key,
                acting_user_id=acting_user_id,
                credential_id=credential_id,
                delivery_mode="stream",
                config={
                    "bot_app_name": None,
                    "bot_avatar_url": None,
                },
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
                logger.opt(exception=True).warning(
                    "[IM] orphan credential {} could not be rolled back",
                    credential_id,
                )
            raise

    async def _validate_dingtalk_credentials(
        self,
        app_key: str,
        app_secret: str,
    ) -> None:
        """Validate credentials via access token exchange."""
        import httpx

        url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        payload = {"appKey": app_key, "appSecret": app_secret}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                raise ValueError(f"DingTalk credential validation failed: {resp.text}")
            data = resp.json()
            token = data.get("accessToken")
            if not token:
                raise ValueError("DingTalk returned empty access token")

    async def _hydrate_discord_bot_info(
        self,
        bot_token: str,
    ) -> tuple[str, str, str]:
        """Validate bot token via Discord API ``GET /users/@me``.

        Returns ``(bot_user_id, username, avatar_url)``. All empty on failure.
        """
        import httpx

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://discord.com/api/v10/users/@me",
                    headers={"Authorization": f"Bot {bot_token}"},
                    timeout=10.0,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "[IM] Discord /users/@me returned {}: {}",
                        resp.status_code,
                        resp.text[:200],
                    )
                    raise ValueError(
                        f"Discord bot token validation failed (HTTP {resp.status_code})"
                    )
                data = resp.json()
                user_id = str(data.get("id") or "")
                username = str(data.get("username") or "")
                avatar_hash = str(data.get("avatar") or "")
                avatar_url = ""
                if avatar_hash and user_id:
                    avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png"
                return user_id, username, avatar_url
        except ValueError:
            raise
        except Exception:
            logger.exception("[IM] Discord /users/@me probe failed")
            raise ValueError("could not validate Discord bot token") from None

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

    async def load_bot_open_id(self, account: IMConnectorAccount) -> str | None:
        """Decrypt the account's credential and return ``bot_open_id``."""
        return await _load_bot_open_id_via_credentials(
            self._credentials, credential_id=account.credential_id
        )

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
            logger.opt(exception=True).debug("[IM] credential delete after account removal failed")

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

    async def update_bot_settings(
        self,
        *,
        account_id: str,
        settings: IMBotSettings,
        workspace_id: str | None = None,
    ) -> IMConnectorAccount | None:
        """Merge account-level bot settings into ``config`` and persist.

        Reassigns ``config`` (not in-place mutation) so SQLAlchemy detects the
        JSON change. Returns None if the account is absent / out of scope.
        """
        from cubebox.im.bot_settings import store_bot_settings

        account = await self.get(account_id=account_id, workspace_id=workspace_id)
        if account is None:
            return None
        account.config = store_bot_settings(account.config, settings)
        self._session.add(account)
        await self._session.commit()
        await self._session.refresh(account)
        return account

    async def _hydrate_bot_info(
        self,
        app_id: str,
        app_secret: str,
        domain: str,
    ) -> tuple[str, str, str]:
        """Probe ``/open-apis/bot/v3/info`` with the tenant access token.

        Returns ``(open_id, app_name, avatar_url)``. All three are empty
        strings on failure; the caller should treat an empty ``open_id``
        as a fatal credential error.
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
                return "", "", ""
            data = json.loads(raw)
            bot = data.get("bot") or {}
            open_id = str(bot.get("open_id") or "")
            if not open_id:
                logger.warning(
                    "[IM] /bot/v3/info probe missing bot.open_id for app_id={}: {}",
                    app_id,
                    data,
                )
            app_name = str(bot.get("app_name") or "")
            avatar_url = str(bot.get("avatar_url") or "")
            return open_id, app_name, avatar_url
        except Exception:
            logger.exception("[IM] /bot/v3/info probe failed for app_id={}", app_id)
            return "", "", ""
