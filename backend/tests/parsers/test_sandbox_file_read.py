"""Sandbox.file_read default-impl integration tests."""

from uuid import uuid4

from cubeplex.parsers.schema import ParseOptions, TextOutput
from cubeplex.sandbox.base import ExecuteResult, Sandbox


class _FakeSandbox(Sandbox):
    @property
    def id(self) -> str:
        return "fake"

    @property
    def workdir(self) -> str:
        return "/work"

    async def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResult:
        raise NotImplementedError

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        raise NotImplementedError

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        return [(paths[0], b"hello world")]

    async def close(self) -> None:
        pass


async def test_file_read_default_impl_dispatches_via_registry() -> None:
    s = _FakeSandbox()
    out = await s.file_read("/tmp/a.txt", conversation_id=uuid4())
    assert isinstance(out, TextOutput)
    assert out.content == "hello world"
    assert out.path == "/tmp/a.txt"


async def test_file_read_passes_options() -> None:
    s = _FakeSandbox()
    out = await s.file_read(
        "/tmp/a.txt",
        options=ParseOptions(page_range="1-3"),
        conversation_id=None,
    )
    assert isinstance(out, TextOutput)
