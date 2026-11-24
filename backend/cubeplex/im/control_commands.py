"""Shared ``/help``, ``/stop``, ``/steer``, ``/models``, and ``/model`` command parsing and execution for IM."""

from __future__ import annotations

import hashlib
import re as _re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from loguru import logger
from sqlalchemy import and_, desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from cubeplex.agents.checkpointer import shared_checkpointer
from cubeplex.im.bot_settings import load_bot_settings, wants_topic
from cubeplex.im.conversation_resolver import (
    _ensure_conversation_participant,
    _ensure_topic_participant,
    resolve_im_conversation,
)
from cubeplex.im.identity import (
    IdentityResolver,
    RejectionNotifier,
    resolve_current_linked_member,
    resolve_or_reject,
)
from cubeplex.im.types import InboundEvent
from cubeplex.llm.snapshot import ModelPreset, _load_presets
from cubeplex.models.conversation import Conversation
from cubeplex.models.conversation_execution import ConversationExecutionAdmission
from cubeplex.models.im_connector import IMConnectorAccount, IMThreadLink, IMWebhookReceipt
from cubeplex.models.topic import Topic
from cubeplex.services.conversation_execution import ConversationExecutionService
from cubeplex.services.conversation_steering import steer_conversation_run
from cubeplex.services.execution_signals import signal_stopped_runs
from cubeplex.streams.run_events import get_active_run

if TYPE_CHECKING:
    from cubeplex.streams.run_manager import RunManager

_HELP_RE = _re.compile(r"^\s*/help\s*$", _re.IGNORECASE)
_STOP_RE = _re.compile(r"^\s*/stop\s*$", _re.IGNORECASE)
_STEER_RE = _re.compile(r"^\s*/steer(?:$|\s+(?P<body>.*))$", _re.IGNORECASE | _re.DOTALL)
_MODELS_RE = _re.compile(r"^\s*/models\s*$", _re.IGNORECASE)
_MODEL_RE = _re.compile(r"^\s*/model(?:$|\s+(?P<body>.*))$", _re.IGNORECASE)

HELP_REPLY = (
    "📖 可用命令：\n"
    "• /help — 查看可用命令说明\n"
    "• /new（或 /reset）— 开始新对话\n"
    "• /stop — 停止当前正在进行的回复\n"
    "• /steer <内容> — 向当前正在进行的回复中途补充指令\n"
    "• /models — 查看可用模型预设列表\n"
    "• /model [名称] — 查看或切换当前会话使用的模型\n"
    "• /link <邮箱> — 绑定当前账号到 CubePlex"
)
STOP_NO_ACTIVE_REPLY = "ℹ️ 当前没有正在进行的回复。"
STOP_SUCCESS_REPLY = "⏹️ 已停止当前回复。"
STEER_USAGE_REPLY = "ℹ️ 用法：/steer <补充内容>"
STEER_SUCCESS_REPLY = "↪️ 已补充到当前回复。"
STEER_ATTACHMENTS_NOTE = "（注：中途补充暂不支持附带文件，仅已注入文本内容。）"
STEER_ACTOR_CONFLICT_REPLY = (
    "⚠️ 只有发起当前回复的成员可以中途补充；你可以直接发送普通消息排队，或发送 /stop 终止当前回复。"
)
STEER_QUEUE_FULL_REPLY = "⚠️ 当前补充消息队列已满，请稍后再试或发送 /stop 终止当前回复。"
STEER_TOO_LARGE_REPLY = "⚠️ 补充内容过长，请精简后再试。"
STEER_IDLE_FALLBACK_SLASH_REPLY = "ℹ️ 当前没有正在进行的回复，已作为新消息发送。"
MODELS_EMPTY_REPLY = "⚠️ 当前组织尚未配置可用模型预设。"


@dataclass(frozen=True, slots=True)
class HelpCommand:
    """Parsed ``/help`` command."""


