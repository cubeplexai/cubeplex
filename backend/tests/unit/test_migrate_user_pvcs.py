"""Unit test for the unambiguous-only PVC migration plan logic.

The CLI itself talks to the cluster; this test exercises the pure-function
planner that decides what to do per user (rename / leave-for-manual /
skip-already-migrated). No cluster I/O.
"""

from cubebox.scripts.dev import migrate_user_pvcs
from cubebox.scripts.dev.migrate_user_pvcs import build_migration_plan, main_async


def test_user_with_one_workspace_gets_a_rename_action() -> None:
    plan = build_migration_plan(
        existing_pvcs=["user-u1", "user-u2"],
        memberships={"u1": ["ws-A"], "u2": ["ws-X", "ws-Y"]},
        target_prefix="user-",
        new_template="ws-{ws}-user-{user}",
    )
    actions = {a.user_id: a for a in plan}
    assert actions["u1"].kind == "rename"
    assert actions["u1"].new_name == "ws-ws-A-user-u1"
    # u2 is in two workspaces -> ambiguous, surfaced for manual cleanup.
    assert actions["u2"].kind == "manual_cleanup"


def test_user_with_no_existing_pvc_is_skipped() -> None:
    plan = build_migration_plan(
        existing_pvcs=[],
        memberships={"u1": ["ws-A"]},
        target_prefix="user-",
        new_template="ws-{ws}-user-{user}",
    )
    assert plan == []


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
