"""NotebookParser plugin tests."""

import json

from cubeplex.parsers.plugins.notebook import NotebookParser
from cubeplex.parsers.protocols import FileParser
from cubeplex.parsers.schema import NotebookOutput, ParseOptions


def test_satisfies_protocol() -> None:
    assert isinstance(NotebookParser(), FileParser)


async def test_parses_simple_notebook() -> None:
    nb = {
        "cells": [
            {"cell_type": "markdown", "source": ["# Title\n", "intro\n"]},
            {
                "cell_type": "code",
                "source": "print('hi')",
                "outputs": [{"output_type": "stream", "text": "hi\n"}],
            },
        ]
    }
    p = NotebookParser()
    out = await p.parse(
        json.dumps(nb).encode(),
        mime="application/x-ipynb+json",
        options=ParseOptions(),
    )
    assert isinstance(out, NotebookOutput)
    assert len(out.cells) == 2
    assert out.cells[0].cell_type == "markdown"
    assert "Title" in out.cells[0].source
    assert out.cells[1].cell_type == "code"
    assert out.cells[1].outputs is not None


async def test_truncates_when_exceeds_20k_with_next_cell_index() -> None:
    nb = {
        "cells": [
            {"cell_type": "markdown", "source": "x" * 25_000},
            {"cell_type": "code", "source": "y", "outputs": []},
            {"cell_type": "code", "source": "z", "outputs": []},
        ]
    }
    p = NotebookParser()
    out = await p.parse(
        json.dumps(nb).encode(),
        mime="application/x-ipynb+json",
        options=ParseOptions(),
    )
    # First big cell included, second + third omitted
    assert out.metadata["truncated_cells"] == 2
    assert out.metadata["cells_returned"] == 1
    assert out.metadata["next_cell_index"] == 2  # 1-indexed
    assert out.metadata["total_cells"] == 3