@dataclass(frozen=True, slots=True)
class StopCommand:
    """Parsed ``/stop`` command."""


@dataclass(frozen=True, slots=True)
class SteerCommand:
    """Parsed ``/steer <content>`` command."""

    content: str


@dataclass(frozen=True, slots=True)
class ListModelsCommand:
    """Parsed ``/models`` command."""


@dataclass(frozen=True, slots=True)
class SwitchModelCommand:
    """Parsed ``/model [model]`` command."""

    model: str


ControlCommand = HelpCommand | StopCommand | SteerCommand | ListModelsCommand | SwitchModelCommand


def parse_control_command(text: str) -> ControlCommand | None:
    """Parse ``/help``, ``/stop``, ``/steer``, ``/models``, or ``/model`` from IM message text."""
    raw = text or ""
    if _HELP_RE.match(raw):
        return HelpCommand()
    if _STOP_RE.match(raw):
        return StopCommand()
    if _MODELS_RE.match(raw):
        return ListModelsCommand()
    model_match = _MODEL_RE.match(raw)
    if model_match is not None:
        return SwitchModelCommand(model=(model_match.group("body") or "").strip())
    match = _STEER_RE.match(raw)
    if match is not None:
        body = (match.group("body") or "").strip()
        return SteerCommand(content=body)
    return None


def make_im_client_steer_id(account_id: str, platform_event_id: str) -> str:
    """Return a deterministic <=64-char client_steer_id for an IM event."""
    digest = hashlib.sha256(f"{account_id}:{platform_event_id}".encode()).hexdigest()[:48]
    return f"im:{digest}"


def _is_receipt_unique_violation(exc: IntegrityError) -> bool:
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    msg = str(name) if name else (str(orig) or str(exc))
    return "uq_im_receipt_account_event" in msg


