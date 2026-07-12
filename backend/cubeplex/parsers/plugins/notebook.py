"""NotebookParser: parse Jupyter .ipynb into structured cells."""

from __future__ import annotations

import json
from typing import Any

from cubeplex.parsers.schema import NotebookCell, NotebookOutput, ParseOptions

MAX_CONTENT_CHARS = 20_000


class NotebookParser:
    mime_types = ["application/x-ipynb+json"]
    extensions = ["ipynb"]
    priority = 10

    async def parse(
        self,
        content: bytes,
        *,
        mime: str,
        options: ParseOptions,
    ) -> NotebookOutput:
        try:
            nb = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid notebook JSON: {e}") from e

        all_cells = nb.get("cells", [])
        result_cells: list[NotebookCell] = []
        running_chars = 0
        truncated_cells = 0

        for raw in all_cells:
            cell_type = raw.get("cell_type", "raw")
            if cell_type not in ("code", "markdown", "raw"):
                cell_type = "raw"
            source = raw.get("source", "")
            if isinstance(source, list):
                source = "".join(source)

            outputs: list[dict[str, Any]] | None = None
            if cell_type == "code":
                outputs = []
                for o in raw.get("outputs", []):
                    if "text" in o:
                        text = o["text"]
                        if isinstance(text, list):
                            text = "".join(text)
                        outputs.append({"type": o.get("output_type", "stream"), "text": text})
                    else:
                        # Drop image base64 blobs; keep a marker only
                        outputs.append({"type": o.get("output_type", "unknown")})

            result_cells.append(NotebookCell(cell_type=cell_type, source=source, outputs=outputs))
            running_chars += len(source) + sum(len(o.get("text", "")) for o in (outputs or []))

            if running_chars > MAX_CONTENT_CHARS:
                truncated_cells = len(all_cells) - len(result_cells)
                break

        metadata: dict[str, Any] = {
            "parser": "notebook",
            "total_cells": len(all_cells),
            "cells_returned": len(result_cells),
        }
        if truncated_cells > 0:
            metadata["truncated_cells"] = truncated_cells
            metadata["next_cell_index"] = len(result_cells) + 1  # 1-indexed

        return NotebookOutput(
            path="<set-by-caller>",
            cells=result_cells,
            metadata=metadata,
        )
