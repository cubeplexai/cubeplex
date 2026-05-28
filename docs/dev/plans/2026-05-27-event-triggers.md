# Event Triggers Implementation Plan

> For agentic workers: execute tasks in order. Each task is TDD — write the
> failing test first, then the implementation, then run the listed command and
> confirm the expected output before moving on. Stay on branch
> `feat/event-triggers`; never switch to main or merge mid-execution. Run only
> the changed-module tests per task; reserve the full suite for the pre-PR
> sweep (Task 12). All paths are relative to the worktree root
> `/home/chris/cubebox/.worktrees/feat/event-triggers`. Read `.worktree.env`
> first — this slot runs the backend on port 8033 and DB
> `cubebox_feat_event_triggers`; tests auto-route to `cubebox_test_<slug>` via
> `tests/conftest.py`, so plain `uv run pytest` is safe.

## Goal

Ship the v1 **Event Triggers** abstraction described in
`docs/dev/specs/2026-05-27-event-triggers-design.md`: a workspace-owned
`Trigger` (source + filter + target + run identity) and a shared
event → run pipeline with dedup, rate limiting, retry/dead-letter, and a
`trigger_events` audit row per inbound event. The only v1 source is an
**inbound webhook** authenticated by HMAC-SHA256 over the raw body with a
timestamp window; the only v1 target is an **inline prompt template**. The
fired run lands through the existing `RunManager.start_run` path under an
explicit `run_as_user_id`. Schedule (#150) and IM (#149) are *not* built here —
the `source_type` enum and `NormalizedEvent` seam are left ready for them, and
`target_type="managed_agent"` is reserved in the schema but unimplemented.

## Architecture

```
POST /api/v1/ws/{ws}/triggers/{trigger_id}/ingest   (public, HMAC-auth)
  → read raw body bytes
  → look up trigger by (ws, id); missing OR disabled → flat 404 {"error":"not_found"}
  → resolve webhook secret from credential vault (source_config cred ref)
  → HMAC-SHA256(secret, f"{ts}.{raw_body}") constant-time compare
  → timestamp window ±300s
  → dedup_key = X-Event-Id header if present, else sha256(raw_body)
  → INSERT trigger_events (trigger_id, dedup_key) — unique hit → 200 duplicate
  → token-bucket rate limit (Redis) → 429/202 rate_limited
  → evaluate declarative filter over payload → 200 filtered_out
  → enqueue NormalizedEvent → 202 Accepted
                                   │
        event → run pipeline (TriggerPipeline.fire) ─ async, source-agnostic
          → build RunContext(run_as_user_id, trig.org_id, trig.workspace_id)
          → conversation policy (new_each_time | pinned + busy_policy)
          → render inline prompt template against payload (whitelisted fields)
          → run_manager.start_run(conversation_id, content, ctx) with retry/backoff
          → write resulting_run_id + terminal status onto the trigger_events row
          → on exhausted retries → status=dead_lettered (replayable)
```

New tables `triggers` (`_PREFIX="trig"`) and `trigger_events`
(`_PREFIX="trev"`), both `CubeboxBase + OrgScopedMixin`. Scope-isolated
workspace CRUD + event-log + replay routes under `require_member`; the
`ingest` route is the only one not member-guarded (HMAC instead).

### Layout (new files)

| File | Purpose |
|---|---|
| `backend/cubebox/models/trigger.py` | `Trigger` + `TriggerEvent` SQLModels, enums |
| `backend/cubebox/repositories/trigger.py` | `TriggerRepository`, `TriggerEventRepository` (scoped) |
| `backend/cubebox/triggers/__init__.py` | package marker |
| `backend/cubebox/triggers/events.py` | `NormalizedEvent` dataclass + `dedup_key` derivation |
| `backend/cubebox/triggers/signature.py` | HMAC sign/verify + timestamp window (pure fns) |
| `backend/cubebox/triggers/filter.py` | declarative AND/OR matcher evaluator (pure) |
| `backend/cubebox/triggers/template.py` | whitelisted prompt-template render (pure) |
| `backend/cubebox/triggers/rate_limit.py` | Redis token-bucket per trigger |
| `backend/cubebox/triggers/pipeline.py` | `TriggerPipeline.fire` (identity→conv→start_run, retry/DLQ) |
| `backend/cubebox/triggers/ingest.py` | ingest orchestration (verify→dedup→ratelimit→filter→enqueue) |
| `backend/cubebox/api/routes/v1/ws_triggers.py` | workspace CRUD + events + replay routes |
| `backend/cubebox/api/routes/v1/trigger_ingest.py` | public `/ingest` route |

## Tech Stack

Backend FastAPI + SQLModel + Alembic (autogenerate), Postgres, Redis
(`RedisHandle`/`redis_dep`), `hmac`/`hashlib` from stdlib. Reuses the existing
credential vault (`CredentialService.get_decrypted`, kind `webhook_secret`),
`RunManager.start_run` / `RunContext`, `ScopedRepository`, `require_member` /
`RequestContext`. Tests: pytest + `async_client` fixture (authenticated httpx
against the in-process app) in `backend/tests/e2e/`; pure-function units in
`backend/tests/unit/`. mypy strict, ruff, 100-char lines, type annotations
everywhere.

---

## Task 1 — Models: `Trigger` + `TriggerEvent` + prefixes

Write `backend/cubebox/models/trigger.py`. Add `PREFIX_TRIGGER = "trig"` and
`PREFIX_TRIGGER_EVENT = "trev"` to `backend/cubebox/models/public_id.py`.

`Trigger(CubeboxBase, OrgScopedMixin, table=True)`, `__tablename__="triggers"`,
`__table_args__ = (org_scope_index("triggers"),)`, fields:
- `name: str = Field(max_length=128)`
- `enabled: bool = Field(default=True, index=True)`
- `source_type: str = Field(max_length=16)` — values `webhook|schedule|im|mcp_event` (v1 writes only `webhook`)
- `source_config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))` — webhook holds `{"secret_cred_id": "...", "event_id_header": "X-Event-Id", "signature_header": "X-Signature", "timestamp_header": "X-Timestamp", "max_body_bytes": 1048576}`
- `filter: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))` — matcher tree; null = match all
- `target_type: str = Field(max_length=16)` — `inline|managed_agent` (v1 writes only `inline`)
- `target_ref: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))` — inline: `{"prompt_template": "...", "model_id": "..."}`
- `conversation_policy: str = Field(default="new_each_time", max_length=16)` — `new_each_time|pinned`
- `pinned_conversation_id: str | None = Field(default=None, foreign_key="conversations.id", max_length=20)`
- `busy_policy: str = Field(default="skip", max_length=8)` — `queue|skip`
- `run_as_user_id: str = Field(foreign_key="users.id", max_length=20)`
- `max_runs_per_minute: int = Field(default=10)`
- `rate_limit_burst: int = Field(default=20)`

`TriggerEvent(CubeboxBase, OrgScopedMixin, table=True)`,
`__tablename__="trigger_events"`, `__table_args__`:
`(org_scope_index("trigger_events"), Index("uq_trigger_event_dedup", "trigger_id", "dedup_key", unique=True))`, fields:
- `trigger_id: str = Field(foreign_key="triggers.id", max_length=20, index=True)`
- `source_type: str`, `event_type: str | None`, `dedup_key: str = Field(max_length=64)`
- `occurred_at: datetime | None`, `received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))`
- `status: str = Field(max_length=16)` — `accepted|duplicate|filtered_out|rate_limited|failed|dead_lettered`
- `attempts: int = Field(default=0)`, `last_error: str | None`
- `payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))`
- `resulting_run_id: str | None`, `resulting_conversation_id: str | None`

Export both from `backend/cubebox/models/__init__.py` (`from
cubebox.models.trigger import Trigger, TriggerEvent` + add to `__all__`).

**Test** `backend/tests/unit/test_trigger_models.py`: assert
`Trigger(...).id.startswith("trig-")`, `TriggerEvent(...).id.startswith("trev-")`,
and that constructing with defaults yields `enabled is True`,
`conversation_policy == "new_each_time"`.

```bash
cd backend && uv run pytest tests/unit/test_trigger_models.py -q
```
Expected: `2 passed`.

## Task 2 — Migration (autogenerate)

Generate the migration; do **not** hand-write it.

```bash
cd backend && uv run alembic revision --autogenerate -m "add triggers and trigger_events"
```
Expected: a new file under `backend/alembic/versions/` whose `upgrade()`
contains `op.create_table('triggers', ...)` and
`op.create_table('trigger_events', ...)` plus
`op.create_index('uq_trigger_event_dedup', 'trigger_events', ['trigger_id', 'dedup_key'], unique=True)`.

Apply and round-trip to verify autogen is complete (no drift):

```bash
cd backend && uv run alembic upgrade head && uv run alembic check
```
Expected: upgrade runs clean; `alembic check` prints `No new upgrade operations detected.`

## Task 3 — Scoped repositories

Write `backend/cubebox/repositories/trigger.py`:
- `TriggerRepository(ScopedRepository[Trigger])` with `model = Trigger`; add
  `list_enabled()` and `get_for_ingest(trigger_id)` (returns the row only if it
  exists *and* `enabled`, else `None` — the caller maps both to a flat 404).
- `TriggerEventRepository(ScopedRepository[TriggerEvent])` with
  `model = TriggerEvent`; add `insert_dedup(event: TriggerEvent) -> TriggerEvent | None`
  that catches `sqlalchemy.exc.IntegrityError` on the unique constraint
  (rollback + return `None` = duplicate), `set_terminal(event_id, status, *, run_id=None, conversation_id=None, last_error=None)`, and `list_for_trigger(trigger_id, *, limit, offset)`.

Export both from `backend/cubebox/repositories/__init__.py`.

**Test** `backend/tests/e2e/test_trigger_repository.py` (hits the DB → e2e dir):
create a trigger, insert a `TriggerEvent`, re-insert the same
`(trigger_id, dedup_key)` and assert the second `insert_dedup` returns `None`;
assert a different-workspace repo cannot `get` the trigger (scope isolation).

```bash
cd backend && uv run pytest tests/e2e/test_trigger_repository.py -q
```
Expected: `2 passed`.

## Task 4 — HMAC signature + timestamp window (pure)

Write `backend/cubebox/triggers/signature.py`:
- `sign(secret: str, timestamp: str, raw_body: bytes) -> str` →
  `hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()`.
- `verify(secret: str, timestamp: str, raw_body: bytes, provided: str) -> bool` →
  `hmac.compare_digest(sign(...), provided)`.
- `timestamp_fresh(timestamp: str, *, now: datetime, max_age_seconds: int = 300) -> bool`
  — parse int epoch seconds, return `abs(now_epoch - ts) <= max_age_seconds`;
  malformed timestamp → `False`.

**Test** `backend/tests/unit/test_trigger_signature.py`: round-trip
sign/verify true; tampered body → false; wrong secret → false; stale and
future timestamps → `timestamp_fresh` false; in-window → true; non-numeric
timestamp → false.

```bash
cd backend && uv run pytest tests/unit/test_trigger_signature.py -q
```
Expected: `6 passed` (or your final count — all pass).

## Task 5 — Dedup key + NormalizedEvent (pure)

Write `backend/cubebox/triggers/events.py`:
- `@dataclass NormalizedEvent` with `event_id, source_type, trigger_id,
  event_type, occurred_at, subject, payload, dedup_key` per the spec.
- `derive_dedup_key(raw_body: bytes, event_id_header: str | None) -> str`:
  return `event_id_header` when truthy, else
  `hashlib.sha256(raw_body).hexdigest()`. **Must not** include the signed
  timestamp — a re-signed identical body must yield the same key.

**Test** `backend/tests/unit/test_trigger_dedup.py`: header present → header
used; absent → body hash; the **same body with two different timestamps**
yields the **same** dedup_key (regression on the spec's body-hash rule).

```bash
cd backend && uv run pytest tests/unit/test_trigger_dedup.py -q
```
Expected: `3 passed`.

## Task 6 — Declarative filter matcher (pure)

Write `backend/cubebox/triggers/filter.py`:
- `matches(filter_tree: dict | None, payload: dict) -> bool`. `None` → `True`.
  Node shapes: `{"and": [..]}`, `{"or": [..]}`, or a leaf
  `{"path": "a.b", "op": "eq|neq|contains|exists|in", "value": ...}`.
  `path` is dot-walked into `payload` (missing → sentinel). `exists` ignores
  `value`; `in` checks membership in a list `value`; `contains` checks the
  field (string/list) contains `value`.

**Test** `backend/tests/unit/test_trigger_filter.py`: `None` matches anything;
`eq` on `action`; nested `and`/`or`; `in`; `exists` on missing path → false;
unknown op raises `ValueError`.

```bash
cd backend && uv run pytest tests/unit/test_trigger_filter.py -q
```
Expected: all pass (`>=6 passed`).

## Task 7 — Prompt template render (pure)

Write `backend/cubebox/triggers/template.py`:
- `render(template: str, payload: dict, *, allowed_paths: list[str]) -> str`.
  Substitute `{{ a.b }}` placeholders only when `a.b` ∈ `allowed_paths`;
  any other placeholder renders as the literal token (never interpolated) so
  untrusted webhook bodies can't smuggle arbitrary fields into the prompt.
  Substituted values are coerced to `str`. No format-string / eval surface.

**Test** `backend/tests/unit/test_trigger_template.py`: allowed path
substituted; disallowed placeholder left verbatim; missing allowed value →
empty string; nested path resolves.

```bash
cd backend && uv run pytest tests/unit/test_trigger_template.py -q
```
Expected: `4 passed`.

## Task 8 — Redis token-bucket rate limit

Write `backend/cubebox/triggers/rate_limit.py`:
- `async def allow(redis: Redis, *, key_prefix: str, trigger_id: str,
  rate_per_min: int, burst: int, now: float) -> bool`. Token-bucket keyed
  `f"{key_prefix}:trig:rl:{trigger_id}"` storing `(tokens, last_refill)` in a
  Redis hash; refill `rate_per_min/60` tokens/sec capped at `burst`; consume 1
  if available. Use a small Lua script or `WATCH/MULTI` for atomicity.

**Test** `backend/tests/e2e/test_trigger_rate_limit.py` (needs Redis → e2e):
with `rate_per_min=60, burst=3` and a fixed `now`, the 1st–3rd calls return
`True`, the 4th `False`; advancing `now` by 1s refills one token → next call
`True`.

```bash
cd backend && uv run pytest tests/e2e/test_trigger_rate_limit.py -q
```
Expected: `2 passed`.

## Task 9 — Event → run pipeline

Write `backend/cubebox/triggers/pipeline.py`. `TriggerPipeline` holds the
`RunManager` + `async_session_maker` + Redis handle.
`async def fire(self, trigger: Trigger, event: NormalizedEvent, event_row_id: str) -> None`:
1. Re-validate `run_as_user_id` is still a member of the workspace
   (`MembershipRepository.get_role`); if not, disable the trigger, mark the
   event `failed` with `last_error="run_as_user lost membership"`, return.
2. Build `RunContext(user_id=trigger.run_as_user_id, org_id=trigger.org_id,
   workspace_id=trigger.workspace_id)`.
3. Conversation: `new_each_time` → create a draft `Conversation` via
   `ConversationRepository.create(title=..., draft=True)`; `pinned` → use
   `trigger.pinned_conversation_id`.
4. `target_type == "inline"` → `render(target_ref["prompt_template"], event.payload,
   allowed_paths=target_ref.get("allowed_paths", []))`. `managed_agent` →
   raise `NotImplementedError` (reserved; never written in v1).
5. Call `self.run_manager.start_run(conversation_id=..., content=...,
   attachments=[], ctx=ctx)` with retry: up to 4 attempts, backoff 1s × 2^n
   capped at 30s, on each attempt bump `TriggerEvent.attempts`. On
   `RuntimeError` "already has an active run" apply `busy_policy`: `skip` →
   mark `failed`/return; `queue` → push to
   `f"{prefix}:trig:queue:{conversation_id}"` and return (drain is a documented
   follow-up; v1 records the queued intent).
6. On success: `set_terminal(event_row_id, "accepted", run_id=..., conversation_id=...)`.
7. On exhausted retries: `set_terminal(event_row_id, "dead_lettered",
   last_error=...)`.

**Test** `backend/tests/e2e/test_trigger_pipeline.py`: with a real
`RunManager` (the test app's), fire an inline trigger and assert a
`TriggerEvent` reaches `accepted` with a non-null `resulting_run_id` and a new
conversation exists. Add a case where `run_as_user_id` is not a member → event
`failed`, trigger `enabled` flips false. Add a `managed_agent` target → event
`failed` (NotImplementedError caught and recorded).

```bash
cd backend && uv run pytest tests/e2e/test_trigger_pipeline.py -q
```
Expected: `3 passed`.

## Task 10 — Scope-isolated workspace CRUD + events + replay routes

Write `backend/cubebox/api/routes/v1/ws_triggers.py`. Router
`APIRouter(prefix="/ws/{workspace_id}/triggers", tags=["triggers"])`, every
handler `ctx: Annotated[RequestContext, Depends(require_member)]`. Routes:
`GET ""` (list), `POST ""` (create — validates `run_as_user_id` membership and
that `secret_cred_id` resolves to a `webhook_secret` credential),
`GET "/{id}"`, `PATCH "/{id}"` (update / enable / disable),
`DELETE "/{id}"`, `GET "/{id}/events"` (event log, `utc_isoformat` on
timestamps), `POST "/{id}/events/{eid}/replay"` (re-fires a `dead_lettered`
event through `TriggerPipeline.fire`; 409 if not dead-lettered). Per the
scope-isolation rule these are workspace-only; any future admin view gets its
own `/api/v1/admin/...` handlers. Mount in `app.py` and export from
`backend/cubebox/api/routes/v1/__init__.py`. This lands before the ingest route
so the ingest E2E (Task 11) can create triggers + secrets through this API.

**Test** `backend/tests/e2e/test_ws_triggers.py`: create → list (visible) →
get → patch disabled → list still returns it but `enabled=false` → delete →
404; a second workspace's client sees an empty list (isolation); creating with
a non-member `run_as_user_id` → 400/422; replay of a non-dead-lettered event →
409. Add a **dead-letter + replay** E2E: force `start_run` failure by pointing
the trigger at a non-existent model so `fire` exhausts retries; assert
`dead_lettered`, call replay, assert a re-attempt is recorded.

```bash
cd backend && uv run pytest tests/e2e/test_ws_triggers.py -q
```
Expected: all pass.

## Task 11 — Public ingest route + orchestration

Write `backend/cubebox/triggers/ingest.py` `handle_ingest(...)` and
`backend/cubebox/api/routes/v1/trigger_ingest.py`. Router
`APIRouter(prefix="/ws/{workspace_id}/triggers", tags=["trigger-ingest"])`,
`POST "/{trigger_id}/ingest"`. Mount in `backend/cubebox/api/app.py`
(`app.include_router(trigger_ingest.router, prefix="/api/v1")`). No
`require_member` — auth is the HMAC. Order exactly per spec §"Inbound webhook
ingestion":
1. `raw = await request.body()`; reject `>` `max_body_bytes` with the same flat
   `404 {"error":"not_found"}` shape before hashing.
2. `TriggerRepository(... ).get_for_ingest(trigger_id)`; `None` (missing **or**
   disabled) → `JSONResponse(status_code=404, content={"error":"not_found"})`.
3. Resolve secret: `CredentialService.get_decrypted(credential_id=
   source_config["secret_cred_id"], requesting_kind="webhook_secret")`.
4. Read signature + timestamp headers (names from `source_config`);
   `verify(...)` false → flat 404. `timestamp_fresh(...)` false → flat 404.
   (Pre-auth failures are constant-shape; do not branch the response.)
5. `derive_dedup_key`; build `TriggerEvent(status="accepted", attempts=0, ...)`
   and `insert_dedup`; `None` → `200 {"status":"duplicate"}`.
6. `rate_limit.allow(...)` false → `set_terminal(..., "rate_limited")` →
   `429 {"status":"rate_limited"}`.
7. `matches(trigger.filter, payload)` false → `set_terminal(...,
   "filtered_out")` → `200 {"status":"filtered_out"}`.
8. Schedule `TriggerPipeline.fire(...)` via `asyncio.create_task` (or
   `BackgroundTasks`) and return `202 {"status":"accepted", "event_id": ...}`.

JSON body that fails to parse → treat `payload={}` (signature already proved
authenticity; don't 400 on body shape after auth, but record `event_type=None`).

**Test** `backend/tests/e2e/test_trigger_ingest.py` — the core E2E suite,
using `async_client`:
- helper that creates a `webhook_secret` credential + a trigger via the CRUD
  API (Task 10), then signs a body with `signature.sign`.
- **happy path**: correctly-signed POST → `202`; poll the events endpoint until
  the row is `accepted` with a `resulting_run_id`; assert a conversation was
  created.
- **bad signature** → `404 {"error":"not_found"}`, no event row.
- **missing trigger id** and **disabled trigger** → identical `404
  {"error":"not_found"}` (oracle check: same status + body).
- **dedup/replay**: send same signed body twice → second `200 duplicate`,
  exactly one `accepted` row.
- **stale timestamp** → `404`.
- **filter miss** → `200 filtered_out`, no run.
- **rate limit**: burst beyond bucket → excess `429 rate_limited`.
- **tenant isolation**: a trigger created in ws A cannot be ingested under ws
  B's path (404), and ws B's CRUD list cannot see it.

```bash
cd backend && uv run pytest tests/e2e/test_trigger_ingest.py -q
```
Expected: all pass (one test per bullet, all green).

## Task 12 — Pre-PR sweep (lint, types, full backend tests)

```bash
cd backend && uv run ruff check cubebox tests && uv run ruff format --check cubebox tests
```
Expected: `All checks passed!` and no formatting diffs.

```bash
cd backend && uv run mypy cubebox
```
Expected: `Success: no issues found` (or no new errors in `cubebox/triggers`,
`cubebox/models/trigger.py`, the two new repos/routes).

```bash
cd backend && uv run pytest tests/unit/test_trigger_*.py tests/e2e/test_trigger_*.py tests/e2e/test_ws_triggers.py -q
```
Expected: all trigger tests pass.

```bash
cd backend && uv run pytest -q
```
Expected: full suite green (no regressions introduced by the new tables /
routes / migration).

---

## Notes / deferred (explicitly out of v1, recorded so reviewers don't flag as gaps)

- **`target_type="managed_agent"`** — schema column + `target_ref` reserved;
  `pipeline.fire` records a `failed` event for it. Implemented when #153 lands.
- **Schedule (#150) / IM (#149) sources** — `source_type` enum + the
  `NormalizedEvent` + `TriggerPipeline.fire` entrypoint are the seam they call;
  no adapter is built here.
- **`busy_policy="queue"` drainer** — v1 records the queued intent on a Redis
  list; the active-run-completion drain hook is a documented follow-up (spec
  open question "One-active-run rule vs new_each_time").
- **`trigger_events` retention/TTL** — open question in the spec; v1 keeps all
  rows. No GC job here.
- **Secret rotation overlap window** — spec open question; v1 resolves a single
  `secret_cred_id`. Dual-secret acceptance is a fast-follow.
- **Per-provider presets** (GitHub `X-Hub-Signature-256`, Stripe) — v1 ships
  the generic HMAC scheme with configurable header names in `source_config`.
- **Cross-process run start** — v1 starts in the receiving process (spec
  option (a)); shared-queue fan-out deferred.
