"""ParserRegistry: discover plugins via entry_points + dispatch by MIME."""

from __future__ import annotations

import asyncio
import importlib.metadata
import logging
from pathlib import Path
from typing import Any

import httpx

from cubeplex.parsers import dedup
from cubeplex.parsers.mime import sniff_mime_async
from cubeplex.parsers.protocols import FileParser
from cubeplex.parsers.schema import (
    ErrorOutput,
    FileReadOutput,
    ParseOptions,
    UnchangedOutput,
    UnsupportedOutput,
)

logger = logging.getLogger(__name__)

GROUP = "cubeplex.parsers"
MAX_FILE_BYTES = 100 * 1024 * 1024


def _is_retryable_exception(exc: BaseException) -> bool:
    """Classify parser exceptions: transient faults are retryable.

    Agents should retry network/timeout errors; parse-format errors should not
    be retried since the file content itself is the problem.
    """
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, httpx.TransportError):  # covers ConnectError, ReadTimeout, etc.
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    return False


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: list[FileParser] = []

    async def discover(self) -> None:
        """Load all FileParser plugins from entry_points."""
        for ep in importlib.metadata.entry_points(group=GROUP):
            cls = ep.load()
            instance = cls() if isinstance(cls, type) else cls
            if not isinstance(instance, FileParser):
                raise RuntimeError(f"entry_point {ep.value} does not satisfy FileParser Protocol")
            self._parsers.append(instance)
            logger.info("registered FileParser: %s (priority=%d)", ep.name, instance.priority)

        # DoclingParser needs config-injected base_url etc. Swap the default-constructed
        # instance for a config-bound one.
        from cubeplex.config import config
        from cubeplex.parsers.plugins.docling import DoclingParser

        for i, p in enumerate(self._parsers):
            if isinstance(p, DoclingParser):
                self._parsers[i] = DoclingParser(
                    base_url=config.get(
                        "parsers.docling_serve.base_url", "http://docling-serve:5001"
                    ),
                    api_key=config.get("parsers.docling_serve.api_key") or None,
                    timeout_sync_seconds=int(
                        config.get("parsers.docling_serve.timeout_sync_seconds", 30)
                    ),
                    timeout_async_minutes=int(
                        config.get("parsers.docling_serve.timeout_async_minutes", 10)
                    ),
                    async_threshold_mb=int(
                        config.get("parsers.docling_serve.async_threshold_mb", 3)
                    ),
                    poll_interval_seconds=int(
                        config.get("parsers.docling_serve.poll_interval_seconds", 2)
                    ),
                )

    def resolve(self, *, mime: str, ext: str) -> FileParser | None:
        """Pick the best parser for a (mime, ext) pair."""
        candidates: list[tuple[int, FileParser]] = []
        for p in self._parsers:
            score = self._match_score(p, mime, ext)
            if score > 0:
                candidates.append((score + p.priority, p))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    @staticmethod
    def _match_score(parser: FileParser, mime: str, ext: str) -> int:
        for pattern in parser.mime_types:
            if pattern == mime:
                return 100  # exact MIME match
            if pattern.endswith("/*") and mime.startswith(pattern[:-1]):
                return 50  # MIME wildcard
        if ext in parser.extensions:
            return 25  # extension fallback
        return 0

    async def dispatch(
        self,
        sandbox: Any,
        path: str,
        options: ParseOptions,
        conversation_id: str | None,
    ) -> FileReadOutput:
        # 1. download — test mocks expose _download_one; production Sandbox uses download.
        content = await self._download(sandbox, path)
        size = len(content)

        # 2. size precheck (backend resource protection, format-agnostic)
        if size > MAX_FILE_BYTES:
            return UnsupportedOutput(
                path=path,
                mime="application/octet-stream",
                size_bytes=size,
                reason="file too large (100MB limit)",
                hint="try reading specific pages with page_range or specific lines with line_range",
            )

        # 3. MIME sniff
        mime = await sniff_mime_async(path, content)

        # 4. dedup check only — update is deferred until after a successful parse so
        #    transient failures are not cached as "already read".
        digest: str | None = None
        if conversation_id is not None:
            try:
                digest = await dedup.hash_bytes(content)
                if await dedup.check(conversation_id, path, options, digest):
                    return UnchangedOutput(path=path)
            except Exception as exc:
                logger.warning("dedup cache unavailable, proceeding without: %s", exc)
                digest = None

        # 5. resolve plugin
        ext = Path(path).suffix.lstrip(".").lower()
        parser = self.resolve(mime=mime, ext=ext)
        if parser is None:
            # No plugin claims this MIME. We don't maintain a hardcoded REJECT
            # list (spec D22) — future plugins can claim ANY format. Do NOT
            # update dedup: user may install a plugin and retry.
            return UnsupportedOutput(
                path=path,
                mime=mime,
                size_bytes=size,
                reason=f"no parser registered for mime={mime}",
                hint=self._unsupported_hint(mime, ext),
            )

        # 6. parse
        try:
            out = await parser.parse(content, mime=mime, options=options)
        except Exception as exc:
            logger.exception("parser %s failed on %s", type(parser).__name__, path)
            return ErrorOutput(path=path, error=str(exc), retryable=_is_retryable_exception(exc))

        # 7. dedup update only after a successful parse (skip errors — transient
        #    failures like docling timeouts return ErrorOutput and must be retryable)
        if conversation_id is not None and digest is not None and not isinstance(out, ErrorOutput):
            try:
                await dedup.update(conversation_id, path, options, digest)
            except Exception as exc:
                logger.warning("dedup update failed, continuing: %s", exc)

        # Overwrite the placeholder path; preserve all other fields
        out_dict = out.model_dump()
        out_dict["path"] = path
        return type(out).model_validate(out_dict)

    @staticmethod
    async def _download(sandbox: Any, path: str) -> bytes:
        """Pull bytes via ``_download_one`` (test helper) or ``download`` (production)."""
        helper = getattr(sandbox, "_download_one", None)
        if helper is not None and callable(helper):
            result = helper(path)
            if asyncio.iscoroutine(result):
                return await result  # type: ignore[no-any-return]
            # Fall through if the helper isn't coroutine-shaped (e.g. default MagicMock)
        files = await sandbox.download([path])
        return bytes(files[0][1])

    @staticmethod
    def _unsupported_hint(mime: str, ext: str) -> str | None:
        """Format-family-aware hint for the no-parser-matched case."""
        if mime.startswith("video/"):
            return "video transcription requires a parser plugin (none installed)"
        if mime.startswith("audio/"):
            return "audio transcription requires a parser plugin (none installed)"
        archive_mimes = {
            "application/zip",
            "application/x-tar",
            "application/gzip",
            "application/x-bzip2",
            "application/x-7z-compressed",
            "application/x-rar-compressed",
            "application/x-xz",
        }
        archive_exts = {"zip", "tar", "gz", "bz2", "rar", "7z", "tgz", "xz"}
        if mime in archive_mimes or ext in archive_exts:
            return 'extract first via execute("unzip <file>") then file_read on contents'
        if ext in {"exe", "so", "dll", "dylib", "bin"}:
            return "binary executable; install a metadata parser plugin if needed"
        return None


_registry: ParserRegistry | None = None


def get_parser_registry() -> ParserRegistry:
    global _registry
    if _registry is None:
        _registry = ParserRegistry()
    return _registry


def reset_parser_registry_for_tests() -> None:
    global _registry
    _registry = None
