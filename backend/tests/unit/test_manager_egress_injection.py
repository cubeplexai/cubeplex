"""Unit test: SandboxManager wires egress env injection into execute-time env.

Strategy: real SandboxManager + real in-memory SQLite session + monkeypatched
opensandbox.Sandbox.create (avoids needing a live sandbox cluster). The resolver
is also monkeypatched to return a deterministic secret + plain value without
needing a seeded credential in the DB.

Key invariant (Task B2): env flows via OpenSandbox._run_env (set at get_or_create
time, injected into every commands.run via RunCommandOpts) — NOT via Sandbox.create.
network_policy still goes into Sandbox.create (egress allow-list is structural).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.models import EgressRef
from cubebox.sandbox.manager import SandboxAttachment, SandboxManager
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


async def _fake_reconnect(sandbox_id: str, **kwargs: Any) -> Any:
    """The create-new path reconnects (skip_health_check) after create to rebind
    the per-command timeout; return a fake sandbox with the same id."""
    return _make_fake_sandbox(sandbox_id)


_RESOLVED_ENVS = [
    ResolvedEnv(
        id="senv-1",
        env_name="GITHUB_TOKEN",
        is_secret=True,
        hosts=["api.github.com"],
        header_names=None,
        credential_id="cred-1",
    ),
    ResolvedEnv(
        id="senv-2",
        env_name="LOG_LEVEL",
        is_secret=False,
        hosts=None,
        header_names=None,
        credential_id="cred-2",
        value="info",
    ),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_create_with_exchange_host_sets_run_env_and_persists_refs(
    session_factory: async_sessionmaker[AsyncSession],
    mock_encryption_backend: Any,
) -> None:
    """With _exchange_host set, Sandbox.create gets network_policy (no env=); run env is
    set on the backend via set_run_env; an EgressRef is persisted."""
    create_kwargs: dict[str, Any] = {}

    async def fake_create(image: str, **kwargs: Any) -> Any:
        create_kwargs.update(kwargs)
        return _make_fake_sandbox("sbx-new")

    manager = SandboxManager(session_factory, mock_encryption_backend)
    # Force exchange host (config returns "" by default in tests)
    manager._exchange_host = "egress-exchange.internal"

    with (
        patch("opensandbox.Sandbox.create", side_effect=fake_create),
        patch("opensandbox.Sandbox.connect", side_effect=_fake_reconnect),
        patch(
            "cubebox.services.sandbox_env.SandboxEnvResolver.resolve",
            return_value=_RESOLVED_ENVS,
        ),
        # Stub get_active_by_scope → None so we always take the create-new path
        patch(
            "cubebox.repositories.user_sandbox.UserSandboxRepository.get_active_by_scope",
            return_value=None,
        ),
        # _RESOLVED_ENVS already has value= populated; skip DB decrypt in these
        # egress-injection-focused tests.
        patch.object(manager, "_decrypt_env_values", new=AsyncMock()),
    ):
        attachment = await manager.get_or_create(
            scope_type="user",
            scope_id="u-1",
            user_id="u-1",
            org_id="org-1",
            workspace_id="ws-1",
        )

    # --- env= must NOT be passed to Sandbox.create (env moved to execute-time) ---
    assert "env" not in create_kwargs, (
        "Sandbox.create must NOT receive env= (env is now injected at execute-time)"
    )

    # --- network_policy IS still passed at creation time ---
    policy = create_kwargs.get("network_policy")
    assert policy is not None, "Sandbox.create must receive network_policy="
    assert hasattr(policy, "egress")
    targets = {r.target for r in policy.egress}
    # Vault host is NOT in the allow-list — network reachability is decoupled
    # from credential substitution.
    assert "api.github.com" not in targets
    # The exchange host stays force-allowed so substitution still works.
    assert "egress-exchange.internal" in targets
    assert policy.default_action == "deny"

    # --- run env was set on the returned backend ---
    from cubebox.sandbox.opensandbox import OpenSandbox

    assert isinstance(attachment, SandboxAttachment)
    sandbox = attachment.sandbox
    assert isinstance(sandbox, OpenSandbox)
    run_env = sandbox._run_env

    # Secret → cbxref_ placeholder
    github_val = run_env.get("GITHUB_TOKEN", "")
    assert PLACEHOLDER_RE.fullmatch(github_val), (
        f"GITHUB_TOKEN must be a cbxref_ placeholder, got {github_val!r}"
    )
    # Plain value passes through verbatim
    assert run_env.get("LOG_LEVEL") == "info"

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


async def test_reuse_path_sets_run_env_and_refreshes_refs(
    session_factory: async_sessionmaker[AsyncSession],
    mock_encryption_backend: Any,
) -> None:
    """Reusing a healthy sandbox: set_run_env is called with fresh placeholders
    and prior EgressRefs are revoked then re-persisted."""
    # Seed a stale EgressRef for the existing sandbox
    async with session_factory() as seed_session:
        old_placeholder = mint_placeholder()
        stale_ref = EgressRef(
            ref_hash=hash_placeholder(old_placeholder),
            sandbox_id="sbx-reuse",
            org_id="org-1",
            workspace_id="ws-1",
            user_id="u-1",
            run_id=None,
            bindings=[],
            status="valid",
        )
        seed_session.add(stale_ref)
        await seed_session.commit()

    old_record = MagicMock()
    old_record.id = "rec-reuse"
    old_record.sandbox_id = "sbx-reuse"
    # The reuse branch is gated on status=="running" (see Task 6: provisioning
    # rows fall through to the race-loss poll path).
    old_record.status = "running"
    old_record.image = "ubuntu:22.04"
    # _connect_existing reads org_id/workspace_id off the record for egress
    # ref persistence (the row's scope, not the caller's).
    old_record.org_id = "org-1"
    old_record.workspace_id = "ws-1"

    async def fake_connect(sandbox_id: str, **kwargs: Any) -> Any:
        fake = MagicMock()

        async def healthy() -> bool:
            return True

        fake.is_healthy = healthy
        fake.id = sandbox_id
        return fake

    manager = SandboxManager(session_factory, mock_encryption_backend)
    manager._exchange_host = "egress-exchange.internal"

    with (
        patch("opensandbox.Sandbox.connect", side_effect=fake_connect),
        patch(
            "cubebox.repositories.user_sandbox.UserSandboxRepository.get_active_by_scope",
            return_value=old_record,
        ),
        patch(
            "cubebox.repositories.user_sandbox.UserSandboxRepository.update_activity",
            return_value=None,
        ),
        patch(
            "cubebox.services.sandbox_env.SandboxEnvResolver.resolve",
            return_value=_RESOLVED_ENVS,
        ),
        # _RESOLVED_ENVS already has value= populated; skip DB decrypt.
        patch.object(manager, "_decrypt_env_values", new=AsyncMock()),
    ):
        attachment = await manager.get_or_create(
            scope_type="user", scope_id="u-1", user_id="u-1", org_id="org-1", workspace_id="ws-1"
        )

    # run env must have been set on the returned backend
    from cubebox.sandbox.opensandbox import OpenSandbox

    assert isinstance(attachment, SandboxAttachment)
    backend = attachment.sandbox
    assert isinstance(backend, OpenSandbox)
    run_env = backend._run_env
    github_val = run_env.get("GITHUB_TOKEN", "")
    assert PLACEHOLDER_RE.fullmatch(github_val), (
        f"GITHUB_TOKEN must be a cbxref_ placeholder, got {github_val!r}"
    )

    # Stale ref must be revoked; fresh ref persisted
    async with session_factory() as check_session:
        refs = (await check_session.execute(select(EgressRef))).scalars().all()

    # One revoked (old) + one new valid ref
    statuses = {r.ref_hash: r.status for r in refs}
    assert any(s == "revoked" for s in statuses.values()), "Stale ref must be revoked"
    assert any(s == "valid" for s in statuses.values()), "Fresh ref must be persisted"
    valid_refs = [r for r in refs if r.status == "valid"]
    assert len(valid_refs) == 1
    assert valid_refs[0].sandbox_id == "sbx-reuse"
    assert valid_refs[0].bindings[0]["env_name"] == "GITHUB_TOKEN"


async def test_create_without_exchange_host_skips_injection(
    session_factory: async_sessionmaker[AsyncSession],
    mock_encryption_backend: Any,
) -> None:
    """When _exchange_host is empty (egress disabled), Sandbox.create gets NO env/network_policy
    and no run env is set."""
    create_kwargs: dict[str, Any] = {}

    async def fake_create(image: str, **kwargs: Any) -> Any:
        create_kwargs.update(kwargs)
        return _make_fake_sandbox("sbx-plain")

    manager = SandboxManager(session_factory, mock_encryption_backend)
    # _exchange_host defaults to "" from config in tests — be explicit
    manager._exchange_host = ""

    with (
        patch("opensandbox.Sandbox.create", side_effect=fake_create),
        patch("opensandbox.Sandbox.connect", side_effect=_fake_reconnect),
        patch(
            "cubebox.repositories.user_sandbox.UserSandboxRepository.get_active_by_scope",
            return_value=None,
        ),
    ):
        attachment = await manager.get_or_create(
            scope_type="user", scope_id="u-1", user_id="u-1", org_id="org-1", workspace_id="ws-1"
        )

    assert "env" not in create_kwargs, "Without exchange_host, env must NOT be passed"
    # After Task 6: the create call always carries a NetworkPolicy so admin
    # `network_rules` apply even when the egress exchange host is unset. With
    # no admin rules AND no exchange host, the policy is the empty deny-default
    # (no allow-list — strictly stricter than the old `network_policy=None`).
    policy = create_kwargs.get("network_policy")
    assert policy is not None, (
        "Without exchange_host, network_policy is now the empty deny-default policy"
    )
    assert policy.default_action == "deny"
    assert list(policy.egress) == [], (
        f"Expected no egress rules when neither admin nor injection contributed, got "
        f"{policy.egress!r}"
    )

    # Backend has an empty run env (no egress injection)
    from cubebox.sandbox.opensandbox import OpenSandbox

    assert isinstance(attachment, SandboxAttachment)
    backend = attachment.sandbox
    assert isinstance(backend, OpenSandbox)
    assert backend._run_env == {}, f"Expected empty run env, got {backend._run_env!r}"

    # No EgressRef rows persisted
    async with session_factory() as session:
        refs = (await session.execute(select(EgressRef))).scalars().all()
    assert refs == [], f"Expected no EgressRef rows, got {refs}"


async def test_unhealthy_sandbox_revokes_refs(
    session_factory: async_sessionmaker[AsyncSession],
    mock_encryption_backend: Any,
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

    # Stub a "running" record for the old sandbox that fails health check.
    # Scope attrs are read by _provision_new_container on the revive path
    # (the unhealthy running row is revived in place rather than replaced).
    old_record = MagicMock()
    old_record.id = "rec-old"
    old_record.sandbox_id = "sbx-old"
    old_record.status = "running"
    old_record.image = "ubuntu:22.04"
    old_record.scope_type = "user"
    old_record.scope_id = "u-1"
    old_record.workspace_id = "ws-1"
    old_record.org_id = "org-1"
    old_record.user_id = "u-1"

    async def fake_connect(sandbox_id: str, **kwargs: Any) -> Any:
        fake = MagicMock()

        async def not_healthy() -> bool:
            return False

        # is_healthy returns False so the manager revokes + recreates
        fake.is_healthy = not_healthy
        return fake

    async def flip_terminated(record_id: str, **kwargs: Any) -> None:
        # _connect_existing marks the row terminated; flip the stub so the
        # re-fetch sees a terminal row and falls through to the revive branch.
        old_record.status = "terminated"
        old_record.sandbox_id = None

    manager = SandboxManager(session_factory, mock_encryption_backend)
    manager._exchange_host = "egress-exchange.internal"

    with (
        patch("opensandbox.Sandbox.create", side_effect=fake_create),
        patch("opensandbox.Sandbox.connect", side_effect=fake_connect),
        patch(
            "cubebox.repositories.user_sandbox.UserSandboxRepository.get_active_by_scope",
            return_value=old_record,
        ),
        patch(
            "cubebox.repositories.user_sandbox.UserSandboxRepository.mark_terminated",
            side_effect=flip_terminated,
        ),
        # Revive path: atomic claim wins, then provision a fresh container on
        # the same row.
        patch(
            "cubebox.repositories.user_sandbox.UserSandboxRepository.claim_for_provisioning",
            return_value=True,
        ),
        patch(
            "cubebox.repositories.user_sandbox.UserSandboxRepository.promote_to_running",
            return_value=None,
        ),
        patch(
            "cubebox.services.sandbox_env.SandboxEnvResolver.resolve",
            return_value=resolved_envs,
        ),
    ):
        await manager.get_or_create(
            scope_type="user", scope_id="u-1", user_id="u-1", org_id="org-1", workspace_id="ws-1"
        )

    # The old ref must now be revoked
    async with session_factory() as check_session:
        refs = (await check_session.execute(select(EgressRef))).scalars().all()

    assert len(refs) == 1
    assert refs[0].sandbox_id == "sbx-old"
    assert refs[0].status == "revoked", f"Expected 'revoked', got {refs[0].status!r}"


async def _seed_expiring_ref(
    session_factory: async_sessionmaker[AsyncSession],
    sandbox_id: str,
    expires_at: datetime,
) -> str:
    """Seed a valid EgressRef whose expiry is about to lapse; return its ref_hash."""
    async with session_factory() as session:
        ref = EgressRef(
            ref_hash=hash_placeholder(mint_placeholder()),
            sandbox_id=sandbox_id,
            org_id="org-1",
            workspace_id="ws-1",
            user_id="u-1",
            run_id=None,
            bindings=[],
            status="valid",
            expires_at=expires_at,
        )
        session.add(ref)
        await session.commit()
        return ref.ref_hash


async def _get_ref(session_factory: async_sessionmaker[AsyncSession], ref_hash: str) -> EgressRef:
    async with session_factory() as session:
        return (
            await session.execute(select(EgressRef).where(EgressRef.ref_hash == ref_hash))
        ).scalar_one()


async def test_touch_extends_egress_ref_expiry(
    session_factory: async_sessionmaker[AsyncSession],
    mock_encryption_backend: Any,
) -> None:
    """touch() on an active sandbox pushes its valid refs' expires_at into the
    future so long, still-active sessions don't lose placeholder substitution
    mid-run (keepalive)."""
    soon = datetime.now(UTC) + timedelta(seconds=5)
    ref_hash = await _seed_expiring_ref(session_factory, "sbx-touch", soon)

    manager = SandboxManager(session_factory, mock_encryption_backend)
    manager._exchange_host = "egress-exchange.internal"

    await manager.touch("sbx-touch", org_id="org-1", workspace_id="ws-1", force=True)

    ref = await _get_ref(session_factory, ref_hash)
    assert ref.status == "valid"
    assert ref.expires_at is not None
    extended = ref.expires_at
    if extended.tzinfo is None:
        extended = extended.replace(tzinfo=UTC)
    assert extended > soon, "touch() must push expires_at beyond the original near-expiry"


async def test_touch_active_extends_egress_ref_expiry(
    session_factory: async_sessionmaker[AsyncSession],
    mock_encryption_backend: Any,
) -> None:
    """touch_active() (browser keepalive, keyed by user) extends the user's
    active sandbox's valid refs the same way touch() does."""
    soon = datetime.now(UTC) + timedelta(seconds=5)
    ref_hash = await _seed_expiring_ref(session_factory, "sbx-active", soon)

    record = MagicMock()
    record.id = "rec-active"
    record.sandbox_id = "sbx-active"
    record.deleted_at = None  # active row; touch_active guards on this
    record.status = "running"  # _touchable_statuses guard (codex R1)

    manager = SandboxManager(session_factory, mock_encryption_backend)
    manager._exchange_host = "egress-exchange.internal"

    with (
        patch(
            "cubebox.repositories.user_sandbox.UserSandboxRepository.get_active_by_scope",
            return_value=record,
        ),
        patch(
            "cubebox.repositories.user_sandbox.UserSandboxRepository.update_activity",
            return_value=None,
        ),
    ):
        found = await manager.touch_active(
            scope_type="user", scope_id="u-1", org_id="org-1", workspace_id="ws-1"
        )

    assert found is True
    ref = await _get_ref(session_factory, ref_hash)
    extended = ref.expires_at
    assert extended is not None
    if extended.tzinfo is None:
        extended = extended.replace(tzinfo=UTC)
    assert extended > soon, "touch_active() must push expires_at beyond the near-expiry"


async def test_cleanup_expired_revokes_refs(
    session_factory: async_sessionmaker[AsyncSession],
    mock_encryption_backend: Any,
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

    manager = SandboxManager(session_factory, mock_encryption_backend)
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
        # Stub sandbox connect+kill so no real network call is made
        patch("opensandbox.Sandbox.connect", return_value=AsyncMock()),
    ):
        await manager.cleanup_expired()

    # The ref for the expired sandbox must now be revoked
    async with session_factory() as check_session:
        refs = (await check_session.execute(select(EgressRef))).scalars().all()

    assert len(refs) == 1
    assert refs[0].sandbox_id == "sbx-expired"
    assert refs[0].status == "revoked", f"Expected 'revoked', got {refs[0].status!r}"
