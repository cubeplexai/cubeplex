# Provider `slug` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every provider a unique, stable `slug` and key model refs (`default_model`/`fallback_models`/`task_models`) on `slug/model-id` instead of the fragile display-name-based ref.

**Architecture:** Add a `slug` column to `providers` (unique per `org_id`), derive it from `name` at create (overridable, immutable after), and switch the LLM resolver's merged-config map to be keyed by slug. A single Alembic revision backfills slugs and rewrites existing OrgSettings refs name→slug (clean cutover). Frontend builds refs from `provider.slug` and surfaces the slug in the provider UI.

**Tech Stack:** FastAPI + SQLModel + Alembic + Postgres (backend); Next.js/React + `@cubeplex/core` (frontend). Spec: `docs/dev/specs/2026-05-20-provider-slug-design.md`.

**Worktree:** `/home/chris/cubeplex/.worktrees/feat/model-mgmt-followup` (branch `feat/model-mgmt-followup`). Run `cat .worktree.env` first; backend port 8015, DB `cubeplex_feat_model_mgmt_followup`. Backend commands: `cd backend && uv run …`. Tests that hit the DB run against the worktree DB (already migrated to the M5 head).

---

## File map

- **Create** `backend/cubeplex/utils/slug.py` — `slugify(name)` pure helper.
- **Create** `backend/tests/unit/test_slug.py` — slugify unit tests.
- **Modify** `backend/cubeplex/models/provider.py` — add `slug` column + `(org_id, slug)` unique constraint.
- **Create** `backend/alembic/versions/<rev>_add_provider_slug.py` — column + backfill + constraint + OrgSettings ref rewrite.
- **Modify** `backend/cubeplex/api/schemas/provider.py` — `ProviderCreate.slug` optional; `ProviderOut.slug`.
- **Modify** `backend/cubeplex/repositories/provider.py` — `get_by_slug`.
- **Modify** `backend/cubeplex/services/provider_service.py` — derive/validate slug in `create_provider`; add `ProviderSlugConflictError`.
- **Modify** `backend/cubeplex/api/routes/v1/admin_providers.py` — map the new error to 409; emit `slug` in `_provider_out`.
- **Modify** `backend/cubeplex/seeders/provider_seeder.py` — set `slug = slugify(name)` on seed create + update.
- **Modify** `backend/cubeplex/llm/factory.py` — key merged config by slug; rename `_parse_model_ref` return.
- **Modify** `backend/cubeplex/services/task_model_resolver.py` — variable rename (provider_name → slug).
- **Modify** `backend/cubeplex/services/provider_service.py` — also `_validate_model_ref` (settings-save write path) → slug + `get_by_slug`.
- **Modify** `backend/cubeplex/llm/runtime_writeback.py` — resolve provider by slug; rename `provider_name` → `provider_slug` through the public API.
- **Modify** `backend/cubeplex/services/conversation_title.py` + `backend/cubeplex/streams/run_manager.py` — writeback call sites pass `provider_slug`.
- **Modify** `frontend/packages/core/src/types/provider.ts` — `Provider.slug`.
- **Modify** `frontend/packages/web/hooks/useAllModels.ts` — `ref = ${slug}/${modelId}` + `providerSlug`.
- **Modify** `frontend/packages/web/components/admin/models/ProviderConfigForm.tsx` — editable slug field (create) / read-only (edit).
- **Modify** `frontend/packages/web/components/admin/models/ProviderDetail.tsx` — show slug.
- **Modify** `backend/tests/e2e/test_admin_providers_crud.py` — slug round-trip + uniqueness + ref resolution e2e.

---

## Task 1: `slugify` helper

**Files:**
- Create: `backend/cubeplex/utils/slug.py`
- Test: `backend/tests/unit/test_slug.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_slug.py
import pytest

from cubeplex.utils.slug import slugify


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("DeepSeek", "deepseek"),
        ("DeepSeek (Anthropic shape)", "deepseek-anthropic-shape"),
        ("  Open AI  ", "open-ai"),
        ("GPT-4o", "gpt-4o"),
        ("a__b--c", "a-b-c"),
        ("智谱 GLM", "glm"),  # non-ascii stripped, ascii kept
        ("!!!", "provider"),  # all-punctuation fallback
        ("", "provider"),
    ],
)
def test_slugify(name: str, expected: str) -> None:
    assert slugify(name) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_slug.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cubeplex.utils.slug'`

- [ ] **Step 3: Implement**

```python
# backend/cubeplex/utils/slug.py
"""Slugify provider names into stable, URL-safe identifiers."""

from __future__ import annotations

import re

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Lowercase, keep ascii alnum, collapse other runs to a single hyphen.

    Non-ascii characters are dropped (they map to nothing). An empty result
    (e.g. the name was all punctuation or non-ascii) falls back to ``provider``.
    """
    lowered = name.lower()
    collapsed = _NON_SLUG.sub("-", lowered).strip("-")
    return collapsed or "provider"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_slug.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/utils/slug.py backend/tests/unit/test_slug.py
git commit -m "feat(provider): add slugify helper"
```

---

## Task 2: `Provider.slug` column + uniqueness constraint (model)

**Files:**
- Modify: `backend/cubeplex/models/provider.py:18,23`

This task adds the field to the SQLModel only. The DB migration is Task 3; do not run the app against an un-migrated DB between these tasks.

- [ ] **Step 1: Add the column + constraints**

