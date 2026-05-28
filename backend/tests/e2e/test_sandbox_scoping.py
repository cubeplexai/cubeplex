"""E2E for sandbox ownership isolation + org command-deny policy.

The ``seeded_org_ws_user`` fixture (see tests/e2e/conftest.py) inserts one
org, two workspaces, and one user with membership in both — exercising the
same-user-different-workspace path that the ``uq_user_sandbox_active``
partial unique index protects.

``fake_opensandbox`` (same conftest) monkeypatches
``opensandbox.Sandbox.create`` / ``.connect`` with a ``_FakeRaw`` shim so
these tests never touch a live OpenSandbox provider.
"""

from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from cubebox.middleware import sandbox as sandbox_mw
from cubebox.middleware.sandbox import _make_execute_tool
from cubebox.sandbox.manager import SandboxManager


async def test_same_user_two_workspaces_distinct_active_rows(
    fake_opensandbox: None,
    session_factory: Any,
    seeded_org_ws_user: tuple[str, str, str, str],
) -> None:
    """Two get_or_create calls for the same user in two workspaces yield two
    distinct active UserSandbox rows (storage + provider isolation boundary).
    """
    del fake_opensandbox  # autouse via parameter
    org_id, ws_a, ws_b, user_id = seeded_org_ws_user
    mgr = SandboxManager(session_factory)
    await mgr.get_or_create(user_id, org_id=org_id, workspace_id=ws_a)
    await mgr.get_or_create(user_id, org_id=org_id, workspace_id=ws_b)
    async with session_factory() as s:
        rows = (
            await s.execute(
                sa.text(
                    "SELECT workspace_id, sandbox_id FROM user_sandboxes "
                    "WHERE user_id=:u AND status='running'"
                ),
                {"u": user_id},
            )
        ).all()
    ws_ids = {r[0] for r in rows}
    sbx_ids = {r[1] for r in rows}
    assert ws_ids == {ws_a, ws_b}
    assert len(sbx_ids) == 2  # distinct provider sandboxes


async def test_concurrent_create_reuses_not_duplicates(
    fake_opensandbox: None,
    session_factory: Any,
    seeded_org_ws_user: tuple[str, str, str, str],
) -> None:
    """A second create for the same identity reuses; never a second running row."""
    del fake_opensandbox
    org_id, ws_a, _ws_b, user_id = seeded_org_ws_user
    mgr = SandboxManager(session_factory)
    await mgr.get_or_create(user_id, org_id=org_id, workspace_id=ws_a)
    await mgr.get_or_create(user_id, org_id=org_id, workspace_id=ws_a)
    async with session_factory() as s:
        count = (
            await s.execute(
                sa.text(
                    "SELECT COUNT(*) FROM user_sandboxes "
                    "WHERE user_id=:u AND workspace_id=:w AND status='running'"
                ),
                {"u": user_id, "w": ws_a},
            )
        ).scalar_one()
    assert count == 1


async def test_command_deny_blocks_and_filesystem_untouched(
    admin_client_with_user_id: tuple[httpx.AsyncClient, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admin denies 'rm *'; the agent's execute attempt is blocked and the
    audit buffer (what actually hit the fs) stays empty for that command.
    """
    del monkeypatch
    client, ws_id, _user_id = admin_client_with_user_id

    put = await client.put(
        "/api/v1/admin/sandbox-policy",
        json={
            "default_image": "ubuntu:22.04",
            "network_rules": None,
            "command_rules": [{"action": "deny", "pattern": "rm *"}],
        },
    )
    assert put.status_code == 200, put.text

    sandbox_mw.enable_audit()
    sandbox_mw.reset_executed_commands()
    try:
        # Build the middleware exactly as run_manager would, with the denied rule,
        # and invoke the execute tool directly (no provider needed for a deny).
        class _Sb:
            workdir = "/workspace"

            async def execute(self, command: str) -> Any:  # pragma: no cover - guard
                raise AssertionError("denied command must not reach the sandbox")

        tool = _make_execute_tool(
            _Sb(),  # type: ignore[arg-type]
            workspace_id=ws_id,
            conversation_id="conv-1",
            command_rules=[{"action": "deny", "pattern": "rm *"}],
        )
        res = await tool.execute("c1", tool.parameters(command="rm -rf /workspace"))
        assert "blocked by org policy" in res.content[0].text
        assert sandbox_mw.executed_commands(ws_id, "conv-1") == []
    finally:
        sandbox_mw.disable_audit()
