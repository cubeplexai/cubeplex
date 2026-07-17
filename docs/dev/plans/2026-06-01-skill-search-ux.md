# Skill Search UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `preview_skill` and `install_skill` agent tools, and auto-render `find_skills` results as interactive skill cards with a right-panel preview.

**Architecture:** Backend adds two new `AgentTool`s registered in `run_manager.py` (sharing the existing `catalog_session` / registry infrastructure of `find_skills`). Frontend auto-renders `find_skills` tool_call blocks as `SkillSearchResults` cards in `AssistantMessage.tsx`, with a new `skill-candidate` panel view type in `panelStore` and a `SkillCandidatePanel` component.

**Tech Stack:** Python / cubepi `AgentTool`, FastAPI, SQLAlchemy async; React 19 / Next.js, Zustand, SWR, ReactMarkdown, Tailwind, shadcn/ui.

---

## File map

**Backend — new files:**
- `backend/cubeplex/tools/builtin/preview_skill.py` — `create_preview_skill_tool` factory
- `backend/cubeplex/tools/builtin/install_skill.py` — `create_install_skill_tool` factory
- `backend/tests/unit/test_preview_skill.py` — unit tests for preview_skill
- `backend/tests/unit/test_install_skill.py` — unit tests for install_skill

**Backend — modified files:**
- `backend/cubeplex/streams/run_manager.py` — register both tools in the `find_skills` block (~line 1134)
- `backend/cubeplex/tools/builtin/find_skills.py` — update `hint` to mention `install_skill`

**Frontend — new files:**
- `frontend/packages/web/app/api/v1/ws/[wsId]/skills/discover/preview/route.ts` — Next.js proxy for `GET /discover/preview?candidate_id=`
- `frontend/packages/web/components/chat/tool-results/SkillSearchResults.tsx` — card list container
- `frontend/packages/web/components/chat/tool-results/SkillCandidateCard.tsx` — individual skill card
- `frontend/packages/web/components/panel/SkillCandidatePanel.tsx` — right-panel SKILL.md preview

**Frontend — modified files:**
- `frontend/packages/core/src/stores/panelStore.ts` — add `skill-candidate` view type + `openSkillCandidate` action
- `frontend/packages/web/components/layout/AppShell.tsx` — add `skill-candidate` panel branch
- `frontend/packages/web/components/chat/AssistantMessage.tsx` — add `find_skills` render branch + groupBlocks exclusion
- `frontend/packages/web/messages/en.json` — add i18n keys for new UI text

---

## Task 1: `preview_skill` backend tool

**Files:**
- Create: `backend/cubeplex/tools/builtin/preview_skill.py`
- Create: `backend/tests/unit/test_preview_skill.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_preview_skill.py
"""Unit tests for the preview_skill agent tool."""

from __future__ import annotations

import json
import pytest
from cubeplex.tools.builtin.preview_skill import create_preview_skill_tool, PreviewSkillInput


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeSkillVersion:
    def __init__(self, sv_id: str) -> None:
        self.id = sv_id


class _FakeSkill:
    def __init__(self, skill_id: str, current_version: str, source: str = "preinstalled") -> None:
        self.id = skill_id
        self.current_version = current_version
        self.source = source
        self.owner_org_id: str | None = None


class _FakeCatalog:
    def __init__(self, content: str) -> None:
        self._content = content

    async def fetch_skill_md(self, skill_version_id: str) -> str:
        return self._content


class _FakeAdapter:
    def __init__(self, files: dict[str, bytes] | Exception) -> None:
        self._files = files

    async def fetch(self, source_ref: str) -> dict[str, bytes]:
        if isinstance(self._files, Exception):
            raise self._files
        return self._files


class _FakeRegistry:
    def __init__(self, adapter: _FakeAdapter | None) -> None:
        self._adapter = adapter

    def adapter_by_id(self, source_id: str) -> _FakeAdapter | None:
        return self._adapter


class _FakeSkillRepo:
    def __init__(self, skill: _FakeSkill | None) -> None:
        self._skill = skill

    async def get(self, skill_id: str) -> _FakeSkill | None:
        return self._skill


class _FakeSkillVersionRepo:
    def __init__(self, sv: _FakeSkillVersion | None) -> None:
        self._sv = sv

    async def find(self, skill_id: str, version: str) -> _FakeSkillVersion | None:
        return self._sv


class _FakeSession:
    pass


# ---------------------------------------------------------------------------
# Tests — remote path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_remote_returns_skill_md() -> None:
    from cubeplex.skills.sources.base import encode_candidate_id

    candidate_id = encode_candidate_id(
        "remote", "owner/repo/main/skills/my-skill", source_id="src-1"
    )
    adapter = _FakeAdapter({"SKILL.md": b"# My Skill\nDoes stuff.", "extra.txt": b"ignore"})
    registry = _FakeRegistry(adapter)
    catalog = _FakeCatalog("irrelevant")

    tool = create_preview_skill_tool(
        session=_FakeSession(),
        registry=registry,
        catalog=catalog,
        org_id="org-1",
    )
    result = await tool.execute("tc-1", PreviewSkillInput(candidate_id=candidate_id))

    assert not result.is_error
    out = json.loads(result.content[0].text)
    assert out["content"] == "# My Skill\nDoes stuff."
    assert out["candidate_id"] == candidate_id


@pytest.mark.asyncio
async def test_preview_remote_no_adapter_returns_error() -> None:
    from cubeplex.skills.sources.base import encode_candidate_id

    candidate_id = encode_candidate_id("remote", "owner/repo/main/skill", source_id="src-x")
    registry = _FakeRegistry(adapter=None)  # no adapter for src-x
    catalog = _FakeCatalog("irrelevant")

    tool = create_preview_skill_tool(
        session=_FakeSession(),
        registry=registry,
        catalog=catalog,
        org_id="org-1",
    )
    result = await tool.execute("tc-2", PreviewSkillInput(candidate_id=candidate_id))

    assert result.is_error
    assert "SOURCE_NOT_FOUND" in result.content[0].text


@pytest.mark.asyncio
async def test_preview_remote_missing_skill_md_returns_error() -> None:
    from cubeplex.skills.sources.base import encode_candidate_id

    candidate_id = encode_candidate_id("remote", "owner/repo/main/skill", source_id="src-1")
    adapter = _FakeAdapter({"README.md": b"no SKILL.md here"})
    registry = _FakeRegistry(adapter)
    catalog = _FakeCatalog("irrelevant")

    tool = create_preview_skill_tool(
        session=_FakeSession(),
        registry=registry,
        catalog=catalog,
        org_id="org-1",
    )
    result = await tool.execute("tc-3", PreviewSkillInput(candidate_id=candidate_id))

    assert result.is_error
    assert "SKILL_MD_MISSING" in result.content[0].text


@pytest.mark.asyncio
async def test_preview_bad_candidate_id_returns_error() -> None:
    registry = _FakeRegistry(adapter=None)
    catalog = _FakeCatalog("irrelevant")

    tool = create_preview_skill_tool(
        session=_FakeSession(),
        registry=registry,
        catalog=catalog,
        org_id="org-1",
    )
    result = await tool.execute("tc-4", PreviewSkillInput(candidate_id="not-base64-valid!!!"))

    assert result.is_error
    assert "BAD_CANDIDATE_ID" in result.content[0].text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_preview_skill.py -v 2>&1 | head -20
```
Expected: `ModuleNotFoundError` or `ImportError` — `preview_skill` does not exist yet.

