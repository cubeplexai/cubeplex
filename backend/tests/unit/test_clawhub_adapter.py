"""Unit tests for ClawhubAdapter — search, fetch, and version resolution."""

from __future__ import annotations

import contextlib
import io
import zipfile

import httpx
import pytest

from cubeplex.skills.sources.base import TrustTier
from cubeplex.skills.sources.clawhub import ClawhubAdapter, _unpack_zip


def _make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _adapter(transport: httpx.MockTransport) -> ClawhubAdapter:
    adapter = ClawhubAdapter(
        source_id="test-clawhub",
        trust_tier=TrustTier.community,
        source_name="Clawhub",
    )

    @contextlib.asynccontextmanager
    async def _mock_client():
        async with httpx.AsyncClient(
            base_url="https://clawhub.ai",
            transport=transport,
            follow_redirects=True,
        ) as client:
            yield client

    adapter._client = _mock_client  # type: ignore[method-assign]
    return adapter


@pytest.mark.asyncio
async def test_search_returns_candidates():
    payload = {
        "results": [
            {
                "slug": "design",
                "displayName": "Design",
                "summary": "Auto-learns your visual preferences.",
                "version": "1.0.0",
                "ownerHandle": "ivangdavila",
                "owner": {"handle": "ivangdavila", "displayName": "Iván"},
            },
            {
                "slug": "frontend-design",
                "displayName": "Frontend Design",
                "summary": "Create polished frontend interfaces.",
                "version": None,
                "ownerHandle": "someone",
                "owner": {"handle": "someone", "displayName": "Someone"},
            },
        ]
    }

    # frontend-design has version=null → needs resolution via GET /api/v1/skills/{slug}
    skill_detail = {
        "skill": {
            "slug": "frontend-design",
            "displayName": "Frontend Design",
            "tags": {"latest": "0.3.1"},
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/search":
            return httpx.Response(200, json=payload)
        if request.url.path == "/api/v1/skills/frontend-design":
            return httpx.Response(200, json=skill_detail)
        return httpx.Response(404)

    adapter = _adapter(httpx.MockTransport(handler))
    results = await adapter.search("design", limit=5)

    assert len(results) == 2
    assert results[0].name == "Design"
    assert results[0].canonical_name == "design"
    assert results[0].source_ref == "design@1.0.0"
    assert results[0].version == "1.0.0"
    # version was null → resolved to "0.3.1"
    assert results[1].source_ref == "frontend-design@0.3.1"
    assert results[1].version == "0.3.1"
    assert results[0].repo == "https://clawhub.ai/ivangdavila/design"


@pytest.mark.asyncio
async def test_search_skips_skill_when_version_unresolvable():
    """Skills with version=null that fail resolution are dropped from results."""
    payload = {
        "results": [
            {
                "slug": "good",
                "displayName": "Good",
                "summary": "",
                "version": "1.0.0",
                "ownerHandle": "u",
            },
            {
                "slug": "no-ver",
                "displayName": "No Ver",
                "summary": "",
                "version": None,
                "ownerHandle": "u",
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/search":
            return httpx.Response(200, json=payload)
        if "/skills/no-ver" in request.url.path:
            return httpx.Response(500)  # resolution fails
        return httpx.Response(404)

    adapter = _adapter(httpx.MockTransport(handler))
    results = await adapter.search("x", limit=5)
    assert len(results) == 1
    assert results[0].canonical_name == "good"


@pytest.mark.asyncio
async def test_search_returns_empty_on_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    adapter = _adapter(httpx.MockTransport(handler))
    results = await adapter.search("test", limit=5)
    assert results == []


@pytest.mark.asyncio
async def test_fetch_with_explicit_version():
    zip_bytes = _make_zip(
        {"SKILL.md": "---\nname: design\n---\n# Design", "criteria.md": "criteria"}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/download"
        assert request.url.params["slug"] == "design"
        assert request.url.params["version"] == "1.0.0"
        return httpx.Response(200, content=zip_bytes, headers={"content-type": "application/zip"})

    adapter = _adapter(httpx.MockTransport(handler))
    files = await adapter.fetch("design@1.0.0")

    assert "SKILL.md" in files
    assert b"Design" in files["SKILL.md"]
    assert "criteria.md" in files


@pytest.mark.asyncio
async def test_fetch_resolves_latest_version():
    skill_detail = {
        "skill": {
            "slug": "design",
            "displayName": "Design",
            "tags": {"latest": "2.0.0"},
            "stats": {"downloads": 100, "stars": 5},
        }
    }
    zip_bytes = _make_zip({"SKILL.md": "# Design v2"})
    call_log: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        call_log.append(request.url.path)
        if request.url.path == "/api/v1/skills/design":
            return httpx.Response(200, json=skill_detail)
        if request.url.path == "/api/v1/download":
            assert request.url.params["version"] == "2.0.0"
            return httpx.Response(200, content=zip_bytes)
        return httpx.Response(404)

    adapter = _adapter(httpx.MockTransport(handler))
    files = await adapter.fetch("design@latest")

    assert "SKILL.md" in files
    assert "/api/v1/skills/design" in call_log
    assert "/api/v1/download" in call_log


def test_unpack_zip_filters_unsafe_paths():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", "# ok")
        zf.writestr("../evil.py", "bad")
        zf.writestr("/absolute.md", "bad")
    files = _unpack_zip(buf.getvalue())
    assert "SKILL.md" in files
    assert "../evil.py" not in files
    assert "/absolute.md" not in files


def test_unpack_zip_rejects_oversized_entry():
    from cubeplex.skills.service import MAX_FILE_BYTES

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Entry larger than MAX_FILE_BYTES
        zf.writestr("big.md", "x" * (MAX_FILE_BYTES + 1))
    with pytest.raises(ValueError, match="declares"):
        _unpack_zip(buf.getvalue())


def test_unpack_zip_rejects_oversized_bundle():
    from cubeplex.skills.service import MAX_FILE_BYTES, MAX_TOTAL_BYTES

    # Two files each just under the per-file cap but together over the bundle cap
    file_size = MAX_FILE_BYTES - 1
    n_files = (MAX_TOTAL_BYTES // file_size) + 2
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i in range(n_files):
            zf.writestr(f"file{i}.md", "x" * file_size)
    with pytest.raises(ValueError, match="total cap"):
        _unpack_zip(buf.getvalue())
