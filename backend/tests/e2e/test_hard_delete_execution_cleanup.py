"""Hard deletion waits for execution cleanup and succeeds on endpoint retry."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubeplex.models import (
    BackgroundTask,
    BackgroundTaskEvent,
    Conversation,
    ConversationExecutionAdmission,
    SandboxCommand,
    SteeringMessage,
    UserSandbox,
    Workspace,
)
from cubeplex.services.background_tasks import (
    BackgroundTaskService,
    CommandExecutionDetails,
    TaskSpec,
)

pytestmark = pytest.mark.e2e


async def test_workspace_delete_retains_cleanup_proof_until_retry(
    admin_client: tuple[httpx.AsyncClient, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client, _ = admin_client
    workspaces = await client.get("/api/v1/workspaces")
    org_id = workspaces.json()[0]["org_id"]
    created = await client.post(
        "/api/v1/workspaces",
        json={"name": "pending deletion", "org_id": org_id},
    )
    assert created.status_code == 201, created.text
    workspace_id = created.json()["id"]
    me = await client.get("/api/v1/auth/me")
    user_id = me.json()["id"]

    async with session_factory() as session:
        now = datetime.now(UTC)
        conversation = Conversation(
            org_id=org_id,
            workspace_id=workspace_id,
            creator_user_id=user_id,
            title="pending workspace run",
            is_group_chat=True,
        )
        session.add(conversation)
        await session.flush()
        admission = ConversationExecutionAdmission(
            org_id=org_id,
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            actor_user_id=user_id,
            execution_generation=0,
            source_kind="user_message",
            source_id=f"web:{uuid4()}",
            run_id=str(uuid4()),
        )
        sandbox = UserSandbox(
            org_id=org_id,
            workspace_id=workspace_id,
            user_id=user_id,
            scope_type="conversation",
            scope_id=conversation.id,
            sandbox_id=f"delete-{uuid4()}",
            status="running",
            provider="local",
            image="test",
        )
        session.add_all([admission, sandbox])
        await session.flush()
        reserved = await BackgroundTaskService(
            session, org_id=org_id, workspace_id=workspace_id
        ).reserve_task(
            admission_id=admission.id,
            task_spec=TaskSpec(
                originating_run_id=admission.run_id or "",
                tool_call_id=str(uuid4()),
            ),
            execution_details=CommandExecutionDetails(
                user_sandbox_id=sandbox.id,
                sandbox_instance_id=sandbox.sandbox_id or "",
                provider="local",
                command="long-running build",
                log_path="/workspace/build.log",
            ),
            owner_token=str(uuid4()),
            owner_until=now + timedelta(seconds=30),
            now=now,
        )
        notice = BackgroundTaskEvent(
            org_id=org_id,
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            task_id=reserved.task.id,
            execution_generation=0,
            reason="completion",
            dedupe_key="completion",
        )
        session.add(notice)
        await session.flush()
        background_input = SteeringMessage(
            org_id=org_id,
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            run_id=admission.run_id or "",
            source_kind="background_task",
            execution_generation=0,
            notice_id=notice.id,
            client_steer_id=notice.id,
            content="internal result",
            sender_user_id=user_id,
        )
        session.add(background_input)
        # A conversation can reopen while an older generation is still cleaning up.
        # Hard deletion must retain that older task and command until both settle.
        conversation.execution_generation = 1
        await session.commit()
        conversation_id, admission_id = conversation.id, admission.id
        task_id, command_id = reserved.task.id, reserved.command.id
        notice_id, background_input_id = notice.id, background_input.id

    first = await client.delete(f"/api/v1/workspaces/{workspace_id}")
    assert first.status_code == 200, first.text
    assert first.json() == {"deleted": False, "cleanup_pending": True}
    async with session_factory() as session:
        workspace = await session.get(Workspace, workspace_id)
        conversation = await session.get(Conversation, conversation_id)
        admission = await session.get(ConversationExecutionAdmission, admission_id)
        task = await session.get(BackgroundTask, task_id)
        command = await session.get(SandboxCommand, command_id)
        assert workspace is not None and workspace.deletion_pending_at is not None
        assert conversation is not None and conversation.deleted_at is not None
        assert admission is not None and admission.revoked_at is not None
        assert task is not None and task.stop_requested_at is not None
        assert command is not None
        now = datetime.now(UTC)
        admission.run_finished_at = now
        admission.run_terminal_at = now
        admission.run_terminal_status = "cancelled"
        task.state = "cancelled"
        task.finished_at = now
        command.status = "killed"
        command.log_state = "complete"
        command.finished_at = now
        await session.commit()

    retry = await client.delete(f"/api/v1/workspaces/{workspace_id}")
    assert retry.status_code == 200, retry.text
    assert retry.json() == {"deleted": True, "cleanup_pending": False}
    async with session_factory() as session:
        assert await session.get(Workspace, workspace_id) is None
        assert await session.get(ConversationExecutionAdmission, admission_id) is None
        assert await session.get(BackgroundTask, task_id) is None
        assert await session.get(SandboxCommand, command_id) is None
        assert await session.get(BackgroundTaskEvent, notice_id) is None
        assert await session.get(SteeringMessage, background_input_id) is None


async def test_workspace_delete_reclaims_paused_sandbox_volumes_and_sync_events(
    admin_client: tuple[httpx.AsyncClient, str],
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typing import Any

    from cubeplex.models.user_sandbox_sync_event import UserSandboxSyncEvent
    from cubeplex.sandbox.base import SandboxCapabilities
    from cubeplex.sandbox.manager import get_sandbox_manager
    from cubeplex.sandbox.provider import ProviderInstanceState, SandboxProvider
    from cubeplex.sandbox.registry import ProviderRegistry

    class _RecordingProvider(SandboxProvider):
        def __init__(self) -> None:
            self.terminated: list[tuple[str, bool]] = []
            self.fail_next = True

        @property
        def name(self) -> str:
            return "cubepod"

        @property
        def capabilities(self) -> SandboxCapabilities:
            return SandboxCapabilities(
                background=True, persistent_volume=True, pause=True, reset_rootfs=True
            )

        @property
        def default_workdir(self) -> str:
            return "/workspace"

        @property
        def default_image(self) -> str | None:
            return "cubeplex-ubuntu24-desktop"

        async def create(self, spec: Any, *, on_created: Any = None) -> Any:
            raise NotImplementedError

        async def connect(
            self,
            sandbox_id: str,
            *,
            workdir: str | None = None,
            user_sandbox_id: str | None = None,
            run_identity: Any = None,
        ) -> Any:
            raise NotImplementedError

        async def inspect_state(self, sandbox_id: str) -> ProviderInstanceState | None:
            del sandbox_id
            return ProviderInstanceState.PAUSED

        async def terminate(self, sandbox_id: str, *, delete_volumes: bool = False) -> None:
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("cubepod gateway temporarily unavailable")
            self.terminated.append((sandbox_id, delete_volumes))

    provider = _RecordingProvider()
    mgr = get_sandbox_manager()
    monkeypatch.setattr(
        mgr,
        "_registry",
        ProviderRegistry({"cubepod": provider}, default_name="cubepod"),
    )

    client, _ = admin_client
    workspaces = await client.get("/api/v1/workspaces")
    org_id = workspaces.json()[0]["org_id"]
    created = await client.post(
        "/api/v1/workspaces",
        json={"name": "sandbox volume cleanup", "org_id": org_id, "sandbox_mode": "workspace"},
    )
    assert created.status_code == 201, created.text
    workspace_id = created.json()["id"]
    sbx_instance_id = f"cpod-ws-{uuid4().hex[:8]}"

    async with session_factory() as session:
        now = datetime.now(UTC)
        sbx = UserSandbox(
            org_id=org_id,
            workspace_id=workspace_id,
            user_id=None,
            scope_type="workspace",
            scope_id=workspace_id,
            sandbox_id=sbx_instance_id,
            status="paused",
            paused_at=now,
            paused_ttl_seconds=0,
            provider="cubepod",
            image="cubeplex-ubuntu24-desktop",
        )
        session.add(sbx)
        await session.flush()
        sync_ev = UserSandboxSyncEvent(
            org_id=org_id,
            workspace_id=workspace_id,
            user_sandbox_id=sbx.id,
            started_at=now,
            finished_at=now,
            status="success",
            n_pushed=3,
            n_removed=0,
        )
        session.add(sync_ev)
        await session.flush()
        sbx.last_skill_sync_event_id = sync_ev.id
        await session.commit()
        sbx_id, sync_ev_id = sbx.id, sync_ev.id

    first = await client.delete(f"/api/v1/workspaces/{workspace_id}")
    assert first.status_code == 200, first.text
    assert first.json() == {"deleted": False, "cleanup_pending": True}
    assert provider.terminated == []

    retry = await client.delete(f"/api/v1/workspaces/{workspace_id}")
    assert retry.status_code == 200, retry.text
    assert retry.json() == {"deleted": True, "cleanup_pending": False}
    assert provider.terminated == [(sbx_instance_id, True)]

    async with session_factory() as session:
        assert await session.get(Workspace, workspace_id) is None
        assert await session.get(UserSandbox, sbx_id) is None
        assert await session.get(UserSandboxSyncEvent, sync_ev_id) is None


async def test_delete_account_reclaims_user_sandbox_volumes_and_sync_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typing import Any

    from sqlalchemy import select

    import cubeplex.db as cubeplex_db
    from cubeplex.models import Role, User
    from cubeplex.models.user_sandbox_sync_event import UserSandboxSyncEvent
    from cubeplex.sandbox.base import SandboxCapabilities
    from cubeplex.sandbox.manager import get_sandbox_manager
    from cubeplex.sandbox.provider import ProviderInstanceState, SandboxProvider
    from cubeplex.sandbox.registry import ProviderRegistry
    from tests.e2e.conftest import (
        _ensure_default_user_and_membership,
        _lifespan_context,
        _login_and_attach,
        _make_isolated_user,
    )

    class _AccountDeleteProvider(SandboxProvider):
        def __init__(self) -> None:
            self.terminated: list[tuple[str, bool]] = []
            self.failures_remaining = 2

        @property
        def name(self) -> str:
            return "cubepod"

        @property
        def capabilities(self) -> SandboxCapabilities:
            return SandboxCapabilities(
                background=True, persistent_volume=True, pause=True, reset_rootfs=True
            )

        @property
        def default_workdir(self) -> str:
            return "/workspace"

        @property
        def default_image(self) -> str | None:
            return "cubeplex-ubuntu24-desktop"

        async def create(self, spec: Any, *, on_created: Any = None) -> Any:
            raise NotImplementedError

        async def connect(
            self,
            sandbox_id: str,
            *,
            workdir: str | None = None,
            user_sandbox_id: str | None = None,
            run_identity: Any = None,
        ) -> Any:
            raise NotImplementedError

        async def inspect_state(self, sandbox_id: str) -> ProviderInstanceState | None:
            del sandbox_id
            return ProviderInstanceState.PAUSED

        async def terminate(self, sandbox_id: str, *, delete_volumes: bool = False) -> None:
            if self.failures_remaining > 0 and sandbox_id.startswith("cpod-usr-"):
                self.failures_remaining -= 1
                raise RuntimeError("transient provider error")
            self.terminated.append((sandbox_id, delete_volumes))

    await _ensure_default_user_and_membership()
    app, email, password, workspace_id = await _make_isolated_user(Role.MEMBER)
    app.state.deployment_mode = "multi_tenant"
    sbx_instance_id = f"cpod-usr-{uuid4().hex[:8]}"

    async with _lifespan_context(app):
        provider = _AccountDeleteProvider()
        mgr = get_sandbox_manager()
        monkeypatch.setattr(
            mgr,
            "_registry",
            ProviderRegistry({"cubepod": provider}, default_name="cubepod"),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login_and_attach(client, email, password)
            async with cubeplex_db.async_session_maker() as session:
                user_row = (
                    await session.execute(select(User).where(User.email == email))
                ).scalar_one()
                ws_row = await session.get(Workspace, workspace_id)
                assert ws_row is not None
                now = datetime.now(UTC)
                sbx = UserSandbox(
                    org_id=ws_row.org_id,
                    workspace_id=ws_row.id,
                    user_id=user_row.id,
                    scope_type="user",
                    scope_id=user_row.id,
                    sandbox_id=sbx_instance_id,
                    status="paused",
                    paused_at=now,
                    paused_ttl_seconds=0,
                    provider="cubepod",
                    image="cubeplex-ubuntu24-desktop",
                )
                session.add(sbx)
                await session.flush()
                sync_ev = UserSandboxSyncEvent(
                    org_id=ws_row.org_id,
                    workspace_id=ws_row.id,
                    user_sandbox_id=sbx.id,
                    started_at=now,
                    finished_at=now,
                    status="success",
                    n_pushed=1,
                    n_removed=0,
                )
                session.add(sync_ev)
                await session.flush()
                sbx.last_skill_sync_event_id = sync_ev.id

                # Also seed a sole-member workspace-scoped sandbox (user_id=None)
                # and a collaborator SandboxCommand referencing user_row's sandbox.
                sole_ws = Workspace(
                    org_id=ws_row.org_id,
                    name="Sole Member Shared Sandbox WS",
                    sandbox_mode="workspace",
                )
                session.add(sole_ws)
                await session.flush()
                from cubeplex.models import Membership

                session.add(
                    Membership(
                        user_id=user_row.id,
                        workspace_id=sole_ws.id,
                        role=Role.ADMIN.value,
                    )
                )
                sole_ws_instance_id = f"cpod-solews-{uuid4().hex[:8]}"
                sole_ws_sbx = UserSandbox(
                    org_id=ws_row.org_id,
                    workspace_id=sole_ws.id,
                    user_id=None,
                    scope_type="workspace",
                    scope_id=sole_ws.id,
                    sandbox_id=sole_ws_instance_id,
                    status="paused",
                    paused_at=now,
                    paused_ttl_seconds=0,
                    provider="cubepod",
                    image="cubeplex-ubuntu24-desktop",
                )
                session.add(sole_ws_sbx)
                await session.flush()

                from cubeplex.models.conversation import Conversation
                from cubeplex.models.sandbox_command import SandboxCommand

                other_user = (
                    await session.execute(select(User).where(User.email != email).limit(1))
                ).scalar_one()
                collab_conv = Conversation(
                    org_id=ws_row.org_id,
                    workspace_id=ws_row.id,
                    creator_user_id=other_user.id,
                    title="Collaborator conv on shared topic sandbox",
                )
                session.add(collab_conv)
                await session.flush()
                collab_adm = ConversationExecutionAdmission(
                    org_id=ws_row.org_id,
                    workspace_id=ws_row.id,
                    conversation_id=collab_conv.id,
                    actor_user_id=other_user.id,
                    execution_generation=0,
                    source_kind="user_message",
                    source_id=f"web:{uuid4()}",
                    run_id="run-collab-1",
                )
                session.add(collab_adm)
                await session.flush()
                collab_task = BackgroundTask(
                    org_id=ws_row.org_id,
                    workspace_id=ws_row.id,
                    conversation_id=collab_conv.id,
                    admission_id=collab_adm.id,
                    kind="execute",
                    execution_generation=0,
                    originating_run_id="run-collab-1",
                    tool_call_id=f"tc-{uuid4().hex[:8]}",
                    started_by_user_id=other_user.id,
                    state="succeeded",
                    result_readiness="ready",
                    foreground_result_delivered_at=now,
                    finished_at=now,
                )
                session.add(collab_task)
                await session.flush()
                collab_cmd = SandboxCommand(
                    org_id=ws_row.org_id,
                    workspace_id=ws_row.id,
                    conversation_id=collab_conv.id,
                    task_id=collab_task.id,
                    run_id="run-collab-1",
                    started_by_user_id=other_user.id,
                    user_sandbox_id=sbx.id,
                    sandbox_instance_id=sbx_instance_id,
                    provider="cubepod",
                    command="echo hi",
                    status="exited",
                    exit_code=0,
                    log_state="complete",
                )
                session.add(collab_cmd)
                await session.commit()
                user_id, sbx_id, sync_ev_id = user_row.id, sbx.id, sync_ev.id
                sole_ws_sbx_id, collab_cmd_id = sole_ws_sbx.id, collab_cmd.id

            first = await client.post("/api/v1/auth/delete-account", json={"password": password})
            assert first.status_code == 200, first.text
            assert first.json() == {"deleted": False, "cleanup_pending": True}
            assert provider.terminated == [(sole_ws_instance_id, True)]

            retry = await client.post("/api/v1/auth/delete-account", json={"password": password})
            assert retry.status_code == 200, retry.text
            assert retry.json() == {"deleted": True, "cleanup_pending": False}
            assert set(provider.terminated) == {
                (sole_ws_instance_id, True),
                (sbx_instance_id, True),
            }

            async with cubeplex_db.async_session_maker() as session:
                from cubeplex.models.sandbox_command import SandboxCommand

                assert await session.get(User, user_id) is None
                assert await session.get(UserSandbox, sbx_id) is None
                assert await session.get(UserSandbox, sole_ws_sbx_id) is None
                assert await session.get(UserSandboxSyncEvent, sync_ev_id) is None
                assert await session.get(SandboxCommand, collab_cmd_id) is None


async def test_restart_cubepod_sandbox_finalizes_managed_tasks_allowing_workspace_delete(
    admin_client: tuple[httpx.AsyncClient, str],
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-destructive Restart on CubePod must terminalize managed BackgroundTasks
    so neither in_use_until nor workspace hard-delete deadlocks on stale tasks."""
    from typing import Any

    from cubeplex.sandbox.base import SandboxCapabilities
    from cubeplex.sandbox.manager import get_sandbox_manager
    from cubeplex.sandbox.provider import ProviderInstanceState, SandboxProvider
    from cubeplex.sandbox.registry import ProviderRegistry

    class _PauseRestartProvider(SandboxProvider):
        def __init__(self) -> None:
            self.paused: list[str] = []
            self.terminated: list[tuple[str, bool]] = []

        @property
        def name(self) -> str:
            return "cubepod"

        @property
        def capabilities(self) -> SandboxCapabilities:
            return SandboxCapabilities(
                background=True, persistent_volume=True, pause=True, reset_rootfs=True
            )

        @property
        def default_workdir(self) -> str:
            return "/workspace"

        @property
        def default_image(self) -> str | None:
            return "cubeplex-ubuntu24-desktop"

        async def create(self, spec: Any, *, on_created: Any = None) -> Any:
            raise NotImplementedError

        async def connect(
            self,
            sandbox_id: str,
            *,
            workdir: str | None = None,
            user_sandbox_id: str | None = None,
            run_identity: Any = None,
        ) -> Any:
            raise NotImplementedError

        async def pause(self, sandbox_id: str) -> None:
            self.paused.append(sandbox_id)

        async def inspect_state(self, sandbox_id: str) -> ProviderInstanceState | None:
            del sandbox_id
            return ProviderInstanceState.PAUSED

        async def terminate(self, sandbox_id: str, *, delete_volumes: bool = False) -> None:
            self.terminated.append((sandbox_id, delete_volumes))

    client, _ = admin_client
    workspaces = await client.get("/api/v1/workspaces")
    org_id = workspaces.json()[0]["org_id"]
    created = await client.post(
        "/api/v1/workspaces",
        json={"name": "restart-task-ws", "org_id": org_id},
    )
    assert created.status_code == 201, created.text
    workspace_id = created.json()["id"]
    user_id = (await client.get("/api/v1/auth/me")).json()["id"]

    provider = _PauseRestartProvider()
    mgr = get_sandbox_manager()
    monkeypatch.setattr(
        mgr,
        "_registry",
        ProviderRegistry({"cubepod": provider}, default_name="cubepod"),
    )
    monkeypatch.setattr(mgr, "_session_factory", session_factory)

    instance_id = f"cpod-rst-{uuid4().hex[:8]}"
    async with session_factory() as session:
        now = datetime.now(UTC)
        conv = Conversation(
            org_id=org_id,
            workspace_id=workspace_id,
            creator_user_id=user_id,
            title="restart-bg-task",
        )
        session.add(conv)
        await session.flush()
        adm = ConversationExecutionAdmission(
            org_id=org_id,
            workspace_id=workspace_id,
            conversation_id=conv.id,
            actor_user_id=user_id,
            execution_generation=0,
            source_kind="user_message",
            source_id=f"web:{uuid4()}",
            run_id=str(uuid4()),
        )
        sbx = UserSandbox(
            org_id=org_id,
            workspace_id=workspace_id,
            user_id=user_id,
            scope_type="user",
            scope_id=user_id,
            sandbox_id=instance_id,
            status="running",
            provider="cubepod",
            image="cubeplex-ubuntu24-desktop",
        )
        session.add_all([adm, sbx])
        await session.flush()
        reserved = await BackgroundTaskService(
            session, org_id=org_id, workspace_id=workspace_id
        ).reserve_task(
            admission_id=adm.id,
            task_spec=TaskSpec(
                originating_run_id=adm.run_id or "",
                tool_call_id=str(uuid4()),
            ),
            execution_details=CommandExecutionDetails(
                user_sandbox_id=sbx.id,
                sandbox_instance_id=instance_id,
                provider="cubepod",
                command="sleep 9999",
                log_path="/workspace/sleep.log",
            ),
            owner_token=str(uuid4()),
            owner_until=now + timedelta(seconds=30),
            now=now,
        )
        adm.revoked_at = now
        adm.run_finished_at = now
        reserved.task.state = "running"
        reserved.task.backgrounded_at = now
        reserved.command.status = "running"
        reserved.command.provider_ref = "proc-1"
        await session.commit()
        sbx_id, task_id, cmd_id = sbx.id, reserved.task.id, reserved.command.id

    rst = await client.post(f"/api/v1/ws/{workspace_id}/sandboxes/{sbx_id}/restart")
    assert rst.status_code == 202, rst.text
    assert provider.paused == [instance_id]

    async with session_factory() as session:
        task_after = await session.get(BackgroundTask, task_id)
        cmd_after = await session.get(SandboxCommand, cmd_id)
        sbx_after = await session.get(UserSandbox, sbx_id)
        assert task_after is not None and task_after.state == "cancelled"
        assert cmd_after is not None and cmd_after.status == "killed"
        assert cmd_after.log_state == "unavailable"
        assert sbx_after is not None and sbx_after.status == "paused"
        assert sbx_after.in_use_until is None

    deleted = await client.delete(f"/api/v1/workspaces/{workspace_id}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"deleted": True, "cleanup_pending": False}
    assert provider.terminated == [(instance_id, True)]
