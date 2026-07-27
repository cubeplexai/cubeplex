from datetime import datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from cubeplex.plugins import get_registry
from cubeplex.plugins.audit import audit_log


@pytest.mark.asyncio
async def test_audit_log_dispatches_to_all_sinks() -> None:
    sink_a = AsyncMock()
    sink_b = AsyncMock()
    reg = get_registry()
    reg._audit_sinks = [sink_a, sink_b]

    await audit_log(
        action="auth.login",
        user_id=uuid4(),
        org_id=uuid4(),
        workspace_id=None,
        ip="127.0.0.1",
    )
    sink_a.record.assert_awaited_once()
    sink_b.record.assert_awaited_once()
    event = sink_a.record.call_args.args[0]
    assert event.action == "auth.login"
    assert isinstance(event.timestamp, datetime)
