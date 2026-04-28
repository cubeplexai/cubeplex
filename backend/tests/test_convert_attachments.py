"""Unit tests for convert.py file_attachment handling."""

from langchain_core.messages import HumanMessage

from cubebox.agents.convert import convert_to_api_messages, render_attachments_hint


def _file_attachment(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "type": "file_attachment",
        "file_id": "01HXY",
        "kind": "image",
        "filename": "chart.png",
        "sandbox_path": "/workspace/uploads/abc/01HXY/chart.png",
        "size_bytes": 122880,
        "width": 800,
        "height": 600,
    }
    base.update(overrides)
    return base


def test_render_image_hint_includes_path_and_view_images_call() -> None:
    out = render_attachments_hint([_file_attachment()])
    assert "[Attachments]" in out
    assert "chart.png" in out
    assert "/workspace/uploads/abc/01HXY/chart.png" in out
    assert "view_images" in out
    assert "800x600" in out


def test_render_document_hint_calls_file_read() -> None:
    out = render_attachments_hint([_file_attachment(kind="document", filename="spec.pdf")])
    assert "spec.pdf" in out
    assert "file_read" in out
    assert "view_images" not in out


def test_render_empty_returns_empty_string() -> None:
    assert render_attachments_hint([]) == ""


def test_convert_to_api_messages_splits_attachments() -> None:
    msg = HumanMessage(
        content=[
            {"type": "text", "text": "look"},
            _file_attachment(),
        ]
    )
    out = convert_to_api_messages([msg])
    assert out[0]["role"] == "user"
    assert out[0]["content"] == "look"
    assert "attachments" in out[0]
    atts = out[0]["attachments"]
    assert isinstance(atts, list)
    assert len(atts) == 1
    assert atts[0]["id"] == "01HXY"
    assert atts[0]["filename"] == "chart.png"
    assert atts[0]["kind"] == "image"
    assert atts[0]["thumbnail_url"]
    assert atts[0]["download_url"]


def test_convert_to_api_messages_legacy_string_content() -> None:
    msg = HumanMessage(content="plain text only")
    out = convert_to_api_messages([msg])
    assert out[0]["content"] == "plain text only"
    assert out[0].get("attachments", []) == []
