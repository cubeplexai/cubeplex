"""Public IM connector schema contracts."""

import pytest
from pydantic import TypeAdapter, ValidationError

from cubeplex.api.schemas.im_connector import (
    ConnectIMAccountIn,
    ConnectWecomAccountIn,
    IMAccountOut,
    ImRuntimeStatus,
)


def test_runtime_status_required_fields() -> None:
    rs = ImRuntimeStatus(
        connection_state="connected",
        last_inbound_at=None,
        bot_open_id="ou_xxx",
        pending_queue=0,
        matched_24h=3,
        rejected_24h=1,
    )
    assert rs.connection_state == "connected"
    assert rs.matched_24h == 3


def test_account_out_embeds_runtime() -> None:
    out = IMAccountOut(
        id="imac-1",
        platform="feishu",
        external_account_id="cli_xxx",
        workspace_id="ws-1",
        acting_user_id="usr-1",
        delivery_mode="long_connection",
        enabled=True,
        runtime=ImRuntimeStatus(
            connection_state="never_connected",
            last_inbound_at=None,
            bot_open_id=None,
            pending_queue=0,
            matched_24h=0,
            rejected_24h=0,
        ),
    )
    assert out.runtime.connection_state == "never_connected"


def test_wecom_input_is_discriminated_and_requires_credentials() -> None:
    parsed = TypeAdapter(ConnectIMAccountIn).validate_python(
        {"platform": "wecom", "bot_id": "bot-1", "secret": "secret-1"}
    )
    assert isinstance(parsed, ConnectWecomAccountIn)
    assert parsed.acting_user_id == "self"

    with pytest.raises(ValidationError):
        TypeAdapter(ConnectIMAccountIn).validate_python({"platform": "wecom", "bot_id": "bot-1"})
