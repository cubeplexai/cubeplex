"""Feishu/Lark QR-code one-click bot registration manager.

Wraps ``lark_oapi.aregister_app`` (OAuth 2.0 Device Authorization Grant) so
workspace users can scan a QR code in Feishu/Lark to provision a pre-configured
bot app and bind it directly into CubePlex without visiting the developer
console.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import lark_oapi as lark
from lark_oapi.scene.registration.errors import (
    AppAccessDeniedError,
    AppExpiredError,
    RegisterAppError,
)
from loguru import logger

from cubeplex.api.schemas.im_connector import IMAccountOut
from cubeplex.utils.time import utc_isoformat

FeishuDomain = Literal["feishu", "lark"]
QrAttemptState = Literal[
    "starting",
    "qr_ready",
    "polling",
    "slow_down",
    "domain_switched",
    "connecting",
    "succeeded",
    "expired",
    "cancelled",
    "error",
]

_ACTIVE_STATES: frozenset[str] = frozenset(
    {"starting", "qr_ready", "polling", "slow_down", "domain_switched", "connecting"}
)
_TERMINAL_RETENTION = timedelta(minutes=10)
_QR_READY_TIMEOUT_SECONDS = 15.0

FEISHU_QR_ADDONS: dict[str, Any] = {
    "preset": False,
    "scopes": {
        "tenant": [
            "im:message.p2p_msg:readonly",
            "im:message.group_at_msg:readonly",
            "im:message:send_as_bot",
            "im:message:update",
            "im:resource",
            "contact:contact.base:readonly",
            "cardkit:card:read",
            "cardkit:card:write",
            "im:message.reactions:read",
            "im:message.reactions:write_only",
            "im:chat:readonly",
            "contact:user.email:readonly",
        ],
    },
    "events": {
        "items": {
            "tenant": ["im.message.receive_v1"],
        }
    },
    "callbacks": {
        "items": ["card.action.trigger"],
    },
}

FEISHU_APP_PRESET: dict[str, str] = {
    "name": "CubePlex AI Assistant",
    "desc": "Connect Feishu / Lark with your CubePlex workspace.",
}

RegisterAppFn = Callable[..., Awaitable[dict[str, Any]]]
ConnectBotFn = Callable[[str, str, FeishuDomain], Awaitable[IMAccountOut]]


def _accounts_url(domain: FeishuDomain) -> str:
    return "https://accounts.larksuite.com" if domain == "lark" else "https://accounts.feishu.cn"


@dataclass
class FeishuQrSnapshot:
    """Public snapshot of a Feishu QR registration attempt (never includes secrets)."""

    attempt_id: str
    state: QrAttemptState
    domain: FeishuDomain
    qr_url: str | None = None
    expires_at: str | None = None
    account: IMAccountOut | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class _AttemptRecord:
    attempt_id: str
    org_id: str
    workspace_id: str
    user_id: str
    domain: FeishuDomain
    connect_bot: ConnectBotFn
    state: QrAttemptState = "starting"
    qr_url: str | None = None
    expires_at: datetime | None = None
    domain_switched: bool = False
    account: IMAccountOut | None = None
    error_code: str | None = None
    error_message: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    qr_ready_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None

    def snapshot(self) -> FeishuQrSnapshot:
        show_qr = self.state in _ACTIVE_STATES and self.qr_url is not None
        return FeishuQrSnapshot(
            attempt_id=self.attempt_id,
            state=self.state,
            domain=self.domain,
            qr_url=self.qr_url if show_qr else None,
            expires_at=utc_isoformat(self.expires_at) if (show_qr and self.expires_at) else None,
            account=self.account,
            error_code=self.error_code,
            error_message=self.error_message,
        )


def _default_register_app(**kwargs: Any) -> Awaitable[dict[str, Any]]:
    return lark.aregister_app(**kwargs)  # type: ignore[no-any-return]


class FeishuQrRegistrationManager:
    """In-process registry for active and recently finished Feishu QR onboarding attempts."""

    def __init__(
        self,
        *,
        register_app: RegisterAppFn | None = None,
        qr_ready_timeout: float = _QR_READY_TIMEOUT_SECONDS,
    ) -> None:
        self._register_app: RegisterAppFn = register_app or _default_register_app
        self._qr_ready_timeout = qr_ready_timeout
        self._attempts: dict[str, _AttemptRecord] = {}

    def _prune(self, now: datetime) -> None:
        cutoff = now - _TERMINAL_RETENTION
        stale = [
            aid
            for aid, rec in self._attempts.items()
            if rec.state not in _ACTIVE_STATES and rec.updated_at < cutoff
        ]
        for aid in stale:
            self._attempts.pop(aid, None)

    def _check_expiry(self, rec: _AttemptRecord, now: datetime) -> None:
        if (
            rec.state in _ACTIVE_STATES
            and rec.state != "connecting"
            and rec.expires_at is not None
            and now >= rec.expires_at
        ):
            rec.state = "expired"
            rec.error_code = "expired_token"
            rec.error_message = "The registration QR code expired."
            rec.updated_at = now
            if rec.task is not None and not rec.task.done():
                rec.task.cancel()

    def _get_scoped(
        self,
        attempt_id: str,
        *,
        org_id: str,
        workspace_id: str,
        user_id: str,
    ) -> _AttemptRecord | None:
        rec = self._attempts.get(attempt_id)
        if rec is None:
            return None
        if rec.org_id != org_id or rec.workspace_id != workspace_id or rec.user_id != user_id:
            return None
        self._check_expiry(rec, datetime.now(UTC))
        return rec

    async def start(
        self,
        *,
        org_id: str,
        workspace_id: str,
        user_id: str,
        domain: FeishuDomain = "feishu",
        connect_bot: ConnectBotFn,
    ) -> FeishuQrSnapshot:
        """Start a new QR registration attempt and wait briefly for the QR URL."""
        now = datetime.now(UTC)
        self._prune(now)

        # Cancel any prior active attempt by the same user in this workspace.
        for existing in list(self._attempts.values()):
            if (
                existing.org_id == org_id
                and existing.workspace_id == workspace_id
                and existing.user_id == user_id
                and existing.state in _ACTIVE_STATES
                and existing.state != "connecting"
            ):
                self._cancel_record(existing, now)

        attempt_id = f"fqr_{secrets.token_urlsafe(12)}"
        rec = _AttemptRecord(
            attempt_id=attempt_id,
            org_id=org_id,
            workspace_id=workspace_id,
            user_id=user_id,
            domain=domain,
            connect_bot=connect_bot,
            updated_at=now,
        )
        self._attempts[attempt_id] = rec
        rec.task = asyncio.create_task(self._run_attempt(rec))

        try:
            await asyncio.wait_for(rec.qr_ready_event.wait(), timeout=self._qr_ready_timeout)
        except TimeoutError:
            if rec.state == "starting":
                self._cancel_record(rec, datetime.now(UTC))
                rec.state = "error"
                rec.error_code = "qr_timeout"
                rec.error_message = "Timed out waiting for Feishu QR code."
        return rec.snapshot()

    def get_status(
        self,
        attempt_id: str,
        *,
        org_id: str,
        workspace_id: str,
        user_id: str,
    ) -> FeishuQrSnapshot | None:
        """Return current snapshot for an attempt owned by the caller, or None."""
        rec = self._get_scoped(
            attempt_id,
            org_id=org_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        return rec.snapshot() if rec is not None else None

    def cancel(
        self,
        attempt_id: str,
        *,
        org_id: str,
        workspace_id: str,
        user_id: str,
    ) -> FeishuQrSnapshot | None:
        """Cancel an active attempt owned by the caller."""
        rec = self._get_scoped(
            attempt_id,
            org_id=org_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        if rec is None:
            return None
        if rec.state in _ACTIVE_STATES and rec.state != "connecting":
            self._cancel_record(rec, datetime.now(UTC))
        return rec.snapshot()

    async def shutdown(self) -> None:
        """Cancel all in-flight QR registration tasks during app shutdown."""
        now = datetime.now(UTC)
        tasks: list[asyncio.Task[None]] = []
        for rec in list(self._attempts.values()):
            if rec.task is not None and not rec.task.done():
                self._cancel_record(rec, now)
                tasks.append(rec.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _cancel_record(self, rec: _AttemptRecord, now: datetime) -> None:
        rec.state = "cancelled"
        rec.error_code = "abort"
        rec.error_message = "Registration was cancelled."
        rec.updated_at = now
        rec.qr_ready_event.set()
        if rec.task is not None and not rec.task.done():
            rec.task.cancel()

    async def _run_attempt(self, rec: _AttemptRecord) -> None:
        def on_qr_code(info: dict[str, Any]) -> None:
            if rec.state in ("cancelled", "expired", "error"):
                return
            url = info.get("url")
            expire_in = int(info.get("expire_in") or 600)
            now = datetime.now(UTC)
            rec.qr_url = str(url) if url else None
            rec.expires_at = now + timedelta(seconds=expire_in)
            rec.state = "qr_ready"
            rec.updated_at = now
            rec.qr_ready_event.set()

        def on_status_change(info: dict[str, Any]) -> None:
            if rec.state not in _ACTIVE_STATES or rec.state == "connecting":
                return
            status_val = info.get("status")
            now = datetime.now(UTC)
            if status_val == "domain_switched":
                rec.domain_switched = True
                rec.domain = "lark"
                rec.state = "domain_switched"
                rec.updated_at = now
            elif status_val in ("polling", "slow_down"):
                rec.state = status_val
                rec.updated_at = now

        try:
            result = await self._register_app(
                on_qr_code=on_qr_code,
                on_status_change=on_status_change,
                source="cubeplex",
                domain=_accounts_url(rec.domain),
                lark_domain="https://accounts.larksuite.com",
                app_preset=FEISHU_APP_PRESET,
                addons=FEISHU_QR_ADDONS,
                create_only=True,
            )
        except asyncio.CancelledError:
            if rec.state not in ("cancelled", "expired"):
                rec.state = "cancelled"
                rec.error_code = "abort"
                rec.error_message = "Registration was cancelled."
                rec.updated_at = datetime.now(UTC)
            rec.qr_ready_event.set()
            return
        except AppAccessDeniedError:
            rec.state = "error"
            rec.error_code = "access_denied"
            rec.error_message = "Authorization was denied in Feishu/Lark."
            rec.updated_at = datetime.now(UTC)
            rec.qr_ready_event.set()
            return
        except AppExpiredError:
            rec.state = "expired"
            rec.error_code = "expired_token"
            rec.error_message = "The registration QR code expired."
            rec.updated_at = datetime.now(UTC)
            rec.qr_ready_event.set()
            return
        except RegisterAppError as exc:
            logger.warning("[FeishuQR] register_app failed: {}", exc.error)
            rec.state = "error"
            rec.error_code = exc.error or "registration_failed"
            rec.error_message = "Unable to complete Feishu app registration."
            rec.updated_at = datetime.now(UTC)
            rec.qr_ready_event.set()
            return
        except Exception:
            logger.opt(exception=True).warning("[FeishuQR] unexpected error during register_app")
            rec.state = "error"
            rec.error_code = "registration_failed"
            rec.error_message = "Unable to complete Feishu app registration."
            rec.updated_at = datetime.now(UTC)
            rec.qr_ready_event.set()
            return

        if rec.state in ("cancelled", "expired"):
            return

        client_id = result.get("client_id")
        client_secret = result.get("client_secret")
        if (
            not isinstance(client_id, str)
            or not client_id
            or not isinstance(client_secret, str)
            or not client_secret
        ):
            rec.state = "error"
            rec.error_code = "invalid_credentials"
            rec.error_message = "Feishu registration returned invalid credentials."
            rec.updated_at = datetime.now(UTC)
            return

        user_info = result.get("user_info") or {}
        if isinstance(user_info, dict) and user_info.get("tenant_brand") == "lark":
            rec.domain = "lark"
        elif rec.domain_switched:
            rec.domain = "lark"

        rec.state = "connecting"
        rec.updated_at = datetime.now(UTC)

        try:
            account = await rec.connect_bot(client_id, client_secret, rec.domain)
        except ValueError as exc:
            rec.state = "error"
            rec.error_code = "connect_conflict"
            rec.error_message = str(exc)
            rec.updated_at = datetime.now(UTC)
            return
        except Exception:
            logger.opt(exception=True).warning("[FeishuQR] failed to connect registered bot")
            rec.state = "error"
            rec.error_code = "connect_failed"
            rec.error_message = "Bot was created, but CubePlex could not connect to it."
            rec.updated_at = datetime.now(UTC)
            return

        rec.account = account
        rec.state = "succeeded"
        rec.updated_at = datetime.now(UTC)
