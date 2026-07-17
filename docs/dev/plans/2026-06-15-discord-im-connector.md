# Discord IM Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Discord as the second IM platform in cubeplex, with a platform-registry mechanism that replaces the current hard-coded Feishu wiring so future platforms slot in cleanly.

**Architecture:** Platform-registry pattern: `PlatformConnector` protocol in `im/registry.py` with `register_platform()`/`get_platform()`. Each platform registers itself in its `__init__.py`. The outbound rendering path gets an `OpDispatcher` protocol extracted from the current `_dispatch_op` monolith, with `FeishuOpDispatcher` wrapping existing CardKit logic and `DiscordOpDispatcher` doing message edits. Distributed Redis lease (`SETNX` with 30s TTL, 15s sweep) coordinates Gateway ownership across API instances.

**Tech Stack:** discord.py (Gateway), FastAPI, Redis (lease coordination), SQLAlchemy/SQLModel (existing models unchanged), React/Next.js (frontend wizard descriptor)

**Spec:** `docs/dev/specs/2026-06-15-discord-im-connector-design.md`

**Worktree:** `/home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/`

---

## File Structure

### New Files

| File | Purpose |
|------|---------|
| `backend/cubeplex/im/registry.py` | `PlatformConnector` protocol + `register_platform()`/`get_platform()` registry |
| `backend/cubeplex/im/card_model.py` | Shared accumulation models lifted from `im/feishu/card_model.py` |
| `backend/cubeplex/im/op_dispatcher.py` | `OpDispatcher` protocol |
| `backend/cubeplex/im/feishu/op_dispatcher.py` | `FeishuOpDispatcher` — extracted from `OutboundRunTailer._dispatch_op` |
| `backend/cubeplex/im/discord/__init__.py` | Register `DiscordPlatform` connector |
| `backend/cubeplex/im/discord/connector.py` | `DiscordConnector` — `parse_inbound`, send/edit message, reactions |
| `backend/cubeplex/im/discord/gateway.py` | discord.py Bot lifecycle (start/stop per account) |
| `backend/cubeplex/im/discord/renderer.py` | `DiscordOpDispatcher` (uses shared `RenderState` from `im/types.py`) |
| `backend/cubeplex/im/discord/interactions.py` | Button interaction handling (AskUser/SandboxConfirm resume) |
| `backend/cubeplex/im/discord/commands.py` | `/new` `/reset` slash commands |
| `backend/tests/unit/im/test_registry.py` | Registry unit tests |
| `backend/tests/unit/im/test_card_model_lift.py` | Verify card_model lift preserves behavior |
| `backend/tests/unit/im/test_op_dispatcher_protocol.py` | OpDispatcher protocol conformance |
| `backend/tests/unit/im/discord/test_connector.py` | Discord connector parse_inbound tests |
| `backend/tests/unit/im/discord/test_renderer.py` | Discord renderer tests |
| `backend/tests/unit/im/test_gateway_lease.py` | Distributed lease mechanism tests |
| `frontend/packages/web/components/im/ImConnectWizard/platforms/discord.ts` | Platform descriptor |

### Modified Files

| File | Change |
|------|--------|
| `backend/cubeplex/im/types.py` | Add `make_channel_scope()`, decouple `RenderState` from `CardState` import |
| `backend/cubeplex/im/feishu/card_model.py` | Re-export from shared `im/card_model.py` |
| `backend/cubeplex/im/outbound.py` | Import from `im/card_model.py` instead of `im/feishu/card_model.py`; inject `OpDispatcher` into `OutboundRunTailer` |
| `backend/tests/im/test_awaiting_responder.py` | Update `OutboundRunTailer` construction: `cardkit=` → `dispatcher=` (4 call sites) |
| `backend/tests/im/test_tailer_dispatch.py` | Update `OutboundRunTailer` construction: `cardkit=` → `dispatcher=` (1 call site) |
| `backend/tests/e2e/test_im_end_to_end.py` | Update `OutboundRunTailer` construction: `cardkit=` → `dispatcher=` (2 call sites) |
| `backend/cubeplex/im/artifacts.py` | Import from `im/card_model.py` instead of `im/feishu/card_model.py` |
| `backend/cubeplex/im/runtime.py` | Registry-based startup + distributed Redis lease sweep |
| `backend/cubeplex/api/schemas/im_connector.py` | Add `ConnectDiscordAccountIn` |
| `backend/cubeplex/api/routes/v1/ws_im.py` | Dispatch connect by platform field |
| `backend/cubeplex/services/im_connector.py` | Add `connect_discord()` |
| `frontend/packages/core/src/api/im.ts` | Add `ConnectDiscordAccountIn`, union body type, extend `delivery_mode` |
| `frontend/packages/web/components/im/ImConnectWizard/platforms/types.ts` | Extend `PlatformDescriptor.id` union, generalize `buildPayload` return type |
| `frontend/packages/web/components/im/ImConnectWizard/platforms/index.ts` | Export + register `discordDescriptor` |
| `frontend/packages/web/components/im/PlatformLogo.tsx` | Add Discord logo |

---

## Phase 1: Infrastructure Refactoring

### Task 1: Platform Registry

**Files:**
- Create: `backend/cubeplex/im/registry.py`
- Test: `backend/tests/unit/im/test_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/im/test_registry.py
from __future__ import annotations

from typing import Any

import pytest

from cubeplex.im.registry import (
    PlatformConnector,
    get_platform,
    register_platform,
)


class FakePlatform:
    """Minimal PlatformConnector implementation for testing."""

    def parse_inbound(self, raw: dict[str, Any]) -> Any:
        return None

    async def build_tailer(
        self, *, run_id: str, queue_item: Any, account: Any, **kwargs: Any
    ) -> Any:
        return None

    async def on_account_enabled(self, account: Any, **kwargs: Any) -> None:
        pass

    async def on_account_disabled(self, account: Any, **kwargs: Any) -> None:
        pass


def test_register_and_get() -> None:
    register_platform("test_plat", FakePlatform())
    connector = get_platform("test_plat")
    assert connector is not None
    assert isinstance(connector, FakePlatform)


def test_get_unknown_raises() -> None:
    with pytest.raises(KeyError, match="no_such_platform"):
        get_platform("no_such_platform")


def test_double_register_raises() -> None:
    register_platform("double_test", FakePlatform())
    with pytest.raises(ValueError, match="double_test"):
        register_platform("double_test", FakePlatform())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run pytest tests/unit/im/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cubeplex.im.registry'`

- [ ] **Step 3: Implement the registry**

```python
# backend/cubeplex/im/registry.py
"""Platform connector registry.

Each IM platform registers itself at import time via ``register_platform()``.
The runtime and worker look up connectors by ``account.platform`` via
``get_platform()``.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PlatformConnector(Protocol):
    """Protocol that each IM platform implements."""

    def parse_inbound(self, raw: dict[str, Any]) -> Any: ...

    async def build_tailer(
        self, *, run_id: str, queue_item: Any, account: Any, **kwargs: Any
    ) -> Any: ...

    async def on_account_enabled(self, account: Any, **kwargs: Any) -> None: ...

    async def on_account_disabled(self, account: Any, **kwargs: Any) -> None: ...


_registry: dict[str, PlatformConnector] = {}


def register_platform(name: str, connector: PlatformConnector) -> None:
    if name in _registry:
        raise ValueError(f"platform already registered: {name}")
    _registry[name] = connector


def get_platform(name: str) -> PlatformConnector:
    try:
        return _registry[name]
    except KeyError:
        raise KeyError(f"unknown IM platform: {name}") from None


def registered_platforms() -> list[str]:
    return list(_registry.keys())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run pytest tests/unit/im/test_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add backend/cubeplex/im/registry.py backend/tests/unit/im/test_registry.py && git commit -m "feat(im): add platform connector registry"
```

---

### Task 2: Lift CardState to Shared Module

Move `CardState`, `ToolStep`, `ArtifactItem`, `PendingInput`, `SubAgentRow` from `im/feishu/card_model.py` into `im/card_model.py`. The Feishu module re-exports for backward compatibility during the transition, then all importers are updated.

**Files:**
- Create: `backend/cubeplex/im/card_model.py`
- Modify: `backend/cubeplex/im/feishu/card_model.py`
- Modify: `backend/cubeplex/im/outbound.py` (4 import sites: lines 98, 192, 244, 321)
- Modify: `backend/cubeplex/im/artifacts.py` (line 19)
- Modify: `backend/cubeplex/im/types.py` (line 15)
- Test: `backend/tests/unit/im/test_card_model_lift.py`

- [ ] **Step 1: Write the verification test**

```python
# backend/tests/unit/im/test_card_model_lift.py
"""Verify shared card_model exports match the original feishu card_model."""
from __future__ import annotations

from cubeplex.im.card_model import (
    ArtifactItem,
    CardState,
    PendingInput,
    SubAgentRow,
    ToolStep,
)


def test_card_state_basic() -> None:
    cs = CardState(bot_name="test", run_id="r1")
    assert cs.streaming_content == ""
    assert cs.tool_steps == []
    assert cs.finalized is False


def test_tool_step_lifecycle() -> None:
    ts = ToolStep(id="t1", name="search", args={"q": "hello"})
    assert ts.status == "running"
    ts.mark_succeeded(result="ok", elapsed_ms=100)
    assert ts.status == "succeeded"
    assert ts.elapsed_ms == 100


def test_card_state_find_tool() -> None:
    cs = CardState(bot_name="test", run_id="r1")
    cs.tool_steps.append(ToolStep(id="t1", name="search", args={}))
    assert cs.find_tool("t1") is not None
    assert cs.find_tool("t2") is None


def test_feishu_reexport() -> None:
    """The feishu module must still re-export the same classes."""
    from cubeplex.im.feishu.card_model import CardState as FeishuCardState
    from cubeplex.im.feishu.card_model import ToolStep as FeishuToolStep

    assert FeishuCardState is CardState
    assert FeishuToolStep is ToolStep
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run pytest tests/unit/im/test_card_model_lift.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cubeplex.im.card_model'`

- [ ] **Step 3: Create shared card_model.py**

Copy the full content of `backend/cubeplex/im/feishu/card_model.py` to `backend/cubeplex/im/card_model.py`. The file is identical — same docstring, same classes, same `__all__`.

- [ ] **Step 4: Replace feishu/card_model.py with re-exports**

```python
# backend/cubeplex/im/feishu/card_model.py
"""Re-export shared card models for backward compatibility.

The canonical definitions now live in ``cubeplex.im.card_model``.
"""
from cubeplex.im.card_model import (
    ArtifactItem,
    CardState,
    PendingInput,
    SubAgentRow,
    ToolStep,
)

__all__ = [
    "ArtifactItem",
    "CardState",
    "PendingInput",
    "SubAgentRow",
    "ToolStep",
]
```

