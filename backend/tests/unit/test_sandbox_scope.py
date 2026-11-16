"""Pure unit tests for workspace sandbox mode resolution, PVC naming, and invariants."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cubeplex.repositories.sandbox_env import SandboxEnvRepository
from cubeplex.repositories.user_sandbox import UserSandboxRepository
from cubeplex.repositories.workspace import WorkspaceRepository
from cubeplex.sandbox.base import SandboxCapabilities
from cubeplex.sandbox.lazy import LazySandbox
from cubeplex.sandbox.manager import build_sandbox_pvc_name
from cubeplex.sandbox.scope import (
    WORKSPACE_SANDBOX_MODES,
    ResolvedSandboxTarget,
    get_workspace_sandbox_mode,
    resolve_sandbox_target_pure,
)
from cubeplex.services.sandbox_env import SandboxEnvResolver
from cubeplex.services.sandbox_runtime_config import resolve_env_inventory


def test_workspace_sandbox_modes_constant() -> None:
    assert WORKSPACE_SANDBOX_MODES == ("per_user", "workspace")


def test_resolve_sandbox_target_pure_per_user_modes() -> None:
    # 1. No conversation -> caller's personal sandbox
    assert resolve_sandbox_target_pure(
        workspace_id="ws-1",
        workspace_sandbox_mode="per_user",
        caller_user_id="usr-caller",
    ) == ResolvedSandboxTarget(
        scope_type="user",
        scope_id="usr-caller",
        owner_user_id="usr-caller",
        workspace_sandbox_mode="per_user",
    )

    # 2. 1:1 personal conversation -> caller's personal sandbox
    assert resolve_sandbox_target_pure(
        workspace_id="ws-1",
        workspace_sandbox_mode="per_user",
        caller_user_id="usr-caller",
        conversation_id="conv-1",
        is_group_chat=False,
        conversation_creator_user_id="usr-caller",
    ) == ResolvedSandboxTarget(
        scope_type="user",
        scope_id="usr-caller",
        owner_user_id="usr-caller",
        workspace_sandbox_mode="per_user",
    )

    # 3. Standalone group chat -> conversation-scoped, owned by conv creator
    assert resolve_sandbox_target_pure(
        workspace_id="ws-1",
        workspace_sandbox_mode="per_user",
        caller_user_id="usr-member",
        conversation_id="conv-group",
        is_group_chat=True,
        conversation_creator_user_id="usr-creator",
    ) == ResolvedSandboxTarget(
        scope_type="conversation",
        scope_id="conv-group",
        owner_user_id="usr-creator",
        workspace_sandbox_mode="per_user",
    )

    # 4. Dedicated topic -> topic-scoped, owned by topic creator
    assert resolve_sandbox_target_pure(
        workspace_id="ws-1",
        workspace_sandbox_mode="per_user",
        caller_user_id="usr-member",
        conversation_id="conv-topic",
        topic_id="top-1",
        topic_sandbox_mode="dedicated",
        topic_creator_user_id="usr-top-owner",
    ) == ResolvedSandboxTarget(
        scope_type="topic",
        scope_id="top-1",
        owner_user_id="usr-top-owner",
        workspace_sandbox_mode="per_user",
    )

    # 5. Creator-mode (or unspecified) topic -> topic creator's personal sandbox
    for t_mode in ("creator", None):
        assert resolve_sandbox_target_pure(
            workspace_id="ws-1",
            workspace_sandbox_mode="per_user",
            caller_user_id="usr-member",
            conversation_id="conv-topic",
            topic_id="top-1",
            topic_sandbox_mode=t_mode,
            topic_creator_user_id="usr-top-owner",
        ) == ResolvedSandboxTarget(
            scope_type="user",
            scope_id="usr-top-owner",
            owner_user_id="usr-top-owner",
            workspace_sandbox_mode="per_user",
        )


@pytest.mark.parametrize(
    ("kwargs"),
    [
        {},
        {"conversation_id": "conv-1", "is_group_chat": False},
        {
            "conversation_id": "conv-group",
            "is_group_chat": True,
            "conversation_creator_user_id": "usr-creator",
        },
        {
            "conversation_id": "conv-topic",
            "topic_id": "top-1",
            "topic_sandbox_mode": "dedicated",
            "topic_creator_user_id": "usr-top-owner",
        },
        {
            "conversation_id": "conv-topic",
            "topic_id": "top-1",
            "topic_sandbox_mode": "creator",
            "topic_creator_user_id": "usr-top-owner",
        },
    ],
)
def test_resolve_sandbox_target_pure_workspace_mode_always_workspace_scope(
    kwargs: dict[str, object],
) -> None:
    target = resolve_sandbox_target_pure(
        workspace_id="ws-shared",
        workspace_sandbox_mode="workspace",
        caller_user_id="usr-any",
        **kwargs,  # type: ignore[arg-type]
    )
    assert target == ResolvedSandboxTarget(
        scope_type="workspace",
        scope_id="ws-shared",
        owner_user_id=None,
        workspace_sandbox_mode="workspace",
    )


@pytest.mark.asyncio
async def test_get_workspace_sandbox_mode_none_session_and_db_values() -> None:
    assert await get_workspace_sandbox_mode(None, "ws-1") == "per_user"

    session_ws = MagicMock()
    session_ws.scalar = AsyncMock(return_value="workspace")
    assert await get_workspace_sandbox_mode(session_ws, "ws-1") == "workspace"

    session_pu = MagicMock()
    session_pu.scalar = AsyncMock(return_value="per_user")
    assert await get_workspace_sandbox_mode(session_pu, "ws-1") == "per_user"

    session_missing = MagicMock()
    session_missing.scalar = AsyncMock(return_value=None)
    with pytest.raises(LookupError):
        await get_workspace_sandbox_mode(session_missing, "ws-missing")


def test_build_sandbox_pvc_name_for_workspace_scope() -> None:
    ws_id = "ws-abc123"
    pvc = build_sandbox_pvc_name("cubeplex-user", ws_id, "workspace", ws_id)
    assert pvc == "cubeplex-user-ws-ws-abc123-workspace-ws-abc123"
    assert len(pvc) <= 63
    user_pvc = build_sandbox_pvc_name("cubeplex-user", ws_id, "user", "usr-abc123")
    assert pvc != user_pvc


@pytest.mark.asyncio
async def test_workspace_repository_rejects_invalid_sandbox_mode() -> None:
    repo = WorkspaceRepository(MagicMock())
    with pytest.raises(ValueError, match="sandbox_mode"):
        await repo.create(org_id="org-1", name="Bad", sandbox_mode="invalid")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_user_sandbox_repository_validates_scope_and_user_id_pair() -> None:
    repo = UserSandboxRepository(MagicMock(), org_id="org-1", workspace_id="ws-1")

    # workspace scope requires user_id is None
    with pytest.raises(ValueError, match="user_id"):
        await repo.reserve(
            user_id="usr-1",
            image="img:1",
            scope_type="workspace",
            scope_id="ws-1",
        )
    with pytest.raises(ValueError, match="user_id"):
        await repo.create(
            user_id="usr-1",
            scope_type="workspace",
            scope_id="ws-1",
            sandbox_id="sbx-1",
            image="img:1",
        )

    # user / conversation / topic scopes require non-None user_id
    for scope_type in ("user", "conversation", "topic"):
        with pytest.raises(ValueError, match="user_id"):
            await repo.reserve(
                user_id=None,
                image="img:1",
                scope_type=scope_type,
                scope_id="sc-1",
            )
        with pytest.raises(ValueError, match="user_id"):
            await repo.create(
                user_id=None,
                scope_type=scope_type,
                scope_id="sc-1",
                sandbox_id="sbx-1",
                image="img:1",
            )

    # unknown scope_type rejected
    with pytest.raises(ValueError, match="scope_type"):
        await repo.reserve(
            user_id="usr-1",
            image="img:1",
            scope_type="bogus",
            scope_id="sc-1",
        )


def test_lazy_sandbox_exposes_owner_user_id_for_workspace_and_user_scopes() -> None:
    mgr = SimpleNamespace(
        default_provider=SimpleNamespace(
            name="local",
            capabilities=SandboxCapabilities(),
            default_workdir="/workspace",
        )
    )
    shared_lazy = LazySandbox(
        manager=mgr,  # type: ignore[arg-type]
        scope_type="workspace",
        scope_id="ws-1",
        user_id=None,
        org_id="org-1",
        workspace_id="ws-1",
    )
    assert shared_lazy.owner_user_id is None

    user_lazy = LazySandbox(
        manager=mgr,  # type: ignore[arg-type]
        scope_type="user",
        scope_id="usr-1",
        user_id="usr-1",
        org_id="org-1",
        workspace_id="ws-1",
    )
    assert user_lazy.owner_user_id == "usr-1"


@pytest.mark.asyncio
async def test_env_resolution_excludes_user_scope_when_user_id_is_none() -> None:
    rows = [
        SimpleNamespace(
            id="env-org",
            env_name="SHARED_KEY",
            is_secret=False,
            scope="org",
            workspace_id=None,
            user_id=None,
            status="valid",
            hosts=None,
            header_names=None,
            credential_id="cred-org",
        ),
        SimpleNamespace(
            id="env-ws",
            env_name="SHARED_KEY",
            is_secret=False,
            scope="workspace",
            workspace_id="ws-1",
            user_id=None,
            status="valid",
            hosts=None,
            header_names=None,
            credential_id="cred-ws",
        ),
        SimpleNamespace(
            id="env-user-override",
            env_name="SHARED_KEY",
            is_secret=False,
            scope="user",
            workspace_id="ws-1",
            user_id="usr-1",
            status="valid",
            hosts=None,
            header_names=None,
            credential_id="cred-user",
        ),
        SimpleNamespace(
            id="env-user-only",
            env_name="PERSONAL_ONLY",
            is_secret=False,
            scope="user",
            workspace_id="ws-1",
            user_id=None,  # even defensive null row would not match
            status="valid",
            hosts=None,
            header_names=None,
            credential_id="cred-u2",
        ),
    ]
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = rows
    exec_res = MagicMock()
    exec_res.scalars.return_value = scalars_mock
    session = MagicMock()
    session.execute = AsyncMock(return_value=exec_res)

    repo = SandboxEnvRepository(session, org_id="org-1")
    filtered = await repo.list_for_resolution(workspace_id="ws-1", user_id=None)
    assert [r.id for r in filtered] == ["env-org", "env-ws"]

    resolved = await SandboxEnvResolver(repo).resolve(workspace_id="ws-1", user_id=None)
    assert len(resolved) == 1
    assert resolved[0].id == "env-ws"

    inv = await resolve_env_inventory(
        session,
        org_id="org-1",
        workspace_id="ws-1",
        user_id=None,
        egress_proxy_supported=True,
    )
    assert [item.env_name for item in inv] == ["SHARED_KEY"]
    assert inv[0].scope == "workspace"
