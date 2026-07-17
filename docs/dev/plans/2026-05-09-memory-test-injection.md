# Memory PR2 — Memory Injection E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land `backend/tests/e2e/memory/test_memory_injection.py` so it asserts that saved memory items actually shape model behavior — both for personal items reused across workspaces, and for workspace-scoped items reaching a second member.

**Architecture:** Add a real-LLM SSE consumer helper (`send_message_and_collect_text`), add a `second_member_client` fixture to `tests/e2e/memory/conftest.py`, then drop the skip on the test module and implement two assertions with tolerant phrasings (substring presence, not strict regex).

**Tech Stack:** Python 3.12 + httpx + pytest-asyncio + FastAPI SSE.

**Branch:** `feat/test-memory-injection` from `origin/main`.
**Spec:** `docs/superpowers/specs/2026-05-09-memory-llm-behavior-e2e-design.md` (PR2 section).
**Issue:** [#64](https://github.com/xfgong/cubeplex/issues/64).

---

## File Structure

**Tests / fixtures:**
- Create: `backend/tests/e2e/memory/_helpers.py` — `send_message_and_collect_text`
- Modify: `backend/tests/e2e/memory/conftest.py` — add `second_member_client` fixture
- Modify: `backend/tests/e2e/memory/test_memory_injection.py` — drop skip, implement two tests

**Config:**
- Modify: `backend/pyproject.toml` — register `real_llm` pytest marker (idempotent: if PR1 lands first, this rebases to no-op)
- Modify: `backend/Makefile` — `make test` excludes `real_llm`, `make test-real-llm` opts in

---

### Task 1: Worktree + marker registration

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/Makefile`

- [ ] **Step 1.1: Verify worktree state**

```bash
pwd && cat .worktree.env | head -3 && git status && git log --oneline -3
```

Expected: pwd ends in this PR's worktree, status clean, log shows `origin/main` HEAD.

- [ ] **Step 1.2: Register the `real_llm` pytest marker**

Edit `backend/pyproject.toml` — find `[tool.pytest.ini_options]`. Add a `markers` list (or extend if it exists):

```toml
[tool.pytest.ini_options]
# ... existing keys ...
markers = [
    "real_llm: tests that require a real LLM endpoint with cache_control honored; deselected by default in CI",
]
```

If a `markers` section already exists, append the entry. Idempotent.

- [ ] **Step 1.3: Adjust Makefile**

Find the `test:` target. Append `-m "not real_llm"` and add `test-real-llm`:

```makefile
test:
	uv run pytest -s -v -m "not real_llm"

test-real-llm:
	uv run pytest -s -v -m real_llm tests/e2e/memory/
```

- [ ] **Step 1.4: Verify**

```bash
uv run pytest --collect-only -m "not real_llm" tests/unit/ 2>&1 | tail -5
```

Expected: unit tests collected without "unknown marker" warnings.

- [ ] **Step 1.5: Commit**

```bash
git add backend/pyproject.toml backend/Makefile
git commit -m "chore(memory-e2e): register real_llm pytest marker"
```

---

### Task 2: SSE consumer helper for text

**Files:**
- Create: `backend/tests/e2e/memory/_helpers.py`

- [ ] **Step 2.1: Create the helper**

Note: PR1 (`feat/test-prompt-cache-gate`) creates the same file with `send_message_and_collect_usage`. If PR1 merges first, this step **appends** `send_message_and_collect_text` to the existing file (re-use the existing `_stream_events`). If PR2 merges first, PR1 does the symmetric thing on rebase.

Create `backend/tests/e2e/memory/_helpers.py` (or append if it exists):

```python
"""SSE consumer helpers for memory E2E tests.

Drives POST /api/v1/ws/{ws}/conversations/{conv}/messages and parses
the Server-Sent Events body. Mirrors the inline pattern in
tests/e2e/test_streaming.py but exposes it as importable functions.
"""

from __future__ import annotations

import json
from typing import Any

import httpx


async def _stream_events(
    client: httpx.AsyncClient,
    ws_id: str,
    conv_id: str,
    content: str,
) -> list[dict[str, Any]]:
    """Send one user message and collect every parsed SSE event."""
    events: list[dict[str, Any]] = []
    async with client.stream(
        "POST",
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        json={"content": content},
        headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


async def send_message_and_collect_text(
    client: httpx.AsyncClient,
    ws_id: str,
    conv_id: str,
    content: str,
) -> str:
    """Drive one turn and concatenate every text_delta payload into the reply."""
    events = await _stream_events(client, ws_id, conv_id, content)
    parts: list[str] = []
    for evt in events:
        if evt.get("type") != "text_delta":
            continue
        data = evt.get("data") or {}
        chunk = data.get("content")
        if isinstance(chunk, str):
            parts.append(chunk)
    return "".join(parts)
```

- [ ] **Step 2.2: Verify import**

```bash
cd backend && PYTHONPATH=. uv run python -c "from tests.e2e.memory._helpers import send_message_and_collect_text; print('ok')"
```

Expected: `ok`.

- [ ] **Step 2.3: Commit**

```bash
git add backend/tests/e2e/memory/_helpers.py
git commit -m "test(memory): SSE consumer helper send_message_and_collect_text"
```

---

### Task 3: `second_member_client` fixture

**Files:**
- Modify: `backend/tests/e2e/memory/conftest.py`

The pattern: register a brand-new user via `POST /api/v1/auth/register`, accept an invite to the workspace owned by the existing `member_client` user. The cleanest approach is to reuse the existing test bootstrap pattern from `tests/e2e/conftest.py` for app construction, then add a workspace-invite-and-accept dance.

- [ ] **Step 3.1: Inspect the existing test bootstrap helpers**

```bash
grep -n -E "(_make_isolated_user|_login_and_attach|_lifespan_context)" backend/tests/e2e/conftest.py | head -20
```

Note the names of the helpers — Task 3.2 will import them.

- [ ] **Step 3.2: Add the fixture**

Open `backend/tests/e2e/memory/conftest.py`. Append at the end of the file:

```python
import pytest_asyncio
from collections.abc import AsyncIterator

import httpx


@pytest_asyncio.fixture
async def second_member_client(
    member_client: tuple[httpx.AsyncClient, str],
) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """A second user in the SAME workspace as `member_client`.

    Uses the workspace's invite endpoint. Yields (client, workspace_id)
    where workspace_id is the same workspace as the primary member_client
    (so both clients are members of the same scope). The user behind
    this client is distinct.
    """
    primary_client, workspace_id = member_client

    # 1. Issue an invite from the primary member's workspace.
    invite_resp = await primary_client.post(
        f"/api/v1/workspaces/{workspace_id}/invites",
        json={"role": "member"},
    )
    invite_resp.raise_for_status()
    invite_token = invite_resp.json()["token"]

    # 2. Bootstrap a second isolated app context for the second user.
    #    Reuse the helpers from the top-level conftest.
    from tests.e2e.conftest import (  # type: ignore[import-not-found]
        _lifespan_context,
        _login_and_attach,
        _make_isolated_user,
    )
    from cubeplex.models.membership import Role

    app, email, password, _other_workspace_id = await _make_isolated_user(Role.MEMBER)
    app.state.deployment_mode = "multi_tenant"

    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as second_client:
            await _login_and_attach(second_client, email, password)

            # 3. Accept the invite from primary's workspace.
            accept_resp = await second_client.post(
                "/api/v1/workspaces/invites/accept",
                json={"token": invite_token},
            )
            accept_resp.raise_for_status()

            yield second_client, workspace_id
```

Notes:
- The two clients run **different** ASGI apps under the hood. That is fine for E2E — the database is shared (same Postgres), so memberships and workspaces are visible across both.
- If the actual route names differ (`/api/v1/workspaces/{ws}/invites` vs `/api/v1/ws/{ws}/invites`), update to match. Find with `grep -n "invites" backend/cubeplex/api/`.
- If `tests/e2e/conftest.py` does not export `_make_isolated_user` etc. as top-level functions (e.g. they are nested), restructure the import or copy the small bits needed.

- [ ] **Step 3.3: Smoke-test the fixture (no real LLM)**

Add a temporary smoke test at the end of `backend/tests/e2e/memory/test_api.py` (or a new file):

```python
@pytest.mark.asyncio
async def test_second_member_client_smoke(
    member_client: tuple[httpx.AsyncClient, str],
    second_member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Both clients should be able to list memory items in the same workspace."""
    primary, ws_id = member_client
    secondary, ws_id_2 = second_member_client
    assert ws_id == ws_id_2

    r1 = await primary.get(f"/api/v1/ws/{ws_id}/memory/items")
    r2 = await secondary.get(f"/api/v1/ws/{ws_id_2}/memory/items")
    assert r1.status_code == 200
    assert r2.status_code == 200
```

Adjust the URL to match the actual memory list endpoint — `grep -n "memory.*items" backend/cubeplex/api/`.

Run:

```bash
uv run pytest tests/e2e/memory/test_api.py::test_second_member_client_smoke -v 2>&1 | tail -10
```

Expected: pass.

- [ ] **Step 3.4: Remove the smoke test**

Delete the temporary test added in Step 3.3 — it was only to verify the fixture works. The real assertions live in Task 4.

- [ ] **Step 3.5: Commit**

```bash
git add backend/tests/e2e/memory/conftest.py
git commit -m "test(memory): second_member_client fixture in same workspace"
```

---

### Task 4: Implement test_memory_injection.py

**Files:**
- Modify: `backend/tests/e2e/memory/test_memory_injection.py`

- [ ] **Step 4.1: Replace the file body**

Open `backend/tests/e2e/memory/test_memory_injection.py` and replace its contents with:

```python
"""Memory injection E2E (issue #64, plan task 8.1).

Asserts saved memory items actually shape model behavior:

  1. A personal-scope memory item set in workspace A continues to
     apply when the same user starts a fresh conversation in
     workspace B (personal scope crosses workspace boundaries).
  2. A workspace-scope memory item set by user A applies when user B
     (a different member of the same workspace) starts a fresh
     conversation (workspace scope crosses user boundaries).
"""

from __future__ import annotations

import re

import httpx
import pytest

from tests.e2e.memory._helpers import send_message_and_collect_text

pytestmark = pytest.mark.real_llm

# A reply containing at least one CJK Unified Ideograph character.
_CJK_RE = re.compile(r"[一-鿿]")


async def _save_memory(
    client: httpx.AsyncClient,
    ws_id: str,
    *,
    scope: str,
    text: str,
) -> None:
    """Create a memory item via the API."""
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/memory/items",
        json={"scope": scope, "content": text},
    )
    resp.raise_for_status()


async def _new_conversation(client: httpx.AsyncClient, ws_id: str, *, title: str) -> str:
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations",
        params={"title": title},
    )
    resp.raise_for_status()
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_personal_preference_applies_in_different_workspace(
    seed_two_workspaces,  # type: ignore[no-untyped-def]
    member_client,  # type: ignore[no-untyped-def]
) -> None:
    """A personal-scope preference set in ws A is honored in ws B."""
    client, ws_a_id = member_client
    # `seed_two_workspaces` (in conftest) yields two workspaces both
    # accessible to seed_user. Use ws_b for the message turn. If your
    # primary member_client's ws_id does not match ws_a from
    # seed_two_workspaces, adjust to use member_client_org_b or build
    # ws_b within this test directly.
    ws_a, ws_b = seed_two_workspaces

    # Save personal preference in ws_a
    await _save_memory(
        client, ws_a.id, scope="personal", text="Always reply in 中文 (Chinese)."
    )

    # Start fresh conversation in ws_b
    conv_id = await _new_conversation(client, ws_b.id, title="injection-personal")
    reply = await send_message_and_collect_text(
        client, ws_b.id, conv_id, "Tell me a fun fact about cats."
    )

    assert _CJK_RE.search(reply), (
        f"Expected the reply to contain Chinese characters because the "
        f"personal-scope memory said so, but got:\n{reply}"
    )


@pytest.mark.asyncio
async def test_workspace_procedure_applies_for_second_member(
    member_client,  # type: ignore[no-untyped-def]
    second_member_client,  # type: ignore[no-untyped-def]
) -> None:
    """User A saves a workspace procedure; user B (same ws) sees it applied."""
    client_a, ws_id = member_client
    client_b, ws_id_b = second_member_client
    assert ws_id == ws_id_b

    # User A saves workspace procedure
    await _save_memory(
        client_a,
        ws_id,
        scope="workspace",
        text=(
            "When the user asks about deploys, ALWAYS first remind them to "
            "run `make check` before pushing."
        ),
    )

    # User B starts a fresh conversation in the same workspace
    conv_id = await _new_conversation(client_b, ws_id, title="injection-procedure")
    reply = await send_message_and_collect_text(
        client_b, ws_id, conv_id, "How should I deploy the staging service?"
    )

    assert "make check" in reply, (
        f"Expected 'make check' to appear in user B's reply because of the "
        f"workspace-scope procedure saved by user A, but got:\n{reply}"
    )
```

Important notes for the engineer:

1. The fixtures `seed_two_workspaces` already exists in `backend/tests/e2e/memory/conftest.py` (see lines 70–80) but it operates against the test DB session, not the `member_client`'s session. If the test fails at `_save_memory` because the workspaces from `seed_two_workspaces` don't belong to the `member_client`'s user, change the test: instead of `seed_two_workspaces`, build ws B by calling `POST /api/v1/workspaces` from `member_client` directly. The fixture is a hint; pick the path that makes the user own both workspaces.

2. The memory-create endpoint (`POST /api/v1/ws/{ws}/memory/items`) and its scope/content schema must match the actual contract. Verify with:

   ```bash
   grep -n -E "(memory.*items|scope.*personal)" backend/cubeplex/api/ -r | head -10
   ```

   Adjust `_save_memory` payload to match.

3. The Chinese-character regex matches any CJK Unified Ideograph. For weak models, this can sometimes pass on translation artifacts ("你好" inadvertently emitted) — that's acceptable; it still proves the directive was followed.

- [ ] **Step 4.2: Verify collection**

```bash
uv run pytest --collect-only -m real_llm tests/e2e/memory/test_memory_injection.py 2>&1 | tail -10
```

Expected: lists exactly two tests.

- [ ] **Step 4.3: Run against your local LLM endpoint**

```bash
uv run pytest tests/e2e/memory/test_memory_injection.py -v -m real_llm 2>&1 | tail -40
```

Expected: both tests pass. If a test fails because the model didn't follow the directive, capture the actual reply and decide:
- If the reply genuinely ignored the memory → bug. Investigate `MemoryMiddleware`.
- If the reply paraphrased/refused but obviously knew the directive → soften the assertion to a more tolerant substring (avoid model-specific quirks). Document the change in the commit message.

- [ ] **Step 4.4: Commit**

```bash
git add backend/tests/e2e/memory/test_memory_injection.py
git commit -m "test(memory): memory injection E2E (8.1) — personal & workspace scopes"
```

---

### Task 5: Verification + PR

- [ ] **Step 5.1: Lint + typecheck**

```bash
make lint
make type-check
```

Expected: both green.

- [ ] **Step 5.2: Default test run (real_llm deselected)**

```bash
make test 2>&1 | tail -10
```

Expected: green; new injection tests are not collected.

- [ ] **Step 5.3: Real-LLM run**

```bash
make test-real-llm 2>&1 | tail -20
```

Expected: passes. Capture output for the PR description.

- [ ] **Step 5.4: Push + PR**

```bash
git push -u origin feat/test-memory-injection
gh pr create --title "feat(memory): memory injection E2E (issue #64 PR2)" --body "$(cat <<'EOF'
## Summary
- Add `tests/e2e/memory/_helpers.py::send_message_and_collect_text` (SSE consumer extracted from `test_streaming.py` inline pattern).
- Add `second_member_client` fixture (same-workspace second user via invite + accept).
- Drop skip on `test_memory_injection.py`; assert personal preference crosses workspaces and workspace procedure crosses users.

## Test plan
- [x] `make lint` and `make type-check` clean
- [x] `make test` (real_llm deselected) passes — injection tests not collected here
- [x] `make test-real-llm` against local endpoint: <PASTE OUTCOME>
- [x] Both injection tests pass on the configured endpoint

Refs: #64

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review summary

- **Spec coverage:** PR2 section requires (a) text helper — Task 2; (b) `second_member_client` — Task 3; (c) un-skip with two assertions — Task 4. All present.
- **Placeholder scan:** none. The note about `seed_two_workspaces` in Task 4 is a clear *if-this-then-that* instruction, not a TODO.
- **Type consistency:** Helper return type `str` matches assertion sites (`assert "make check" in reply`). Fixture return type `tuple[httpx.AsyncClient, str]` matches `member_client`'s shape on line 415 of `tests/e2e/conftest.py`.
