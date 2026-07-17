# Conversational Skill Discovery & Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Work from the worktree `/home/chris/cubeplex/.worktrees/feat/skill-discovery-install` on branch `feat/skill-discovery-install`; `cat .worktree.env` first — the backend runs on the per-slot port, never 8000.

**Goal:** Let a user describe a need in plain language → the agent calls a read-only `find_skills` tool that searches the local catalog (own-org-visible, not-yet-enabled skills) and one config-driven remote registry → returns ranked candidates with descriptions and a `candidate_id` + `canonical_name` → the user confirms via an authenticated workspace route → the chosen skill installs workspace-private (importing remote files when needed, minting `Skill.name = <org-slug>:<skill-slug>`) → it becomes loadable by `load_skill(canonical_name)` in the same conversation through the existing `SkillsMiddleware` path.

**Architecture:** A new `SkillSource` interface with two responsibilities — `search(query, limit) -> list[SkillCandidate]` and `fetch(source_ref) -> dict[str, bytes]`. Two implementations: `LocalCatalogSource` (wraps `SkillRepository.list_visible_for_org`, fetch is a no-op since files already live in our object store) and `RemoteRegistrySource` (HTTP GitHub-backed registry the shape `npx skills` consumes; search hits a directory endpoint, fetch lists the chosen skill subpath's tree and downloads every safe file under it). A `SkillSourceRegistry` holds the local source (always present) plus DB-backed remote sources (`SkillSource` table: `kind`, `base_url`, `repo`, `trust_tier`, `enabled`), and exposes `remote_source_by_id(source_id)` so preview/install resolve the EXACT source a candidate came from. A `SkillDiscoveryService` fans out across enabled sources, normalizes every result to one `SkillCandidate` shape (`candidate_id`, `name`, `canonical_name`, `description`, `keywords`, `source_kind`, `source_ref`, `trust`, `install_state`, `source_name`, `repo`), de-dupes on a **normalized display slug** (strip any `<org>:` prefix + lowercase) so a local skill collapses its remote twin and local wins, and ranks (exact → keyword → trust → popularity). `candidate_id` is an **opaque base64url token encoding `(source_kind, source_id, source_ref)`** — stateless, no slashes — so the slash-laden remote `source_ref` never has to fit a FastAPI path segment and `source_id` pins which registered remote source it came from. A `SkillInstallService` installs a candidate workspace-private: local → `OrgSkillInstallRepository.create_for_workspace`; remote → fetch the whole subpath, validate the file set (path-traversal + size, shared with the zip path), then run it through the existing `SkillPublishService._publish_from_files` (which mints `<org-slug>:<skill-slug>` and creates the workspace-private install). Scope-isolated routes: member `GET …/skills/discover`, `GET …/skills/discover/preview`, `POST …/skills/install` (workspace-private, the authenticated call **is** the confirm); admin `…/admin/skill-sources/` for remote-source management. The `find_skills` builtin tool calls `SkillDiscoveryService` directly (in-process, same as `load_skill`).

**Tech Stack:** Python 3.13, FastAPI, SQLModel, Alembic, Postgres, `httpx` (already a dep) for the remote registry client, pytest + httpx async test clients. mypy strict, ruff, 100-char lines.

**Spec:** `docs/dev/specs/2026-05-27-skill-discovery-install-design.md` — §1 source abstraction + opaque `candidate_id` + `canonical_name`; §2 `find_skills` read-only tool; §3 preview→confirm→install + immediate loadability; §4 scope/trust; §6 scope-isolated routes; §7 v1 scope.

**Scope note:** v1 ships `SkillSource` + `LocalCatalogSource` + one `RemoteRegistrySource`, the `find_skills` tool, member discover/preview/install/refresh routes, admin source-management routes, in-run enabled-set recompute, **and** the workspace skills page (discover panel + Install button + "Check for update" + chat-fallback parser) — see Task 12. Remote-skill **trust enforcement** (allowlist gating / approval queue / injection scan) is deferred to a future dedicated security/gating module per spec OQ-1 resolution; this PR ships only the user-visible **"unvetted" badge** (in the frontend candidate card and on remote-imported skill detail). Semantic search, personal scope, automated update polling, and agent-initiated install via HITL are out of scope (see spec Future Work).

---

## File Structure

- Create `cubeplex/skills/sources/__init__.py` — package marker + re-exports.
- Create `cubeplex/skills/sources/base.py` — `SkillCandidate` dataclass, `TrustTier` enum, `SkillSource` Protocol, `encode_candidate_id` / `decode_candidate_id`.
- Create `cubeplex/skills/sources/local.py` — `LocalCatalogSource`.
- Create `cubeplex/skills/sources/remote.py` — `RemoteRegistrySource` (httpx client, registry metadata parse, subpath fetch).
- Create `cubeplex/skills/sources/registry.py` — `SkillSourceRegistry` (build from DB + always-on local).
- Create `cubeplex/skills/discovery.py` — `SkillDiscoveryService` (fan-out, merge, dedupe, rank) + `SkillInstallService` (local + remote install).
- Create `cubeplex/models/skill_source.py` — `SkillSource` table (remote-source config row).
- Modify `cubeplex/models/__init__.py` — export `SkillSource`.
- Modify `cubeplex/models/public_id.py` — add `PREFIX_SKILL_SOURCE = "sksrc"`.
- Create `cubeplex/repositories/skill_source.py` — `SkillSourceRepository`.
- Create Alembic migration (autogenerate) for `skill_sources`.
- Create `cubeplex/tools/builtin/find_skills.py` — `create_find_skills_tool` factory (mirrors `load_skill.py` shape).
- Modify `cubeplex/streams/run_manager.py` — register `find_skills` next to `load_skill`; recompute enabled-skills suffix after install (already-present `list_enabled_for_workspace` call is per-turn, so no change needed there — see Task 8).
- Create `cubeplex/api/schemas/skill_discovery.py` — `SkillCandidateResponse`, `InstallCandidateRequest`, `InstallCandidateResponse`, `SkillSourceResponse`, `CreateSkillSourceRequest`.
- Modify `cubeplex/api/routes/v1/ws_skills.py` — add `GET …/discover`, `GET …/discover/preview`, `POST …/install`.
- Create `cubeplex/api/routes/v1/admin_skill_sources.py` — `/admin/skill-sources` CRUD.
- Modify `cubeplex/api/routes/v1/__init__.py` + `cubeplex/api/app.py` — register the admin router.
- Tests: `tests/unit/test_skill_candidate_id.py`, `tests/unit/test_skill_discovery_ranking.py`, `tests/unit/test_remote_registry_source.py`, `tests/e2e/test_skill_discovery_local.py`, `tests/e2e/test_skill_discovery_remote.py`, `tests/e2e/test_skill_sources_admin.py`, `tests/e2e/test_find_skills_tool.py`.

---

## Task 1: `SkillCandidate` shape + opaque `candidate_id` codec

The candidate is the one normalized shape every source returns and every route/tool speaks. `candidate_id` must round-trip `(source_kind, source_id, source_ref)` with no slashes (remote `source_ref` is e.g. `vercel-labs/skills/tree/main/skills/find-skills`; `source_id` is the registered `SkillSource` row id, empty for local). Use URL-safe base64 over a `kind|source_id|ref` payload — stateless, no DB lookup, no expiry/GC.

**Files:**
- Create: `cubeplex/skills/sources/__init__.py`
- Create: `cubeplex/skills/sources/base.py`
- Test: `tests/unit/test_skill_candidate_id.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_skill_candidate_id.py
import pytest

from cubeplex.skills.sources.base import (
    CandidateIdError,
    decode_candidate_id,
    encode_candidate_id,
)


def test_roundtrip_with_slashes_and_source_id():
    cid = encode_candidate_id(
        "remote", "vercel-labs/skills/tree/main/skills/find-skills", source_id="sksrc-7"
    )
    assert "/" not in cid  # URL-path safe
    kind, source_id, ref = decode_candidate_id(cid)
    assert kind == "remote"
    assert source_id == "sksrc-7"  # which remote source this candidate came from
    assert ref == "vercel-labs/skills/tree/main/skills/find-skills"


def test_roundtrip_local_has_empty_source_id():
    cid = encode_candidate_id("local", "skl-ABC123")
    assert decode_candidate_id(cid) == ("local", "", "skl-ABC123")


def test_decode_rejects_garbage():
    with pytest.raises(CandidateIdError):
        decode_candidate_id("!!!not-base64!!!")
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd backend && uv run pytest tests/unit/test_skill_candidate_id.py -q`
Expected: FAIL with `ModuleNotFoundError: cubeplex.skills.sources.base`.

- [ ] **Step 3: Implement**

```python
# cubeplex/skills/sources/__init__.py
"""Skill discovery sources: local catalog + remote registry behind one interface."""
```

```python
# cubeplex/skills/sources/base.py
"""Candidate shape, trust tiers, the SkillSource protocol, and the opaque
candidate-id codec.

candidate_id is a URL-safe base64 token over ``"{kind}|{source_id}|{source_ref}"``.
It is the *only* handle clients pass back to preview/install, so a slash-laden
remote source_ref (a GitHub repo subpath) never has to fit a FastAPI path
segment. ``source_id`` is the registered remote ``SkillSource`` row id (empty for
local candidates) — preview/install use it to pick the EXACT source the candidate
came from, since an org can register multiple remote sources. Stateless: decode
recovers (kind, source_id, source_ref) without any server lookup.

Both ``source_id`` and ``source_ref`` may not contain the ``|`` delimiter; row
ids and GitHub subpaths never do, so we split on the first two ``|`` only.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Protocol

SourceKind = Literal["local", "remote"]


class TrustTier(str, Enum):
    official = "official"   # vetted upstream (vercel-labs, anthropics, …)
    community = "community"  # known but unvetted
    untrusted = "untrusted"  # default for ad-hoc remote sources


class CandidateIdError(ValueError):
    """Raised when a candidate_id cannot be decoded."""


def encode_candidate_id(kind: SourceKind, source_ref: str, *, source_id: str = "") -> str:
    payload = f"{kind}|{source_id}|{source_ref}".encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_candidate_id(candidate_id: str) -> tuple[SourceKind, str, str]:
    pad = "=" * (-len(candidate_id) % 4)
    try:
        raw = base64.urlsafe_b64decode(candidate_id + pad).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise CandidateIdError(f"undecodable candidate_id: {candidate_id!r}") from exc
    parts = raw.split("|", 2)
    if len(parts) != 3 or parts[0] not in ("local", "remote"):
        raise CandidateIdError(f"malformed candidate_id payload: {raw!r}")
    kind, source_id, source_ref = parts
    return kind, source_id, source_ref  # type: ignore[return-value]


@dataclass(frozen=True)
class SkillCandidate:
    """One normalized discovery result across any source.

    name           — human-facing display name (remote: upstream slug).
    canonical_name — the name load_skill resolves: local catalog name, or for a
                     not-yet-imported remote skill the name import WILL mint
                     (<org-slug>:<skill-slug>), computed up front.
    """

    candidate_id: str
    name: str
    canonical_name: str
    description: str
    source_kind: SourceKind
    source_ref: str
    keywords: list[str] = field(default_factory=list)
    version: str | None = None
    trust: TrustTier = TrustTier.untrusted
    install_state: Literal["enabled", "in_catalog", "available"] = "available"
    stars: int | None = None
    install_count: int | None = None
    # Display-safe provenance for the confirm/trust card (spec §3, §4): which
    # source this came from and the upstream repo. Local: source_name="catalog",
    # repo=None. Remote: the registered SkillSource.name + its repo.
    source_name: str = "catalog"
    repo: str | None = None


class SkillSource(Protocol):
    kind: SourceKind

    async def search(self, query: str, *, limit: int) -> list[SkillCandidate]: ...

    async def fetch(self, source_ref: str) -> dict[str, bytes]:
        """Return {rel_path: bytes} of the skill bundle for import. No-op-able."""
        ...
```

- [ ] **Step 4: Run to confirm pass + lint**

Run: `cd backend && uv run pytest tests/unit/test_skill_candidate_id.py -q && uv run ruff check cubeplex/skills/sources/`
Expected: 3 passed; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/skills/sources/__init__.py backend/cubeplex/skills/sources/base.py backend/tests/unit/test_skill_candidate_id.py
git commit -m "feat(skills): candidate shape + opaque candidate_id codec"
```

---

## Task 2: `SkillSource` config table + repository + migration

Remote sources are DB/config-driven, never hardcoded. One row per registered remote source.

**Files:**
- Create: `cubeplex/models/skill_source.py`
- Modify: `cubeplex/models/__init__.py`
- Modify: `cubeplex/models/public_id.py`
- Create: `cubeplex/repositories/skill_source.py`
- Migration: autogenerated

- [ ] **Step 1: Add the prefix**

In `cubeplex/models/public_id.py`, after `PREFIX_EGRESS_REF`:

```python
PREFIX_SKILL_SOURCE: str = "sksrc"
```

- [ ] **Step 2: Model**

```python
# cubeplex/models/skill_source.py
"""Registered remote skill registries (org-scoped admin config)."""

from typing import ClassVar

from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase


class SkillSource(CubeplexBase, table=True):
    """A remote registry an org admin registered for discovery.

    The built-in local catalog source is implicit (always present) and has no
    row here — only remote registries are persisted.
    """

    _PREFIX: ClassVar[str] = "sksrc"
    __tablename__ = "skill_sources"

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    name: str = Field(max_length=128)
    kind: str = Field(max_length=16, default="remote")  # "remote"
    base_url: str = Field(max_length=512)  # registry directory/query endpoint
    repo: str | None = Field(default=None, max_length=256)  # GitHub owner/repo if applicable
    trust_tier: str = Field(max_length=16, default="untrusted")
    enabled: bool = Field(default=True)
    created_by_user_id: str = Field(foreign_key="users.id", max_length=20)
```

In `cubeplex/models/__init__.py` add `SkillSource` to the skill-model import block and `__all__`.

- [ ] **Step 3: Repository**

```python
# cubeplex/repositories/skill_source.py
"""Repository for registered remote skill sources."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import SkillSource


class SkillSourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        org_id: str,
        name: str,
        base_url: str,
        repo: str | None,
        trust_tier: str,
        created_by_user_id: str,
    ) -> SkillSource:
        row = SkillSource(
            org_id=org_id,
            name=name,
            base_url=base_url,
            repo=repo,
            trust_tier=trust_tier,
            created_by_user_id=created_by_user_id,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def get(self, org_id: str, source_id: str) -> SkillSource | None:
        row = await self.session.get(SkillSource, source_id)
        if row is None or row.org_id != org_id:
            return None
        return row

    async def list_for_org(self, org_id: str, *, enabled_only: bool = False) -> list[SkillSource]:
        stmt = select(SkillSource).where(SkillSource.org_id == org_id)  # type: ignore[arg-type]
        if enabled_only:
            stmt = stmt.where(SkillSource.enabled.is_(True))  # type: ignore[attr-defined]
        stmt = stmt.order_by(SkillSource.name)
        return list((await self.session.execute(stmt)).scalars().all())

    async def set_enabled(self, org_id: str, source_id: str, enabled: bool) -> bool:
        row = await self.get(org_id, source_id)
        if row is None:
            return False
        row.enabled = enabled
        await self.session.commit()
        return True

    async def set_trust_tier(self, org_id: str, source_id: str, trust_tier: str) -> bool:
        row = await self.get(org_id, source_id)
        if row is None:
            return False
        row.trust_tier = trust_tier
        await self.session.commit()
        return True
```

- [ ] **Step 4: Autogenerate the migration**

Run: `cd backend && uv run alembic revision --autogenerate -m "add skill_sources"`
Expected: a new file under `alembic/versions/` creating `skill_sources` with the `org_id` index. Do **not** hand-edit it; inspect that it only adds `skill_sources`.

- [ ] **Step 5: Apply + verify**

Run: `cd backend && uv run alembic upgrade head && uv run python -c "from cubeplex.models import SkillSource; print(SkillSource.__tablename__)"`
Expected: prints `skill_sources`; no error.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/models/skill_source.py backend/cubeplex/models/__init__.py backend/cubeplex/models/public_id.py backend/cubeplex/repositories/skill_source.py backend/alembic/versions/
git commit -m "feat(skills): SkillSource config table + repository + migration"
```

---

## Task 3: `LocalCatalogSource`

Wraps `SkillRepository.list_visible_for_org` scoped to the asking org; candidates are catalog rows **not yet enabled in the asking workspace**. `fetch` is a no-op (files already in our store; install just creates the install row). `install_state` distinguishes already-enabled (`enabled`) from in-catalog-but-not-enabled (`in_catalog`).

**Files:**
- Create: `cubeplex/skills/sources/local.py`
- (covered by Task 6 ranking unit test + Task 7 e2e)

- [ ] **Step 1: Implement**

```python
# cubeplex/skills/sources/local.py
"""Local catalog as a SkillSource: own-org-visible skills, not yet enabled here."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.repositories.skill import SkillRepository
from cubeplex.skills.service import SkillCatalogService
from cubeplex.skills.sources.base import (
    SkillCandidate,
    SourceKind,
    TrustTier,
    encode_candidate_id,
)


class LocalCatalogSource:
    kind: SourceKind = "local"

    def __init__(
        self,
        *,
        session: AsyncSession,
        catalog: SkillCatalogService,
        org_id: str,
        workspace_id: str,
    ) -> None:
        self._session = session
        self._catalog = catalog
        self._org_id = org_id
        self._workspace_id = workspace_id

    async def search(self, query: str, *, limit: int) -> list[SkillCandidate]:
        visible = await SkillRepository(self._session).list_visible_for_org(self._org_id)
        enabled = await self._catalog.list_enabled_for_workspace(
            self._workspace_id, org_id=self._org_id
        )
        enabled_names = {r.name for r in enabled}
        out: list[SkillCandidate] = []
        for s in visible:
            out.append(
                SkillCandidate(
                    candidate_id=encode_candidate_id("local", s.id),
                    name=s.name,
                    canonical_name=s.name,  # local: catalog name IS the canonical name
                    description=s.description,
                    source_kind="local",
                    source_ref=s.id,
                    keywords=s.keywords,
                    version=s.current_version,
                    trust=TrustTier.official,  # already in our trust boundary
                    install_state="enabled" if s.name in enabled_names else "in_catalog",
                    source_name="catalog",
                    repo=None,
                )
            )
        return out  # discovery service ranks/filters/limits; source returns the full visible set

    async def fetch(self, source_ref: str) -> dict[str, bytes]:
        return {}  # no-op: local files already in our object store
```

- [ ] **Step 2: Lint + import check**

Run: `cd backend && uv run ruff check cubeplex/skills/sources/local.py && uv run python -c "from cubeplex.skills.sources.local import LocalCatalogSource"`
Expected: clean; no import error.

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/skills/sources/local.py
git commit -m "feat(skills): LocalCatalogSource over list_visible_for_org"
```

---

## Task 4: `RemoteRegistrySource` + shared file-set validator

Talks to a GitHub-backed registry the `npx skills` shape. `search` GETs the directory query endpoint and parses skill metadata. `fetch` lists the chosen skill's subpath **tree** (so it imports the real skill — `references/`, `scripts/`, assets — not just `SKILL.md` + a hardcoded handful), then downloads every safe file under that one pinned subpath (the `npx skills` subpath footgun — issue #1015 — means we pin the subpath, not pull the whole repo). `canonical_name` is computed up front from the installing org slug. This task also extracts the zip-path size/traversal guards into a shared `validate_skill_files` so the remote path enforces the same limits (Task 7 calls it).

**Files:**
- Create: `cubeplex/skills/sources/remote.py`
- Modify: `cubeplex/skills/service.py` — extract `validate_skill_files(files)` from `_extract_zip`.
- Test: `tests/unit/test_remote_registry_source.py`

- [ ] **Step 1: Write the failing test (against a faithful in-test HTTP stub)**

```python
# tests/unit/test_remote_registry_source.py
import httpx
import pytest

from cubeplex.skills.sources.base import TrustTier
from cubeplex.skills.sources.remote import RemoteRegistrySource


def _registry_app() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(
                200,
                json={
                    "skills": [
                        {
                            "name": "slide-deck",
                            "description": "Build slide decks",
                            "keywords": ["slides", "deck"],
                            "ref": "acme/skills/tree/main/skills/slide-deck",
                            "stars": 1200,
                            "installs": 50,
                        }
                    ]
                },
            )
        if request.url.path.startswith("/tree/"):
            # The skill subpath tree: SKILL.md plus a reference and a script —
            # proving fetch imports the WHOLE subpath, not a hardcoded handful.
            return httpx.Response(
                200,
                json={"files": ["SKILL.md", "references/style.md", "scripts/run.py"]},
            )
        if request.url.path.endswith("/SKILL.md"):
            return httpx.Response(
                200,
                text="---\nname: slide-deck\ndescription: Build slide decks\nversion: 1.0.0\n---\n# x\n",
            )
        if request.url.path.endswith("/references/style.md"):
            return httpx.Response(200, text="# style guide\n")
        if request.url.path.endswith("/scripts/run.py"):
            return httpx.Response(200, text="print('hi')\n")
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_search_normalizes_and_computes_canonical_name():
    src = RemoteRegistrySource(
        source_id="sksrc-1",
        base_url="https://reg.test",
        trust_tier=TrustTier.community,
        org_slug="acme",
        transport=_registry_app(),
    )
    cands = await src.search("slides", limit=5)
    assert len(cands) == 1
    c = cands[0]
    assert c.name == "slide-deck"
    assert c.canonical_name == "acme:slide-deck"  # what import WILL mint
    assert c.source_ref == "acme/skills/tree/main/skills/slide-deck"
    assert c.trust == TrustTier.community
    assert c.stars == 1200
    # candidate_id carries the originating source id so preview/install pick the
    # exact source even with multiple remote sources registered.
    from cubeplex.skills.sources.base import decode_candidate_id

    assert decode_candidate_id(c.candidate_id) == (
        "remote", "sksrc-1", "acme/skills/tree/main/skills/slide-deck",
    )


@pytest.mark.asyncio
async def test_fetch_imports_whole_subpath_tree_not_just_skill_md():
    src = RemoteRegistrySource(
        source_id="sksrc-1",
        base_url="https://reg.test",
        trust_tier=TrustTier.community,
        org_slug="acme",
        transport=_registry_app(),
    )
    files = await src.fetch("acme/skills/tree/main/skills/slide-deck")
    # Whole subpath came down: SKILL.md PLUS the reference + script, by their
    # tree-relative paths — not a hardcoded SKILL.md + guessed-sibling set.
    assert set(files) == {"SKILL.md", "references/style.md", "scripts/run.py"}
    assert b"slide-deck" in files["SKILL.md"]
    assert b"style guide" in files["references/style.md"]
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd backend && uv run pytest tests/unit/test_remote_registry_source.py -q`
Expected: FAIL with `ModuleNotFoundError: cubeplex.skills.sources.remote`.

- [ ] **Step 3: Implement**

```python
# cubeplex/skills/sources/remote.py
"""Remote GitHub-backed skill registry as a SkillSource.

search() hits the registry directory; fetch() lists the chosen skill's subpath
TREE then downloads every safe file under it (issue #1015: pulling the bare repo
grabs every skill — we pin the subpath so we import exactly the chosen skill, but
ALL of it: references/, scripts/, assets — not just SKILL.md + a guessed handful).
Files are stored, never executed at install time.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import httpx

from cubeplex.skills.sources.base import (
    SkillCandidate,
    SourceKind,
    TrustTier,
    encode_candidate_id,
)

# Cap how many files one skill bundle may contain (defense-in-depth alongside the
# per-file / total-byte caps validate_skill_files enforces at install time).
_MAX_TREE_ENTRIES = 200


class RemoteRegistrySource:
    kind: SourceKind = "remote"

    def __init__(
        self,
        *,
        source_id: str,
        base_url: str,
        trust_tier: TrustTier,
        org_slug: str,
        source_name: str = "remote",
        repo: str | None = None,
        transport: httpx.AsyncBaseTransport | httpx.MockTransport | None = None,
    ) -> None:
        self.source_id = source_id  # registered SkillSource row id; goes in candidate_id
        self._base_url = base_url.rstrip("/")
        self._trust = trust_tier
        self._org_slug = org_slug
        self._source_name = source_name  # display name for the trust card
        self._repo = repo  # upstream owner/repo for the trust card
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url, transport=self._transport, timeout=15.0
        )

    async def search(self, query: str, *, limit: int) -> list[SkillCandidate]:
        async with self._client() as client:
            resp = await client.get("/search", params={"q": query, "limit": limit})
            resp.raise_for_status()
            data = resp.json()
        out: list[SkillCandidate] = []
        for item in data.get("skills", []):
            slug = item["name"]
            out.append(
                SkillCandidate(
                    candidate_id=encode_candidate_id(
                        "remote", item["ref"], source_id=self.source_id
                    ),
                    name=slug,
                    canonical_name=f"{self._org_slug}:{slug}",  # what import WILL mint
                    description=item.get("description", ""),
                    source_kind="remote",
                    source_ref=item["ref"],
                    keywords=list(item.get("keywords", [])),
                    version=item.get("version"),
                    trust=self._trust,
                    install_state="available",
                    stars=item.get("stars"),
                    install_count=item.get("installs"),
                    source_name=self._source_name,
                    repo=item.get("repo") or self._repo,
                )
            )
        return out

    async def fetch(self, source_ref: str) -> dict[str, bytes]:
        """Import the WHOLE skill subpath: list its tree, then pull every safe file.

        Real skills carry references/, scripts/, and assets — not just SKILL.md.
        We list the registry's tree endpoint for the pinned subpath and download
        each entry under it, so the imported bundle matches the upstream skill.
        Path-traversal / size enforcement happens at install time via
        validate_skill_files (Task 7); here we only refuse obviously-unsafe rels
        and cap entry count so a hostile tree can't fan out unbounded fetches.
        """
        files: dict[str, bytes] = {}
        async with self._client() as client:
            tree = await client.get(f"/tree/{source_ref}")
            tree.raise_for_status()
            entries = tree.json().get("files", [])
            if len(entries) > _MAX_TREE_ENTRIES:
                raise ValueError(f"skill tree has {len(entries)} files; cap {_MAX_TREE_ENTRIES}")
            for rel in entries:
                # rel is the path RELATIVE to the skill subpath (e.g. "SKILL.md",
                # "references/api.md", "scripts/run.py"). Reject absolute / `..`
                # rels before issuing the fetch; install-time validation re-checks.
                parts = PurePosixPath(rel).parts
                if rel.startswith("/") or ".." in parts:
                    raise ValueError(f"unsafe path in remote skill tree: {rel!r}")
                resp = await client.get(f"/raw/{source_ref}/{rel}")
                resp.raise_for_status()
                files[rel] = resp.content
        if "SKILL.md" not in files:
            raise ValueError("remote skill subpath has no SKILL.md")
        return files
```

- [ ] **Step 4: Extract the shared file-set validator in `service.py`**

`_extract_zip` currently inlines the path-traversal (`..`) and size checks. Pull the per-file
checks into a reusable `validate_skill_files(files: dict[str, bytes]) -> None` that raises the
existing `InvalidZipPathError` / `FileTooLargeError`, then call it from `_extract_zip` so the
zip path is unchanged AND the remote import path (Task 7) can call the same function. No new
limits — reuse `MAX_FILE_BYTES` / `MAX_TOTAL_BYTES`.

```python
# cubeplex/skills/service.py — new module-level helper, called by _extract_zip and remote install
def validate_skill_files(files: dict[str, bytes]) -> None:
    """Enforce path-traversal + per-file + total-size limits on a skill bundle.

    Shared by the zip-upload path (_extract_zip) and the remote-import path so both
    enforce identical limits. Raises InvalidZipPathError / FileTooLargeError.
    """
    total = 0
    for rel, data in files.items():
        if rel.startswith("/") or ".." in PurePosixPath(rel).parts:
            raise InvalidZipPathError(f"invalid path in skill bundle: {rel!r}")
        if len(data) > MAX_FILE_BYTES:
            raise FileTooLargeError(f"{rel} is {len(data)} bytes; cap is {MAX_FILE_BYTES}")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise FileTooLargeError(f"bundle exceeds total cap of {MAX_TOTAL_BYTES} bytes")
```

In `_extract_zip`, after building `out` (and before/after `_normalize_skill_zip_files`), keep the
existing per-`info` checks OR replace them with a single `validate_skill_files(out)` call — pick
whichever leaves zip behavior byte-identical (the per-`info.file_size` check reads the declared
size; `validate_skill_files` checks the read bytes — equivalent for our purposes).

- [ ] **Step 5: Run to confirm pass + lint**

Run: `cd backend && uv run pytest tests/unit/test_remote_registry_source.py -q && uv run ruff check cubeplex/skills/sources/remote.py cubeplex/skills/service.py`
Expected: 2 passed; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/skills/sources/remote.py backend/cubeplex/skills/service.py backend/tests/unit/test_remote_registry_source.py
git commit -m "feat(skills): RemoteRegistrySource whole-subpath fetch + shared file validator"
```

---

## Task 5: `SkillSourceRegistry`

Builds the live source set for one (org, workspace): the always-on local source + every enabled remote `SkillSource` row, each wrapped in a `RemoteRegistrySource`.

**Files:**
- Create: `cubeplex/skills/sources/registry.py`

- [ ] **Step 1: Implement**

```python
# cubeplex/skills/sources/registry.py
"""Assembles the live SkillSource set for an (org, workspace)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.repositories.skill_source import SkillSourceRepository
from cubeplex.skills.service import SkillCatalogService
from cubeplex.skills.sources.base import SkillSource, TrustTier
from cubeplex.skills.sources.local import LocalCatalogSource
from cubeplex.skills.sources.remote import RemoteRegistrySource


class SkillSourceRegistry:
    def __init__(self, sources: list[SkillSource]) -> None:
        self._sources = sources

    @property
    def sources(self) -> list[SkillSource]:
        return self._sources

    def remote_source_by_id(self, source_id: str) -> SkillSource | None:
        """Return the enabled remote source with this row id, or None.

        Preview/install decode the candidate_id's source_id and look the exact
        source up here — never "first remote", which would fetch from the wrong
        registry when an org has multiple remote sources (or none, if the source
        was disabled/deleted between discover and install → caller maps to 404).
        """
        for s in self._sources:
            if s.kind == "remote" and getattr(s, "source_id", None) == source_id:
                return s
        return None

    @classmethod
    async def build(
        cls,
        *,
        session: AsyncSession,
        catalog: SkillCatalogService,
        org_id: str,
        org_slug: str,
        workspace_id: str,
    ) -> SkillSourceRegistry:
        sources: list[SkillSource] = [
            LocalCatalogSource(
                session=session, catalog=catalog, org_id=org_id, workspace_id=workspace_id
            )
        ]
        rows = await SkillSourceRepository(session).list_for_org(org_id, enabled_only=True)
        for row in rows:
            sources.append(
                RemoteRegistrySource(
                    source_id=row.id,
                    base_url=row.base_url,
                    trust_tier=TrustTier(row.trust_tier),
                    org_slug=org_slug,
                    source_name=row.name,
                    repo=row.repo,
                )
            )
        return cls(sources)
```

- [ ] **Step 2: Import check**

Run: `cd backend && uv run python -c "from cubeplex.skills.sources.registry import SkillSourceRegistry" && uv run ruff check cubeplex/skills/sources/registry.py`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/skills/sources/registry.py
git commit -m "feat(skills): SkillSourceRegistry (always-on local + enabled remotes)"
```

---

## Task 6: `SkillDiscoveryService` — fan-out, merge, dedupe, rank (unit)

Pure logic worth a focused unit test: exact > keyword > trust > popularity; the same skill across sources collapses on its **normalized display slug** (not `canonical_name`, which differs — local `slug` vs remote `<org>:slug`), with local winning.

**Files:**
- Create: `cubeplex/skills/discovery.py` (discovery half; install half lands in Task 7)
- Test: `tests/unit/test_skill_discovery_ranking.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_skill_discovery_ranking.py
import pytest

from cubeplex.skills.discovery import rank_candidates
from cubeplex.skills.sources.base import SkillCandidate, TrustTier


def _c(name, *, desc="", trust=TrustTier.untrusted, stars=None, kind="remote", keywords=None):
    return SkillCandidate(
        candidate_id=f"{kind}-{name}",
        name=name,
        canonical_name=name if kind == "local" else f"acme:{name}",
        description=desc,
        source_kind=kind,  # type: ignore[arg-type]
        source_ref=name,
        keywords=keywords or [],
        trust=trust,
        stars=stars,
    )


def test_exact_name_match_ranks_first():
    cands = [_c("slide-deck", desc="slides"), _c("deck", desc="exact deck match")]
    ranked = rank_candidates(cands, query="deck", limit=5)
    assert ranked[0].name == "deck"


def test_trust_then_popularity_breaks_ties():
    a = _c("a", desc="data tool", trust=TrustTier.community, stars=10)
    b = _c("b", desc="data tool", trust=TrustTier.official, stars=1)
    c = _c("c", desc="data tool", trust=TrustTier.community, stars=99)
    ranked = rank_candidates([a, b, c], query="data", limit=5)
    assert ranked[0].name == "b"          # official beats community
    assert [x.name for x in ranked[1:]] == ["c", "a"]  # then stars desc


def test_dedupe_local_wins_against_remote_twin():
    # Local canonical is the bare slug; the remote twin's canonical is "acme:slug".
    # Dedupe must collapse them on the normalized slug (not canonical_name) so local
    # wins — keying on canonical_name would leave BOTH because the strings differ.
    local = _c("frontend-design", kind="local")          # canonical "frontend-design"
    remote = SkillCandidate(
        candidate_id="remote-fd", name="frontend-design",
        canonical_name="acme:frontend-design", description="", source_kind="remote",
        source_ref="x/y", keywords=[],
    )
    ranked = rank_candidates([remote, local], query="frontend", limit=5)
    assert len(ranked) == 1
    assert ranked[0].source_kind == "local"
    assert ranked[0].canonical_name == "frontend-design"  # survivor keeps its own canonical


def test_limit_applied():
    cands = [_c(f"s{i}", desc="thing") for i in range(10)]
    assert len(rank_candidates(cands, query="thing", limit=3)) == 3


def test_plain_language_query_matches_tokens():
    # "make a slide deck" must surface slide-deck even though the whole query
    # string is not a substring of the name/keywords.
    target = _c("slide-deck", desc="Build presentations", keywords=["slides", "deck"])
    noise = _c("data-pipeline", desc="ETL jobs", keywords=["etl"])
    ranked = rank_candidates([noise, target], query="make a slide deck", limit=5)
    assert ranked[0].name == "slide-deck"


def test_single_keyword_token_matches():
    target = _c("deck-builder", desc="", keywords=["slides"])
    ranked = rank_candidates([_c("unrelated", desc="x"), target], query="slides", limit=5)
    assert ranked[0].name == "deck-builder"
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd backend && uv run pytest tests/unit/test_skill_discovery_ranking.py -q`
Expected: FAIL with `ModuleNotFoundError: cubeplex.skills.discovery`.

- [ ] **Step 3: Implement the discovery half**

```python
# cubeplex/skills/discovery.py
"""Discovery (fan-out + rank) and install services for conversational skills."""

from __future__ import annotations

import re

from cubeplex.skills.sources.base import SkillCandidate, TrustTier
from cubeplex.skills.sources.registry import SkillSourceRegistry

_TRUST_RANK = {TrustTier.official: 0, TrustTier.community: 1, TrustTier.untrusted: 2}


def _dedupe_key(c: SkillCandidate) -> str:
    """Normalized display slug used to collapse the same skill across sources.

    Local canonical_name is a bare slug ("frontend-design"); remote canonical_name
    is "<org>:<slug>" ("acme:frontend-design"). Deduping on canonical_name would
    therefore NEVER match a local skill against its remote twin. Key on the slug
    AFTER stripping any "<org>:" prefix and lowercasing, so local and remote of the
    same skill collide and "local wins" can actually fire.
    """
    return c.name.split(":", 1)[-1].strip().lower()


def _tokens(text: str) -> set[str]:
    # Lowercase word tokens, splitting on non-alphanumerics so "slide-deck",
    # "slide deck" and "make a slide deck" all yield {slide, deck, ...}.
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t}


def _score(c: SkillCandidate, query: str) -> tuple[int, int, int, int]:
    q = query.lower().strip()
    name = c.name.lower()
    haystack = f"{name} {c.description.lower()} {' '.join(k.lower() for k in c.keywords)}"
    # Tokenize so plain-language queries ("make a slide deck") match keyword/name
    # tokens ("slide-deck" → {slide, deck}); whole-string substring alone misses these.
    q_tokens = _tokens(query)
    name_tokens = _tokens(c.name)
    hay_tokens = _tokens(haystack)
    if name == q:
        match = 0
    elif q and (name.startswith(q) or q in name):
        match = 1
    elif q_tokens and q_tokens <= name_tokens:
        # every query token appears in the skill name → strong match
        match = 1
    elif q and q in haystack:
        match = 2
    elif q_tokens and (q_tokens & hay_tokens):
        # at least one query token hits name/description/keywords
        match = 2
    else:
        match = 3
    return (match, _TRUST_RANK.get(c.trust, 9), -(c.stars or 0), -(c.install_count or 0))


def rank_candidates(
    candidates: list[SkillCandidate], *, query: str, limit: int
) -> list[SkillCandidate]:
    """Dedupe by normalized display slug (local wins), then sort and truncate.

    Returns survivors unchanged — each still carries its own canonical_name (bare
    slug for local, "<org>:<slug>" for remote), which install/load resolve against.
    """
    by_slug: dict[str, SkillCandidate] = {}
    for c in candidates:
        key = _dedupe_key(c)
        prev = by_slug.get(key)
        if prev is None or (prev.source_kind != "local" and c.source_kind == "local"):
            by_slug[key] = c
    ordered = sorted(by_slug.values(), key=lambda c: _score(c, query))
    return ordered[:limit]


class SkillDiscoveryService:
    def __init__(self, registry: SkillSourceRegistry) -> None:
        self._registry = registry

    async def discover(self, query: str, *, limit: int = 5) -> list[SkillCandidate]:
        merged: list[SkillCandidate] = []
        for source in self._registry.sources:
            try:
                merged.extend(await source.search(query, limit=limit * 2))
            except Exception:  # noqa: BLE001 — one bad remote must not kill discovery
                continue
        return rank_candidates(merged, query=query, limit=limit)
```

- [ ] **Step 4: Run to confirm pass + lint**

Run: `cd backend && uv run pytest tests/unit/test_skill_discovery_ranking.py -q && uv run ruff check cubeplex/skills/discovery.py`
Expected: 4 passed; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/skills/discovery.py backend/tests/unit/test_skill_discovery_ranking.py
git commit -m "feat(skills): SkillDiscoveryService fan-out + ranking"
```

---

## Task 7: `SkillInstallService` — local + remote workspace-private install (e2e)

Install decodes the `candidate_id`, then: **local** → `OrgSkillInstallRepository.create_for_workspace` against the catalog row; **remote** → `source.fetch(source_ref)` → `SkillPublishService._publish_from_files(..., workspace_id=ws)` which mints `<org-slug>:<skill-slug>` and creates the workspace-private install. Both return the **canonical name** that `load_skill` resolves. Drive it end-to-end against a real DB + object store (local path) — that's the spec's primary E2E.

**Files:**
- Modify: `cubeplex/skills/discovery.py` (add `SkillInstallService` + `InstallResult`)
- Test: `tests/e2e/test_skill_discovery_local.py`

- [ ] **Step 1: Write the failing E2E (local catalog round-trip via routes)**

This test asserts the full member flow over HTTP. It depends on the routes from Task 8, so it will first fail on the missing endpoints — write it now to pin the contract.

```python
# tests/e2e/test_skill_discovery_local.py
import httpx
import pytest


@pytest.mark.asyncio
async def test_discover_then_install_local_skill_becomes_enabled(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = member_client

    # A preinstalled skill ("deep-research") is in the catalog but NOT enabled here.
    disc = await client.get(
        f"/api/v1/ws/{ws_id}/skills/discover", params={"q": "research"}
    )
    assert disc.status_code == 200
    cands = disc.json()
    cand = next(c for c in cands if c["name"] == "deep-research")
    assert cand["install_state"] == "in_catalog"
    assert cand["canonical_name"] == "deep-research"
    assert "candidate_id" in cand and "/" not in cand["candidate_id"]

    install = await client.post(
        f"/api/v1/ws/{ws_id}/skills/install",
        json={"candidate_id": cand["candidate_id"]},
    )
    assert install.status_code == 201
    body = install.json()
    assert body["canonical_name"] == "deep-research"

    # Now enabled in THIS workspace.
    enabled = await client.get(
        f"/api/v1/ws/{ws_id}/skills", params={"scope": "workspace"}
    )
    assert any(s["name"] == "deep-research" for s in enabled.json())


@pytest.mark.asyncio
async def test_install_is_workspace_private_not_visible_in_other_ws(
    member_client_two_workspaces: tuple[httpx.AsyncClient, str, str],
) -> None:
    client, ws_a, ws_b = member_client_two_workspaces
    disc = await client.get(f"/api/v1/ws/{ws_a}/skills/discover", params={"q": "research"})
    cand = next(c for c in disc.json() if c["name"] == "deep-research")
    await client.post(
        f"/api/v1/ws/{ws_a}/skills/install", json={"candidate_id": cand["candidate_id"]}
    )
    a = await client.get(f"/api/v1/ws/{ws_a}/skills", params={"scope": "workspace"})
    b = await client.get(f"/api/v1/ws/{ws_b}/skills", params={"scope": "workspace"})
    assert any(s["name"] == "deep-research" for s in a.json())
    assert not any(s["name"] == "deep-research" for s in b.json())
```

> If `member_client_two_workspaces` does not exist in `tests/e2e/conftest.py`, add it next to `member_client`: same authenticated client, a second workspace created in the same org. Reuse the existing workspace-creation helper the conftest already uses for `member_client`.

- [ ] **Step 2: Run to confirm it fails**

Run: `cd backend && uv run pytest tests/e2e/test_skill_discovery_local.py -q`
Expected: FAIL — 404 on `/discover` (routes not yet added in Task 8).

- [ ] **Step 3: Implement `SkillInstallService`**

```python
# append to cubeplex/skills/discovery.py
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.repositories.skill import OrgSkillInstallRepository, SkillRepository
from cubeplex.skills.service import SkillPublishService, validate_skill_files
from cubeplex.skills.sources.base import decode_candidate_id


class SkillInstallError(ValueError):
    pass


@dataclass(frozen=True)
class InstallResult:
    canonical_name: str
    skill_id: str
    installed_version: str


class SkillInstallService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        registry: SkillSourceRegistry,
        publisher: SkillPublishService,
        org_id: str,
        org_slug: str,
        workspace_id: str,
        actor_user_id: str,
    ) -> None:
        self._session = session
        self._registry = registry
        self._publisher = publisher
        self._org_id = org_id
        self._org_slug = org_slug
        self._workspace_id = workspace_id
        self._actor = actor_user_id

    async def install(self, candidate_id: str) -> InstallResult:
        kind, source_id, source_ref = decode_candidate_id(candidate_id)
        if kind == "local":
            return await self._install_local(source_ref)
        return await self._install_remote(source_id, source_ref)

    async def _install_local(self, skill_id: str) -> InstallResult:
        skills = SkillRepository(self._session)
        skill = await skills.get(skill_id)
        # visible-to-org guard: preinstalled OR own-org uploaded only
        if skill is None or not (
            skill.source == "preinstalled" or skill.owner_org_id == self._org_id
        ):
            raise SkillInstallError("candidate not visible to this org")
        await OrgSkillInstallRepository(self._session).create_for_workspace(
            org_id=self._org_id,
            workspace_id=self._workspace_id,
            skill_id=skill.id,
            installed_version=skill.current_version,
            installed_by_user_id=self._actor,
        )
        return InstallResult(
            canonical_name=skill.name,
            skill_id=skill.id,
            installed_version=skill.current_version,
        )

    async def _install_remote(self, source_id: str, source_ref: str) -> InstallResult:
        # Resolve the EXACT source the candidate came from by its row id — never
        # "first remote", which would fetch from the wrong registry when an org has
        # multiple remote sources (or none, if it was disabled/deleted since discover).
        source = self._registry.remote_source_by_id(source_id)
        if source is None:
            raise SkillInstallError("no enabled remote source for this candidate")
        files = await source.fetch(source_ref)
        if "SKILL.md" not in files:
            raise SkillInstallError("remote candidate has no SKILL.md")
        # Remote-fetched files never passed through _extract_zip, so the zip-path
        # guards (path traversal, per-file + total size) were skipped. Run the SAME
        # validation here before publish so a remote bundle can't smuggle a `..`
        # path or oversize file past the checks an uploaded zip would hit.
        validate_skill_files(files)  # raises InvalidZipPathError / FileTooLargeError
        sv = await self._publisher._publish_from_files(
            org_id=self._org_id,
            org_slug=self._org_slug,
            actor_user_id=self._actor,
            files=files,
            workspace_id=self._workspace_id,
        )
        skill = await SkillRepository(self._session).get(sv.skill_id)
        assert skill is not None
        return InstallResult(
            canonical_name=skill.name,  # <org-slug>:<skill-slug>
            skill_id=skill.id,
            installed_version=sv.version,
        )
```

> `_publish_from_files` validates the slug/frontmatter, rejects a `:` in the name, detects
> version collisions, decodes `SKILL.md` as UTF-8, uploads files, mints `<org-slug>:<skill-slug>`,
> and creates the workspace-private install (`workspace_id` set). It does **not** run the
> path-traversal/`..` and per-file/total-size checks — those live in `_extract_zip`, which only
> the zip-upload path calls. So the remote path must validate the file set itself. **Task 4
> already extracts those checks into a shared `validate_skill_files(files)` helper** (see Task 4
> Step 3 below); `_extract_zip` calls it too, so zip-upload and remote-import enforce the exact
> same `..`-path, per-file (`MAX_FILE_BYTES`), and total-size (`MAX_TOTAL_BYTES`) limits.
> Reusing the publish path plus the shared validator keeps remote import identical to a member
> upload — the spec's "reuse the publish path."

> **Route error mapping (Task 8):** the install route must map `InvalidZipPathError` and
> `FileTooLargeError` (now reachable on the remote path) to 400 the same way the existing upload
> route does — see Task 8 Step 2's `except` block, which adds these alongside `SkillInstallError`.

- [ ] **Step 4: Re-run after Task 8 wires the routes** (the e2e goes green once routes exist). For now confirm import + lint:

Run: `cd backend && uv run python -c "from cubeplex.skills.discovery import SkillInstallService" && uv run ruff check cubeplex/skills/discovery.py`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/skills/discovery.py backend/tests/e2e/test_skill_discovery_local.py backend/tests/e2e/conftest.py
git commit -m "feat(skills): SkillInstallService (local + remote workspace-private)"
```

---

## Task 8: Member routes — discover / preview / install

Scope-isolated member routes on the existing `ws_skills.py` router. The authenticated `POST …/install` **is** the user confirmation (trust boundary at the human). `candidate_id` rides the query string (preview) and JSON body (install), never the path.

**Files:**
- Create: `cubeplex/api/schemas/skill_discovery.py`
- Modify: `cubeplex/api/routes/v1/ws_skills.py`
- Tests: `tests/e2e/test_skill_discovery_local.py` (from Task 7) goes green here.

- [ ] **Step 1: Schemas**

```python
# cubeplex/api/schemas/skill_discovery.py
"""Request/response models for conversational skill discovery + install."""

from __future__ import annotations

from pydantic import BaseModel


class SkillCandidateResponse(BaseModel):
    candidate_id: str
    name: str
    canonical_name: str
    description: str
    source_kind: str
    keywords: list[str]
    version: str | None
    trust: str
    install_state: str
    stars: int | None = None
    install_count: int | None = None
    source_name: str  # display source ("catalog" or the registered remote's name)
    repo: str | None = None  # upstream owner/repo, for the trust card
    unvetted: bool  # True when source_kind == "remote" and trust != "official"


class CandidatePreviewResponse(BaseModel):
    candidate_id: str
    name: str
    canonical_name: str
    content: str  # SKILL.md text (not imported yet for remote)


class InstallCandidateRequest(BaseModel):
    candidate_id: str


class InstallCandidateResponse(BaseModel):
    canonical_name: str
    skill_id: str
    installed_version: str
```

- [ ] **Step 2: Add the three member routes to `ws_skills.py`**

Add imports for `SkillDiscoveryService`, `SkillInstallService`, `SkillInstallError`, `SkillSourceRegistry`, `decode_candidate_id`, `CandidateIdError`, `OrganizationRepository`, the publish exceptions already imported for the upload route (`InvalidZipPathError`, `FileTooLargeError`, `VersionCollisionError`, `InvalidFrontmatterError`, `InvalidSkillNameError`, `SkillMdMissingError` — `InvalidZipPathError` is new to this file), and the new schemas, then:

```python
@router.get("/discover", response_model=list[SkillCandidateResponse])
async def discover_skills(
    workspace_id: str,
    *,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str = Query(..., min_length=1),
    limit: int = Query(5, ge=1, le=20),
) -> list[SkillCandidateResponse]:
    org = await OrganizationRepository(session).get(ctx.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")
    catalog = SkillCatalogService(session=session, cache=_cache())
    registry = await SkillSourceRegistry.build(
        session=session, catalog=catalog, org_id=ctx.org_id,
        org_slug=org.slug, workspace_id=workspace_id,
    )
    cands = await SkillDiscoveryService(registry).discover(q, limit=limit)
    return [
        SkillCandidateResponse(
            candidate_id=c.candidate_id, name=c.name, canonical_name=c.canonical_name,
            description=c.description, source_kind=c.source_kind, keywords=c.keywords,
            version=c.version, trust=c.trust.value, install_state=c.install_state,
            stars=c.stars, install_count=c.install_count,
            source_name=c.source_name, repo=c.repo,
            unvetted=(c.source_kind == "remote" and c.trust.value != "official"),
        )
        for c in cands
    ]


@router.get("/discover/preview", response_model=CandidatePreviewResponse)
async def preview_candidate(
    workspace_id: str,
    *,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    candidate_id: str = Query(...),
) -> CandidatePreviewResponse:
    org = await OrganizationRepository(session).get(ctx.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")
    try:
        kind, source_id, source_ref = decode_candidate_id(candidate_id)
    except CandidateIdError as e:
        raise HTTPException(status_code=400, detail="BAD_CANDIDATE_ID") from e
    catalog = SkillCatalogService(session=session, cache=_cache())
    if kind == "local":
        skill = await SkillRepository(session).get(source_ref)
        if skill is None or not _visible(skill, ctx.org_id):
            raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")
        sv = await SkillVersionRepository(session).find(skill.id, skill.current_version)
        content = await catalog.fetch_skill_md(sv.id)  # type: ignore[union-attr]
        return CandidatePreviewResponse(
            candidate_id=candidate_id, name=skill.name,
            canonical_name=skill.name, content=content,
        )
    registry = await SkillSourceRegistry.build(
        session=session, catalog=catalog, org_id=ctx.org_id,
        org_slug=org.slug, workspace_id=workspace_id,
    )
    remote = registry.remote_source_by_id(source_id)  # exact source, never "first remote"
    if remote is None:
        raise HTTPException(status_code=404, detail="SOURCE_NOT_FOUND")
    files = await remote.fetch(source_ref)
    if "SKILL.md" not in files:
        raise HTTPException(status_code=404, detail="SKILL_MD_MISSING")
    return CandidatePreviewResponse(
        candidate_id=candidate_id, name=source_ref.rsplit("/", 1)[-1],
        canonical_name=f"{org.slug}:{source_ref.rsplit('/', 1)[-1]}",
        content=files["SKILL.md"].decode("utf-8"),
    )


@router.post("/install", status_code=201, response_model=InstallCandidateResponse)
async def install_candidate(
    workspace_id: str,
    body: InstallCandidateRequest,
    *,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> InstallCandidateResponse:
    org = await OrganizationRepository(session).get(ctx.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")
    catalog = SkillCatalogService(session=session, cache=_cache())
    registry = await SkillSourceRegistry.build(
        session=session, catalog=catalog, org_id=ctx.org_id,
        org_slug=org.slug, workspace_id=workspace_id,
    )
    install = SkillInstallService(
        session=session, registry=registry,
        publisher=SkillPublishService(session=session, cache=_cache()),
        org_id=ctx.org_id, org_slug=org.slug,
        workspace_id=workspace_id, actor_user_id=ctx.user.id,
    )
    try:
        result = await install.install(body.candidate_id)
    except CandidateIdError as e:
        raise HTTPException(status_code=400, detail="BAD_CANDIDATE_ID") from e
    except InvalidZipPathError as e:  # remote bundle had a `..` / absolute path
        raise HTTPException(status_code=400, detail={"code": "INVALID_PATH", "reason": str(e)}) from e
    except FileTooLargeError as e:  # remote bundle exceeded per-file / total caps
        raise HTTPException(status_code=400, detail={"code": "FILE_TOO_LARGE", "reason": str(e)}) from e
    except VersionCollisionError as e:
        raise HTTPException(status_code=409, detail={"code": "VERSION_EXISTS", "reason": str(e)}) from e
    except (InvalidFrontmatterError, InvalidSkillNameError, SkillMdMissingError) as e:
        raise HTTPException(status_code=400, detail={"code": "INVALID_SKILL", "reason": str(e)}) from e
    except SkillInstallError as e:
        raise HTTPException(status_code=400, detail={"code": "INSTALL_FAILED", "reason": str(e)}) from e
    return InstallCandidateResponse(
        canonical_name=result.canonical_name, skill_id=result.skill_id,
        installed_version=result.installed_version,
    )
```

> **Route order:** declare `/discover` and `/discover/preview` **before** the existing `GET /{skill_id}` so FastAPI doesn't match `discover` as a `skill_id`. Place them directly after `list_skills_in_ws`.

- [ ] **Step 3: Run the local E2E (now green) + the existing ws_skills suite**

Run: `cd backend && uv run pytest tests/e2e/test_skill_discovery_local.py tests/e2e/test_skills_marketplace.py -q`
Expected: all PASS.

- [ ] **Step 4: Type-check + lint changed files**

Run: `cd backend && uv run mypy cubeplex/skills cubeplex/api/routes/v1/ws_skills.py && uv run ruff check cubeplex/skills cubeplex/api/routes/v1/ws_skills.py cubeplex/api/schemas/skill_discovery.py`
Expected: no issues.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/api/schemas/skill_discovery.py backend/cubeplex/api/routes/v1/ws_skills.py
git commit -m "feat(skills): member discover/preview/install routes"
```

---

## Task 9: `find_skills` builtin tool + run_manager wiring

Read-only agent tool sitting next to `load_skill`. It calls `SkillDiscoveryService` in-process and returns descriptions-only candidates plus the opaque `candidate_id` and `canonical_name`. It never installs; for already-enabled candidates it tells the agent to `load_skill(canonical_name)`.

**Files:**
- Create: `cubeplex/tools/builtin/find_skills.py`
- Modify: `cubeplex/streams/run_manager.py`
- Test: `tests/e2e/test_find_skills_tool.py`

- [ ] **Step 1: Write the failing E2E (tool executes against a real catalog)**

```python
# tests/e2e/test_find_skills_tool.py
import pytest

from cubeplex.skills.cache import SkillCache
from cubeplex.skills.service import SkillCatalogService
from cubeplex.skills.sources.registry import SkillSourceRegistry
from cubeplex.skills.discovery import SkillDiscoveryService
from cubeplex.tools.builtin.find_skills import FindSkillsInput, create_find_skills_tool


@pytest.mark.asyncio
async def test_find_skills_tool_returns_local_candidate(seeded_session_org_ws):
    session, org_id, org_slug, ws_id = seeded_session_org_ws  # fixture: seeded catalog
    catalog = SkillCatalogService(session=session, cache=SkillCache(cache_root="skills_cache"))
    registry = await SkillSourceRegistry.build(
        session=session, catalog=catalog, org_id=org_id, org_slug=org_slug, workspace_id=ws_id
    )
    tool = create_find_skills_tool(discovery=SkillDiscoveryService(registry))
    result = await tool.execute("tc-1", FindSkillsInput(query="research"))
    assert not result.is_error
    text = result.content[0].text
    assert "deep-research" in text
    assert "candidate_id" in text
```

> Add a `seeded_session_org_ws` fixture to `tests/e2e/conftest.py` if absent: a DB session with the preinstalled seeder run + a bootstrapped org/workspace. Reuse `seed_preinstalled_skills` and the existing org/workspace bootstrap helpers.

- [ ] **Step 2: Run to confirm it fails**

Run: `cd backend && uv run pytest tests/e2e/test_find_skills_tool.py -q`
Expected: FAIL with `ModuleNotFoundError: cubeplex.tools.builtin.find_skills`.

- [ ] **Step 3: Implement the tool**

```python
# cubeplex/tools/builtin/find_skills.py
"""find_skills tool — read-only conversational skill discovery (cubepi AgentTool).

Mirrors load_skill.py's wrapper shape. Returns ranked candidates as JSON
(descriptions only, never full SKILL.md). The agent passes a candidate_id back
to the install route (a user-confirmed action), or load_skill(canonical_name)
for already-enabled candidates.
"""

from __future__ import annotations

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from pydantic import BaseModel, Field

from cubeplex.skills.discovery import SkillDiscoveryService


class FindSkillsInput(BaseModel):
    query: str = Field(description="Plain-language description of the capability you need.")
    limit: int = Field(default=5, ge=1, le=20)


def create_find_skills_tool(*, discovery: SkillDiscoveryService) -> AgentTool[FindSkillsInput]:
    async def _execute(
        tool_call_id: str,
        args: FindSkillsInput,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        cands = await discovery.discover(args.query, limit=args.limit)
        payload = {
            "candidates": [
                {
                    "candidate_id": c.candidate_id,
                    "name": c.name,
                    "canonical_name": c.canonical_name,
                    "description": c.description,
                    "source": c.source_kind,
                    "source_name": c.source_name,
                    "repo": c.repo,
                    "trust": c.trust.value,
                    "install_state": c.install_state,
                    "unvetted": c.source_kind == "remote" and c.trust.value != "official",
                }
                for c in cands
            ],
            "hint": (
                "To use an 'enabled' candidate now, call load_skill(canonical_name). "
                "To install an 'in_catalog' or 'available' candidate, ask the user to "
                "confirm — installation is a user action via the install button/route, "
                "never silent."
            ),
        }
        import json

        return AgentToolResult(content=[TextContent(text=json.dumps(payload))])

    return AgentTool(
        name="find_skills",
        description=(
            "Search available skills (your org's catalog + registered remote registries) "
            "by a plain-language need. Read-only: returns ranked candidates with "
            "descriptions; it never installs anything."
        ),
        parameters=FindSkillsInput,
        execute=_execute,
    )
```

- [ ] **Step 4: Wire it in `run_manager.py` right after the `load_skill` block (~line 975)**

```python
        # find_skills — read-only discovery; needs catalog + a source registry.
        # NOTE: `_run_cubepi_path` does NOT have a `session` local — the DB session
        # in scope is the `catalog_session` PARAM (the same one `skill_catalog` was
        # built from). Use `catalog_session` here and guard it for None (it can be
        # None when the catalog DB was unavailable at run start).
        if skill_catalog is not None and catalog_session is not None:
            try:
                from cubeplex.repositories.organization import OrganizationRepository
                from cubeplex.skills.discovery import SkillDiscoveryService
                from cubeplex.skills.sources.registry import SkillSourceRegistry
                from cubeplex.tools.builtin.find_skills import create_find_skills_tool

                _org = await OrganizationRepository(catalog_session).get(ctx.org_id)
                if _org is not None:
                    _registry = await SkillSourceRegistry.build(
                        session=catalog_session, catalog=skill_catalog, org_id=ctx.org_id,
                        org_slug=_org.slug, workspace_id=ctx.workspace_id,
                    )
                    _builtin_tools.append(
                        create_find_skills_tool(
                            discovery=SkillDiscoveryService(_registry)
                        )
                    )
            except Exception as _exc:  # noqa: BLE001
                logger.warning("find_skills unavailable for cubepi run: {}", _exc)
```

> Place it **after** `load_skill` so tool order stays `… memory → load_skill → find_skills → view_images …`, preserving the cache-prefix discipline (see `backend/docs/prompt-cache-discipline.md`). The in-scope session is `catalog_session` (a `_run_cubepi_path` parameter), not `session` — there is no `session` local in this method.

- [ ] **Step 5: Run the tool E2E + lint**

Run: `cd backend && uv run pytest tests/e2e/test_find_skills_tool.py -q && uv run ruff check cubeplex/tools/builtin/find_skills.py`
Expected: PASS; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/tools/builtin/find_skills.py backend/cubeplex/streams/run_manager.py backend/tests/e2e/test_find_skills_tool.py backend/tests/e2e/conftest.py
git commit -m "feat(skills): find_skills builtin tool + run_manager wiring"
```

---

## Task 10: Same-conversation loadability — recompute enabled set (verify, not change)

The spec requires a freshly installed skill to be loadable in the *same* run. `run_manager.py` already calls `list_enabled_for_workspace` and rebuilds the available-skills suffix **per agent turn** (line ~1806). Because install writes a real `OrgSkillInstall` row, the next turn's recompute sees it and `load_skill(canonical_name)` resolves via `find_enabled_by_name`. This task **verifies** that with an E2E rather than adding code — only add a recompute if the verification shows the suffix is built once per conversation, not per turn.

**Files:**
- Test: `tests/e2e/test_skill_discovery_local.py` (extend)

- [ ] **Step 1: Add an E2E asserting post-install load resolves**

Extend the local E2E: after install, assert `find_enabled_by_name(ws, org_id, name=canonical_name)` returns non-None (drives the exact resolution `load_skill` uses), and that a fresh `SkillCatalogService.list_enabled_for_workspace` includes it. If the run-loop turns out to cache the suffix per conversation (inspect lines ~1799–1820), add a recompute call after the agent loop detects a successful install tool-result and document it here.

```python
@pytest.mark.asyncio
async def test_installed_skill_resolves_via_find_enabled_by_name(
    member_client_with_session: tuple[httpx.AsyncClient, str, "AsyncSession", str],
) -> None:
    client, ws_id, session, org_id = member_client_with_session
    disc = await client.get(f"/api/v1/ws/{ws_id}/skills/discover", params={"q": "research"})
    cand = next(c for c in disc.json() if c["name"] == "deep-research")
    inst = await client.post(
        f"/api/v1/ws/{ws_id}/skills/install", json={"candidate_id": cand["candidate_id"]}
    )
    name = inst.json()["canonical_name"]

    from cubeplex.skills.cache import SkillCache
    from cubeplex.skills.service import SkillCatalogService

    catalog = SkillCatalogService(session=session, cache=SkillCache(cache_root="skills_cache"))
    resolved = await catalog.find_enabled_by_name(ws_id, org_id=org_id, name=name)
    assert resolved is not None  # exactly what load_skill calls
```

- [ ] **Step 2: Run + confirm**

Run: `cd backend && uv run pytest tests/e2e/test_skill_discovery_local.py -q`
Expected: all PASS. If it fails because the suffix is cached per-conversation, implement the recompute and re-run.

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/streams/run_manager.py backend/tests/e2e/test_skill_discovery_local.py
git commit -m "test(skills): verify installed skill is loadable in same conversation"
```

---

## Task 11: Admin source-management routes

Scope-isolated admin router for registering/listing/enabling/disabling remote sources and pinning trust tier. Members never touch source config (spec §4: only org admins register remote sources).

**Files:**
- Create: `cubeplex/api/routes/v1/admin_skill_sources.py`
- Modify: `cubeplex/api/routes/v1/__init__.py`, `cubeplex/api/app.py`
- Test: `tests/e2e/test_skill_sources_admin.py`

- [ ] **Step 1: Write the failing E2E**

```python
# tests/e2e/test_skill_sources_admin.py
import httpx
import pytest


@pytest.mark.asyncio
async def test_admin_can_register_and_disable_remote_source(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = admin_client
    create = await client.post(
        "/api/v1/admin/skill-sources",
        json={"name": "skills.sh", "base_url": "https://www.skills.sh",
              "repo": "vercel-labs/skills", "trust_tier": "official"},
    )
    assert create.status_code == 201
    sid = create.json()["id"]

    listed = await client.get("/api/v1/admin/skill-sources")
    assert any(s["id"] == sid for s in listed.json())

    disabled = await client.patch(
        f"/api/v1/admin/skill-sources/{sid}", json={"enabled": False}
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False


@pytest.mark.asyncio
async def test_member_cannot_reach_admin_source_routes(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = member_client
    resp = await client.get("/api/v1/admin/skill-sources")
    assert resp.status_code in (401, 403)
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd backend && uv run pytest tests/e2e/test_skill_sources_admin.py -q`
Expected: FAIL — 404 (router not registered).

- [ ] **Step 3: Implement the admin router** (uses `get_admin_request_context` from `cubeplex.mcp.dependencies`, the org-admin dependency used by other admin routes)

```python
# cubeplex/api/routes/v1/admin_skill_sources.py
"""Org-admin management of remote skill sources (/admin/skill-sources)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.context import RequestContext
from cubeplex.db import get_session
from cubeplex.mcp.dependencies import get_admin_request_context
from cubeplex.models import SkillSource
from cubeplex.repositories.skill_source import SkillSourceRepository

router = APIRouter(prefix="/admin/skill-sources", tags=["admin-skill-sources"])

_TRUST_TIERS = {"official", "community", "untrusted"}


class CreateSkillSourceRequest(BaseModel):
    name: str
    base_url: str
    repo: str | None = None
    trust_tier: str = "untrusted"


class PatchSkillSourceRequest(BaseModel):
    enabled: bool | None = None
    trust_tier: str | None = None


class SkillSourceResponse(BaseModel):
    id: str
    name: str
    kind: str
    base_url: str
    repo: str | None
    trust_tier: str
    enabled: bool


def _to_response(row: SkillSource) -> SkillSourceResponse:
    return SkillSourceResponse(
        id=row.id, name=row.name, kind=row.kind, base_url=row.base_url,
        repo=row.repo, trust_tier=row.trust_tier, enabled=row.enabled,
    )


@router.post("", status_code=201, response_model=SkillSourceResponse)
async def create_source(
    body: CreateSkillSourceRequest,
    *,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillSourceResponse:
    if body.trust_tier not in _TRUST_TIERS:
        raise HTTPException(status_code=400, detail="BAD_TRUST_TIER")
    row = await SkillSourceRepository(session).create(
        org_id=ctx.org_id, name=body.name, base_url=body.base_url,
        repo=body.repo, trust_tier=body.trust_tier, created_by_user_id=ctx.user.id,
    )
    return _to_response(row)


@router.get("", response_model=list[SkillSourceResponse])
async def list_sources(
    *,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[SkillSourceResponse]:
    rows = await SkillSourceRepository(session).list_for_org(ctx.org_id)
    return [_to_response(r) for r in rows]


@router.patch("/{source_id}", response_model=SkillSourceResponse)
async def patch_source(
    source_id: str,
    body: PatchSkillSourceRequest,
    *,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillSourceResponse:
    repo = SkillSourceRepository(session)
    if body.enabled is not None:
        if not await repo.set_enabled(ctx.org_id, source_id, body.enabled):
            raise HTTPException(status_code=404, detail="SOURCE_NOT_FOUND")
    if body.trust_tier is not None:
        if body.trust_tier not in _TRUST_TIERS:
            raise HTTPException(status_code=400, detail="BAD_TRUST_TIER")
        if not await repo.set_trust_tier(ctx.org_id, source_id, body.trust_tier):
            raise HTTPException(status_code=404, detail="SOURCE_NOT_FOUND")
    row = await repo.get(ctx.org_id, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="SOURCE_NOT_FOUND")
    return _to_response(row)
```

- [ ] **Step 4: Register the router**

In `cubeplex/api/routes/v1/__init__.py` add `admin_skill_sources` to the import block + `__all__`. In `cubeplex/api/app.py`, alongside the existing `admin_skills` include:

```python
    app.include_router(admin_skill_sources.router, prefix="/api/v1")
```

- [ ] **Step 5: Run the admin E2E + lint/type**

Run: `cd backend && uv run pytest tests/e2e/test_skill_sources_admin.py -q && uv run mypy cubeplex/api/routes/v1/admin_skill_sources.py && uv run ruff check cubeplex/api/routes/v1/admin_skill_sources.py`
Expected: PASS; clean.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/api/routes/v1/admin_skill_sources.py backend/cubeplex/api/routes/v1/__init__.py backend/cubeplex/api/app.py backend/tests/e2e/test_skill_sources_admin.py
git commit -m "feat(skills): admin remote-source management routes"
```

---

## Task 12: Remote discovery + install E2E against a faithful fake registry

The spec's second primary E2E: admin registers a remote source (same org+workspace as the member, via `four_layer_admin_and_member`) pointed at a **local HTTP server that serves real SKILL.md + files** (a faithful stand-in, not a mock of our own code) → member discovers a remote candidate → previews → confirms install → it imports into the org catalog as `<org-slug>:<slug>` and installs workspace-private → becomes loadable. Also covers the trust banner + disabled-source-returns-nothing cases.

**Files:**
- Test: `tests/e2e/test_skill_discovery_remote.py`
- Test helper: a fixture-scoped aiohttp/uvicorn fake registry, or an httpx `MockTransport` injected via a test-only `RemoteRegistrySource` transport hook. Prefer a **real local HTTP server** per the "faithful stand-in" rule; fall back to `MockTransport` only if standing up a server in the test loop is impractical.

- [ ] **Step 1: Stand up the fake registry fixture**

Serve `/search` (returns one skill's metadata with `ref`) and `/raw/<ref>/SKILL.md` (real frontmatter). To make `RemoteRegistrySource` reach it without a transport hook, the source must read `base_url` from the registered `SkillSource` row — so point `base_url` at the fixture's `http://127.0.0.1:<port>`. If `SkillSourceRegistry.build` needs a transport override for tests, add an optional `transport` param threaded only in tests; production passes `None`.

> **Fixture choice (important):** `admin_client` and `member_client` are
> built from SEPARATE `_make_isolated_user` calls — different orgs AND
> different app instances/ASGI transports. A source the admin registers in
> one org+app is invisible to the member in the other. Use
> `four_layer_admin_and_member` instead: it yields an admin client and a
> member client in the **same workspace, same org, same app**, which is what
> "admin registers a remote source → member discovers it" requires. Unpack as
> `(admin, ws_id, _admin_uid), (member, ws_id2, _member_uid)` (ws ids are the
> same workspace).

- [ ] **Step 2: Write the E2E**

```python
# tests/e2e/test_skill_discovery_remote.py
import httpx
import pytest


@pytest.mark.asyncio
async def test_remote_discover_preview_install_then_loadable(
    four_layer_admin_and_member: tuple[
        tuple[httpx.AsyncClient, str, str],
        tuple[httpx.AsyncClient, str, str],
    ],
    fake_registry_url: str,
) -> None:
    (admin, _admin_ws, _admin_uid), (member, ws_id, _member_uid) = four_layer_admin_and_member
    src = await admin.post(
        "/api/v1/admin/skill-sources",
        json={"name": "fake", "base_url": fake_registry_url,
              "repo": "acme/skills", "trust_tier": "community"},
    )
    assert src.status_code == 201

    disc = await member.get(f"/api/v1/ws/{ws_id}/skills/discover", params={"q": "slides"})
    cand = next(c for c in disc.json() if c["name"] == "slide-deck")
    assert cand["source_kind"] == "remote"
    assert cand["unvetted"] is True  # community tier → banner
    assert cand["canonical_name"].endswith(":slide-deck")

    preview = await member.get(
        f"/api/v1/ws/{ws_id}/skills/discover/preview",
        params={"candidate_id": cand["candidate_id"]},
    )
    assert preview.status_code == 200
    assert "slide-deck" in preview.json()["content"]

    install = await member.post(
        f"/api/v1/ws/{ws_id}/skills/install",
        json={"candidate_id": cand["candidate_id"]},
    )
    assert install.status_code == 201
    canonical = install.json()["canonical_name"]
    assert canonical.endswith(":slide-deck")

    enabled = await member.get(f"/api/v1/ws/{ws_id}/skills", params={"scope": "workspace"})
    assert any(s["name"] == canonical for s in enabled.json())


@pytest.mark.asyncio
async def test_disabled_source_returns_no_remote_candidates(
    four_layer_admin_and_member, fake_registry_url,
) -> None:
    (admin, _admin_ws, _admin_uid), (member, ws_id, _member_uid) = four_layer_admin_and_member
    src = await admin.post(
        "/api/v1/admin/skill-sources",
        json={"name": "fake", "base_url": fake_registry_url, "trust_tier": "community"},
    )
    sid = src.json()["id"]
    await admin.patch(f"/api/v1/admin/skill-sources/{sid}", json={"enabled": False})
    disc = await member.get(f"/api/v1/ws/{ws_id}/skills/discover", params={"q": "slides"})
    assert not any(c["source_kind"] == "remote" for c in disc.json())
```

- [ ] **Step 3: Run**

Run: `cd backend && uv run pytest tests/e2e/test_skill_discovery_remote.py -q`
Expected: all PASS — remote import mints `<org-slug>:slide-deck`, install is workspace-private, disabled source yields no remote candidates.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/e2e/test_skill_discovery_remote.py backend/tests/e2e/conftest.py
git commit -m "test(skills): remote discover→preview→install E2E via fake registry"
```

---

## Task 13: Trust enforcement — DEFERRED to future security module

Source allowlist + content-scan + admin approval queue are deferred to a future dedicated security/gating module per spec OQ-1 resolution. This PR ships only the user-visible **"unvetted" banner** UX surfaced from the frontend task (Task 12 — candidate cards + remote-imported skill detail). No backend enforcement hook lands in v1; the install path stays a single endpoint with no policy gate. When the security module lands, it will introduce its own seam (allowlist table + approval queue) rather than inheriting a half-built kill-switch here.

(No backend code, no commits.)

---

## Task 14: Workspace skills page — discover panel + install button + check-for-update + chat-fallback

Build the user-facing skills surface in the workspace. Mirrors the spec §7 v1 inclusion of the workspace skills page and the OQ-5 two-surface confirm decision. The chat fallback parses `install <canonical_name>` from a user message **server-side in the conversation route** (not as a frontend UI confirm card) — see the "Chat-fallback design choice" subsection below. All three new HTTP surfaces — discover, install, refresh — are existing backend endpoints from Tasks 8 and 12; this task wires up the proxy routes, the page, the module components, the `@cubeplex/core` types, and the Playwright smoke.

> **Workspace-port reminder:** inside this worktree the frontend port is allocated in `.worktree.env`, NEVER 3000. `cat .worktree.env` before `pnpm dev` — the wrapper at `frontend/scripts/with-worktree-env.mjs` reads it.

**Chat-fallback design choice — server-side message parser (not a UI confirm card).** The fallback's whole point is "pure-chat clients still work." A UI confirm card requires a UI client to render it; a server-side parser works in any client that sends a user message. The conversation route (`backend/cubeplex/api/routes/v1/ws_conversations.py` — the existing user-message ingest) gets a small detector that, on a user message matching `^install <canonical_name>\s*$` (case-sensitive, single-line), looks up the candidate by `canonical_name` in the workspace's catalog + enabled-remote candidates, calls the same `SkillInstallService.install` the HTTP route calls, and rewrites the message before the agent loop sees it (e.g. replaces with an assistant-shaped system note: "Installed `<name>` (v<version>). Use `load_skill('<canonical_name>')`."). Single source of truth = `SkillInstallService.install`. The UI button on the candidate card is the primary path; the parser is the strict fallback. (Frontend does NOT render a confirm card; the candidate card itself IS the confirmation — clicking Install is the confirm.)

**Files:**
- Create: `frontend/packages/core/src/api/skills.ts` — discover/install/refresh client + types.
- Create: `frontend/packages/core/src/stores/skillsStore.ts` — Zustand store (list, candidates, status).
- Modify: `frontend/packages/core/src/index.ts` — re-export new types + store.
- Create: `frontend/packages/web/app/api/v1/ws/[wsId]/skills/discover/route.ts` — Next proxy (GET).
- Create: `frontend/packages/web/app/api/v1/ws/[wsId]/skills/install/route.ts` — Next proxy (POST).
- Create: `frontend/packages/web/app/api/v1/ws/[wsId]/skills/[skillId]/refresh/route.ts` — Next proxy (POST).
- Create: `frontend/packages/web/app/(app)/w/[wsId]/skills/page.tsx` — workspace skills page (scope-isolated, NEW route).
- Create: `frontend/packages/web/components/skills/SkillsList.tsx` — list module (name + source badge + enabled column + "Check for update").
- Create: `frontend/packages/web/components/skills/DiscoverPanel.tsx` — search input + ranked candidate cards + Install button.
- Create: `frontend/packages/web/components/skills/SkillCandidateCard.tsx` — single card (name, canonical_name, source, repo, trust badges, description, Install).
- Modify: `frontend/packages/web/components/layout/WorkspaceNav.tsx` (or the existing workspace-sidebar component) — add "Skills" nav item linking to `/w/[wsId]/skills`.
- Modify: `backend/cubeplex/api/routes/v1/ws_conversations.py` — add the `install <canonical_name>` parser BEFORE the agent-loop kickoff; uses the existing `SkillInstallService` already wired in Task 8.
- Test: `frontend/packages/web/e2e/skills-discover-install.spec.ts` — Playwright smoke.

- [ ] **Step 1: Write the failing Playwright smoke**

```ts
// frontend/packages/web/e2e/skills-discover-install.spec.ts
import { test, expect } from "@playwright/test";
import { loginAsMember } from "./helpers/login";

test("discover → install local skill → appears in workspace list", async ({ page }) => {
  const { wsId } = await loginAsMember(page);

  await page.goto(`/w/${wsId}/skills`);
  await expect(page.getByRole("heading", { name: /Skills/i })).toBeVisible();

  // Discover panel
  await page.getByPlaceholder(/Search skills/i).fill("research");
  await page.getByRole("button", { name: /Search/i }).click();

  const card = page.getByTestId("skill-candidate-card").filter({ hasText: "deep-research" });
  await expect(card).toBeVisible();
  await expect(card.getByText(/preinstalled/i)).toBeVisible();

  await card.getByRole("button", { name: /^Install$/ }).click();
  await expect(page.getByText(/Installed deep-research/i)).toBeVisible();

  // Now in the workspace list
  await expect(
    page.getByTestId("skills-list").getByText("deep-research"),
  ).toBeVisible();
});

test("install remote variant of a same-name skill shows canonical suffix", async ({ page }) => {
  const { wsId } = await loginAsMember(page);
  // Test fixture pre-registers a fake remote source that exposes a "deep-research" skill.
  await page.goto(`/w/${wsId}/skills`);
  await page.getByPlaceholder(/Search skills/i).fill("research");
  await page.getByRole("button", { name: /Search/i }).click();

  const remoteCard = page
    .getByTestId("skill-candidate-card")
    .filter({ hasText: "deep-research" })
    .filter({ hasText: /unvetted/i });
  await expect(remoteCard).toBeVisible();
  await remoteCard.getByRole("button", { name: /^Install$/ }).click();

  // Canonical suffix on the toast + list row (e.g. "acme:deep-research").
  await expect(page.getByText(/Installed .+:deep-research/i)).toBeVisible();
  await expect(
    page.getByTestId("skills-list").getByText(/:deep-research/i),
  ).toBeVisible();
});

test("Check for update no-op on a remote-imported skill", async ({ page }) => {
  const { wsId } = await loginAsMember(page);
  await page.goto(`/w/${wsId}/skills`);
  const row = page.getByTestId("skills-list").getByText(/:deep-research/i);
  await row.click();
  await page.getByRole("button", { name: /Check for update/i }).click();
  await expect(page.getByText(/No new version|Up to date/i)).toBeVisible();
});
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd frontend && pnpm exec playwright test packages/web/e2e/skills-discover-install.spec.ts --reporter=line`
Expected: FAIL — `/w/[wsId]/skills` 404s (page not yet created).

- [ ] **Step 3: `@cubeplex/core` types + API module**

```ts
// frontend/packages/core/src/api/skills.ts
import { apiClient } from "./client";

export interface SkillCandidateOut {
  candidate_id: string;
  name: string;
  canonical_name: string;
  description: string;
  source_kind: "local" | "remote";
  keywords: string[];
  version: string | null;
  trust: "official" | "community" | "untrusted";
  install_state: "enabled" | "in_catalog" | "available";
  stars: number | null;
  install_count: number | null;
  source_name: string;
  repo: string | null;
  unvetted: boolean;
}

export type SkillCandidateListResponse = SkillCandidateOut[];

export interface SkillInstallResponse {
  canonical_name: string;
  skill_id: string;
  installed_version: string;
}

export interface SkillRefreshResponse {
  canonical_name: string;
  skill_id: string;
  installed_version: string;
  changed: boolean;  // false when re-import produced no new version
}

export async function discoverSkills(
  wsId: string,
  q: string,
  limit = 5,
): Promise<SkillCandidateListResponse> {
  const r = await apiClient.get(`/api/v1/ws/${wsId}/skills/discover`, {
    params: { q, limit },
  });
  return r.data;
}

export async function installSkill(
  wsId: string,
  candidateId: string,
): Promise<SkillInstallResponse> {
  const r = await apiClient.post(`/api/v1/ws/${wsId}/skills/install`, {
    candidate_id: candidateId,
  });
  return r.data;
}

export async function refreshSkill(
  wsId: string,
  skillId: string,
): Promise<SkillRefreshResponse> {
  const r = await apiClient.post(`/api/v1/ws/${wsId}/skills/${skillId}/refresh`);
  return r.data;
}
```

```ts
// frontend/packages/core/src/stores/skillsStore.ts
import { create } from "zustand";
import {
  discoverSkills,
  installSkill,
  refreshSkill,
  type SkillCandidateOut,
} from "../api/skills";

interface SkillsState {
  candidates: SkillCandidateOut[];
  query: string;
  installing: Record<string, boolean>;
  lastInstalled: { canonical_name: string; version: string } | null;
  search: (wsId: string, q: string) => Promise<void>;
  install: (wsId: string, candidateId: string) => Promise<void>;
  refresh: (wsId: string, skillId: string) => Promise<boolean>;
  reset: () => void;
}

export const useSkillsStore = create<SkillsState>((set, get) => ({
  candidates: [],
  query: "",
  installing: {},
  lastInstalled: null,
  search: async (wsId, q) => {
    set({ query: q });
    const candidates = await discoverSkills(wsId, q);
    set({ candidates });
  },
  install: async (wsId, candidateId) => {
    set((s) => ({ installing: { ...s.installing, [candidateId]: true } }));
    try {
      const r = await installSkill(wsId, candidateId);
      set((s) => ({
        lastInstalled: { canonical_name: r.canonical_name, version: r.installed_version },
        installing: { ...s.installing, [candidateId]: false },
      }));
    } catch (e) {
      set((s) => ({ installing: { ...s.installing, [candidateId]: false } }));
      throw e;
    }
  },
  refresh: async (wsId, skillId) => {
    const r = await refreshSkill(wsId, skillId);
    return r.changed;
  },
  reset: () => set({ candidates: [], query: "", installing: {}, lastInstalled: null }),
}));
```

Re-export in `frontend/packages/core/src/index.ts`:

```ts
export * from "./api/skills";
export { useSkillsStore } from "./stores/skillsStore";
```

Run: `cd frontend && pnpm build --filter @cubeplex/core`
Expected: clean build, types emitted.

- [ ] **Step 4: Next proxy routes**

```ts
// frontend/packages/web/app/api/v1/ws/[wsId]/skills/discover/route.ts
import { proxyJsonGet } from "@/lib/proxy";
export async function GET(req: Request, { params }: { params: { wsId: string } }) {
  return proxyJsonGet(req, `/api/v1/ws/${params.wsId}/skills/discover`);
}
```

```ts
// frontend/packages/web/app/api/v1/ws/[wsId]/skills/install/route.ts
import { proxyJsonPost } from "@/lib/proxy";
export async function POST(req: Request, { params }: { params: { wsId: string } }) {
  return proxyJsonPost(req, `/api/v1/ws/${params.wsId}/skills/install`);
}
```

```ts
// frontend/packages/web/app/api/v1/ws/[wsId]/skills/[skillId]/refresh/route.ts
import { proxyJsonPost } from "@/lib/proxy";
export async function POST(
  req: Request,
  { params }: { params: { wsId: string; skillId: string } },
) {
  return proxyJsonPost(
    req,
    `/api/v1/ws/${params.wsId}/skills/${params.skillId}/refresh`,
  );
}
```

> Reuse the existing `proxyJsonGet` / `proxyJsonPost` helpers (same pattern as `app/api/v1/ws/[wsId]/conversations/[id]/route.ts`). These are plain JSON request/response — NOT SSE — so no special streaming buffering concerns.

- [ ] **Step 5: Workspace skills page + modules**

```tsx
// frontend/packages/web/app/(app)/w/[wsId]/skills/page.tsx
"use client";
import { useEffect } from "react";
import { useParams } from "next/navigation";
import { SkillsList } from "@/components/skills/SkillsList";
import { DiscoverPanel } from "@/components/skills/DiscoverPanel";

export default function WorkspaceSkillsPage() {
  const { wsId } = useParams<{ wsId: string }>();
  useEffect(() => {
    document.title = "Skills";
  }, []);
  return (
    <div className="flex flex-col gap-6 p-6">
      <h1 className="text-2xl font-semibold">Skills</h1>
      <DiscoverPanel wsId={wsId} />
      <SkillsList wsId={wsId} />
    </div>
  );
}
```

```tsx
// frontend/packages/web/components/skills/SkillCandidateCard.tsx
"use client";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useSkillsStore, type SkillCandidateOut } from "@cubeplex/core";

export function SkillCandidateCard({
  wsId,
  candidate,
}: {
  wsId: string;
  candidate: SkillCandidateOut;
}) {
  const install = useSkillsStore((s) => s.install);
  const installing = useSkillsStore((s) => s.installing[candidate.candidate_id] ?? false);
  return (
    <div data-testid="skill-candidate-card" className="rounded-lg border p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-medium">{candidate.name}</div>
          <div className="text-muted-foreground text-xs">{candidate.canonical_name}</div>
          <p className="mt-1 text-sm">{candidate.description}</p>
          <div className="mt-2 flex flex-wrap gap-1">
            <Badge variant="secondary">{candidate.source_name}</Badge>
            {candidate.unvetted && <Badge variant="destructive">unvetted</Badge>}
            {candidate.repo && (
              <span className="text-muted-foreground text-xs">{candidate.repo}</span>
            )}
          </div>
        </div>
        <Button
          disabled={installing || candidate.install_state === "enabled"}
          onClick={() => install(wsId, candidate.candidate_id)}
        >
          {candidate.install_state === "enabled" ? "Installed" : "Install"}
        </Button>
      </div>
    </div>
  );
}
```

```tsx
// frontend/packages/web/components/skills/DiscoverPanel.tsx
"use client";
import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useSkillsStore } from "@cubeplex/core";
import { SkillCandidateCard } from "./SkillCandidateCard";

export function DiscoverPanel({ wsId }: { wsId: string }) {
  const [q, setQ] = useState("");
  const search = useSkillsStore((s) => s.search);
  const candidates = useSkillsStore((s) => s.candidates);
  const lastInstalled = useSkillsStore((s) => s.lastInstalled);
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Input
          placeholder="Search skills"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="max-w-md"
        />
        <Button onClick={() => search(wsId, q)} disabled={!q.trim()}>
          Search
        </Button>
      </div>
      {lastInstalled && (
        <div className="rounded-md bg-emerald-50 px-3 py-2 text-sm">
          Installed {lastInstalled.canonical_name} (v{lastInstalled.version}). Use in
          conversation with <code>load_skill(&quot;{lastInstalled.canonical_name}&quot;)</code>.
        </div>
      )}
      <div className="grid gap-3">
        {candidates.map((c) => (
          <SkillCandidateCard key={c.candidate_id} wsId={wsId} candidate={c} />
        ))}
      </div>
    </section>
  );
}
```

```tsx
// frontend/packages/web/components/skills/SkillsList.tsx
"use client";
// Renders the workspace-enabled skills. Reuses the EXISTING workspace skills
// endpoint (`GET /api/v1/ws/{ws}/skills?scope=workspace`) — already proxied —
// so this module just lists; per-row "Check for update" calls refreshSkill.
import { useEffect, useState } from "react";
import { useSkillsStore } from "@cubeplex/core";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

interface EnabledSkill {
  id: string;
  name: string;
  source: "preinstalled" | "uploaded";
  source_ref?: string | null;  // remote-imported has this set
}

export function SkillsList({ wsId }: { wsId: string }) {
  const refresh = useSkillsStore((s) => s.refresh);
  const [rows, setRows] = useState<EnabledSkill[]>([]);
  const [updateStatus, setUpdateStatus] = useState<Record<string, string>>({});

  useEffect(() => {
    (async () => {
      const r = await fetch(`/api/v1/ws/${wsId}/skills?scope=workspace`);
      setRows(await r.json());
    })();
  }, [wsId]);

  return (
    <section className="flex flex-col gap-2" data-testid="skills-list">
      <h2 className="text-lg font-medium">Installed in this workspace</h2>
      <ul className="divide-y rounded-lg border">
        {rows.map((s) => (
          <li key={s.id} className="flex items-center justify-between px-4 py-2">
            <div>
              <div className="font-medium">{s.name}</div>
              <div className="flex gap-1">
                <Badge variant="secondary">{s.source}</Badge>
                {s.source_ref && <Badge variant="outline">remote · unvetted</Badge>}
              </div>
            </div>
            {s.source_ref && (
              <div className="flex items-center gap-2">
                {updateStatus[s.id] && (
                  <span className="text-muted-foreground text-xs">{updateStatus[s.id]}</span>
                )}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={async () => {
                    const changed = await refresh(wsId, s.id);
                    setUpdateStatus((u) => ({
                      ...u,
                      [s.id]: changed ? "Updated" : "Up to date",
                    }));
                  }}
                >
                  Check for update
                </Button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
```

- [ ] **Step 6: Workspace nav entry**

In the existing workspace sidebar (e.g. `frontend/packages/web/components/layout/WorkspaceNav.tsx`), append next to "Memory":

```tsx
<NavItem href={`/w/${wsId}/skills`} icon={SkillsIcon}>
  Skills
</NavItem>
```

Pick an icon already in the project's icon set (`Sparkles` / `BookOpen` work). Keep the existing nav ordering: Conversations → Memory → **Skills** → Settings.

- [ ] **Step 7: Chat-fallback parser in the conversation route**

In `backend/cubeplex/api/routes/v1/ws_conversations.py`, in the user-message ingest handler, BEFORE the agent loop kicks off:

```python
import re

_INSTALL_RE = re.compile(r"^install\s+([A-Za-z0-9_\-:]+)\s*$")

async def _maybe_install_from_user_message(
    *,
    session: AsyncSession,
    org: Organization,
    workspace_id: str,
    actor_user_id: str,
    text: str,
) -> str | None:
    """If the user message is `install <canonical_name>`, install it and return
    a replacement system note. Otherwise return None and let the message flow.

    Resolves <canonical_name> against (a) the workspace's catalog (local skills
    not yet installed) and (b) the live candidate set from registered remote
    sources. The same SkillInstallService.install backs both surfaces so the
    UI button and this parser share one code path."""
    m = _INSTALL_RE.match(text.strip())
    if m is None:
        return None
    canonical = m.group(1)
    catalog = SkillCatalogService(session=session, cache=_cache())
    registry = await SkillSourceRegistry.build(
        session=session, catalog=catalog, org_id=org.id,
        org_slug=org.slug, workspace_id=workspace_id,
    )
    cands = await SkillDiscoveryService(registry).discover(canonical, limit=20)
    match = next((c for c in cands if c.canonical_name == canonical), None)
    if match is None:
        return f"Could not find a skill called `{canonical}` in your workspace catalog."
    install = SkillInstallService(
        session=session, registry=registry,
        publisher=SkillPublishService(session=session, cache=_cache()),
        org_id=org.id, org_slug=org.slug,
        workspace_id=workspace_id, actor_user_id=actor_user_id,
    )
    result = await install.install(match.candidate_id)
    return (
        f"Installed `{result.canonical_name}` (v{result.installed_version}). "
        f"Use `load_skill('{result.canonical_name}')` to load it in this conversation."
    )
```

Call `_maybe_install_from_user_message(...)` once per user message; if it returns non-None, persist the result as a system/assistant note in the conversation history and **skip** the agent loop for that turn. If it returns None, behavior is unchanged.

- [ ] **Step 8: Run frontend build + lint + type-check**

Run: `cd frontend && pnpm build --filter @cubeplex/core && pnpm lint --filter @cubeplex/web && pnpm type-check --filter @cubeplex/web`
Expected: all clean.

- [ ] **Step 9: Run the Playwright smoke**

Run: `cd frontend && pnpm exec playwright test packages/web/e2e/skills-discover-install.spec.ts --reporter=line`
Expected: all three tests PASS — discover surfaces candidate, install button works, list refreshes, remote variant lands under `<source>:<slug>`, "Check for update" round-trips.

- [ ] **Step 10: Backend chat-fallback unit + E2E**

Add `tests/e2e/test_chat_install_fallback.py`:

```python
import pytest

@pytest.mark.asyncio
async def test_user_message_install_command_installs_skill_and_replaces_message(
    member_client_with_session,
):
    client, ws_id, session, _org_id = member_client_with_session
    convo = await client.post(f"/api/v1/ws/{ws_id}/conversations", json={"title": "x"})
    cid = convo.json()["id"]
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations/{cid}/messages",
        json={"role": "user", "content": "install deep-research"},
    )
    assert resp.status_code in (200, 201)
    # The agent loop was skipped; the conversation now contains an
    # install-result system/assistant note (not the original "install ..." text
    # nor any agent response).
    msgs = (await client.get(
        f"/api/v1/ws/{ws_id}/conversations/{cid}/messages"
    )).json()
    assert any("Installed `deep-research`" in m.get("content", "") for m in msgs)
```

Run: `cd backend && uv run pytest tests/e2e/test_chat_install_fallback.py -q`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add frontend/packages/core/src/api/skills.ts frontend/packages/core/src/stores/skillsStore.ts frontend/packages/core/src/index.ts
git add frontend/packages/web/app/api/v1/ws/\[wsId\]/skills/ frontend/packages/web/app/\(app\)/w/\[wsId\]/skills/
git add frontend/packages/web/components/skills/ frontend/packages/web/components/layout/WorkspaceNav.tsx
git add frontend/packages/web/e2e/skills-discover-install.spec.ts
git add backend/cubeplex/api/routes/v1/ws_conversations.py backend/tests/e2e/test_chat_install_fallback.py
git commit -m "feat(skills): workspace skills page + chat-install fallback parser"
```

---

## Task 15: Pre-PR sweep

**Files:** none (verification only).

- [ ] **Step 1: Full changed-area backend test run**

Run: `cd backend && uv run pytest tests/unit/test_skill_candidate_id.py tests/unit/test_skill_discovery_ranking.py tests/unit/test_remote_registry_source.py tests/e2e/test_skill_discovery_local.py tests/e2e/test_skill_discovery_remote.py tests/e2e/test_skill_sources_admin.py tests/e2e/test_find_skills_tool.py tests/e2e/test_chat_install_fallback.py tests/e2e/test_skills_marketplace.py tests/e2e/memory/test_prompt_cache.py -q`
Expected: all PASS. `test_prompt_cache.py` is included because Task 9 adds `find_skills` to the builtin-tool list, changing the cached tool/prompt prefix — this guards against a cache-prefix regression (see `backend/docs/prompt-cache-discipline.md`); the tool MUST be appended after `load_skill` to keep the prefix stable.

- [ ] **Step 2: Backend type + lint across new + touched modules**

Run: `cd backend && uv run mypy cubeplex/skills cubeplex/tools/builtin/find_skills.py cubeplex/api/routes/v1/admin_skill_sources.py cubeplex/api/routes/v1/ws_skills.py cubeplex/api/routes/v1/ws_conversations.py cubeplex/repositories/skill_source.py cubeplex/models/skill_source.py cubeplex/streams/run_manager.py && uv run ruff check cubeplex/`
Expected: no issues.

- [ ] **Step 3: Frontend build + lint + type-check + Playwright**

Run: `cd frontend && pnpm build --filter @cubeplex/core && pnpm lint && pnpm type-check && pnpm exec playwright test packages/web/e2e/skills-discover-install.spec.ts --reporter=line`
Expected: clean across the board; Playwright smoke green.

- [ ] **Step 4: Migration sanity (no drift)**

Run: `cd backend && uv run alembic upgrade head && uv run alembic check`
Expected: head applied; `alembic check` reports no new pending autogenerate diff.

---

## Self-Review Checklist (completed by plan author)

- **Spec coverage:**
  - §1 `SkillSource` interface + `LocalCatalogSource` (Task 3, scoped to `list_visible_for_org`) + `RemoteRegistrySource` (Task 4) + config-driven `SkillSourceRegistry`/`SkillSource` table (Tasks 2, 5). HMAC-signed `candidate_id` codec, no path routing (Task 1; OQ-7 resolved); `canonical_name` carried on every candidate and used by install/load, never display `name` (Tasks 1, 3, 4, 7, 9).
  - §2 read-only `find_skills` tool returning `{candidate_id, name, canonical_name, description, source, trust, install_state}` descriptions-only; `load_skill(canonical_name)` hint for enabled (Task 9).
  - §3 preview → user-confirmed install via two surfaces (OQ-5 resolved): authenticated POST = the UI Install button (Tasks 7, 8, 10, 12, 14) AND the chat-fallback `install <canonical_name>` parser in the conversation route (Task 14, Step 7). Remote import via `_publish_from_files` minting `<org-slug>:<skill-slug>`; install returns canonical name; same-conversation loadability verified.
  - §4 default workspace-private scope (OQ-3); trust tier + `unvetted` flag surfaced in candidate, preview, and UI banner; admin-only source management; files stored not executed, subject to existing sandbox + #144 command_rules (OQ-2) (Tasks 8, 11, 12, 14).
  - §6 scope-isolated routes — member `/ws/.../skills/{discover,discover/preview,install,{skill_id}/refresh}` (`candidate_id` in query/body, never path) vs admin `/admin/skill-sources/`; shared logic in services only (Tasks 8, 11, 14).
  - §7 v1 scope matches Tasks 1–14: backend (1–12) + workspace skills page + chat fallback (14). Trust *enforcement* deferred to a future security/gating module (Task 13 is a note, no code) per OQ-1 resolution; semantic search, personal scope, auto-update polling, and agent-initiated install via HITL stay deferred per Future Work.
- **Type consistency:** `SkillCandidate` fields are identical wherever constructed (base, local, remote, ranking test). `InstallResult.canonical_name` ↔ `InstallCandidateResponse.canonical_name` ↔ test assertions. `decode_candidate_id` returns the 3-tuple `(kind, source_id, source_ref)`, unpacked identically in install + preview, and both resolve the remote source via `registry.remote_source_by_id(source_id)` (never "first remote"). `SkillSourceRegistry.build(...)` signature identical across run_manager + all three routes.
- **Reuse, not re-route:** install reuses `OrgSkillInstallRepository.create_for_workspace` (local) and `SkillPublishService._publish_from_files` (remote) — the exact existing publish path; load reuses `find_enabled_by_name` + `SkillsMiddleware` untouched.
- **Resolved against the real repo:** `require_member` (`cubeplex.auth.dependencies`) and `get_admin_request_context` (`cubeplex.mcp.dependencies`) are the existing member/admin deps; e2e clients yield `(client, workspace_id)` / `(client, _)` tuples per `test_skills_marketplace.py` / `test_skills_artifact_flow.py`; `_publish_from_files` already accepts `workspace_id`; `list_visible_for_org` already excludes deprecated + scopes to own-org uploaded + preinstalled.

---

## Open follow-ups (out of this plan)

See spec Future Work for the canonical list. The major items deferred past this PR:

- **Source allowlist + content-scan + admin approval queue** — future dedicated security/gating module (spec OQ-1 resolution).
- **Personal scope** — lands with #153 managed agents user-pinned definitions (spec OQ-3).
- **Embedding-based semantic search** (spec OQ-4).
- **Automated update polling** beyond the v1 manual "Check for update" button (spec OQ-6).
- **Agent-initiated install via HITL** — gated on cubepi shipping HITL first (extends OQ-5).
