"""Turn usage panel reads the last LLM call, not the sum of the run."""

from datetime import UTC, datetime, timedelta

import pytest

from cubeplex.services.usage import get_turn_usage
from tests.e2e.billing_fixtures import _db_session, _seed_events

pytestmark = pytest.mark.e2e

_ORG = "org-turn-usage-last"
_WS = "ws-turn-usage-last"
_USER = "usr-turn-usage-last"
_CONV = "conv-turn-usage-last"


@pytest.mark.asyncio
async def test_get_turn_usage_returns_last_llm_call_not_sum() -> None:
    t0 = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    async with _db_session() as session:
        await _seed_events(
            session,
            org_id=_ORG,
            rows=[
                {
                    "workspace_id": _WS,
                    "user_id": _USER,
                    "conversation_id": _CONV,
                    "provider": "cubeplex",
                    "model_id": "grok-4.6",
                    "started_at": t0,
                    "cost_micro": 100,
                    "input": 100,
                    "output": 1282,
                    "cache_read": 80,
                    "cache_write": 0,
                },
                {
                    "workspace_id": _WS,
                    "user_id": _USER,
                    "conversation_id": _CONV,
                    "provider": "cubeplex",
                    "model_id": "grok-4.6",
                    "started_at": t0 + timedelta(minutes=1),
                    "cost_micro": 20,
                    "input": 50,
                    "output": 17,
                    "cache_read": 40,
                    "cache_write": 0,
                },
            ],
        )
        turn, context_tokens = await get_turn_usage(session, _CONV, after=t0)
        assert turn == {
            "input_tokens": 50,
            "output_tokens": 17,
            "cache_read_tokens": 40,
            "cache_write_tokens": 0,
        }
        assert context_tokens == 100
