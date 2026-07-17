"""TextParser plugin tests."""

from cubeplex.parsers.plugins.text import TextParser
from cubeplex.parsers.protocols import FileParser
from cubeplex.parsers.schema import ParseOptions, TextOutput


def test_satisfies_protocol() -> None:
    assert isinstance(TextParser(), FileParser)


async def test_decodes_utf8() -> None:
    p = TextParser()
    out = await p.parse(b"hello world", mime="text/plain", options=ParseOptions())
    assert isinstance(out, TextOutput)
    assert out.content == "hello world"
    assert out.size_bytes == 11
    assert out.truncated is False


async def test_truncates_at_20k() -> None:
    p = TextParser()
    body = "x" * 30_000
    out = await p.parse(body.encode(), mime="text/plain", options=ParseOptions())
    assert out.truncated is True
    assert len(out.content) == 20_000
    assert out.metadata["total_chars"] == 30_000
    assert out.metadata["truncated_at_char"] == 20_000


async def test_falls_back_to_latin1_on_decode_error() -> None:
    p = TextParser()
    body = b"\xff\xfe hello"
    out = await p.parse(body, mime="text/plain", options=ParseOptions())
    assert isinstance(out, TextOutput)
    assert out.metadata.get("decode_fallback") == "latin-1"


async def test_line_range_returns_specific_lines() -> None:
    """line_range='2-4' returns lines 2 through 4 (1-indexed, inclusive)."""
    p = TextParser()
    body = "\n".join(f"line{i}" for i in range(1, 11)).encode()
    out = await p.parse(body, mime="text/plain", options=ParseOptions(line_range="2-4"))
    assert out.content == "line2\nline3\nline4"
    assert out.metadata["lines_returned"] == "2-4"
    assert out.metadata["total_lines"] == 10


async def test_line_range_single_line() -> None:
    p = TextParser()
    body = "\n".join(f"line{i}" for i in range(1, 6)).encode()
    out = await p.parse(body, mime="text/plain", options=ParseOptions(line_range="3"))
    assert out.content == "line3"
    assert out.metadata["lines_returned"] == "3-3"


async def test_line_range_clamps_to_file_length() -> None:
    """Out-of-range end is clamped silently."""
    p = TextParser()
    body = "\n".join(f"line{i}" for i in range(1, 6)).encode()
    out = await p.parse(body, mime="text/plain", options=ParseOptions(line_range="3-100"))
    assert out.content == "line3\nline4\nline5"
    assert out.metadata["lines_returned"] == "3-5"


async def test_line_range_open_end() -> None:
    """'3-' = from line 3 to end."""
    p = TextParser()
    body = "\n".join(f"line{i}" for i in range(1, 6)).encode()
    out = await p.parse(body, mime="text/plain", options=ParseOptions(line_range="3-"))
    assert out.content == "line3\nline4\nline5"
    assert out.metadata["lines_returned"] == "3-5"


async def test_line_range_negative_returns_last_n() -> None:
    """'-3' = last 3 lines (tail-style)."""
    p = TextParser()
    body = "\n".join(f"line{i}" for i in range(1, 11)).encode()
    out = await p.parse(body, mime="text/plain", options=ParseOptions(line_range="-3"))
    assert out.content == "line8\nline9\nline10"
    assert out.metadata["lines_returned"] == "8-10"


async def test_line_range_negative_more_than_file_returns_all() -> None:
    """'-100' on a 5-line file returns all 5 lines."""
    p = TextParser()
    body = "\n".join(f"line{i}" for i in range(1, 6)).encode()
    out = await p.parse(body, mime="text/plain", options=ParseOptions(line_range="-100"))
    assert out.metadata["lines_returned"] == "1-5"


async def test_no_line_range_returns_all_with_truncation_hint() -> None:
    """Without line_range and content > 20K → truncated + hint to use line_range."""
    p = TextParser()
    body = ("line\n" * 5000).encode()
    out = await p.parse(body, mime="text/plain", options=ParseOptions())
    assert out.truncated is True
    assert "line_range" in out.metadata.get("hint", "")


async def test_truncation_emits_next_line_to_read() -> None:
    """When truncated, metadata.next_line_to_read tells agent where to resume."""
    p = TextParser()
    # 1500 lines of 20 chars each ~= 30k chars (triggers truncation ~ line 1000)
    body = "\n".join("x" * 19 for _ in range(1500)).encode()
    out = await p.parse(body, mime="text/plain", options=ParseOptions())
    assert out.truncated is True
    assert "next_line_to_read" in out.metadata
    assert out.metadata["next_line_to_read"] > 1
    returned = out.metadata["lines_returned"]
    assert isinstance(returned, str)
    end = int(returned.split("-")[1])
    assert out.metadata["next_line_to_read"] == end + 1
