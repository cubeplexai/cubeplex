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


def test_convert_to_api_messages_reads_additional_kwargs_attachments_meta() -> None:
    """New Strategy-1 shape: plain string content + additional_kwargs.attachments_meta."""
    block = _file_attachment()
    msg = HumanMessage(
        content="look at this",
        additional_kwargs={"attachments_meta": [block]},
    )
    out = convert_to_api_messages([msg])
    assert out[0]["role"] == "user"
    assert out[0]["content"] == "look at this"
    atts = out[0]["attachments"]
    assert isinstance(atts, list)
    assert len(atts) == 1
    assert atts[0]["id"] == "01HXY"
    assert atts[0]["filename"] == "chart.png"
    assert atts[0]["kind"] == "image"
    assert atts[0]["thumbnail_url"]
    assert atts[0]["download_url"]


def test_convert_to_api_messages_strips_legacy_baked_in_hint() -> None:
    """Pre-middleware checkpoints had the [Attachments] hint baked into content."""
    block = _file_attachment()
    augmented = "look at this" + render_attachments_hint([block])
    msg = HumanMessage(
        content=augmented,
        additional_kwargs={"attachments_meta": [block]},
    )
    out = convert_to_api_messages([msg])
    assert out[0]["content"] == "look at this"
    assert "[Attachments]" not in out[0]["content"]


def test_convert_to_api_messages_keeps_user_text_with_attachments_marker() -> None:
    """New-shape checkpoints: don't truncate user content that happens to contain the marker."""
    block = _file_attachment()
    user_text = "discuss\n[Attachments]\nas mentioned earlier"
    msg = HumanMessage(
        content=user_text,
        additional_kwargs={"attachments_meta": [block]},
    )
    out = convert_to_api_messages([msg])
    # Suffix doesn't match the rendered hint, so content stays intact
    assert out[0]["content"] == user_text


def test_convert_to_lc_messages_appends_attachments_hint() -> None:
    from cubebox.agents.convert import convert_to_lc_messages

    api_msgs = [
        {
            "role": "user",
            "content": "look",
            "attachments": [
                {
                    "id": "01HXY",
                    "kind": "image",
                    "filename": "chart.png",
                    "sandbox_path": "/workspace/uploads/abc/01HXY/chart.png",
                    "size_bytes": 100,
                    "width": 800,
                    "height": 600,
                }
            ],
        }
    ]
    lc = convert_to_lc_messages(api_msgs)
    assert isinstance(lc[0].content, str)
    assert "look" in lc[0].content
    assert "[Attachments]" in lc[0].content
    assert "view_images" in lc[0].content
