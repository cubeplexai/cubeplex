"""Unit test for the unambiguous-only PVC migration plan logic.

The CLI itself talks to the cluster; this test exercises the pure-function
planner that decides what to do per user (rename / leave-for-manual /
skip-already-migrated). No cluster I/O.
"""

from cubebox.scripts.dev.migrate_user_pvcs import build_migration_plan


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
