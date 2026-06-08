"""E2E pause/resume tests against a real OpenSandbox.

Per the internals note G11, the dev OpenSandbox at 39.99.248.80:18080
returns 202 for pause but never reaches the ``Paused`` state — the server
silently no-ops pause for the default image. These tests recognise that
empirical reality:

* If the provider transitions to ``Paused`` within a bounded window, the
  full DB + resume assertions run.
* If the provider stays ``Running`` after the window, each scenario calls
  ``pytest.skip(...)`` with a message that names G11, so the file is not
  marked xfail (which would hide real regressions on a pause-capable
  backend).

The fixtures mirror ``tests/e2e/test_opensandbox.py``: module-scope handle
created via ``opensandbox.Sandbox.create`` + function-scope re-``connect``
to avoid event-loop conflicts.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import opensandbox
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from opensandbox.config import ConnectionConfig
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubebox.config import config
from cubebox.credentials.encryption import FernetBackend
from cubebox.models import Organization, Workspace
from cubebox.models.user import User
from cubebox.models.user_sandbox import UserSandbox
from cubebox.repositories.user_sandbox import UserSandboxRepository
from cubebox.sandbox.local import LocalSandbox
from cubebox.sandbox.manager import SandboxManager

pytestmark = pytest.mark.e2e

# Use distinct org/ws/user IDs so this file doesn't fight with other E2Es that
# pin DEFAULT_ORG_ID/DEFAULT_WS_ID.
_ORG_ID = "org-pause-resume-e2e"
_WS_ID = "ws-pause-resume-e2e"
_USER_ID = "u-pause-resume-e2e"
_PAUSE_WAIT_SECONDS = 20.0
_PAUSE_POLL_INTERVAL = 0.5


async def _wait_for_provider_state(
    raw_sandbox: opensandbox.Sandbox,
    expected: str,
    *,
    timeout: float = _PAUSE_WAIT_SECONDS,
) -> bool:
    """Poll ``get_info().status.state`` until it matches ``expected`` or timeout.

    Returns whether the state was reached. State is compared as a free-form
    string per internals G3 (the SDK enum is incomplete).
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        info = await raw_sandbox.get_info()
        state = (info.status.state if info and info.status else "") or ""
        if state == expected:
            return True
        await asyncio.sleep(_PAUSE_POLL_INTERVAL)
    return False


async def _seed_org_ws_user(session_maker: async_sessionmaker[AsyncSession]) -> None:
    """Idempotently seed the org/workspace/user rows needed for the DB FK chain."""
    async with session_maker() as session:
        org = await session.get(Organization, _ORG_ID)
        if org is None:
            session.add(
                Organization(id=_ORG_ID, name="Pause/Resume E2E Org", slug="pause-resume-e2e")
            )
            await session.commit()
        ws = await session.get(Workspace, _WS_ID)
        if ws is None:
            session.add(Workspace(id=_WS_ID, org_id=_ORG_ID, name="Pause/Resume E2E WS"))
            await session.commit()
        user = await session.get(User, _USER_ID)
        if user is None:
            session.add(
                User(
                    id=_USER_ID,
                    email="pause-resume-e2e@example.com",
                    hashed_password="x",  # not used; this user never logs in
                    is_active=True,
                    is_superuser=False,
                    is_verified=True,
                )
            )
            await session.commit()


def _build_conn_config() -> ConnectionConfig:
    return ConnectionConfig(
        domain=config.get("sandbox.domain"),
        api_key=config.get("sandbox.api_key"),
        request_timeout=timedelta(seconds=60),
        use_server_proxy=config.get("sandbox.use_server_proxy", False),
    )


# ---------------------------------------------------------------------------
# Module fixture: create the sandbox once + skip cleanly if unreachable
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def shared_sandbox_id() -> AsyncIterator[str]:
    """Create a real sandbox once for the whole module, kill it on teardown."""
    try:
        raw_sandbox = await opensandbox.Sandbox.create(
            config.get("sandbox.image"),
            connection_config=_build_conn_config(),
            ready_timeout=timedelta(seconds=120),
        )
    except Exception as exc:
        pytest.skip(f"OpenSandbox service not available: {exc}")

    sandbox_id = raw_sandbox.id
    print(f"\n[Module Setup] Created shared sandbox: {sandbox_id}")
    try:
        yield sandbox_id
    finally:
        print(f"\n[Module Teardown] Killing shared sandbox: {sandbox_id}")
        try:
            await raw_sandbox.kill()
        except Exception as exc:
            print(f"Warning: failed to kill sandbox: {exc}")
        try:
            await raw_sandbox.close()
        except Exception:
            pass


