"""SandboxManager-level integration test for topic-keyed sandbox scoping.

Repo unit tests alone don't catch the bugs where ``LazySandbox`` /
``SandboxManager`` forget to thread ``topic_id`` through to the lookup /
reserve callsites. This file exercises the manager end-to-end against
the real test Postgres DB (so the partial unique indexes installed by
the topic_id migration actually fire) with the OpenSandbox provider
faked out.

This is the gate that proves the spec's "dedicated mode = isolated
environment" promise: without it, every bug in Task 2.5 Steps 1-3
(missed callsite, forgotten ``reserve()`` parameter, predicate not
hand-edited) would be invisible to CI.
"""

from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from cubebox.sandbox.manager import SandboxManager


async def test_dedicated_topic_sandbox_isolated_from_personal(
    fake_opensandbox: None,
    sandbox_manager: SandboxManager,
    session_factory: async_sessionmaker[Any],
    topic_scope_fixture: tuple[str, str, str, str],
) -> None:
    """A user's personal sandbox and their dedicated topic sandbox are
    distinct provider instances and survive a second lookup."""
    del fake_opensandbox  # autouse via parameter
    org_id, ws_id, user_id, topic_id = topic_scope_fixture

    personal = await sandbox_manager.get_or_create(
        user_id, org_id=org_id, workspace_id=ws_id, topic_id=None
    )
    topic_sb = await sandbox_manager.get_or_create(
        user_id, org_id=org_id, workspace_id=ws_id, topic_id=topic_id
    )
    assert personal.id != topic_sb.id

    # A second lookup in topic scope must reuse — not create a fresh row.
    again = await sandbox_manager.get_or_create(
        user_id, org_id=org_id, workspace_id=ws_id, topic_id=topic_id
    )
    assert again.id == topic_sb.id

    # And a second lookup in personal scope must reuse the personal row.
    again_personal = await sandbox_manager.get_or_create(
        user_id, org_id=org_id, workspace_id=ws_id, topic_id=None
    )
    assert again_personal.id == personal.id

    # DB-level sanity check: exactly two active rows for this user — one with
    # ``topic_id IS NULL``, one with ``topic_id = topic_id``.
    async with session_factory() as s:
        rows = (
            await s.execute(
                sa.text(
                    "SELECT topic_id, sandbox_id FROM user_sandboxes "
                    "WHERE user_id=:u AND workspace_id=:w "
                    "AND status IN ('provisioning','running')"
                ),
                {"u": user_id, "w": ws_id},
            )
        ).all()
    topic_ids = {r[0] for r in rows}
    sandbox_ids = {r[1] for r in rows}
    assert topic_ids == {None, topic_id}
    assert len(sandbox_ids) == 2  # distinct provider sandboxes


async def test_topic_sandbox_shared_across_participants(
    fake_opensandbox: None,
    sandbox_manager: SandboxManager,
    session_factory: async_sessionmaker[Any],
    topic_scope_fixture: tuple[str, str, str, str],
) -> None:
    """A second participant joining the same topic attaches to the existing
    topic sandbox rather than creating a duplicate. (Same workspace; only
    the topic_id matches — user_id on the active row may differ.)"""
    del fake_opensandbox
    org_id, ws_id, user_id, topic_id = topic_scope_fixture

    winner = await sandbox_manager.get_or_create(
        user_id, org_id=org_id, workspace_id=ws_id, topic_id=topic_id
    )

    # A different user looking up the SAME topic must get the same sandbox.
    # In real usage this user would be added as a topic participant, but the
    # sandbox manager is keyed on (org, ws, topic_id) and never reads the
    # participant table — so the lookup is enough to exercise the path.
    other_user = "user-other-participant"
    shared = await sandbox_manager.get_or_create(
        other_user, org_id=org_id, workspace_id=ws_id, topic_id=topic_id
    )
    assert shared.id == winner.id

    # DB-level: still exactly one active topic row.
    async with session_factory() as s:
        count = (
            await s.execute(
                sa.text(
                    "SELECT COUNT(*) FROM user_sandboxes "
                    "WHERE workspace_id=:w AND topic_id=:t "
                    "AND status IN ('provisioning','running')"
                ),
                {"w": ws_id, "t": topic_id},
            )
        ).scalar_one()
    assert count == 1
