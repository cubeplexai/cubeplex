"""E2E tests for workspace sandbox mode ('per_user' vs 'workspace' shared).

Covers:
1. Workspace creation, listing, rename immutability, and onboarding (full + ws-only).
2. Repository DB validation + partial unique index on ('workspace', workspace_id).
3. Multi-user sharing across 1:1 conversations, group chats, topics, panels, and
   background task scope validation (plus 404 non-leak on private conversations).
4. Concurrent first-run provisioning race (IntegrityError -> winner wait + attach).
5. RBAC on GET /sandboxes (can_manage), restart, delete, reset-rootfs, and
   admin observability serialization with user_id=None.
6. Creator departure + account deletion preserving shared UserSandbox & EgressRef,
   and workspace deletion cleaning them up.
7. Sandbox env vault (/me disabled, /workspace member read-only, runtime & agent
   view excluding user-scope entries) and IM bot settings in shared workspaces.
"""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.schemas import BaseUserCreate
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.auth.users import UserManager, _slugify_org_name
from cubeplex.credentials.encryption import FernetBackend
from cubeplex.db.engine import _build_database_url
from cubeplex.im.conversation_resolver import resolve_im_conversation
from cubeplex.models import (
    Conversation,
    EgressRef,
    Membership,
    OrganizationMembership,
    OrgRole,
    Role,
    Topic,
    User,
    UserSandbox,
)
from cubeplex.models.im_connector import IMConnectorAccount
from cubeplex.repositories import (
    MembershipRepository,
    OrganizationMembershipRepository,
    OrganizationRepository,
    UserSandboxRepository,
    WorkspaceRepository,
)
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.repositories.sandbox_env import SandboxEnvRepository
from cubeplex.sandbox.base import ExecuteResult, Sandbox, SandboxCapabilities
from cubeplex.sandbox.manager import (
    SandboxManager,
    build_sandbox_pvc_name,
    get_sandbox_manager,
)
from cubeplex.sandbox.provider import (
    ProviderInstanceState,
    RunIdentity,
    SandboxCreateSpec,
    SandboxProvider,
)
from cubeplex.sandbox.providers.local import LocalSandboxProvider
from cubeplex.sandbox.registry import ProviderRegistry
from cubeplex.services.background_tasks import BackgroundTaskService
from cubeplex.services.credential import CredentialService
from cubeplex.services.sandbox_env import SandboxEnvService
from cubeplex.services.sandbox_runtime_config import load_agent_view
from cubeplex.streams.run_manager import RunManager
from cubeplex.tools.builtin.sandbox_config import make_session_loader
from tests.e2e.conftest import _lifespan_context, _login_and_attach, _make_memory_test_app

pytestmark = pytest.mark.e2e


def _make_session_maker() -> tuple[Any, async_sessionmaker[AsyncSession]]:
    eng = create_async_engine(_build_database_url(), poolclass=NullPool)
    return eng, async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


async def _create_org_user(
    session: AsyncSession,
    *,
    org_id: str,
    org_role: OrgRole,
    password: str = "StrongPass1!abc",
) -> tuple[User, str]:
    email = f"ws-sbx-{secrets.token_hex(5)}@example.com"
    user_db = SQLAlchemyUserDatabase(session, User)
    user = await UserManager(user_db).create(
        BaseUserCreate(email=email, password=password),
        safe=False,
    )
    await session.execute(
        sa_delete(OrganizationMembership).where(
            OrganizationMembership.user_id == user.id,  # type: ignore[arg-type]
        )
    )
    await session.execute(
        sa_delete(Membership).where(
            Membership.user_id == user.id,  # type: ignore[arg-type]
        )
    )
    await OrganizationMembershipRepository(session).grant(
        user_id=user.id,
        org_id=org_id,
        role=org_role,
    )
    await session.commit()
    return user, password


class _ResettableEgressSandbox(Sandbox):
    """In-memory Sandbox supporting reset_rootfs + egress env inspection."""

    def __init__(self, sandbox_id: str, workdir: str = "/workspace") -> None:
        self._id = sandbox_id
        self._workdir = workdir
        self.run_env: dict[str, str] = {}
        self.terminal_env_written = 0

    @property
    def id(self) -> str:
        return self._id

    @property
    def provider_name(self) -> str:
        return "resettable"

    @property
    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            background=True,
            reconnect=True,
            persistent_volume=True,
            reset_rootfs=True,
            egress_proxy=True,
        )

    @property
    def workdir(self) -> str:
        return self._workdir

    def set_run_env(self, env: dict[str, str]) -> None:
        self.run_env = dict(env)

    async def write_terminal_env(self) -> None:
        self.terminal_env_written += 1

    async def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        envs: dict[str, str] | None = None,
        as_root: bool = False,
        on_chunk: Any = None,
    ) -> ExecuteResult:
        del command, timeout, envs, as_root, on_chunk
        return ExecuteResult(output="ok", exit_code=0)

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        del files

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        del paths
        return []

    async def close(self) -> None:
        return None


