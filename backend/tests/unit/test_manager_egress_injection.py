"""Unit test: SandboxManager wires egress env injection into Sandbox.create.

Strategy: real SandboxManager + real in-memory SQLite session + monkeypatched
opensandbox.Sandbox.create (avoids needing a live sandbox cluster). The resolver
is also monkeypatched to return a deterministic secret + plain value without
needing a seeded credential in the DB.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.models import EgressRef
from cubebox.sandbox.manager import SandboxManager
from cubebox.sandbox_env.placeholder import PLACEHOLDER_RE, hash_placeholder, mint_placeholder
from cubebox.services.sandbox_env import ResolvedEnv

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _make_fake_sandbox(sandbox_id: str = "sbx-new") -> Any:
    """Return a minimal fake object matching the attributes read after Sandbox.create."""
    fake = MagicMock()
    fake.id = sandbox_id
    return fake


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_create_with_exchange_host_injects_env_and_persists_refs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """With _exchange_host set, Sandbox.create receives env+policy and an EgressRef is persisted."""
    resolved_envs = [
        ResolvedEnv(
            env_name="GITHUB_TOKEN",
            is_secret=True,
            hosts=["api.github.com"],
            header_names=None,
            credential_id="cred-1",
            plain_value=None,
        ),
        ResolvedEnv(
            env_name="LOG_LEVEL",
            is_secret=False,
            hosts=None,
            header_names=None,
            credential_id=None,
            plain_value="info",
        ),
    ]

    create_kwargs: dict[str, Any] = {}

    async def fake_create(image: str, **kwargs: Any) -> Any:
        create_kwargs.update(kwargs)
        return _make_fake_sandbox("sbx-new")

    manager = SandboxManager(session_factory)
    # Force exchange host (config returns "" by default in tests)
    manager._exchange_host = "egress-exchange.internal"

    with (
        patch("opensandbox.Sandbox.create", side_effect=fake_create),
        patch(
            "cubebox.services.sandbox_env.SandboxEnvResolver.resolve",
            return_value=resolved_envs,
        ),
        # Stub get_active_by_user → None so we always take the create-new path
        patch(
            "cubebox.repositories.user_sandbox.UserSandboxRepository.get_active_by_user",
            return_value=None,
        ),
    ):
        sandbox = await manager.get_or_create(
            "u-1",
            org_id="org-1",
            workspace_id="ws-1",
        )

    # --- Sandbox.create was called with env + network_policy ---
    assert "env" in create_kwargs, "Sandbox.create must receive env="
    env = create_kwargs["env"]
    assert isinstance(env, dict)

    # Secret → cbxref_ placeholder
    github_val = env.get("GITHUB_TOKEN", "")
    assert PLACEHOLDER_RE.fullmatch(github_val), (
        f"GITHUB_TOKEN must be a cbxref_ placeholder, got {github_val!r}"
    )

    # Plain value passes through verbatim
    assert env.get("LOG_LEVEL") == "info"

    # network_policy is non-empty
    policy = create_kwargs.get("network_policy")
    assert policy is not None, "Sandbox.create must receive network_policy="
    assert hasattr(policy, "egress")
    targets = {r.target for r in policy.egress}
    assert "api.github.com" in targets
    assert "egress-exchange.internal" in targets

    # --- EgressRef was persisted in the DB ---
    async with session_factory() as session:
        refs = (await session.execute(select(EgressRef))).scalars().all()

    assert len(refs) == 1, f"Expected 1 EgressRef, got {len(refs)}"
    ref = refs[0]
    assert ref.sandbox_id == "sbx-new"
    assert ref.status == "valid"
    assert ref.expires_at is not None
    assert ref.org_id == "org-1"
    assert ref.workspace_id == "ws-1"
    assert ref.user_id == "u-1"
    assert len(ref.bindings) == 1
    assert ref.bindings[0]["env_name"] == "GITHUB_TOKEN"

    _ = sandbox  # ensure sandbox was returned


async def test_create_without_exchange_host_skips_injection(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """When _exchange_host is empty (egress disabled), Sandbox.create gets NO env/network_policy."""
    create_kwargs: dict[str, Any] = {}

    async def fake_create(image: str, **kwargs: Any) -> Any:
        create_kwargs.update(kwargs)
        return _make_fake_sandbox("sbx-plain")

    manager = SandboxManager(session_factory)
    # _exchange_host defaults to "" from config in tests — be explicit
    manager._exchange_host = ""

    with (
        patch("opensandbox.Sandbox.create", side_effect=fake_create),
        patch(
            "cubebox.repositories.user_sandbox.UserSandboxRepository.get_active_by_user",
            return_value=None,
        ),
    ):
        await manager.get_or_create("u-1", org_id="org-1", workspace_id="ws-1")

    assert "env" not in create_kwargs, "Without exchange_host, env must NOT be passed"
    assert "network_policy" not in create_kwargs, (
        "Without exchange_host, network_policy must NOT be passed"
    )

    # No EgressRef rows persisted
    async with session_factory() as session:
        refs = (await session.execute(select(EgressRef))).scalars().all()
    assert refs == [], f"Expected no EgressRef rows, got {refs}"


async def test_unhealthy_sandbox_revokes_refs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """When an unhealthy sandbox is terminated, its egress refs are revoked."""
    # Seed an EgressRef for the old sandbox
    async with session_factory() as seed_session:
        placeholder = mint_placeholder()
        old_ref = EgressRef(
            ref_hash=hash_placeholder(placeholder),
            sandbox_id="sbx-old",
            org_id="org-1",
            workspace_id="ws-1",
            user_id="u-1",
            run_id=None,
            bindings=[],
            status="valid",
        )
        seed_session.add(old_ref)
        await seed_session.commit()

    resolved_envs: list[ResolvedEnv] = []  # no secrets → no new refs

    async def fake_create(image: str, **kwargs: Any) -> Any:
        return _make_fake_sandbox("sbx-new2")

    # Stub a "running" record for the old sandbox that fails health check
    old_record = MagicMock()
    old_record.id = "rec-old"
    old_record.sandbox_id = "sbx-old"

    async def fake_connect(sandbox_id: str, **kwargs: Any) -> Any:
        fake = MagicMock()

        async def not_healthy() -> bool:
            return False

        # is_healthy returns False so the manager revokes + recreates
        fake.is_healthy = not_healthy
        return fake

    manager = SandboxManager(session_factory)
    manager._exchange_host = "egress-exchange.internal"

    with (
        patch("opensandbox.Sandbox.create", side_effect=fake_create),
        patch("opensandbox.Sandbox.connect", side_effect=fake_connect),
        patch(
            "cubebox.repositories.user_sandbox.UserSandboxRepository.get_active_by_user",
            return_value=old_record,
        ),
        patch(
            "cubebox.repositories.user_sandbox.UserSandboxRepository.mark_terminated",
            return_value=None,
        ),
        patch(
            "cubebox.services.sandbox_env.SandboxEnvResolver.resolve",
            return_value=resolved_envs,
        ),
    ):
        await manager.get_or_create("u-1", org_id="org-1", workspace_id="ws-1")

    # The old ref must now be revoked
    async with session_factory() as check_session:
        refs = (await check_session.execute(select(EgressRef))).scalars().all()

    assert len(refs) == 1
    assert refs[0].sandbox_id == "sbx-old"
    assert refs[0].status == "revoked", f"Expected 'revoked', got {refs[0].status!r}"


async def test_cleanup_expired_revokes_refs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """cleanup_expired must revoke EgressRefs for terminated sandboxes."""
    # Seed an EgressRef for a sandbox that will be "expired"
    async with session_factory() as seed_session:
        placeholder = mint_placeholder()
        expired_ref = EgressRef(
            ref_hash=hash_placeholder(placeholder),
            sandbox_id="sbx-expired",
            org_id="org-1",
            workspace_id="ws-1",
            user_id="u-1",
            run_id=None,
            bindings=[],
            status="valid",
        )
        seed_session.add(expired_ref)
        await seed_session.commit()

    # Build a fake expired record matching what list_expired_system returns
    fake_record = MagicMock()
    fake_record.sandbox_id = "sbx-expired"
    fake_record.org_id = "org-1"
    fake_record.workspace_id = "ws-1"
    fake_record.id = "rec-expired"

    manager = SandboxManager(session_factory)
    manager._exchange_host = "egress-exchange.internal"

    with (
        patch(
            "cubebox.repositories.user_sandbox.UserSandboxRepository.list_expired_system",
            new=AsyncMock(return_value=[fake_record]),
        ),
        patch(
            "cubebox.repositories.user_sandbox.UserSandboxRepository.mark_terminated",
            new=AsyncMock(return_value=None),
        ),
        # Stub sandbox kill so no real network call is made
        patch("opensandbox.Sandbox.connect", side_effect=Exception("no sandbox")),
    ):
        await manager.cleanup_expired()

    # The ref for the expired sandbox must now be revoked
    async with session_factory() as check_session:
        refs = (await check_session.execute(select(EgressRef))).scalars().all()

    assert len(refs) == 1
    assert refs[0].sandbox_id == "sbx-expired"
    assert refs[0].status == "revoked", f"Expected 'revoked', got {refs[0].status!r}"
