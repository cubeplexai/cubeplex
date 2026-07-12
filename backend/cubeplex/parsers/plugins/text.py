"""TextParser: UTF-8 decode for code/config/text files."""

from __future__ import annotations

from cubeplex.parsers.schema import ParseOptions, TextOutput

MAX_CONTENT_CHARS = 20_000


class TextParser:
    mime_types = ["text/*"]
    extensions = [
        "txt", "md", "markdown", "rst", "org",
        "py", "pyi",
        "js", "ts", "jsx", "tsx", "mjs", "cjs",
        "json", "json5", "yaml", "yml", "toml", "ini", "conf", "env",
        "csv", "tsv",
        "html", "htm", "xhtml", "xml", "svg",
        "css", "scss", "sass", "less",
        "sh", "bash", "zsh", "fish",
        "sql", "graphql",
        "go", "rs", "java", "kt", "kts", "scala", "groovy",
        "c", "h", "cpp", "cc", "cxx", "hpp", "hxx",
        "rb", "php", "pl", "pm",
        "log", "lock", "properties",
    ]  # fmt: skip
    priority = 0

    async def parse(
        self,
        content: bytes,
        *,
        mime: str,
        options: ParseOptions,
    ) -> TextOutput:
        size = len(content)
        decode_fallback: str | None = None
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("latin-1", errors="replace")
            decode_fallback = "latin-1"

        all_lines = text.splitlines()
        total_lines = len(all_lines)

        start_idx, end_idx = self._parse_line_range(options.line_range, total_lines)
        sliced = "\n".join(all_lines[start_idx:end_idx])

        truncated = False
        last_line_returned = end_idx  # 1-indexed end (inclusive)
        metadata: dict[str, object] = {
            "parser": "text",
            "total_lines": total_lines,
            "total_chars": len(text),
        }
        if decode_fallback:
            metadata["decode_fallback"] = decode_fallback

        if len(sliced) > MAX_CONTENT_CHARS:
            sliced = sliced[:MAX_CONTENT_CHARS]
            truncated = True
            lines_kept = sliced.count("\n")
            single_line_truncated = lines_kept == 0 and bool(sliced)
            if single_line_truncated:
                lines_kept = 1
            last_line_returned = start_idx + lines_kept
            metadata["truncated_at_char"] = MAX_CONTENT_CHARS
            metadata["next_line_to_read"] = (
                last_line_returned if single_line_truncated else last_line_returned + 1
            )
            if options.line_range is None:
                metadata["hint"] = "content truncated; use line_range to navigate"

        metadata["lines_returned"] = f"{start_idx + 1}-{last_line_returned}"

        return TextOutput(
            path="<set-by-caller>",
            mime=mime,
            content=sliced,
            size_bytes=size,
            truncated=truncated,
            metadata=metadata,
        )

    @staticmethod
    def _parse_line_range(spec: str | None, total_lines: int) -> tuple[int, int]:
        """Parse line_range syntax to (start_index_0based, end_index_exclusive).

        Supported syntaxes (1-indexed input):
          ``42``       -> line 42 only
          ``100-200``  -> lines 100 through 200
          ``100-``     -> from line 100 to end (sed-style)
          ``-50``      -> last 50 lines (tail-style)

        Returns ``(0, total_lines)`` on None or invalid input. End clamped to total_lines.
        """
        if not spec:
            return 0, total_lines
        try:
            if spec.startswith("-"):
                n = int(spec[1:])
                if n <= 0:
                    return 0, total_lines
                start = max(total_lines - n, 0)
                return start, total_lines
            if spec.endswith("-"):
                start = max(int(spec[:-1]), 1) - 1
                start = min(start, total_lines)
                return start, total_lines
            if "-" in spec:
                a, b = spec.split("-", 1)
                start = max(int(a), 1) - 1
                end = min(int(b), total_lines)
                return start, max(end, start)
            n = min(max(int(spec), 1), total_lines) - 1
            return n, min(n + 1, total_lines)
        except (ValueError, TypeError):
            return 0, total_lines
