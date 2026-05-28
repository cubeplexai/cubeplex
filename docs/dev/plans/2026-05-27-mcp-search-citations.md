# Search MCP Sources + Citation Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bundle bearer-auth search MCP connectors (Tavily, and Exa if bearer-compatible) into the catalog seed with correct `tool_citation_defaults`, and calibrate the shared `CITATION_PROMPT` so search-result facts carry visible `【N-M】` markers in the final answer the user reads.

**Architecture:** This is a configuration/seed feature on top of the already-shipped citation machinery (`CitationConfig` + `CitationMiddleware` + per-install `tool_citations` snapshot). Two surfaces change: (1) `backend/cubebox/mcp/template_seed.py::CATALOG` gains search entries shaped exactly like the existing `webtools` entry; (2) `backend/cubebox/prompts/citations.py::CITATION_PROMPT` gains a reinforced visible-answer rule plus a worked search example. No new schema, no new middleware, no runtime/auth code. The runtime static-auth branch (`cubepi_runtime.py:244-250`) sends creds only as `Authorization: Bearer <key>`, so v1 ships only servers that authenticate that way.

**Tech Stack:** Python 3.13, FastAPI, SQLModel/SQLAlchemy async, Pydantic, pytest (`uv run pytest`), pytest markers (`real_llm`), Alembic (no new migration needed — the `tool_citations` columns already exist).

---

## File Structure

- `backend/cubebox/mcp/template_seed.py` — add `tavily` (and conditionally `exa`) `MCPConnectorTemplateSeedEntry` to `CATALOG`, each with `tool_citation_defaults`. Optionally add `_SEARCH_TOKEN_FIELD` reuse of `_TOKEN_FIELD`.
- `backend/cubebox/prompts/citations.py` — calibrate the module-level `CITATION_PROMPT` constant (reinforced rule + worked search example). Stays a constant; no dynamic content.
- `backend/cubebox/middleware/citations/config.py` — touched **only if** Task 1 decides a shipping connector needs a dotted-path `content_field` (Bocha-style); v1 default is no change.
- `backend/tests/unit/test_catalog_seed.py` — extend with search-connector assertions (entries exist, `tool_citation_defaults` round-trip through `CitationConfig`, bearer-only auth shape).
- `backend/tests/unit/test_citation.py` — extend with the calibrated-prompt string invariants and (if needed) a dotted-path `extract_items` case.
- `backend/tests/e2e/test_search_citations.py` (Create) — install a seeded search connector into a workspace, assert the snapshot, and run a real-LLM agent turn (against `webtools` when no third-party key is present) asserting visible `【N-M】` markers in the final assistant text.
- `backend/tests/e2e/memory/test_prompt_cache.py` — no edit expected (it does not string-match `CITATION_PROMPT`); a verification step confirms it stays green.

---

## Decisions locked from the spec (do not re-litigate)

- **OQ-1 (RESOLVED in spec):** v1 is bearer-only. Seed entries must NOT rely on a `static_auth_header_template` the runtime won't honor for non-bearer schemes; the static branch always emits `Authorization: Bearer <key>`. Set `static_auth_header_template="Bearer {token}"` (the `_BEARER_TEMPLATE` constant) to match `webtools`, knowing it is informational for the static path.
- **OQ-3 (deferred):** one shared, server-agnostic prompt. Do not enumerate verticals or server names in the prompt. Revisit only if E2E shows a gap.
- **OQ-4 (deferred):** Tavily news and web share one tool (`tavily_search`); both map to `source_type="web"`. No discriminator split in v1.
- **OQ-7 (deferred):** keep `default_credential_policy="org"` to match `webtools`. A `workspace`-policy switch for metered keys is future work, noted, not built.

These open questions are turned into the concrete decision task below (Task 1) or carried as the explicit deferrals above. OQ-2/OQ-5/OQ-6 are resolved by Task 1's live verification.

---

## Task 1: Decide which search connectors ship (live auth + tool-name verification)

This is a **decision task** that produces a short note; it gates Task 2's exact seed content. The spec mandates each candidate be verified against its live endpoint with a bearer key before seeding (OQ-2, OQ-5, OQ-6).

**Files:**
- Create: `docs/dev/notes/2026-05-27-search-connector-verification.md`

- [ ] **Step 1: Verify Tavily bearer auth + tool names**

Tavily MCP is hosted Streamable HTTP at `https://mcp.tavily.com/mcp/`. Confirm it accepts the API key as `Authorization: Bearer <key>` (not only `?tavilyApiKey=`) and list its bare tool names. Run this as a one-off inline command (a heredoc piped to `uv run python -`; nothing is written to disk):