- [ ] **Step 5: Update imports in outbound.py**

In `backend/cubeplex/im/outbound.py`, change 4 import sites:

Line 98: `from cubeplex.im.feishu.card_model import SubAgentRow, ToolStep` → `from cubeplex.im.card_model import SubAgentRow, ToolStep`

Line 192: `from cubeplex.im.feishu.card_model import ArtifactItem` → `from cubeplex.im.card_model import ArtifactItem`

Line 244: `from cubeplex.im.feishu.card_model import PendingInput` → `from cubeplex.im.card_model import PendingInput`

Line 321: `from cubeplex.im.feishu.card_model import PendingInput` → `from cubeplex.im.card_model import PendingInput`

- [ ] **Step 6: Update import in artifacts.py**

In `backend/cubeplex/im/artifacts.py`, line 19:
`from cubeplex.im.feishu.card_model import ArtifactItem, CardState` → `from cubeplex.im.card_model import ArtifactItem, CardState`

- [ ] **Step 7: Update import in types.py**

In `backend/cubeplex/im/types.py`, line 15:
`from cubeplex.im.feishu.card_model import CardState` → `from cubeplex.im.card_model import CardState`

- [ ] **Step 8: Run tests to verify**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run pytest tests/unit/im/test_card_model_lift.py tests/unit/im/ -v`
Expected: PASS

- [ ] **Step 9: Run existing IM tests to verify no regressions**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run pytest tests/unit/im/ -v`
Expected: All PASS

- [ ] **Step 10: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add backend/cubeplex/im/card_model.py backend/cubeplex/im/feishu/card_model.py backend/cubeplex/im/outbound.py backend/cubeplex/im/artifacts.py backend/cubeplex/im/types.py backend/tests/unit/im/test_card_model_lift.py && git commit -m "refactor(im): lift CardState to shared im/card_model.py"
```

---

### Task 3: Extract OpDispatcher Protocol

Extract `OutboundRunTailer._dispatch_op` into an `OpDispatcher` protocol. Create `FeishuOpDispatcher` wrapping the existing CardKit logic. Inject the dispatcher into `OutboundRunTailer` instead of having it call CardKit directly.

**Files:**
- Create: `backend/cubeplex/im/op_dispatcher.py`
- Create: `backend/cubeplex/im/feishu/op_dispatcher.py`
- Modify: `backend/cubeplex/im/outbound.py`
- Test: `backend/tests/unit/im/test_op_dispatcher_protocol.py`

- [ ] **Step 1: Write the protocol test**

```python
# backend/tests/unit/im/test_op_dispatcher_protocol.py
from __future__ import annotations

from typing import Any

import pytest

from cubeplex.im.op_dispatcher import OpDispatcher


class FakeDispatcher:
    """Minimal OpDispatcher for protocol conformance testing."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def dispatch_create(self, state: Any) -> bool:
        self.calls.append("create")
        return True

    async def dispatch_stream(self, state: Any, text: str) -> bool:
        self.calls.append(f"stream:{text[:20]}")
        return True

    async def dispatch_patch(self, state: Any) -> bool:
        self.calls.append("patch")
        return True

    async def dispatch_finalize(self, state: Any) -> bool:
        self.calls.append("finalize")
        return True

    async def emergency_text(self, text: str) -> None:
        self.calls.append(f"emergency:{text[:20]}")

    async def aclose(self) -> None:
        self.calls.append("aclose")


def test_fake_is_op_dispatcher() -> None:
    d = FakeDispatcher()
    assert isinstance(d, OpDispatcher)


@pytest.mark.asyncio
async def test_fake_dispatch_create() -> None:
    d = FakeDispatcher()
    result = await d.dispatch_create(None)
    assert result is True
    assert d.calls == ["create"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run pytest tests/unit/im/test_op_dispatcher_protocol.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create OpDispatcher protocol**

```python
# backend/cubeplex/im/op_dispatcher.py
"""OpDispatcher protocol — platform-specific outbound rendering.

