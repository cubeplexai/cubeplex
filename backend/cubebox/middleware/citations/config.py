"""Citation configuration model.

Parses per-tool citation config from config.yaml and provides
methods to extract metadata and text from tool output.
"""

from typing import Any

from pydantic import BaseModel

_SNIPPET_KEY = "snippet"


class CitationConfig(BaseModel):
    """Per-tool citation configuration.

    Attributes:
        source_type: Citation source type (e.g., "web", "file").
        content_field: JSON path to result array in tool output.
                       None means the entire output is a single result.
        mapping: Maps citation metadata field names to tool output field names.
                 The special key "snippet" identifies the text field to chunk.
    """

    source_type: str
    content_field: str | None
    mapping: dict[str, str]

    def extract_metadata(self, item: dict[str, Any]) -> dict[str, Any]:
        """Extract citation metadata from a single result item."""
        metadata: dict[str, Any] = {"source_type": self.source_type}
        for meta_key, item_key in self.mapping.items():
            if meta_key == _SNIPPET_KEY:
                continue
            value = item.get(item_key)
            if value is not None:
                metadata[meta_key] = value
        return metadata

    def extract_text(self, item: dict[str, Any]) -> str:
        """Extract the text content to be chunked from a result item."""
        snippet_field = self.mapping.get(_SNIPPET_KEY)
        if snippet_field:
            return str(item.get(snippet_field, ""))
        return str(item)

    def extract_items(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract the list of result items from parsed tool output."""
        if self.content_field is None:
            return [data]
        items = data.get(self.content_field, [])
        if not isinstance(items, list):
            return [items] if items else []
        return items


def load_citation_configs(
    tool_defs: list[dict[str, Any]] | None,
) -> dict[str, CitationConfig]:
    """Build tool_name -> CitationConfig mapping from MCP tool definitions."""
    if not tool_defs:
        return {}
    configs: dict[str, CitationConfig] = {}
    for td in tool_defs:
        if not isinstance(td, dict):
            continue
        name = td.get("name")
        citation = td.get("citation")
        if name and isinstance(citation, dict):
            configs[str(name)] = CitationConfig(**citation)
    return configs