```bash
cd backend && TAVILY_API_KEY=<key> uv run python - <<'PY'
import asyncio, os
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

async def main() -> None:
    key = os.environ["TAVILY_API_KEY"]
    headers = {"Authorization": f"Bearer {key}"}
    async with streamablehttp_client("https://mcp.tavily.com/mcp/", headers=headers) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            for t in tools.tools:
                print(t.name)

asyncio.run(main())
PY
```

Expected: a 200 handshake (bearer accepted) and tool names including `tavily_search` and `tavily_extract`. Record the exact names.

- [ ] **Step 2: Verify Exa bearer auth + tool names (conditional ship)**

Repeat Step 1 against `https://mcp.exa.ai/mcp` with `Authorization: Bearer <key>`. Record:
- whether bearer is accepted (if it requires `x-api-key` or a `server_url` query token, Exa is **deferred** — note it and skip Exa in Task 2);
- exact tool names (e.g. `web_search_exa`, `research_paper_search_exa`, `code_search_exa`) and the result-array field (expected `results`) plus the snippet field (expected `text`).

- [ ] **Step 3: Confirm result JSON shape for each shipping connector**

For each connector that passed bearer auth, call one search tool and inspect the JSON so the `content_field` is a **single top-level key** reachable by `extract_items` (`data.get(content_field, [])` — not a dotted path). Confirm:
- Tavily: array at `results`; per item `url`, `title`, `content`. Extract: array at `results`; per item `url`, `raw_content`.
- Exa (if shipping): array at `results`; per item `url`, `title`, `text`.

If any shipping connector's array is nested under a dotted path, that connector is **deferred** for v1 (do not extend `extract_items` for a deferred connector). Bocha is already deferred by the spec.

- [ ] **Step 4: Write the decision note**

Write `docs/dev/notes/2026-05-27-search-connector-verification.md` recording, per candidate: bearer accepted? (yes/no), exact tool names, result-array key, field mapping, and SHIP/DEFER verdict. Example skeleton:

```markdown
# Search connector verification (issue #148)

Date: 2026-05-27

## Tavily — SHIP
- Bearer accepted: yes (`Authorization: Bearer <key>` → 200 handshake)
- Tools: `tavily_search`, `tavily_extract`
- `tavily_search`: results at `results`; item fields `url`, `title`, `content`
- `tavily_extract`: results at `results`; item fields `url`, `raw_content`

## Exa — SHIP | DEFER (record actual)
- Bearer accepted: <yes/no>
- Tools: <exact names or n/a>
- Mapping: <or "deferred: requires x-api-key / query token">

## Bocha — DEFER (spec decision; auth unconfirmed)
```

No script to clean up — the verification command above runs inline and writes nothing to disk.

- [ ] **Step 5: Commit**

```bash
git add docs/dev/notes/2026-05-27-search-connector-verification.md
git commit -m "docs(mcp): verify search connector bearer auth + tool names (#148)"
```

---

## Task 2: Seed the search connector(s) into CATALOG

Shape each entry exactly like `webtools` (`template_seed.py:370-405`): `transport="streamable_http"`, `supported_auth_methods=["static"]`, `static_form_schema=_TOKEN_FIELD`, `static_auth_header_template=_BEARER_TEMPLATE`, `default_credential_policy="org"`, and `tool_citation_defaults` keyed by the **bare tool names confirmed in Task 1**. The code below uses the spec's expected names — replace with Task 1's verified names if they differ.

**Files:**
- Modify: `backend/cubebox/mcp/template_seed.py` (append entries to `CATALOG`, before the closing `]` at line 406)
- Test: `backend/tests/unit/test_catalog_seed.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_catalog_seed.py`:

```python
from cubebox.middleware.citations.config import CitationConfig

_SEARCH_SLUGS = {"tavily"}  # add "exa" iff Task 1 verified bearer auth


def test_search_connectors_present_and_bearer_only() -> None:
    by_slug = {e.slug: e for e in CATALOG}
    for slug in _SEARCH_SLUGS:
        entry = by_slug[slug]
        assert entry.transport == "streamable_http"
        assert entry.supported_auth_methods == ["static"]
        assert entry.default_credential_policy == "org"
        # Bearer-only: token field present, no OAuth env wiring.
        assert entry.static_form_schema is not None
        assert entry.static_auth_header_template == "Bearer {token}"
        assert entry.oauth_static_client_id_env is None
        assert entry.oauth_static_client_secret_env is None


def test_search_tool_citation_defaults_roundtrip_citationconfig() -> None:
    by_slug = {e.slug: e for e in CATALOG}
    for slug in _SEARCH_SLUGS:
        entry = by_slug[slug]
        assert entry.tool_citation_defaults, f"{slug} has empty tool_citation_defaults"
        for tool_name, raw in entry.tool_citation_defaults.items():
            cfg = CitationConfig(**raw)  # raises if shape is invalid
            assert cfg.source_type
            assert "snippet" in cfg.mapping, f"{slug}/{tool_name} missing snippet mapping"


def test_tavily_search_mapping_targets_results_array() -> None:
    entry = {e.slug: e for e in CATALOG}["tavily"]
    search = entry.tool_citation_defaults["tavily_search"]
    assert search["content_field"] == "results"
    assert search["mapping"]["url"] == "url"
    assert search["mapping"]["title"] == "title"
    assert search["mapping"]["snippet"] == "content"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_catalog_seed.py::test_search_connectors_present_and_bearer_only tests/unit/test_catalog_seed.py::test_search_tool_citation_defaults_roundtrip_citationconfig tests/unit/test_catalog_seed.py::test_tavily_search_mapping_targets_results_array -v`
