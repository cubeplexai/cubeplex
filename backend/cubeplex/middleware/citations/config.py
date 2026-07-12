"""Citation configuration model.

Parses per-tool citation config from config.yaml and provides
methods to extract metadata and text from tool output.
"""

from typing import Any, Literal

from pydantic import BaseModel

_SNIPPET_KEY = "snippet"


class CitationConfig(BaseModel):
    """Per-tool citation configuration.

    Attributes:
        content_type: How the tool output is encoded. "json" runs the
                      response through JSON parsing; "text" treats it as
                      a single text blob (used by e.g. web_fetch).
        source_type: Citation source type (e.g., "web", "file").
        content_field: JSON path to result array in tool output.
                       None means the entire output is a single result.
        mapping: Maps citation metadata field names to tool output field names.
                 The special key "snippet" identifies the text field to chunk.
        args_mapping: Maps citation metadata field names to tool call argument names.
                      Used as fallback when metadata fields are missing from the result
                      (e.g., web_fetch returns plain text but the URL is in the args).
        discriminator_field: Field name to check for filtering results.
                             If set, results are filtered by discriminator_values.
        discriminator_values: Allowed values for discriminator_field.
                              Results with other values are skipped.
    """

    content_type: Literal["json", "text"] = "json"
    source_type: str
    content_field: str | None
    mapping: dict[str, str]
    args_mapping: dict[str, str] | None = None
    discriminator_field: str | None = None
    discriminator_values: list[str] | None = None

    def extract_metadata(
        self,
        item: dict[str, Any],
        tool_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Extract citation metadata from a single result item.

        Falls back to tool_args via args_mapping for fields not found in item.
        """
        metadata: dict[str, Any] = {"source_type": self.source_type}
        for meta_key, item_key in self.mapping.items():
            if meta_key == _SNIPPET_KEY:
                continue
            value = item.get(item_key)
            if value is not None:
                metadata[meta_key] = value
        # Fill missing metadata from tool call arguments
        if tool_args and self.args_mapping:
            for meta_key, arg_key in self.args_mapping.items():
                if meta_key not in metadata:
                    value = tool_args.get(arg_key)
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
        """Extract the list of result items from parsed tool output.

        ``content_field`` may be a single top-level key (``"results"``) or
        a dotted path (``"data.webPages.value"``) — the path is walked
        with ``dict.get`` at each step, returning ``[]`` if any segment
        is missing. Dotted paths are how providers like Bocha nest the
        result array under metadata wrappers without us flattening the
        payload before extraction.
        """
        if self.discriminator_field:
            value = data.get(self.discriminator_field)
            if self.discriminator_values and value not in self.discriminator_values:
                return []
        if self.content_field is None:
            return [data]
        items: Any = data
        for segment in self.content_field.split("."):
            if not isinstance(items, dict):
                return []
            items = items.get(segment)
            if items is None:
                return []
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
