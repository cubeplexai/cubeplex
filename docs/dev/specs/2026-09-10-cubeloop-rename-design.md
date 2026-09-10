# CubePlex cutover to CubeLoop 0.14

**Status:** Draft for review
**Date:** 2026-09-10
**Branch / worktree:** `feat/2026-09-10-cubeloop-rename` → `.worktrees/feat/2026-09-10-cubeloop-rename`
**Upstream:** CubeLoop 0.14.0 (`cubeplexai/cubeloop` tag `v0.14.0`),
[migration guide](https://cubeloop.dev/docs/migration/from-cubepi)

## Goal

Move CubePlex off the `cubepi` package onto `cubeloop` 0.14 so installs,
imports, checkpointer tables, traces, CLI, skills, and current docs all
use the new name.

## Context

CubePlex currently pins CubePi 0.13.5:

```
cubepi[mcp,postgres,trace-cli,tracing,tracing-otlp]
git+https://github.com/cubeplexai/cubepi.git@af38ff772a1c5f7c2129727a103d11624613b248
```

CubeLoop 0.14.0 (2026-09-10) renamed the project in place. GitHub is
already `cubeplexai/cubeloop`. The old PyPI name remains as a
transitional wrapper that depends on `cubeloop==0.14.0` and warns on
`import cubepi`.

What actually moved in 0.14, and what CubePlex touches:

| Surface | 0.13.5 (today) | 0.14 | CubePlex impact |
|---|---|---|---|
| PyPI / git package | `cubepi` | `cubeloop` | `pyproject.toml` + `uv.lock` |
| Import root | `from cubepi import …` | `from cubeloop import …` | ~all backend live Python |
| Checkpointer tables | `cubepi_threads`, `cubepi_messages` (+ 64 partitions), `cubepi_runs` (+ 64), `cubepi_hitl_answers`, `cubepi_schema_version` | `cubeloop_*` same suffix | Alembic v5→v6 + every raw SQL |
| SQLAlchemy metadata | `cubepi_metadata` | `cubeloop_metadata` | `alembic/env.py` |
| Schema version | 5 | 6 | Opening 0.14 against v5 raises `CubeloopSchemaMismatch` |
| OTel vendor attrs / span name | `cubepi.run_id`, `cubepi.metadata.*`, `cubepi.turn`, … | `cubeloop.*` same suffix | Admin Tempo viewer cut over to `cubeloop.*` only; pre-0.14 traces drop out of the UI |
| JSONL default dir | `./cubepi-traces` | `./cubeloop-traces` | config, Dockerfile, gitignore, uvicorn reload exclude |
| CLI | `cubepi trace` | `cubeloop trace` | docs + skills |
| Skills | `cubepi`, `cubepi-trace` from `cubeplexai/cubepi` | `cubeloop`, `cubeloop-trace` from `cubeplexai/cubeloop` | `skills-lock.json`, `.agents/skills/`, `AGENTS.md` |

0.13.6 (compaction: trailing synthetic user controls no longer hide
current-turn evidence) sits between the current pin and 0.14. It rides
along with the bump. No CubePlex code change is expected for it.

The Python agent API CubePlex already uses (`Agent`, `tool`, providers,
middleware, HITL, checkpointer methods) is otherwise the same. This is a
rename plus a schema bump, not a runtime-API redesign.

## Approaches considered

### 1. Stay on the `cubepi` 0.14 shim

`uv add cubepi@0.14` would keep `from cubepi import …` working. The shim
still pulls `cubeloop`, still requires schema v6, still writes
`cubeloop.*` spans, and prints a deprecation warning on every import.

Rejected. CubePlex is the first-party host. Living on a wrapper that
exists for anonymous PyPI lockfiles is worse than doing the import
rewrite once. Schema and Tempo work do not go away.

### 2. Library surface only — keep CubePlex identifiers named cubepi

Change the pin, imports, tables, and traces, but leave
`_run_cubepi_path`, `cubepi_runtime.py`, `CubepiAgentRunError`,
`cubepi_dict_to_agent_event`, and `test_cubepi_*.py` as they are.

Rejected. After the library rename those names are a split identity:
grep for the runtime and you hit the old word. CubePlex is not publicly
shipped; AGENTS.md says cut over cleanly rather than keep a compatibility
layer.

### 3. Full live-code cutover; frozen history stays — chosen

Depend on `cubeloop` directly. Rewrite live imports, identifiers, SQL,
config, current docs, and skills. Tempo and the admin trace viewer
cut over to `cubeloop.*` only — pre-0.14 traces in Tempo are not
kept searchable. Do **not** rewrite frozen `docs/dev/{specs,plans,notes}`
or the SQL inside historical Alembic revisions (those revisions must
keep emitting `cubepi_*` table names so a greenfield replay of v1→v5
still works).

## Design

### Pin

Replace the `cubepi` dependency with:

```
cubeloop[mcp,postgres,trace-cli,tracing,tracing-otlp]
git+https://github.com/cubeplexai/cubeloop.git@v0.14.0
```

`[tool.uv.sources]` drops the `cubepi` git override and adds the matching
`cubeloop` one. Do this with `uv add`, not a hand-edit of
`pyproject.toml`. Inspect the lock diff: only cubeloop and its
direct transitives should move. 0.13.5 already used anthropic / openai /
pydantic in the same family; no CubePlex API change is expected from
those.

Do not also depend on the `cubepi` shim.

### Imports

Every live `from cubepi…` / `import cubepi…` becomes `cubeloop`. That
includes application code, tests, and Alembic `env.py`.

Historical Alembic helper imports are **not** a blanket swap. In
cubeloop 0.14, current-schema factories emit `cubeloop_*` names;
only some historical helpers still emit `cubepi_*`:

| Helper | 0.14 SQL | CubePlex callers |
|---|---|---|
| `create_message_partitions_op()` | `cubeloop_messages_pXX PARTITION OF cubeloop_messages` | `555c11215b57` (v1) |
| `create_runs_partitions_op()` | `cubeloop_runs_pXX` | none (v4 inlined cubepi names via `upgrade_v3_to_v4_op`) |
| `upgrade_v3_to_v4_op()`, `upgrade_v4_to_v5_op()`, `add_pending_request_column_op()`, `add_run_id_column_op()` | still `cubepi_*` | v4 / v5 revisions |
| `write_schema_version_op()` | writes `EXPECTED_SCHEMA_VERSION` (now 6) to `cubeloop_schema_version` if that table exists, else `cubepi_schema_version` | v1–v5 |

v1 (`555c11215b57`) creates `cubepi_messages` then calls
`create_message_partitions_op()`. After the pin that helper would
`PARTITION OF cubeloop_messages`, which does not exist yet — greenfield
`alembic upgrade head` dies at v1. **Stop calling that helper from v1.**
Inline the original SQL in the revision: `CREATE TABLE cubepi_messages_p00…p63
PARTITION OF cubepi_messages FOR VALUES WITH (modulus 64, remainder N)`.

v2–v5 may switch the import path to
`cubeloop.checkpointer.postgres.alembic_helpers` because the helpers
they call still emit `cubepi_*`. `write_schema_version_op()` writing 6
from v1 on a greenfield replay is the existing host contract (today v1
already writes current expected, which is 5). Alembic runs v1→v6 in one
`upgrade head`; the checkpointer is not opened against the intermediate
row. Do not rewrite those calls to insert historical version numbers.

Exception: `eef196f4c8f9` already `try/except ImportError`s
`cubepi.providers.catalog` (deleted years ago). Leave that import as
`cubepi.providers.catalog` — the except path is the real body, and
`cubeloop.providers.catalog` does not exist either.

### CubePlex identifiers

Rename live CubePlex symbols that embed the old package name. The
mapping is mechanical (`cubepi` → `cubeloop`, `Cubepi` → `Cubeloop`,
`CUBEPI` does not appear as a CubePlex env prefix). Load-bearing ones:

| Old | New |
|---|---|
| `CubepiAgentRunError` | `CubeloopAgentRunError` |
| `_run_cubepi_path` / `_run_cubepi_respond_path` | `_run_cubeloop_path` / `_run_cubeloop_respond_path` |
| `cubepi_dict_to_agent_event` | `cubeloop_dict_to_agent_event` |
| `_drain_cubepi_sse_queue` | `_drain_cubeloop_sse_queue` |
| `_stamp_cubepi_runs` | `_stamp_cubeloop_runs` |
| `wire_input_to_cubepi_user_message` | `wire_input_to_cubeloop_user_message` |
| `steering_message_to_cubepi` | `steering_message_to_cubeloop` |
| `mcp/cubepi_runtime.py` | `mcp/cubeloop_runtime.py` |
| `load_workspace_mcp_tools_for_cubepi` | `load_workspace_mcp_tools_for_cubeloop` |
| `_invoke_tool_via_cubepi` | `_invoke_tool_via_cubeloop` |
| `database.cubepi_pool_min` / `_max` | `database.cubeloop_pool_min` / `_max` |
| `test_cubepi_*.py`, `test_run_manager_cubepi_*.py` | `test_cubeloop_*` / `test_run_manager_cubeloop_*` |

No fallback alias for the old names. Call sites and tests move in the
same change.

### Checkpointer schema v5 → v6

New Alembic revision, same pattern as `c2f1a7b9d340` (v4→v5):

1. Update `alembic/env.py` **first** so autogenerate is empty:
   - Import `cubeloop_metadata` from
     `cubeloop.checkpointer.postgres.models`.
   - `target_metadata = [SQLModel.metadata, cubeloop_metadata]`.
   - `_CHECKPOINT_TABLES` must list **both** old and new parent names,
     including **`cubepi_threads` and `cubeloop_threads`**. Today
     `cubepi_threads` is *not* in the set (v1 autogen created it). After
     metadata points at cubeloop-named tables, a still-v5 DB still has
     `cubepi_threads`; omitting it lets autogen propose
     `DROP cubepi_threads` (CASCADE onto messages/runs/hitl). Also
     exclude both partition prefixes (`cubepi_messages_p` /
     `cubepi_runs_p` **and** `cubeloop_messages_p` / `cubeloop_runs_p`).
   - Full table set: existing langgraph leftovers, plus
     `cubepi_{threads,messages,runs,hitl_answers,schema_version}` and
     `cubeloop_{threads,messages,runs,hitl_answers,schema_version}`.
2. `alembic revision --autogenerate -m "cubeloop v5 to v6 rename"`.
   Body is empty. Hand-add:

   ```python
   op.execute(upgrade_v5_to_v6_op())
   op.execute(write_schema_version_op())
   ```

   `upgrade_v5_to_v6_op()` is idempotent when both `cubeloop_threads` and
   `cubeloop_schema_version` exist. It renames parents, 64 message
   partitions, 64 run partitions, `cubepi_hitl_answers`, the version
   table, and `ix_cubepi_*` indexes.
3. Downgrade **reverses the rename**. It must not `DROP` the tables —
   that would delete every conversation. PostgreSQL `ALTER TABLE …
   RENAME TO` on a partitioned parent does **not** rename children, so
   the inverse must rename each of the 128 partitions the same way the
   upgrade helper does. Required order (names only; FKs follow relation
   identity):

   1. `ix_cubeloop_*` → `ix_cubepi_*` (the three indexes the helper
      renamed)
   2. `cubeloop_schema_version` → `cubepi_schema_version`
   3. `cubeloop_hitl_answers` → `cubepi_hitl_answers`
   4. `cubeloop_runs_p00…p63` then `cubeloop_runs` → `cubepi_runs*`
   5. `cubeloop_messages_p00…p63` then `cubeloop_messages` → `cubepi_messages*`
   6. `cubeloop_threads` → `cubepi_threads`
   7. Write version 5 into `cubepi_schema_version`

   Verify with a throwaway-DB round trip: v5 → v6 → v5 → v6, then
   `\dt cubeloop_*` / partition count / version row.

`write_schema_version_op()` in 0.14 writes to `cubeloop_schema_version`
if that table exists, else to `cubepi_schema_version`. Historical
revisions that call it during a greenfield replay therefore still hit
the old table until v6 runs. Do not "fix" those old `write_schema_version_op()`
calls to target `cubeloop_schema_version` by name.

Opening 0.14 against an unmigrated v5 database raises
`CubeloopSchemaMismatch` pointing at `upgrade_v5_to_v6_op()`, not
"tables not found". CubePlex must ship the Alembic revision in the same
release as the pin.

### Live SQL against checkpointer tables

CubePlex queries the checkpointer tables directly in several places
(bypassing the checkpointer API). After v6 those names are `cubeloop_*`:

- `cubeplex/services/history_window.py` — `SELECT … FROM cubepi_messages`
- `cubeplex/repositories/attachment.py` — `SELECT parent_thread_id FROM cubepi_threads`
- `cubeplex/api/routes/v1/workspaces.py` and `auth.py` — `DELETE FROM cubepi_threads WHERE thread_id IN (…)`
- Tests that insert/delete `cubepi_threads` / `cubepi_messages` /
  `cubepi_runs` (`test_stranded_run_recovery`, `test_conversation_fork`,
  `test_cubepi_checkpointer_integration`, `test_history_window`,
  `test_agent_history_artifacts_actions`, …)

Update those strings to `cubeloop_*`. Do not dual-read table names:
after the migration the old names are gone.

`recovery._stamp_cubepi_runs` already goes through
`cp.mark_run_complete`; only the function name and log text change.

### Tracing — cubeloop-only, no dual-read

Writers (CubeLoop itself) emit `cubeloop.*` only. CubePlex stamps
unprefixed metadata (`conversation_id`, `org_id`, …) via
`tracing_context`; the library prefixes it. No CubePlex write-path
change beyond comments.

Readers CubePlex owns cut over to `cubeloop.*` in the same way. Pre-0.14
Tempo traces and on-disk JSONL that still carry `cubepi.*` are out of
scope for the admin viewer: they will not appear in search, detail, or
tag-values. Upstream `cubeloop trace` still dual-reads JSONL on its own;
CubePlex does not.

**Parser** (`tempo_client.parse_trace_detail` and friends): every vendor
key is `cubeloop.<suffix>`. Span classification treats `cubeloop.turn`
as `SpanKind.TURN` (`cubepi.turn` is no longer recognized). Do not call
`cubeloop.tracing.schema.attr` for a cubepi fallback.

**TraceQL search / tag-values:** `span.cubepi.metadata.X` /
`span.cubepi.run_id` become `span.cubeloop.metadata.X` /
`span.cubeloop.run_id`. The org-scope gate on detail
(`_has_foreign_org_span`) reads `cubeloop.metadata.org_id` only. A
trace with no cubeloop org attribute stays invisible.

**Allowlist** for `/admin/traces/tag-values`:
`cubeloop.metadata.{workspace,user,conversation}_id` and
`gen_ai.request.model`. Drop the `cubepi.*` spellings.

**Fixtures:** rewrite the existing Tempo JSON fixtures from `cubepi.*`
to `cubeloop.*`. No 0.13 corpus is kept for the viewer tests.

**JSONL directory:** default `tracing.directory` becomes
`./cubeloop-traces`. No automatic fallback to `./cubepi-traces` (matches
upstream CLI). Operators who already set the path keep their setting.
`backend/main.py` mkdir/reload-exclude and the backend Dockerfile
`mkdir` follow the new default. `.gitignore` adds `cubeloop-traces/` and
**keeps** `cubepi-traces/` so leftover local dirs stay ignored.

### Config

| Key / path | Change |
|---|---|
| `tracing.directory` default | `./cubepi-traces` → `./cubeloop-traces` |
| `database.cubepi_pool_min` / `_max` | `database.cubeloop_pool_min` / `_max`. Not present in `config.yaml` today (code defaults 1/10). No read-fallback of the old key. |

### Skills, agent docs, current product docs

- Replace `.agents/skills/cubepi` and `cubepi-trace` with upstream
  `cubeloop` / `cubeloop-trace` from `cubeplexai/cubeloop`. Update
  `skills-lock.json` source + paths + hashes.
- `AGENTS.md` skill index and the backend one-liner.
- README / `backend/README.md` / `backend/docs/{agent-system-design,prompt-cache-discipline,quick-reference}.md`.
- `docs/site` current EN + zh-Hans: `deployment/backend-config.md`
  (directory + "cubepi dynamic budget"), `deployment/kubernetes.md`
  (`GITHUB_MIRROR` still rewrites the git+url; the package name in that
  sentence becomes cubeloop), `guides/conversations/basics.md` (`cubeloop
  trace`).
- Frontend comments that name `cubepi_messages` / "cubepi's wire shape"
  — update when they would otherwise describe a table or package that no
  longer exists. No TypeScript type change: the on-wire message dump is
  the same pydantic shape.

### What stays `cubepi` on purpose

- Frozen `docs/dev/specs/`, `plans/`, `notes/` (they document what
  shipped).
- SQL **strings** inside historical Alembic revisions (v1–v5 helpers
  still create/alter `cubepi_*`).
- `.gitignore` entry for `cubepi-traces/`.
- The `eef196f4c8f9` `cubepi.providers.catalog` ImportError branch.

A case-insensitive grep of live first-party code after the cutover
should only hit those classes.

## Out of scope

- Switching the pin from git to PyPI. Keep the git source override,
  pointed at `cubeplexai/cubeloop@v0.14.0`.
- Depending on the `cubepi` 0.14 shim for any extra.
- Rewriting frozen `docs/dev` snapshots or git history.
- Renaming Alembic revision **filenames** (`555c11215b57_add_cubepi_…`).
- Migrating on-disk JSONL from `cubepi-traces/` into `cubeloop-traces/`.
- Dual-reading or dual-writing OTel attributes. The admin viewer and
  Tempo TraceQL are cubeloop-only; pre-0.14 `cubepi.*` traces drop out
  of the UI. (Upstream `cubeloop trace` still dual-reads JSONL; that is
  not CubePlex's job.)
- Product/UI copy that says "CubePlex" — this is the runtime rename,
  not a CubePlex rebrand.
- Adopting new CubeLoop APIs that 0.14 did not introduce (fork wiring,
  etc.).

## Success criteria

- `uv run python -c "import cubeloop; import cubepi"` — first import
  works, second raises `ModuleNotFoundError` (we did not install the
  shim).
- After `alembic upgrade head` on a DB that already had v5: `\dt
  cubeloop_*` shows parents + 64+64 partitions; `cubepi_*` data tables
  are gone; `SELECT version FROM cubeloop_schema_version` is `6`.
- A greenfield `alembic upgrade head` (empty DB, replay of v1→v6,
  including the inlined v1 `cubepi_messages_pXX` DDL) ends on the same
  schema. This is what the e2e test-DB bootstrap already does.
- Opening a checkpointer against that DB does not raise
  `CubeloopSchemaMismatch` / `CubeloopSchemaUninitialized`.
- Admin trace list/detail/tag-values work against `cubeloop.*` fixtures
  and are org-scoped by `cubeloop.metadata.org_id`. A leftover 0.13
  Tempo payload with only `cubepi.*` keys is not a supported input.
- `history_window` / conversation bootstrap / fork / user-or-workspace
  delete still read and delete the renamed tables.
- `rg -n "from cubepi|import cubepi" backend/cubeplex backend/tests
  backend/alembic/env.py` is empty except the documented
  `eef196f4c8f9` catalog ImportError.
- Current docs and skills tell an operator to `pip install cubeloop` /
  `uv run cubeloop trace`, and point at `https://cubeloop.dev` /
  `cubeplexai/cubeloop`.
