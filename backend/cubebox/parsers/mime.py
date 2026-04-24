"""MIME sniffing helpers for file_read.

There is intentionally NO hardcoded REJECT list. "Unsupported" status is
determined by whether any registered FileParser plugin claims the MIME
type. See spec D22 for rationale.
"""

from __future__ import annotations

import asyncio
import mimetypes

import filetype
import magic


def sniff_mime(path: str, content: bytes) -> str:
    """Detect MIME via libmagic; fall back to filetype lib then extension.

    Synchronous on purpose — caller offloads to thread for very large files.
    """
    try:
        mime = magic.from_buffer(content[:8192], mime=True)
        if mime and mime != "application/octet-stream":
            return mime
    except Exception:
        pass

    kind = filetype.guess(content[:8192])
    if kind is not None:
        return str(kind.mime)

    guessed, _ = mimetypes.guess_type(path)
    if guessed:
        return guessed
    return "application/octet-stream"


async def sniff_mime_async(path: str, content: bytes) -> str:
    return await asyncio.to_thread(sniff_mime, path, content)
