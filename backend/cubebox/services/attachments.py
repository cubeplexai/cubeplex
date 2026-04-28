"""Attachment service: validate / persist / process uploaded files."""

from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError


class InvalidImageError(ValueError):
    """Raised when an uploaded image is invalid or too large to decode safely."""


def decode_image_dimensions(data: bytes, *, max_long_edge: int = 16384) -> tuple[int, int]:
    """Open *data* with PIL, return (width, height). Reject if larger than limit."""
    try:
        img = Image.open(io.BytesIO(data))
        w, h = img.size
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidImageError(f"cannot decode image: {exc}") from exc
    if max(w, h) > max_long_edge:
        raise InvalidImageError(
            f"image too large to process ({w}x{h}, max long edge {max_long_edge})"
        )
    return w, h


def resize_to_long_edge(data: bytes, *, target: int, jpeg_quality: int) -> bytes:
    """Resize so max(w, h) <= target, preserving aspect ratio. Output JPEG bytes.

    If image is already smaller than target, returns the original encoded back to JPEG
    (so callers always get a normalized JPEG output).
    """
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    if max(w, h) > target:
        if w >= h:
            new_w = target
            new_h = max(1, round(h * target / w))
        else:
            new_h = target
            new_w = max(1, round(w * target / h))
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=jpeg_quality)
    return buf.getvalue()