@pytest_asyncio.fixture
async def sandbox_handle(shared_sandbox_id: str) -> AsyncIterator[opensandbox.Sandbox]:
    """Per-test reconnect — new httpx client per event loop. Does not kill on exit."""
    raw_sandbox = await opensandbox.Sandbox.connect(
        shared_sandbox_id,
        connection_config=_build_conn_config(),
        skip_health_check=True,
    )
    try:
        yield raw_sandbox
    finally:
        try:
            await raw_sandbox.close()
        except Exception:
            pass


@pytest_asyncio.fixture
async def manager(
    session_factory: async_sessionmaker[AsyncSession],
) -> SandboxManager:
    """Manager wired to the test DB session factory + seeded org/ws/user."""
    await _seed_org_ws_user(session_factory)
    mgr = SandboxManager(session_factory, FernetBackend([Fernet.generate_key()]))
    # No egress in these tests — keep the focus on pause/resume orchestration.
    mgr._exchange_host = ""
    # The deployed config disables pause_on_idle (OpenSandbox v1.0.12 "pause"
    # silently deletes the pod). These tests are specifically exercising the
    # pause/resume orchestration and must run against the pause-enabled branch
    # — mirrors tests/unit/test_sandbox_manager_pause.py:71.
    mgr._pause_on_idle = True
    return mgr


async def _insert_record(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    sandbox_id: str,
    ttl_seconds: int = 600,
    status: str = "running",
    paused_at: datetime | None = None,
    in_use_until: datetime | None = None,
    paused_ttl_seconds: int = 24 * 60,
    idle_secs: int = 3600,
) -> str:
    """Insert a UserSandbox row directly and return its id.

    Backdates ``last_activity_at`` by ``idle_secs`` so the row is immediately
    stale against ``list_idle_to_pause_system`` queries with positive
    ``idle_ttl_seconds`` — the default match for the test SQL
    ``last_activity_at + idle_ttl * INTERVAL '1 second' <= NOW()``.

    Default 3600s is safely past the default ``SandboxManager._idle_ttl_seconds``
    (1800s / 30 min) so the test row is selected by ``list_idle_to_pause_system``
    *without* lowering the manager's threshold — lowering it to 0 in the
    fixture would make the system-scope reaper match every concurrent
    unleased row in the shared test DB (codex P2 on PR #213).
    """
    async with session_maker() as session:
        repo = UserSandboxRepository(session, org_id=_ORG_ID, workspace_id=_WS_ID)
        record = await repo.create(
            user_id=_USER_ID,
            sandbox_id=sandbox_id,
            image=config.get("sandbox.image"),
            ttl_seconds=ttl_seconds,
        )
        # Stamp non-default fields via direct attribute writes — ScopedRepository.create
        # doesn't accept them, and this keeps the helper compact.
        record.last_activity_at = datetime.now(UTC) - timedelta(seconds=idle_secs)
        if status != "running":
            record.status = status
        if paused_at is not None:
            record.paused_at = paused_at
        if in_use_until is not None:
            record.in_use_until = in_use_until
        if paused_ttl_seconds != 24 * 60:
            record.paused_ttl_seconds = paused_ttl_seconds
        await session.commit()
        return record.id


async def _fetch_record(
    session_maker: async_sessionmaker[AsyncSession], record_id: str
) -> UserSandbox | None:
    async with session_maker() as session:
        repo = UserSandboxRepository(session, org_id=_ORG_ID, workspace_id=_WS_ID)
        return await repo.get(record_id)


async def _delete_record(session_maker: async_sessionmaker[AsyncSession], record_id: str) -> None:
    async with session_maker() as session:
        repo = UserSandboxRepository(session, org_id=_ORG_ID, workspace_id=_WS_ID)
        rec = await repo.get(record_id)
        if rec is not None:
            await session.delete(rec)
            await session.commit()


