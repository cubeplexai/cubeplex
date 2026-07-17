"""E2E: TriggerRepository + TriggerEventRepository — dedup and scope isolation."""

import secrets

from cubeplex.models import User
from cubeplex.models.credential import Credential
from cubeplex.models.trigger import Trigger, TriggerEvent
from cubeplex.repositories import (
    OrganizationRepository,
    TriggerEventRepository,
    TriggerRepository,
    WorkspaceRepository,
)


def _uid(prefix: str = "u") -> str:
    return f"{prefix}-{secrets.token_hex(4)}"


async def test_trigger_event_dedup(session_factory):
    async with session_factory() as session:
        org = await OrganizationRepository(session).create(
            name=f"Org {secrets.token_hex(4)}", slug=_uid("slug")
        )
        ws = await WorkspaceRepository(session).create(org_id=org.id, name="WS Dedup")

        user = User(id=_uid(), email=f"{secrets.token_hex(4)}@e.com", hashed_password="x")
        session.add(user)
        await session.flush()

        cred = Credential(
            id=_uid("cred"),
            org_id=org.id,
            kind="webhook_secret",
            name="t",
            value_encrypted=b"x",
        )
        session.add(cred)
        await session.commit()

        trigger_repo = TriggerRepository(session, org_id=org.id, workspace_id=ws.id)
        t = await trigger_repo.add(
            Trigger(
                name="My Trigger",
                source_type="webhook",
                target_type="conversation",
                target_ref={},
                run_as_user_id=user.id,
                current_secret_cred_id=cred.id,
            )
        )

        event_repo = TriggerEventRepository(session, org_id=org.id, workspace_id=ws.id)

        # First insert — should succeed and return the event with an id.
        e1 = TriggerEvent(
            trigger_id=t.id,
            source_type="webhook",
            dedup_key="abc",
            status="accepted",
        )
        result1 = await event_repo.insert_dedup(e1)
        assert result1 is not None
        assert result1.id is not None

        # Second insert with same dedup_key — must return None (duplicate).
        e2 = TriggerEvent(
            trigger_id=t.id,
            source_type="webhook",
            dedup_key="abc",
            status="accepted",
        )
        result2 = await event_repo.insert_dedup(e2)
        assert result2 is None


async def test_trigger_scope_isolation(session_factory):
    async with session_factory() as session:
        org = await OrganizationRepository(session).create(
            name=f"Org {secrets.token_hex(4)}", slug=_uid("slug")
        )
        ws = await WorkspaceRepository(session).create(org_id=org.id, name="WS Scope")

        user = User(id=_uid(), email=f"{secrets.token_hex(4)}@e.com", hashed_password="x")
        session.add(user)
        await session.flush()

        cred = Credential(
            id=_uid("cred"),
            org_id=org.id,
            kind="webhook_secret",
            name="t",
            value_encrypted=b"x",
        )
        session.add(cred)
        await session.commit()

        trigger_repo = TriggerRepository(session, org_id=org.id, workspace_id=ws.id)
        t = await trigger_repo.add(
            Trigger(
                name="Scoped Trigger",
                source_type="webhook",
                target_type="conversation",
                target_ref={},
                run_as_user_id=user.id,
                current_secret_cred_id=cred.id,
            )
        )

    # In a fresh session scoped to a different org/workspace — must not find the trigger.
    async with session_factory() as session:
        other_repo = TriggerRepository(session, org_id="org-OTHER", workspace_id="ws-OTHER")
        found = await other_repo.get(t.id)
        assert found is None