- [ ] **Step 3: Implement `preview_skill` tool**

```python
# backend/cubeplex/tools/builtin/preview_skill.py
"""preview_skill tool — fetch SKILL.md content for any candidate (installed or not).

Used by the agent to read a skill before recommending installation. Mirrors
the logic in GET /ws/{ws}/skills/discover/preview without requiring an HTTP
round-trip.
"""

from __future__ import annotations

import json

import httpx
from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.skills.frontmatter import extract_env_vars, parse_skill_md
from cubeplex.skills.service import SkillCatalogService
from cubeplex.skills.sources.base import CandidateIdError, decode_candidate_id
from cubeplex.skills.sources.registry import SkillsAdapterManager


class PreviewSkillInput(BaseModel):
    candidate_id: str = Field(
        description=(
            "The candidate_id from a find_skills result. "
            "Returns the SKILL.md content so you can describe the skill before suggesting installation."
        )
    )


def _env_vars(content: str) -> list[str]:
    try:
        fm = parse_skill_md(content)
        return extract_env_vars(fm.raw_metadata)
    except Exception:  # noqa: BLE001
        return []


def create_preview_skill_tool(
    *,
    session: AsyncSession,
    registry: SkillsAdapterManager,
    catalog: SkillCatalogService,
    org_id: str,
) -> AgentTool[PreviewSkillInput]:
    async def _execute(
        tool_call_id: str,
        args: PreviewSkillInput,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        try:
            kind, source_id, source_ref = decode_candidate_id(args.candidate_id)
        except CandidateIdError:
            return AgentToolResult(
                content=[TextContent(text="BAD_CANDIDATE_ID")], is_error=True
            )

        if kind == "local":
            from cubeplex.repositories.skill import SkillRepository, SkillVersionRepository

            skill = await SkillRepository(session).get(source_ref)
            if skill is None or not (
                skill.source == "preinstalled" or skill.owner_org_id == org_id
            ):
                return AgentToolResult(
                    content=[TextContent(text="SKILL_NOT_FOUND")], is_error=True
                )
            sv = await SkillVersionRepository(session).find(skill.id, skill.current_version)
            if sv is None:
                return AgentToolResult(
                    content=[TextContent(text="SKILL_VERSION_NOT_FOUND")], is_error=True
                )
            content = await catalog.fetch_skill_md(sv.id)
            payload = {
                "candidate_id": args.candidate_id,
                "name": skill.name,
                "content": content,
                "env_vars": _env_vars(content),
            }
            return AgentToolResult(content=[TextContent(text=json.dumps(payload))])

        # Remote path
        adapter = registry.adapter_by_id(source_id)
        if adapter is None:
            return AgentToolResult(
                content=[TextContent(text="SOURCE_NOT_FOUND")], is_error=True
            )
        try:
            files = await adapter.fetch(source_ref)
        except (httpx.HTTPError, ValueError) as exc:
            return AgentToolResult(
                content=[TextContent(text=f"REMOTE_FETCH_FAILED: {exc}")], is_error=True
            )
        if "SKILL.md" not in files:
            return AgentToolResult(
                content=[TextContent(text="SKILL_MD_MISSING")], is_error=True
            )
        try:
            content = files["SKILL.md"].decode("utf-8")
        except UnicodeDecodeError as exc:
            return AgentToolResult(
                content=[TextContent(text=f"INVALID_UTF8: {exc}")], is_error=True
            )
        slug = source_ref.rsplit("/", 1)[-1]
        payload = {
            "candidate_id": args.candidate_id,
            "name": slug,
            "content": content,
            "env_vars": _env_vars(content),
        }
        return AgentToolResult(content=[TextContent(text=json.dumps(payload))])

    return AgentTool(
        name="preview_skill",
        description=(
            "Fetch the full SKILL.md of any skill candidate — installed or not. "
            "Use this after find_skills to read what a skill does before recommending installation. "
            "Pass the candidate_id from the find_skills result."
        ),
        parameters=PreviewSkillInput,
        execute=_execute,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_preview_skill.py -v
```
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/tools/builtin/preview_skill.py backend/tests/unit/test_preview_skill.py
git commit -m "feat(skills): add preview_skill agent tool"
```

---

## Task 2: `install_skill` backend tool + update `find_skills` hint

**Files:**
- Create: `backend/cubeplex/tools/builtin/install_skill.py`
- Create: `backend/tests/unit/test_install_skill.py`
- Modify: `backend/cubeplex/tools/builtin/find_skills.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_install_skill.py
"""Unit tests for the install_skill agent tool."""

