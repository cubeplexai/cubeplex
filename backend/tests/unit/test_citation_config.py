from langchain_core.tools import StructuredTool

from cubebox.middleware.citations.config import (
    CitationConfig,
    load_builtin_citation_configs,
    load_citation_configs,
)


class TestCitationConfig:
    def test_basic_config(self):
        cfg = CitationConfig(
            source_type="web",
            content_field="results",
            mapping={"url": "link", "title": "title", "snippet": "snippet"},
        )
        assert cfg.source_type == "web"
        assert cfg.content_field == "results"
        assert cfg.mapping["url"] == "link"

    def test_content_field_none(self):
        cfg = CitationConfig(
            source_type="web",
            content_field=None,
            mapping={"url": "url", "title": "title"},
        )
        assert cfg.content_field is None

    def test_extract_metadata_from_item(self):
        cfg = CitationConfig(
            source_type="web",
            content_field="results",
            mapping={"url": "link", "title": "name", "snippet": "body"},
        )
        item = {"link": "https://example.com", "name": "Example", "body": "Content here"}
        metadata = cfg.extract_metadata(item)
        assert metadata["source_type"] == "web"
        assert metadata["url"] == "https://example.com"
        assert metadata["title"] == "Example"
        assert "snippet" not in metadata

    def test_extract_metadata_missing_fields(self):
        cfg = CitationConfig(
            source_type="web",
            content_field=None,
            mapping={"url": "link", "title": "name"},
        )
        item = {"link": "https://example.com"}
        metadata = cfg.extract_metadata(item)
        assert metadata["url"] == "https://example.com"
        assert metadata.get("title") is None

    def test_extract_text_from_snippet_field(self):
        cfg = CitationConfig(
            source_type="web",
            content_field="results",
            mapping={"url": "link", "snippet": "body"},
        )
        item = {"link": "https://example.com", "body": "The actual text content"}
        text = cfg.extract_text(item)
        assert text == "The actual text content"

    def test_extract_text_no_snippet_uses_str(self):
        cfg = CitationConfig(
            source_type="web",
            content_field=None,
            mapping={"url": "link"},
        )
        item = {"link": "https://example.com", "content": "Fallback text"}
        text = cfg.extract_text(item)
        assert "Fallback text" in text

    def test_extract_items_from_array_field(self):
        cfg = CitationConfig(
            source_type="web",
            content_field="results",
            mapping={"url": "link"},
        )
        data = {"results": [{"link": "a"}, {"link": "b"}]}
        items = cfg.extract_items(data)
        assert len(items) == 2

    def test_extract_items_null_content_field(self):
        cfg = CitationConfig(
            source_type="web",
            content_field=None,
            mapping={"url": "link"},
        )
        data = {"link": "https://example.com", "text": "content"}
        items = cfg.extract_items(data)
        assert len(items) == 1
        assert items[0] is data


class TestLoadCitationConfigs:
    def test_load_from_mcp_tool_configs(self):
        tool_defs = [
            {
                "name": "web_search",
                "citation": {
                    "source_type": "web",
                    "content_field": "results",
                    "mapping": {"url": "link", "title": "title"},
                },
            },
            {"name": "calculator"},
        ]
        configs = load_citation_configs(tool_defs)
        assert "web_search" in configs
        assert "calculator" not in configs
        assert configs["web_search"].source_type == "web"

    def test_load_empty_returns_empty(self):
        assert load_citation_configs([]) == {}
        assert load_citation_configs(None) == {}

    def test_load_with_args_mapping(self):
        tool_defs = [
            {
                "name": "web_fetch",
                "citation": {
                    "source_type": "web",
                    "content_field": None,
                    "mapping": {"snippet": "text"},
                    "args_mapping": {"url": "url", "title": "title"},
                },
            },
        ]
        configs = load_citation_configs(tool_defs)
        assert configs["web_fetch"].args_mapping == {"url": "url", "title": "title"}


