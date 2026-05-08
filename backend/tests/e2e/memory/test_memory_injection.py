"""Memory injection E2E — does the agent actually use what it stored?

Plan task 8.1. Currently SKIPPED — depends on infrastructure not yet wired:

1. An SSE consumer helper that drives /messages and concatenates `text_delta`
   payloads (pattern in tests/e2e/test_agents.py — extract to a memory helper).
2. A `second_member_client` fixture in tests/e2e/memory/conftest.py that
   creates a second user + membership in the same workspace.
3. The agent stack is wired through this E2E entrypoint (memory middleware +
   cache markers + repo factory), which is true once Phase 4 lands but is
   only meaningful with a real LLM endpoint.

When the helpers exist, drop the skip and uncomment the test bodies in this
file (see plan Task 8.1 for the verbatim assertions).
"""

import pytest

pytestmark = pytest.mark.skip(reason="requires SSE consumer + second_member_client fixture")


async def test_personal_preference_applies_in_different_workspace() -> None:  # type: ignore[no-untyped-def]
    """Save a personal preference in ws_a, send a message in ws_b, assert
    the assistant reply respects the preference."""
    raise NotImplementedError


async def test_workspace_procedure_applies_for_second_member() -> None:
    """User A saves a workspace procedure; user B (same workspace) sees the
    agent apply it on a fresh conversation."""
    raise NotImplementedError