class _ResettableEgressProvider(SandboxProvider):
    """Provider fake supporting reset_rootfs, egress_proxy, and optional create gate."""

    def __init__(self, *, create_gate: asyncio.Event | None = None) -> None:
        self.created_specs: list[SandboxCreateSpec] = []
        self.reset_calls: list[str] = []
        self.terminated_ids: list[str] = []
        self.instances: dict[str, _ResettableEgressSandbox] = {}
        self.first_create_entered = asyncio.Event()
        self._create_gate = create_gate

    @property
    def name(self) -> str:
        return "resettable"

    @property
    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            background=True,
            reconnect=True,
            persistent_volume=True,
            reset_rootfs=True,
            egress_proxy=True,
        )

    @property
    def default_image(self) -> str:
        return "ubuntu:22.04"

    @property
    def default_workdir(self) -> str:
        return "/workspace"

    async def create(
        self,
        spec: SandboxCreateSpec,
        *,
        on_created: Any = None,
    ) -> Sandbox:
        self.created_specs.append(spec)
        self.first_create_entered.set()
        if self._create_gate is not None:
            await self._create_gate.wait()
        sbx_id = f"sbx-{secrets.token_hex(6)}"
        if on_created is not None:
            await on_created(sbx_id)
        inst = _ResettableEgressSandbox(sbx_id, workdir=spec.workdir)
        self.instances[sbx_id] = inst
        return inst

    async def connect(
        self,
        sandbox_id: str,
        *,
        workdir: str,
        user_sandbox_id: str | None = None,
        run_identity: RunIdentity | None = None,
    ) -> Sandbox:
        del user_sandbox_id, run_identity
        existing = self.instances.get(sandbox_id)
        if existing is not None:
            return existing
        inst = _ResettableEgressSandbox(sandbox_id, workdir=workdir)
        self.instances[sandbox_id] = inst
        return inst

    async def terminate(self, sandbox_id: str) -> None:
        self.terminated_ids.append(sandbox_id)
        self.instances.pop(sandbox_id, None)

    async def inspect_state(self, sandbox_id: str) -> ProviderInstanceState | None:
        if sandbox_id in self.instances:
            return ProviderInstanceState.RUNNING
        return None

    async def reset_rootfs(self, sandbox_id: str) -> None:
        self.reset_calls.append(sandbox_id)


