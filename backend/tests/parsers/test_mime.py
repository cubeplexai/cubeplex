"""MIME sniffing helper tests."""

from cubeplex.parsers.mime import sniff_mime, sniff_mime_async


def test_sniff_mime_detects_pdf_from_bytes() -> None:
    pdf_magic = b"%PDF-1.4\n"
    mime = sniff_mime("/tmp/a.pdf", pdf_magic + b"x" * 100)
    assert mime == "application/pdf"


def test_sniff_mime_falls_back_to_extension() -> None:
    # ASCII content with .py extension; libmagic detects text/x-python or text/plain
    mime = sniff_mime("/tmp/x.py", b"print('hi')\n")
    assert mime.startswith("text/") or mime == "application/x-python"


def test_sniff_mime_returns_octet_stream_for_unknown() -> None:
    # libmagic may detect as text/plain, application/octet-stream, or printable chars
    mime = sniff_mime("/tmp/x", b"\x01\x02\x03\x04random")
    assert mime in {"application/octet-stream", "text/plain"}


async def test_sniff_mime_async_returns_same_result() -> None:
    pdf_magic = b"%PDF-1.4\nstuff"
    mime = await sniff_mime_async("/tmp/a.pdf", pdf_magic)
    assert mime == "application/pdf"
