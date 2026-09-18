"""E2E: sandbox_commands cap is atomic across concurrent sessions."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from cubeplex.repositories.sandbox_command import (
    SandboxCommandCapError,
    SandboxCommandRepository,
)
from cubeplex.repositories.user_sandbox import UserSandboxRepository


def _until() -> datetime:
    return datetime.now(UTC) + timedelta(seconds=30)


async def test_concurrent_reservations_respect_cap(
    session_factory: Any,
    seeded_org_ws_user: tuple[str, str, str, str],
) -> None:
    org_id, ws_a, _ws_b, user_id = seeded_org_ws_user
    async with session_factory() as session:
        us_repo = UserSandboxRepository(session, org_id=org_id, workspace_id=ws_a)
        us = await us_repo.reserve(
            user_id=user_id,
            image="ubuntu:22.04",
            ttl_seconds=600,
            scope_type="user",
            scope_id=user_id,
        )
        repo = SandboxCommandRepository(session, org_id=org_id, workspace_id=ws_a)
        for i in range(7):
            await repo.reserve(
                user_sandbox_id=us.id,
                conversation_id="conv-e2e",
                run_id="run-e2e",
                tool_call_id=f"tc-{i}",
                started_by_user_id=user_id,
                command="sleep 1",
                description="seed",
                notify_on_complete=True,
                owner_id="run:e2e",
                owner_until=_until(),
                log_path=f"/tmp/{i}.log",
            )
        user_sandbox_id = us.id

    async def _attempt(tag: str) -> str:
        async with session_factory() as session:
            repo = SandboxCommandRepository(session, org_id=org_id, workspace_id=ws_a)
            try:
                await repo.reserve(
                    user_sandbox_id=user_sandbox_id,
                    conversation_id="conv-e2e",
                    run_id="run-e2e",
                    tool_call_id=f"tc-{tag}",
                    started_by_user_id=user_id,
                    command="sleep 1",
                    description=tag,
                    notify_on_complete=True,
                    owner_id="run:e2e",
                    owner_until=_until(),
                    log_path=f"/tmp/{tag}.log",
                )
                return "ok"
            except SandboxCommandCapError:
                return "cap"

    results = await asyncio.gather(_attempt("a"), _attempt("b"))
    assert sorted(results) == ["cap", "ok"]