A single `UniqueConstraint("org_id", "slug")` does **not** enforce uniqueness for
system providers (Postgres treats `NULL` org_ids as distinct). Mirror the
credential-vault pattern (`models/credential.py`): two **partial unique
indexes** — one for the org bucket, one for the system bucket. Add `Index` to
the SQLAlchemy import.

In `backend/cubeplex/models/provider.py`:

```python
from sqlalchemy import Column, Index, UniqueConstraint  # add Index
```

```python
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_provider_org_name"),
        Index(
            "uq_provider_org_slug",
            "org_id",
            "slug",
            unique=True,
            postgresql_where="org_id IS NOT NULL",
        ),
        Index(
            "uq_provider_system_slug",
            "slug",
            unique=True,
            postgresql_where="org_id IS NULL",
        ),
    )

    org_id: str | None = Field(
        default=None, foreign_key="organizations.id", max_length=20, index=True
    )
    name: str = Field(max_length=64)
    slug: str = Field(max_length=64, index=True)
```

(`slug` is non-optional in the model; the migration backfills before adding NOT NULL, so existing rows are covered.)

- [ ] **Step 2: Verify it imports**

Run: `cd backend && uv run python -c "from cubeplex.models.provider import Provider; print('slug' in Provider.model_fields)"`
Expected: `True`

- [ ] **Step 3: Update existing direct `Provider(...)` test fixtures**

`slug` is now a required field, so every test that constructs `Provider(...)`
directly (not via the API/service) must pass `slug=`. Find them and add a
deterministic slug (e.g. `slug="acme"`, or `slug=slugify(name)`):

