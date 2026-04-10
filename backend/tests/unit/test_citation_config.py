import pytest

from cubebox.middleware.citations.config import CitationConfig, load_citation_configs


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
