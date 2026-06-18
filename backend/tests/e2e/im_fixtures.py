"""Shared seed/cleanup helpers for IM connector e2e tests.

Three test files (``test_im_feishu_ingress.py``, ``test_im_worker.py``,
``test_im_runtime_aggregates.py``) were each carrying their own copy of the
"insert org/workspace/user/credential/account, then delete them in the right
order on teardown" boilerplate (~50-60 lines per file). The variation that
mattered between them was only the credential value: the ingress test needs
a real encrypted payload (it goes through the route's decrypt path); the
worker / runtime-aggregate tests never decrypt, so a stub ``\\x00`` byte
works.

Each helper takes a live ``AsyncSession`` rather than yielding one — this
keeps callers in control of their own session_maker / engine lifecycle
(they already have one) and lets them batch the seed in the same
transaction as test-specific setup.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def im_seed_org_ws_user(
    session: AsyncSession,
    *,
    org_id: str,
    ws_id: str,
    user_id: str,
    email: str | None = None,
) -> None:
    """Insert organizations + workspaces + users rows. ON CONFLICT DO NOTHING."""
    await session.execute(
        text(
            "INSERT INTO organizations (id, name, slug, created_at)"
            " VALUES (:id, :id, :id, NOW()) ON CONFLICT (id) DO NOTHING"
        ),
        {"id": org_id},
    )
    await session.execute(
        text(
            "INSERT INTO workspaces (id, org_id, name, created_at)"
            " VALUES (:id, :org, :id, NOW()) ON CONFLICT (id) DO NOTHING"
        ),
        {"id": ws_id, "org": org_id},
    )
    await session.execute(
        text(
            "INSERT INTO users (id, email, hashed_password, is_active,"
            " is_superuser, is_verified, created_at, language)"
            " VALUES (:id, :email, 'x', true, false, false, NOW(), 'en')"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": user_id, "email": email or f"{user_id}@example.com"},
    )


async def im_seed_stub_credential(
    session: AsyncSession,
    *,
    credential_id: str,
    org_id: str,
    user_id: str | None = None,
    kind: str = "im_bot",
    name: str = "stub",
) -> None:
    """Insert a credentials row with ``\\x00`` ciphertext.

    For tests that never round-trip through decrypt (worker, aggregates).
    Tests that need a real ciphertext should call ``CredentialService.create``
    against the live app's encryption backend instead.
    """
    await session.execute(
        text(
            "INSERT INTO credentials (id, org_id, kind, name, value_encrypted,"
            " cred_metadata, created_by_user_id, created_at, updated_at)"
            " VALUES (:id, :org, :kind, :name, '\\x00'::bytea,"
            " '{}'::jsonb, :uid, NOW(), NOW())"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": credential_id, "org": org_id, "kind": kind, "name": name, "uid": user_id},
    )


async def im_seed_account(
    session: AsyncSession,
    *,
    account_id: str,
    org_id: str,
    ws_id: str,
    user_id: str,
    credential_id: str,
    external_account_id: str,
    delivery_mode: str = "webhook",
    platform: str = "feishu",
) -> None:
    """Insert an im_connector_accounts row."""
    await session.execute(
        text(
            "INSERT INTO im_connector_accounts (id, org_id, workspace_id,"
            " platform, external_account_id, acting_user_id, credential_id,"
            " delivery_mode, enabled, config, created_at, updated_at)"
            " VALUES (:id, :org, :ws, :platform, :ext, :uid, :cred,"
            " :mode, true, '{}'::jsonb, NOW(), NOW())"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": account_id,
            "org": org_id,
            "ws": ws_id,
            "platform": platform,
            "ext": external_account_id,
            "uid": user_id,
            "cred": credential_id,
            "mode": delivery_mode,
        },
    )


async def im_cleanup(
    session: AsyncSession,
    *,
    org_ids: list[str] | None = None,
    ws_ids: list[str] | None = None,
    user_ids: list[str] | None = None,
    credential_ids: list[str] | None = None,
    account_ids: list[str] | None = None,
    cleanup_conversations_in_ws: bool = False,
) -> None:
    """Idempotent reverse-FK-order cleanup.

    Pass whichever ids the test seeded. Children of each id (queue rows,
    receipts, thread links) are deleted first, then the id itself.
    ``cleanup_conversations_in_ws=True`` also drops conversations under
    ``ws_ids`` — only meaningful when the test wrote them.
    """
    if account_ids:
        for table in ("im_run_queue", "im_webhook_receipts", "im_thread_links"):
            await session.execute(
                text(f"DELETE FROM {table} WHERE account_id = ANY(:ids)"),
                {"ids": account_ids},
            )
        await session.execute(
            text("DELETE FROM im_connector_accounts WHERE id = ANY(:ids)"),
            {"ids": account_ids},
        )
    if credential_ids:
        await session.execute(
            text("DELETE FROM credentials WHERE id = ANY(:ids)"),
            {"ids": credential_ids},
        )
    if ws_ids and cleanup_conversations_in_ws:
        await session.execute(
            text("DELETE FROM conversations WHERE workspace_id = ANY(:ids)"),
            {"ids": ws_ids},
        )
    if ws_ids:
        await session.execute(
            text("DELETE FROM workspaces WHERE id = ANY(:ids)"),
            {"ids": ws_ids},
        )
    if user_ids:
        await session.execute(
            text("DELETE FROM users WHERE id = ANY(:ids)"),
            {"ids": user_ids},
        )
    if org_ids:
        await session.execute(
            text("DELETE FROM organizations WHERE id = ANY(:ids)"),
            {"ids": org_ids},
        )
