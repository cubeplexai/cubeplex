"""Unit tests for search-MCP citation extraction against real captured shapes.

The fixtures in ``tests/unit/fixtures/search_responses/`` were captured from
each provider's HTTP API on 2026-05-27 with a live key (keys are gitignored
in ``backend/.env``). The catalog seed entries for Tavily / Exa / Jina map
``CitationConfig`` fields against those exact shapes, and the citation
middleware feeds the same shapes through ``CitationConfig.extract_items`` +
``extract_metadata`` + ``extract_text``. If a provider changes their JSON
shape under us, these tests fail before deploy rather than at runtime.

Bocha and Perplexity are NOT seeded in the catalog (no clean hosted MCP at
PR time — see spec Open Questions). Bocha's captured REST shape is still
asserted here because the v1 plan extends ``content_field`` to walk dotted
paths (``data.webPages.value``); having the test guards that helper.

Live (``@pytest.mark.requires_api_key``) tests skip in CI when the env
vars are not set; they re-hit each provider's REST endpoint and re-validate
the captured shape against today's response, so a silent provider-side
schema change is caught before it bites a workspace.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from cubeplex.mcp.template_seed import CATALOG, MCPConnectorTemplateSeedEntry
from cubeplex.middleware.citations.config import CitationConfig

_FIXTURES = Path(__file__).parent / "fixtures" / "search_responses"


def _entry(slug: str) -> MCPConnectorTemplateSeedEntry:
    return next(e for e in CATALOG if e.slug == slug)


def _load(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Captured-shape tests — run in CI without keys.
# ---------------------------------------------------------------------------


def test_tavily_search_extracts_url_title_snippet_from_real_shape() -> None:
    """Tavily REST/MCP returns ``{results: [{url, title, content, ...}]}``."""
    raw = _load("tavily_search.json")
    cfg = CitationConfig(**_entry("tavily").tool_citation_defaults["tavily_search"])
    items = cfg.extract_items(raw)
    assert len(items) >= 1
    metadata = cfg.extract_metadata(items[0])
    assert metadata["source_type"] == "web"
    assert metadata["url"].startswith("http")
    assert metadata["title"]
    snippet = cfg.extract_text(items[0])
    assert snippet, "tavily snippet must come from the 'content' field"


def test_exa_search_extracts_url_title_snippet_from_real_shape() -> None:
    """Exa REST/MCP returns ``{results: [{url, title, text, ...}]}``."""
    raw = _load("exa_search.json")
    cfg = CitationConfig(**_entry("exa").tool_citation_defaults["web_search_exa"])
    items = cfg.extract_items(raw)
    assert len(items) >= 1
    metadata = cfg.extract_metadata(items[0])
    assert metadata["source_type"] == "web"
    assert metadata["url"].startswith("http")
    assert metadata["title"]
    # Snippet pulled from the ``text`` field — Exa's full extracted content.
    snippet = cfg.extract_text(items[0])
    assert snippet


def test_jina_search_extracts_url_title_snippet_from_real_shape() -> None:
    """Jina ``s.jina.ai`` (and ``search_web`` MCP) returns
    ``{code, status, data: [{url, title, description, ...}], meta}``."""
    raw = _load("jina_search.json")
    cfg = CitationConfig(**_entry("jina").tool_citation_defaults["search_web"])
    items = cfg.extract_items(raw)
    assert len(items) >= 1
    metadata = cfg.extract_metadata(items[0])
    assert metadata["source_type"] == "web"
    assert metadata["url"].startswith("http")
    assert metadata["title"]
    snippet = cfg.extract_text(items[0])
    assert snippet


def test_bocha_search_extracts_from_nested_dotted_path() -> None:
    """Bocha nests results under ``data.webPages.value`` — this is the
    motivating case for ``extract_items`` walking a dotted ``content_field``.
    """
    raw = _load("bocha_search.json")
    # Bocha isn't a seeded catalog entry yet (no hosted MCP); the config
    # below is the shape a future seed would use, and is the canonical
    # exemplar of dotted-path citation extraction.
    cfg = CitationConfig(
        content_type="json",
        source_type="web",
        content_field="data.webPages.value",
        mapping={"url": "url", "title": "name", "snippet": "snippet"},
    )
    items = cfg.extract_items(raw)
    assert len(items) >= 1, "dotted-path content_field failed to find the array"
    metadata = cfg.extract_metadata(items[0])
    assert metadata["source_type"] == "web"
    assert metadata["url"].startswith("http")
    assert metadata["title"]
    snippet = cfg.extract_text(items[0])
    assert snippet


def test_extract_items_returns_empty_when_dotted_path_misses() -> None:
    cfg = CitationConfig(
        content_type="json",
        source_type="web",
        content_field="a.b.c",
        mapping={"snippet": "x"},
    )
    assert cfg.extract_items({"a": {"b": {}}}) == []
    assert cfg.extract_items({"a": "scalar"}) == []
    assert cfg.extract_items({}) == []


def test_extract_items_single_key_still_works() -> None:
    """Single-key ``content_field`` must keep its existing behaviour
    (the dotted-path change is purely additive)."""
    cfg = CitationConfig(
        content_type="json",
        source_type="web",
        content_field="results",
        mapping={"url": "url", "snippet": "content"},
    )
    items = cfg.extract_items({"results": [{"url": "x", "content": "y"}]})
    assert items == [{"url": "x", "content": "y"}]


# ---------------------------------------------------------------------------
# Live API smoke tests — opt-in, never run in default CI.
# ---------------------------------------------------------------------------


def _key(name: str) -> str | None:
    val = os.environ.get(name)
    return val if val else None


@pytest.mark.requires_api_key
def test_tavily_live_response_still_matches_captured_shape() -> None:
    api_key = _key("TAVILY_API_KEY")
    if not api_key:
        pytest.skip("TAVILY_API_KEY not set")
    resp = httpx.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"query": "Python release", "max_results": 1},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    cfg = CitationConfig(**_entry("tavily").tool_citation_defaults["tavily_search"])
    items = cfg.extract_items(data)
    assert items, "live Tavily response no longer has a ``results`` array"
    metadata = cfg.extract_metadata(items[0])
    assert metadata["url"].startswith("http")
    assert metadata["title"]


@pytest.mark.requires_api_key
def test_exa_live_response_still_matches_captured_shape() -> None:
    api_key = _key("EXA_API_KEY")
    if not api_key:
        pytest.skip("EXA_API_KEY not set")
    resp = httpx.post(
        "https://api.exa.ai/search",
        headers={"x-api-key": api_key},
        json={"query": "Python release", "numResults": 1, "contents": {"text": True}},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    cfg = CitationConfig(**_entry("exa").tool_citation_defaults["web_search_exa"])
    items = cfg.extract_items(data)
    assert items, "live Exa response no longer has a ``results`` array"
    metadata = cfg.extract_metadata(items[0])
    assert metadata["url"].startswith("http")
    assert metadata["title"]


@pytest.mark.requires_api_key
def test_bocha_live_response_still_matches_captured_shape() -> None:
    api_key = _key("BOCHA_API_KEY")
    if not api_key:
        pytest.skip("BOCHA_API_KEY not set")
    resp = httpx.post(
        "https://api.bochaai.com/v1/web-search",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"query": "Python 版本", "count": 1},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    cfg = CitationConfig(
        content_type="json",
        source_type="web",
        content_field="data.webPages.value",
        mapping={"url": "url", "title": "name", "snippet": "snippet"},
    )
    items = cfg.extract_items(data)
    assert items, "live Bocha response no longer has data.webPages.value"
    metadata = cfg.extract_metadata(items[0])
    assert metadata["url"].startswith("http")


@pytest.mark.requires_api_key
def test_jina_live_response_still_matches_captured_shape() -> None:
    api_key = _key("JINA_API_KEY")
    if not api_key:
        pytest.skip("JINA_API_KEY not set")
    resp = httpx.get(
        "https://s.jina.ai/",
        params={"q": "Python release"},
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "X-Respond-With": "no-content",
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    cfg = CitationConfig(**_entry("jina").tool_citation_defaults["search_web"])
    items = cfg.extract_items(data)
    assert items, "live Jina response no longer has a ``data`` array"
    metadata = cfg.extract_metadata(items[0])
    assert metadata["url"].startswith("http")
    assert metadata["title"]
