"""ParserRegistry discover + dispatch tests."""

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import fakeredis.aioredis
import pytest

from cubeplex.parsers.registry import (
    ParserRegistry,
    reset_parser_registry_for_tests,
)
from cubeplex.parsers.schema import (
    ErrorOutput,
    ParseOptions,
    TextOutput,
    UnchangedOutput,
    UnsupportedOutput,
)


@pytest.fixture(autouse=True)
def fresh_registry() -> Iterator[None]:
    reset_parser_registry_for_tests()
    yield
    reset_parser_registry_for_tests()


@pytest.fixture
def fake_redis() -> Iterator[fakeredis.aioredis.FakeRedis]:
    """Register a fakeredis as the shared cache for dedup tests."""
    from cubeplex.cache import reset_for_tests, set_redis

    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    set_redis(fake)
    try:
        yield fake
    finally:
        reset_for_tests()


async def test_resolve_picks_text_for_python_file() -> None:
    reg = ParserRegistry()
    await reg.discover()
    parser = reg.resolve(mime="text/x-python", ext="py")
    assert parser is not None
    assert "text" in type(parser).__name__.lower()


async def test_resolve_picks_docling_for_pdf() -> None:
    reg = ParserRegistry()
    await reg.discover()
    parser = reg.resolve(mime="application/pdf", ext="pdf")
    assert parser is not None
    assert "docling" in type(parser).__name__.lower()


async def test_resolve_picks_notebook_for_ipynb() -> None:
    reg = ParserRegistry()
    await reg.discover()
    parser = reg.resolve(mime="application/x-ipynb+json", ext="ipynb")
    assert parser is not None
    assert "notebook" in type(parser).__name__.lower()


