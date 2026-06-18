"""SandboxManager-level integration test for polymorphic-scope sandbox routing.

Repo unit tests alone don't catch the bugs where ``LazySandbox`` /
``SandboxManager`` forget to thread ``(scope_type, scope_id)`` through to
the lookup / reserve callsites. This file exercises the manager
end-to-end against the real test Postgres DB (so the partial unique index
installed by the polymorphic-scope migration actually fires) with the
OpenSandbox provider faked out.
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
        scope_type="user",
        scope_id=user_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_id,
    )
    topic_sb = await sandbox_manager.get_or_create(
        scope_type="topic",
        scope_id=topic_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_id,
    )
    assert personal.id != topic_sb.id

    # A second lookup in topic scope must reuse — not create a fresh row.
    again = await sandbox_manager.get_or_create(
        scope_type="topic",
        scope_id=topic_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_id,
    )
    assert again.id == topic_sb.id

    # And a second lookup in personal scope must reuse the personal row.
    again_personal = await sandbox_manager.get_or_create(
        scope_type="user",
        scope_id=user_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_id,
    )
    assert again_personal.id == personal.id

    # DB-level sanity check: exactly two active rows for this user — one with
    # ``scope_type='user'``, one with ``scope_type='topic'``.
    async with session_factory() as s:
        rows = (
            await s.execute(
                sa.text(
                    "SELECT scope_type, scope_id, sandbox_id FROM user_sandboxes "
                    "WHERE user_id=:u AND workspace_id=:w "
                    "AND status IN ('provisioning','running')"
                ),
                {"u": user_id, "w": ws_id},
            )
        ).all()
    scope_keys = {(r[0], r[1]) for r in rows}
    sandbox_ids = {r[2] for r in rows}
    assert scope_keys == {("user", user_id), ("topic", topic_id)}
    assert len(sandbox_ids) == 2  # distinct provider sandboxes


async def test_topic_sandbox_shared_across_participants(
    fake_opensandbox: None,
    sandbox_manager: SandboxManager,
    session_factory: async_sessionmaker[Any],
    topic_scope_fixture: tuple[str, str, str, str],
) -> None:
    """A second participant joining the same topic attaches to the existing
    topic sandbox rather than creating a duplicate."""
    del fake_opensandbox
    org_id, ws_id, user_id, topic_id = topic_scope_fixture

    winner = await sandbox_manager.get_or_create(
        scope_type="topic",
        scope_id=topic_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_id,
    )

    # A different user looking up the SAME topic must get the same sandbox.
    other_user = "user-other-participant"
    shared = await sandbox_manager.get_or_create(
        scope_type="topic",
        scope_id=topic_id,
        user_id=other_user,
        org_id=org_id,
        workspace_id=ws_id,
    )
    assert shared.id == winner.id

    # DB-level: still exactly one active topic row.
    async with session_factory() as s:
        count = (
            await s.execute(
                sa.text(
                    "SELECT COUNT(*) FROM user_sandboxes "
                    "WHERE workspace_id=:w AND scope_type='topic' AND scope_id=:t "
                    "AND status IN ('provisioning','running')"
                ),
                {"w": ws_id, "t": topic_id},
            )
        ).scalar_one()
    assert count == 1


async def test_standalone_group_chat_scope_distinct_from_user(
    fake_opensandbox: None,
    sandbox_manager: SandboxManager,
    session_factory: async_sessionmaker[Any],
    topic_scope_fixture: tuple[str, str, str, str],
) -> None:
    """A standalone group chat (``scope_type='conversation'``) is keyed
    separately from any user-scoped sandbox the same caller already has."""
    del fake_opensandbox
    org_id, ws_id, user_id, _topic_id = topic_scope_fixture

    personal = await sandbox_manager.get_or_create(
        scope_type="user",
        scope_id=user_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_id,
    )
    conv_id = "conv-group-1"
    group = await sandbox_manager.get_or_create(
        scope_type="conversation",
        scope_id=conv_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_id,
    )
    assert personal.id != group.id

    async with session_factory() as s:
        rows = (
            await s.execute(
                sa.text(
                    "SELECT scope_type, scope_id FROM user_sandboxes "
                    "WHERE user_id=:u AND workspace_id=:w "
                    "AND status IN ('provisioning','running')"
                ),
                {"u": user_id, "w": ws_id},
            )
        ).all()
    assert {(r[0], r[1]) for r in rows} == {
        ("user", user_id),
        ("conversation", conv_id),
    }
