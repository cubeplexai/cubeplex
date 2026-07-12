"""Unit tests for image resize helpers."""

import io

import pytest
from PIL import Image

from cubeplex.services.attachments import (
    InvalidImageError,
    decode_image_dimensions,
    resize_to_long_edge,
)


def _img_bytes(w: int, h: int, fmt: str = "PNG") -> bytes:
    img = Image.new("RGB", (w, h), color=(0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def test_decode_dimensions_returns_size() -> None:
    w, h = decode_image_dimensions(_img_bytes(640, 480))
    assert (w, h) == (640, 480)


def test_decode_dimensions_rejects_huge() -> None:
    big = _img_bytes(17000, 100)
    with pytest.raises(InvalidImageError):
        decode_image_dimensions(big, max_long_edge=16384)


def test_resize_high_scales_down_keeping_ratio() -> None:
    out = resize_to_long_edge(_img_bytes(2000, 1500), target=1568, jpeg_quality=85)
    img = Image.open(io.BytesIO(out))
    assert max(img.size) == 1568
    assert img.size == (1568, 1176)


def test_resize_skips_when_smaller() -> None:
    src = _img_bytes(600, 400)
    out = resize_to_long_edge(src, target=1568, jpeg_quality=85)
    img = Image.open(io.BytesIO(out))
    assert img.size == (600, 400)
