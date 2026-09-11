# CubePlex cutover to CubeLoop 0.14.1

**Status:** Draft for review
**Date:** 2026-09-11
**Branch / worktree:** `feat/2026-09-10-cubeloop-rename` → `.worktrees/feat/2026-09-10-cubeloop-rename`
**Upstream:** CubeLoop 0.14.1 (`cubeplexai/cubeloop` tag `v0.14.1`),
[migration guide](https://cubeloop.dev/docs/migration/from-cubepi)

## Goal

Move CubePlex off the `cubepi` package onto `cubeloop` 0.14.1 so
installs, imports, traces, CLI, skills, and current docs use the new
name. The Postgres checkpointer schema does **not** move.

## Context

CubePlex currently pins CubePi 0.13.5:

```
cubepi[mcp,postgres,trace-cli,tracing,tracing-otlp]
git+https://github.com/cubeplexai/cubepi.git@af38ff772a1c5f7c2129727a103d11624613b248
```

CubeLoop 0.14.1 is the supported rename release. GitHub is already
`cubeplexai/cubeloop`. The withdrawn 0.14.0 cut renamed physical tables
to `cubeloop_*` and bumped schema to 6; that contract was dropped.
0.14.1 keeps `cubepi_*` tables at schema version 5. `cubepi` 0.14.1+ on
PyPI is a fail-fast tombstone: no dependency on cubeloop, no import
alias, no CLI proxy.

What actually moved in 0.14.1, and what CubePlex touches:

| Surface | 0.13.5 (today) | 0.14.1 | CubePlex impact |
|---|---|---|---|
| PyPI / git package | `cubepi` | `cubeloop` | `pyproject.toml` + `uv.lock` |
| Import root | `from cubepi import …` | `from cubeloop import …` | ~all backend live Python |
| Checkpointer tables | `cubepi_threads`, `cubepi_messages` (+ 64 partitions), `cubepi_runs` (+ 64), `cubepi_hitl_answers`, `cubepi_schema_version` | **unchanged** | no Alembic revision, no live-SQL rewrite |
| SQLAlchemy metadata symbol | `cubepi_metadata` | `cubeloop_metadata` (tables still named `cubepi_*`) | `alembic/env.py` import only |
| Schema version | 5 | 5 | existing v5 DB opens with no DDL |
| OTel vendor attrs / span name | `cubepi.run_id`, `cubepi.metadata.*`, `cubepi.turn`, … | `cubeloop.*` same suffix | Admin Tempo viewer cut over to `cubeloop.*` only; pre-0.14 traces drop out of the UI |
| JSONL default dir | `./cubepi-traces` | `./cubeloop-traces` | config, Dockerfile, gitignore, uvicorn reload exclude |
| CLI | `cubepi trace` | `cubeloop trace` | docs + skills |
| Skills | `cubepi`, `cubepi-trace` from `cubeplexai/cubepi` | `cubeloop`, `cubeloop-trace` from `cubeplexai/cubeloop` | `skills-lock.json`, `.agents/skills/`, `AGENTS.md` |

0.13.6 (compaction: trailing synthetic user controls no longer hide
current-turn evidence) sits between the current pin and 0.14.1. It rides
along with the bump. No CubePlex code change is expected for it.

The Python agent API CubePlex already uses (`Agent`, `tool`, providers,
middleware, HITL, checkpointer methods) is otherwise the same. This is a
package rename, not a runtime-API or schema redesign.

## Approaches considered

### 1. Depend on the `cubepi` 0.14.1 tombstone

Rejected. The tombstone has no CubeLoop dependency and every
`import cubepi` fails. There is no compatibility proxy.

### 2. Library surface only — keep CubePlex identifiers named cubepi

Change the pin and imports, but leave `_run_cubepi_path`,
`cubepi_runtime.py`, `CubepiAgentRunError`, and `test_cubepi_*.py`.

Rejected. After the library rename those names are a split identity.
CubePlex is not publicly shipped; AGENTS.md says cut over cleanly.

### 3. Full live-code cutover; schema and frozen history stay — chosen

Depend on `cubeloop` directly. Rewrite live imports, CubePlex
identifiers, config, current docs, and skills. Leave physical table
names `cubepi_*`. Tempo and the admin trace viewer cut over to
`cubeloop.*` only — pre-0.14 traces in Tempo are not kept searchable.
Do **not** rewrite frozen `docs/dev/{specs,plans,notes}` or the SQL
inside historical Alembic revisions.

## Design

### Pin

Replace the `cubepi` dependency with:

```
cubeloop[mcp,postgres,trace-cli,tracing,tracing-otlp]
git+https://github.com/cubeplexai/cubeloop.git@v0.14.1
```

`[tool.uv.sources]` drops the `cubepi` git override and adds the matching
`cubeloop` one. Do this with `uv add`, not a hand-edit of
`pyproject.toml`. Inspect the lock diff: only cubeloop and its
direct transitives should move.

Do not also depend on the `cubepi` tombstone.

### Imports

Every live `from cubepi…` / `import cubepi…` becomes `cubeloop`. That
includes application code, tests, and Alembic `env.py`.

Historical Alembic revisions that import
`cubepi.checkpointer.postgres.alembic_helpers` switch the **Python
import** to `cubeloop.checkpointer.postgres.alembic_helpers`. The SQL
those helpers emit is still `cubepi_*` (`create_message_partitions_op`,
`upgrade_v3_to_v4_op`, `upgrade_v4_to_v5_op`,
`write_schema_version_op` writing version 5). No inlining, no new
revision.

Pin and helper-import retarget are one slice: after `uv add` removes
`cubepi`, a greenfield `alembic upgrade head` dies at v1 until those
imports move.

Exception: `eef196f4c8f9` already `try/except ImportError`s
`cubepi.providers.catalog` (deleted years ago). Leave that import as
`cubepi.providers.catalog` — the except path is the real body, and
`cubeloop.providers.catalog` does not exist either.

### CubePlex identifiers

Rename live CubePlex symbols that embed the old **package** name. Table
names stay `cubepi_*`. The mapping is mechanical (`cubepi` → `cubeloop`,
`Cubepi` → `Cubeloop`). Load-bearing ones:

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

### Checkpointer — no schema migration

`EXPECTED_SCHEMA_VERSION` stays 5. `cubeloop_metadata` still maps to
`cubepi_threads` / `cubepi_messages` / `cubepi_runs` /
`cubepi_hitl_answers` / `cubepi_schema_version`. Opening 0.14.1 against
an existing CubePlex v5 database succeeds with no DDL.

`alembic/env.py` changes the import to `cubeloop_metadata`. The
autogenerate exclusion set does **not** need `cubeloop_*` table names —
those tables are not in this release. Keep today's `cubepi_messages` /
`cubepi_runs` / `cubepi_hitl_answers` / `cubepi_schema_version` plus
the `cubepi_messages_p` / `cubepi_runs_p` prefixes. `cubepi_threads`
stays out of the set (v1 autogen created it); metadata still describes
that same table, so autogen will not propose a drop.

Do not add an Alembic revision. Do not call a v6 helper — 0.14.1 does
not ship `upgrade_v5_to_v6_op()`.

Live SQL (`history_window`, fork-chain walk, workspace/user delete,
e2e inserts) keeps `FROM cubepi_threads` / `cubepi_messages` /
`cubepi_runs`. Only the CubePlex function name `_stamp_cubepi_runs`
changes; it already goes through `cp.mark_run_complete`.

### Tracing — cubeloop-only, no dual-read

Writers (CubeLoop itself) emit `cubeloop.*` only. CubePlex stamps
unprefixed metadata (`conversation_id`, `org_id`, …) via
`tracing_context`; the library prefixes it. No CubePlex write-path
change beyond comments.

Readers CubePlex owns cut over to `cubeloop.*` in the same way. Pre-0.14
Tempo traces and on-disk JSONL that still carry `cubepi.*` are out of
scope for the admin viewer. Upstream `cubeloop trace` still dual-reads
JSONL on its own; CubePlex does not.

**Parser** (`tempo_client.parse_trace_detail` and friends): every vendor
key is `cubeloop.<suffix>`. Span classification treats `cubeloop.turn`
as `SpanKind.TURN` (`cubepi.turn` is no longer recognized).

**TraceQL search / tag-values:** `span.cubepi.metadata.X` /
`span.cubepi.run_id` become `span.cubeloop.metadata.X` /
`span.cubeloop.run_id`. The org-scope gate on detail
(`_has_foreign_org_span`) reads `cubeloop.metadata.org_id` only.

**Allowlist** for `/admin/traces/tag-values`:
`cubeloop.metadata.{workspace,user,conversation}_id` and
`gen_ai.request.model`. Drop the `cubepi.*` spellings.

**Fixtures:** rewrite the existing Tempo JSON fixtures from `cubepi.*`
to `cubeloop.*`.

**JSONL directory:** default `tracing.directory` becomes
`./cubeloop-traces`. No automatic fallback to `./cubepi-traces`.
Operators who already set the path keep their setting.
`backend/main.py` mkdir/reload-exclude and the backend Dockerfile
`mkdir` follow the new default. `.gitignore` adds `cubeloop-traces/` and
**keeps** `cubepi-traces/` so leftover local dirs stay ignored.

The pin and the Tempo attribute rename must ship together: 0.14.1 spans
are invisible to today's `span.cubepi.*` TraceQL.

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
- Frontend comments that name the **package** (`from cubepi`, "cubepi
  runtime"). Comments that name the **table** (`cubepi_messages.seq`)
  stay — that table is still called that. No TypeScript type change.

### What stays `cubepi` on purpose

- Physical table / index / partition names (`cubepi_threads`, …).
- Frozen `docs/dev/specs/`, `plans/`, `notes/` (they document what
  shipped).
- SQL **strings** inside historical Alembic revisions.
- Live application SQL against those tables.
- `.gitignore` entry for `cubepi-traces/`.
- The `eef196f4c8f9` `cubepi.providers.catalog` ImportError branch.

A case-insensitive grep of live first-party **imports** after the
cutover should only hit that catalog ImportError.

## Out of scope

- Switching the pin from git to PyPI. Keep the git source override,
  pointed at `cubeplexai/cubeloop@v0.14.1`.
- Depending on the `cubepi` 0.14.1 tombstone.
- Any Alembic revision that renames checkpointer tables.
- Rewriting frozen `docs/dev` snapshots or git history.
- Renaming Alembic revision **filenames** (`555c11215b57_add_cubepi_…`).
- Migrating on-disk JSONL from `cubepi-traces/` into `cubeloop-traces/`.
- Dual-reading or dual-writing OTel attributes. The admin viewer and
  Tempo TraceQL are cubeloop-only; pre-0.14 `cubepi.*` traces drop out
  of the UI. (Upstream `cubeloop trace` still dual-reads JSONL; that is
  not CubePlex's job.)
- Product/UI copy that says "CubePlex" — this is the runtime rename,
  not a CubePlex rebrand.
- Adopting new CubeLoop APIs that 0.14.1 did not introduce.

## Success criteria

- `uv run python -c "import cubeloop; import cubepi"` — first import
  works, second raises `ModuleNotFoundError` (we did not install the
  tombstone).
- After `alembic upgrade head` on a DB that already had v5: `\dt
  cubepi_*` still shows parents + 64+64 partitions; no `cubeloop_*`
  data tables; `SELECT version FROM cubepi_schema_version` is `5`.
- A greenfield `alembic upgrade head` (empty DB, replay of v1→v5)
  ends on the same schema. v1 still calls `create_message_partitions_op()`.
- Opening a checkpointer against that DB does not raise
  `CubeloopSchemaMismatch` / `CubeloopSchemaUninitialized`.
- Admin trace list/detail/tag-values work against `cubeloop.*` fixtures
  and are org-scoped by `cubeloop.metadata.org_id`.
- `history_window` / conversation bootstrap / fork / user-or-workspace
  delete still read and delete `cubepi_*` tables.
- `rg -n "from cubepi|import cubepi" backend/cubeplex backend/tests
  backend/alembic/env.py` is empty except the documented
  `eef196f4c8f9` catalog ImportError.
- Current docs and skills tell an operator to `pip install cubeloop` /
  `uv run cubeloop trace`, and point at `https://cubeloop.dev` /
  `cubeplexai/cubeloop`.
