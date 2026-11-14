"""E2E tests for capability-gated sandbox lifecycle extensions (/reset-rootfs)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from cubepod import AsyncCubePod
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import cubeplex.sandbox.command_coordinator as command_coordinator
from cubeplex.config import config
from cubeplex.models.background_task import BackgroundTask, BackgroundTaskState, TaskStopReason
from cubeplex.models.conversation import Conversation
from cubeplex.models.conversation_execution import ConversationExecutionAdmission
from cubeplex.models.sandbox_command import SandboxCommand, SandboxCommandStatus
from cubeplex.models.user_sandbox import UserSandbox
from cubeplex.repositories.user_sandbox import UserSandboxRepository
from cubeplex.sandbox.manager import get_sandbox_manager
from cubeplex.sandbox.providers.cubepod import CubePodProvider
from cubeplex.sandbox.providers.local import LocalSandboxProvider
from cubeplex.sandbox.providers.opensandbox import OpenSandboxProvider
from cubeplex.sandbox.registry import ProviderRegistry


class _FakeCubePodBackend:
    """In-memory CubePod gateway transport recording RootFS reset and guest commands."""

    def __init__(self) -> None:
        self.states: dict[str, str] = {}
        self.reset_calls: list[str] = []
        self.connect_calls: list[str] = []
        self.commands: list[tuple[str, dict[str, Any]]] = []
        self.files: dict[tuple[str, str], bytes] = {}
        self.rootfs_generation: dict[str, int] = {}

    def client(self) -> AsyncCubePod:
        return AsyncCubePod(
            base_url="http://cubepod-test:8080",
            api_key="test-token",
            transport=httpx.MockTransport(self._handle),
            max_retries=0,
            trust_env=False,
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/sandboxes":
            sid = "cpod-e2e-1"
            self.states[sid] = "Running"
            self.rootfs_generation[sid] = 1
            return httpx.Response(
                201,
                json={
                    "sandboxID": sid,
                    "templateID": "cubeplex-ubuntu24-desktop",
                    "state": "Running",
                },
            )
        if path.startswith("/sandboxes/"):
            parts = path.strip("/").split("/")
            sid = parts[1]
            suffix = "/".join(parts[2:])
            if request.method == "GET" and suffix == "":
                st = self.states.get(sid)
                if st is None:
                    return httpx.Response(404, json={"error": "not_found"})
                return httpx.Response(
                    200,
                    json={
                        "sandboxID": sid,
                        "templateID": "cubeplex-ubuntu24-desktop",
                        "state": st,
                        "rootfsGeneration": self.rootfs_generation.get(sid, 1),
                    },
                )
            if request.method == "POST" and suffix == "connect":
                self.connect_calls.append(sid)
                self.states[sid] = "Running"
                return httpx.Response(
                    200,
                    json={
                        "sandboxID": sid,
                        "templateID": "cubeplex-ubuntu24-desktop",
                        "state": "Running",
                    },
                )
            if request.method == "POST" and suffix == "reset-rootfs":
                self.reset_calls.append(sid)
                # RootFS wipe removes /run files while keeping /workspace PVC files.
                for key in list(self.files):
                    if key[0] == sid and not key[1].startswith("/workspace"):
                        del self.files[key]
                self.rootfs_generation[sid] = self.rootfs_generation.get(sid, 1) + 1
                self.states[sid] = "Running"
                return httpx.Response(
                    200,
                    json={
                        "sandboxID": sid,
                        "templateID": "cubeplex-ubuntu24-desktop",
                        "state": "Running",
                        "rootfsGeneration": self.rootfs_generation[sid],
                    },
                )
            if request.method == "PUT" and suffix == "files":
                file_path = request.url.params["path"]
                self.files[(sid, file_path)] = request.content
                return httpx.Response(200, json={"written": True})
            if request.method == "GET" and suffix == "files":
                file_path = request.url.params["path"]
                content = self.files.get((sid, file_path))
                if content is None:
                    return httpx.Response(404, json={"error": "not_found"})
                return httpx.Response(200, content=content)
            if request.method == "POST" and suffix == "commands/run":
                payload = json.loads(request.content.decode("utf-8"))
                self.commands.append((sid, payload))
                return httpx.Response(
                    200,
                    json={"id": "cmd-exec", "exitCode": 0, "output": "", "running": False},
                )
        raise AssertionError(f"Unhandled fake CubePod request: {request.method} {request.url}")


def _mount_registry(
    monkeypatch: pytest.MonkeyPatch,
    registry: ProviderRegistry,
) -> None:
    manager = get_sandbox_manager()
    monkeypatch.setattr(manager, "_registry", registry)
    monkeypatch.setattr(
        "cubeplex.api.routes.v1.admin_sandbox_policy.get_provider_registry",
        lambda: registry,
    )


async def _org_for_workspace(session: AsyncSession, ws_id: str) -> str:
    from cubeplex.models import Workspace

    ws = (
        await session.execute(
            select(Workspace).where(Workspace.id == ws_id)  # type: ignore[arg-type]
        )
    ).scalar_one()
    return ws.org_id


async def test_reset_rootfs_returns_404_when_absent_or_terminal(
    admin_client_with_user_id: tuple[httpx.AsyncClient, str, str],
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, ws_id, user_id = admin_client_with_user_id
    backend = _FakeCubePodBackend()
    cpod = CubePodProvider(client=backend.client(), poll_interval=0.0)
    _mount_registry(
        monkeypatch,
        ProviderRegistry({"cubepod": cpod}, default_name="cubepod"),
    )

    absent_resp = await client.post(f"/api/v1/ws/{ws_id}/sandbox/reset-rootfs")
    assert absent_resp.status_code == 404

    for idx, terminal_status in enumerate(("terminated", "failed")):
        async with session_factory() as session:
            org_id = await _org_for_workspace(session, ws_id)
            row = UserSandbox(
                org_id=org_id,
                workspace_id=ws_id,
                scope_type="user",
                scope_id=f"{user_id}-{idx}",
                user_id=user_id,
                provider="cubepod",
                sandbox_id=f"cpod-dead-{ws_id}-{idx}",
                image="cubeplex-ubuntu24-desktop",
                status=terminal_status,
                expires_at=datetime.now(UTC),
                last_activity_at=datetime.now(UTC),
            )
            if terminal_status == "terminated":
                # Keep one row per (org, ws, scope_type, scope_id); use conversation scope for test.
                conv = Conversation(
                    org_id=org_id,
                    workspace_id=ws_id,
                    creator_user_id=user_id,
                    title="dead",
                )
                session.add(conv)
                await session.flush()
                row.scope_type = "conversation"
                row.scope_id = conv.id
            session.add(row)
            await session.commit()

    term_resp = await client.post(f"/api/v1/ws/{ws_id}/sandbox/reset-rootfs")
    assert term_resp.status_code == 404
    assert backend.reset_calls == []


async def test_reset_rootfs_returns_501_for_providers_without_capability(
    admin_client_with_user_id: tuple[httpx.AsyncClient, str, str],
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, ws_id, user_id = admin_client_with_user_id
    local = LocalSandboxProvider(base_workdir=str(tmp_path))
    opensandbox = OpenSandboxProvider()
    _mount_registry(
        monkeypatch,
        ProviderRegistry(
            {"local": local, "opensandbox": opensandbox},
            default_name="local",
        ),
    )

    async with session_factory() as session:
        org_id = await _org_for_workspace(session, ws_id)
        local_row = UserSandbox(
            org_id=org_id,
            workspace_id=ws_id,
            scope_type="user",
            scope_id=user_id,
            user_id=user_id,
            provider="local",
            sandbox_id=f"local-{ws_id}",
            image="local",
            status="running",
            expires_at=datetime.now(UTC),
            last_activity_at=datetime.now(UTC),
        )
        session.add(local_row)
        await session.commit()

    resp_local = await client.post(f"/api/v1/ws/{ws_id}/sandbox/reset-rootfs")
    assert resp_local.status_code == 501, resp_local.text

    async with session_factory() as session:
        row = await session.get(UserSandbox, local_row.id)
        assert row is not None
        row.provider = "opensandbox"
        row.sandbox_id = f"osbx-{ws_id}"
        await session.commit()

    resp_os = await client.post(f"/api/v1/ws/{ws_id}/sandbox/reset-rootfs")
    assert resp_os.status_code == 501, resp_os.text


@pytest.mark.parametrize(
    "transient_status",
    ["provisioning", "pausing", "resuming", "kill_pending"],
)
async def test_reset_rootfs_returns_409_during_transient_states(
    transient_status: str,
    admin_client_with_user_id: tuple[httpx.AsyncClient, str, str],
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, ws_id, user_id = admin_client_with_user_id
    backend = _FakeCubePodBackend()
    cpod = CubePodProvider(client=backend.client(), poll_interval=0.0)
    _mount_registry(
        monkeypatch,
        ProviderRegistry({"cubepod": cpod}, default_name="cubepod"),
    )

    async with session_factory() as session:
        org_id = await _org_for_workspace(session, ws_id)
        session.add(
            UserSandbox(
                org_id=org_id,
                workspace_id=ws_id,
                scope_type="user",
                scope_id=user_id,
                user_id=user_id,
                provider="cubepod",
                sandbox_id=f"cpod-transient-{transient_status}-{ws_id}",
                image="cubeplex-ubuntu24-desktop",
                status=transient_status,
                expires_at=datetime.now(UTC),
                last_activity_at=datetime.now(UTC),
            )
        )
        await session.commit()

    resp = await client.post(f"/api/v1/ws/{ws_id}/sandbox/reset-rootfs")
    assert resp.status_code == 409, resp.text
    assert backend.reset_calls == []


async def test_reset_rootfs_promotes_paused_fences_tasks_and_rebootstraps_guest(
    admin_client_with_user_id: tuple[httpx.AsyncClient, str, str],
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, ws_id, user_id = admin_client_with_user_id
    backend = _FakeCubePodBackend()
    orig_get = config.get
    overrides = {
        "sandbox.cubepod.default_template": "cubeplex-ubuntu24-desktop",
        "sandbox.cubepod.base_url": "http://cubepod-test:8080",
    }

    def _patched_get(key: str, default: Any = None, *args: Any, **kwargs: Any) -> Any:
        if key in overrides:
            return overrides[key]
        return orig_get(key, default, *args, **kwargs)

    monkeypatch.setattr(config, "get", _patched_get)
    cpod = CubePodProvider(client=backend.client(), poll_interval=0.0)
    _mount_registry(
        monkeypatch,
        ProviderRegistry({"cubepod": cpod}, default_name="cubepod"),
    )

    # Seed a plain workspace env var so guest re-bootstrap rewrites sandbox-env.sh.
    env_resp = await client.post(
        f"/api/v1/ws/{ws_id}/sandbox-env/workspace",
        json={
            "env_name": "CUBE_RESET_TEST",
            "is_secret": False,
            "secret_value": "fresh-value",
        },
    )
    assert env_resp.status_code == 201, env_resp.text

    # Seed a persisted skill file under /workspace/.skills and stale RootFS state.
    sandbox_id = f"cpod-e2e-paused-{ws_id}"
    backend.states[sandbox_id] = "Paused"
    backend.files[(sandbox_id, "/workspace/.skills/demo/SKILL.md")] = b"# skill"
    backend.files[(sandbox_id, "/run/cubeplex/sandbox-env.sh")] = b"export STALE=1\n"

    async with session_factory() as session:
        org_id = await _org_for_workspace(session, ws_id)
        usbx = UserSandbox(
            org_id=org_id,
            workspace_id=ws_id,
            scope_type="user",
            scope_id=user_id,
            user_id=user_id,
            provider="cubepod",
            sandbox_id=sandbox_id,
            image="cubeplex-ubuntu24-desktop",
            status="paused",
            paused_at=datetime.now(UTC),
            skills_manifest_hash="abc123",
            skills_count=1,
            expires_at=datetime.now(UTC),
            last_activity_at=datetime.now(UTC),
        )
        conv = Conversation(
            org_id=org_id,
            workspace_id=ws_id,
            creator_user_id=user_id,
            title="reset-rootfs conv",
        )
        session.add(usbx)
        session.add(conv)
        await session.flush()

        admission = ConversationExecutionAdmission(
            org_id=org_id,
            workspace_id=ws_id,
            conversation_id=conv.id,
            actor_user_id=user_id,
            source_kind="user_message",
            source_id="src-1",
            execution_generation=1,
            run_id="run-1",
        )
        session.add(admission)
        await session.flush()

        task = BackgroundTask(
            org_id=org_id,
            workspace_id=ws_id,
            conversation_id=conv.id,
            admission_id=admission.id,
            kind="execute",
            description="long running",
            originating_run_id="run-1",
            tool_call_id="tc-1",
            started_by_user_id=user_id,
            execution_generation=1,
            state=BackgroundTaskState.running.value,
        )
        session.add(task)
        await session.flush()

        cmd = SandboxCommand(
            org_id=org_id,
            workspace_id=ws_id,
            user_sandbox_id=usbx.id,
            task_id=task.id,
            sandbox_instance_id=sandbox_id,
            conversation_id=conv.id,
            run_id="run-1",
            started_by_user_id=user_id,
            command="sleep 60",
            provider="cubepod",
            provider_ref="cmd-old",
            status=SandboxCommandStatus.running.value,
        )
        session.add(cmd)
        await session.commit()
        usbx_id = usbx.id
        task_id = task.id

    resp = await client.post(f"/api/v1/ws/{ws_id}/sandbox/reset-rootfs")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "ok": True,
        "user_sandbox_id": usbx_id,
        "sandbox_id": sandbox_id,
    }

    assert backend.reset_calls == [sandbox_id]
    assert backend.connect_calls == []  # reset_rootfs reconciled Running; connect() used GET
    assert backend.states[sandbox_id] == "Running"
    # Stale RootFS /run file was wiped and /workspace/.skills survived intact.
    assert (sandbox_id, "/run/cubeplex/sandbox-env.sh") not in backend.files
    assert backend.files[(sandbox_id, "/workspace/.skills/demo/SKILL.md")] == b"# skill"

    # Guest re-bootstrap ran ensure_workspace_owner() and write_terminal_env() with new env.
    cmd_strings = [payload["command"] for sid, payload in backend.commands if sid == sandbox_id]
    assert any("chown" in c for c in cmd_strings)
    env_cmds = [c for c in cmd_strings if "/run/cubeplex/sandbox-env.sh" in c]
    assert len(env_cmds) == 1
    # Verify encoded script inside write_terminal_env contains our workspace env var.
    assert any(
        "export CUBE_RESET_TEST=fresh-value" in line
        for token in env_cmds[0].split()
        if len(token) > 12 and token.isascii()
        for line in _try_b64_decode(token).splitlines()
    )

    # DB state promoted paused -> running, released in_use lease, preserved skill metadata,
    # and fenced inflight background task.
    async with session_factory() as session:
        refreshed = await session.get(UserSandbox, usbx_id)
        assert refreshed is not None
        assert refreshed.status == "running"
        assert refreshed.last_resumed_at is not None
        assert refreshed.in_use_until is None
        assert refreshed.skills_manifest_hash == "abc123"
        assert refreshed.skills_count == 1

        refreshed_task = await session.get(BackgroundTask, task_id)
        assert refreshed_task is not None
        assert refreshed_task.stop_requested_at is not None
        assert refreshed_task.stop_reason == TaskStopReason.user_stop.value


def _try_b64_decode(raw: str) -> str:
    stripped = raw.strip("'\"")
    try:
        return base64.b64decode(stripped, validate=True).decode("utf-8", errors="ignore")
    except Exception:
        return ""


async def test_reset_rootfs_supports_running_dedicated_topic_conversation_scope(
    admin_client_with_user_id: tuple[httpx.AsyncClient, str, str],
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, ws_id, user_id = admin_client_with_user_id
    backend = _FakeCubePodBackend()
    cpod = CubePodProvider(client=backend.client(), poll_interval=0.0)
    _mount_registry(
        monkeypatch,
        ProviderRegistry({"cubepod": cpod}, default_name="cubepod"),
    )

    topic_resp = await client.post(
        f"/api/v1/ws/{ws_id}/topics",
        json={"title": "Dedicated reset topic", "sandbox_mode": "dedicated"},
    )
    assert topic_resp.status_code in (200, 201), topic_resp.text
    topic_data = topic_resp.json()
    topic_id = topic_data["topic"]["id"]
    conv_id = topic_data["conversation"]["id"]

    sandbox_id = f"cpod-topic-running-{ws_id}"
    backend.states[sandbox_id] = "Running"
    async with session_factory() as session:
        org_id = await _org_for_workspace(session, ws_id)
        usbx = UserSandbox(
            org_id=org_id,
            workspace_id=ws_id,
            scope_type="topic",
            scope_id=topic_id,
            user_id=user_id,
            provider="cubepod",
            sandbox_id=sandbox_id,
            image="cubeplex-ubuntu24-desktop",
            status="running",
            expires_at=datetime.now(UTC),
            last_activity_at=datetime.now(UTC),
        )
        session.add(usbx)
        await session.commit()
        usbx_id = usbx.id

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/sandbox/reset-rootfs",
        params={"conversation_id": conv_id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "ok": True,
        "user_sandbox_id": usbx_id,
        "sandbox_id": sandbox_id,
    }
    assert backend.reset_calls == [sandbox_id]
    assert backend.rootfs_generation[sandbox_id] == 2


async def test_leased_rows_excluded_from_reconcile_and_expired_cleanup(
    admin_client_with_user_id: tuple[httpx.AsyncClient, str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _client, ws_id, user_id = admin_client_with_user_id
    now = datetime.now(UTC)

    async with session_factory() as session:
        org_id = await _org_for_workspace(session, ws_id)

        def _row(
            *,
            scope_id: str,
            status: str,
            expires_at: datetime,
            in_use_until: datetime | None,
        ) -> UserSandbox:
            return UserSandbox(
                org_id=org_id,
                workspace_id=ws_id,
                scope_type="conversation",
                scope_id=scope_id,
                user_id=user_id,
                provider="cubepod",
                sandbox_id=f"cpod-{ws_id}-{scope_id}",
                image="cubeplex-ubuntu24-desktop",
                status=status,
                expires_at=expires_at,
                last_activity_at=now - timedelta(hours=1),
                last_provider_check=None,
                in_use_until=in_use_until,
            )

        leased_transient = _row(
            scope_id="lease-tr-active",
            status="resuming",
            expires_at=now + timedelta(minutes=10),
            in_use_until=now + timedelta(seconds=120),
        )
        stale_transient = _row(
            scope_id="lease-tr-expired",
            status="resuming",
            expires_at=now + timedelta(minutes=10),
            in_use_until=now - timedelta(seconds=5),
        )
        leased_expired = _row(
            scope_id="lease-exp-active",
            status="running",
            expires_at=now - timedelta(seconds=60),
            in_use_until=now + timedelta(seconds=120),
        )
        unleased_expired = _row(
            scope_id="lease-exp-expired",
            status="running",
            expires_at=now - timedelta(seconds=60),
            in_use_until=now - timedelta(seconds=5),
        )
        session.add_all([leased_transient, stale_transient, leased_expired, unleased_expired])
        await session.commit()

        leased_transient_id = leased_transient.id
        stale_transient_id = stale_transient.id
        leased_expired_id = leased_expired.id
        unleased_expired_id = unleased_expired.id

    async with session_factory() as session:
        transient_ids = {
            r.id
            for r in await UserSandboxRepository.list_transient_for_reconcile_system(
                session, claim_timeout=0
            )
            if r.workspace_id == ws_id
        }
        expired_ids = {
            r.id
            for r in await UserSandboxRepository.list_expired_system(session)
            if r.workspace_id == ws_id
        }

    assert leased_transient_id not in transient_ids
    assert stale_transient_id in transient_ids
    assert leased_expired_id not in expired_ids
    assert unleased_expired_id in expired_ids


async def test_reset_rootfs_protects_lease_activity_runs_kill_commands_and_handles_reconciler_flip(
    admin_client_with_user_id: tuple[httpx.AsyncClient, str, str],
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, ws_id, user_id = admin_client_with_user_id
    backend = _FakeCubePodBackend()
    cpod = CubePodProvider(client=backend.client(), poll_interval=0.0)
    _mount_registry(
        monkeypatch,
        ProviderRegistry({"cubepod": cpod}, default_name="cubepod"),
    )

    sandbox_id = f"cpod-race-{ws_id}"
    backend.states[sandbox_id] = "Paused"
    old_activity = datetime.now(UTC) - timedelta(hours=1)
    async with session_factory() as session:
        org_id = await _org_for_workspace(session, ws_id)
        usbx = UserSandbox(
            org_id=org_id,
            workspace_id=ws_id,
            scope_type="user",
            scope_id=user_id,
            user_id=user_id,
            provider="cubepod",
            sandbox_id=sandbox_id,
            image="cubeplex-ubuntu24-desktop",
            status="paused",
            paused_at=old_activity,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            last_activity_at=old_activity,
        )
        session.add(usbx)
        await session.commit()
        usbx_id = usbx.id

    kill_calls: list[str] = []
    orig_kill = command_coordinator.kill_sandbox_commands

    async def _spy_kill(
        session: AsyncSession,
        target_sandbox_id: str,
        **kwargs: Any,
    ) -> list[str]:
        kill_calls.append(target_sandbox_id)
        return await orig_kill(session, target_sandbox_id, **kwargs)

    monkeypatch.setattr(command_coordinator, "kill_sandbox_commands", _spy_kill)

    inspected_during_reset: list[str] = []
    orig_inspect = cpod.inspect_state

    async def _spy_inspect(target_id: str) -> Any:
        inspected_during_reset.append(target_id)
        return await orig_inspect(target_id)

    monkeypatch.setattr(cpod, "inspect_state", _spy_inspect)

    orig_reset_rootfs = cpod.reset_rootfs

    async def _reset_with_concurrent_race(target_id: str) -> None:
        # While reset_rootfs is in-flight, lease + activity bump must hide row from reconciler.
        async with session_factory() as check_session:
            inflight = await check_session.get(UserSandbox, usbx_id)
            assert inflight is not None
            assert inflight.status == "resuming"
            assert inflight.in_use_until is not None
            assert inflight.in_use_until > datetime.now(UTC)
            assert inflight.last_activity_at > old_activity

        await get_sandbox_manager().reconcile_transients(claim_timeout=0)
        assert sandbox_id not in inspected_during_reset

        await orig_reset_rootfs(target_id)

        # Simulate a concurrent transition flipping resuming -> running before post-reset commit.
        async with session_factory() as race_session:
            race_repo = UserSandboxRepository(race_session, org_id=org_id, workspace_id=ws_id)
            assert await race_repo.mark_running(usbx_id, last_resumed_at=datetime.now(UTC))

    monkeypatch.setattr(cpod, "reset_rootfs", _reset_with_concurrent_race)

    resp = await client.post(f"/api/v1/ws/{ws_id}/sandbox/reset-rootfs")
    assert resp.status_code == 200, resp.text
    assert kill_calls == [usbx_id]

    cmd_strings = [payload["command"] for sid, payload in backend.commands if sid == sandbox_id]
    assert any("chown" in c for c in cmd_strings)
    assert any("/run/cubeplex/sandbox-env.sh" in c for c in cmd_strings)

    async with session_factory() as session:
        refreshed = await session.get(UserSandbox, usbx_id)
        assert refreshed is not None
        assert refreshed.status == "running"
        assert refreshed.in_use_until is None
        assert refreshed.last_activity_at > old_activity