Each platform implements this protocol. The OutboundRunTailer calls
dispatch methods without knowing whether the target is CardKit,
Discord message edits, or something else.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class OpDispatcher(Protocol):
    async def dispatch_create(self, state: Any) -> bool: ...
    async def dispatch_stream(self, state: Any, text: str) -> bool: ...
    async def dispatch_patch(self, state: Any) -> bool: ...
    async def dispatch_finalize(self, state: Any) -> bool: ...
    async def emergency_text(self, text: str) -> None: ...
    async def aclose(self) -> None: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run pytest tests/unit/im/test_op_dispatcher_protocol.py -v`
Expected: PASS

- [ ] **Step 5: Create FeishuOpDispatcher**

Extract the body of `OutboundRunTailer._dispatch_op` (lines 601–758 of `outbound.py`) into `FeishuOpDispatcher`. Also extract `_emergency_text`, `_maybe_surface_pending_via_emergency`, and `_emergency_card_create_fallback`.

```python
# backend/cubeplex/im/feishu/op_dispatcher.py
"""Feishu-specific OpDispatcher — wraps CardKit calls.

Extracted from OutboundRunTailer._dispatch_op so the tailer no longer
hard-codes Feishu rendering logic.
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from cubeplex.im.outbound import _FloodSignal, note_edit_success, note_flood_strike
from cubeplex.im.types import RenderState


class FeishuOpDispatcher:
    """Dispatches outbound ops to Feishu CardKit."""

    def __init__(
        self,
        *,
        connector: Any,
        state: RenderState,
        cardkit: Any,
    ) -> None:
        self._connector = connector
        self._state = state
        self._cardkit = cardkit

    async def dispatch_create(self, state: Any) -> bool:
        from cubeplex.im.feishu.card_renderer import render

        s = self._state
        if s.card_unavailable:
            return False
        card_json = render(s.card_state)
        try:
            card_id = await self._cardkit.create_entity(card_json)
        except Exception:
            logger.warning(
                "[outbound] CardKit create_entity failed; engaging emergency text",
                exc_info=True,
            )
            s.card_unavailable = True
            await self._emergency_card_create_fallback()
            return False
        s.card_id = card_id
        s.card_state.advance_seq()
        try:
            msg_id = await self._connector.send_card_init_message(card_id)
        except Exception:
            logger.warning("[outbound] send_card_init_message raised", exc_info=True)
            msg_id = None
        s.bot_message_id = msg_id
        if msg_id is None:
            logger.warning(
                "[outbound] send_card_init_message returned no message_id;"
                " engaging emergency text"
            )
            s.card_unavailable = True
            await self._emergency_card_create_fallback()
            return False
        return True

    async def dispatch_stream(self, state: Any, text: str) -> bool:
        from cubeplex.im.feishu.card_renderer import optimize_markdown_style as _optimize

        s = self._state
        if s.card_id is None or s.card_unavailable:
            return False
        seq = s.card_state.advance_seq()
        sanitized = _optimize(text, citation_index=s.card_state.citation_index)
        try:
            await self._cardkit.stream_text(
                card_id=s.card_id,
                element_id="streaming_content",
                content=sanitized,
                sequence=seq,
            )
            note_edit_success(s)
            return True
        except _FloodSignal:
            note_flood_strike(s)
            return False
        except Exception:
            logger.warning("[outbound] stream_text failed", exc_info=True)
            return False

    async def dispatch_patch(self, state: Any) -> bool:
        from cubeplex.im.feishu.card_renderer import render

        s = self._state
        if s.card_id is None or s.card_unavailable:
            return False
        seq = s.card_state.advance_seq()
        try:
            await self._cardkit.patch_card(
                card_id=s.card_id,
                card_json=render(s.card_state),
                sequence=seq,
            )
            note_edit_success(s)
            return True
        except _FloodSignal:
            note_flood_strike(s)
            await self._maybe_surface_pending_via_emergency()
            return False
        except Exception:
            logger.warning("[outbound] patch_card failed", exc_info=True)
            await self._maybe_surface_pending_via_emergency()
            return False

    async def dispatch_finalize(self, state: Any) -> bool:
        from cubeplex.im.feishu.card_renderer import render

        s = self._state
        if s.card_id is None or s.card_unavailable:
            if s.card_state.error:
                await self.emergency_text(f"⚠️ {s.card_state.error}")
            elif s.card_state.streaming_content:
                await self.emergency_text(s.card_state.streaming_content[:4000])
            return False
        seq = s.card_state.advance_seq()
        try:
            delivered = bool(
                await self._cardkit.finalize(
                    card_id=s.card_id,
                    card_json=render(s.card_state),
                    sequence=seq,
                )
            )
        except Exception:
            logger.warning(
                "[outbound] cardkit.finalize raised; falling back to emergency text",
                exc_info=True,
            )
            delivered = False
        if not delivered:
            logger.warning(
                "[outbound] CardKit finalize gave up for card_id={};"
                " surfacing answer via emergency text",
                s.card_id,
            )
            if s.card_state.error:
                await self.emergency_text(f"⚠️ {s.card_state.error}")
            elif s.card_state.streaming_content:
                await self.emergency_text(s.card_state.streaming_content[:4000])
        return delivered

    async def emergency_text(self, text: str) -> None:
        try:
            await self._connector._send_emergency_text(text)
        except Exception:
            logger.warning("[outbound] emergency text send failed", exc_info=True)

    async def aclose(self) -> None:
        close = getattr(self._cardkit, "aclose", None)
        if callable(close):
            try:
                await close()
            except Exception:
                logger.warning("[outbound] cardkit.aclose() raised", exc_info=True)

    async def _maybe_surface_pending_via_emergency(self) -> None:
        state = self._state
        pending = state.card_state.pending_input
        if pending is None or pending.resolved_choice is not None:
            return
        qid = pending.question_id or ""
        if not qid or state.pending_prompt_emergency_sent_qid == qid:
            return
        state.pending_prompt_emergency_sent_qid = qid
        kind_label = "❓ 待用户输入" if pending.kind == "ask_user" else "❓ 待沙箱操作确认"
        await self.emergency_text(
            f"{kind_label}\n\n{pending.question}\n\n"
            f"_(卡片更新暂时不可用；请在 cubeplex 网页端继续。)_"[:4000]
        )

    async def _emergency_card_create_fallback(self) -> None:
        state = self._state.card_state
        await self.emergency_text("⚠️ 飞书富文本渲染暂时不可用，结果将以文本展示")
        if state.streaming_content:
            await self.emergency_text(state.streaming_content[:4000])
        pending = state.pending_input
        if pending is not None and pending.resolved_choice is None:
            kind_label = "❓ 待用户输入" if pending.kind == "ask_user" else "❓ 待沙箱操作确认"
            await self.emergency_text(
                f"{kind_label}\n\n{pending.question}\n\n_(请在 cubeplex 网页端继续。)_"[:4000]
            )
```

- [ ] **Step 6: Rewire OutboundRunTailer to use OpDispatcher**

In `backend/cubeplex/im/outbound.py`:

1. Replace `__init__` parameter `cardkit` with `dispatcher: OpDispatcher` (from `cubeplex.im.op_dispatcher`). Keep `connector` for lifecycle hooks only (`on_processing_start/complete/failed`).

2. Replace `_dispatch_op` body with delegation to the dispatcher:

```python
async def _dispatch_op(self, op: OutboundOp, *, is_terminal: bool) -> bool:
    _ = is_terminal
    if self._dispatcher is None:
        return False
    state = self._state
    if op.kind == "card_create":
        return await self._dispatcher.dispatch_create(state)
    if op.kind == "stream_text":
        return await self._dispatcher.dispatch_stream(state, op.text)
    if op.kind == "patch_card":
        return await self._dispatcher.dispatch_patch(state)
    if op.kind == "finalize":
        return await self._dispatcher.dispatch_finalize(state)
    return False
```

3. In the `finally` block of `run()`, replace the `cardkit.aclose()` call with `self._dispatcher.aclose()`.

4. Remove `_emergency_text`, `_maybe_surface_pending_via_emergency`, and `_emergency_card_create_fallback` methods from the class (they now live in `FeishuOpDispatcher`).

- [ ] **Step 7: Update runtime.py to construct FeishuOpDispatcher**

In `backend/cubeplex/im/runtime.py`, in `_on_run_started`:

Replace:
```python
tailer = OutboundRunTailer(
    redis=..., ..., connector=connector, state=state,
    cardkit=cardkit, artifact_dispatcher=dispatcher, ...
)
```

With:
```python
from cubeplex.im.feishu.op_dispatcher import FeishuOpDispatcher

op_dispatcher = FeishuOpDispatcher(
    connector=connector, state=state, cardkit=cardkit
)
tailer = OutboundRunTailer(
    redis=..., ..., connector=connector, state=state,
    dispatcher=op_dispatcher, artifact_dispatcher=dispatcher, ...
)
```

- [ ] **Step 8: Update existing test files that construct OutboundRunTailer**

Three test files still pass `cardkit=` to `OutboundRunTailer`. Each must create a `FeishuOpDispatcher` and pass `dispatcher=` instead:

- `backend/tests/im/test_awaiting_responder.py` — 4 call sites, change `cardkit=_FakeCardKit()` → wrap in `FeishuOpDispatcher` and pass `dispatcher=`
- `backend/tests/im/test_tailer_dispatch.py` — 1 call site, same change
- `backend/tests/e2e/test_im_end_to_end.py` — 2 call sites, same change

In each file, add `from cubeplex.im.feishu.op_dispatcher import FeishuOpDispatcher` and update each `OutboundRunTailer(... cardkit=X ...)` to `OutboundRunTailer(... dispatcher=FeishuOpDispatcher(connector=connector, state=state, cardkit=X) ...)`.

- [ ] **Step 9: Run all IM tests (including e2e)**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run pytest tests/im/ tests/e2e/test_im_end_to_end.py -v`
Expected: All PASS

- [ ] **Step 10: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add backend/cubeplex/im/op_dispatcher.py backend/cubeplex/im/feishu/op_dispatcher.py backend/cubeplex/im/outbound.py backend/cubeplex/im/runtime.py backend/tests/unit/im/test_op_dispatcher_protocol.py backend/tests/im/test_awaiting_responder.py backend/tests/im/test_tailer_dispatch.py backend/tests/e2e/test_im_end_to_end.py && git commit -m "refactor(im): extract OpDispatcher protocol from OutboundRunTailer"
```

---

### Task 4: Add `make_channel_scope()` Helper

**Files:**
- Modify: `backend/cubeplex/im/types.py`

- [ ] **Step 1: Add the helper**

In `backend/cubeplex/im/types.py`, after `make_participant_scope` (line 26):

```python
def make_channel_scope() -> str:
    """Channel-shared session (Discord guild channels, future public rooms).

    All users in the same channel share one conversation. The channel_id
    column already distinguishes channels, so scope_key only differentiates
    session types within the same channel (regular vs thread).
    """
    return "ch"
```

- [ ] **Step 2: Run existing tests**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run pytest tests/unit/im/ -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add backend/cubeplex/im/types.py && git commit -m "feat(im): add make_channel_scope() helper for channel-shared sessions"
```

---

## Phase 2: Discord Connector Backend

### Task 5: Add discord.py Dependency

- [ ] **Step 1: Install discord.py**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv add discord.py`

- [ ] **Step 2: Verify import works**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run python -c "import discord; print(discord.__version__)"`
Expected: Version printed without error

- [ ] **Step 3: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add backend/pyproject.toml backend/uv.lock && git commit -m "deps(backend): add discord.py for Gateway integration"
```

---

### Task 6: Discord Connector — parse_inbound

**Files:**
- Create: `backend/cubeplex/im/discord/__init__.py`
- Create: `backend/cubeplex/im/discord/connector.py`
- Test: `backend/tests/unit/im/discord/test_connector.py`

- [ ] **Step 1: Write tests**

```python
# backend/tests/unit/im/discord/test_connector.py
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from cubeplex.im.discord.connector import DiscordConnector
from cubeplex.im.types import DM_SCOPE_KEY


def _make_message(
    *,
    content: str = "hello bot",
    author_id: int = 111,
    author_bot: bool = False,
    channel_id: int = 222,
    message_id: int = 333,
    guild_id: int | None = 444,
    is_dm: bool = False,
    mentions_bot: bool = True,
    bot_user_id: int = 999,
    thread_id: int | None = None,
) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.id = message_id
    msg.author.id = author_id
    msg.author.bot = author_bot
    msg.channel.id = thread_id or channel_id
    msg.channel.type = MagicMock()
    if is_dm:
        msg.channel.type.value = 1  # DM
        msg.guild = None
    else:
        msg.channel.type.value = 0  # GUILD_TEXT
        msg.guild = MagicMock()
        msg.guild.id = guild_id
    # Thread detection
    if thread_id is not None:
        msg.channel.type.value = 11  # PUBLIC_THREAD
        msg.channel.parent_id = channel_id
    # Mentions
    bot_user = MagicMock()
    bot_user.id = bot_user_id
    if mentions_bot:
        mention = MagicMock()
        mention.id = bot_user_id
        msg.mentions = [mention]
    else:
        msg.mentions = []
    return msg


class TestDiscordConnectorParseInbound:
    def setup_method(self) -> None:
        self.connector = DiscordConnector(bot_user_id=999)

    def test_dm_message(self) -> None:
        msg = _make_message(is_dm=True, mentions_bot=False)
        event = self.connector.parse_inbound(msg)
        assert event is not None
        assert event.platform == "discord"
        assert event.scope_key == DM_SCOPE_KEY
        assert event.scope_kind == "dm"
        assert event.text == "hello bot"

    def test_guild_mention(self) -> None:
        msg = _make_message(mentions_bot=True)
        event = self.connector.parse_inbound(msg)
        assert event is not None
        assert event.scope_key == "ch"
        assert event.scope_kind == "channel"
        assert event.reply_to_id == "333"

    def test_guild_no_mention_ignored(self) -> None:
        msg = _make_message(mentions_bot=False)
        event = self.connector.parse_inbound(msg)
        assert event is None

    def test_bot_message_ignored(self) -> None:
        msg = _make_message(author_bot=True)
        event = self.connector.parse_inbound(msg)
        assert event is None

    def test_own_message_ignored(self) -> None:
        msg = _make_message(author_id=999)
        event = self.connector.parse_inbound(msg)
        assert event is None

    def test_empty_text_ignored(self) -> None:
        msg = _make_message(content="<@999>", mentions_bot=True)
        event = self.connector.parse_inbound(msg)
        assert event is None

    def test_thread_message(self) -> None:
        msg = _make_message(
            mentions_bot=True,
            thread_id=555,
            channel_id=222,
        )
        event = self.connector.parse_inbound(msg)
        assert event is not None
        assert event.scope_key == "t:555"
        assert event.scope_kind == "thread"
        assert event.channel_id == "555"

    def test_mention_stripped_from_text(self) -> None:
        msg = _make_message(content="<@999> what is 2+2?", mentions_bot=True)
        event = self.connector.parse_inbound(msg)
        assert event is not None
        assert event.text == "what is 2+2?"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run pytest tests/unit/im/discord/test_connector.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create `__init__.py`**

```python
# backend/cubeplex/im/discord/__init__.py
```

Create empty `backend/tests/unit/im/discord/__init__.py` too.

- [ ] **Step 4: Implement DiscordConnector**

```python
# backend/cubeplex/im/discord/connector.py
"""Discord connector: inbound parse + outbound message send/edit + reactions.

