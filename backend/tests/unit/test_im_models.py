"""Unit tests for IM connector models (Task 1)."""

from cubeplex.models.im_connector import (
    IMConnectorAccount,
    IMIdentityLink,
    IMRunQueueItem,
    IMThreadLink,
    IMWebhookReceipt,
)


def test_account_id_prefix_and_defaults() -> None:
    acc = IMConnectorAccount(
        org_id="org-x",
        workspace_id="ws-x",
        platform="feishu",
        external_account_id="cli_a1b2",
        acting_user_id="usr-x",
        credential_id="cred-x",
    )
    assert acc.id.startswith("imac-")
    assert acc.delivery_mode == "long_connection"
    assert acc.enabled is True
    assert acc.config == {}


def test_thread_link_uses_neutral_scope_key() -> None:
    dm = IMThreadLink(
        org_id="org-x",
        workspace_id="ws-x",
        account_id="imac-1",
        channel_id="oc_dm1",
        scope_key="dm",
        scope_kind="dm",
        conversation_id="conv-1",
    )
    group = IMThreadLink(
        org_id="org-x",
        workspace_id="ws-x",
        account_id="imac-1",
        channel_id="oc_g",
        scope_key="u:on_user1",
        scope_kind="participant",
        conversation_id="conv-2",
    )
    assert dm.id.startswith("imtl-")
    assert group.id.startswith("imtl-")
    # Schema does not encode platform-specific terms.
    assert not hasattr(dm, "thread_root_id")
    assert not hasattr(dm, "thread_ts")


def test_receipt_prefix() -> None:
    rcpt = IMWebhookReceipt(
        org_id="org-x",
        workspace_id="ws-x",
        account_id="imac-1",
        platform_event_id="ev1",
        status="pending",
    )
    assert rcpt.id.startswith("imwr-")
    assert rcpt.lease_expires_at is None


def test_run_queue_item_has_neutral_columns_only() -> None:
    item = IMRunQueueItem(
        org_id="org-x",
        workspace_id="ws-x",
        account_id="imac-1",
        conversation_id="conv-1",
        receipt_id="imwr-1",
        content="hi",
        channel_id="oc_x",
        scope_key="u:on_user1",
        scope_kind="participant",
        reply_to_id="om_msg1",
        inbound_message_id="om_msg1",
        sender_im_user_id="on_user1",
    )
    assert item.id.startswith("imrq-")
    assert item.status == "pending"
    assert item.attempts == 0
    assert item.claimed_at is None
    assert item.claim_lease_expires_at is None
    # No Slack / Feishu / thread-specific column leakage:
    assert not hasattr(item, "slack_channel_id")
    assert not hasattr(item, "slack_thread_ts")
    assert not hasattr(item, "feishu_chat_id")
    assert not hasattr(item, "thread_root_id")
    assert not hasattr(item, "reply_thread_ts")


def test_identity_link_prefix() -> None:
    il = IMIdentityLink(
        org_id="org-x",
        workspace_id="ws-x",
        account_id="imac-1",
        im_user_id="on_user1",
        user_id="usr-1",
    )
    assert il.id.startswith("imil-")
