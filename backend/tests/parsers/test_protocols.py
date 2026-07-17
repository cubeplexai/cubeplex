"""FileParser runtime_checkable Protocol tests."""

from cubeplex.parsers.protocols import FileParser
from cubeplex.parsers.schema import TextOutput


class _ConformingParser:
    mime_types = ["text/plain"]
    extensions = ["txt"]
    priority = 0

    async def parse(self, content, *, mime, options):  # type: ignore[no-untyped-def]
        return TextOutput(path="/tmp/x", mime=mime, content="x", size_bytes=len(content))


class _MissingParse:
    mime_types: list[str] = []
    extensions: list[str] = []
    priority = 0


def test_protocol_accepts_conforming() -> None:
    assert isinstance(_ConformingParser(), FileParser)


def test_protocol_rejects_missing_parse() -> None:
    assert not isinstance(_MissingParse(), FileParser)
