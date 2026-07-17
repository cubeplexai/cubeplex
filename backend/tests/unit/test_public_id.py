"""Unit tests for cubeplex.models.public_id."""

import re
import time

import pytest

from cubeplex.models import public_id

BASE62_RE = re.compile(r"^[0-9A-Za-z]{14}$")


def test_format_shape() -> None:
    pid = public_id.generate_public_id("conv")
    assert pid.startswith("conv-")
    body = pid.split("-", 1)[1]
    assert BASE62_RE.match(body) is not None


def test_prefix_2_to_4_chars_accepted() -> None:
    for prefix in ("ws", "art", "atch"):
        pid = public_id.generate_public_id(prefix)
        assert pid.startswith(f"{prefix}-")


def test_each_id_unique_in_tight_loop() -> None:
    ids = {public_id.generate_public_id("tt") for _ in range(10_000)}
    assert len(ids) == 10_000


def test_ids_strictly_increase_within_process() -> None:
    """Sorting by ID must equal the generation order (lexicographic == numeric
    because base62 fixed-width is order-preserving)."""
    ids = [public_id.generate_public_id("tt") for _ in range(5_000)]
    assert ids == sorted(ids)


def test_monotonic_under_clock_freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two IDs generated at the same ms must still sort in generation order."""
    fixed_ms = int(time.time() * 1000)
    monkeypatch.setattr(public_id, "_now_ms", lambda: fixed_ms)
    a = public_id.generate_public_id("tt")
    b = public_id.generate_public_id("tt")
    assert a < b


def test_monotonic_under_clock_rewind(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clock going backwards must not produce a smaller ID."""
    times = iter([1_700_000_000_000, 1_700_000_000_000 - 5])
    monkeypatch.setattr(public_id, "_now_ms", lambda: next(times))
    a = public_id.generate_public_id("tt")
    b = public_id.generate_public_id("tt")
    assert a < b


def test_ms_spill_on_rand_overflow(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the random space is exhausted within one ms, the generator spills
    into the next logical ms instead of wrapping."""
    fixed_ms = 1_700_000_000_000
    monkeypatch.setattr(public_id, "_now_ms", lambda: fixed_ms)
    # Force last_rand to its max so the next call must overflow.
    public_id._STATE.last_ms = fixed_ms
    public_id._STATE.last_rand = public_id._RAND_MASK
    pid = public_id.generate_public_id("tt")
    # Decode body to int and assert the timestamp portion advanced.
    body = pid.split("-", 1)[1]
    n = public_id._base62_decode(body)
    extracted_ms = n >> public_id._RAND_BITS
    assert extracted_ms == fixed_ms + 1


def test_id_length_bounds() -> None:
    """Total length is prefix + 1 + 14 = 17..19 chars; fits VARCHAR(20)."""
    assert len(public_id.generate_public_id("ws")) == 17
    assert len(public_id.generate_public_id("art")) == 18
    assert len(public_id.generate_public_id("atch")) == 19