@pytest.mark.asyncio
async def test_workspace_crud_and_onboarding_sandbox_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST/GET/PATCH /workspaces and POST /onboarding persist immutable sandbox_mode."""
    monkeypatch.setattr("cubeplex.auth.email_otp.is_email_verification_enabled", lambda: False)
    app = _make_memory_test_app()
    app.state.deployment_mode = "multi_tenant"

    eng, maker = _make_session_maker()
    try:
        async with _lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            # 1. Full onboarding with sandbox_mode='workspace'
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                email = f"onb-shared-{secrets.token_hex(4)}@example.com"
                pw = "StrongPass1!abc"
                slug = f"shared-{secrets.token_hex(4)}"
                reg = await client.post(
                    "/api/v1/auth/register",
                    json={"email": email, "password": pw},
                )
                assert reg.status_code == 201, reg.text
                await _login_and_attach(client, email, pw)

                onb = await client.post(
                    "/api/v1/onboarding",
                    json={
                        "org_name": "Shared Org",
                        "org_slug": slug,
                        "workspace_name": "Shared Root WS",
                        "sandbox_mode": "workspace",
                    },
                )
                assert onb.status_code == 201, onb.text
                onb_ws_id = onb.json()["workspace_id"]

                listed = await client.get("/api/v1/workspaces")
                assert listed.status_code == 200, listed.text
                ws_items = {w["id"]: w for w in listed.json()}
                assert ws_items[onb_ws_id]["sandbox_mode"] == "workspace"
                assert ws_items[onb_ws_id]["role"] == "admin"
                org_id = ws_items[onb_ws_id]["org_id"]

                # 2. POST /workspaces default ('per_user') vs explicit ('workspace') + invalid (422)
                created_default = await client.post(
                    "/api/v1/workspaces",
                    json={"name": "Isolated WS", "org_id": org_id},
                )
                assert created_default.status_code == 201, created_default.text
                assert created_default.json()["role"] == "admin"
                assert created_default.json()["sandbox_mode"] == "per_user"

                created_shared = await client.post(
                    "/api/v1/workspaces",
                    json={
                        "name": "Team Shared WS",
                        "org_id": org_id,
                        "sandbox_mode": "workspace",
                    },
                )
                assert created_shared.status_code == 201, created_shared.text
                shared_ws_id = created_shared.json()["id"]
                assert created_shared.json() == {
                    "id": shared_ws_id,
                    "name": "Team Shared WS",
                    "org_id": org_id,
                    "role": "admin",
                    "sandbox_mode": "workspace",
                }

                bad_mode = await client.post(
                    "/api/v1/workspaces",
                    json={
                        "name": "Invalid Mode WS",
                        "org_id": org_id,
                        "sandbox_mode": "container_per_topic",
                    },
                )
                assert bad_mode.status_code == 422

                # 3. PATCH /workspaces/{id} renames only and never mutates sandbox_mode
                patched = await client.patch(
                    f"/api/v1/workspaces/{shared_ws_id}",
                    json={"name": "Renamed Shared WS", "sandbox_mode": "per_user"},
                )
                assert patched.status_code == 200, patched.text
                assert patched.json() == {
                    "id": shared_ws_id,
                    "name": "Renamed Shared WS",
                    "org_id": org_id,
                    "role": "admin",
                    "sandbox_mode": "workspace",
                }

            # 4. Workspace-only onboarding (existing org member with no workspace yet)
            async with maker() as session:
                ws_only_user, ws_only_pw = await _create_org_user(
                    session, org_id=org_id, org_role=OrgRole.MEMBER
                )
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client2:
                await _login_and_attach(client2, ws_only_user.email, ws_only_pw)
                onb2 = await client2.post(
                    "/api/v1/onboarding",
                    json={"workspace_name": "Invited Shared WS", "sandbox_mode": "workspace"},
                )
                assert onb2.status_code == 201, onb2.text
                ws2_id = onb2.json()["workspace_id"]
                listed2 = await client2.get("/api/v1/workspaces")
                assert listed2.status_code == 200
                by_id2 = {w["id"]: w for w in listed2.json()}
                assert by_id2[ws2_id]["sandbox_mode"] == "workspace"
                assert by_id2[ws2_id]["role"] == "admin"
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_repository_db_invariants_and_unique_active_workspace_sandbox() -> None:
    """Postgres enforces nullable user_id + active partial unique index on ('workspace', ws_id)."""
    eng, maker = _make_session_maker()
    try:
        async with maker() as session:
            suffix = secrets.token_hex(4)
            org = await OrganizationRepository(session).create(
                name=f"Repo Org {suffix}", slug=f"repo-org-{suffix}"
            )
            ws_repo = WorkspaceRepository(session)
            ws = await ws_repo.create(
                org_id=org.id, name="Shared Repo WS", sandbox_mode="workspace"
            )
            assert ws.sandbox_mode == "workspace"
            ws_id = ws.id

            with pytest.raises(ValueError, match="sandbox_mode"):
                await ws_repo.create(
                    org_id=org.id,
                    name="Bad",
                    sandbox_mode="invalid",  # type: ignore[arg-type]
                )

            sbx_repo = UserSandboxRepository(session, org_id=org.id, workspace_id=ws_id)
            with pytest.raises(ValueError, match="user_id"):
                await sbx_repo.reserve(
                    user_id="usr-should-not-be-set",
                    image="ubuntu:22.04",
                    scope_type="workspace",
                    scope_id=ws_id,
                )

            reserved = await sbx_repo.reserve(
                user_id=None,
                image="ubuntu:22.04",
                scope_type="workspace",
                scope_id=ws_id,
            )
            assert reserved.user_id is None
            assert reserved.scope_type == "workspace"
            assert reserved.scope_id == ws_id
            reserved_id = reserved.id

            # Partial unique index uq_user_sandbox_active_scope rejects a second live row
            with pytest.raises(IntegrityError):
                await sbx_repo.reserve(
                    user_id=None,
                    image="ubuntu:22.04",
                    scope_type="workspace",
                    scope_id=ws_id,
                )
            await session.rollback()

            # Soft-deleting the first row frees the scope for a fresh reserve
            assert await sbx_repo.soft_delete(reserved_id) is True
            replacement = await sbx_repo.reserve(
                user_id=None,
                image="ubuntu:22.04",
                scope_type="workspace",
                scope_id=ws_id,
            )
            assert replacement.id != reserved_id
            assert replacement.user_id is None
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_shared_workspace_members_share_one_sandbox_and_pvc_across_all_scopes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two members in a 'workspace'-mode workspace share one UserSandbox & PVC everywhere."""
    app = _make_memory_test_app()
    app.state.deployment_mode = "multi_tenant"
    eng, maker = _make_session_maker()

    try:
        async with maker() as session:
            suffix = secrets.token_hex(4)
            org = await OrganizationRepository(session).create(
                name=f"Collab Org {suffix}", slug=_slugify_org_name(f"collab-{suffix}")
            )
            ws = await WorkspaceRepository(session).create(
                org_id=org.id, name="Collab Shared WS", sandbox_mode="workspace"
            )
            admin_user, admin_pw = await _create_org_user(
                session, org_id=org.id, org_role=OrgRole.OWNER
            )
            member_user, member_pw = await _create_org_user(
                session, org_id=org.id, org_role=OrgRole.MEMBER
            )
            mem_repo = MembershipRepository(session)
            await mem_repo.grant(user_id=admin_user.id, workspace_id=ws.id, role=Role.ADMIN)
            await mem_repo.grant(user_id=member_user.id, workspace_id=ws.id, role=Role.MEMBER)
            await session.commit()

        async with _lifespan_context(app):
            local_provider = LocalSandboxProvider(base_workdir=str(tmp_path))
            registry = ProviderRegistry({"local": local_provider}, default_name="local")
            manager = get_sandbox_manager()
            monkeypatch.setattr(manager, "_registry", registry)
            monkeypatch.setattr(manager, "_session_factory", maker)

            transport = httpx.ASGITransport(app=app)
            async with (
                httpx.AsyncClient(transport=transport, base_url="http://test") as admin_client,
                httpx.AsyncClient(transport=transport, base_url="http://test") as member_client,
            ):
                await _login_and_attach(admin_client, admin_user.email, admin_pw)
                await _login_and_attach(member_client, member_user.email, member_pw)

                # 1. Admin creates personal 1:1 conv and writes file via LazySandbox
                c_a = await admin_client.post(f"/api/v1/ws/{ws.id}/conversations")
                assert c_a.status_code == 201, c_a.text
                conv_admin_id = c_a.json()["id"]

                catalog = MagicMock()
                catalog.list_enabled_for_workspace = AsyncMock(return_value=[])

                ctx_admin = SimpleNamespace(
                    org_id=org.id,
                    workspace_id=ws.id,
                    user_id=admin_user.id,
                    conversation_id=conv_admin_id,
                    topic_id=None,
                    is_group_chat=False,
                    conversation_creator_user_id=admin_user.id,
                    topic_creator_user_id=None,
                    sandbox_mode=None,
                )
                lazy_admin = await RunManager._build_lazy_sandbox(
                    ctx_admin,  # type: ignore[arg-type]
                    skill_catalog=catalog,
                )
                assert lazy_admin.owner_user_id is None
                expected_pvc = build_sandbox_pvc_name(
                    manager._volume_pvc_prefix, ws.id, "workspace", ws.id
                )
                assert lazy_admin.workdir == str(tmp_path / expected_pvc)

                res_a = await lazy_admin.execute("printf 'from-admin' > shared_note.txt")
                assert res_a.exit_code == 0
                shared_sbx_row_id = lazy_admin.user_sandbox_id
                assert shared_sbx_row_id is not None

                # 2. Member creates their own separate 1:1 conv -> attaches to SAME sandbox & PVC
                c_b = await member_client.post(f"/api/v1/ws/{ws.id}/conversations")
                assert c_b.status_code == 201, c_b.text
                conv_member_id = c_b.json()["id"]

                ctx_member = SimpleNamespace(
                    org_id=org.id,
                    workspace_id=ws.id,
                    user_id=member_user.id,
                    conversation_id=conv_member_id,
                    topic_id=None,
                    is_group_chat=False,
                    conversation_creator_user_id=member_user.id,
                    topic_creator_user_id=None,
                    sandbox_mode=None,
                )
                lazy_member = await RunManager._build_lazy_sandbox(
                    ctx_member,  # type: ignore[arg-type]
                    skill_catalog=catalog,
                )
                assert lazy_member.owner_user_id is None
                res_b = await lazy_member.execute("cat shared_note.txt")
                assert res_b.exit_code == 0
                assert res_b.output == "from-admin"
                assert lazy_member.user_sandbox_id == shared_sbx_row_id

                # 3. Non-participant probing Admin's private 1:1 conv still gets 404!
                probe = await member_client.get(
                    f"/api/v1/ws/{ws.id}/sandbox/status",
                    params={"conversation_id": conv_admin_id},
                )
                assert probe.status_code == 404

                # Member reading workspace panel / their own conv sees shared file
                file_read = await member_client.get(
                    f"/api/v1/ws/{ws.id}/sandbox/files/content",
                    params={
                        "path": f"{lazy_member.workdir}/shared_note.txt",
                        "conversation_id": conv_member_id,
                    },
                )
                assert file_read.status_code == 200, file_read.text
                assert file_read.json()["content"] == "from-admin"

                # 4. Inviting Member to Admin's 1:1 -> group chat does NOT rekey shared row
                inv = await admin_client.post(
                    f"/api/v1/ws/{ws.id}/conversations/{conv_admin_id}/invite-to-group",
                    json={"user_ids": [member_user.id]},
                )
                assert inv.status_code in (200, 201), inv.text
                assert inv.json()["conversation"]["is_group_chat"] is True

                async with maker() as session:
                    sbx_row = await session.get(UserSandbox, shared_sbx_row_id)
                    assert sbx_row is not None
                    assert sbx_row.scope_type == "workspace"
                    assert sbx_row.scope_id == ws.id
                    assert sbx_row.user_id is None

                # 5. Upgrading group chat to topic forces topic.sandbox_mode=None & skips rekey
                upg = await admin_client.post(
                    f"/api/v1/ws/{ws.id}/conversations/{conv_admin_id}/upgrade-to-topic",
                    json={
                        "title": "Upgraded Shared Topic",
                        "sandbox_mode": "dedicated",
                        "member_user_ids": [member_user.id],
                    },
                )
                assert upg.status_code == 201, upg.text
                assert upg.json()["topic"]["sandbox_mode"] is None
                upgraded_topic_id = upg.json()["topic"]["id"]

                # 6. Creating a new topic also normalizes sandbox_mode=None & resolves shared sbx
                top_create = await admin_client.post(
                    f"/api/v1/ws/{ws.id}/topics",
                    json={
                        "title": "Direct Shared Topic",
                        "sandbox_mode": "dedicated",
                        "member_user_ids": [member_user.id],
                    },
                )
                assert top_create.status_code == 201, top_create.text
                assert top_create.json()["topic"]["sandbox_mode"] is None
                direct_topic_id = top_create.json()["topic"]["id"]
                direct_topic_conv_id = top_create.json()["conversation"]["id"]

                top_sbx_status = await member_client.get(
                    f"/api/v1/ws/{ws.id}/topics/{direct_topic_id}/sandbox"
                )
                assert top_sbx_status.status_code == 200, top_sbx_status.text
                assert top_sbx_status.json()["status"] == "running"

                ctx_topic = SimpleNamespace(
                    org_id=org.id,
                    workspace_id=ws.id,
                    user_id=member_user.id,
                    conversation_id=direct_topic_conv_id,
                    topic_id=direct_topic_id,
                    is_group_chat=False,
                    conversation_creator_user_id=admin_user.id,
                    topic_creator_user_id=admin_user.id,
                    sandbox_mode=None,
                )
                lazy_topic = await RunManager._build_lazy_sandbox(
                    ctx_topic,  # type: ignore[arg-type]
                    skill_catalog=catalog,
                )
                await lazy_topic.ensure_created()
                assert lazy_topic.user_sandbox_id == shared_sbx_row_id

                # Verify still only ONE UserSandbox row in this workspace
                async with maker() as session:
                    all_rows = list(
                        (
                            await session.execute(
                                select(UserSandbox).where(
                                    UserSandbox.workspace_id == ws.id  # type: ignore[arg-type]
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert len(all_rows) == 1
                    assert all_rows[0].scope_type == "workspace"
                    assert all_rows[0].scope_id == ws.id
                    assert all_rows[0].user_id is None

                    # 7. BackgroundTaskService._validate_sandbox_scope accepts ('workspace', ws, None)
                    bg_svc = BackgroundTaskService(session, org_id=org.id, workspace_id=ws.id)
                    for cid in (conv_member_id, conv_admin_id, direct_topic_conv_id):
                        conv_obj = await session.get(Conversation, cid)
                        assert conv_obj is not None
                        await bg_svc._validate_sandbox_scope(conv_obj, all_rows[0], member_user.id)
                    assert upgraded_topic_id is not None

                await lazy_admin.close()
                await lazy_member.close()
                await lazy_topic.close()
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_concurrent_first_run_provisioning_race_on_shared_workspace() -> None:
    """Concurrent get_or_create calls on a shared workspace coalesce onto one container."""
    eng, maker = _make_session_maker()
    try:
        async with maker() as session:
            suffix = secrets.token_hex(4)
            org = await OrganizationRepository(session).create(
                name=f"Race Org {suffix}", slug=f"race-org-{suffix}"
            )
            ws = await WorkspaceRepository(session).create(
                org_id=org.id, name="Race Shared WS", sandbox_mode="workspace"
            )

        gate = asyncio.Event()
        provider = _ResettableEgressProvider(create_gate=gate)
        registry = ProviderRegistry({"resettable": provider}, default_name="resettable")
        enc = FernetBackend([Fernet.generate_key()])
        mgr = SandboxManager(maker, enc, registry=registry)
        mgr._reserve_poll_interval = 0.05
        mgr._reserve_wait_timeout = 5.0

        # Force the second caller into the record-is-None -> repo.reserve IntegrityError
        # path deterministically by starting caller 1 until provider.create is entered,
        # and having caller 2's initial get_active_by_scope return None once before reserve.
        t1 = asyncio.create_task(
            mgr.get_or_create(
                scope_type="workspace",
                scope_id=ws.id,
                user_id=None,
                org_id=org.id,
                workspace_id=ws.id,
            )
        )
        await asyncio.wait_for(provider.first_create_entered.wait(), timeout=5.0)

        orig_get_active = UserSandboxRepository.get_active_by_scope
        first_call = True
        entered_winner_poll = asyncio.Event()

        async def _stale_first_lookup(
            self_repo: UserSandboxRepository, *, scope_type: str, scope_id: str
        ) -> UserSandbox | None:
            nonlocal first_call
            if first_call:
                first_call = False
                return None
            entered_winner_poll.set()
            return await orig_get_active(self_repo, scope_type=scope_type, scope_id=scope_id)

        UserSandboxRepository.get_active_by_scope = _stale_first_lookup  # type: ignore[method-assign]
        try:
            t2 = asyncio.create_task(
                mgr.get_or_create(
                    scope_type="workspace",
                    scope_id=ws.id,
                    user_id=None,
                    org_id=org.id,
                    workspace_id=ws.id,
                )
            )
            # Wait deterministically until t2 hits IntegrityError on reserve, rolls back,
            # and enters _await_provisioning_winner before releasing winner's provider.create.
            await asyncio.wait_for(entered_winner_poll.wait(), timeout=5.0)
            gate.set()
            att1, att2 = await asyncio.wait_for(asyncio.gather(t1, t2), timeout=5.0)
        finally:
            UserSandboxRepository.get_active_by_scope = orig_get_active  # type: ignore[method-assign]

        assert len(provider.created_specs) == 1
        assert att1.user_sandbox_id == att2.user_sandbox_id
        assert att1.sandbox.id == att2.sandbox.id
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_shared_sandbox_rbac_and_admin_observability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Members view shared sandbox (can_manage=False) & 403 on mutations; admins succeed."""
    app = _make_memory_test_app()
    app.state.deployment_mode = "multi_tenant"
    eng, maker = _make_session_maker()

    try:
        async with maker() as session:
            suffix = secrets.token_hex(4)
            org = await OrganizationRepository(session).create(
                name=f"RBAC Org {suffix}", slug=f"rbac-org-{suffix}"
            )
            ws = await WorkspaceRepository(session).create(
                org_id=org.id, name="RBAC Shared WS", sandbox_mode="workspace"
            )
            admin_user, admin_pw = await _create_org_user(
                session, org_id=org.id, org_role=OrgRole.OWNER
            )
            member_user, member_pw = await _create_org_user(
                session, org_id=org.id, org_role=OrgRole.MEMBER
            )
            mem_repo = MembershipRepository(session)
            await mem_repo.grant(user_id=admin_user.id, workspace_id=ws.id, role=Role.ADMIN)
            await mem_repo.grant(user_id=member_user.id, workspace_id=ws.id, role=Role.MEMBER)

            # Private 1:1 conv owned by admin to verify 404 precedes 403 on reset-rootfs
            priv_conv = Conversation(
                org_id=org.id,
                workspace_id=ws.id,
                creator_user_id=admin_user.id,
                title="Admin Private",
            )
            session.add(priv_conv)
            await session.commit()
            priv_conv_id = priv_conv.id

        async with _lifespan_context(app):
            provider = _ResettableEgressProvider()
            registry = ProviderRegistry({"resettable": provider}, default_name="resettable")
            manager = get_sandbox_manager()
            monkeypatch.setattr(manager, "_registry", registry)
            monkeypatch.setattr(manager, "_session_factory", maker)

            transport = httpx.ASGITransport(app=app)
            async with (
                httpx.AsyncClient(transport=transport, base_url="http://test") as admin_client,
                httpx.AsyncClient(transport=transport, base_url="http://test") as member_client,
            ):
                await _login_and_attach(admin_client, admin_user.email, admin_pw)
                await _login_and_attach(member_client, member_user.email, member_pw)

                # Before any container exists:
                # - Member reset-rootfs with unauthorized conversation_id -> 404 (no leak)
                r_unauth_conv = await member_client.post(
                    f"/api/v1/ws/{ws.id}/sandbox/reset-rootfs",
                    params={"conversation_id": priv_conv_id},
                )
                assert r_unauth_conv.status_code == 404

                # - Member reset-rootfs without conversation_id -> 403 (even before active row!)
                r_member_reset_empty = await member_client.post(
                    f"/api/v1/ws/{ws.id}/sandbox/reset-rootfs"
                )
                assert r_member_reset_empty.status_code == 403
                assert r_member_reset_empty.json()["detail"] == "workspace admin required"

                # Provision shared sandbox via Member exec (daily collaboration allowed)
                exec_r = await member_client.post(
                    f"/api/v1/ws/{ws.id}/sandbox/exec",
                    json={"command": "echo hi"},
                )
                assert exec_r.status_code == 200, exec_r.text

                # GET /ws/{ws}/sandboxes: member gets can_manage=False, admin gets can_manage=True
                m_list = await member_client.get(f"/api/v1/ws/{ws.id}/sandboxes")
                assert m_list.status_code == 200, m_list.text
                m_items = m_list.json()
                assert len(m_items) == 1
                sbx_id = m_items[0]["id"]
                assert m_items[0]["scope_type"] == "workspace"
                assert m_items[0]["scope_id"] == ws.id
                assert m_items[0]["can_manage"] is False

                a_list = await admin_client.get(f"/api/v1/ws/{ws.id}/sandboxes")
                assert a_list.status_code == 200, a_list.text
                a_items = a_list.json()
                assert len(a_items) == 1
                assert a_items[0]["id"] == sbx_id
                assert a_items[0]["can_manage"] is True

                # Admin observability (/api/v1/admin/sandboxes) serializes user_id=None cleanly
                adm_list = await admin_client.get("/api/v1/admin/sandboxes")
                assert adm_list.status_code == 200, adm_list.text
                adm_row = next(s for s in adm_list.json() if s["id"] == sbx_id)
                assert adm_row["user_id"] is None
                assert adm_row["scope_type"] == "workspace"

                adm_one = await admin_client.get(f"/api/v1/admin/sandboxes/{sbx_id}")
                assert adm_one.status_code == 200, adm_one.text
                assert adm_one.json()["user_id"] is None

                # Member gets 403 on reset-rootfs, restart, and delete
                assert (
                    await member_client.post(f"/api/v1/ws/{ws.id}/sandbox/reset-rootfs")
                ).status_code == 403
                assert (
                    await member_client.post(f"/api/v1/ws/{ws.id}/sandboxes/{sbx_id}/restart")
                ).status_code == 403
                assert (
                    await member_client.delete(f"/api/v1/ws/{ws.id}/sandboxes/{sbx_id}")
                ).status_code == 403

                # Admin succeeds on reset-rootfs (200), restart (202), and delete (202)
                adm_reset = await admin_client.post(f"/api/v1/ws/{ws.id}/sandbox/reset-rootfs")
                assert adm_reset.status_code == 200, adm_reset.text
                assert len(provider.reset_calls) == 1

                adm_restart = await admin_client.post(
                    f"/api/v1/ws/{ws.id}/sandboxes/{sbx_id}/restart"
                )
                assert adm_restart.status_code == 202, adm_restart.text

                adm_del = await admin_client.delete(f"/api/v1/ws/{ws.id}/sandboxes/{sbx_id}")
                assert adm_del.status_code == 202, adm_del.text

                after_del = await admin_client.get(f"/api/v1/ws/{ws.id}/sandboxes")
                assert after_del.status_code == 200
                assert after_del.json() == []
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_creator_account_deletion_and_workspace_deletion_with_shared_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting the workspace creator's account leaves shared UserSandbox & EgressRefs intact;
    deleting the workspace removes both cleanly (note P2-4: creator owns no Topic rows)."""
    app = _make_memory_test_app()
    app.state.deployment_mode = "multi_tenant"
    eng, maker = _make_session_maker()

    try:
        async with maker() as session:
            suffix = secrets.token_hex(4)
            org = await OrganizationRepository(session).create(
                name=f"Del Org {suffix}", slug=f"del-org-{suffix}"
            )
            shared_ws = await WorkspaceRepository(session).create(
                org_id=org.id, name="Survivor Shared WS", sandbox_mode="workspace"
            )
            spare_ws = await WorkspaceRepository(session).create(
                org_id=org.id, name="Spare WS", sandbox_mode="per_user"
            )
            # Creator starts as MEMBER at org level (so we transfer ownership cleanly to survivor)
            creator_user, creator_pw = await _create_org_user(
                session, org_id=org.id, org_role=OrgRole.MEMBER
            )
            survivor_user, survivor_pw = await _create_org_user(
                session, org_id=org.id, org_role=OrgRole.OWNER
            )
            mem_repo = MembershipRepository(session)
            await mem_repo.grant(
                user_id=creator_user.id, workspace_id=shared_ws.id, role=Role.ADMIN
            )
            await mem_repo.grant(
                user_id=survivor_user.id, workspace_id=shared_ws.id, role=Role.ADMIN
            )
            await mem_repo.grant(
                user_id=survivor_user.id, workspace_id=spare_ws.id, role=Role.ADMIN
            )
            await session.commit()

        async with _lifespan_context(app):
            provider = _ResettableEgressProvider()
            registry = ProviderRegistry({"resettable": provider}, default_name="resettable")
            manager = get_sandbox_manager()
            monkeypatch.setattr(manager, "_registry", registry)
            monkeypatch.setattr(manager, "_session_factory", maker)
            monkeypatch.setattr(manager, "_exchange_host", "egress.internal:9443")

            transport = httpx.ASGITransport(app=app)
            async with (
                httpx.AsyncClient(transport=transport, base_url="http://test") as creator_client,
                httpx.AsyncClient(transport=transport, base_url="http://test") as survivor_client,
            ):
                await _login_and_attach(creator_client, creator_user.email, creator_pw)
                await _login_and_attach(survivor_client, survivor_user.email, survivor_pw)

                # Seed a workspace secret env var so provisioning mints an EgressRef(user_id=None)
                env_r = await creator_client.post(
                    f"/api/v1/ws/{shared_ws.id}/sandbox-env/workspace",
                    json={
                        "env_name": "SHARED_API_TOKEN",
                        "is_secret": True,
                        "hosts": ["api.example.com"],
                        "secret_value": "sk-shared-secret",
                    },
                )
                assert env_r.status_code == 201, env_r.text

                # Creator provisions the shared workspace sandbox
                exec_r = await creator_client.post(
                    f"/api/v1/ws/{shared_ws.id}/sandbox/exec",
                    json={"command": "echo bootstrap"},
                )
                assert exec_r.status_code == 200, exec_r.text

                async with maker() as session:
                    sbx_row = (
                        await session.execute(
                            select(UserSandbox).where(
                                UserSandbox.workspace_id == shared_ws.id  # type: ignore[arg-type]
                            )
                        )
                    ).scalar_one()
                    assert sbx_row.user_id is None
                    assert sbx_row.scope_type == "workspace"
                    refs = list(
                        (
                            await session.execute(
                                select(EgressRef).where(
                                    EgressRef.workspace_id == shared_ws.id  # type: ignore[arg-type]
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert len(refs) == 1
                    assert refs[0].user_id is None
                    assert refs[0].status == "valid"

                # Creator deletes their user account (no creator-owned Topic rows exist)
                del_acct = await creator_client.post(
                    "/api/v1/auth/delete-account",
                    json={"password": creator_pw},
                )
                assert del_acct.status_code == 200, del_acct.text
                assert del_acct.json()["deleted"] is True

                # Shared UserSandbox and EgressRef survive untouched!
                async with maker() as session:
                    assert await session.get(User, creator_user.id) is None
                    surviving_sbx = await session.get(UserSandbox, sbx_row.id)
                    assert surviving_sbx is not None
                    assert surviving_sbx.status == "running"
                    assert surviving_sbx.user_id is None
                    surviving_ref = await session.get(EgressRef, refs[0].id)
                    assert surviving_ref is not None
                    assert surviving_ref.status == "valid"
                    assert surviving_ref.user_id is None

                # Survivor still sees and uses the shared sandbox
                s_list = await survivor_client.get(f"/api/v1/ws/{shared_ws.id}/sandboxes")
                assert s_list.status_code == 200
                assert len(s_list.json()) == 1

                # Deleting the workspace removes both UserSandbox and EgressRef cleanly
                del_ws = await survivor_client.delete(f"/api/v1/workspaces/{shared_ws.id}")
                assert del_ws.status_code == 200, del_ws.text
                assert del_ws.json()["deleted"] is True

                async with maker() as session:
                    assert await session.get(UserSandbox, sbx_row.id) is None
                    assert await session.get(EgressRef, refs[0].id) is None
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_sandbox_env_vault_and_im_settings_in_shared_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In 'workspace' mode: /me env disabled, /workspace readable by members, runtime &
    sandbox_config exclude user-scope vars, and shared IM settings normalize sandbox_mode=None."""
    app = _make_memory_test_app()
    app.state.deployment_mode = "multi_tenant"
    eng, maker = _make_session_maker()

    try:
        async with maker() as session:
            suffix = secrets.token_hex(4)
            org = await OrganizationRepository(session).create(
                name=f"Env Org {suffix}", slug=f"env-org-{suffix}"
            )
            shared_ws = await WorkspaceRepository(session).create(
                org_id=org.id, name="Env Shared WS", sandbox_mode="workspace"
            )
            isolated_ws = await WorkspaceRepository(session).create(
                org_id=org.id, name="Env Isolated WS", sandbox_mode="per_user"
            )
            admin_user, admin_pw = await _create_org_user(
                session, org_id=org.id, org_role=OrgRole.OWNER
            )
            member_user, member_pw = await _create_org_user(
                session, org_id=org.id, org_role=OrgRole.MEMBER
            )
            mem_repo = MembershipRepository(session)
            for target_ws in (shared_ws, isolated_ws):
                await mem_repo.grant(
                    user_id=admin_user.id, workspace_id=target_ws.id, role=Role.ADMIN
                )
                await mem_repo.grant(
                    user_id=member_user.id, workspace_id=target_ws.id, role=Role.MEMBER
                )

            # Seed an IM account in shared_ws to test PUT /im/accounts/{id}/settings
            im_cred_id = await CredentialService(
                CredentialRepository(session, org_id=org.id),
                FernetBackend([Fernet.generate_key()]),
                org_id=org.id,
                actor_user_id=admin_user.id,
            ).create(kind="im_feishu", name=f"im_feishu:{suffix}", plaintext="{}")
            im_account = IMConnectorAccount(
                org_id=org.id,
                workspace_id=shared_ws.id,
                platform="feishu",
                external_account_id=f"cli_{suffix}",
                acting_user_id=admin_user.id,
                credential_id=im_cred_id,
                delivery_mode="webhook",
                enabled=True,
                config={"bot_app_name": "SharedBot"},
            )
            session.add(im_account)
            await session.commit()
            im_account_id = im_account.id

        async with _lifespan_context(app):
            provider = _ResettableEgressProvider()
            registry = ProviderRegistry({"resettable": provider}, default_name="resettable")
            manager = get_sandbox_manager()
            monkeypatch.setattr(manager, "_registry", registry)
            monkeypatch.setattr(manager, "_session_factory", maker)
            monkeypatch.setattr(manager, "_exchange_host", "egress.internal:9443")

            transport = httpx.ASGITransport(app=app)
            async with (
                httpx.AsyncClient(transport=transport, base_url="http://test") as admin_client,
                httpx.AsyncClient(transport=transport, base_url="http://test") as member_client,
            ):
                await _login_and_attach(admin_client, admin_user.email, admin_pw)
                await _login_and_attach(member_client, member_user.email, member_pw)

                # Seed org-scope + workspace-scope vars via API, plus a DB-level user-scope var
                org_env_name = f"ORG_DEFAULT_{suffix.upper()}"
                org_env = await admin_client.post(
                    "/api/v1/admin/sandbox-env",
                    json={
                        "env_name": org_env_name,
                        "is_secret": False,
                        "secret_value": "from-org",
                    },
                )
                assert org_env.status_code == 201, org_env.text
                org_env_id = org_env.json()["id"]

                ws_env = await admin_client.post(
                    f"/api/v1/ws/{shared_ws.id}/sandbox-env/workspace",
                    json={
                        "env_name": "WS_SHARED_SECRET",
                        "is_secret": True,
                        "hosts": ["api.example.com"],
                        "secret_value": "shh-ws",
                    },
                )
                assert ws_env.status_code == 201, ws_env.text
                ws_env_id = ws_env.json()["id"]

                # Direct DB seed of a user-scope SandboxEnvVar to prove /me GET & runtime ignore it
                async with maker() as session:
                    enc_backend = app.state.encryption_backend
                    svc = SandboxEnvService(
                        repo=SandboxEnvRepository(session, org_id=org.id),
                        credentials=CredentialService(
                            CredentialRepository(session, org_id=org.id),
                            enc_backend,
                            org_id=org.id,
                            actor_user_id=member_user.id,
                        ),
                        org_id=org.id,
                        actor_user_id=member_user.id,
                    )
                    seeded_user_entry_id = await svc.create_entry(
                        env_name="USER_LEAK_PROBE",
                        is_secret=False,
                        scope="user",
                        workspace_id=shared_ws.id,
                        user_id=member_user.id,
                        hosts=None,
                        header_names=None,
                        secret_value="should-never-resolve",
                    )

                # 1. /me endpoints on shared_ws: GET -> [], POST/PATCH/DELETE -> 400
                me_get = await member_client.get(f"/api/v1/ws/{shared_ws.id}/sandbox-env/me")
                assert me_get.status_code == 200
                assert me_get.json() == {"entries": []}

                me_post = await member_client.post(
                    f"/api/v1/ws/{shared_ws.id}/sandbox-env/me",
                    json={"env_name": "MY_VAR", "is_secret": False, "secret_value": "val"},
                )
                assert me_post.status_code == 400
                assert "disabled in shared-sandbox workspaces" in me_post.json()["detail"]

                for target_entry_id in (seeded_user_entry_id, "sbe-nonexistent"):
                    me_patch = await member_client.patch(
                        f"/api/v1/ws/{shared_ws.id}/sandbox-env/me/{target_entry_id}",
                        json={"secret_value": "new"},
                    )
                    assert me_patch.status_code == 400
                    me_del = await member_client.delete(
                        f"/api/v1/ws/{shared_ws.id}/sandbox-env/me/{target_entry_id}"
                    )
                    assert me_del.status_code == 400

                # 2. /workspace endpoints: member GET -> 200 in shared_ws, 403 in isolated_ws
                m_ws_list = await member_client.get(
                    f"/api/v1/ws/{shared_ws.id}/sandbox-env/workspace"
                )
                assert m_ws_list.status_code == 200, m_ws_list.text
                entries = m_ws_list.json()["entries"]
                assert [e["env_name"] for e in entries] == ["WS_SHARED_SECRET"]
                assert "secret_value" not in entries[0]
                assert "credential_id" not in entries[0]

                m_iso_list = await member_client.get(
                    f"/api/v1/ws/{isolated_ws.id}/sandbox-env/workspace"
                )
                assert m_iso_list.status_code == 403

                # Member mutations on /workspace remain 403 even in shared_ws
                assert (
                    await member_client.post(
                        f"/api/v1/ws/{shared_ws.id}/sandbox-env/workspace",
                        json={"env_name": "NOPE", "is_secret": False, "secret_value": "x"},
                    )
                ).status_code == 403
                assert (
                    await member_client.patch(
                        f"/api/v1/ws/{shared_ws.id}/sandbox-env/workspace/{ws_env_id}",
                        json={"secret_value": "x"},
                    )
                ).status_code == 403
                assert (
                    await member_client.delete(
                        f"/api/v1/ws/{shared_ws.id}/sandbox-env/workspace/{ws_env_id}"
                    )
                ).status_code == 403

                # 3. Runtime SandboxManager + agent sandbox_config loader exclude user-scope vars
                att = await manager.get_or_create(
                    scope_type="workspace",
                    scope_id=shared_ws.id,
                    user_id=member_user.id,  # even if caller passes user_id, scope='workspace' wins
                    org_id=org.id,
                    workspace_id=shared_ws.id,
                )
                sbx_inst = att.sandbox
                assert isinstance(sbx_inst, _ResettableEgressSandbox)
                assert sbx_inst.run_env.get(org_env_name) == "from-org"
                assert "WS_SHARED_SECRET" in sbx_inst.run_env
                assert "USER_LEAK_PROBE" not in sbx_inst.run_env

                loader = make_session_loader(
                    session_factory=maker,
                    org_id=org.id,
                    workspace_id=shared_ws.id,
                    user_id=None,
                    default_image="ubuntu:22.04",
                    egress_proxy_supported=True,
                )
                view = await loader()
                env_names = {item["env_name"] for item in view["env"]}
                assert env_names == {org_env_name, "WS_SHARED_SECRET"}

                async with maker() as session:
                    direct_view = await load_agent_view(
                        session,
                        org_id=org.id,
                        workspace_id=shared_ws.id,
                        user_id=None,
                        default_image="ubuntu:22.04",
                        egress_proxy_supported=True,
                    )
                    assert {i["env_name"] for i in direct_view["env"]} == {
                        org_env_name,
                        "WS_SHARED_SECRET",
                    }
                    # Clean up seeded user env row + backing credential
                    cleanup_svc = SandboxEnvService(
                        repo=SandboxEnvRepository(session, org_id=org.id),
                        credentials=CredentialService(
                            CredentialRepository(session, org_id=org.id),
                            enc_backend,
                            org_id=org.id,
                            actor_user_id=member_user.id,
                        ),
                        org_id=org.id,
                        actor_user_id=member_user.id,
                    )
                    await cleanup_svc.delete_entry(entry_id=seeded_user_entry_id)

                await admin_client.delete(f"/api/v1/admin/sandbox-env/{org_env_id}")

                # 4. IM bot settings in shared_ws allow routing_mode='shared' without sandbox_mode
                im_put = await admin_client.put(
                    f"/api/v1/ws/{shared_ws.id}/im/accounts/{im_account_id}/settings",
                    json={"routing_mode": "shared", "topic_mode": "topic"},
                )
                assert im_put.status_code == 200, im_put.text
                assert im_put.json() == {
                    "routing_mode": "shared",
                    "topic_mode": "topic",
                    "sandbox_mode": None,
                }

                # Even if client sends sandbox_mode='dedicated', shared_ws normalizes to None
                im_put2 = await admin_client.put(
                    f"/api/v1/ws/{shared_ws.id}/im/accounts/{im_account_id}/settings",
                    json={
                        "routing_mode": "shared",
                        "topic_mode": "topic",
                        "sandbox_mode": "dedicated",
                    },
                )
                assert im_put2.status_code == 200, im_put2.text
                assert im_put2.json()["sandbox_mode"] is None

                async with maker() as session:
                    acct = await session.get(IMConnectorAccount, im_account_id)
                    assert acct is not None
                    resolved_im = await resolve_im_conversation(
                        session,
                        acct,
                        channel_id="oc_group_1",
                        scope_key="group:oc_group_1",
                        scope_kind="group",
                        effective_user_id=member_user.id,
                        title_hint="Hello team",
                        origin="inbound",
                        channel_name="Team Channel",
                    )
                    await session.commit()
                    assert resolved_im.sandbox_mode is None
                    assert resolved_im.topic_id is not None
                    im_topic = await session.get(Topic, resolved_im.topic_id)
                    assert im_topic is not None
                    assert im_topic.sandbox_mode is None
    finally:
        await eng.dispose()
