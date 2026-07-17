"""File parser plugin registry shared by file_read tool and future filebox."""

from cubeplex.parsers.protocols import FileParser
from cubeplex.parsers.registry import (
    ParserRegistry,
    get_parser_registry,
    reset_parser_registry_for_tests,
)
from cubeplex.parsers.schema import (
    ErrorOutput,
    FileReadOutput,
    NotebookCell,
    NotebookOutput,
    ParseOptions,
    TextOutput,
    UnchangedOutput,
    UnsupportedOutput,
)

__all__ = [
    "ErrorOutput",
    "FileParser",
    "FileReadOutput",
    "NotebookCell",
    "NotebookOutput",
    "ParseOptions",
    "ParserRegistry",
    "TextOutput",
    "UnchangedOutput",
    "UnsupportedOutput",
    "get_parser_registry",
    "reset_parser_registry_for_tests",
]