async def test_dispatch_unsupported_when_no_plugin_matches(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """No plugin claims video/* → returns unsupported with format-aware hint."""
    sandbox = MagicMock()
    # Plausible MP4 magic so libmagic detects video/mp4
    sandbox._download_one = AsyncMock(return_value=b"\x00\x00\x00\x20ftypisom" + b"\x00" * 200)
    reg = ParserRegistry()
    await reg.discover()
    out = await reg.dispatch(
        sandbox=sandbox,
        path="/tmp/movie.mp4",
        options=ParseOptions(),
        conversation_id=uuid4(),
    )
    assert isinstance(out, UnsupportedOutput)
    assert "no parser registered" in out.reason
    assert out.hint is not None
    assert "video" in out.hint.lower()


async def test_dispatch_unsupported_archive_hint(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Archives suggest extract-then-read flow."""
    sandbox = MagicMock()
    sandbox._download_one = AsyncMock(return_value=b"PK\x03\x04" + b"\x00" * 200)
    reg = ParserRegistry()
    await reg.discover()
    out = await reg.dispatch(
        sandbox=sandbox,
        path="/tmp/data.zip",
        options=ParseOptions(),
        conversation_id=uuid4(),
    )
    assert isinstance(out, UnsupportedOutput)
    assert out.hint is not None
    assert "extract" in out.hint.lower() or "unzip" in out.hint.lower()


async def test_dispatch_rejects_oversize() -> None:
    sandbox = MagicMock()
    sandbox._download_one = AsyncMock(return_value=b"\x00" * (101 * 1024 * 1024))
    reg = ParserRegistry()
    await reg.discover()
    out = await reg.dispatch(
        sandbox=sandbox,
        path="/tmp/big.txt",
        options=ParseOptions(),
        conversation_id=uuid4(),
    )
    assert isinstance(out, UnsupportedOutput)
    assert "100" in out.reason or "large" in out.reason.lower()


async def test_dispatch_returns_unchanged_on_second_read(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Same content + same options + same conv → second call returns UnchangedOutput."""
    sandbox = MagicMock()
    sandbox._download_one = AsyncMock(return_value=b"hello world")
    reg = ParserRegistry()
    await reg.discover()
    conv = uuid4()

    first = await reg.dispatch(
        sandbox=sandbox, path="/tmp/a.txt", options=ParseOptions(), conversation_id=conv
    )
    assert isinstance(first, TextOutput)

    second = await reg.dispatch(
        sandbox=sandbox, path="/tmp/a.txt", options=ParseOptions(), conversation_id=conv
    )
    assert isinstance(second, UnchangedOutput)


async def test_dispatch_different_line_range_does_not_unchanged(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Different line_range = different cache key = re-parses."""
    sandbox = MagicMock()
    body = "\n".join(f"line{i}" for i in range(1, 21)).encode()
    sandbox._download_one = AsyncMock(return_value=body)
    reg = ParserRegistry()
    await reg.discover()
    conv = uuid4()

    first = await reg.dispatch(
        sandbox=sandbox,
        path="/tmp/log.txt",
        options=ParseOptions(line_range="1-5"),
        conversation_id=conv,
    )
    assert isinstance(first, TextOutput)

    second = await reg.dispatch(
        sandbox=sandbox,
        path="/tmp/log.txt",
        options=ParseOptions(line_range="10-15"),
        conversation_id=conv,
    )
    # Different line_range → different cache slot → re-parses.
    assert isinstance(second, TextOutput)
    assert "line10" in second.content


async def test_dispatch_overwrites_path_in_output() -> None:
    sandbox = MagicMock()
    sandbox._download_one = AsyncMock(return_value=b"x = 1")
    reg = ParserRegistry()
    await reg.discover()
    out = await reg.dispatch(
        sandbox=sandbox,
        path="/tmp/script.py",
        options=ParseOptions(),
        conversation_id=None,
    )
    assert isinstance(out, TextOutput)
    assert out.path == "/tmp/script.py"


async def test_dispatch_wraps_parser_exceptions_as_error() -> None:
    """Parser raising ValueError -> ErrorOutput with retryable=False."""

    class BadFormatParser:
        priority = 50
        mime_types = ["text/plain"]
        extensions = ["txt"]

        async def parse(self, content: bytes, *, mime: str, options: ParseOptions):  # type: ignore[no-untyped-def]
            raise ValueError("invalid format")

    sandbox = MagicMock()
    sandbox._download_one = AsyncMock(return_value=b"garbage")
    reg = ParserRegistry()
    reg._parsers = [BadFormatParser()]  # type: ignore[list-item]
    out = await reg.dispatch(
        sandbox=sandbox,
        path="/tmp/x.txt",
        options=ParseOptions(),
        conversation_id=None,
    )
    assert isinstance(out, ErrorOutput)
    # Parse-format error: not retryable.
    assert out.retryable is False


async def test_dispatch_marks_transient_errors_retryable() -> None:
    """httpx.TransportError / timeout → retryable=True so agents can retry."""
    import httpx

    class FlakyParser:
        priority = 50
        mime_types = ["application/pdf"]
        extensions = ["pdf"]

        async def parse(self, content: bytes, *, mime: str, options: ParseOptions):  # type: ignore[no-untyped-def]
            raise httpx.ConnectError("connection refused")

    sandbox = MagicMock()
    sandbox._download_one = AsyncMock(return_value=b"%PDF-1.4\n...")
    reg = ParserRegistry()
    reg._parsers = [FlakyParser()]  # type: ignore[list-item]
    out = await reg.dispatch(
        sandbox=sandbox,
        path="/tmp/x.pdf",
        options=ParseOptions(),
        conversation_id=None,
    )
    assert isinstance(out, ErrorOutput)
    assert out.retryable is True


async def test_dispatch_does_not_cache_unsupported_so_retry_works(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Unsupported result must NOT update dedup; user can install a plugin and retry."""
    sandbox = MagicMock()
    sandbox._download_one = AsyncMock(return_value=b"\x00\x01binary")
    reg = ParserRegistry()
    reg._parsers = []  # no plugins → unsupported
    conv = uuid4()

    first = await reg.dispatch(
        sandbox=sandbox,
        path="/tmp/x.bin",
        options=ParseOptions(),
        conversation_id=conv,
    )
    assert isinstance(first, UnsupportedOutput)

    class BinParser:
        priority = 10
        mime_types = ["application/octet-stream"]
        extensions = ["bin"]

        async def parse(self, content: bytes, *, mime: str, options: ParseOptions):  # type: ignore[no-untyped-def]
            return TextOutput(
                path="<set-by-caller>",
                mime=mime,
                content="parsed",
                size_bytes=len(content),
            )

    reg._parsers = [BinParser()]  # type: ignore[list-item]
    second = await reg.dispatch(
        sandbox=sandbox,
        path="/tmp/x.bin",
        options=ParseOptions(),
        conversation_id=conv,
    )
    assert isinstance(second, TextOutput), "unsupported must not be cached"


async def test_dispatch_does_not_cache_error_so_retry_works(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """ErrorOutput must NOT update dedup; transient failures should be retryable end-to-end."""
    import httpx

    call_count = {"n": 0}

    class RecoveringParser:
        priority = 50
        mime_types = ["application/pdf"]
        extensions = ["pdf"]

        async def parse(self, content: bytes, *, mime: str, options: ParseOptions):  # type: ignore[no-untyped-def]
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise httpx.ConnectError("transient outage")
            return TextOutput(
                path="<set-by-caller>",
                mime=mime,
                content="ok",
                size_bytes=len(content),
            )

    sandbox = MagicMock()
    sandbox._download_one = AsyncMock(return_value=b"%PDF-1.4\n...")
    reg = ParserRegistry()
    reg._parsers = [RecoveringParser()]  # type: ignore[list-item]
    conv = uuid4()

    first = await reg.dispatch(
        sandbox=sandbox,
        path="/tmp/x.pdf",
        options=ParseOptions(),
        conversation_id=conv,
    )
    assert isinstance(first, ErrorOutput)
    assert first.retryable is True

    second = await reg.dispatch(
        sandbox=sandbox,
        path="/tmp/x.pdf",
        options=ParseOptions(),
        conversation_id=conv,
    )
    assert isinstance(second, TextOutput), "error must not be cached"
