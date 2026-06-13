"""Unit tests for FeishuConnector flood-code recognition + reaction hooks.

The lark_oapi HTTP boundary is the unsimulatable part (covered by the manual
smoke checklist in Task 16). Here we test:
- ``_response_code`` / ``_raise_for_flood``: flood-code recognition.
- ``on_processing_start`` / ``_complete`` / ``_failed``: lifecycle hooks
  against a recording-stub connector.

The legacy ``_build_payload`` tests were removed in Task 8 when the outbound
path moved from ``messages/create`` text payloads to the cardkit op-set.
"""

import pytest

from cubebox.im.feishu.connector import (
    _FLOOD_CONTROL_CODES,
    FeishuConnector,
    FeishuRateLimitError,
)
from cubebox.im.outbound import _FloodSignal
from cubebox.im.types import RenderState

# ----------------------------------------------------------------------
# Flood-code recognition
# ----------------------------------------------------------------------


class _FakeResp:
    def __init__(self, code: int | str | None) -> None:
        self.code = code

    def success(self) -> bool:
        return self.code == 0


def test_raise_for_flood_translates_known_codes() -> None:
    for code in _FLOOD_CONTROL_CODES:
        with pytest.raises(FeishuRateLimitError):
            FeishuConnector._raise_for_flood(_FakeResp(code), "edit")


def test_raise_for_flood_translates_to_flood_signal_for_tailer() -> None:
    """FeishuRateLimitError must subclass the platform-agnostic _FloodSignal
    so the tailer's `except _FloodSignal` branch catches it without
    importing the Feishu module."""
    with pytest.raises(_FloodSignal):
        FeishuConnector._raise_for_flood(_FakeResp(1061045), "edit")


def test_raise_for_flood_ignores_non_flood_codes() -> None:
    # 0 = success; 99999 = unknown server error — neither should raise.
    FeishuConnector._raise_for_flood(_FakeResp(0), "edit")
    FeishuConnector._raise_for_flood(_FakeResp(99999), "edit")


def test_raise_for_flood_handles_missing_code() -> None:
    FeishuConnector._raise_for_flood(_FakeResp(None), "edit")


# ----------------------------------------------------------------------
# Reaction lifecycle: on_processing_start / complete / failed
# ----------------------------------------------------------------------


class _ReactionStub(FeishuConnector):
    """A FeishuConnector subclass that records add/remove calls and never
    touches the SDK, so we can drive the hooks without lark_oapi."""

    def __init__(self) -> None:
        super().__init__(bot_open_id="ou_bot")
        self.added: list[tuple[str, str]] = []
        self.removed: list[tuple[str, str | None]] = []
        self._next_id = 0
        self.add_should_fail = False

    async def add_reaction(self, message_id: str, reaction_type: str) -> str | None:
        if self.add_should_fail:
            return None
        self._next_id += 1
        rid = f"r-{self._next_id}"
        self.added.append((message_id, reaction_type))
        return rid

    async def remove_reaction(self, message_id: str, reaction_id: str | None) -> None:
        self.removed.append((message_id, reaction_id))


pytestmark = pytest.mark.asyncio


async def test_processing_start_adds_reaction_to_inbound_message_id() -> None:
    c = _ReactionStub()
    st = RenderState(bot_name="cubebox", run_id="r1", inbound_message_id="om_user_msg")
    await c.on_processing_start(st)
    assert c.added == [("om_user_msg", "THUMBSUP")]
    assert st.reaction_in_progress_id == "r-1"


async def test_processing_start_noop_without_inbound_message_id() -> None:
    c = _ReactionStub()
    st = RenderState(bot_name="cubebox", run_id="r1", inbound_message_id=None)
    await c.on_processing_start(st)
    assert c.added == []
    assert st.reaction_in_progress_id is None


async def test_processing_complete_removes_reaction() -> None:
    c = _ReactionStub()
    st = RenderState(bot_name="cubebox", run_id="r1", inbound_message_id="om_user_msg")
    await c.on_processing_start(st)
    assert st.reaction_in_progress_id == "r-1"
    await c.on_processing_complete(st)
    assert c.removed == [("om_user_msg", "r-1")]
    assert st.reaction_in_progress_id is None


async def test_processing_failed_removes_then_marks_failure() -> None:
    c = _ReactionStub()
    st = RenderState(bot_name="cubebox", run_id="r1", inbound_message_id="om_user_msg")
    await c.on_processing_start(st)
    await c.on_processing_failed(st)
    assert c.removed == [("om_user_msg", "r-1")]
    # The failure reaction is added after removal.
    assert ("om_user_msg", "OK") in c.added
    assert st.reaction_in_progress_id is None


async def test_processing_failed_safe_when_initial_add_returned_none() -> None:
    """If add_reaction on start returned None (e.g. missing scope), the failed
    hook must NOT crash when removing with reaction_id=None — that masked the
    real run error in older drafts."""
    c = _ReactionStub()
    c.add_should_fail = True
    st = RenderState(bot_name="cubebox", run_id="r1", inbound_message_id="om_user_msg")
    await c.on_processing_start(st)
    assert st.reaction_in_progress_id is None
    # Must not raise — remove_reaction with reaction_id=None no-ops.
    await c.on_processing_failed(st)


async def test_processing_complete_safe_when_inbound_missing() -> None:
    c = _ReactionStub()
    st = RenderState(bot_name="cubebox", run_id="r1", inbound_message_id=None)
    # No exception expected; no SDK calls.
    await c.on_processing_complete(st)
    assert c.removed == []
