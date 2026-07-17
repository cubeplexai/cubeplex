# Short Prefixed Public IDs

**Date**: 2026-05-05
**Status**: Draft (pending user review)
**Branch**: `feat/short-public-ids`
**Scope**: Replace the current `uuid7()` string primary keys on every business
table with short prefixed public IDs (e.g. `conv-V1StGXR8Z5jdHi`). Single-column
schema (no separate auto-increment column). No data migration — dev/test
schema is rebuilt from a fresh Alembic baseline.

## Goals

1. Every (non-association) business table's primary key becomes a string of the
   form `{prefix}-{14-char-base62}`, where the prefix is a 2–4 char mnemonic
   tied to the table.
2. IDs are **time-ordered at millisecond granularity** — sorting by ID
   approximates sorting by creation time, the same property uuid7 currently
   provides.
3. IDs are **safe to generate from multiple application instances** without
   coordination — collision probability under any realistic load is negligible,
   and a DB `unique` constraint provides a hard backstop.
4. API/SSE/URL/log surface area sees the new shorter ID with no field-name
   changes (`id` is still `id`); internal storage and FK columns stay simple
   (`workspace_id`, `org_id` etc. remain string FKs to the parent's `id`).
5. Frontend types remain `id: string` — the change is invisible at the type
   level; only string contents and URL segments shift.

## Non-goals

- **No data migration**. Existing rows are dropped. Dev/test databases are
  rebuilt from the new baseline. There is no production data; no compat layer.
- **No internal bigint PK** (`aid`-style). FKs continue to be string columns
  referencing the parent table's `id`. We deliberately accept a slightly
  larger index footprint in exchange for one ID concept across all layers.
  Revisit only if a specific table outgrows it.
- **No change to LangGraph checkpointer state** or message-level IDs. Messages
  live in checkpointer state, not a DB table; their IDs are LangGraph-managed.
- **No change to `invite_tokens.token`** column — the token is the user-facing
  invite link, has its own format and purpose, and is not a generic public ID.
- **No frontend-visible field renames**. API responses keep `id` as the field
  name; only the value shape changes.
- **No JS/TS-side ID generation**. All IDs originate on the backend.

## §1 ID format

### 1.1 Layout

```
{prefix}-{14 chars base62}
        ^
        single ASCII hyphen
```

- Prefix: lowercase ASCII, 2–4 characters, table-specific (see §2).
- Body: 14 base62 characters (alphabet `0-9A-Za-z`, 62 symbols).
- Total length: 17–19 characters.
- Stored as `VARCHAR(20)` (room for the longest 4-char prefix + `-` + 14 + 1
  spare).

### 1.2 Body bit layout

The 14-char base62 body encodes an 83-bit unsigned integer:

| Bits | Field      | Meaning                                                      |
|------|------------|--------------------------------------------------------------|
| 41   | timestamp  | Milliseconds since custom epoch `2024-01-01T00:00:00Z` (UTC) |
| 42   | randomness | Cryptographically random per ID                              |

- 41 bits of ms gives ~69.7 years of headroom from the epoch.
- 42 bits of randomness per ms gives a collision probability per pair within
  the same millisecond on the order of 2⁻⁴². For any realistic generation
  rate this is multiple orders of magnitude below "never observed."
- 14 base62 chars = `floor(14 × log2(62))` ≈ 83.36 bits of representational
  capacity; the 83-bit payload fits with one bit slack (the high bit is
  effectively zero).

### 1.3 Monotonicity guarantees

| Scope                           | Guarantee                                                              |
|---------------------------------|------------------------------------------------------------------------|
| Across instances, different ms  | Strictly increasing (timestamp prefix dominates)                       |
| Across instances, same ms       | Time-tied; tie-breaker is random — IDs from the same ms are unordered  |
| Within one process, same ms     | **Strictly increasing** via in-process monotonic factory (see below)   |

This matches the contract uuid7 / ULID provide. It is the strongest
monotonicity reachable without coordination.

### 1.4 In-process monotonic factory

The generator keeps two thread-local state values: `last_ms` and `last_rand`.

```
def next_id_int() -> int:
    now_ms = current_ms()
    if now_ms <= last_ms:
        # same or backwards clock: keep last_ms, bump rand
        rand = (last_rand + 1) & RAND_MASK
        if rand == 0:                # 42-bit overflow within one ms
            last_ms += 1             # spill into next logical ms
            rand = secure_rand_42()
    else:
        last_ms = now_ms
        rand = secure_rand_42()
    last_rand = rand
    return (last_ms << 42) | rand
```

- Clock-going-backwards (NTP slew, container migration) is absorbed: we never
  emit a smaller integer than the previous one in the same process.
- 42-bit overflow within a single ms requires 4 trillion IDs/ms/process and is
  not reachable; the spill path exists for completeness.

### 1.5 Generator API

New module: `backend/cubeplex/models/public_id.py`

```python
def generate_public_id(prefix: str) -> str: ...
```

That is the entire public surface. No `parse_public_id` / shape-validation
helper — malformed IDs from clients are filtered naturally by the DB lookup
returning no row (→ 404), and the cost of a missing-row lookup is trivial.

Implementation notes:
- `secrets.randbits(42)` for randomness.
- A single module-level `Lock` guards `last_ms` / `last_rand` (the work is
  trivial, contention isn't a concern). No async needed — call sites are
  default-factory hooks invoked synchronously by SQLModel/SQLAlchemy.
- Base62 encode is hand-rolled, fixed-length 14 chars, leading zeros padded
  with `'0'`.
- No external dependency. We deliberately avoid `nanoid` (random-only) and
  `python-ulid` (different alphabet, longer string).

## §2 Prefix registry

Central registry in `cubeplex/models/public_id.py`:

```python
PREFIX_CONVERSATION       = "conv"
PREFIX_ATTACHMENT         = "atch"
# ...etc
```

Each model imports its own prefix constant; the prefix is referenced exactly
once per model in the `default_factory`. No model should construct an ID with
a literal string.

| Table                          | Prefix | Notes                                                  |
|--------------------------------|--------|--------------------------------------------------------|
| `organizations`                | `org`  |                                                        |
| `workspaces`                   | `ws`   |                                                        |
| `users`                        | `usr`  |                                                        |
| `invite_tokens`                | —      | **No prefix; token column unchanged** (see Non-goals)  |
| `conversations`                | `conv` |                                                        |
| `attachments`                  | `atch` |                                                        |
| `artifacts`                    | `art`  |                                                        |
| `artifact_versions`            | `artv` |                                                        |
| `agent_configs`                | `agt`  |                                                        |
| `user_sandboxes`               | `sbx`  |                                                        |
| `skills`                       | `skl`  |                                                        |
| `skill_versions`               | `sklv` |                                                        |
| `org_skill_installs`           | `osi`  |                                                        |
| `mcp_servers`                  | `mcp`  |                                                        |
| `workspace_mcp_credentials`    | `wmc`  |                                                        |
| `user_mcp_credentials`         | `umc`  |                                                        |
| `providers`                    | `prv`  |                                                        |
| `models`                       | `mdl`  |                                                        |
| `credentials`                  | `cred` |                                                        |
| `billing_events`               | `bill` |                                                        |
| `billing_llm_events`           | `llmb` |                                                        |
| `org_provider_overrides`       | `opo`  |                                                        |

Tables that intentionally do **not** get a public ID (composite key or
singleton):

| Table                            | Reason                                                        |
|----------------------------------|---------------------------------------------------------------|
| `memberships`                    | Composite PK `(user_id, workspace_id)` — never URL-addressed  |
| `workspace_skill_bindings`       | Pure association                                              |
| `workspace_mcp_bindings`         | Pure association                                              |
| `org_preinstalled_tombstones`    | Internal state marker, not user-addressable                   |
| `org_settings`                   | 1:1 with `organizations`; addressed via `org_id` only         |

## §3 Schema changes

### 3.1 Per-table PK column

Before:
```python
id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
```

After:
```python
id: str = Field(
    default_factory=lambda: generate_public_id(PREFIX_CONVERSATION),
    primary_key=True,
    max_length=20,
)
```

### 3.2 `OrgScopedMixin`

Before:
```python
class OrgScopedMixin:
    org_id: str = Field(max_length=36, index=True)
    workspace_id: str = Field(max_length=36, index=True)
```

After:
```python
class OrgScopedMixin:
    org_id: str = Field(max_length=20, foreign_key="organizations.id", index=True)
    workspace_id: str = Field(max_length=20, foreign_key="workspaces.id", index=True)
```

(Adds explicit FK declarations that were previously implicit by string match.
SQLModel didn't enforce them at the DB level. We make them real FKs.)

### 3.3 All other FK columns

Every column referencing another table's `id` shrinks `max_length=36` → 20 and
declares `foreign_key=`. Touch list:

- `conversations.creator_user_id` → `users.id`
- `attachments.conversation_id` → `conversations.id` and `attachments.uploader_user_id` → `users.id`
- `artifacts.*`, `artifact_versions.artifact_id`, etc.
- All MCP/skill/credential/provider/model FKs.

A complete column-by-column map will be produced in the implementation plan;
the design rule is: **any column that holds another table's `id` must be
declared as a real FK with `max_length=20`**.

### 3.4 Alembic baseline

- Delete every revision under `backend/alembic/versions/`.
- Generate one fresh revision via `alembic revision --autogenerate -m
  "initial short public id schema"`.
- Hand-audit the generated revision for sanity (FK directions, indexes,
  composite indexes carried over from `__table_args__`).
- Reset all dev/test databases:
  - In each worktree: `worktree-env destroy && new-worktree …` or manual
    `dropdb && createdb && alembic upgrade head`.
  - Main worktree: same drop/recreate.
- Document the schema-reset step in the implementation plan; AI agents and
  contributors with existing local databases need this when pulling.

## §4 Application-layer changes

### 4.1 Repositories

`ScopedRepository[T]` already filters by `(org_id, workspace_id)` and exposes
the row via its existing `get` / `get_by_id` API. No structural change —
column is still named `id`, so existing query call sites continue to work.

### 4.2 Routes / dependencies

Existing FastAPI dependencies that resolve `workspace_id` / `conversation_id`
from the URL stay unchanged. They accept a string from the path, look up the
row, and 404 on miss — that path already handles malformed IDs correctly
without any pre-validation.

### 4.3 API schemas (Pydantic / response models)

Field name remains `id: str`. No alias. The shorter format is a transparent
content change for clients.

### 4.4 SSE event payloads

Any event field that carries a DB row ID (e.g. attachment IDs threaded
through tool results, conversation ID echoed in `done` events) inherits the
new format automatically — they are read directly from ORM rows. No field
renaming, no payload version bump.

### 4.5 Frontend

- `@cubeplex/core` types: `id: string` unchanged. Build still required after
  re-exporting any updated zod schemas (none expected here).
- URL segments (`/w/{wsId}/conversations/{convId}`): values change shape
  (shorter, prefixed). Code that builds these URLs already uses the values
  returned from the API, so no string manipulation needs editing.
- Hardcoded UUID-shaped strings in tests/fixtures need to be replaced with
  values fetched from the API or generated via test factories. See §5.
- No changes to `ApiClient.setWorkspaceId`, the SSE proxy route, or zustand
  stores.

## §5 Testing

### 5.1 Backend

- E2E tests rely on the API to return IDs and rarely hardcode them. Spot fixes
  expected; the implementation plan enumerates files.
- A small unit test for `public_id.py` covers:
  - Format shape and base62 alphabet.
  - Monotonicity within one process across ms boundaries (mock clock).
  - Same-ms rand bump and ms-spill on overflow.

### 5.2 Frontend

- Playwright E2E uses live API responses for IDs in nearly all paths; affected
  spots are limited to fixtures that pre-create rows by literal ID. Those
  switch to API calls.

## §6 Rollout

There is no rollout — no production data, no two-phase migration. The change
is dev-only schema replacement.

Steps:

1. Implement model + generator changes on `feat/short-public-ids`.
2. Single Alembic baseline replaces existing revisions.
3. CI is unaffected (CI uses fresh ephemeral DBs).
4. Worktree owners (including main) rebuild their local databases when
   pulling the merged branch — this is documented in the merge PR description
   and added to the "Pulling main" hint in `backend/CLAUDE.md`.

## §7 Open items

None. All previously-discussed decisions are resolved:

- Prefix list — fixed in §2.
- ID format — single-PK string, no internal `aid` column.
- ID body — 14 base62, 41-ms-bit + 42-rand-bit, monotonic factory.
- `org_settings` and `invite_tokens` — explicitly excluded.
- API contract — field name `id` preserved, frontend types unchanged.

## §8 Implementation entry points

A separate writing-plans pass will produce the step-by-step plan. The expected
phases:

1. Generator + prefix registry (`public_id.py`) with unit tests.
2. Model + mixin updates, tablewise FK rewiring.
3. Alembic baseline reset.
4. Database reset across worktrees + run full backend E2E.
5. Frontend Playwright pass and any fixture updates.
6. Merge plan: explicit DB-rebuild step in the PR description.