Expected: FAIL with `KeyError: 'tavily'` (the entry doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

In `backend/cubebox/mcp/template_seed.py`, insert the following entry/entries into `CATALOG` immediately before the closing `]` on line 406 (after the `webtools` entry). Ship `exa` only if Task 1 verified bearer auth; otherwise omit the Exa block and keep its DEFER note in the verification doc.

```python
    MCPConnectorTemplateSeedEntry(
        slug="tavily",
        name="Tavily",
        provider="Tavily",
        description="Tavily search MCP server: web search, news, and page extraction.",
        server_url="https://mcp.tavily.com/mcp/",
        transport="streamable_http",
        supported_auth_methods=["static"],
        default_credential_policy="org",
        oauth_dcr_supported=None,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_schema=_TOKEN_FIELD,
        static_auth_header_template=_BEARER_TEMPLATE,
        template_metadata={"docs_url": "https://docs.tavily.com/documentation/mcp"},
        tool_citation_defaults={
            "tavily_search": {
                "content_type": "json",
                "source_type": "web",
                "content_field": "results",
                "mapping": {"url": "url", "title": "title", "snippet": "content"},
            },
            "tavily_extract": {
                "content_type": "json",
                "source_type": "web",
                "content_field": "results",
                "mapping": {"url": "url", "snippet": "raw_content"},
            },
        },
    ),
    # Ship the entry below ONLY if Task 1 verified Exa accepts
    # `Authorization: Bearer <key>`. Otherwise delete this block — Exa is
    # deferred until query-param/x-api-key auth plumbing exists (OQ-1).
    MCPConnectorTemplateSeedEntry(
        slug="exa",
        name="Exa",
        provider="Exa",
        description="Exa search MCP server: web search, academic research, and code search.",
        server_url="https://mcp.exa.ai/mcp",
        transport="streamable_http",
        supported_auth_methods=["static"],
        default_credential_policy="org",
        oauth_dcr_supported=None,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_schema=_TOKEN_FIELD,
        static_auth_header_template=_BEARER_TEMPLATE,
        template_metadata={"docs_url": "https://docs.exa.ai/reference/exa-mcp"},
        tool_citation_defaults={
            "web_search_exa": {
                "content_type": "json",
                "source_type": "web",
                "content_field": "results",
                "mapping": {"url": "url", "title": "title", "snippet": "text"},
            },
            "research_paper_search_exa": {
                "content_type": "json",
                "source_type": "academic",
                "content_field": "results",
                "mapping": {"url": "url", "title": "title", "snippet": "text"},
            },
            "code_search_exa": {
                "content_type": "json",
                "source_type": "code",
                "content_field": "results",
                "mapping": {"url": "url", "title": "title", "snippet": "text"},
            },
        },
    ),
```

If Exa is deferred, also leave `_SEARCH_SLUGS = {"tavily"}` in the test. If Exa ships, change it to `{"tavily", "exa"}` and add an Exa mapping assertion mirroring `test_tavily_search_mapping_targets_results_array` (assert `web_search_exa` → `content_field == "results"`, `mapping["snippet"] == "text"`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_catalog_seed.py -v`
Expected: PASS — including the pre-existing `test_seed_with_full_env_writes_templates_and_credentials` (which asserts `result.upserted == len(CATALOG)` and the slug set, so it stays correct automatically).

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/mcp/template_seed.py backend/tests/unit/test_catalog_seed.py
git commit -m "feat(mcp): seed Tavily search connector with citation defaults (#148)"
```

---

## Task 3: Calibrate CITATION_PROMPT for search results

The prompt is a module-level constant appended to the system prompt by `CitationMiddleware.transform_system_prompt` **only when ≥1 citation config is registered** (prompt-cache-safe). Add (a) a line reinforcing that search-result facts delivered via `tool_result` are the most important to cite, surfacing the `[url: … | title: …]` shape the model sees, and (b) a worked search example. Keep it server-agnostic — no server names, no `web/academic/code/news` enumeration.

**Files:**
- Modify: `backend/cubebox/prompts/citations.py`
- Test: `backend/tests/unit/test_citation.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_citation.py`:

```python
from cubebox.prompts.citations import CITATION_PROMPT


def test_citation_prompt_has_search_result_reinforcement() -> None:
    # Search facts arrive via tool_result; the prompt must explicitly call
    # that out and show the url/title metadata shape the model is attributing.
    assert "tool result" in CITATION_PROMPT.lower()
    assert "[url:" in CITATION_PROMPT
    assert "title:" in CITATION_PROMPT


def test_citation_prompt_has_worked_search_example() -> None:
    # A *second* worked example, distinct from the existing weather example,
    # showing an answer assembled from several search hits. The today's prompt
    # already mentions web_search and has the weather marker, so this must
    # assert the new example text directly or it passes vacuously (the
    # "expect fail" step would not fail). Assert the new example's anchor
    # phrase and that it carries a marker inline.
    assert "assembled from several search hits" in CITATION_PROMPT.lower()
    import re

    # Two distinct worked-example blocks must exist (weather + search), each
    # introduced by "Example of a correct final answer".
    assert len(re.findall(r"Example of a correct final answer", CITATION_PROMPT)) >= 2
    # The new search example block itself contains the marker syntax.
    search_block = CITATION_PROMPT.lower().split("assembled from several search hits", 1)[1]
    assert re.search(r"【\d+-\d+】", search_block)


def test_citation_prompt_stays_server_agnostic() -> None:
    # No server names, no vertical enumeration in the stable prefix.
    lowered = CITATION_PROMPT.lower()
    for forbidden in ("tavily", "exa", "bocha", "academic", "code search", "news mode"):
        assert forbidden not in lowered, f"prompt leaked server/vertical term: {forbidden}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_citation.py -k citation_prompt -v`
Expected: FAIL — `test_citation_prompt_has_search_result_reinforcement` fails on the missing `[url:` substring (current prompt has no worked search example or tool-result metadata shape).

- [ ] **Step 3: Write minimal implementation**

Replace the `CITATION_PROMPT` string in `backend/cubebox/prompts/citations.py`. Insert a new rule #7 reinforcing search/tool-result facts, and append a second worked example. Keep the existing rules 1-6 and the weather example verbatim; the diff is additive:

```python
"""System prompt for citation behavior."""

CITATION_PROMPT = """## Citation Rules

Your tool results (web_search, web_fetch, subagents, …) contain citation markers like 【N-M】 (N = source number, M = chunk index). These markers are how the user's interface links each statement you make back to its source, so they only work if they appear in the answer the user actually reads.

**The core rule**: every fact in your final answer that came from a tool result MUST carry its 【N-M】 marker inline, immediately after that fact — even after you rephrase it, summarize it, translate it, or drop it into a table. Summarizing tool data is exactly when you cite; it is not a reason to stop citing.

1. **Markers go in the visible answer, not only in your private thinking.** If your reasoning noted "source 【11-4】 shows a high of 24°", the answer must read "high of 24°【11-4】". A fact in the answer without its marker is a rule violation.

2. **Syntax**: Use 【N-M】 exactly as given. N is the source number, M is the chunk index (e.g. 【3-0】, 【3-1】). Never invent, renumber, or convert to other formats like [1], (source 1), markdown links, or footnotes. Renumbering breaks frontend reference linking.

3. **Placement**: Immediately after the supported fact. Multiple sources go consecutively, e.g. "Revenue grew 15%【2-0】 while costs fell【2-1】【3-0】". Inside a table, put the marker in the cell with the value.

4. **When NOT to cite**: Only your own analysis, general knowledge, or conversational filler. A fact you copied from a tool result is never "your own analysis", even after you reword it.

5. **No references section**: Citations are inline only. Never append a "Sources" / "信息来源" / "References" list at the end.

6. **Subagent citations**: When a subagent's output contains 【N-M】 markers, copy them through verbatim into your response. The system has already registered the citation sources — you do not need the original data to use them. Treat subagent citation markers the same as those from your own tool results.

7. **Search-result facts are the most important to cite.** Facts that arrived through a tool result — search hits especially — already carry their source in the text you were given: each chunk is prefixed with its marker and metadata, e.g. `【7-0】 [url: https://example.com | title: Example] …`. When you use such a fact in your answer, reproduce its 【N-M】 marker inline. Do not strip the marker just because the fact came in through a tool result rather than your own reasoning — that is exactly the case the user is relying on you to attribute.

Example of a correct final answer (weather):
Tomorrow Beijing is cloudy【11-4】, with a high of 24° and a low of 16°【11-0】【11-1】, and no precipitation all day【11-2】.

Example of a correct final answer (assembled from several search hits):
The new model was released in March 2026【4-0】 and scored 92% on the benchmark【4-1】, ahead of the previous leader at 88%【5-0】. Pricing starts at $20/month【5-2】."""  # noqa: E501
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_citation.py -k citation_prompt -v`
Expected: PASS (all three new tests green).

- [ ] **Step 5: Run the full citation unit suite to confirm no regression**

Run: `cd backend && uv run pytest tests/unit/test_citation.py -v`
Expected: PASS — existing middleware tests unaffected (the prompt change is additive text).

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/prompts/citations.py backend/tests/unit/test_citation.py
git commit -m "feat(prompts): calibrate citation prompt for search results (#148)"
```

---

## Task 4: Prompt-cache safety + stability gate

The prompt stays a module-level constant — no per-turn/per-user/per-workspace content. Confirm the prompt-cache discipline gate is unaffected, and add a guard test that catches any future attempt to inject dynamic content (server names, f-strings) into the prefix.

**Files:**
- Test: `backend/tests/unit/test_citation.py`
- Verify (no edit expected): `backend/tests/e2e/memory/test_prompt_cache.py`

- [ ] **Step 1: Write the failing test (byte-stability guard)**

Append to `backend/tests/unit/test_citation.py`:

```python
def test_citation_prompt_is_a_stable_constant() -> None:
    # Two imports must yield the identical object/string — the prefix must
    # not be templated per call (prompt-cache discipline: stable prefix).
    import importlib

    import cubebox.prompts.citations as mod1

    importlib.reload(mod1)
    first = mod1.CITATION_PROMPT
    importlib.reload(mod1)
    second = mod1.CITATION_PROMPT
    assert first == second
    # No template placeholders that would imply per-call interpolation of
    # workspace/server identity into the stable prefix.
    assert "{workspace" not in CITATION_PROMPT
    assert "{server" not in CITATION_PROMPT
    assert "{org" not in CITATION_PROMPT
```

- [ ] **Step 2: Run test to verify it fails, then passes**

Run: `cd backend && uv run pytest tests/unit/test_citation.py::test_citation_prompt_is_a_stable_constant -v`
Expected: PASS immediately if Task 3's prompt is a clean constant (it is). If a `{workspace…}`/`{server…}` placeholder leaked in, this FAILS — fix the prompt to remove dynamic content. (Write the test before re-reading the prompt; this is the guard, not a TDD-red requirement, since correct Task 3 output already satisfies it.)

- [ ] **Step 3: Verify the real-LLM prompt-cache gate is untouched**

Confirm `backend/tests/e2e/memory/test_prompt_cache.py` does not string-match `CITATION_PROMPT` (it asserts cache-hit *ratios*, not prompt text):

Run: `cd backend && grep -n "CITATION\|citation" tests/e2e/memory/test_prompt_cache.py`
Expected: no output (no edit needed). If — and only if — a future assertion string-matched the old prompt text, update it to the calibrated text. Today there is none.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/unit/test_citation.py
git commit -m "test(prompts): guard citation prompt stays a stable constant (#148)"
```

---

## Task 5: E2E — install a search connector and assert visible citations

E2E-first per CLAUDE.md. The install path snapshots the catalog `tool_citations` onto the new `mcp_servers` row, then a real-LLM agent turn must produce `【N-M】` markers in the **final assistant message text** (the deepseek failure mode is the side channel being fine while prose lacks markers — so assert on text). When no third-party search key is available in CI, drive the run against the `webtools` server (config-wired `web_search`, see `config.development.local.yaml`) so the calibrated prompt is exercised without a third-party key; keep the real-key Tavily/Exa run as a local/manual check gated by an env var.

**Setting an env var is not enough to make a search tool available.** The gate (`CUBEBOX_E2E_WEBTOOLS_READY` / `CUBEBOX_E2E_SEARCH_KEY`) only decides whether to run; the run still needs a tool the agent can actually call. Two paths, pick one and wire it in the test setup before sending the message:
- **webtools path** (CI-friendly when the local webtools server on `:8020` is reachable): the `web_search` tool comes from config, not a catalog install, so no install/grant is needed — but the test MUST confirm the workspace agent actually sees a search tool (e.g. assert `web_search` is in the active-tools listing) before asserting on markers, or a no-tool run passes vacuously.
- **catalog path** (real Tavily/Exa key): seed → `POST /api/v1/ws/{ws}/mcp/installs` with `auth_method="static"` → create the static credential grant for the install → confirm the runtime resolves a usable, citation-enabled connector for `member_client`. Only then send the message.

**Files:**
- Create: `backend/tests/e2e/test_search_citations.py`

- [ ] **Step 1: Write the failing test — install snapshots citation defaults**

Create `backend/tests/e2e/test_search_citations.py`:

```python
"""E2E: search connector install snapshots citation defaults, and the
calibrated prompt yields visible 【N-M】 markers in the final answer.

The marker-visibility turn is real-LLM (marker `real_llm`). When no
third-party search key is present it drives the already-seeded `webtools`
connector so the prompt calibration is still exercised; a real Tavily/Exa
run is gated by CUBEBOX_E2E_SEARCH_KEY for local/manual verification.
"""

from __future__ import annotations

import os
import re

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubebox.credentials.encryption import FernetBackend
from cubebox.mcp.template_seed import seed_templates
from cubebox.middleware.citations.config import CitationConfig
from cubebox.models.mcp import MCPConnectorInstall, MCPConnectorTemplate

_MARKER_RE = re.compile(r"【\d+-\d+】")


async def _seed_into_db(session: AsyncSession) -> None:
    backend = FernetBackend([Fernet.generate_key()])
    await seed_templates(session, backend, get_env=lambda _k: None)
    await session.commit()


async def test_install_search_connector_snapshots_tool_citations(
    admin_client: tuple,  # (httpx.AsyncClient, ws_id)
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client, ws_id = admin_client
    async with session_factory() as session:
        await _seed_into_db(session)
        tmpl = (
            await session.execute(
                select(MCPConnectorTemplate).where(MCPConnectorTemplate.slug == "tavily")
            )
        ).scalar_one()
        template_id = tmpl.id

    res = await client.post(
        f"/api/v1/ws/{ws_id}/mcp/installs",
        json={
            "template_id": template_id,
            "auth_method": "static",
            "default_credential_policy": "org",
        },
    )
    assert res.status_code == 201, res.text
    # The install response is MCPConnectorInstallOut → key is "install_id",
    # not "id" (backend/cubebox/api/schemas/mcp.py:64).
    install_id = res.json()["install_id"]

    async with session_factory() as session:
        install = (
            await session.execute(
                select(MCPConnectorInstall).where(MCPConnectorInstall.id == install_id)
            )
        ).scalar_one()
        assert install.tool_citations, "tool_citations not snapshotted from catalog default"
        assert "tavily_search" in install.tool_citations
        # Snapshot is a valid CitationConfig.
        CitationConfig(**install.tool_citations["tavily_search"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/e2e/test_search_citations.py::test_install_search_connector_snapshots_tool_citations -v`
Expected: FAIL — initially because (depending on ordering with Task 2) the `tavily` template doesn't exist, or the install-snapshot assertion is unmet. After Task 2 is in place, FAIL only if the install path doesn't snapshot `tool_citations` (it does, per the 2026-05-14 spec — this then PASSES, confirming the wiring). If it PASSES at this step that is the expected outcome once Task 2 landed; record the green run as evidence.

- [ ] **Step 3: Add the real-LLM visible-marker turn**

Append to `backend/tests/e2e/test_search_citations.py`:

The `send_message_and_collect_text` helper returns only the concatenated `text_delta` payloads, so it cannot prove a citation event fired. Use the lower-level `_stream_events` helper (same module) to collect every parsed SSE event, then assert **both**: (a) at least one event with `type == "citation"` (the `CitationEvent`, `backend/cubebox/agents/schemas.py:130`) confirms the side channel fired, and (b) the concatenated `text_delta` prose carries a visible `【N-M】` marker — the deepseek failure mode is exactly (a) firing while (b) is empty.

```python
from tests.e2e.memory._helpers import _stream_events


@pytest.mark.real_llm
async def test_search_answer_carries_visible_markers(
    member_client: tuple,  # (httpx.AsyncClient, ws_id)
) -> None:
    """The calibrated prompt must make the model reproduce 【N-M】 markers in
    the final answer text for facts delivered via tool_result. Drives the
    config-wired `webtools` search tool when no third-party key is set."""
    if not os.environ.get("CUBEBOX_E2E_SEARCH_KEY") and not os.environ.get(
        "CUBEBOX_E2E_WEBTOOLS_READY"
    ):
        pytest.skip(
            "No search backend available: set CUBEBOX_E2E_WEBTOOLS_READY (webtools "
            "reachable) or CUBEBOX_E2E_SEARCH_KEY (real Tavily/Exa key) to run."
        )

    client, ws_id = member_client
    # Precondition (see Setup above): confirm a search tool is actually
    # available to this workspace agent before asserting on its output, or a
    # no-tool run would pass vacuously. For the webtools path, assert
    # `web_search` is in the active-tools listing; for the catalog path,
    # confirm the install + grant resolved a usable connector.
    conv = await client.post(
        f"/api/v1/ws/{ws_id}/conversations", params={"title": "search-citations"}
    )
    conv.raise_for_status()
    conv_id = conv.json()["id"]

    events = await _stream_events(
        client,
        ws_id,
        conv_id,
        "Search the web for the latest stable Python release and tell me its "
        "version number and release date. Cite your sources inline.",
    )

    # (a) The citation side channel must fire at least once.
    citation_events = [e for e in events if e.get("type") == "citation"]
    assert citation_events, "no citation SSE event emitted; tool result not cited"

    # (b) The visible prose must carry a marker — the deepseek failure mode is
    # (a) present while (b) empty.
    text = "".join(
        (e.get("data") or {}).get("content", "")
        for e in events
        if e.get("type") == "text_delta"
    )
    assert _MARKER_RE.search(text), (
        f"final answer has no 【N-M】 marker; calibration failed.\n---\n{text}\n---"
    )
    # No trailing references list (rule #5).
    lowered = text.lower()
    assert "sources:" not in lowered
    assert "references" not in lowered
    assert "信息来源" not in text
```

- [ ] **Step 4: Run the install test (always-on) and the real-LLM test (gated)**

Run: `cd backend && uv run pytest tests/e2e/test_search_citations.py::test_install_search_connector_snapshots_tool_citations -v`
Expected: PASS.

Run (gated, local/manual): `cd backend && CUBEBOX_E2E_WEBTOOLS_READY=1 uv run pytest tests/e2e/test_search_citations.py::test_search_answer_carries_visible_markers -v -m real_llm`
Expected: PASS — final text contains at least one `【N-M】` marker and no references list. If it FAILS on a missing marker, the prompt calibration regressed; iterate on Task 3 before claiming done. Without the env var it SKIPS (CI default), consistent with the existing `real_llm` gating pattern.

- [ ] **Step 5: Run deepseek regression (gated, local/manual)**

The deepseek-v4-flash model is the one that drops markers for `tool_result` facts. If a deepseek-backed provider is configured for the test workspace, run the same `real_llm` test against it and confirm markers appear. This is the regression that motivated the prompt change.

Run: `cd backend && CUBEBOX_E2E_WEBTOOLS_READY=1 uv run pytest tests/e2e/test_search_citations.py::test_search_answer_carries_visible_markers -v -m real_llm`
Expected: PASS against the deepseek-backed workspace. Record the run in the verification note. (No code change here — this step is verification of Task 3.)

- [ ] **Step 6: Commit**

```bash
git add backend/tests/e2e/test_search_citations.py
git commit -m "test(e2e): search install snapshots citations + visible markers (#148)"
```

---

## Task 6: Pre-PR sweep + verification

**Files:** none (verification only).

- [ ] **Step 1: Read worktree env (ports/DB are non-default)**

Run: `cd /home/chris/cubebox/.worktrees/feat/mcp-search-citations && cat .worktree.env`
Expected: confirms slot 16 — API `:8016`, DB `cubebox_feat_mcp_search_citations`. Tests auto-route to the per-slot test DB via `tests/conftest.py`; plain `uv run pytest` is safe.

- [ ] **Step 2: Run the changed-module unit suites**

Run: `cd backend && uv run pytest tests/unit/test_catalog_seed.py tests/unit/test_citation.py -v`
Expected: PASS.

- [ ] **Step 3: Run the seed-idempotency and seed-parity tests (they iterate CATALOG)**

Run: `cd backend && uv run pytest tests/unit/test_seed_idempotent.py tests/unit/test_seed_backfill_parity.py tests/unit/test_cli_seed.py -v`
Expected: PASS — the new entries must not break idempotency or the upsert count invariants.

- [ ] **Step 4: Run the citation E2E (install path) + prompt-cache gate**

Run: `cd backend && uv run pytest tests/e2e/test_search_citations.py::test_install_search_connector_snapshots_tool_citations tests/e2e/memory/test_prompt_cache.py -v`
Expected: install test PASS; prompt-cache test PASS or SKIP (skips when `CUBEBOX_E2E_LLM_CACHE_CAPABLE` is unset — the documented default), never FAIL.

- [ ] **Step 5: Type + lint check**

Run: `cd backend && uv run mypy cubebox/mcp/template_seed.py cubebox/prompts/citations.py && uv run ruff check cubebox/mcp/template_seed.py cubebox/prompts/citations.py tests/unit/test_catalog_seed.py tests/unit/test_citation.py tests/e2e/test_search_citations.py`
Expected: no errors (mypy strict clean, ruff clean, line length ≤ 100).

- [ ] **Step 6: Final commit if the sweep produced fixes**

```bash
git add -A backend/
git commit -m "chore(mcp): pre-PR sweep fixes for search citations (#148)"
```

(Skip if the sweep was clean — do not create an empty commit.)

---

## Self-Review

**1. Spec coverage**

| Spec item | Task |
|---|---|
| Add bearer-auth search connectors to CATALOG with `tool_citation_defaults` | Task 2 (Tavily; Exa conditional) |
| Live-verify bearer auth + tool names before seeding (OQ-2/OQ-5/OQ-6) | Task 1 |
| Reuse `CitationConfig`/`CitationMiddleware` verbatim, no new schema/middleware | Tasks 2-5 (no schema/middleware files touched) |
| Calibrate `CITATION_PROMPT`: reinforce visible-answer rule for tool_result/search facts | Task 3 (rule #7) |
| Add worked search example with exact `【N-M】` syntax | Task 3 (second example) |
| Keep prompt server-agnostic (no verticals/server names) | Task 3 (`test_citation_prompt_stays_server_agnostic`) |
| Prompt stays a prompt-cache-safe constant | Task 4 |
| E2E: install snapshots catalog citation defaults | Task 5 Step 1 |
| E2E: forced search run → SSE citation events + `【N-M】` in final text, no references list | Task 5 Step 3 |
| E2E: deepseek regression (the model that drops markers) | Task 5 Step 5 |
| E2E: `webtools` fallback when no third-party key in CI | Task 5 Step 3 (env-gated) |
| Unit: seed entries valid `CitationConfig` round-trip | Task 2 Step 1 |
| Unit: Bocha dotted-path `extract_items` case | Deferred — Bocha is not shipping in v1 (spec defers it); the dotted-path `extract_items` change is only made *if a shipping connector needs it*, and Task 1 Step 3 explicitly defers any connector whose array is nested. So no `extract_items` change and no dotted-path test in v1. Carried as a noted future item, not a task. |
| Prompt-cache E2E gate stays green | Task 4 Step 3 + Task 6 Step 4 |
| OQ-1 bearer-only (resolved) | Locked in "Decisions" + Task 2 shape |
| OQ-3 single shared prompt (deferred) | "Decisions" + Task 3 server-agnostic guard |
| OQ-4 news vs web source_type (deferred) | "Decisions"; both `web` |
| OQ-7 org credential policy (deferred) | "Decisions"; `org` policy |

**2. Placeholder scan:** No "TBD"/"add tests"/"handle edge cases". Every code step shows real code; every run step shows the exact command and expected result. The one conditional (Exa ships only if Task 1 verifies bearer auth) is explicit with the exact code and the exact fallback (delete the block, keep `_SEARCH_SLUGS = {"tavily"}`).

**3. Type consistency:** `MCPConnectorTemplateSeedEntry` field names match the dataclass (`template_seed.py:38-59`), including `oauth_dcr_supported=None`, `default_credential_policy` literal, and `tool_citation_defaults`. `CitationConfig(**raw)` matches the model (`content_type`, `source_type`, `content_field`, `mapping`, optional `args_mapping`). `_TOKEN_FIELD` / `_BEARER_TEMPLATE` are existing module constants. The E2E `_stream_events` helper, the `admin_client`/`member_client` fixtures, the `session_factory` fixture (the actual name — there is no `db_session_maker`), and the `real_llm` marker all match existing test infrastructure. Install route `POST /api/v1/ws/{ws}/mcp/installs` with `template_id`/`auth_method`/`default_credential_policy` body matches `create_workspace_install` (`ws_mcp.py:287`) and returns `MCPConnectorInstallOut` whose key is `install_id`, not `id`.