# ---------------------------------------------------------------------------
# Scenario 1 — Round-trip + memory survival (idle auto-pause via manager)
# ---------------------------------------------------------------------------


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_pause_resume_roundtrip_preserves_memory(
    manager: SandboxManager,
    sandbox_handle: opensandbox.Sandbox,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Drive pause_idle() with ttl_seconds=0 → verify provider state +
    DB row, then re-enter via get_or_create and confirm both PVC and /tmp
    files survived (proof that pause preserves more than the PVC alone).

    Skips with G11 if the provider silently no-ops pause.
    """
    # Write the two files BEFORE pausing.
    await sandbox_handle.files.write_file("/workspace/keep.txt", b"keep-pvc\n")
    await sandbox_handle.files.write_file("/tmp/ephemeral.txt", b"keep-tmp\n")

    # Seed a UserSandbox row with ttl_seconds=0 so it's immediately idle.
    record_id = await _insert_record(
        session_factory,
        sandbox_id=sandbox_handle.id,
        ttl_seconds=0,
    )
    try:
        await manager.pause_idle()

        # Wait for the provider to actually transition.
        paused = await _wait_for_provider_state(sandbox_handle, "Paused")
        if not paused:
            # G11: this dev backend silently no-ops pause. The row sits at
            # ``pausing`` and the reconciler will keep observing ``Running``.
            # Round-trip resume can't be proven here.
            record = await _fetch_record(session_factory, record_id)
            pytest.skip(
                f"G11: OpenSandbox at {config.get('sandbox.domain')} silently no-ops pause; "
                "round-trip resume is only meaningful on a pause-capable backend "
                f"(record.status={record.status if record else 'missing'!r})"
            )

        # Per G1, pause() is async — pause_idle leaves the row at ``pausing``
        # and the reconciler advances it once the provider reports ``Paused``.
        # Drive the reconciler explicitly so the test doesn't depend on the
        # cleanup-loop cadence.
        await manager.reconcile_transients(claim_timeout=0)

        record = await _fetch_record(session_factory, record_id)
        assert record is not None
        assert record.status == "paused", f"expected paused, got {record.status!r}"
        assert record.paused_at is not None

        # Re-enter via the manager — this should trigger resume-on-reuse.
        backend = await manager.get_or_create(_USER_ID, org_id=_ORG_ID, workspace_id=_WS_ID)
        try:
            # Both files must survive native pause + resume.
            results = await backend.download(["/workspace/keep.txt", "/tmp/ephemeral.txt"])
            payload = dict(results)
            assert payload["/workspace/keep.txt"] == b"keep-pvc\n"
            assert payload["/tmp/ephemeral.txt"] == b"keep-tmp\n"

            # Endpoint reconstruction: execute should work after resume.
            result = await backend.execute("echo ok")
            assert result.exit_code == 0
            assert "ok" in result.output

            # DB row should be back to running.
            record = await _fetch_record(session_factory, record_id)
            assert record is not None
            assert record.status == "running"
            assert record.last_resumed_at is not None
        finally:
            await backend.close()
    finally:
        await _delete_record(session_factory, record_id)


# ---------------------------------------------------------------------------
# Scenario 2 — Browser endpoint reconstruction after resume
# ---------------------------------------------------------------------------


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_browser_endpoint_after_resume(
    manager: SandboxManager,
    sandbox_handle: opensandbox.Sandbox,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """After a successful resume, ``get_browser_endpoint`` should return a
    fresh URL string. Skips if the image lacks the in-image browser launcher
    or if G11 blocks the pause."""
    # Try to start the browser; if the image doesn't have it, skip — this is
    # documented in spec OQ-3 / browser skill notes and is not a defect.
    from cubebox.sandbox.opensandbox import OpenSandbox

    pre_backend = OpenSandbox(sandbox=sandbox_handle)
    try:
        await pre_backend.start_browser()
    except Exception as exc:
        pytest.skip(f"sandbox image lacks Neko browser launcher: {exc}")

    record_id = await _insert_record(
        session_factory,
        sandbox_id=sandbox_handle.id,
        ttl_seconds=0,
    )
    try:
        await manager.pause_idle()
        if not await _wait_for_provider_state(sandbox_handle, "Paused"):
            pytest.skip(
                f"G11: OpenSandbox at {config.get('sandbox.domain')} silently no-ops pause; "
                "browser-endpoint-after-resume only meaningful on a pause-capable backend"
            )

        backend = await manager.get_or_create(_USER_ID, org_id=_ORG_ID, workspace_id=_WS_ID)
        try:
            # Restart browser (idempotent) then fetch the endpoint.
            await backend.start_browser()
            endpoint = await backend.get_browser_endpoint()
            assert endpoint.url.startswith(("http://", "https://"))
            assert endpoint.url.endswith("/")
        finally:
            await backend.close()
    finally:
        await _delete_record(session_factory, record_id)


# ---------------------------------------------------------------------------
# Scenario 3 — Idle auto-pause vs active (lease guard)
# ---------------------------------------------------------------------------


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_lease_blocks_idle_pause(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A row whose ``in_use_until`` is in the future must not be selected by
    ``list_idle_to_pause_system`` even when ``ttl_seconds=0``.

    This is a pure DB/repository assertion — no provider call — so it runs
    regardless of G11.
    """
    await _seed_org_ws_user(session_factory)
    fake_sbx_id = f"sbx-fake-lease-{datetime.now(UTC).timestamp()}"
    record_id = await _insert_record(
        session_factory,
        sandbox_id=fake_sbx_id,
        ttl_seconds=0,
        in_use_until=datetime.now(UTC) + timedelta(minutes=5),
    )
    try:
        async with session_factory() as session:
            idle = await UserSandboxRepository.list_idle_to_pause_system(
                session, idle_ttl_seconds=1
            )
        leased_ids = {r.id for r in idle}
        assert record_id not in leased_ids, (
            "row with future in_use_until should NOT be picked by the idle reaper"
        )

        # Conversely, once the lease is cleared the row IS picked.
        async with session_factory() as session:
            repo = UserSandboxRepository(session, org_id=_ORG_ID, workspace_id=_WS_ID)
            await repo.release_in_use(record_id)

        async with session_factory() as session:
            idle = await UserSandboxRepository.list_idle_to_pause_system(
                session, idle_ttl_seconds=1
            )
        assert record_id in {r.id for r in idle}
    finally:
        await _delete_record(session_factory, record_id)


# ---------------------------------------------------------------------------
# Scenario 4 — Paused-TTL reap terminates and revokes
# ---------------------------------------------------------------------------


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_reap_paused_terminates_stale_paused_row(
    manager: SandboxManager,
    sandbox_handle: opensandbox.Sandbox,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """After ``paused_ttl_seconds`` elapses, ``reap_paused`` must terminate
    the row + kill the underlying sandbox. Skips if pause itself didn't
    take (G11) since we need a paused row to reap."""
    record_id = await _insert_record(
        session_factory,
        sandbox_id=sandbox_handle.id,
        ttl_seconds=0,
        paused_ttl_seconds=1,  # paused expires after 1 second
    )
    try:
        await manager.pause_idle()

        if not await _wait_for_provider_state(sandbox_handle, "Paused"):
            pytest.skip(
                f"G11: OpenSandbox at {config.get('sandbox.domain')} silently no-ops pause; "
                "reap-paused needs an actually-paused row to be meaningful"
            )

        # Per G1 pause is async — drive the reconciler to advance pausing -> paused
        # so reap_paused has an actually-paused row to operate on.
        await manager.reconcile_transients(claim_timeout=0)

        # Wait past the 1-second paused_ttl.
        await asyncio.sleep(2.0)
        await manager.reap_paused()

        record = await _fetch_record(session_factory, record_id)
        assert record is not None
        assert record.status == "terminated"
    finally:
        await _delete_record(session_factory, record_id)


# ---------------------------------------------------------------------------
# Scenario 5 — Capability gap (LocalSandbox)
#
# Covered fully by tests/unit/test_sandbox_manager_pause.py
# (test_pause_idle_no_capability_kills_record). A one-line sanity check
# here keeps the matrix explicit: LocalSandbox.supports_pause() must be
# False so the manager picks the kill path.
# ---------------------------------------------------------------------------


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_local_sandbox_does_not_support_pause() -> None:
    """LocalSandbox is the canonical non-pausing driver — capability flag
    must stay False so the manager always falls back to kill for it."""
    local = LocalSandbox()
    assert local.supports_pause() is False
    with pytest.raises(NotImplementedError):
        await local.pause()