Run: `cd backend && grep -rln "Provider(" tests/` — known offenders include
`tests/e2e/test_provider_vault.py`, `tests/e2e/test_provider_runtime_writeback_e2e.py`,
`tests/e2e/test_title_model_routing_e2e.py`, `tests/unit/test_seed_idempotent.py`,
`tests/unit/test_provider_service_invariants.py`. Add `slug=` to each `Provider(...)`
call. (Skip files where `Provider(` is an unrelated symbol — verify it's the model.)

- [ ] **Step 4: Verify fixtures construct**

Run: `cd backend && set -a && source ../.worktree.env && set +a && uv run pytest tests/unit/test_provider_service_invariants.py tests/e2e/test_provider_vault.py -q`
Expected: PASS (no "field required: slug" construction errors).

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/models/provider.py backend/tests/
git commit -m "feat(provider): add slug column to Provider model; update fixtures"
```

---

## Task 3: Alembic migration — column, backfill, constraint, ref rewrite

**Files:**
- Create: `backend/alembic/versions/<autogen-rev>_add_provider_slug.py`

The migration does four things in `upgrade()`: (1) add `slug` nullable, (2) backfill `slug = slugify(name)` with per-`org_id` collision suffixing, (3) make `slug` NOT NULL + add the `(org_id, slug)` unique constraint, (4) rewrite OrgSettings refs name→slug per org.

- [ ] **Step 1: Generate the migration skeleton**

Run: `cd backend && set -a && source ../.worktree.env && set +a && uv run alembic revision --autogenerate -m "add provider slug"`
Expected: a new file under `backend/alembic/versions/`. Because Task 2 put the two partial indexes in `__table_args__`, autogenerate will likely emit `op.add_column(...)` **and** `op.create_index(...)` ops for them. **Do not keep the autogenerated body** — Step 2 below is the single source of truth for `upgrade()`/`downgrade()`. Keep only the generated `revision` / `down_revision` identifiers, and ensure each schema op appears exactly once (the column add, the two partial indexes, the `ix_providers_slug` index) — no duplicates.

- [ ] **Step 2: Replace `upgrade()`/`down_revision` body with the full data-aware migration**

Edit the generated file so `upgrade()` reads (keep the generated `revision`/`down_revision` values):

```python
import json
import re
from collections import defaultdict

import sqlalchemy as sa
from alembic import op

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    collapsed = _NON_SLUG.sub("-", name.lower()).strip("-")
    return collapsed or "provider"


def upgrade() -> None:
    bind = op.get_bind()

    # 1. add nullable column (autogenerated op kept here)
    op.add_column("providers", sa.Column("slug", sa.String(length=64), nullable=True))

    # 2. backfill slug. System providers (org_id IS NULL) first so org providers
    #    can also dedup against system slugs — an org provider must NOT reuse a
    #    slug that's visible system-wide, or resolution within that org would be
    #    ambiguous. Two orgs may still share a slug (separate resolution contexts).
    rows = bind.execute(
        sa.text(
            "SELECT id, org_id, name FROM providers "
            "ORDER BY (org_id IS NOT NULL), created_at, id"  # NULL (system) first
        )
    ).fetchall()
    upd = sa.text("UPDATE providers SET slug = :slug WHERE id = :id")
    system_slugs: set[str] = set()
    org_used: dict[str, set[str]] = defaultdict(set)

    def _assign(base: str, taken: set[str]) -> str:
        base = base or "provider"
        n = 1
        while True:
            suffix = "" if n == 1 else f"-{n}"
            candidate = base[: 64 - len(suffix)] + suffix  # always fits the 64-char column
            if candidate not in taken:
                return candidate
            n += 1

    for row in rows:
        base = _slugify(row.name)
        if row.org_id is None:
            slug = _assign(base, system_slugs)
            system_slugs.add(slug)
        else:
            slug = _assign(base, org_used[row.org_id] | system_slugs)
            org_used[row.org_id].add(slug)
        bind.execute(upd, {"slug": slug, "id": row.id})

    # 3. NOT NULL + index + partial unique indexes (org bucket + system bucket).
    #    Two partial indexes (not one composite constraint) so the org_id=NULL
    #    system bucket is also uniquely constrained. Mirrors models/credential.py.
    op.alter_column("providers", "slug", existing_type=sa.String(length=64), nullable=False)
    op.create_index("ix_providers_slug", "providers", ["slug"])
    op.create_index(
        "uq_provider_org_slug", "providers", ["org_id", "slug"],
        unique=True, postgresql_where=sa.text("org_id IS NOT NULL"),
    )
    op.create_index(
        "uq_provider_system_slug", "providers", ["slug"],
        unique=True, postgresql_where=sa.text("org_id IS NULL"),
    )

    # 4. rewrite OrgSettings refs name->slug, per org
    #    refs can point at system providers (org_id NULL) too, so the name->slug
    #    map for an org includes that org's providers AND the system bucket.
    prov_rows = bind.execute(sa.text("SELECT org_id, name, slug FROM providers")).fetchall()
    system_map: dict[str, str] = {}
    org_maps: dict[str, dict[str, str]] = defaultdict(dict)
    for r in prov_rows:
        if r.org_id is None:
            system_map[r.name] = r.slug
        else:
            org_maps[r.org_id][r.name] = r.slug

    def _rewrite_ref(ref: str, name_to_slug: dict[str, str]) -> str:
        parts = ref.split("/", 1)
        if len(parts) != 2:
            return ref
        provider_name, model_id = parts
        slug = name_to_slug.get(provider_name)
        return f"{slug}/{model_id}" if slug else ref

    settings_rows = bind.execute(
        sa.text(
            "SELECT org_id, key, value FROM org_settings "
            "WHERE key IN ('default_model', 'fallback_models', 'task_models')"
        )
    ).fetchall()
    set_value = sa.text(
        "UPDATE org_settings SET value = :value WHERE org_id = :org_id AND key = :key"
    )
    for s in settings_rows:
        name_to_slug = {**system_map, **org_maps.get(s.org_id, {})}
        value = s.value if isinstance(s.value, dict) else json.loads(s.value)
        if s.key == "default_model" and value.get("model_ref"):
            value = {**value, "model_ref": _rewrite_ref(str(value["model_ref"]), name_to_slug)}
        elif s.key == "fallback_models" and value.get("models"):
            value = {
                **value,
                "models": [_rewrite_ref(str(m), name_to_slug) for m in value["models"]],
            }
        elif s.key == "task_models":
            value = {
                **value,
                **{
                    task: _rewrite_ref(str(ref), name_to_slug)
                    for task, ref in value.items()
                    if isinstance(ref, str)
                },
            }
        else:
            continue
        bind.execute(set_value, {"value": json.dumps(value), "org_id": s.org_id, "key": s.key})


def downgrade() -> None:
    op.drop_index("uq_provider_system_slug", table_name="providers")
    op.drop_index("uq_provider_org_slug", table_name="providers")
    op.drop_index("ix_providers_slug", table_name="providers")
    op.drop_column("providers", "slug")
```

> Note: `org_settings.value` is a JSON column; depending on the driver `bind.execute` returns it as a `dict` already (handled by the `isinstance` guard).
>
> **Cross-bucket rule (revised after codex round 2):** within a single org's
> resolution context (its providers + the system bucket), slugs must be unique —
> otherwise `get_by_slug` and the merged config would be ambiguous. The DB
> partial indexes only enforce per-bucket uniqueness, so app-level code enforces
> the stronger rule: the migration backfill dedups org slugs against the system
> set (above), and `get_by_slug` (Task 4) spans both buckets so the create path
> rejects an org slug that collides with a system slug. Two *different* orgs may
> still share a slug — they never share a resolution context.

- [ ] **Step 3: Apply the migration to the worktree DB**

Run: `cd backend && set -a && source ../.worktree.env && set +a && uv run alembic upgrade head`
Expected: runs the new revision with no error.

- [ ] **Step 4: Verify the column + a sample slug**

The worktree env exposes `CUBEPLEX_DATABASE__NAME` (not a full URL). Query via the
shared docker Postgres:

Run: `source backend/../.worktree.env 2>/dev/null; docker exec -e PGPASSWORD=postgres infra-postgresql psql -h localhost -U postgres -d "$CUBEPLEX_DATABASE__NAME" -c "SELECT name, slug, org_id FROM providers ORDER BY name;"`
Expected: every row has a non-null slug; `DeepSeek (Anthropic shape)` → `deepseek-anthropic-shape`; `deepseek` → `deepseek`.

- [ ] **Step 5: Migration data test (fail-first risk coverage)**

Mirror the harness in `backend/tests/e2e/test_migration.py` (it stamps a prior
revision, seeds rows, runs `alembic upgrade`, asserts). Add a test that seeds:
a system provider named `DeepSeek` and an org provider also named `DeepSeek` in
some org; plus that org's `OrgSettings` rows `default_model={"model_ref":
"DeepSeek/m-1"}`, `fallback_models={"models": ["DeepSeek/m-2"]}`,
`task_models={"title": "DeepSeek/m-1"}`. After running this revision's upgrade,
assert: the system provider's slug is `deepseek`, the org provider's slug is
`deepseek-2` (deduped against the system slug), and the three OrgSettings refs
were rewritten to the org provider's slug (`deepseek-2/m-1`, etc.) since the
name `DeepSeek` maps to the org provider within that org's name→slug map. If the
repo's migration harness can't easily stamp+seed+upgrade, assert the same end
state by extracting the `_assign` and ref-rewrite logic into module-level pure
functions in the migration and unit-testing those directly.

Run: `cd backend && set -a && source ../.worktree.env && set +a && uv run pytest tests/e2e/test_migration.py -k slug -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/ backend/tests/e2e/test_migration.py
git commit -m "feat(provider): migration — add slug, backfill, rewrite OrgSettings refs name->slug"
```

---

## Task 4: API — derive/validate slug on create, expose on read

**Files:**
- Modify: `backend/cubeplex/api/schemas/provider.py:13-25` (ProviderCreate) and ProviderOut block (after `name`)
- Modify: `backend/cubeplex/repositories/provider.py` (add `get_by_slug`)
- Modify: `backend/cubeplex/services/provider_service.py` (`create_provider`, new error class)
- Modify: `backend/cubeplex/api/routes/v1/admin_providers.py` (`_provider_out`, error→409)
- Test: `backend/tests/e2e/test_admin_providers_crud.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/e2e/test_admin_providers_crud.py` (follow the file's existing async client fixture/style — adapt `client`/auth helpers to match the file):

```python
async def test_create_provider_derives_slug_from_name(admin_client) -> None:
    body = {"name": "My DeepSeek", "provider_type": "openai-completions",
            "base_url": "https://x.test/v1", "auth_type": "api_key", "api_key": "k"}
    r = await admin_client.post("/api/v1/admin/providers", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["slug"] == "my-deepseek"


async def test_create_provider_explicit_slug_and_conflict(admin_client) -> None:
    base = {"provider_type": "openai-completions", "base_url": "https://x.test/v1",
            "auth_type": "api_key", "api_key": "k"}
    r1 = await admin_client.post("/api/v1/admin/providers", json={**base, "name": "A", "slug": "shared"})
    assert r1.status_code == 201
    assert r1.json()["slug"] == "shared"
    r2 = await admin_client.post("/api/v1/admin/providers", json={**base, "name": "B", "slug": "shared"})
    assert r2.status_code == 409


async def test_create_provider_auto_slug_suffixes_on_collision(admin_client) -> None:
    base = {"provider_type": "openai-completions", "base_url": "https://x.test/v1",
            "auth_type": "api_key", "api_key": "k"}
    r1 = await admin_client.post("/api/v1/admin/providers", json={**base, "name": "Dup Name"})
    r2 = await admin_client.post("/api/v1/admin/providers", json={**base, "name": "Dup  Name"})
    assert {r1.json()["slug"], r2.json()["slug"]} == {"dup-name", "dup-name-2"}


@pytest.mark.parametrize("bad", ["Has Space", "UPPER", "trailing-", "has/slash", ""])
async def test_create_provider_rejects_malformed_explicit_slug(admin_client, bad) -> None:
    body = {"name": "Whatever", "provider_type": "openai-completions",
            "base_url": "https://x.test/v1", "auth_type": "api_key", "api_key": "k", "slug": bad}
    r = await admin_client.post("/api/v1/admin/providers", json=body)
    assert r.status_code == 422
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && set -a && source ../.worktree.env && set +a && uv run pytest tests/e2e/test_admin_providers_crud.py -k slug -q`
Expected: FAIL (slug not in response / no 409).

- [ ] **Step 3: Schemas — add slug**

In `backend/cubeplex/api/schemas/provider.py`, add to `ProviderCreate` (after `name`):

```python
    slug: str | None = Field(default=None, max_length=64)
```

Add to `ProviderOut` (after `name`):

```python
    slug: str
```

(Do **not** add `slug` to `ProviderUpdate` — immutable.)

- [ ] **Step 4: Repo — `get_by_slug`**

In `backend/cubeplex/repositories/provider.py`, add (mirror `get_by_name`):

```python
    async def get_by_slug(self, slug: str) -> Provider | None:
        # Spans the org bucket + the system (org_id NULL) bucket. App-level
        # uniqueness keeps these from colliding, but order org-scoped first and
        # take one row defensively (the DB partial indexes don't forbid a cross-
        # bucket duplicate, so never let this raise MultipleResultsFound).
        stmt = (
            select(Provider)
            .where(
                (Provider.org_id.is_(None))  # type: ignore[union-attr]
                | (Provider.org_id == self.org_id)
            )
            .where(Provider.slug == slug)  # type: ignore[arg-type]
            .order_by(Provider.org_id.is_(None))  # org-scoped (False) before system (True)
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()
```

- [ ] **Step 5: Service — derive + validate slug, new error**

In `backend/cubeplex/services/provider_service.py`: add the error class near the other provider errors:

```python
class ProviderSlugConflictError(Exception):
    """Raised when a provider slug already exists in the org."""
```

Add a private helper and use it in `create_provider` (insert after the existing name-conflict check, before building the `Provider`):

```python
    import re
    from cubeplex.utils.slug import slugify  # add to module imports at top

    _SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")  # module-level constant


class InvalidProviderSlugError(Exception):
    """Raised when an explicitly-provided slug is malformed."""
```

```python
    async def _resolve_slug(self, name: str, explicit: str | None) -> str:
        if explicit is not None:
            if not _SLUG_RE.match(explicit) or len(explicit) > 64:
                raise InvalidProviderSlugError(
                    "slug must match ^[a-z0-9]+(-[a-z0-9]+)*$ and be <= 64 chars"
                )
            if await self._providers.get_by_slug(explicit) is not None:
                raise ProviderSlugConflictError(f"Provider slug '{explicit}' already exists")
            return explicit
        base = slugify(name)
        n = 1
        while True:
            suffix = "" if n == 1 else f"-{n}"
            candidate = base[: 64 - len(suffix)] + suffix  # always fits the 64-char column
            if await self._providers.get_by_slug(candidate) is None:
                return candidate
            n += 1
```

In `create_provider`, after the name-conflict check, add:

```python
        slug = await self._resolve_slug(data.name, data.slug)
```

and pass `slug=slug` into the `Provider(...)` constructor.

- [ ] **Step 6: Route — emit slug, map error to 409**

In `backend/cubeplex/api/routes/v1/admin_providers.py`:
- In `_provider_out`, add `slug=p.slug,` to the `ProviderOut(...)` construction.
- Import `ProviderSlugConflictError` and `InvalidProviderSlugError` from `provider_service` (add to the existing import block). In the POST `/providers` handler, mirror the existing `ProviderNameConflictError` 409 mapping — the detail is an **object**, not a string:

```python
    except ProviderSlugConflictError as e:
        raise HTTPException(status_code=409, detail={"code": "provider_slug_conflict"}) from e
    except InvalidProviderSlugError as e:
        raise HTTPException(status_code=422, detail={"code": "invalid_provider_slug"}) from e
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend && set -a && source ../.worktree.env && set +a && uv run pytest tests/e2e/test_admin_providers_crud.py -k slug -q && uv run mypy cubeplex/`
Expected: PASS + mypy clean.

- [ ] **Step 8: Commit**

```bash
git add backend/cubeplex/api/schemas/provider.py backend/cubeplex/repositories/provider.py backend/cubeplex/services/provider_service.py backend/cubeplex/api/routes/v1/admin_providers.py backend/tests/e2e/test_admin_providers_crud.py
git commit -m "feat(provider): derive+validate slug on create, expose slug in ProviderOut"
```

---

## Task 5: Seeder sets slug

**Files:**
- Modify: `backend/cubeplex/seeders/provider_seeder.py`

The seeder creates/updates system providers from config. Set `slug = slugify(name)` on the create branch (and on the update branch if a row predates this change and has no slug — though the migration already backfilled, so update-branch is belt-and-suspenders for fresh DBs seeded before migration; set it when missing).

- [ ] **Step 1: Add the import**

At the top of `provider_seeder.py`:

```python
from cubeplex.utils.slug import slugify
```

- [ ] **Step 2: Set slug on create**

In the `if provider is None:` branch, add `slug=slugify(name),` to the `Provider(...)` constructor.

- [ ] **Step 3: Backfill slug on the update branch**

In the `else:` branch (existing provider), add:

```python
            if not getattr(provider, "slug", None):
                provider.slug = slugify(name)
```

- [ ] **Step 4: Verify seeding (idempotency test)**

Run: `cd backend && set -a && source ../.worktree.env && set +a && uv run pytest -k "seed" -q`
Expected: PASS (existing seeder tests still green; every seeded provider now has a slug).

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/seeders/provider_seeder.py
git commit -m "feat(provider): seeder stores slug=slugify(name)"
```

---

## Task 6: Resolve & validate model refs by slug (everywhere)

This task switches **every** model-ref consumer from name to slug — read path
(resolver), write-path validation (settings save), and runtime writeback. Missing
any one leaves a half-cutover where refs silently fail.

**Files:**
- Modify: `backend/cubeplex/llm/factory.py` — `_load_db_provider_configs` (key by slug), `_build_merged_config` (key by slug), `_parse_model_ref` (docstring)
- Modify: `backend/cubeplex/services/task_model_resolver.py:30-34` (var rename)
- Modify: `backend/cubeplex/services/provider_service.py:556-562` — `_validate_model_ref` uses slug + `get_by_slug` (write path: saving `default_model`/`fallback_models`/`task_models`)
- Modify: `backend/cubeplex/llm/runtime_writeback.py` — resolve provider by slug; rename `provider_name` → `provider_slug` through the public API
- Modify: `backend/cubeplex/services/conversation_title.py` + `backend/cubeplex/streams/run_manager.py` — writeback call sites pass `provider_slug=...`
- Test: `backend/tests/` (factory resolver test — add to the existing factory test module if present, else create `backend/tests/unit/test_factory_slug_resolve.py`)

- [ ] **Step 1: Write a failing resolver test**

Create `backend/tests/unit/test_factory_slug_resolve.py` (adapt construction to the factory's real test fixtures — if a factory test file already exists, add this case there instead):

```python
import pytest

from cubeplex.llm.factory import LLMFactory


def test_parse_model_ref_returns_slug_and_model() -> None:
    slug, model_id = LLMFactory._parse_model_ref("my-deepseek/deepseek-v4-pro")
    assert slug == "my-deepseek"
    assert model_id == "deepseek-v4-pro"
```

- [ ] **Step 1b: Add a DB-backed resolution test (name ≠ slug)**

The parse test alone passes before the keying change, so add a DB-backed test
that *fails first* against the current name-keyed resolver. Mirror
`backend/tests/unit/test_llm_factory_cubepi.py` / `tests/unit/test_task_model_resolver.py`
(they build an `LLMFactory` over a session with seeded providers). Seed a provider
with **name `Routed Provider`, slug `routed-provider`** and an enabled model `m-1`,
set the org `default_model` to `routed-provider/m-1`, and assert
`await factory.resolve_default_provider_and_config()` returns that provider/model.
Add a second assertion that a `default_model` of `Routed Provider/m-1` (the old
name-based ref) no longer resolves to it. This test FAILS before Step 3-4 (the
merged map is still name-keyed) and PASSES after.

- [ ] **Step 2: Run to verify it passes already (parse is unchanged) — then make the keying change**

Run: `cd backend && uv run pytest tests/unit/test_factory_slug_resolve.py -q`
Expected: PASS (parse logic is identical; only its meaning changes). This test guards the contract.

- [ ] **Step 3: Key `db_configs` by slug**

In `factory.py` `_load_db_provider_configs` (the method that returns
`(db_configs, db_names)`), change the dict key from name to slug, and rename the returned set:

```python
            db_configs[p.slug] = {
                # ... unchanged value dict ...
            }
        # All slugs (incl. disabled) so config.yaml providers that exist in DB are skipped.
        all_rows_stmt = select(DBP).where(
            (DBP.org_id == None) | (DBP.org_id == self._org_id),  # type: ignore[arg-type]  # noqa: E711
        )
        all_rows = (await self._session.execute(all_rows_stmt)).scalars().all()
        db_slugs = {p.slug for p in all_rows}
        return db_configs, db_slugs
```

Update the method's docstring/return-type comment from "set of ALL provider names" to "set of ALL provider slugs".

- [ ] **Step 4: Key merged config by slug**

In `_build_merged_config`, change the signature param name and the keying:

```python
    def _build_merged_config(
        self, db_configs: dict[str, dict[str, Any]], db_slugs: set[str]
    ) -> LLMConfig:
        from cubeplex.utils.slug import slugify

        config_providers = dict(self.llm_config.providers)
        db_slugs_lower = {s.lower() for s in db_slugs}
        merged: dict[str, ProviderConfig] = {}
        for name, cfg in config_providers.items():
            slug = slugify(name)
            if slug.lower() not in db_slugs_lower:  # skip config provider already in DB
                merged[slug] = cfg
        for slug, db_cfg in db_configs.items():
            merged[slug] = ProviderConfig(**db_cfg)  # DB always overrides
        return LLMConfig(
            # ... unchanged other fields, providers=merged ...
        )
```

(Keep all other `LLMConfig(...)` fields exactly as they are; only the `providers=merged` dict keying changed. Verify the caller passes the renamed `db_slugs`.)

- [ ] **Step 5: Update `_parse_model_ref` docstring + task resolver var names**

In `factory.py` `_parse_model_ref`, update the docstring to say it returns `(slug, model_id)` (logic unchanged). In `get_default_model` update the return doc to `(slug, model_id)`.

In `task_model_resolver.py` `_resolve_ref`, rename `provider_name` → `slug`:

```python
    def _resolve_ref(merged: LLMConfig, model_ref: str) -> tuple[str, str, ProviderConfig]:
        slug, model_id = factory._parse_model_ref(model_ref)
        provider_config = merged.providers.get(slug)
        if provider_config is None:
            raise ValueError(f"Task '{task}' provider '{slug}' not found in merged config")
        return slug, model_id, provider_config
```

- [ ] **Step 5b: Write-path — validate saved settings refs by slug**

`provider_service._validate_model_ref` is called from `update_llm_settings` when
an admin saves `default_model` / `fallback_models` / `task_models`. It currently
looks the provider up by name; after the cutover the ref is slug-based. **Only the
provider lookup changes** — preserve the model-id existence, org-override-disabled,
and model-enabled checks exactly:

```python
    async def _validate_model_ref(self, model_ref: str) -> None:
        """Verify a provider/model-id reference points to a visible, enabled model."""
        parts = model_ref.split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid model ref format: '{model_ref}'")
        slug, model_id = parts
        provider = await self._providers.get_by_slug(slug)  # was get_by_name(provider_name)
        if provider is None:
            raise ValueError(f"Provider slug '{slug}' not found")
        if provider.org_id is None:
            override = await self._overrides.get(provider.id)
            if override and not override.enabled:
                raise ValueError(f"Provider '{slug}' is disabled by org")
        model = await self._models.get_by_model_id(provider.id, model_id)
        if model is None:
            raise ValueError(f"Model '{model_id}' not found in provider '{slug}'")
        if not model.enabled:
            raise ValueError(f"Model '{model_id}' is disabled")
```

- [ ] **Step 5c: Runtime writeback — resolve by slug (param rename through public API)**

`runtime_writeback.py` writes back liveness/test status for the provider the
resolver selected. The resolver now returns a **slug** as its first element (it
used to be the name), and that value flows into the writeback. Rename the
parameter through the whole writeback path so it isn't a slug masquerading as a
name:

- In `backend/cubeplex/llm/runtime_writeback.py`: rename `provider_name` →
  `provider_slug` in the resolve helper (≈line 126), `_do_writeback` (≈line 143),
  and the public `schedule_runtime_status_writeback` (≈line 167); switch the lookup
  `repo.get_by_name(provider_name)` → `repo.get_by_slug(provider_slug)`; update the
  internal pass-throughs (≈lines 152, 200, 208).
- Update the external call sites that pass `provider_name=...` (the value is
  already the resolver's slug) to `provider_slug=...`:
  - `backend/cubeplex/services/conversation_title.py:170,173`
  - `backend/cubeplex/streams/run_manager.py` (the `schedule_runtime_status_writeback`/`_schedule_writeback` call — grep for `provider_name=` in that file).
  Rename the local variable feeding it to `provider_slug` where it improves clarity (the value comes from the resolver's first return).

- [ ] **Step 6: Run tests**

Run: `cd backend && set -a && source ../.worktree.env && set +a && uv run pytest tests/unit/test_factory_slug_resolve.py tests/ -k "factory or resolver or default_model or task_model" -q && uv run mypy cubeplex/`
Expected: PASS + mypy clean. (If pre-existing factory tests reference name-based merged keys, update them to slug — the DB providers' slug == slugify(name) for single-word names, so most fixtures keyed on simple names are unaffected.)

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/llm/factory.py backend/cubeplex/services/task_model_resolver.py backend/cubeplex/services/provider_service.py backend/cubeplex/llm/runtime_writeback.py backend/cubeplex/services/conversation_title.py backend/cubeplex/streams/run_manager.py backend/tests/
git commit -m "feat(provider): resolve & validate model refs by provider slug everywhere"
```

---

## Task 7: Frontend core type + ref construction

**Files:**
- Modify: `frontend/packages/core/src/types/provider.ts`
- Modify: `frontend/packages/web/hooks/useAllModels.ts:7-16,60-80`
- Test: `frontend/packages/web/hooks/__tests__/useAllModels.test.ts` (if present; else assert via an existing picker test)

- [ ] **Step 1: Add `slug` to the core Provider + ProviderCreate types**

In `frontend/packages/core/src/types/provider.ts`:
- add to the `Provider` (read) interface, near `name`: `slug: string`
- add to the `ProviderCreate` (write) interface: `slug?: string` (optional — the
  form sends it only in create mode; the backend derives it when omitted). Do
  **not** add slug to `ProviderUpdate` (immutable).

Rebuild core: `cd frontend && pnpm --filter @cubeplex/core build`.

- [ ] **Step 2: Build the ref from slug**

In `frontend/packages/web/hooks/useAllModels.ts`:
- Add `providerSlug: string` to `ProviderModelOption` (after `providerName`), and update the doc comment to `${providerSlug}/${modelId}`.
- In the option builder, set `providerSlug: p.slug,` and change `ref:` to `` `${p.slug}/${m.model_id}` ``.

- [ ] **Step 3: Update/add the test**

If `useAllModels` has a test, update the expected `ref` to use slug. Otherwise add a minimal test that, given a provider `{ id, name: 'My X', slug: 'my-x', ... }` with a model `m-1`, the produced option `ref === 'my-x/m-1'`. (Mock `fetchProviders`/`fetchProvider` as the existing hook tests do.)

- [ ] **Step 4: Verify**

Run: `cd frontend && pnpm --filter @cubeplex/core build && pnpm --filter web exec tsc --noEmit && pnpm --filter web test 2>&1 | tail -5`
Expected: build OK, type-check clean, tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/core/src/types/provider.ts frontend/packages/web/hooks/useAllModels.ts frontend/packages/web/hooks/__tests__
git commit -m "feat(provider): build model refs from provider.slug"
```

---

## Task 8: Provider config form slug field + detail display + e2e

**Files:**
- Modify: `frontend/packages/web/components/admin/models/ProviderConfigForm.tsx`
- Modify: `frontend/packages/web/components/admin/models/ProviderDetail.tsx`
- Test: `frontend/packages/web/components/admin/models/__tests__/ProviderConfigForm.test.tsx`
- Test (backend e2e): `backend/tests/e2e/test_admin_providers_crud.py`

- [ ] **Step 1: Add a TS slugify + slug state to the form**

In `ProviderConfigForm.tsx`, add a tiny local slugify (mirror the backend rule) and slug state that auto-fills from name until the user edits slug:

```ts
function slugifyTs(name: string): string {
  const s = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  return s || 'provider'
}
```

State (with the other `useState` calls):

```ts
const [slug, setSlug] = useState(() => (isCreate ? slugifyTs(preset?.display_name ?? '') : (provider?.slug ?? '')))
const [slugTouched, setSlugTouched] = useState(false)
```

When `name` changes in create mode and the slug field hasn't been manually edited, keep slug in sync. Implement by updating the name `onChange` handler:

```ts
onChange={(e) => {
  setName(e.target.value)
  if (isCreate && !slugTouched) setSlug(slugifyTs(e.target.value))
}}
```

- [ ] **Step 2: Render the slug field**

Add, right after the name field:

```tsx
<div className="flex flex-col gap-1.5">
  <Label htmlFor="cfg-slug">{t('slug')}</Label>
  <Input
    id="cfg-slug"
    value={slug}
    onChange={(e) => { setSlug(e.target.value); setSlugTouched(true) }}
    disabled={!isCreate}
    aria-label={t('slug')}
  />
  {isCreate && <span className="text-[11px] text-muted-foreground">{t('slugHint')}</span>}
</div>
```

Add `slug: slug.trim() || undefined` to the `ProviderCreate` body in create mode (omit from the `ProviderUpdate` body — slug is immutable). The field uses `t('slug')` / `t('slugHint')` where `t = useTranslations('adminModels')` (same translator as the `name` field), so add the keys at **`adminModels.slug`** and **`adminModels.slugHint`** in `messages/en.json` and `messages/zh.json` (parity hook). Do NOT put them under `adminModels.wizard.configure`.

- [ ] **Step 3: Show slug on the detail panel**

In `ProviderDetail.tsx`, render `provider.slug` as muted secondary text next to the provider name (e.g. a `<code className="text-xs text-muted-foreground">{provider.slug}</code>`).

- [ ] **Step 4: Update the form test**

In `ProviderConfigForm.test.tsx`: create mode — typing a name auto-fills the slug field; editing the slug then changing the name does not overwrite it; the submitted `ProviderCreate` body carries the slug. Edit mode — the slug input is disabled and the `ProviderUpdate` body has no `slug`.

- [ ] **Step 5: Add the backend e2e round-trip**

Append to `backend/tests/e2e/test_admin_providers_crud.py`:

```python
async def test_provider_slug_round_trips(admin_client) -> None:
    body = {"name": "Round Trip", "provider_type": "openai-completions",
            "base_url": "https://x.test/v1", "auth_type": "api_key", "api_key": "k"}
    created = (await admin_client.post("/api/v1/admin/providers", json=body)).json()
    assert created["slug"] == "round-trip"
    fetched = (await admin_client.get(f"/api/v1/admin/providers/{created['id']}")).json()
    assert fetched["slug"] == "round-trip"


async def test_default_model_accepts_slug_ref_and_rejects_unknown(admin_client) -> None:
    # create provider + model, then set default_model by slug ref (write-path
    # validation must resolve the provider by slug, not name).
    pbody = {"name": "Routed Provider", "provider_type": "openai-completions",
             "base_url": "https://x.test/v1", "auth_type": "api_key", "api_key": "k"}
    prov = (await admin_client.post("/api/v1/admin/providers", json=pbody)).json()
    assert prov["slug"] == "routed-provider"
    mbody = {"model_id": "m-1", "display_name": "M1", "context_window": 8000, "max_tokens": 1000}
    await admin_client.post(f"/api/v1/admin/providers/{prov['id']}/models", json=mbody)

    ok = await admin_client.put(
        "/api/v1/admin/settings/llm", json={"default_model": "routed-provider/m-1"}
    )
    assert ok.status_code == 200, ok.text
    bad = await admin_client.put(
        "/api/v1/admin/settings/llm", json={"default_model": "Routed Provider/m-1"}
    )
    assert bad.status_code >= 400  # the old display-name ref no longer resolves
```

(Adjust the llm-settings route path / request body to match the real
`OrgLLMSettingsUpdate` endpoint in `admin_providers.py` — confirm the verb and
shape before writing.)

- [ ] **Step 6: Verify**

Run: `cd frontend && pnpm --filter web exec tsc --noEmit && pnpm --filter web test 2>&1 | tail -5`
and `cd backend && set -a && source ../.worktree.env && set +a && uv run pytest tests/e2e/test_admin_providers_crud.py -k slug -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/web/components/admin/models/ProviderConfigForm.tsx frontend/packages/web/components/admin/models/ProviderDetail.tsx frontend/packages/web/components/admin/models/__tests__/ProviderConfigForm.test.tsx frontend/packages/web/messages backend/tests/e2e/test_admin_providers_crud.py
git commit -m "feat(provider): editable slug in create form, show slug on detail"
```

---

## Task 9: Full sweep

- [ ] **Step 1: Backend**

Run: `cd backend && set -a && source ../.worktree.env && set +a && uv run pytest -k "provider or probe or slug or factory or resolver or seed" -q && uv run mypy cubeplex/`
Expected: green.

- [ ] **Step 2: Frontend**

Run: `cd frontend && pnpm --filter @cubeplex/core build && pnpm -w lint && pnpm -w type-check && pnpm --filter web test && pnpm --filter web build`
Expected: green (incl. `next build`).

- [ ] **Step 3: Commit any sweep fixups, then open PR**

PR base = `main` (this is a fresh feature off main, not a stacked slice). Tag `@codex`.

---

## Self-review notes

- **Spec coverage:** schema+constraint (T2/T3), slugify (T1), backfill + per-org collision (T3), ProviderCreate.slug + derive/suffix + 409 (T4), ProviderUpdate omits slug / immutable (T4 + T8 edit read-only), ProviderOut.slug (T4), resolver keyed by slug incl. config providers via slugify(yaml key) (T6), seeder slug (T5), one-shot OrgSettings ref rewrite (T3), frontend ref by slug (T7), editable slug field create-only + read-only edit + show on UI (T8). All mapped.
- **Type consistency:** `slugify` (py) / `slugifyTs` (ts); `ProviderSlugConflictError`; `get_by_slug`; `db_slugs` (renamed from `db_names`); `ProviderModelOption.providerSlug`. Names consistent across tasks.
- **Cutover (all consumers, post-codex):** resolver read path (factory + task_model_resolver), settings-save write path (`_validate_model_ref`), and runtime writeback all switch to slug — no name fallback anywhere (T6). Migration rewrites OrgSettings refs so saved settings keep resolving.
- **System-bucket uniqueness:** enforced via two partial unique indexes (org bucket + `org_id IS NULL` bucket), mirroring `models/credential.py` — a single composite constraint would leave NULL org_ids unconstrained (T2/T3).
- **Slug length:** base capped at 60 chars before `-NN` suffixing in both migration and create path, keeping within the 64-char column (T3/T4).
- **Explicit slug:** validated against `^[a-z0-9]+(-[a-z0-9]+)*$` (422) and uniqueness (409, object detail shape) on create (T4).
- **Existing fixtures (codex r3):** `slug` is required, so direct `Provider(...)` constructors in tests need `slug=` — T2 step 3 enumerates the known files + a grep.
- **Test coverage (codex r3):** migration data test (T3 step 5, mirrors `test_migration.py`) and a DB-backed name≠slug resolution test that fails-first (T6 step 1b).
- **Correct paths (codex r3):** `run_manager.py` lives at `backend/cubeplex/streams/run_manager.py` (not `agent/`); writeback call sites ≈ lines 1067/1153/1162.
- **Risk:** if any pre-existing factory/resolver test fixtures hard-code name-keyed merged config with multi-word names, T6 calls out updating them.