All discord.py API calls are async-native (no to_thread needed unlike
Feishu's sync SDK).
"""
from __future__ import annotations

import re
from typing import Any

from loguru import logger

from cubeplex.im.outbound import _FloodSignal
from cubeplex.im.types import (
    DM_SCOPE_KEY,
    InboundEvent,
    make_channel_scope,
    make_thread_scope,
)

_USER_MENTION_RE = re.compile(r"<@!?(\d+)>")

_REACTION_PROCESSING = "⏳"
_REACTION_FAILURE = "❌"


class DiscordRateLimitError(_FloodSignal):
    """Raised when Discord API returns 429."""


class DiscordConnector:
    """Connector for one Discord bot account.

    Construction:
    - Inbound parsing only needs ``bot_user_id``.
    - Outbound calls need a bound ``bot`` (discord.py Bot instance)
      plus ``channel_id``, set at tailer construction time.
    """

    def __init__(
        self,
        *,
        bot_user_id: int | None = None,
        bot: Any = None,
        channel_id: str | None = None,
        reply_to_id: str | None = None,
    ) -> None:
        self._bot_user_id = bot_user_id
        self._bot = bot
        self._channel_id = channel_id
        self._reply_to_id = reply_to_id

    def _clean_mentions(self, text: str, message: Any) -> str:
        """Strip the bot's own @mention; replace other user mentions with display names."""

        def _replace(match: re.Match[str]) -> str:
            uid = int(match.group(1))
            if self._bot_user_id is not None and uid == self._bot_user_id:
                return ""
            for m in getattr(message, "mentions", []):
                if int(m.id) == uid:
                    return f"@{m.display_name}"
            return match.group(0)

        return _USER_MENTION_RE.sub(_replace, text).strip()

    def parse_inbound(self, message: Any) -> InboundEvent | None:
        """Normalize one discord.py Message into InboundEvent.

        Returns None for messages we ignore: bot authors, own messages,
        non-text, guild messages that don't mention the bot, empty text.
        """
        if getattr(message.author, "bot", False):
            return None
        author_id = message.author.id
        if self._bot_user_id is not None and author_id == self._bot_user_id:
            return None

        channel = message.channel
        channel_type = getattr(channel, "type", None)
        channel_type_value = getattr(channel_type, "value", -1)

        is_dm = channel_type_value == 1
        is_thread = channel_type_value in (11, 12)  # PUBLIC_THREAD, PRIVATE_THREAD

        text = str(message.content or "")
        text = self._clean_mentions(text, message)
        if not text:
            return None

        message_id = str(message.id)
        sender_ref = str(author_id)

        if is_dm:
            return InboundEvent(
                platform="discord",
                account_external_id="",
                platform_event_id=message_id,
                channel_id=str(channel.id),
                scope_key=DM_SCOPE_KEY,
                scope_kind="dm",
                reply_to_id=None,
                inbound_message_id=message_id,
                sender_ref=sender_ref,
                sender_open_id=sender_ref,
                text=text,
            )

        if not self._mentions_bot(message):
            return None

        if is_thread:
            thread_id = str(channel.id)
            return InboundEvent(
                platform="discord",
                account_external_id="",
                platform_event_id=message_id,
                channel_id=thread_id,
                scope_key=make_thread_scope(thread_id),
                scope_kind="thread",
                reply_to_id=message_id,
                inbound_message_id=message_id,
                sender_ref=sender_ref,
                sender_open_id=sender_ref,
                text=text,
            )

        return InboundEvent(
            platform="discord",
            account_external_id="",
            platform_event_id=message_id,
            channel_id=str(channel.id),
            scope_key=make_channel_scope(),
            scope_kind="channel",
            reply_to_id=message_id,
            inbound_message_id=message_id,
            sender_ref=sender_ref,
            sender_open_id=sender_ref,
            text=text,
        )

    def _mentions_bot(self, message: Any) -> bool:
        if self._bot_user_id is None:
            return False
        for mention in getattr(message, "mentions", []):
            if getattr(mention, "id", None) == self._bot_user_id:
                return True
        return False

    async def send_message(self, text: str) -> str | None:
        """Send a message to the bound channel. Returns message_id."""
        if self._bot is None or not self._channel_id:
            return None
        try:
            channel = self._bot.get_channel(int(self._channel_id))
            if channel is None:
                channel = await self._bot.fetch_channel(int(self._channel_id))
            msg = await channel.send(text)
            return str(msg.id)
        except Exception:
            logger.warning("[Discord] send_message failed", exc_info=True)
            return None

    async def edit_message(self, message_id: str, text: str) -> bool:
        """Edit an existing message. Returns True on success."""
        if self._bot is None or not self._channel_id:
            return False
        try:
            channel = self._bot.get_channel(int(self._channel_id))
            if channel is None:
                channel = await self._bot.fetch_channel(int(self._channel_id))
            msg = await channel.fetch_message(int(message_id))
            await msg.edit(content=text)
            return True
        except Exception as exc:
            if "429" in str(exc) or "rate limit" in str(exc).lower():
                raise DiscordRateLimitError(f"edit rate limited: {exc}") from exc
            logger.warning("[Discord] edit_message failed", exc_info=True)
            return False

    async def add_reaction(self, message_id: str, emoji: str) -> bool:
        if self._bot is None or not self._channel_id:
            return False
        try:
            channel = self._bot.get_channel(int(self._channel_id))
            if channel is None:
                channel = await self._bot.fetch_channel(int(self._channel_id))
            msg = await channel.fetch_message(int(message_id))
            await msg.add_reaction(emoji)
            return True
        except Exception:
            logger.warning("[Discord] add_reaction failed", exc_info=True)
            return False

    async def remove_reaction(self, message_id: str, emoji: str) -> bool:
        if self._bot is None or not self._channel_id:
            return False
        try:
            channel = self._bot.get_channel(int(self._channel_id))
            if channel is None:
                channel = await self._bot.fetch_channel(int(self._channel_id))
            msg = await channel.fetch_message(int(message_id))
            await msg.remove_reaction(emoji, self._bot.user)
            return True
        except Exception:
            logger.warning("[Discord] remove_reaction failed", exc_info=True)
            return False

    async def on_processing_start(self, state: Any) -> None:
        target = getattr(state, "inbound_message_id", None)
        if target:
            await self.add_reaction(target, _REACTION_PROCESSING)

    async def on_processing_complete(self, state: Any) -> None:
        target = getattr(state, "inbound_message_id", None)
        if target:
            await self.remove_reaction(target, _REACTION_PROCESSING)

    async def on_processing_failed(self, state: Any) -> None:
        target = getattr(state, "inbound_message_id", None)
        if not target:
            return
        await self.remove_reaction(target, _REACTION_PROCESSING)
        await self.add_reaction(target, _REACTION_FAILURE)

    async def _send_emergency_text(self, text: str) -> str | None:
        return await self.send_message(text)

    async def send_to_chat(
        self, chat_id: str, reply_to_id: str | None, text: str
    ) -> str | None:
        if self._bot is None:
            return None
        try:
            channel = self._bot.get_channel(int(chat_id))
            if channel is None:
                channel = await self._bot.fetch_channel(int(chat_id))
            if reply_to_id:
                try:
                    ref_msg = await channel.fetch_message(int(reply_to_id))
                    msg = await channel.send(text, reference=ref_msg)
                except Exception:
                    msg = await channel.send(text)
            else:
                msg = await channel.send(text)
            return str(msg.id)
        except Exception:
            logger.warning("[Discord] send_to_chat failed", exc_info=True)
            return None
```

- [ ] **Step 5: Run tests**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run pytest tests/unit/im/discord/test_connector.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add backend/cubeplex/im/discord/__init__.py backend/cubeplex/im/discord/connector.py backend/tests/unit/im/discord/__init__.py backend/tests/unit/im/discord/test_connector.py && git commit -m "feat(im/discord): add DiscordConnector with parse_inbound"
```

---

### Task 7: Discord OpDispatcher (Renderer)

**Files:**
- Create: `backend/cubeplex/im/discord/renderer.py`
- Test: `backend/tests/unit/im/discord/test_renderer.py`

- [ ] **Step 1: Write tests**

```python
# backend/tests/unit/im/discord/test_renderer.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest

from cubeplex.im.card_model import CardState
from cubeplex.im.discord.renderer import DiscordOpDispatcher
from cubeplex.im.types import RenderState


@dataclass
class FakeConnector:
    sent: list[str] = field(default_factory=list)
    edited: list[tuple[str, str]] = field(default_factory=list)
    reactions_added: list[tuple[str, str]] = field(default_factory=list)
    reactions_removed: list[tuple[str, str]] = field(default_factory=list)

    async def send_message(self, text: str) -> str:
        self.sent.append(text)
        return f"msg_{len(self.sent)}"

    async def edit_message(self, msg_id: str, text: str) -> bool:
        self.edited.append((msg_id, text))
        return True

    async def add_reaction(self, msg_id: str, emoji: str) -> bool:
        self.reactions_added.append((msg_id, emoji))
        return True

    async def remove_reaction(self, msg_id: str, emoji: str) -> bool:
        self.reactions_removed.append((msg_id, emoji))
        return True

    async def _send_emergency_text(self, text: str) -> str | None:
        self.sent.append(text)
        return f"msg_{len(self.sent)}"


def _make_dispatcher() -> tuple[DiscordOpDispatcher, RenderState, FakeConnector]:
    card_state = CardState(bot_name="test", run_id="r1")
    state = RenderState(
        card_state=card_state,
        inbound_message_id="100",
        stream_interval=1.2,
        patch_interval=0.3,
    )
    connector = FakeConnector()
    dispatcher = DiscordOpDispatcher(connector=connector, state=state)
    return dispatcher, state, connector


class TestDiscordDispatchCreate:
    @pytest.mark.asyncio
    async def test_sends_initial_message(self) -> None:
        d, state, conn = _make_dispatcher()
        state.card_state.streaming_content = "Hello"
        result = await d.dispatch_create(state)
        assert result is True
        assert len(conn.sent) == 1
        assert conn.sent[0] == "Hello"
        assert state.bot_message_id == "msg_1"


class TestDiscordDispatchStream:
    @pytest.mark.asyncio
    async def test_edits_current_message(self) -> None:
        d, state, conn = _make_dispatcher()
        state.bot_message_id = "msg_1"
        state.card_state.streaming_content = "Hello world"
        result = await d.dispatch_stream(state, "Hello world")
        assert result is True
        assert len(conn.edited) == 1
        assert conn.edited[0] == ("msg_1", "Hello world")

    @pytest.mark.asyncio
    async def test_split_at_2000_chars(self) -> None:
        d, state, conn = _make_dispatcher()
        state.bot_message_id = "msg_1"
        long_text = "x" * 2500
        state.card_state.streaming_content = long_text
        result = await d.dispatch_stream(state, long_text)
        assert result is True
        assert state.sent_char_offset > 0
        assert state.bot_message_id == "msg_1"  # new message started


class TestDiscordDispatchFinalize:
    @pytest.mark.asyncio
    async def test_finalize_edits_final_content(self) -> None:
        d, state, conn = _make_dispatcher()
        state.bot_message_id = "msg_1"
        state.card_state.streaming_content = "Final answer"
        result = await d.dispatch_finalize(state)
        assert result is True
        assert conn.reactions_removed  # ⏳ removed

    @pytest.mark.asyncio
    async def test_finalize_with_error(self) -> None:
        d, state, conn = _make_dispatcher()
        state.bot_message_id = "msg_1"
        state.card_state.error = "something broke"
        result = await d.dispatch_finalize(state)
        assert result is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run pytest tests/unit/im/discord/test_renderer.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement DiscordOpDispatcher**

```python
# backend/cubeplex/im/discord/renderer.py
"""Discord outbound renderer — plain Markdown message edits.

Unlike Feishu's CardKit pipeline, Discord rendering is straightforward:
send one message, edit it as content accumulates, split at 2000 chars.

Discord reuses the shared ``RenderState`` from ``im/types.py`` (which
carries card_id, run_id, edits_disabled, stream_interval, etc. — all
fields that ``fold_event`` accesses). Discord-specific fields
(``sent_char_offset``) are added directly to this module's dispatcher
state rather than subclassing RenderState.
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from cubeplex.im.discord.connector import DiscordRateLimitError
from cubeplex.im.outbound import note_edit_success, note_flood_strike
from cubeplex.im.types import RenderState

_DISCORD_MSG_LIMIT = 2000
_SPLIT_THRESHOLD = 1900


class DiscordOpDispatcher:
    """Dispatches outbound ops to Discord via message send/edit.

    Uses the shared ``RenderState`` (same type as Feishu) so
    ``fold_event`` can access ``state.card_id``, ``state.run_id``,
    ``state.edits_disabled``, etc. without AttributeError.

    Discord-specific tracking (``sent_char_offset``) lives on the
    dispatcher itself, not on the state.
    """

    def __init__(
        self,
        *,
        connector: Any,
        state: RenderState,
    ) -> None:
        self._connector = connector
        self._state = state
        self.sent_char_offset: int = 0

    async def dispatch_create(self, state: Any) -> bool:
        s = self._state
        text = s.card_state.streaming_content
        if not text:
            text = "..."
        current_segment = text[self.sent_char_offset:]
        if len(current_segment) > _SPLIT_THRESHOLD:
            split_at = _find_split_point(current_segment, _SPLIT_THRESHOLD)
            send_text = current_segment[:split_at]
            self.sent_char_offset += split_at
        else:
            send_text = current_segment
        msg_id = await self._connector.send_message(send_text)
        if msg_id is None:
            return False
        s.bot_message_id = msg_id
        return True

    async def dispatch_stream(self, state: Any, text: str) -> bool:
        s = self._state
        if s.bot_message_id is None:
            return await self.dispatch_create(state)
        full_content = s.card_state.streaming_content
        current_segment = full_content[self.sent_char_offset:]
        if len(current_segment) > _SPLIT_THRESHOLD:
            split_at = _find_split_point(current_segment, _SPLIT_THRESHOLD)
            finalize_text = current_segment[:split_at]
            try:
                await self._connector.edit_message(s.bot_message_id, finalize_text)
            except DiscordRateLimitError:
                note_flood_strike(s)
                return False
            self.sent_char_offset += split_at
            remaining = full_content[self.sent_char_offset:]
            if remaining:
                msg_id = await self._connector.send_message(remaining[:_SPLIT_THRESHOLD])
                if msg_id:
                    s.bot_message_id = msg_id
            note_edit_success(s)
            return True
        try:
            ok = await self._connector.edit_message(s.bot_message_id, current_segment)
        except DiscordRateLimitError:
            note_flood_strike(s)
            return False
        if ok:
            note_edit_success(s)
        return ok

    async def dispatch_patch(self, state: Any) -> bool:
        s = self._state
        # Render AskUser/SandboxConfirm buttons when pending_input is set
        pending = s.card_state.pending_input
        if pending is not None and pending.resolved_choice is None and pending.choices:
            await self._send_pending_input_buttons(pending)
        # Send typing indicator during tool calls
        if self._connector._bot is not None and s.bot_message_id is not None:
            try:
                channel = self._connector._bot.get_channel(
                    int(self._connector._channel_id or "0")
                )
                if channel is not None:
                    await channel.typing()
            except Exception:
                pass
        return True

    async def _send_pending_input_buttons(self, pending: Any) -> None:
        """Send AskUser/SandboxConfirm as Discord buttons.

        Button custom_id format: ``im:{kind}:{run_id}:{value}``
        matches what interactions.py expects.
        """
        import discord

        view = discord.ui.View(timeout=600)
        for label, value, btn_type in pending.choices:
            style = discord.ButtonStyle.primary
            if btn_type == "danger":
                style = discord.ButtonStyle.danger
            elif btn_type == "default":
                style = discord.ButtonStyle.secondary
            button = discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"im:{pending.kind}:{pending.run_id}:{value}",
            )
            view.add_item(button)
        text = pending.question or "请选择："
        msg_id = await self._connector.send_message_with_view(text, view)
        if msg_id is None:
            # Fallback: send plain text with web client notice
            notice = "_(请在 cubeplex 网页端继续。)_"
            await self._connector.send_message(f"{text}\n\n{notice}")

    async def dispatch_finalize(self, state: Any) -> bool:
        s = self._state
        full_content = s.card_state.streaming_content
        if s.card_state.error:
            error_suffix = f"\n\n⚠️ {s.card_state.error}"
            full_content = (full_content + error_suffix) if full_content else error_suffix
        artifacts = s.card_state.artifacts
        if artifacts:
            links = "\n".join(
                f"📎 [{a.name}]({a.share_url})" for a in artifacts if a.share_url
            )
            if links:
                full_content = f"{full_content}\n\n{links}" if full_content else links
        if not full_content:
            return True
        remaining = full_content[self.sent_char_offset:]
        if s.bot_message_id is not None and len(remaining) <= _DISCORD_MSG_LIMIT:
            try:
                await self._connector.edit_message(s.bot_message_id, remaining)
            except Exception:
                logger.warning("[Discord] finalize edit failed", exc_info=True)
                await self.emergency_text(remaining[:4000])
        else:
            while remaining:
                chunk = remaining[:_DISCORD_MSG_LIMIT]
                remaining = remaining[_DISCORD_MSG_LIMIT:]
                if s.bot_message_id and not self.sent_char_offset:
                    try:
                        await self._connector.edit_message(s.bot_message_id, chunk)
                    except Exception:
                        await self._connector.send_message(chunk)
                else:
                    msg_id = await self._connector.send_message(chunk)
                    if msg_id:
                        s.bot_message_id = msg_id
                self.sent_char_offset += len(chunk)
        if s.inbound_message_id:
            await self._connector.remove_reaction(s.inbound_message_id, "⏳")
        return True

    async def emergency_text(self, text: str) -> None:
        try:
            await self._connector._send_emergency_text(text)
        except Exception:
            logger.warning("[Discord] emergency text send failed", exc_info=True)

    async def aclose(self) -> None:
        pass


def _find_split_point(text: str, limit: int) -> int:
    """Find a line-boundary split point at or before ``limit``."""
    idx = text.rfind("\n", 0, limit)
    if idx > limit // 2:
        return idx + 1
    return limit


# _note_flood / _note_success are not needed — the dispatcher uses the
# shared ``note_flood_strike()`` and ``note_edit_success()`` from
# ``cubeplex.im.outbound`` which already operate on ``RenderState``.
```

- [ ] **Step 4: Run tests**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run pytest tests/unit/im/discord/test_renderer.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add backend/cubeplex/im/discord/renderer.py backend/tests/unit/im/discord/test_renderer.py && git commit -m "feat(im/discord): add DiscordOpDispatcher with message-edit rendering"
```

---

### Task 8: Discord Gateway Lifecycle

**Files:**
- Create: `backend/cubeplex/im/discord/gateway.py`

- [ ] **Step 1: Implement gateway module**

```python
# backend/cubeplex/im/discord/gateway.py
"""Discord Gateway lifecycle — one discord.py Bot per IM account.

The Bot runs in an asyncio.Task. ``start()`` decrypts the bot token,
creates the Bot, registers event handlers, and spawns the task.
``stop()`` calls ``bot.close()`` and cancels the task.
"""
from __future__ import annotations

import asyncio
from typing import Any

import discord
from discord.ext import commands
from loguru import logger

from cubeplex.im.discord.connector import DiscordConnector
from cubeplex.im.inbound import ingest_inbound_event


class DiscordGateway:
    """Manages one discord.py Bot per IM account."""

    def __init__(
        self,
        *,
        account: Any,
        bot_token: str,
        application_id: str,
        ingest: Any,
        session_maker: Any,
        run_manager: Any,
        redis_key_prefix: str,
    ) -> None:
        self._account = account
        self._bot_token = bot_token
        self._application_id = application_id
        self._ingest = ingest
        self._session_maker = session_maker
        self._run_manager = run_manager
        self._redis_key_prefix = redis_key_prefix
        self._bot: commands.Bot | None = None
        self._task: asyncio.Task[None] | None = None
        self._connector: DiscordConnector | None = None

    async def start(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guild_messages = True
        intents.dm_messages = True
        intents.guild_reactions = True

        bot = commands.Bot(
            command_prefix="!",  # unused — we use slash commands
            intents=intents,
            application_id=int(self._application_id),
        )
        self._bot = bot
        account = self._account
        session_maker = self._session_maker
        ingest = self._ingest

        @bot.event
        async def on_ready() -> None:
            assert bot.user is not None
            logger.info(
                "[Discord] Bot ready: {} (id={})",
                bot.user.name,
                bot.user.id,
            )
            self._connector = DiscordConnector(bot_user_id=bot.user.id)
            from cubeplex.im.discord.commands import register_commands

            await register_commands(bot)

        @bot.event
        async def on_message(message: discord.Message) -> None:
            if bot.user is None or self._connector is None:
                return
            event = self._connector.parse_inbound(message)
            if event is None:
                return
            event.account_external_id = account.external_account_id
            try:
                result = await ingest(
                    event,
                    account=account,
                    session_maker=session_maker,
                )
                logger.info(
                    "[Discord] inbound {}: {}", event.platform_event_id, result.outcome
                )
            except Exception:
                logger.exception(
                    "[Discord] ingest failed for {}", event.platform_event_id
                )

        @bot.event
        async def on_interaction(interaction: discord.Interaction) -> None:
            if interaction.type == discord.InteractionType.component:
                from cubeplex.im.discord.interactions import handle_component_interaction

                await handle_component_interaction(
                    interaction,
                    run_manager=self._run_manager,
                    redis_key_prefix=self._redis_key_prefix,
                )

        self._task = asyncio.create_task(
            bot.start(self._bot_token),
            name=f"discord-gateway:{account.id}",
        )

    async def stop(self) -> None:
        if self._bot is not None:
            try:
                await self._bot.close()
            except Exception:
                logger.debug("[Discord] bot.close() raised", exc_info=True)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    def is_open(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def bot(self) -> commands.Bot | None:
        return self._bot

    @property
    def bot_user_id(self) -> int | None:
        if self._bot and self._bot.user:
            return self._bot.user.id
        return None
```

- [ ] **Step 2: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add backend/cubeplex/im/discord/gateway.py && git commit -m "feat(im/discord): add DiscordGateway lifecycle"
```

---

### Task 9: Discord Interactions (Button Resume)

**Files:**
- Create: `backend/cubeplex/im/discord/interactions.py`

- [ ] **Step 1: Implement interactions handler**

```python
# backend/cubeplex/im/discord/interactions.py
"""Handle Discord component interactions (button clicks for AskUser / SandboxConfirm)."""
from __future__ import annotations

from typing import Any

import discord
from loguru import logger


async def handle_component_interaction(
    interaction: discord.Interaction,
    *,
    run_manager: Any,
    redis_key_prefix: str,
) -> None:
    """Route a button click to the resume path.

    Button custom_id format: ``im:{kind}:{run_id}:{value}``
    where kind is ``ask_user`` or ``sandbox_confirm``.
    """
    custom_id = interaction.data.get("custom_id", "") if interaction.data else ""
    if not custom_id.startswith("im:"):
        return

    parts = custom_id.split(":", 3)
    if len(parts) < 4:
        await interaction.response.send_message("Invalid button.", ephemeral=True)
        return

    _, kind, run_id, value = parts

    from cubeplex.im.resume import resume_paused_run

    try:
        result = await resume_paused_run(
            run_id=run_id,
            input_kind=kind,
            choice=value,
            operator_open_id="",
            run_manager=run_manager,
        )
        if result:
            await interaction.response.send_message("✅", ephemeral=True)
        else:
            await interaction.response.send_message(
                "操作已过期或已被处理。", ephemeral=True
            )
    except Exception:
        logger.warning("[Discord] interaction handler failed", exc_info=True)
        try:
            await interaction.response.send_message("处理失败。", ephemeral=True)
        except Exception:
            pass
```

- [ ] **Step 2: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add backend/cubeplex/im/discord/interactions.py && git commit -m "feat(im/discord): add button interaction handler for AskUser/SandboxConfirm"
```

---

### Task 10: Discord Slash Commands

**Files:**
- Create: `backend/cubeplex/im/discord/commands.py`

- [ ] **Step 1: Implement slash commands**

```python
# backend/cubeplex/im/discord/commands.py
"""Discord slash commands: /new and /reset."""
from __future__ import annotations

from typing import Any

import discord
from discord.ext import commands
from loguru import logger


async def register_commands(bot: commands.Bot) -> None:
    """Register /new and /reset slash commands, then sync to all guilds."""

    @bot.tree.command(name="new", description="Start a new conversation")
    async def cmd_new(interaction: discord.Interaction) -> None:
        await _reset_conversation(interaction)

    @bot.tree.command(name="reset", description="Reset the current conversation")
    async def cmd_reset(interaction: discord.Interaction) -> None:
        await _reset_conversation(interaction)

    try:
        synced = await bot.tree.sync()
        logger.info("[Discord] Synced {} slash commands", len(synced))
    except Exception:
        logger.warning("[Discord] Failed to sync slash commands", exc_info=True)


async def _reset_conversation(interaction: discord.Interaction) -> None:
    """Delete the IMThreadLink for the current channel/scope so the next
    message starts a fresh conversation."""
    from cubeplex.im.types import DM_SCOPE_KEY, make_channel_scope, make_thread_scope

    channel = interaction.channel
    if channel is None:
        await interaction.response.send_message("无法确定频道。", ephemeral=True)
        return

    channel_type = getattr(channel, "type", None)
    channel_type_value = getattr(channel_type, "value", -1)
    is_dm = channel_type_value == 1
    is_thread = channel_type_value in (11, 12)

    channel_id = str(channel.id)
    if is_dm:
        scope_key = DM_SCOPE_KEY
    elif is_thread:
        scope_key = make_thread_scope(channel_id)
    else:
        scope_key = make_channel_scope()

    from cubeplex.im.models import IMThreadLink

    session_maker = getattr(bot, "_cubeplex_session_maker", None)
    account_id = getattr(bot, "_cubeplex_account_id", None)
    if session_maker is None or account_id is None:
        await interaction.response.send_message("内部错误。", ephemeral=True)
        return

    async with session_maker() as session:
        from sqlmodel import select

        stmt = select(IMThreadLink).where(
            IMThreadLink.account_id == account_id,
            IMThreadLink.channel_id == channel_id,
            IMThreadLink.scope_key == scope_key,
        )
        link = (await session.execute(stmt)).scalar_one_or_none()
        if link is not None:
            await session.delete(link)
            await session.commit()

    await interaction.response.send_message(
        "✅ 新对话已开始。下一条消息将创建新的会话。", ephemeral=True
    )
```

- [ ] **Step 2: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add backend/cubeplex/im/discord/commands.py && git commit -m "feat(im/discord): add /new and /reset slash commands"
```

---

## Phase 3: Backend Connect Route + Service

### Task 11: API Schema — ConnectDiscordAccountIn

**Files:**
- Modify: `backend/cubeplex/api/schemas/im_connector.py`

- [ ] **Step 1: Add the schema**

In `backend/cubeplex/api/schemas/im_connector.py`, after `ConnectFeishuAccountIn`:

```python
class ConnectDiscordAccountIn(BaseModel):
    """Payload for ``POST /ws/{ws}/im/accounts`` when ``platform == 'discord'``."""

    platform: str = Field(pattern="^discord$")
    bot_token: str = Field(min_length=1)
    application_id: str = Field(min_length=1, max_length=128)
    acting_user_id: str = Field(default="self", min_length=1)
```

- [ ] **Step 2: Run mypy on the file**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run mypy cubeplex/api/schemas/im_connector.py --strict`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add backend/cubeplex/api/schemas/im_connector.py && git commit -m "feat(im): add ConnectDiscordAccountIn schema"
```

---

### Task 12: Service — connect_discord()

**Files:**
- Modify: `backend/cubeplex/services/im_connector.py`

- [ ] **Step 1: Add connect_discord method**

In `IMConnectorService`, after `connect_feishu`, add:

```python
async def connect_discord(
    self,
    *,
    workspace_id: str,
    bot_token: str,
    application_id: str,
    acting_user_id: str,
) -> IMConnectorAccount:
    """Bind one Discord bot: validate token, store credential, return account."""
    existing = (
        await self._session.execute(
            select(IMConnectorAccount).where(
                IMConnectorAccount.platform == "discord",  # type: ignore[arg-type]
                IMConnectorAccount.external_account_id == application_id,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError(
            f"discord account already exists for application_id={application_id}"
            f" (id={existing.id})"
        )

    bot_username, bot_avatar_url = await self._hydrate_discord_bot_info(bot_token)

    secret_payload = json.dumps(
        {
            "bot_token": bot_token,
            "application_id": application_id,
        }
    )
    try:
        credential_id = await self._credentials.create(
            kind="im_bot",
            name=f"discord:{application_id}",
            plaintext=secret_payload,
        )
    except IntegrityError as exc:
        await self._session.rollback()
        raise ValueError(
            f"discord account already exists for application_id={application_id}"
            " (credential race)"
        ) from exc
    try:
        account = IMConnectorAccount(
            org_id=self._org_id,
            workspace_id=workspace_id,
            platform="discord",
            external_account_id=application_id,
            acting_user_id=acting_user_id,
            credential_id=credential_id,
            delivery_mode="gateway",
            config={
                "bot_app_name": bot_username or None,
                "bot_avatar_url": bot_avatar_url or None,
            },
        )
        self._session.add(account)
        await self._session.commit()
        await self._session.refresh(account)
        return account
    except Exception:
        await self._session.rollback()
        try:
            await self._credentials.delete(credential_id=credential_id)
        except Exception:
            logger.warning(
                "[IM] orphan credential {} could not be rolled back",
                credential_id,
                exc_info=True,
            )
        raise

async def _hydrate_discord_bot_info(
    self,
    bot_token: str,
) -> tuple[str, str]:
    """Validate bot token via Discord API ``GET /users/@me``.

    Returns ``(username, avatar_url)``. Both are empty strings on failure.
    """
    import httpx

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": f"Bot {bot_token}"},
                timeout=10.0,
            )
            if resp.status_code != 200:
                logger.warning(
                    "[IM] Discord /users/@me returned {}: {}",
                    resp.status_code,
                    resp.text[:200],
                )
                raise ValueError(
                    f"Discord bot token validation failed (HTTP {resp.status_code})"
                )
            data = resp.json()
            username = str(data.get("username") or "")
            avatar_hash = str(data.get("avatar") or "")
            user_id = str(data.get("id") or "")
            avatar_url = ""
            if avatar_hash and user_id:
                avatar_url = (
                    f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png"
                )
            return username, avatar_url
    except ValueError:
        raise
    except Exception:
        logger.exception("[IM] Discord /users/@me probe failed")
        raise ValueError("could not validate Discord bot token") from None
```

- [ ] **Step 2: Run mypy**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run mypy cubeplex/services/im_connector.py --strict`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add backend/cubeplex/services/im_connector.py && git commit -m "feat(im): add connect_discord() to IMConnectorService"
```

---

### Task 13: Route — Dispatch Connect by Platform

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/ws_im.py`

- [ ] **Step 1: Update the connect_account route**

Use a Pydantic discriminated union so FastAPI auto-generates the correct OpenAPI schema.

In `backend/cubeplex/api/schemas/im_connector.py`, add the union type after both schema classes:

```python
from typing import Annotated, Literal, Union

from pydantic import Discriminator, Tag

ConnectIMAccountIn = Annotated[
    Union[
        Annotated[ConnectFeishuAccountIn, Tag("feishu")],
        Annotated[ConnectDiscordAccountIn, Tag("discord")],
    ],
    Discriminator("platform"),
]
```

In `ws_im.py`:

1. Add import: `from cubeplex.api.schemas.im_connector import ConnectDiscordAccountIn, ConnectIMAccountIn`

2. Change the route signature to use the discriminated union:

```python
@router.post("/accounts", status_code=status.HTTP_201_CREATED, response_model=IMAccountOut)
async def connect_account(
    workspace_id: str,
    body: ConnectIMAccountIn,
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountOut:
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")

    if isinstance(body, ConnectFeishuAccountIn):
        return await _connect_feishu(body, request, ctx, session, backend)
    elif isinstance(body, ConnectDiscordAccountIn):
        return await _connect_discord(body, request, ctx, session, backend)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported platform",
        )
```

3. Extract the existing Feishu connect logic into `_connect_feishu()` and add `_connect_discord()`:

```python
async def _connect_discord(
    body: ConnectDiscordAccountIn,
    request: Request,
    ctx: RequestContext,
    session: AsyncSession,
    backend: EncryptionBackend,
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    acting = await _resolve_acting_user(body.acting_user_id, ctx, session)
    try:
        account = await svc.connect_discord(
            workspace_id=ctx.workspace_id,
            bot_token=body.bot_token,
            application_id=body.application_id,
            acting_user_id=acting,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    # Start Gateway connection inline
    starter = getattr(request.app.state, "im_connect_account", None)
    if starter is not None and account.enabled:
        try:
            await starter(account)
        except Exception:
            logger.warning(
                "[IM ws] discord gateway startup failed for {}", account.id, exc_info=True
            )
    return _to_out(account)
```

4. Extract `_resolve_acting_user()` from the existing code to share between Feishu and Discord:

```python
async def _resolve_acting_user(
    acting_user_id: str,
    ctx: RequestContext,
    session: AsyncSession,
) -> str:
    if acting_user_id == "self":
        return ctx.user.id
    caller_ws_role = await MembershipRepository(session).get_role(
        user_id=ctx.user.id, workspace_id=ctx.workspace_id
    )
    if caller_ws_role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="workspace admin required to impersonate another user",
        )
    om_repo = OrganizationMembershipRepository(session)
    target_org_role = await om_repo.get_role(
        user_id=acting_user_id, org_id=ctx.org_id
    )
    if target_org_role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="acting_user_id is not a member of this organization",
        )
    return acting_user_id
```

- [ ] **Step 2: Run mypy**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run mypy cubeplex/api/routes/v1/ws_im.py --strict`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add backend/cubeplex/api/routes/v1/ws_im.py && git commit -m "feat(im): dispatch connect route by platform (feishu + discord)"
```

---

## Phase 4: Runtime Integration

### Task 14: Register FeishuPlatform + DiscordPlatform

**Files:**
- Create: `backend/cubeplex/im/discord/__init__.py` (update — register DiscordPlatform)
- Modify: `backend/cubeplex/im/feishu/__init__.py` (register FeishuPlatform)
- Modify: `backend/cubeplex/im/runtime.py`

- [ ] **Step 1: Implement FeishuPlatform connector**

Add registration to `backend/cubeplex/im/feishu/__init__.py`:

```python
# backend/cubeplex/im/feishu/__init__.py
"""Feishu IM platform — registers itself with the platform registry."""
from cubeplex.im.registry import register_platform
from cubeplex.im.feishu._platform import FeishuPlatform

register_platform("feishu", FeishuPlatform())
```

Create `backend/cubeplex/im/feishu/_platform.py`:

```python
# backend/cubeplex/im/feishu/_platform.py
"""FeishuPlatform — PlatformConnector implementation for Feishu."""
from __future__ import annotations

from typing import Any

from loguru import logger


class FeishuPlatform:
    """PlatformConnector for Feishu (long-connection + webhook)."""

    def parse_inbound(self, raw: dict[str, Any]) -> Any:
        from cubeplex.im.feishu.connector import FeishuConnector

        connector = FeishuConnector()
        return connector.parse_inbound(raw)

    async def build_tailer(
        self, *, run_id: str, queue_item: Any, account: Any, **kwargs: Any
    ) -> Any:
        # This is called by runtime._on_run_started. The kwargs carry
        # app.state dependencies (redis, key_prefix, etc.).
        # Return a tuple of (connector, op_dispatcher, state) that the
        # runtime assembles into an OutboundRunTailer.
        pass  # Wired in Task 15 (runtime rewrite)

    async def on_account_enabled(self, account: Any, **kwargs: Any) -> None:
        pass  # Wired in Task 15

    async def on_account_disabled(self, account: Any, **kwargs: Any) -> None:
        pass  # Wired in Task 15
```

- [ ] **Step 2: Implement DiscordPlatform connector**

Update `backend/cubeplex/im/discord/__init__.py`:

```python
# backend/cubeplex/im/discord/__init__.py
"""Discord IM platform — registers itself with the platform registry."""
from cubeplex.im.registry import register_platform
from cubeplex.im.discord._platform import DiscordPlatform

register_platform("discord", DiscordPlatform())
```

Create `backend/cubeplex/im/discord/_platform.py`:

```python
# backend/cubeplex/im/discord/_platform.py
"""DiscordPlatform — PlatformConnector implementation for Discord."""
from __future__ import annotations

from typing import Any

from loguru import logger


class DiscordPlatform:
    """PlatformConnector for Discord (Gateway only)."""

    def parse_inbound(self, raw: dict[str, Any]) -> Any:
        from cubeplex.im.discord.connector import DiscordConnector

        connector = DiscordConnector()
        return connector.parse_inbound(raw)

    async def build_tailer(
        self, *, run_id: str, queue_item: Any, account: Any, **kwargs: Any
    ) -> Any:
        pass  # Wired in Task 15

    async def on_account_enabled(self, account: Any, **kwargs: Any) -> None:
        pass  # Wired in Task 15

    async def on_account_disabled(self, account: Any, **kwargs: Any) -> None:
        pass  # Wired in Task 15
```

- [ ] **Step 3: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add backend/cubeplex/im/feishu/__init__.py backend/cubeplex/im/feishu/_platform.py backend/cubeplex/im/discord/__init__.py backend/cubeplex/im/discord/_platform.py && git commit -m "feat(im): register FeishuPlatform + DiscordPlatform in registry"
```

---

### Task 15: Runtime Rewrite — Registry-Based Startup + Distributed Lease

This is the largest single task. The runtime needs to:
1. Import platform registrations (triggering `register_platform`)
2. Replace hard-coded Feishu `_on_run_started` with registry dispatch
3. Replace hard-coded Feishu `_connect_one` with platform-dispatched connection
4. Add distributed Redis lease sweep for multi-instance gateway ownership

**Files:**
- Modify: `backend/cubeplex/im/runtime.py`
- Modify: `backend/cubeplex/im/feishu/_platform.py` (wire build_tailer / on_account_enabled)
- Modify: `backend/cubeplex/im/discord/_platform.py` (wire build_tailer / on_account_enabled)
- Test: `backend/tests/unit/im/test_gateway_lease.py`

- [ ] **Step 1: Write lease test**

```python
# backend/tests/unit/im/test_gateway_lease.py
from __future__ import annotations

import pytest

from cubeplex.im.runtime import try_acquire_lease, release_lease, LEASE_TTL


class FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int = 30) -> bool:
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def delete(self, key: str) -> int:
        if key in self._store:
            del self._store[key]
            return 1
        return 0

    async def expire(self, key: str, seconds: int) -> bool:
        return key in self._store


@pytest.mark.asyncio
async def test_acquire_lease_success() -> None:
    redis = FakeRedis()
    acquired = await try_acquire_lease(
        redis, account_id="a1", instance_id="inst1", prefix="test"
    )
    assert acquired is True
    assert await redis.get("test:im:gateway:a1:owner") == "inst1"


@pytest.mark.asyncio
async def test_acquire_lease_already_owned() -> None:
    redis = FakeRedis()
    await try_acquire_lease(redis, account_id="a1", instance_id="inst1", prefix="test")
    acquired = await try_acquire_lease(
        redis, account_id="a1", instance_id="inst2", prefix="test"
    )
    assert acquired is False


@pytest.mark.asyncio
async def test_release_lease() -> None:
    redis = FakeRedis()
    await try_acquire_lease(redis, account_id="a1", instance_id="inst1", prefix="test")
    await release_lease(redis, account_id="a1", instance_id="inst1", prefix="test")
    assert await redis.get("test:im:gateway:a1:owner") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run pytest tests/unit/im/test_gateway_lease.py -v`
Expected: FAIL — `ImportError: cannot import name 'try_acquire_lease'`

- [ ] **Step 3: Rewrite runtime.py**

The full rewrite of `runtime.py`:

1. Add lease functions at module level:

```python
LEASE_TTL = 30
SWEEP_INTERVAL = 15

async def try_acquire_lease(
    redis: Any, *, account_id: str, instance_id: str, prefix: str
) -> bool:
    key = f"{prefix}:im:gateway:{account_id}:owner"
    return bool(await redis.set(key, instance_id, nx=True, ex=LEASE_TTL))

async def release_lease(
    redis: Any, *, account_id: str, instance_id: str, prefix: str
) -> None:
    key = f"{prefix}:im:gateway:{account_id}:owner"
    current = await redis.get(key)
    if current is not None and (current.decode() if isinstance(current, bytes) else current) == instance_id:
        await redis.delete(key)

async def renew_lease(
    redis: Any, *, account_id: str, instance_id: str, prefix: str
) -> bool:
    key = f"{prefix}:im:gateway:{account_id}:owner"
    current = await redis.get(key)
    decoded = current.decode() if isinstance(current, bytes) else current
    if decoded == instance_id:
        await redis.expire(key, LEASE_TTL)
        return True
    return False
```

2. Replace `start()` to:
   - Import platform registrations: `import cubeplex.im.feishu; import cubeplex.im.discord`
   - Generate `instance_id = str(uuid.uuid4())`
   - Replace `_on_run_started` with registry-based dispatch
   - Replace `_connect_one` with platform-dispatched connection
   - Start a sweep task that runs every 15s

3. Replace `_on_run_started` to use the registry:

```python
async def _on_run_started(run_id: str, item: Any) -> None:
    async with async_session_maker() as s:
        account = (await s.execute(
            select(IMConnectorAccount).where(IMConnectorAccount.id == item.account_id)
        )).scalar_one()
    platform = get_platform(account.platform)
    await platform.build_tailer(
        run_id=run_id, queue_item=item, account=account,
        redis=app.state.redis, key_prefix=app.state.redis_key_prefix,
        session_maker=async_session_maker, run_manager=run_manager,
        secret_cache=secret_cache, client_cache=client_cache,
        load_secrets=_load_secrets, config=_config,
    )
```

4. Replace the Feishu-only account query with all platforms:

```python
async with async_session_maker() as s:
    accounts = (await s.execute(
        select(IMConnectorAccount).where(
            IMConnectorAccount.enabled == True,  # type: ignore
            IMConnectorAccount.delivery_mode.in_(["long_connection", "gateway"]),  # type: ignore
        )
    )).scalars().all()
```

5. For each account, attempt lease and call `get_platform(account.platform).on_account_enabled(...)`.

- [ ] **Step 4: Wire FeishuPlatform.build_tailer and on_account_enabled**

In `backend/cubeplex/im/feishu/_platform.py`, implement the full methods that mirror the current `runtime.py` logic but scoped to Feishu.

- [ ] **Step 5: Wire DiscordPlatform.build_tailer and on_account_enabled**

In `backend/cubeplex/im/discord/_platform.py`, implement:

```python
async def build_tailer(
    self, *, run_id: str, queue_item: Any, account: Any, **kwargs: Any
) -> Any:
    import asyncio
    from cubeplex.im.discord.connector import DiscordConnector
    from cubeplex.im.discord.renderer import DiscordOpDispatcher
    from cubeplex.im.artifacts import IMArtifactDispatcher
    from cubeplex.im.card_model import CardState
    from cubeplex.im.outbound import OutboundRunTailer
    from cubeplex.im.types import RenderState

    redis = kwargs["redis"]
    key_prefix = kwargs["key_prefix"]
    gateways = kwargs.get("gateways", {})
    session_maker = kwargs.get("session_maker")

    gateway = gateways.get(account.id)
    bot = gateway.bot if gateway else None
    bot_user_id = gateway.bot_user_id if gateway else None

    connector = DiscordConnector(
        bot_user_id=bot_user_id,
        bot=bot,
        channel_id=queue_item.channel_id,
        reply_to_id=queue_item.reply_to_id,
    )
    card_state = CardState(bot_name="cubeplex", run_id=run_id)
    state = RenderState(
        card_state=card_state,
        inbound_message_id=queue_item.inbound_message_id,
        stream_interval=1.2,
        patch_interval=0.3,
    )
    op_dispatcher = DiscordOpDispatcher(connector=connector, state=state)
    artifact_dispatcher = IMArtifactDispatcher(
        run_id=run_id,
        session_maker=session_maker,
    )
    tailer = OutboundRunTailer(
        redis=redis,
        key_prefix=key_prefix,
        run_id=run_id,
        connector=connector,
        state=state,
        dispatcher=op_dispatcher,
        artifact_dispatcher=artifact_dispatcher,
        responder_open_id=queue_item.sender_open_id,
    )
    asyncio.create_task(tailer.run(), name=f"im-tailer:{run_id}")

async def on_account_enabled(self, account: Any, **kwargs: Any) -> None:
    from cubeplex.im.discord.gateway import DiscordGateway

    secrets = kwargs.get("secrets", {})
    gateways = kwargs.get("gateways", {})
    session_maker = kwargs.get("session_maker")
    run_manager = kwargs.get("run_manager")
    redis_key_prefix = kwargs.get("redis_key_prefix", "")
    ingest = kwargs.get("ingest")

    bot_token = str(secrets.get("bot_token") or "")
    application_id = str(secrets.get("application_id") or "")
    if not bot_token:
        logger.warning("[Discord] skipping account {} — no bot_token", account.id)
        return

    gw = DiscordGateway(
        account=account,
        bot_token=bot_token,
        application_id=application_id,
        ingest=ingest,
        session_maker=session_maker,
        run_manager=run_manager,
        redis_key_prefix=redis_key_prefix,
    )
    await gw.start()
    gateways[account.id] = gw

async def on_account_disabled(self, account: Any, **kwargs: Any) -> None:
    gateways = kwargs.get("gateways", {})
    gw = gateways.pop(account.id, None)
    if gw is not None:
        await gw.stop()
```

- [ ] **Step 6: Run lease tests**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run pytest tests/unit/im/test_gateway_lease.py -v`
Expected: All PASS

- [ ] **Step 7: Run all IM unit tests**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run pytest tests/unit/im/ -v`
Expected: All PASS

- [ ] **Step 8: Run mypy on modified modules**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run mypy cubeplex/im/runtime.py cubeplex/im/feishu/_platform.py cubeplex/im/discord/_platform.py --strict`
Expected: PASS (or only pre-existing warnings)

- [ ] **Step 9: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add backend/cubeplex/im/runtime.py backend/cubeplex/im/feishu/_platform.py backend/cubeplex/im/discord/_platform.py backend/tests/unit/im/test_gateway_lease.py && git commit -m "feat(im): registry-based runtime startup + distributed Redis lease"
```

---

## Phase 5: Frontend

### Task 16: Core API Types

**Files:**
- Modify: `frontend/packages/core/src/api/im.ts`

- [ ] **Step 1: Add Discord types and extend existing ones**

```typescript
// Add after ConnectFeishuAccountIn:
export interface ConnectDiscordAccountIn {
  platform: 'discord'
  bot_token: string
  application_id: string
  acting_user_id?: string
}

export type ConnectImAccountIn = ConnectFeishuAccountIn | ConnectDiscordAccountIn
```

Update `ImAccount.delivery_mode`:
```typescript
delivery_mode: 'long_connection' | 'webhook' | 'gateway'
```

Update `wsConnectImAccount` to accept the union:
```typescript
export async function wsConnectImAccount(
  client: ApiClient,
  wsId: string,
  body: ConnectImAccountIn,
): Promise<ImAccount> {
```

- [ ] **Step 2: Build core package**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/frontend && pnpm build --filter @cubeplex/core`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add frontend/packages/core/src/api/im.ts && git commit -m "feat(im-fe): add ConnectDiscordAccountIn + union type"
```

---

### Task 17: Platform Descriptor Types

**Files:**
- Modify: `frontend/packages/web/components/im/ImConnectWizard/platforms/types.ts`

- [ ] **Step 1: Extend PlatformDescriptor**

```typescript
// Change id union:
id: 'feishu' | 'slack' | 'teams' | 'discord'

// Change buildPayload return type:
import type { ConnectImAccountIn } from '@cubeplex/core'
buildPayload: (form: FormState) => ConnectImAccountIn
```

- [ ] **Step 2: Verify feishu.ts still type-checks**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/frontend && pnpm tsc --noEmit --project packages/web/tsconfig.json 2>&1 | head -20`
Expected: No new errors

- [ ] **Step 3: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add frontend/packages/web/components/im/ImConnectWizard/platforms/types.ts && git commit -m "feat(im-fe): extend PlatformDescriptor for discord"
```

---

### Task 18: Discord Platform Descriptor

**Files:**
- Create: `frontend/packages/web/components/im/ImConnectWizard/platforms/discord.ts`
- Modify: `frontend/packages/web/components/im/ImConnectWizard/platforms/index.ts`

- [ ] **Step 1: Create discord.ts**

```typescript
// frontend/packages/web/components/im/ImConnectWizard/platforms/discord.ts
import { StepCredentials } from '../steps/StepCredentials'
import { StepPrereqs } from '../steps/StepPrereqs'
import { StepVerify } from '../steps/StepVerify'

import type { PlatformDescriptor } from './types'

export const discordDescriptor: PlatformDescriptor = {
  id: 'discord',
  labelKey: 'im.platform.discord.label',
  iconName: 'MessageCircle',
  live: true,
  prereqs: [
    {
      key: 'app',
      labelKey: 'im.wizard.discord.prereq.app',
      helpUrl: () => 'https://discord.com/developers/applications',
    },
    {
      key: 'bot',
      labelKey: 'im.wizard.discord.prereq.bot',
    },
    {
      key: 'intents',
      labelKey: 'im.wizard.discord.prereq.intents',
      items: ['MESSAGE_CONTENT (privileged)', 'GUILD_MESSAGES', 'DIRECT_MESSAGES'],
    },
    {
      key: 'invite',
      labelKey: 'im.wizard.discord.prereq.invite',
    },
  ],
  credentialFields: [
    {
      key: 'bot_token',
      labelKey: 'im.wizard.discord.field.botToken',
      type: 'password',
      required: true,
    },
    {
      key: 'application_id',
      labelKey: 'im.wizard.discord.field.applicationId',
      type: 'text',
      required: true,
      placeholder: '123456789012345678',
    },
  ],
  steps: [
    {
      key: 'prereqs',
      labelKey: 'im.wizard.step.prereqs',
      Component: StepPrereqs,
      canAdvance: () => true,
    },
    {
      key: 'credentials',
      labelKey: 'im.wizard.step.credentials',
      Component: StepCredentials,
      canAdvance: (f) => !!(f.bot_token && f.application_id),
    },
    {
      key: 'verify',
      labelKey: 'im.wizard.step.verify',
      Component: StepVerify,
    },
  ],
  buildPayload: (f) => ({
    platform: 'discord' as const,
    bot_token: f.bot_token || '',
    application_id: f.application_id || '',
    acting_user_id: 'self',
  }),
  scopeConsoleUrl: (appId) =>
    `https://discord.com/developers/applications/${appId}/bot`,
}
```

- [ ] **Step 2: Register in index.ts**

```typescript
// frontend/packages/web/components/im/ImConnectWizard/platforms/index.ts
export { feishuDescriptor } from './feishu'
export { discordDescriptor } from './discord'
export { slackDescriptor } from './slack.stub'
export { teamsDescriptor } from './teams.stub'
export type {
  PlatformDescriptor,
  WizardStepDef,
  WizardStepProps,
  FieldDef,
  FormState,
} from './types'

import { feishuDescriptor } from './feishu'
import { discordDescriptor } from './discord'
import { slackDescriptor } from './slack.stub'
import { teamsDescriptor } from './teams.stub'
import type { PlatformDescriptor } from './types'

export const ALL_PLATFORMS: PlatformDescriptor[] = [
  feishuDescriptor,
  discordDescriptor,
  slackDescriptor,
  teamsDescriptor,
]
```

- [ ] **Step 3: Verify frontend type-checks**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/frontend && pnpm tsc --noEmit --project packages/web/tsconfig.json 2>&1 | head -20`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add frontend/packages/web/components/im/ImConnectWizard/platforms/discord.ts frontend/packages/web/components/im/ImConnectWizard/platforms/index.ts && git commit -m "feat(im-fe): add Discord platform descriptor + register in wizard"
```

---

### Task 19: Discord Logo + i18n Keys

**Files:**
- Modify: `frontend/packages/web/components/im/PlatformLogo.tsx`
- Modify: i18n locale files (find the exact path at implementation time)

- [ ] **Step 1: Add Discord to PlatformLogo**

In `PlatformLogo.tsx`, add a case for `'discord'` that renders a Discord SVG icon or uses a Lucide icon.

- [ ] **Step 2: Add i18n keys**

Find the locale JSON files (likely under `frontend/packages/web/locales/` or similar). Add keys:

```json
{
  "im.platform.discord.label": "Discord",
  "im.wizard.discord.prereq.app": "Create a Discord application at discord.com/developers/applications",
  "im.wizard.discord.prereq.bot": "Add a Bot to the application and copy the bot token",
  "im.wizard.discord.prereq.intents": "Enable these Gateway intents in the Bot settings",
  "im.wizard.discord.prereq.invite": "Invite the bot to your server with appropriate permissions",
  "im.wizard.discord.field.botToken": "Bot Token",
  "im.wizard.discord.field.applicationId": "Application ID"
}
```

And Chinese equivalents:

```json
{
  "im.platform.discord.label": "Discord",
  "im.wizard.discord.prereq.app": "在 discord.com/developers/applications 创建 Discord 应用",
  "im.wizard.discord.prereq.bot": "添加 Bot，复制 Bot Token",
  "im.wizard.discord.prereq.intents": "在 Bot 设置中启用以下 Gateway Intents",
  "im.wizard.discord.prereq.invite": "使用适当权限将 Bot 邀请到服务器",
  "im.wizard.discord.field.botToken": "Bot Token",
  "im.wizard.discord.field.applicationId": "Application ID"
}
```

- [ ] **Step 3: Verify frontend builds**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/frontend && pnpm build --filter web`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im && git add -A frontend/packages/web/components/im/PlatformLogo.tsx frontend/packages/web/locales/ && git commit -m "feat(im-fe): add Discord logo + i18n keys"
```

---

## Phase 6: Pre-PR Sweep

### Task 20: Full Test Suite + Type Check

- [ ] **Step 1: Run all backend unit tests**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run pytest tests/unit/ -v`
Expected: All PASS

- [ ] **Step 2: Run mypy on full backend**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/backend && uv run mypy cubeplex/ --strict`
Expected: PASS (or only pre-existing warnings)

- [ ] **Step 3: Run frontend type-check**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/frontend && pnpm tsc --noEmit`
Expected: PASS

- [ ] **Step 4: Verify frontend build**

Run: `cd /home/chris/cubeplex/.worktrees/feat/2025-06-15-discord-im/frontend && pnpm build`
Expected: PASS
