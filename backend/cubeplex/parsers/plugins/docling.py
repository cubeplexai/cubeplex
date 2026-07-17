"""DoclingParser: HTTP client to docling-serve."""

from __future__ import annotations

import asyncio
import base64
import re
from typing import Any

import httpx

from cubeplex.parsers.schema import ErrorOutput, FileReadOutput, ParseOptions, TextOutput

MAX_CONTENT_CHARS = 20_000
DOCLING_IMAGE_EXPORT_MODE = "placeholder"


class DoclingParser:
    mime_types = [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/epub+zip",
        "image/*",
    ]
    extensions = [
        "pdf", "docx", "pptx", "xlsx", "epub",
        "png", "jpg", "jpeg", "gif", "webp", "tiff", "bmp",
    ]  # fmt: skip
    priority = 20

    def __init__(
        self,
        *,
        base_url: str = "http://docling-serve:5001",
        api_key: str | None = None,
        timeout_sync_seconds: int = 30,
        timeout_async_minutes: int = 10,
        async_threshold_mb: int = 3,
        poll_interval_seconds: int = 2,
        _transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_sync = timeout_sync_seconds
        self.timeout_async_seconds = timeout_async_minutes * 60
        self.async_threshold_bytes = async_threshold_mb * 1024 * 1024
        self.poll_interval = poll_interval_seconds
        self._transport = _transport

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-Api-Key"] = self.api_key
        return h

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            transport=self._transport,
        )

    async def parse(
        self,
        content: bytes,
        *,
        mime: str,
        options: ParseOptions,
    ) -> FileReadOutput:
        if len(content) < self.async_threshold_bytes:
            return await self._parse_sync(content, mime, options)
        return await self._parse_async(content, mime, options)

    async def _parse_sync(
        self,
        content: bytes,
        mime: str,
        options: ParseOptions,
    ) -> FileReadOutput:
        body = {
            "sources": [
                {
                    "kind": "file",
                    "filename": "input",
                    "base64_string": base64.b64encode(content).decode("ascii"),
                }
            ],
            "options": self._build_options(options),
        }
        try:
            async with self._client(timeout=self.timeout_sync) as client:
                resp = await client.post(
                    "/v1/convert/source",
                    json=body,
                    headers=self._headers(),
                )
                if resp.status_code >= 500:
                    return ErrorOutput(
                        path="<set-by-caller>",
                        error=f"docling-serve {resp.status_code}: {resp.text[:200]}",
                        retryable=True,
                    )
                if resp.status_code >= 400:
                    return ErrorOutput(
                        path="<set-by-caller>",
                        error=f"docling-serve {resp.status_code}: {resp.text[:200]}",
                        retryable=False,
                    )
                data = resp.json()
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            return ErrorOutput(
                path="<set-by-caller>",
                error=f"docling-serve unreachable: {e}",
                retryable=True,
            )
        return self._make_text_output(data, content, mime)

    async def _parse_async(
        self,
        content: bytes,
        mime: str,
        options: ParseOptions,
    ) -> FileReadOutput:
        body = {
            "sources": [
                {
                    "kind": "file",
                    "filename": "input",
                    "base64_string": base64.b64encode(content).decode("ascii"),
                }
            ],
            "options": self._build_options(options),
        }
        try:
            async with self._client(timeout=self.timeout_async_seconds) as client:
                resp = await client.post(
                    "/v1/convert/source/async",
                    json=body,
                    headers=self._headers(),
                )
                if resp.status_code >= 400:
                    return ErrorOutput(
                        path="<set-by-caller>",
                        error=f"docling-serve submit {resp.status_code}: {resp.text[:200]}",
                        retryable=resp.status_code >= 500,
                    )
                task_id = resp.json().get("task_id")
                if not task_id:
                    return ErrorOutput(
                        path="<set-by-caller>",
                        error="docling-serve async submit returned no task_id",
                        retryable=False,
                    )

                deadline = asyncio.get_event_loop().time() + self.timeout_async_seconds
                while asyncio.get_event_loop().time() < deadline:
                    poll = await client.get(
                        f"/v1/status/poll/{task_id}",
                        headers=self._headers(),
                    )
                    if poll.status_code >= 500:
                        await asyncio.sleep(self.poll_interval)
                        continue
                    if poll.status_code >= 400:
                        return ErrorOutput(
                            path="<set-by-caller>",
                            error=f"docling-serve poll {poll.status_code}: {poll.text[:200]}",
                            retryable=False,
                        )
                    status = (poll.json().get("task_status") or "").lower()
                    if status in ("success", "partial_success"):
                        result_resp = await client.get(
                            f"/v1/result/{task_id}",
                            headers=self._headers(),
                        )
                        if result_resp.status_code >= 400:
                            return ErrorOutput(
                                path="<set-by-caller>",
                                error=(
                                    f"docling-serve result {result_resp.status_code}: "
                                    f"{result_resp.text[:200]}"
                                ),
                                retryable=result_resp.status_code >= 500,
                            )
                        return self._make_text_output(result_resp.json(), content, mime)
                    if status == "failure":
                        return ErrorOutput(
                            path="<set-by-caller>",
                            error=(
                                "docling-serve task failed: "
                                f"{poll.json().get('error_message') or 'no detail'}"
                            ),
                            retryable=False,
                        )
                    await asyncio.sleep(self.poll_interval)

                return ErrorOutput(
                    path="<set-by-caller>",
                    error="docling-serve async timeout",
                    retryable=True,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            return ErrorOutput(
                path="<set-by-caller>",
                error=f"docling-serve unreachable: {e}",
                retryable=True,
            )

    def _build_options(self, options: ParseOptions) -> dict[str, object]:
        opts: dict[str, object] = {"image_export_mode": DOCLING_IMAGE_EXPORT_MODE}
        if options.page_range:
            opts["page_range"] = options.page_range
        if options.language_hint:
            opts["lang"] = options.language_hint
        return opts

    def _make_text_output(self, data: dict[str, Any], content: bytes, mime: str) -> TextOutput:
        # docling-serve response shape: {"document": {"md_content": "..."}}
        md = (data.get("document") or {}).get("md_content", "")
        if not isinstance(md, str):
            md = str(md)

        truncated = False
        total = len(md)
        metadata: dict[str, object] = {"parser": "docling", "total_chars": total}
        if total > MAX_CONTENT_CHARS:
            truncated_md = md[:MAX_CONTENT_CHARS]
            truncated = True
            metadata["truncated_at_char"] = MAX_CONTENT_CHARS

            # Best-effort: extract last page marker visible in truncated md.
            last_page = self._extract_last_page(truncated_md)
            if last_page is not None:
                metadata["last_page_returned"] = last_page
                metadata["next_page_to_read"] = last_page + 1
            else:
                metadata["hint"] = "use page_range to read later sections"
            md = truncated_md

        return TextOutput(
            path="<set-by-caller>",
            mime=mime,
            content=md,
            size_bytes=len(content),
            truncated=truncated,
            metadata=metadata,
        )

    @staticmethod
    def _extract_last_page(md: str) -> int | None:
        """Best-effort scan for the last page marker in docling markdown.

        Patterns checked (last occurrence wins):
          * HTML comments:  ``<!-- page N -->``
          * Heading line:   ``## Page N``
          * PageBreak:      ``<!-- PageBreak: N -->``

        Returns None if no marker found.
        """
        patterns = [
            re.compile(r"<!--\s*page[:\s]+(\d+)\s*-->", re.IGNORECASE),
            re.compile(r"<!--\s*PageBreak[:\s]+(\d+)\s*-->", re.IGNORECASE),
            re.compile(r"^#+\s+Page\s+(\d+)\s*$", re.MULTILINE | re.IGNORECASE),
        ]
        last_page: int | None = None
        for pat in patterns:
            for m in pat.finditer(md):
                try:
                    last_page = int(m.group(1))
                except (ValueError, IndexError):
                    pass
        return last_page
