"""Account-bound WeCom callback routing."""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubeplex.im.identity import NullIdentityResolver
from cubeplex.im.inbound import ingest_command_response, ingest_inbound_event
from cubeplex.im.reset_command import format_reset_reply
from cubeplex.im.types import lookup_binding_mode
from cubeplex.im.wecom.commands import parse_command, render_response
from cubeplex.im.wecom.connector import WecomConnector
from cubeplex.models.im_connector import IMConnectorAccount, IMWebhookReceipt
from cubeplex.repositories.im_connector import (
    claim_command_response,
    mark_command_response_delivered,
    release_command_response_claim,
)

# A passive final can spend one ACK window draining an intermediate response,
# then proactive fallback spends another. Keep the ownership fence well beyond
# both windows plus database/network overhead.
_COMMAND_LEASE_SECONDS = 60


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
    run_manager: Any = None,
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
    connector = WecomConnector(
        bot_id=account.external_account_id,
        bot_display_name=str((account.config or {}).get("bot_app_name") or ""),
        gateway=gateway,
    )
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

    from cubeplex.im.control_commands import (
        ListModelsCommand,
        SteerCommand,
        StopCommand,
        SwitchModelCommand,
        _find_existing_receipt,
        execute_list_models_command,
        execute_steer_command,
        execute_stop_command,
        execute_switch_model_command,
        parse_control_command,
    )
    from cubeplex.im.identity import resolve_current_linked_member

    control_cmd = parse_control_command(event.text)
    if isinstance(control_cmd, StopCommand):
        stopped_target: tuple[str, str, str] | None = None

        async def build_stop_response(
            session: AsyncSession,
            user_id: str | None,
        ) -> dict[str, Any]:
            nonlocal stopped_target
            assert user_id is not None
            stop_res = await execute_stop_command(
                session=session,
                account=account,
                effective_user_id=user_id,
                channel_id=event.channel_id,
                scope_key=event.scope_key,
                scope_kind=event.scope_kind,
                run_manager=run_manager,
                commit_fn=None,
                defer_signal=True,
            )
            if stop_res.stopped_conversation_id and stop_res.stopped_run_id:
                stopped_target = (
                    stop_res.stopped_conversation_id,
                    stop_res.stopped_run_id,
                    user_id,
                )
            return {"kind": "text", "text": stop_res.reply_text}

        result = await ingest_command_response(
            event,
            account=account,
            session_maker=session_maker,
            build_response=build_stop_response,
            require_current_identity=True,
        )
        if result.outcome == "created" and stopped_target is not None and run_manager is not None:
            from cubeplex.services.execution_signals import signal_stopped_runs

            conv_id, run_id, actor_id = stopped_target
            await signal_stopped_runs(
                run_manager,
                conversation_id=conv_id,
                run_ids=(run_id,),
                user_id=actor_id,
                org_id=account.org_id,
                workspace_id=account.workspace_id,
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

    if isinstance(control_cmd, (ListModelsCommand, SwitchModelCommand)):

        async def build_model_response(
            session: AsyncSession,
            user_id: str | None,
        ) -> dict[str, Any]:
            assert user_id is not None
            if isinstance(control_cmd, ListModelsCommand):
                text = await execute_list_models_command(
                    session=session,
                    account=account,
                    effective_user_id=user_id,
                    channel_id=event.channel_id,
                    scope_key=event.scope_key,
                    scope_kind=event.scope_kind,
                )
            else:
                text = await execute_switch_model_command(
                    session=session,
                    account=account,
                    effective_user_id=user_id,
                    channel_id=event.channel_id,
                    scope_key=event.scope_key,
                    scope_kind=event.scope_kind,
                    model_arg=control_cmd.model,
                    channel_name=event.channel_name,
                )
            return {"kind": "text", "text": text}

        result = await ingest_command_response(
            event,
            account=account,
            session_maker=session_maker,
            build_response=build_model_response,
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

    if isinstance(control_cmd, SteerCommand) and event.platform_event_id:
        delivered_receipt_id: str | None = None
        async with session_maker() as session:
            existing = await _find_existing_receipt(session, account.id, event.platform_event_id)
            if existing is not None:
                if existing.response_payload is not None and event.reply_to_id is not None:
                    await _deliver_command_response(
                        receipt_id=existing.id,
                        req_id=event.reply_to_id,
                        chat_id=event.channel_id,
                        session_maker=session_maker,
                        gateway=gateway,
                    )
                return

            user_id, rejection_text = await resolve_current_linked_member(
                session=session,
                account=account,
                event=event,
            )
            if rejection_text is not None or user_id is None:
                receipt = IMWebhookReceipt(
                    org_id=account.org_id,
                    workspace_id=account.workspace_id,
                    account_id=account.id,
                    platform_event_id=event.platform_event_id,
                    status="pending",
                    response_payload={"kind": "text", "text": rejection_text or ""},
                )
                session.add(receipt)
                try:
                    await session.commit()
                    delivered_receipt_id = receipt.id
                except IntegrityError:
                    await session.rollback()
                    return
            else:
                outcome, reply_text = await execute_steer_command(
                    session=session,
                    account=account,
                    effective_user_id=user_id,
                    channel_id=event.channel_id,
                    scope_key=event.scope_key,
                    scope_kind=event.scope_kind,
                    platform_event_id=event.platform_event_id,
                    content=control_cmd.content,
                    has_attachments=bool(event.attachments),
                    run_manager=run_manager,
                )
                if outcome == "fallback":
                    await session.rollback()
                    event.text = control_cmd.content
                else:
                    receipt = IMWebhookReceipt(
                        org_id=account.org_id,
                        workspace_id=account.workspace_id,
                        account_id=account.id,
                        platform_event_id=event.platform_event_id,
                        status="pending",
                        response_payload={"kind": "text", "text": reply_text or ""},
                    )
                    session.add(receipt)
                    try:
                        await session.commit()
                        delivered_receipt_id = receipt.id
                    except IntegrityError:
                        await session.rollback()
                        return

        if delivered_receipt_id is not None:
            if event.reply_to_id is not None:
                await _deliver_command_response(
                    receipt_id=delivered_receipt_id,
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
