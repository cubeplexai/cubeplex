"""InboundAttachmentRef (de)serialization.

Guards the queue-row round-trip: if a ref loses its ``handle``, the worker's
resolver downloads nothing and the user's file silently vanishes.
"""

from cubebox.im.types import InboundAttachmentRef


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
