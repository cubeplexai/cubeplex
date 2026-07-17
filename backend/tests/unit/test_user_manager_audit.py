"""UserManager emits audit events on login and register."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cubeplex.auth.users import UserManager
from cubeplex.plugins import get_registry


@pytest.mark.asyncio
async def test_on_after_login_emits_audit_event() -> None:
    sink = AsyncMock()
    reg = get_registry()
    reg._audit_sinks = [sink]

    user = MagicMock(id=str(uuid4()), email="a@b.com")
    request = MagicMock(client=MagicMock(host="1.2.3.4"), headers={"user-agent": "test"})
    user_db = MagicMock()
    mgr = UserManager(user_db)

    await mgr.on_after_login(user, request)
    sink.record.assert_awaited_once()
    ev = sink.record.call_args.args[0]
    assert ev.action == "auth.login"
    assert ev.ip == "1.2.3.4"
    assert ev.user_agent == "test"