async def find_live_im_conversation(
    session: AsyncSession,
    account: IMConnectorAccount,
    *,
    channel_id: str,
    scope_key: str,
    scope_kind: str,
    effective_user_id: str,
) -> Conversation | None:
    """Look up the active Conversation for an IM scope without creating one.

    In shared channel mode, idempotently ensures ``effective_user_id`` is a
    participant on the Conversation (and Topic, if any) so any authorized
    channel member can issue ``/stop``.
    """
    link = (
        await session.execute(
            select(IMThreadLink).where(
                col(IMThreadLink.account_id) == account.id,
                col(IMThreadLink.channel_id) == channel_id,
                col(IMThreadLink.scope_key) == scope_key,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        return None

    conversation = await session.get(Conversation, link.conversation_id)
    if conversation is None or conversation.deleted_at is not None:
        return None

    settings = load_bot_settings(account.config)
    if wants_topic(settings) and conversation.topic_id is not None:
        topic = await session.get(Topic, conversation.topic_id)
        if topic is None or topic.is_archived:
            return None

    is_shared = settings.routing_mode == "shared" and scope_kind != "dm"
    if is_shared:
        await _ensure_conversation_participant(session, account, conversation.id, effective_user_id)
        if conversation.topic_id is not None:
            await _ensure_topic_participant(session, conversation.topic_id, effective_user_id)

    return conversation


async def _find_stop_target_run_id(
    session: AsyncSession,
    *,
    account: IMConnectorAccount,
    conversation_id: str,
    run_manager: RunManager | None,
) -> str | None:
    if run_manager is not None:
        active = await get_active_run(
            run_manager.redis,
            prefix=run_manager.key_prefix,
            conversation_id=conversation_id,
        )
        if active is not None and active.status in ("running", "paused_hitl"):
            return str(active.run_id)

    async with shared_checkpointer() as cp:
        pending_run_id = await cp.load_pending_run_id(conversation_id)
    if pending_run_id is not None:
        return str(pending_run_id)

    admission = (
        await session.execute(
            select(ConversationExecutionAdmission)
            .where(
                col(ConversationExecutionAdmission.org_id) == account.org_id,
                col(ConversationExecutionAdmission.workspace_id) == account.workspace_id,
                col(ConversationExecutionAdmission.conversation_id) == conversation_id,
                col(ConversationExecutionAdmission.execution_kind) == "run",
                col(ConversationExecutionAdmission.run_started_at).is_not(None),
                col(ConversationExecutionAdmission.run_finished_at).is_(None),
                col(ConversationExecutionAdmission.run_id).is_not(None),
            )
            .order_by(desc(col(ConversationExecutionAdmission.created_at)))
            .limit(1)
        )
    ).scalar_one_or_none()
    if admission is not None and admission.run_id is not None:
        return str(admission.run_id)
    return None


@dataclass(frozen=True, slots=True)
class StopExecutionResult:
    """Result of executing ``/stop`` inside an open database session."""

    reply_text: str
    stopped_conversation_id: str | None = None
    stopped_run_id: str | None = None


async def execute_stop_command(
    *,
    session: AsyncSession,
    account: IMConnectorAccount,
    effective_user_id: str,
    channel_id: str,
    scope_key: str,
    scope_kind: str,
    run_manager: RunManager | None,
    commit_fn: Callable[[], Awaitable[None]] | None = None,
    defer_signal: bool = False,
) -> StopExecutionResult:
    """Stop the active run for an IM scope and return the user-facing reply.

    Does not create a conversation or topic if none exists. When
    ``defer_signal=True`` (used by ``ingest_command_response``), the caller is
    responsible for calling ``signal_stopped_runs`` after committing.
    """
    conversation = await find_live_im_conversation(
        session,
        account,
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        effective_user_id=effective_user_id,
    )
    if conversation is None:
        return StopExecutionResult(reply_text=STOP_NO_ACTIVE_REPLY)

    target_run_id = await _find_stop_target_run_id(
        session,
        account=account,
        conversation_id=conversation.id,
        run_manager=run_manager,
    )
    if target_run_id is None:
        return StopExecutionResult(reply_text=STOP_NO_ACTIVE_REPLY)

    exec_service = ConversationExecutionService(
        session, org_id=account.org_id, workspace_id=account.workspace_id
    )
    async with session.begin_nested() as savepoint:
        try:
            stopped = await exec_service.stop_run(
                conversation_id=conversation.id,
                run_id=target_run_id,
                actor_user_id=effective_user_id,
                now=datetime.now(UTC),
            )
        except LookupError:
            await savepoint.rollback()
            return StopExecutionResult(reply_text=STOP_NO_ACTIVE_REPLY)

        if stopped.admission.run_finished_at is not None:
            await savepoint.rollback()
            return StopExecutionResult(reply_text=STOP_NO_ACTIVE_REPLY)

    if commit_fn is not None:
        await commit_fn()

    if not defer_signal and run_manager is not None:
        await signal_stopped_runs(
            run_manager,
            conversation_id=conversation.id,
            run_ids=(stopped.run_id,),
            user_id=effective_user_id,
            org_id=account.org_id,
            workspace_id=account.workspace_id,
        )
    return StopExecutionResult(
        reply_text=STOP_SUCCESS_REPLY,
        stopped_conversation_id=conversation.id,
        stopped_run_id=stopped.run_id,
    )


async def execute_steer_command(
    *,
    session: AsyncSession,
    account: IMConnectorAccount,
    effective_user_id: str,
    channel_id: str,
    scope_key: str,
    scope_kind: str,
    platform_event_id: str,
    content: str,
    has_attachments: bool,
    run_manager: RunManager | None,
) -> tuple[Literal["handled", "fallback"], str | None]:
    """Attempt to steer the active run for an IM scope.

    Returns ``("fallback", None)`` when no active run exists so the caller can
    delegate to normal message ingestion.
    """
    if not content:
        return "handled", STEER_USAGE_REPLY

    conversation = await find_live_im_conversation(
        session,
        account,
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        effective_user_id=effective_user_id,
    )
    if conversation is None or run_manager is None:
        return "fallback", None

    result = await steer_conversation_run(
        session=session,
        org_id=account.org_id,
        workspace_id=account.workspace_id,
        conversation=conversation,
        actor_user_id=effective_user_id,
        sender_display_name=None,
        client_steer_id=make_im_client_steer_id(account.id, platform_event_id),
        content=content,
        redis=run_manager.redis,
        redis_key_prefix=run_manager.key_prefix,
        run_manager=run_manager,
    )
    if result.outcome == "no_active_run":
        return "fallback", None
    if result.outcome == "queued":
        reply = STEER_SUCCESS_REPLY
        if has_attachments:
            reply = f"{reply}{STEER_ATTACHMENTS_NOTE}"
        return "handled", reply
    if result.outcome == "actor_conflict":
        return "handled", STEER_ACTOR_CONFLICT_REPLY
    if result.outcome == "queue_full":
        return "handled", STEER_QUEUE_FULL_REPLY
    if result.outcome == "too_large":
        return "handled", STEER_TOO_LARGE_REPLY
    return "fallback", None


async def _find_existing_receipt(
    session: AsyncSession, account_id: str, platform_event_id: str
) -> IMWebhookReceipt | None:
    return (
        await session.execute(
            select(IMWebhookReceipt).where(
                col(IMWebhookReceipt.account_id) == account_id,
                col(IMWebhookReceipt.platform_event_id) == platform_event_id,
            )
        )
    ).scalar_one_or_none()


async def _commit_terminal_receipt(
    session: AsyncSession,
    *,
    account: IMConnectorAccount,
    platform_event_id: str,
    receipt_status: str,
) -> bool:
    """Insert and commit a terminal IMWebhookReceipt; returns False on duplicate."""
    receipt = IMWebhookReceipt(
        org_id=account.org_id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        platform_event_id=platform_event_id,
        status=receipt_status,
    )
    session.add(receipt)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if _is_receipt_unique_violation(exc):
            return False
        raise
    return True


def _is_preset_enabled(preset: ModelPreset, enabled_refs: set[str] | None) -> bool:
    return enabled_refs is None or all(ref in enabled_refs for ref in preset.chain)


def _resolve_active_model_key(
    presets: tuple[ModelPreset, ...],
    configured_key: str | None,
    *,
    enabled_refs: set[str] | None = None,
) -> str | None:
    if configured_key and any(
        p.key == configured_key and _is_preset_enabled(p, enabled_refs) for p in presets
    ):
        return configured_key
    default_preset = next(
        (p for p in presets if p.is_default and _is_preset_enabled(p, enabled_refs)),
        None,
    )
    return default_preset.key if default_preset is not None else None


def _match_model_preset(
    presets: tuple[ModelPreset, ...],
    arg: str,
) -> ModelPreset | None:
    exact = next((p for p in presets if p.key == arg), None)
    if exact is not None:
        return exact
    lowered = arg.lower()
    ci_matches = [p for p in presets if p.key.lower() == lowered]
    if len(ci_matches) == 1:
        return ci_matches[0]
    if len(ci_matches) > 1:
        return None
    primary_matches = [p for p in presets if p.primary.lower() == lowered]
    if len(primary_matches) == 1:
        return primary_matches[0]
    return None


async def _load_enabled_model_refs(session: AsyncSession, org_id: str) -> set[str]:
    from cubeplex.models.org_provider_override import OrgProviderOverride as DBO
    from cubeplex.models.provider import Model as DBM
    from cubeplex.models.provider import Provider as DBP

    stmt = (
        select(col(DBP.slug), col(DBM.model_id))
        .join(DBM, and_(col(DBM.provider_id) == col(DBP.id), col(DBM.enabled)))
        .outerjoin(DBO, and_(col(DBO.provider_id) == col(DBP.id), col(DBO.org_id) == org_id))
        .where(col(DBP.org_id).is_(None) | (col(DBP.org_id) == org_id))
        .where(func.coalesce(col(DBO.enabled), col(DBP.enabled), True))
    )
    rows = (await session.execute(stmt)).all()
    return {f"{slug}/{model_id}" for slug, model_id in rows}


async def execute_list_models_command(
    *,
    session: AsyncSession,
    account: IMConnectorAccount,
    effective_user_id: str,
    channel_id: str,
    scope_key: str,
    scope_kind: str,
) -> str:
    """Format the org's available model presets and mark the active conversation model."""
    presets, _ = await _load_presets(session, account.org_id)
    if not presets:
        return MODELS_EMPTY_REPLY

    conv = await find_live_im_conversation(
        session,
        account,
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        effective_user_id=effective_user_id,
    )
    enabled_refs = await _load_enabled_model_refs(session, account.org_id)
    active_key = _resolve_active_model_key(
        presets,
        conv.model_key if conv is not None else None,
        enabled_refs=enabled_refs,
    )
    lines = ["🤖 可用模型预设："]
    for p in presets:
        badges: list[str] = []
        if p.is_default:
            badges.append("默认")
        if p.key == active_key:
            badges.append("当前使用")
        if not _is_preset_enabled(p, enabled_refs):
            badges.append("不可用")
        badge_str = f" [{' · '.join(badges)}]" if badges else ""
        desc_str = f" — {p.description}" if p.description else ""
        lines.append(f"• {p.key} ({p.primary}){badge_str}{desc_str}")
    lines.append("")
    lines.append("发送 `/model <名称>` 切换当前会话模型。")
    return "\n".join(lines)


async def execute_switch_model_command(
    *,
    session: AsyncSession,
    account: IMConnectorAccount,
    effective_user_id: str,
    channel_id: str,
    scope_key: str,
    scope_kind: str,
    model_arg: str,
    channel_name: str | None = None,
) -> str:
    """Switch the current IM conversation's ``model_key`` (creating it if needed)."""
    presets, _ = await _load_presets(session, account.org_id)
    enabled_refs = await _load_enabled_model_refs(session, account.org_id)
    arg = model_arg.strip()
    if not arg:
        conv = await find_live_im_conversation(
            session,
            account,
            channel_id=channel_id,
            scope_key=scope_key,
            scope_kind=scope_kind,
            effective_user_id=effective_user_id,
        )
        active_key = _resolve_active_model_key(
            presets,
            conv.model_key if conv is not None else None,
            enabled_refs=enabled_refs,
        )
        current_label = active_key or "未配置"
        return (
            f"ℹ️ 当前使用模型：{current_label}\n用法：/model <模型名称>（发送 /models 查看可用模型）"
        )

    if not presets:
        return MODELS_EMPTY_REPLY

    target = _match_model_preset(presets, arg)
    if target is None:
        return f"⚠️ 未找到模型预设「{arg}」，请发送 /models 查看可用模型。"

    if not _is_preset_enabled(target, enabled_refs):
        return f"⚠️ 模型预设「{target.key}」包含已禁用或不可用的模型，暂时无法切换。"

    conv = await find_live_im_conversation(
        session,
        account,
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        effective_user_id=effective_user_id,
    )
    if conv is None:
        resolved = await resolve_im_conversation(
            session,
            account,
            channel_id=channel_id,
            scope_key=scope_key,
            scope_kind=scope_kind,
            effective_user_id=effective_user_id,
            title_hint="",
            origin="inbound",
            channel_name=channel_name,
        )
        conv = await session.get(Conversation, resolved.conversation_id)
        if conv is None:
            raise LookupError(f"Conversation {resolved.conversation_id} not found after resolve")
    else:
        locked = (
            await session.execute(
                select(Conversation)
                .where(col(Conversation.id) == conv.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if locked is not None:
            conv = locked

    conv.model_key = target.key
    conv.updated_at = datetime.now(UTC)
    await session.flush()
    return f"✅ 已将当前会话模型切换为「{target.key}」（{target.primary}），后续消息将使用该模型。"


async def maybe_handle_control_command(
    event: InboundEvent,
    *,
    account: IMConnectorAccount,
    session_maker: async_sessionmaker[AsyncSession],
    run_manager: RunManager | None,
    reply: Callable[[str], Awaitable[None]],
    identity_resolver: IdentityResolver | None = None,
    rejection_notifier: RejectionNotifier | None = None,
    require_linked_member: bool = False,
) -> bool:
    """Intercept ``/help``, ``/stop``, ``/steer <content>``, ``/models``, or ``/model`` before ``ingest_inbound_event``.

    Returns ``True`` if the event was completely handled (including duplicates
    and identity rejections) and should NOT be passed to ``ingest_inbound_event``.
    Returns ``False`` if the message is not a control command, or if
    ``/steer <content>`` found no active run and rewrote ``event.text`` to the
    stripped content for normal idle-fallback ingestion.
    """
    cmd = parse_control_command(event.text)
    if cmd is None:
        return False
    if not event.platform_event_id:
        return True

    async with session_maker() as session:
        if await _find_existing_receipt(session, account.id, event.platform_event_id) is not None:
            return True

        live_account = await session.get(IMConnectorAccount, account.id) or account

        if isinstance(cmd, HelpCommand):
            committed = await _commit_terminal_receipt(
                session,
                account=live_account,
                platform_event_id=event.platform_event_id,
                receipt_status="completed",
            )
            if committed:
                await _safe_reply(reply, HELP_REPLY)
            return True

        effective_user_id: str = live_account.acting_user_id

        if identity_resolver is not None and rejection_notifier is not None:
            resolved = await resolve_or_reject(
                session=session,
                account=live_account,
                event=event,
                resolver=identity_resolver,
                notifier=rejection_notifier,
            )
            if resolved is None:
                await _commit_terminal_receipt(
                    session,
                    account=live_account,
                    platform_event_id=event.platform_event_id,
                    receipt_status="rejected",
                )
                return True
            effective_user_id = resolved
        elif require_linked_member:
            linked_user_id, rejection_text = await resolve_current_linked_member(
                session=session,
                account=live_account,
                event=event,
            )
            if rejection_text is not None or linked_user_id is None:
                committed = await _commit_terminal_receipt(
                    session,
                    account=live_account,
                    platform_event_id=event.platform_event_id,
                    receipt_status="rejected",
                )
                if committed and rejection_text:
                    await _safe_reply(reply, rejection_text)
                return True
            effective_user_id = linked_user_id

        if isinstance(cmd, StopCommand):
            receipt = IMWebhookReceipt(
                org_id=live_account.org_id,
                workspace_id=live_account.workspace_id,
                account_id=live_account.id,
                platform_event_id=event.platform_event_id,
                status="completed",
            )
            session.add(receipt)
            try:
                await session.flush()
            except IntegrityError as exc:
                await session.rollback()
                if _is_receipt_unique_violation(exc):
                    return True
                raise

            stop_res = await execute_stop_command(
                session=session,
                account=live_account,
                effective_user_id=effective_user_id,
                channel_id=event.channel_id,
                scope_key=event.scope_key,
                scope_kind=event.scope_kind,
                run_manager=run_manager,
                commit_fn=session.commit,
            )
            reply_text = stop_res.reply_text
            # If execute_stop_command didn't hit the commit_fn path (e.g. no
            # active run or rolled back finished run), persist the receipt now.
            if receipt not in session:
                committed = await _commit_terminal_receipt(
                    session,
                    account=live_account,
                    platform_event_id=event.platform_event_id,
                    receipt_status="completed",
                )
                if not committed:
                    return True
            else:
                await session.commit()
            await _safe_reply(reply, reply_text)
            return True

        if isinstance(cmd, ListModelsCommand):
            list_reply = await execute_list_models_command(
                session=session,
                account=live_account,
                effective_user_id=effective_user_id,
                channel_id=event.channel_id,
                scope_key=event.scope_key,
                scope_kind=event.scope_kind,
            )
            committed = await _commit_terminal_receipt(
                session,
                account=live_account,
                platform_event_id=event.platform_event_id,
                receipt_status="completed",
            )
            if committed:
                await _safe_reply(reply, list_reply)
            return True

        if isinstance(cmd, SwitchModelCommand):
            receipt = IMWebhookReceipt(
                org_id=live_account.org_id,
                workspace_id=live_account.workspace_id,
                account_id=live_account.id,
                platform_event_id=event.platform_event_id,
                status="completed",
            )
            session.add(receipt)
            try:
                await session.flush()
            except IntegrityError as exc:
                await session.rollback()
                if _is_receipt_unique_violation(exc):
                    return True
                raise

            switch_reply = await execute_switch_model_command(
                session=session,
                account=live_account,
                effective_user_id=effective_user_id,
                channel_id=event.channel_id,
                scope_key=event.scope_key,
                scope_kind=event.scope_kind,
                model_arg=cmd.model,
                channel_name=event.channel_name,
            )
            await session.commit()
            await _safe_reply(reply, switch_reply)
            return True

        # SteerCommand
        steer_outcome, steer_reply = await execute_steer_command(
            session=session,
            account=live_account,
            effective_user_id=effective_user_id,
            channel_id=event.channel_id,
            scope_key=event.scope_key,
            scope_kind=event.scope_kind,
            platform_event_id=event.platform_event_id,
            content=cmd.content,
            has_attachments=bool(event.attachments),
            run_manager=run_manager,
        )
        if steer_outcome == "fallback":
            await session.rollback()
            event.text = cmd.content
            return False

        # Steer was handled (queued, usage hint, conflict, queue_full, too_large).
        # Note: steer_conversation_run already committed on "queued", so add a
        # fresh terminal receipt row if not yet present.
        if await _find_existing_receipt(session, live_account.id, event.platform_event_id) is None:
            committed = await _commit_terminal_receipt(
                session,
                account=live_account,
                platform_event_id=event.platform_event_id,
                receipt_status="completed",
            )
            if not committed:
                return True
        if steer_reply is not None:
            await _safe_reply(reply, steer_reply)
        return True


async def _safe_reply(reply: Callable[[str], Awaitable[None]], text: str) -> None:
    try:
        await reply(text)
    except Exception:
        logger.opt(exception=True).warning("[IM control] failed to send control reply")


class EphemeralRejectionNotifier:
    """``RejectionNotifier`` adapter for slash commands using an ephemeral callback."""

    def __init__(self, reply: Callable[[str], Awaitable[None]]) -> None:
        self._reply = reply

    async def send_to_chat(self, chat_id: str, reply_to_id: str | None, text: str) -> str | None:
        del chat_id, reply_to_id
        await _safe_reply(self._reply, text)
        return None

    async def send_link_required(self, chat_id: str, reply_to_id: str | None, open_id: str) -> None:
        del chat_id, reply_to_id, open_id
        from cubeplex.im.identity import _REJECTION_LINK_REQUIRED

        await _safe_reply(self._reply, _REJECTION_LINK_REQUIRED)

    async def send_not_member(self, chat_id: str, reply_to_id: str | None, open_id: str) -> None:
        del chat_id, reply_to_id, open_id
        from cubeplex.im.identity import _REJECTION_NOT_MEMBER

        await _safe_reply(self._reply, _REJECTION_NOT_MEMBER)
