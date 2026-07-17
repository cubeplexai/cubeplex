"""Unit test for the unambiguous-only PVC migration plan logic.

The CLI itself talks to the cluster; this test exercises the pure-function
planner that decides what to do per user (rename / leave-for-manual /
skip-already-migrated). No cluster I/O.
"""

from unittest.mock import MagicMock

from cubeplex.sandbox.manager import (
    SandboxManager,
    build_legacy_user_pvc_name,
    build_user_pvc_name,
)
from cubeplex.scripts.dev import migrate_user_pvcs
from cubeplex.scripts.dev.migrate_user_pvcs import build_migration_plan, main_async

_PREFIX = "cubeplex-user"


def test_user_with_one_workspace_gets_a_rename_action() -> None:
    old = build_legacy_user_pvc_name(_PREFIX, "u1")
    plan = build_migration_plan(
        existing_pvcs=[old, build_legacy_user_pvc_name(_PREFIX, "u2")],
        memberships={"u1": ["ws-A"], "u2": ["ws-X", "ws-Y"]},
        pvc_prefix=_PREFIX,
    )
    actions = {a.user_id: a for a in plan}
    assert actions["u1"].kind == "rename"
    assert actions["u1"].old_name == old
    assert actions["u1"].new_name == build_user_pvc_name(_PREFIX, "ws-A", "u1")
    # u2 is in two workspaces -> ambiguous, surfaced for manual cleanup.
    assert actions["u2"].kind == "manual_cleanup"


def test_user_with_no_existing_pvc_is_skipped() -> None:
    plan = build_migration_plan(
        existing_pvcs=[],
        memberships={"u1": ["ws-A"]},
        pvc_prefix=_PREFIX,
    )
    assert plan == []


def test_planned_new_name_matches_what_sandbox_manager_actually_mounts(
    mock_encryption_backend,
) -> None:
    """The whole point of building the names off shared helpers is so an
    operator who reads the dry-run output can trust that the renamed PVC
    will be the exact claim the new SandboxManager mounts. Verify by going
    through SandboxManager._build_user_volume directly."""
    manager = SandboxManager(MagicMock(), mock_encryption_backend)
    proposed = build_user_pvc_name(manager._volume_pvc_prefix, "ws-A", "user-1")
    volume = manager._build_user_volume("ws-A", "user", "user-1")
    actual = volume.pvc.claim_name  # type: ignore[union-attr]
    assert proposed == actual


async def test_main_async_refuses_apply_when_list_pvcs_is_unwired(capsys, monkeypatch) -> None:
    """The stub _list_pvcs returns [] in this build. main_async must refuse
    --apply and emit a loud warning so operators don't mistake a stub-empty
    result for 'PVCs already migrated'."""
    monkeypatch.setattr(migrate_user_pvcs, "_LIST_PVCS_WIRED", False)

    async def _no_memberships() -> dict[str, list[str]]:
        return {}

    monkeypatch.setattr(migrate_user_pvcs, "_fetch_memberships", _no_memberships)

    rc = await main_async(apply=True)
    out = capsys.readouterr().out
    assert rc == 2
    assert "not wired" in out.lower()
    assert "refusing to --apply" in out


async def test_main_async_warns_but_proceeds_with_dry_run_when_unwired(capsys, monkeypatch) -> None:
    """Dry-run still runs, but the warning is loud so the output isn't taken
    at face value."""
    monkeypatch.setattr(migrate_user_pvcs, "_LIST_PVCS_WIRED", False)

    async def _no_memberships() -> dict[str, list[str]]:
        return {}

    monkeypatch.setattr(migrate_user_pvcs, "_fetch_memberships", _no_memberships)

    rc = await main_async(apply=False)
    out = capsys.readouterr().out
    assert rc == 0
    assert "not wired" in out.lower()
    assert "nothing to migrate" in out