class TestArgsMapping:
    def test_args_mapping_fills_missing_metadata(self):
        cfg = CitationConfig(
            source_type="web",
            content_field=None,
            mapping={"snippet": "text"},
            args_mapping={"url": "url", "title": "title"},
        )
        item = {"text": "Page content here"}
        tool_args = {"url": "https://example.com", "title": "Example Page"}
        metadata = cfg.extract_metadata(item, tool_args=tool_args)
        assert metadata["source_type"] == "web"
        assert metadata["url"] == "https://example.com"
        assert metadata["title"] == "Example Page"

    def test_result_metadata_takes_precedence_over_args(self):
        cfg = CitationConfig(
            source_type="web",
            content_field=None,
            mapping={"url": "link", "snippet": "text"},
            args_mapping={"url": "url"},
        )
        item = {"link": "https://from-result.com", "text": "content"}
        tool_args = {"url": "https://from-args.com"}
        metadata = cfg.extract_metadata(item, tool_args=tool_args)
        assert metadata["url"] == "https://from-result.com"

    def test_no_args_mapping_works(self):
        cfg = CitationConfig(
            source_type="web",
            content_field=None,
            mapping={"snippet": "text"},
        )
        item = {"text": "content"}
        metadata = cfg.extract_metadata(item, tool_args={"url": "https://example.com"})
        assert "url" not in metadata

    def test_args_mapping_with_no_tool_args(self):
        cfg = CitationConfig(
            source_type="web",
            content_field=None,
            mapping={"snippet": "text"},
            args_mapping={"url": "url"},
        )
        item = {"text": "content"}
        metadata = cfg.extract_metadata(item)
        assert "url" not in metadata


class TestCitationDiscriminator:
    def _file_cfg(self) -> CitationConfig:
        return CitationConfig(
            source_type="file",
            content_field=None,
            discriminator_field="kind",
            discriminator_values=["text"],
            mapping={"snippet": "content", "path": "path"},
        )

    def test_discriminator_allows_matching_kind(self):
        cfg = self._file_cfg()
        items = cfg.extract_items({"kind": "text", "path": "/a.md", "content": "hello"})
        assert len(items) == 1
        assert items[0]["content"] == "hello"

    def test_discriminator_rejects_other_kind(self):
        cfg = self._file_cfg()
        assert cfg.extract_items({"kind": "notebook", "path": "/a.ipynb"}) == []
        assert cfg.extract_items({"kind": "unsupported", "path": "/a.bin"}) == []
        assert cfg.extract_items({"kind": "error", "path": "/a.md", "error": "boom"}) == []

    def test_discriminator_no_field_set_passes_through(self):
        # No discriminator_field → behaviour unchanged from before.
        cfg = CitationConfig(
            source_type="web",
            content_field=None,
            mapping={"snippet": "body"},
        )
        assert cfg.extract_items({"body": "x"}) == [{"body": "x"}]


def _make_tool(name: str, metadata: dict | None) -> StructuredTool:
    async def _noop() -> str:
        return ""

    return StructuredTool.from_function(
        coroutine=_noop,
        name=name,
        description="t",
        metadata=metadata,
    )


class TestLoadBuiltinCitationConfigs:
    def test_reads_citation_from_tool_metadata(self):
        tool = _make_tool(
            "file_read",
            {
                "content_type": "file_read",
                "citation": {
                    "source_type": "file",
                    "content_field": None,
                    "mapping": {"snippet": "content"},
                },
            },
        )
        configs = load_builtin_citation_configs([tool])
        assert "file_read" in configs
        assert configs["file_read"].source_type == "file"

    def test_skips_tool_without_citation_metadata(self):
        tool = _make_tool("execute", {"content_type": "shell"})
        assert load_builtin_citation_configs([tool]) == {}

    def test_skips_tool_without_metadata(self):
        tool = _make_tool("calculator", None)
        assert load_builtin_citation_configs([tool]) == {}

    def test_empty_input_returns_empty(self):
        assert load_builtin_citation_configs([]) == {}


class TestFileReadToolCitationWiring:
    def test_file_read_tool_metadata_loadable(self):
        from unittest.mock import MagicMock

        from cubebox.middleware.sandbox import _create_file_read_tool

        sandbox = MagicMock()
        tool = _create_file_read_tool(sandbox, conversation_id=None)
        configs = load_builtin_citation_configs([tool])
        assert "file_read" in configs
        cfg = configs["file_read"]
        assert cfg.source_type == "file"
        assert cfg.discriminator_field == "kind"
        assert cfg.discriminator_values == ["text"]
        # text result chunks via 'content'
        items = cfg.extract_items({"kind": "text", "path": "/a.md", "content": "x" * 50})
        assert len(items) == 1
        # error result is filtered
        assert cfg.extract_items({"kind": "error", "path": "/a.md", "error": "boom"}) == []
