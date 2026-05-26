"""HTTP header helpers."""

from urllib.parse import quote


def content_disposition(filename: str, *, inline: bool = False) -> str:
    """Build a Content-Disposition header value safe for non-ASCII filenames.

    Starlette encodes header values as latin-1, which raises on CJK and other
    non-latin-1 characters. Emit an ASCII fallback plus an RFC 5987
    ``filename*=UTF-8''`` parameter so unicode names download correctly.
    """
    disposition = "inline" if inline else "attachment"
    ascii_fallback = filename.encode("ascii", "ignore").decode() or "download"
    return f"{disposition}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"
