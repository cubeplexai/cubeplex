"""compute_runtime: 4 connection_state branches via mocked aggregates."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from cubeplex.api.schemas.im_connector import ImRuntimeStatus
from cubeplex.models.im_connector import IMConnectorAccount
from cubeplex.repositories.im_connector import _RuntimeAgg
from cubeplex.services.im_connector import compute_runtime


def _mk_account(**kw: object) -> IMConnectorAccount:
    return IMConnectorAccount(
        id=str(kw.get("id", "imac-1")),
        org_id="org-1",
        workspace_id="ws-1",
        platform="feishu",
        external_account_id=str(kw.get("ext", "cli_a")),
        acting_user_id="usr-1",
        credential_id="cred-1",
        delivery_mode=str(kw.get("mode", "long_connection")),
        enabled=bool(kw.get("enabled", True)),
    )


def test_connected_when_long_conn_open() -> None:
    acc = _mk_account()
    lc = MagicMock()
    lc.is_open.return_value = True
    out = compute_runtime(
        acc,
        long_conns={"imac-1": lc},
        agg=_RuntimeAgg(),
        bot_open_id="ou_xxx",
    )
    assert out.connection_state == "connected"


def test_disconnected_when_long_conn_missing_and_no_recent_webhook() -> None:
    acc = _mk_account()
    out = compute_runtime(acc, long_conns={}, agg=_RuntimeAgg(), bot_open_id="ou_xxx")
    assert out.connection_state == "disconnected"


def test_connected_via_recent_webhook_for_webhook_mode() -> None:
    acc = _mk_account(mode="webhook")
    agg = _RuntimeAgg(last_receipt_at=datetime.now(UTC) - timedelta(minutes=5))
    out = compute_runtime(acc, long_conns={}, agg=agg, bot_open_id="ou_xxx")
    assert out.connection_state == "connected"


def test_never_connected_when_bot_open_id_missing() -> None:
    acc = _mk_account()
    out = compute_runtime(acc, long_conns={}, agg=_RuntimeAgg(), bot_open_id=None)
    assert out.connection_state == "never_connected"


def test_disabled_account_keeps_raw_state_ui_handles_overlay() -> None:
    # ``enabled=false`` is surfaced separately on IMAccountOut.enabled; this
    # service does not mutate connection_state on disable — UI maps disabled
    # to its own pill. (Spec §5: "enabled=false overrides everything → Disabled".)
    acc = _mk_account(enabled=False)
    lc = MagicMock()
    lc.is_open.return_value = True
    out = compute_runtime(acc, long_conns={"imac-1": lc}, agg=_RuntimeAgg(), bot_open_id="ou_xxx")
    assert out.connection_state == "connected"  # raw runtime; UI overlay


def test_returns_aggregates_verbatim() -> None:
    acc = _mk_account()
    ts = datetime.now(UTC)
    agg = _RuntimeAgg(last_receipt_at=ts, pending_count=3, matched_24h=7, rejected_24h=2)
    out = compute_runtime(acc, long_conns={}, agg=agg, bot_open_id="ou_xxx")
    assert isinstance(out, ImRuntimeStatus)
    assert out.pending_queue == 3
    assert out.matched_24h == 7
    assert out.rejected_24h == 2
    assert out.last_inbound_at is not None
    assert out.last_inbound_at.endswith("+00:00")  # utc_isoformat
