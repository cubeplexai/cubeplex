"""The expand revision preserves legacy execution and notification evidence."""

import asyncio
from pathlib import Path

from alembic.config import Config
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from alembic import command as alembic_command
from cubeplex.config import config
from cubeplex.models import (
    BackgroundTask,
    Conversation,
    ConversationExecutionAdmission,
    SandboxCommand,
    SandboxCommandWake,
    User,
    UserSandbox,
)
from tests.e2e.conftest import (
    DEFAULT_ORG_ID,
    DEFAULT_TEST_EMAIL,
    DEFAULT_WS_ID,
    _ensure_default_user_and_membership,
)


def _migrate(target: str, *, downgrade: bool = False) -> None:
    backend = Path(__file__).parents[2]
    settings = Config(str(backend / "alembic.ini"))
    settings.set_main_option("script_location", str(backend / "alembic"))
    if downgrade:
        alembic_command.downgrade(settings, target)
    else:
        alembic_command.upgrade(settings, target)


async def test_expand_preserves_legacy_handles_notices_and_unknown_instance(
    db_session: AsyncSession,
) -> None:
    assert str(config.get("database.name")).startswith("cubeplex_test")
    for model in (BackgroundTask, ConversationExecutionAdmission):
        assert await db_session.scalar(select(func.count()).select_from(model)) == 0
    await db_session.commit()
    await _ensure_default_user_and_membership()
    user = (
        await db_session.execute(select(User).where(col(User.email) == DEFAULT_TEST_EMAIL))
    ).scalar_one()
    conv = Conversation(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        creator_user_id=user.id,
        title="legacy lifecycle migration evidence",
    )
    db_session.add(conv)
    await db_session.flush()
    sandbox = UserSandbox(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_id=user.id,
        scope_type="conversation",
        scope_id=conv.id,
        sandbox_id="replacement-not-proof-of-original-instance",
        image="test",
    )
    db_session.add(sandbox)
    await db_session.flush()
    legacy = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_sandbox_id=sandbox.id,
        conversation_id=conv.id,
        run_id="legacy-run",
        started_by_user_id=user.id,
        command="watch build",
        provider_ref="original-process-handle",
        log_cursor="original-log-cursor",
        kind="monitor",
        lifetime="conversation",
        status="running",
    )
    db_session.add(legacy)
    await db_session.flush()
    wake = SandboxCommandWake(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        command_id=legacy.id,
        conversation_id=conv.id,
        reason="line",
        dedupe_key=f"{legacy.id}:line:1",
        state="delivered",
        delivery_run_id="notification-run",
        delivery_steer_id="original-input-id",
        started_by_user_id=user.id,
    )
    db_session.add(wake)
    await db_session.commit()
    conv_id, sandbox_id, command_id, wake_id = conv.id, sandbox.id, legacy.id, wake.id
    db_session.expunge_all()
    try:
        await asyncio.to_thread(_migrate, "b2141c3f7682", downgrade=True)
        before = (
            await db_session.execute(
                text(
                    "SELECT provider_ref, log_cursor, lifetime, status "
                    "FROM sandbox_commands WHERE id = :id"
                ),
                {"id": command_id},
            )
        ).one()
        assert before == (
            "original-process-handle",
            "original-log-cursor",
            "conversation",
            "running",
        )
        await db_session.commit()
        await asyncio.to_thread(_migrate, "0430ad3006d8")
        restored = await db_session.get(SandboxCommand, command_id)
        restored_wake = await db_session.get(SandboxCommandWake, wake_id)
        assert restored is not None and restored_wake is not None
        assert restored.provider_ref == "original-process-handle"
        assert restored.log_cursor == "original-log-cursor"
        assert restored.task_id is None and restored.sandbox_instance_id is None
        assert restored_wake.state == "delivered"
        assert restored_wake.delivery_run_id == "notification-run"
        assert restored_wake.delivery_steer_id == "original-input-id"
        assert await db_session.scalar(select(func.count()).select_from(BackgroundTask)) == 0
    finally:
        await db_session.rollback()
        await asyncio.to_thread(_migrate, "head")
        for model, record_id in (
            (SandboxCommandWake, wake_id),
            (SandboxCommand, command_id),
            (UserSandbox, sandbox_id),
            (Conversation, conv_id),
        ):
            await db_session.execute(delete(model).where(col(model.id) == record_id))
        await db_session.commit()
