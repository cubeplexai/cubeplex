"""Adversarial shared-memory E2E — Trust Model gates.

Plan task 8.2.

The screen-rejection halves are wired in tests/e2e/memory/test_api.py — the
adversarial workspace-create path is exercised there once Phase 6 lands,
which it does (commit f4479a5). What remains here is the harder claim:

  > Even if a poisoned workspace memory item bypasses the screen, the
  > sandbox/tool gate still refuses to run the destructive command.

That assertion needs:
- A sandbox-audit accessor (`_executed_commands(...)`) that surfaces what
  the sandbox actually executed during a turn. The current sandbox
  middleware does not expose this in tests.
- The SSE consumer helper from Task 8.1.

Once those exist, replace the body below with the verbatim plan-Task-8.2
assertion. Until then we keep the test file present so the layout matches
the plan and a future PR has a clear destination.
"""

import pytest

pytestmark = pytest.mark.skip(reason="requires sandbox-audit accessor + SSE consumer")


async def test_pre_existing_malicious_workspace_memory_does_not_bypass_gate() -> None:
    raise NotImplementedError