from __future__ import annotations

import json
import pytest
from cubeplex.tools.builtin.install_skill import InstallSkillInput, create_install_skill_tool
from cubeplex.skills.discovery import InstallResult, SkillInstallError


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeInstallService:
    def __init__(self, result: InstallResult | Exception) -> None:
        self._result = result

    async def install(self, candidate_id: str) -> InstallResult:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_skill_success() -> None:
    from cubeplex.skills.sources.base import encode_candidate_id

    candidate_id = encode_candidate_id("remote", "owner/repo/main/skill", source_id="src-1")
    svc = _FakeInstallService(
        InstallResult(
            canonical_name="myorg:my-skill",
            skill_id="skl-abc",
            installed_version="1.0.0",
        )
    )

    tool = create_install_skill_tool(install_service_factory=lambda: svc)
    result = await tool.execute("tc-1", InstallSkillInput(candidate_id=candidate_id))

    assert not result.is_error
    out = json.loads(result.content[0].text)
    assert out["installed"] is True
    assert out["canonical_name"] == "myorg:my-skill"
    assert out["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_install_skill_error_propagates() -> None:
    from cubeplex.skills.sources.base import encode_candidate_id

    candidate_id = encode_candidate_id("remote", "owner/repo/main/skill", source_id="src-1")
    svc = _FakeInstallService(SkillInstallError("trust tier too low"))

    tool = create_install_skill_tool(install_service_factory=lambda: svc)
    result = await tool.execute("tc-2", InstallSkillInput(candidate_id=candidate_id))

    assert result.is_error
    assert "trust tier too low" in result.content[0].text


@pytest.mark.asyncio
async def test_install_bad_candidate_id_returns_error() -> None:
    svc = _FakeInstallService(InstallResult("x", "y", "1.0"))
    tool = create_install_skill_tool(install_service_factory=lambda: svc)
    result = await tool.execute("tc-3", InstallSkillInput(candidate_id="!!!bad!!!"))

    assert result.is_error
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_install_skill.py -v 2>&1 | head -10
```
Expected: `ImportError` — `install_skill` does not exist yet.

- [ ] **Step 3: Implement `install_skill` tool**

Note: the factory accepts an `install_service_factory` callable (for testability) rather than a pre-built service. In `run_manager.py` (Task 3), the factory will close over `catalog_session`, `registry`, `catalog`, `org_id`, `org_slug`, `workspace_id`, and `actor_user_id`.

```python
# backend/cubeplex/tools/builtin/install_skill.py
"""install_skill tool — install a skill candidate for the current workspace.

Only call this when the user has explicitly requested installation in
the current conversation. On success, call load_skill(canonical_name)
immediately to begin using the installed skill.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from pydantic import BaseModel, Field

from cubeplex.skills.discovery import SkillInstallError, SkillInstallService
from cubeplex.skills.sources.base import CandidateIdError


class InstallSkillInput(BaseModel):
    candidate_id: str = Field(
        description=(
            "The candidate_id from a find_skills result. "
            "Only call this after the user has explicitly confirmed they want to install."
        )
    )


def create_install_skill_tool(
    *,
    install_service_factory: Callable[[], SkillInstallService],
) -> AgentTool[InstallSkillInput]:
    async def _execute(
        tool_call_id: str,
        args: InstallSkillInput,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        svc = install_service_factory()
        try:
            result = await svc.install(args.candidate_id)
        except CandidateIdError as exc:
            return AgentToolResult(
                content=[TextContent(text=f"BAD_CANDIDATE_ID: {exc}")], is_error=True
            )
        except SkillInstallError as exc:
            return AgentToolResult(
                content=[TextContent(text=str(exc))], is_error=True
            )

        payload = {
            "installed": True,
            "canonical_name": result.canonical_name,
            "version": result.installed_version,
        }
        return AgentToolResult(content=[TextContent(text=json.dumps(payload))])

    return AgentTool(
        name="install_skill",
        description=(
            "Install a skill candidate into the current workspace. "
            "Only call this when the user has explicitly asked to install. "
            "Pass the candidate_id from a find_skills result. "
            "On success, call load_skill(canonical_name) to use the skill immediately."
        ),
        parameters=InstallSkillInput,
        execute=_execute,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_install_skill.py -v
```
Expected: 3 tests pass.

- [ ] **Step 5: Update `find_skills` hint**

In `backend/cubeplex/tools/builtin/find_skills.py`, replace the `hint` string:

```python
            "hint": (
                "To use an 'enabled' candidate now, call load_skill(canonical_name). "
                "To install an 'in_catalog' or 'available' candidate: present it to the "
                "user with preview_skill(candidate_id) so they can see what it does, then "
                "call install_skill(candidate_id) only when the user explicitly asks to install. "
                "Never install silently."
            ),
```

- [ ] **Step 6: Run existing find_skills tests to confirm no regression**

```bash
cd backend && uv run pytest tests/unit/ tests/e2e/test_find_skills_tool.py -v -k "skill" 2>&1 | tail -15
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/tools/builtin/install_skill.py backend/cubeplex/tools/builtin/find_skills.py backend/tests/unit/test_install_skill.py
git commit -m "feat(skills): add install_skill agent tool; update find_skills hint"
```

---

## Task 3: Register `preview_skill` and `install_skill` in `run_manager.py`

**Files:**
- Modify: `backend/cubeplex/streams/run_manager.py`

- [ ] **Step 1: Locate the `find_skills` registration block**

Open `backend/cubeplex/streams/run_manager.py`. The block to extend is around line 1134:

```python
        if skill_catalog is not None and catalog_session is not None:
            try:
                from cubeplex.repositories.organization import OrganizationRepository
                from cubeplex.skills.discovery import SkillDiscoveryService
                from cubeplex.skills.sources.registry import SkillsAdapterManager
                from cubeplex.tools.builtin.find_skills import create_find_skills_tool

                _org = await OrganizationRepository(catalog_session).get(ctx.org_id)
                if _org is not None:
                    _registry = await SkillsAdapterManager.build(
                        session=catalog_session,
                        catalog=skill_catalog,
                        org_id=ctx.org_id,
                        org_slug=_org.slug,
                        workspace_id=ctx.workspace_id,
                    )
                    _builtin_tools.append(
                        create_find_skills_tool(discovery=SkillDiscoveryService(_registry))
                    )
            except Exception as _exc:  # noqa: BLE001
                logger.warning("find_skills unavailable for cubepi run: {}", _exc)
```

- [ ] **Step 2: Extend the block to also register `preview_skill` and `install_skill`**

Replace the entire `if skill_catalog is not None and catalog_session is not None:` block with:

```python
        if skill_catalog is not None and catalog_session is not None:
            try:
                from cubeplex.repositories.organization import OrganizationRepository
                from cubeplex.skills.discovery import (
                    SkillDiscoveryService,
                    SkillInstallService,
                )
                from cubeplex.skills.service import SkillPublishService
                from cubeplex.skills.sources.registry import SkillsAdapterManager
                from cubeplex.tools.builtin.find_skills import create_find_skills_tool
                from cubeplex.tools.builtin.install_skill import create_install_skill_tool
                from cubeplex.tools.builtin.preview_skill import create_preview_skill_tool

                _org = await OrganizationRepository(catalog_session).get(ctx.org_id)
                if _org is not None:
                    _registry = await SkillsAdapterManager.build(
                        session=catalog_session,
                        catalog=skill_catalog,
                        org_id=ctx.org_id,
                        org_slug=_org.slug,
                        workspace_id=ctx.workspace_id,
                    )
                    _builtin_tools.append(
                        create_find_skills_tool(discovery=SkillDiscoveryService(_registry))
                    )
                    _builtin_tools.append(
                        create_preview_skill_tool(
                            session=catalog_session,
                            registry=_registry,
                            catalog=skill_catalog,
                            org_id=ctx.org_id,
                        )
                    )

                    def _make_install_factory(
                        _session: object = catalog_session,
                        _registry: object = _registry,
                        _catalog: object = skill_catalog,
                        _org_id: str = ctx.org_id,
                        _org_slug: str = _org.slug,
                        _workspace_id: str | None = ctx.workspace_id,
                        _actor: str = ctx.user_id,
                    ) -> SkillInstallService:
                        return SkillInstallService(
                            session=_session,
                            registry=_registry,
                            publisher=SkillPublishService(
                                session=_session, cache=_catalog.cache
                            ),
                            org_id=_org_id,
                            org_slug=_org_slug,
                            workspace_id=_workspace_id,
                            actor_user_id=_actor,
                        )

                    _builtin_tools.append(
                        create_install_skill_tool(
                            install_service_factory=_make_install_factory
                        )
                    )
            except Exception as _exc:  # noqa: BLE001
                logger.warning("find_skills unavailable for cubepi run: {}", _exc)
```

- [ ] **Step 3: Type-check**

```bash
cd backend && uv run mypy cubeplex/streams/run_manager.py cubeplex/tools/builtin/preview_skill.py cubeplex/tools/builtin/install_skill.py
```
Expected: no errors (or only pre-existing `Any` notes).

- [ ] **Step 4: Run full unit test suite**

```bash
cd backend && uv run pytest tests/unit/ -x -q 2>&1 | tail -10
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/streams/run_manager.py
git commit -m "feat(skills): register preview_skill and install_skill in run_manager"
```

---

## Task 4: Frontend — `/discover/preview` proxy route

The `SkillCandidatePanel` (Task 6) calls `GET /api/v1/ws/{wsId}/skills/discover/preview?candidate_id=xxx`. This Next.js proxy route does not yet exist.

**Files:**
- Create: `frontend/packages/web/app/api/v1/ws/[wsId]/skills/discover/preview/route.ts`

- [ ] **Step 1: Create the proxy route**

```typescript
// frontend/packages/web/app/api/v1/ws/[wsId]/skills/discover/preview/route.ts
/**
 * Skill candidate preview proxy — GET /discover/preview?candidate_id=
 * Forwards to the backend and returns { candidate_id, name, canonical_name, content, env_vars }.
 */
import { type NextRequest, NextResponse } from 'next/server'

const BACKEND_URL = process.env.CUBEPLEX_API_URL ?? 'http://localhost:8000'

function buildProxyHeaders(request: NextRequest): HeadersInit {
  const headers: Record<string, string> = { Accept: 'application/json' }
  const cookie = request.headers.get('cookie')
  const userId = request.headers.get('x-user-id')
  const csrf = request.headers.get('x-csrf-token')
  if (cookie) headers.cookie = cookie
  if (userId) headers['x-user-id'] = userId
  if (csrf) headers['X-CSRF-Token'] = csrf
  return headers
}

function appendSetCookie(target: Headers, source: Headers): void {
  const getSetCookie = (source as Headers & { getSetCookie?: () => string[] }).getSetCookie
  if (typeof getSetCookie === 'function') {
    for (const value of getSetCookie.call(source)) {
      target.append('set-cookie', value)
    }
    return
  }
  const setCookie = source.get('set-cookie')
  if (setCookie) target.append('set-cookie', setCookie)
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ wsId: string }> },
) {
  const { wsId } = await params
  const url = new URL(request.url)
  const candidateId = url.searchParams.get('candidate_id') ?? ''

  const backendRes = await fetch(
    `${BACKEND_URL}/api/v1/ws/${wsId}/skills/discover/preview?candidate_id=${encodeURIComponent(candidateId)}`,
    { headers: buildProxyHeaders(request) },
  )

  const data = await backendRes.json()
  const response = NextResponse.json(data, { status: backendRes.status })
  appendSetCookie(response.headers, backendRes.headers)
  return response
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd frontend && pnpm --filter web tsc --noEmit 2>&1 | grep -E "error|Error" | head -10
```
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add "frontend/packages/web/app/api/v1/ws/[wsId]/skills/discover/preview/route.ts"
git commit -m "feat(skills): add /discover/preview Next.js proxy route"
```

---

## Task 5: panelStore — `skill-candidate` view type

**Files:**
- Modify: `frontend/packages/core/src/stores/panelStore.ts`

- [ ] **Step 1: Add `skill-candidate` to `PanelView` union and `PanelStore` interface**

In `frontend/packages/core/src/stores/panelStore.ts`, add to the `PanelView` union:

```typescript
export type PanelView =
  | { type: 'closed' }
  | {
      type: 'tool'
      toolName: string
      toolArgs: Record<string, unknown>
      toolResult: string | null
      contentType: PanelContentType
      toolRef: ToolCallRef | null
      highlightText: string | null
      highlightKey: number
    }
  | {
      type: 'artifact'
      conversationId: string
      artifactId: string
    }
  | {
      type: 'attachment'
      info: AttachmentPanelInfo
    }
  | { type: 'browser' }
  | { type: 'skill-candidate'; candidateId: string }  // ← add this
```

Add to the `PanelStore` interface:

```typescript
  openSkillCandidate: (candidateId: string) => void
```

Add to the `create<PanelStore>` implementation (after `openBrowser`):

```typescript
  openSkillCandidate: (candidateId) => set({ view: { type: 'skill-candidate', candidateId } }),
```

- [ ] **Step 2: Build `@cubeplex/core` to check for type errors**

```bash
cd frontend && pnpm --filter @cubeplex/core build 2>&1 | tail -10
```
Expected: build succeeds, no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/core/src/stores/panelStore.ts
git commit -m "feat(skills): add skill-candidate panel view type to panelStore"
```

---

## Task 6: `SkillCandidatePanel` + i18n keys + AppShell integration

**Files:**
- Create: `frontend/packages/web/components/panel/SkillCandidatePanel.tsx`
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/components/layout/AppShell.tsx`

- [ ] **Step 1: Add i18n keys to `en.json`**

In `frontend/packages/web/messages/en.json`, add a `skillCandidatePanel` key inside `panel`:

```json
"panel": {
  ...existing keys...,
  "skillCandidatePanel": {
    "loading": "Loading skill preview…",
    "fetchError": "Could not load skill preview. The remote source may be unavailable.",
    "retry": "Retry",
    "noDescription": "No description available.",
    "installButton": "Install",
    "installing": "Installing…",
    "installed": "Installed",
    "installError": "Installation failed. Please try again."
  }
}
```

- [ ] **Step 2: Create `SkillCandidatePanel`**

```typescript
// frontend/packages/web/components/panel/SkillCandidatePanel.tsx
'use client'

import useSWR from 'swr'
import { useState } from 'react'
import { useTranslations } from 'next-intl'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Download } from 'lucide-react'
import { usePanelStore } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { csrfHeaders } from '@/lib/csrf'
import { proseClasses, cn } from '@/lib/utils'

interface CandidatePreview {
  candidate_id: string
  name: string
  canonical_name: string
  content: string
  env_vars: string[]
}

async function fetchPreview(url: string): Promise<CandidatePreview> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`preview fetch failed: ${res.status}`)
  return res.json() as Promise<CandidatePreview>
}

export function SkillCandidatePanel({ candidateId }: { candidateId: string }) {
  const t = useTranslations('panel.skillCandidatePanel')
  const { workspaceId } = useWorkspaceContext()
  const close = usePanelStore((s) => s.close)

  const url = workspaceId
    ? `/api/v1/ws/${workspaceId}/skills/discover/preview?candidate_id=${encodeURIComponent(candidateId)}`
    : null

  const { data, error, isLoading, mutate } = useSWR<CandidatePreview>(url, fetchPreview, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })

  const [installing, setInstalling] = useState(false)
  const [installState, setInstallState] = useState<'idle' | 'done' | 'error'>('idle')
  const [installError, setInstallError] = useState<string | null>(null)

  async function handleInstall(): Promise<void> {
    if (!workspaceId) return
    setInstalling(true)
    setInstallError(null)
    try {
      const res = await fetch(`/api/v1/ws/${workspaceId}/skills/install`, {
        method: 'POST',
        credentials: 'include',
        headers: { ...csrfHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ candidate_id: candidateId }),
      })
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as Record<string, unknown>
        setInstallError(typeof body.detail === 'string' ? body.detail : t('installError'))
        setInstallState('error')
        return
      }
      setInstallState('done')
    } finally {
      setInstalling(false)
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
        {isLoading && (
          <p className="text-sm text-muted-foreground">{t('loading')}</p>
        )}

        {error && !isLoading && (
          <div className="flex flex-col gap-2">
            <p className="text-sm text-destructive">{t('fetchError')}</p>
            <Button variant="outline" size="sm" onClick={() => void mutate()}>
              {t('retry')}
            </Button>
          </div>
        )}

        {data && (
          <>
            <header className="flex flex-wrap items-baseline gap-2">
              <span className="font-mono font-semibold">{data.name}</span>
              {data.env_vars.length > 0 && (
                <span className="text-xs text-muted-foreground">
                  requires: {data.env_vars.join(', ')}
                </span>
              )}
            </header>

            {installError && (
              <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {installError}
              </p>
            )}

            <div className={proseClasses}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.content}</ReactMarkdown>
            </div>
          </>
        )}
      </div>

      {data && (
        <div className="shrink-0 border-t p-4">
          <Button
            size="sm"
            disabled={installing || installState === 'done'}
            onClick={() => void handleInstall()}
            className="flex items-center gap-1.5"
          >
            <Download className="size-3.5" />
            {installState === 'done'
              ? t('installed')
              : installing
                ? t('installing')
                : t('installButton')}
          </Button>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Add `skill-candidate` branch to `AppShell`**

In `frontend/packages/web/components/layout/AppShell.tsx`, add the import:

```typescript
import { SkillCandidatePanel } from '@/components/panel/SkillCandidatePanel'
```

Replace the panel content conditional (the `{view.type === 'artifact' ? ... : <ToolDetailPanel />}` block) with:

```typescript
            {view.type === 'artifact' ? (
              <ArtifactPanel />
            ) : view.type === 'attachment' ? (
              <AttachmentPreviewView info={view.info} />
            ) : view.type === 'browser' ? (
              <BrowserView workspaceId={workspaceId} />
            ) : view.type === 'skill-candidate' ? (
              <SkillCandidatePanel candidateId={view.candidateId} />
            ) : (
              <ToolDetailPanel />
            )}
```

- [ ] **Step 4: Check TypeScript**

```bash
cd frontend && pnpm --filter web tsc --noEmit 2>&1 | grep -E "error TS" | head -10
```
Expected: no new errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/components/panel/SkillCandidatePanel.tsx \
        frontend/packages/web/messages/en.json \
        frontend/packages/web/components/layout/AppShell.tsx
git commit -m "feat(skills): add SkillCandidatePanel and skill-candidate AppShell branch"
```

---

## Task 7: `SkillCandidateCard` + `SkillSearchResults`

**Files:**
- Create: `frontend/packages/web/components/chat/tool-results/SkillCandidateCard.tsx`
- Create: `frontend/packages/web/components/chat/tool-results/SkillSearchResults.tsx`

First, add i18n keys for the card in `en.json` under a new `skillCard` key inside `chat` (or a top-level `skillSearch` namespace — check existing chat keys and follow the convention):

```json
"skillSearch": {
  "noDescription": "No description available",
  "preview": "Preview",
  "install": "Install",
  "installing": "Installing…",
  "installed": "Installed",
  "installError": "Install failed",
  "trustOfficial": "official",
  "trustCommunity": "community",
  "trustUnvetted": "unvetted",
  "stateEnabled": "installed",
  "stateAvailable": "available",
  "downloads": "{count} installs"
}
```

- [ ] **Step 1: Add `skillSearch` i18n keys to `en.json`**

Add the `skillSearch` block from above as a top-level key in `frontend/packages/web/messages/en.json`.

- [ ] **Step 2: Create `SkillCandidateCard`**

```typescript
// frontend/packages/web/components/chat/tool-results/SkillCandidateCard.tsx
'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Download } from 'lucide-react'
import { usePanelStore } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { csrfHeaders } from '@/lib/csrf'
import { cn } from '@/lib/utils'

export interface SkillCandidate {
  candidate_id: string
  name: string
  canonical_name: string
  description: string
  source: string
  source_name: string
  repo: string | null
  trust: 'official' | 'community' | 'untrusted'
  install_state: 'enabled' | 'in_catalog' | 'available'
  install_count: number | null
  unvetted: boolean
}

const TRUST_BADGE: Record<string, string> = {
  official: 'bg-blue-500/10 text-blue-600 dark:text-blue-400',
  community: 'bg-amber-500/10 text-amber-700 dark:text-amber-400',
  untrusted: 'bg-muted text-muted-foreground',
}

const STATE_BADGE: Record<string, string> = {
  enabled: 'bg-green-500/10 text-green-700 dark:text-green-400',
  in_catalog: 'bg-muted text-muted-foreground',
  available: 'bg-muted text-muted-foreground',
}

export function SkillCandidateCard({ candidate }: { candidate: SkillCandidate }) {
  const t = useTranslations('skillSearch')
  const { workspaceId } = useWorkspaceContext()
  const openSkillCandidate = usePanelStore((s) => s.openSkillCandidate)

  const [installing, setInstalling] = useState(false)
  const [installState, setInstallState] = useState<SkillCandidate['install_state']>(
    candidate.install_state,
  )
  const [installError, setInstallError] = useState<string | null>(null)

  async function handleInstall(): Promise<void> {
    if (!workspaceId || installState === 'enabled') return
    setInstalling(true)
    setInstallError(null)
    try {
      const res = await fetch(`/api/v1/ws/${workspaceId}/skills/install`, {
        method: 'POST',
        credentials: 'include',
        headers: { ...csrfHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ candidate_id: candidate.candidate_id }),
      })
      if (!res.ok) {
        setInstallError(t('installError'))
        return
      }
      setInstallState('enabled')
    } finally {
      setInstalling(false)
    }
  }

  const trustLabel = t(`trust${candidate.trust.charAt(0).toUpperCase()}${candidate.trust.slice(1)}` as 'trustOfficial' | 'trustCommunity' | 'trustUnvetted')
  const stateLabel = installState === 'enabled' ? t('stateEnabled') : t('stateAvailable')

  return (
    <div className="rounded-xl border border-border bg-card p-3 flex flex-col gap-2 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-col gap-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="font-mono text-sm font-semibold truncate">{candidate.name}</span>
            <span
              className={cn(
                'rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                TRUST_BADGE[candidate.trust] ?? TRUST_BADGE.untrusted,
              )}
            >
              {trustLabel}
            </span>
            <span
              className={cn(
                'rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                STATE_BADGE[installState] ?? STATE_BADGE.available,
              )}
            >
              {stateLabel}
            </span>
          </div>
          <span className="text-xs text-muted-foreground">{candidate.source_name}</span>
        </div>
        {candidate.install_count != null && (
          <div className="flex items-center gap-0.5 shrink-0 text-xs text-muted-foreground">
            <Download className="size-3" />
            <span>{candidate.install_count.toLocaleString()}</span>
          </div>
        )}
      </div>

      <p className="text-xs text-foreground/80 leading-relaxed">
        {candidate.description || t('noDescription')}
      </p>

      {installError && (
        <p className="text-xs text-destructive">{installError}</p>
      )}

      <div className="flex gap-1.5">
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs"
          onClick={() => openSkillCandidate(candidate.candidate_id)}
        >
          {t('preview')}
        </Button>
        <Button
          size="sm"
          className="h-7 text-xs"
          disabled={installing || installState === 'enabled'}
          onClick={() => void handleInstall()}
        >
          {installState === 'enabled'
            ? t('installed')
            : installing
              ? t('installing')
              : t('install')}
        </Button>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Create `SkillSearchResults`**

```typescript
// frontend/packages/web/components/chat/tool-results/SkillSearchResults.tsx
'use client'

import { SkillCandidateCard, type SkillCandidate } from './SkillCandidateCard'

interface SkillSearchResultsProps {
  resultJson: string
}

interface FindSkillsPayload {
  candidates: SkillCandidate[]
  hint?: string
}

function parsePayload(json: string): SkillCandidate[] {
  try {
    const parsed = JSON.parse(json) as FindSkillsPayload
    return Array.isArray(parsed.candidates) ? parsed.candidates : []
  } catch {
    return []
  }
}

export function SkillSearchResults({ resultJson }: SkillSearchResultsProps) {
  const candidates = parsePayload(resultJson)
  if (candidates.length === 0) return null

  return (
    <div className="flex flex-col gap-2 py-1">
      {candidates.map((c) => (
        <SkillCandidateCard key={c.candidate_id} candidate={c} />
      ))}
    </div>
  )
}
```

- [ ] **Step 4: TypeScript check**

```bash
cd frontend && pnpm --filter web tsc --noEmit 2>&1 | grep -E "error TS" | head -10
```
Expected: no new errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/components/chat/tool-results/ frontend/packages/web/messages/en.json
git commit -m "feat(skills): add SkillCandidateCard and SkillSearchResults components"
```

---

## Task 8: Wire `find_skills` rendering in `AssistantMessage.tsx`

**Files:**
- Modify: `frontend/packages/web/components/chat/AssistantMessage.tsx`

- [ ] **Step 1: Add `SkillSearchResults` import**

At the top of `AssistantMessage.tsx` with the other component imports, add:

```typescript
import { SkillSearchResults } from './tool-results/SkillSearchResults'
```

- [ ] **Step 2: Add `find_skills` branch in `ContentBlockRenderer`**

In `ContentBlockRenderer`, immediately before the `if (block.type === 'tool_call' && block.name === 'show_widget')` check (around line 316), add:

```typescript
  if (block.type === 'tool_call' && block.name === 'find_skills') {
    const toolResult = toolResultMap[block.id]
    if (!toolResult) return null
    return (
      <SkillSearchResults
        key={block.id}
        resultJson={toolResult.content}
      />
    )
  }
```

- [ ] **Step 3: Exclude `find_skills` from `groupBlocks`**

In the `groupBlocks` function, add `'find_skills'` to the exclusion list:

```typescript
    if (
      block.type === 'tool_call' &&
      block.name !== 'subagent' &&
      block.name !== 'save_artifact' &&
      block.name !== 'write_todos' &&
      block.name !== 'show_widget' &&
      block.name !== 'find_skills'   // ← add this
    ) {
```

- [ ] **Step 4: TypeScript check**

```bash
cd frontend && pnpm --filter web tsc --noEmit 2>&1 | grep -E "error TS" | head -10
```
Expected: no errors.

- [ ] **Step 5: Verify `pnpm build` passes**

```bash
cd frontend && pnpm --filter web build 2>&1 | tail -15
```
Expected: build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/components/chat/AssistantMessage.tsx
git commit -m "feat(skills): auto-render find_skills results as skill cards in chat"
```

---

## Self-review notes

- All three spec goals covered: `preview_skill` tool (Tasks 1, 3), `install_skill` tool (Tasks 2, 3), card rendering + panel preview (Tasks 4–8).
- `find_skills` hint updated in Task 2 to mention both `preview_skill` and `install_skill`.
- Frontend proxy route for `/discover/preview` added in Task 4 (was missing — caught by codex review).
- Session lifetime for `install_skill` is the same `catalog_session` as `find_skills`; write commits are handled by SQLAlchemy's unit-of-work when `session.commit()` is called inside `SkillInstallService` operations.
- `install_count` shown when non-null; hidden (not shown as zero) when null, per spec.
- `find_skills` excluded from `groupBlocks` so it renders standalone, not merged with adjacent tool calls.
- i18n parity: `en.json` gets both `skillSearch` and `panel.skillCandidatePanel` keys. Other locale files (if any) will need the same keys — run `pnpm --filter web build` to catch missing i18n key errors at build time.
