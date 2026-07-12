"""Unit tests for ScheduledTaskCreate / ScheduledTaskPatch validation."""

import pytest
from pydantic import ValidationError

from cubeplex.api.schemas.ws_scheduled_tasks import ScheduledTaskCreate

pytestmark = pytest.mark.unit

_BASE = {
    "name": "t",
    "prompt": "p",
    "target_mode": "new_each_run",
}


def _cron(**kw):
    return {**_BASE, "schedule_kind": "cron", **kw}


def test_6_field_cron_rejected():
    with pytest.raises(ValidationError, match="5 fields"):
        ScheduledTaskCreate(**_cron(cron_expr="0 9 * * * *"))


def test_4_field_cron_rejected():
    with pytest.raises(ValidationError, match="5 fields"):
        ScheduledTaskCreate(**_cron(cron_expr="9 * * *"))


def test_5_field_cron_accepted():
    obj = ScheduledTaskCreate(**_cron(cron_expr="0 9 * * *", timezone="Asia/Shanghai"))
    assert obj.cron_expr == "0 9 * * *"


def test_cron_with_l_dom_accepted():
    obj = ScheduledTaskCreate(**_cron(cron_expr="0 9 L * *", timezone="UTC"))
    assert obj.cron_expr == "0 9 L * *"
