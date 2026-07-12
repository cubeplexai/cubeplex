"""Connector retains a single emergency text path; old text streaming gone."""

from cubeplex.im.feishu import connector


def test_connector_module_does_not_export_build_payload() -> None:
    assert not hasattr(connector.FeishuConnector, "_build_payload")


def test_connector_module_drops_markdown_regexes() -> None:
    assert not hasattr(connector, "_MARKDOWN_TABLE_RE")
    assert not hasattr(connector, "_MARKDOWN_HINT_RE")


def test_connector_has_send_emergency_text_method() -> None:
    assert hasattr(connector.FeishuConnector, "_send_emergency_text")


def test_connector_drops_post_placeholder_and_edit() -> None:
    assert not hasattr(connector.FeishuConnector, "post_placeholder")
    assert not hasattr(connector.FeishuConnector, "edit")


def test_connector_drops_send_text_message() -> None:
    assert not hasattr(connector.FeishuConnector, "send_text_message")


def test_connector_keeps_send_card_init_message() -> None:
    assert hasattr(connector.FeishuConnector, "send_card_init_message")


def test_connector_keeps_reaction_methods() -> None:
    assert hasattr(connector.FeishuConnector, "add_reaction")
    assert hasattr(connector.FeishuConnector, "remove_reaction")
    assert hasattr(connector.FeishuConnector, "on_processing_start")
    assert hasattr(connector.FeishuConnector, "on_processing_complete")
    assert hasattr(connector.FeishuConnector, "on_processing_failed")
