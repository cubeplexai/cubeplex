"""InboundAttachmentRef (de)serialization.

Guards the queue-row round-trip: if a ref loses its ``handle``, the worker's
resolver downloads nothing and the user's file silently vanishes.
"""

from cubeplex.im.inbound_attachments import _effective_name_mime
from cubeplex.im.types import InboundAttachmentRef


def test_to_json_from_json_round_trip() -> None:
    ref = InboundAttachmentRef(
        kind="file",
        filename="report.pdf",
        mime="application/pdf",
        handle="file_v3_abc",
        size_hint=12345,
    )
    assert InboundAttachmentRef.from_json(ref.to_json()) == ref


def test_from_json_degrades_malformed_dict_to_safe_defaults() -> None:
    # Missing keys must not raise; kind/filename/handle fall back to strings.
    ref = InboundAttachmentRef.from_json({})
    assert ref.kind == "file"
    assert ref.filename == "file"
    assert ref.handle == ""
    assert ref.mime is None
    assert ref.size_hint is None


def test_from_json_missing_handle_yields_empty_string_not_none() -> None:
    # The worker treats handle="" as undownloadable (DownloadError), which is
    # the safe outcome — not an attribute error mid-resolution.
    ref = InboundAttachmentRef.from_json(
        {"kind": "image", "filename": "x.png", "mime": "image/png"}
    )
    assert ref.handle == ""
    assert ref.kind == "image"


_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


def test_effective_name_mime_sniffs_image_format_over_placeholder() -> None:
    # Feishu hands us a placeholder 'image.png'; a JPEG body must be stored as
    # image/jpeg with a .jpg name, not masqueraded as png.
    ref = InboundAttachmentRef(kind="image", filename="image.png", mime=None, handle="k")
    name, mime = _effective_name_mime(ref, _JPEG)
    assert name == "image.jpg"
    assert mime == "image/jpeg"


def test_effective_name_mime_passes_through_non_images() -> None:
    ref = InboundAttachmentRef(kind="file", filename="r.pdf", mime="application/pdf", handle="k")
    assert _effective_name_mime(ref, _PNG) == ("r.pdf", "application/pdf")


def test_effective_name_mime_preserves_non_image_extension() -> None:
    # A Feishu file (mime=None) must keep its real extension — NOT get renamed
    # to a sniffed coarse type. AttachmentService guesses the mime from .csv.
    ref = InboundAttachmentRef(kind="file", filename="合同终稿.csv", mime=None, handle="k")
    name, mime = _effective_name_mime(ref, b"a,b,c\n1,2,3\n")
    assert name == "合同终稿.csv"  # extension preserved, not clobbered to .txt
    assert mime is None  # let AttachmentService guess from the extension


def test_effective_name_mime_strips_charset_on_non_image() -> None:
    ref = InboundAttachmentRef(
        kind="file", filename="n.txt", mime="text/plain; charset=utf-8", handle="k"
    )
    assert _effective_name_mime(ref, b"hi") == ("n.txt", "text/plain")
