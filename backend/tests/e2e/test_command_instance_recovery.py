"""Recovery never substitutes a replacement sandbox for the recorded instance."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import opensandbox
import pytest
from cryptography.fernet import Fernet
from opensandbox.exceptions import SandboxApiException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubeplex.config import config
from cubeplex.credentials.encryption import EncryptionBackend, FernetBackend
from cubeplex.models import Conversation, UserSandbox
from cubeplex.sandbox.base import SandboxError, SandboxInstanceGoneError
from cubeplex.sandbox.manager import SandboxManager
from tests.e2e import test_background_task_reservation as reservation_fixtures
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID
from tests.e2e.test_background_task_reservation import (
    NOW,
    ReservationContext,
    reserve,
)

reservation_context = reservation_fixtures.reservation_context


@pytest.fixture
def mock_encryption_backend() -> FernetBackend:
    return FernetBackend([Fernet.generate_key()])


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock, AsyncMock]:
    original_get = config.get

    def get(key: str, default: object = None, **kwargs: object) -> object:
        if key == "sandbox.domain":
            return "unused-provider.invalid"
        if key == "sandbox.image":
            return "unused-image"
        return original_get(key, default, **kwargs)

    monkeypatch.setattr(config, "get", get)
    control = MagicMock()
    control.get_sandbox_info = AsyncMock(
        return_value=SimpleNamespace(status=SimpleNamespace(state="Running"))
    )
    control.close = AsyncMock()
    raw = MagicMock()
    raw.close = AsyncMock()
    raw.commands.get_background_command_logs = AsyncMock(
        return_value=SimpleNamespace(content="", cursor=None)
    )
    connection = AsyncMock(return_value=raw)
    monkeypatch.setattr(opensandbox.SandboxManager, "create", AsyncMock(return_value=control))
    monkeypatch.setattr(opensandbox.Sandbox, "connect", connection)
    monkeypatch.setattr(opensandbox.Sandbox, "create", AsyncMock(side_effect=AssertionError))
    monkeypatch.setattr(opensandbox.Sandbox, "resume", AsyncMock(side_effect=AssertionError))
    return control, raw, connection


async def remote_command(session: AsyncSession, context: ReservationContext) -> str:
    sandbox = await session.get(UserSandbox, context.details.user_sandbox_id)
    assert sandbox is not None
    sandbox.provider = "opensandbox"
    item = await reserve(session, context, details=replace(context.details, provider="opensandbox"))
    await session.commit()
    return item.command.id


async def test_deleted_conversation_recovery_uses_old_instance_after_replacement(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    mock_encryption_backend: EncryptionBackend,
    remote: tuple[MagicMock, MagicMock, AsyncMock],
) -> None:
    command_id = await remote_command(db_session, reservation_context)
    sandbox = await db_session.get(UserSandbox, reservation_context.details.user_sandbox_id)
    conversation = await db_session.get(Conversation, reservation_context.conversation_id)
    assert sandbox is not None and conversation is not None
    sandbox.sandbox_id = "replacement-instance"
    conversation.deleted_at = NOW
    await db_session.commit()
    control, raw, connection = remote
    raw.id = reservation_context.details.sandbox_instance_id
    manager = SandboxManager(session_factory, mock_encryption_backend)
    async with manager.connect_command_instance(
        command_id=command_id, org_id=DEFAULT_ORG_ID, workspace_id=DEFAULT_WS_ID
    ) as attached:
        assert (
            attached is not None and attached.id == reservation_context.details.sandbox_instance_id
        )
        assert attached.supports_background_reconnect()
    assert control.get_sandbox_info.await_args.args == (raw.id,)
    assert connection.await_args.args == (raw.id,)
    raw.close.assert_awaited_once()
    await db_session.refresh(sandbox)
    assert sandbox.sandbox_id == "replacement-instance" and sandbox.status == "running"


@pytest.mark.parametrize("endpoint,status", [("info", 404), ("info", 503), ("connect", 404)])
async def test_only_instance_info_404_proves_environment_gone(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    mock_encryption_backend: EncryptionBackend,
    remote: tuple[MagicMock, MagicMock, AsyncMock],
    endpoint: str,
    status: int,
) -> None:
    command_id = await remote_command(db_session, reservation_context)
    control, _, connection = remote
    operation = control.get_sandbox_info if endpoint == "info" else connection
    operation.side_effect = SandboxApiException("not found", status_code=status)
    manager = SandboxManager(session_factory, mock_encryption_backend)
    with pytest.raises(SandboxError) as failure:
        async with manager.connect_command_instance(
            command_id=command_id, org_id=DEFAULT_ORG_ID, workspace_id=DEFAULT_WS_ID
        ):
            pytest.fail("unavailable instance cannot be attached")
    assert isinstance(failure.value, SandboxInstanceGoneError) is (
        endpoint == "info" and status == 404
    )
    control.close.assert_awaited_once()


@pytest.mark.parametrize("state", ["Failed", "Terminated", "Succeed"])
async def test_terminal_provider_state_proves_environment_gone(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    mock_encryption_backend: EncryptionBackend,
    remote: tuple[MagicMock, MagicMock, AsyncMock],
    state: str,
) -> None:
    command_id = await remote_command(db_session, reservation_context)
    control, _, connection = remote
    control.get_sandbox_info.return_value = SimpleNamespace(status=SimpleNamespace(state=state))
    manager = SandboxManager(session_factory, mock_encryption_backend)

    with pytest.raises(SandboxInstanceGoneError):
        async with manager.connect_command_instance(
            command_id=command_id,
            org_id=DEFAULT_ORG_ID,
            workspace_id=DEFAULT_WS_ID,
        ):
            pytest.fail("terminal instance cannot be attached")

    connection.assert_not_awaited()
    control.close.assert_awaited_once()


async def test_cross_scope_recovery_never_contacts_provider(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    mock_encryption_backend: EncryptionBackend,
    remote: tuple[MagicMock, MagicMock, AsyncMock],
) -> None:
    command_id = await remote_command(db_session, reservation_context)
    manager = SandboxManager(session_factory, mock_encryption_backend)
    for scope in [("other-org", DEFAULT_WS_ID), (DEFAULT_ORG_ID, "other-ws")]:
        with pytest.raises(LookupError, match="not found"):
            async with manager.connect_command_instance(
                command_id=command_id, org_id=scope[0], workspace_id=scope[1]
            ):
                pytest.fail("cross-scope attachment")
    control, _, connection = remote
    control.get_sandbox_info.assert_not_awaited()
    connection.assert_not_awaited()


async def test_local_driver_does_not_claim_cross_worker_recovery(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    mock_encryption_backend: EncryptionBackend,
    remote: tuple[MagicMock, MagicMock, AsyncMock],
) -> None:
    item = await reserve(db_session, reservation_context)
    await db_session.commit()
    manager = SandboxManager(session_factory, mock_encryption_backend)
    with pytest.raises(SandboxError, match="reconnect"):
        async with manager.connect_command_instance(
            command_id=item.command.id, org_id=DEFAULT_ORG_ID, workspace_id=DEFAULT_WS_ID
        ):
            pytest.fail("local process cannot be recovered on a new worker")
