"""Unit tests for AttachmentHintMiddleware (M3.a.1)."""

import pytest
from cubepi.providers.base import AssistantMessage, TextContent, Usage, UserMessage

from cubeplex.middleware.attachments import AttachmentHintMiddleware


@pytest.mark.asyncio
async def test_user_msg_with_no_attachments_passes_through() -> None:
    mw = AttachmentHintMiddleware()
    msg = UserMessage(content=[TextContent(text="hi")])
    out = await mw.transform_context([msg], ctx=object())
    assert len(out) == 1
    assert out[0].content[0].text == "hi"


@pytest.mark.asyncio
async def test_user_msg_with_empty_attachments_list_passes_through() -> None:
    mw = AttachmentHintMiddleware()
    msg = UserMessage(content=[TextContent(text="hi")], metadata={"attachments": []})
    out = await mw.transform_context([msg], ctx=object())
    assert len(out) == 1
    assert out[0].content[0].text == "hi"


@pytest.mark.asyncio
async def test_user_msg_with_attachments_gets_hint_appended() -> None:
    mw = AttachmentHintMiddleware()
    msg = UserMessage(
        content=[TextContent(text="look")],
        metadata={
            "attachments": [
                {
                    "kind": "image",
                    "filename": "a.png",
                    "size_bytes": 100,
                    "sandbox_path": "/x/a.png",
                }
            ]
        },
    )
    out = await mw.transform_context([msg], ctx=object())
    text = out[0].content[0].text
    assert "look" in text
    assert "[Attachments]" in text
    assert "a.png" in text


@pytest.mark.asyncio
async def test_image_attachment_includes_view_images_hint() -> None:
    mw = AttachmentHintMiddleware()
    msg = UserMessage(
        content=[TextContent(text="q")],
        metadata={
            "attachments": [
                {
                    "kind": "image",
                    "filename": "photo.png",
                    "size_bytes": 512,
                    "sandbox_path": "/s/photo.png",
                    "width": 800,
                    "height": 600,
                }
            ]
        },
    )
    out = await mw.transform_context([msg], ctx=object())
    text = out[0].content[0].text
    assert "view_images" in text
    assert "photo.png" in text


@pytest.mark.asyncio
async def test_document_attachment_includes_file_read_hint() -> None:
    mw = AttachmentHintMiddleware()
    msg = UserMessage(
        content=[TextContent(text="q")],
        metadata={
            "attachments": [
                {
                    "kind": "document",
                    "filename": "report.pdf",
                    "size_bytes": 2048,
                    "sandbox_path": "/s/report.pdf",
                }
            ]
        },
    )
    out = await mw.transform_context([msg], ctx=object())
    text = out[0].content[0].text
    assert "file_read" in text
    assert "report.pdf" in text


@pytest.mark.asyncio
async def test_assistant_message_unchanged() -> None:
    mw = AttachmentHintMiddleware()
    msg = AssistantMessage(content=[TextContent(text="ok")], usage=Usage())
    out = await mw.transform_context([msg], ctx=object())
    assert out[0] is msg or out[0].content[0].text == "ok"


@pytest.mark.asyncio
async def test_multiple_messages_only_attachments_user_modified() -> None:
    mw = AttachmentHintMiddleware()
    msgs: list = [
        UserMessage(content=[TextContent(text="first")]),
        UserMessage(
            content=[TextContent(text="second")],
            metadata={
                "attachments": [
                    {
                        "kind": "image",
                        "filename": "x.png",
                        "size_bytes": 1,
                        "sandbox_path": "/x",
                    }
                ]
            },
        ),
        UserMessage(content=[TextContent(text="third")]),
    ]
    out = await mw.transform_context(msgs, ctx=object())
    assert out[0].content[0].text == "first"
    assert "[Attachments]" in out[1].content[0].text
    assert "second" in out[1].content[0].text
    assert out[2].content[0].text == "third"


@pytest.mark.asyncio
async def test_original_message_not_mutated() -> None:
    """transform_context must not mutate the input message."""
    mw = AttachmentHintMiddleware()
    msg = UserMessage(
        content=[TextContent(text="original")],
        metadata={
            "attachments": [
                {
                    "kind": "document",
                    "filename": "f.txt",
                    "size_bytes": 10,
                    "sandbox_path": "/f.txt",
                }
            ]
        },
    )
    original_text = msg.content[0].text
    await mw.transform_context([msg], ctx=object())
    assert msg.content[0].text == original_text


@pytest.mark.asyncio
async def test_metadata_preserved_on_augmented_message() -> None:
    mw = AttachmentHintMiddleware()
    msg = UserMessage(
        content=[TextContent(text="hi")],
        metadata={
            "attachments": [
                {
                    "kind": "image",
                    "filename": "img.png",
                    "size_bytes": 50,
                    "sandbox_path": "/img.png",
                }
            ],
            "extra_key": "extra_value",
        },
    )
    out = await mw.transform_context([msg], ctx=object())
    assert out[0].metadata.get("extra_key") == "extra_value"
    assert "attachments" in out[0].metadata


@pytest.mark.asyncio
async def test_no_text_content_appends_new_block() -> None:
    """When there is no existing TextContent, a new one is appended."""
    from cubepi.providers.base import ImageContent

    mw = AttachmentHintMiddleware()
    msg = UserMessage(
        content=[ImageContent(source="data:image/png;base64,abc", media_type="image/png")],
        metadata={
            "attachments": [
                {
                    "kind": "document",
                    "filename": "f.pdf",
                    "size_bytes": 100,
                    "sandbox_path": "/f.pdf",
                }
            ]
        },
    )
    out = await mw.transform_context([msg], ctx=object())
    # Should have gained a TextContent with the hint
    texts = [c for c in out[0].content if isinstance(c, TextContent)]
    assert texts, "Expected at least one TextContent block after augmentation"
    assert "[Attachments]" in texts[-1].text
