"""Account-bound WeCom callback routing."""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubeplex.im.identity import NullIdentityResolver
from cubeplex.im.inbound import ingest_command_response, ingest_inbound_event
from cubeplex.im.reset_command import format_reset_reply
from cubeplex.im.types import lookup_binding_mode
from cubeplex.im.wecom.commands import parse_command, render_response
from cubeplex.im.wecom.connector import WecomConnector
from cubeplex.models.im_connector import IMConnectorAccount
from cubeplex.repositories.im_connector import (
    claim_command_response,
    mark_command_response_delivered,
    release_command_response_claim,
)

_COMMAND_LEASE_SECONDS = 30


def _delivery_error_code(response: dict[str, Any]) -> int:
    source = response
    if "errcode" not in source:
        body = response.get("body")
        source = body if isinstance(body, dict) else {}
    try:
        return int(source.get("errcode") or 0)
    except (TypeError, ValueError):
        return -1


async def _deliver_command_response(
    *,
    receipt_id: str,
    req_id: str,
    chat_id: str,
    session_maker: async_sessionmaker[AsyncSession],
    gateway: Any,
) -> None:
    async with session_maker() as session:
        claim = await claim_command_response(
            session,
            receipt_id=receipt_id,
            lease_seconds=_COMMAND_LEASE_SECONDS,
        )
        await session.commit()
    if claim is None:
        return

    text = render_response(claim.payload)
    body = {"msgtype": "markdown", "markdown": {"content": text}}
    try:
        result = await gateway.send_passive(
            req_id,
            body,
            final=True,
            skip_if_pending=False,
        )
        if result.get("proactive_required") or _delivery_error_code(result):
            proactive_result = await gateway.send_proactive(chat_id, body)
            if _delivery_error_code(proactive_result):
                raise RuntimeError("WeCom rejected the proactive command response")
    except Exception:
        async with session_maker() as session:
            await release_command_response_claim(
                session,
                receipt_id=receipt_id,
                lease_expires_at=claim.lease_expires_at,
            )
            await session.commit()
        logger.opt(exception=True).warning(
            "[WeCom] command response delivery failed for receipt {}",
            receipt_id,
        )
        return

    async with session_maker() as session:
        await mark_command_response_delivered(
            session,
            receipt_id=receipt_id,
            lease_expires_at=claim.lease_expires_at,
        )
        await session.commit()


async def handle_inbound_callback(
    raw: dict[str, Any],
    *,
    account: IMConnectorAccount,
    session_maker: async_sessionmaker[AsyncSession],
    gateway: Any,
) -> None:
    """Route one WeCom callback through commands or ordinary ingestion."""
    async with session_maker() as session:
        live_account = await session.get(IMConnectorAccount, account.id)
        if live_account is None or not live_account.enabled:
            return
        account = live_account

    body = raw.get("body")
    body = body if isinstance(body, dict) else {}
    sender = body.get("from")
    sender = sender if isinstance(sender, dict) else {}
    channel_id = str(body.get("chatid") or sender.get("userid") or "").strip()
    binding_mode = await lookup_binding_mode(session_maker, account.id, channel_id)
    connector = WecomConnector(bot_id=account.external_account_id, gateway=gateway)
    event = connector.parse_inbound(raw, binding_mode=binding_mode)
    if event is None:
        return
    event.account_external_id = account.external_account_id

    command = parse_command(event.text)
    if command is not None:
        if command.kind == "link":
            email = command.email
            assert email is not None

            async def build_link_response(
                _session: AsyncSession,
                _user_id: str | None,
            ) -> dict[str, Any]:
                return {
                    "kind": "link",
                    "im_user_id": event.sender_ref or event.sender_open_id or "",
                    "email": email,
                    "account_id": account.id,
                    "workspace_id": account.workspace_id,
                    "platform": "wecom",
                    "chat_id": event.channel_id,
                }

            result = await ingest_command_response(
                event,
                account=account,
                session_maker=session_maker,
                build_response=build_link_response,
            )
        else:
            from cubeplex.im.conversation_resolver import reset_im_conversation

            async def build_reset_response(
                session: AsyncSession,
                _user_id: str | None,
            ) -> dict[str, Any]:
                outcome = await reset_im_conversation(
                    session,
                    account_id=account.id,
                    channel_id=event.channel_id,
                    scope_key=event.scope_key,
                )
                return {"kind": "text", "text": format_reset_reply(outcome)}

            result = await ingest_command_response(
                event,
                account=account,
                session_maker=session_maker,
                build_response=build_reset_response,
                require_current_identity=True,
            )
        if result.receipt_id is not None and event.reply_to_id is not None:
            await _deliver_command_response(
                receipt_id=result.receipt_id,
                req_id=event.reply_to_id,
                chat_id=event.channel_id,
                session_maker=session_maker,
                gateway=gateway,
            )
        return

    ingest_result = await ingest_inbound_event(
        event,
        account=account,
        session_maker=session_maker,
        identity_resolver=NullIdentityResolver(),
        rejection_notifier=connector,
    )
    logger.info(
        "[WeCom] inbound {}: {}",
        event.platform_event_id,
        ingest_result.outcome,
    )
