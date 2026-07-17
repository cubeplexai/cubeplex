"""Unit tests for the ScheduleTargetSpec.validate matrix.

The dataclass is the single source of truth used by Pydantic schemas,
agent tools, and the service layer to enforce destination-field shape per
``target_mode``. Each parametrized case names the bug it would have caught.
"""

from __future__ import annotations

import pytest

from cubeplex.services.schedule_target_spec import (
    ScheduleTargetError,
    ScheduleTargetSpec,
)

# (target_mode, target_conv, topic, im_acct, im_ch, im_scope, im_kind, should_pass)
CASES = [
    ("fixed", "conv_1", None, None, None, None, None, True),
    ("fixed", None, None, None, None, None, None, False),
    ("fixed", "conv_1", "top_1", None, None, None, None, False),
    ("fixed", "conv_1", None, "imac_1", "C", "dm", "dm", False),
    ("new_each_run", None, None, None, None, None, None, True),
    ("new_each_run", None, "top_1", None, None, None, None, True),
    ("new_each_run", "conv_1", None, None, None, None, None, False),
    ("new_each_run", None, None, "imac_1", "C", "dm", "dm", False),
    ("im_channel", None, None, "imac_1", "C", "dm", "dm", True),
    ("im_channel", "conv_1", None, "imac_1", "C", "dm", "dm", False),
    ("im_channel", None, "top_1", "imac_1", "C", "dm", "dm", False),
    ("im_channel", None, None, None, "C", "dm", "dm", False),
    ("im_channel", None, None, "imac_1", "C", "dm", None, False),
    ("bogus", "conv_1", None, None, None, None, None, False),
]


@pytest.mark.parametrize("case", CASES)
def test_schedule_target_spec_matrix(
    case: tuple[str, str | None, str | None, str | None, str | None, str | None, str | None, bool],
) -> None:
    target_mode, conv, topic, acct, ch, scope, kind, ok = case
    spec = ScheduleTargetSpec(
        target_mode=target_mode,
        target_conversation_id=conv,
        topic_id=topic,
        im_account_id=acct,
        im_channel_id=ch,
        im_scope_key=scope,
        im_scope_kind=kind,
    )
    if ok:
        spec.validate()
    else:
        with pytest.raises(ScheduleTargetError):
            spec.validate()
