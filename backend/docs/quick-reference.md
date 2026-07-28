# Backend Quick Reference

Reference content (env vars, commands, file layout). For workflow discipline,
hard rules, and skill triggers, see the root [AGENTS.md](../../AGENTS.md).
For prompt cache discipline, auth, and architecture deep dives, see the
focused docs alongside this one.

## Repository Structure

```
backend/
├── cubeplex/
│   ├── agents/       # Agent graph factory, schemas, message conversion
│   ├── api/          # FastAPI app, routes, exceptions
│   ├── llm/          # LLM factory, config, OpenAI-compatible client
│   ├── memory/       # Memory manager (short / long-term)
│   ├── mcp/          # MCP protocol client + runtime
│   ├── middleware/   # Agent middleware (sandbox, subagents, skills, ...)
│   ├── prompts/      # System prompts
│   ├── sandbox/      # Code execution sandbox
│   ├── tools/        # Tool registry + built-in tools
│   ├── utils/        # Logging
│   └── config.py     # Dynaconf-based config
├── tests/e2e/        # E2E tests (primary focus)
├── docs/             # Architecture + reference docs
├── scripts/dev/      # Temporary dev scripts
├── config.yaml
├── config.development.yaml
├── config.production.yaml
├── main.py
└── Makefile
```

## Commands (run from `backend/`)

```bash
make dev-install       # uv sync --all-extras
make format            # ruff format + import sort
make lint              # ruff check
make lint-fix          # ruff check --fix
make type-check        # mypy cubeplex/
make test              # pytest -s -v
make test-cov          # pytest with HTML coverage
make check             # format + lint + type-check + test (pre-commit sweep)
make pre-commit-install
```

Single test file: `uv run pytest tests/e2e/test_agents.py`.

## Environment Variables

The actual entry points loaded by `config.py`:

- `ENV_FOR_DYNACONF` (optional, default `development`)
- `CUBEPLEX_LLM__PROVIDERS__<PROVIDER>__BASE_URL`
- `CUBEPLEX_LLM__PROVIDERS__<PROVIDER>__API_KEY`
- `CUBEPLEX_LLM__DEFAULT_MODEL` (overrides `default_model`; format
  `provider/model-id`)
- `CUBEPLEX_AUTH__JWT_SECRET`
- `CUBEPLEX_AUTH__CSRF_SECRET`
- `CUBEPLEX_AUTH__VAULT_KEY` — comma-separated Fernet keys; first encrypts,
  all decrypt. Required once Credential Vault is enabled.
- `CUBEPLEX_REDIS__URL`
- `CUBEPLEX_DATABASE__HOST|PORT|USER|PASSWORD|NAME`
- `CUBEPLEX_SANDBOX__DOMAIN`
- `CUBEPLEX_SANDBOX__IMAGE`
- `CUBEPLEX_SANDBOX__API_KEY`
- `CUBEPLEX_LOG_LEVEL` (default `INFO`)
- `CUBEPLEX_LICENSE__KEY` (`license.key`) — EE license key (`CPX1.…`); absent or
  invalid → OSS edition, with a warning. Verified offline in
  `cubeplex/plugins/license.py` against the `kid` named in the key.
- `CUBEPLEX_LICENSE__PUBLIC_KEY_HEX` (`license.public_key_hex`) — bypasses the
  `kid` lookup and verifies against this key instead. **Tests/dev only** (pair
  with `scripts/dev/license_keygen.py`); never set in production.

Test env (`config.test.yaml`):

- `CUBEPLEX_E2E_LLM_BASE_URL`
- `CUBEPLEX_E2E_LLM_API_KEY`
- `CUBEPLEX_E2E_LLM_MODEL_ID`

`backend/.env.example` shows ready-to-use names. `config.py` also loads
`backend/.env` and `backend/config.<env>.local.yaml` as local overrides.

Env vars override YAML config (use the `CUBEPLEX_` prefix).

## Sandbox Skills Storage (PVC Requirement)

Skills persist under `/workspace/.skills/` in the sandbox, backed by a PVC mount.
The `sandbox.volume.enabled` setting (default `true` as of 2026-06-25) enables
persistent volumes scoped by `(workspace_id, user_id)` — each active user–workspace
pair gets one volume. Deployers must provision PVC storage; at scale, monitor
usage and configure reclaim policies. The fallback `sandbox.volume.enabled: false`
still works but re-syncs skills on every sandbox kill+recreate (defeats caching).
Not recommended for production.

## Running E2E Tests Locally

Local E2E runs the `development` env. Required files (both gitignored):

- `backend/.env` — secret-bearing env vars (LLM keys, sandbox keys,
  `CUBEPLEX_E2E_LLM_*`).
- `backend/config.development.local.yaml` — machine-specific overrides
  (LLM endpoint URL, sandbox domain).

**Copy both from a working machine; do not recreate from scratch.** With
them in place, `uv run pytest tests/e2e/` runs cleanly with no
command-line env vars.

### Working on the commercially licensed package

`backend/ee/` builds `cubeplex-ee`, a separate distribution exposing the top-level
`cubeplex_ee` package. It is an opt-in extra, absent from a default install —
which is what keeps the import gate in `cubeplex/plugins/ee.py` meaningful.

```bash
cd backend
uv pip install -e ee          # work on it
uv pip uninstall cubeplex-ee  # back to the default surface
```

It is declared as a **dependency group**, not an extra, on purpose:
`make dev-install` runs `uv sync --all-extras`, which would drag an extra in and
leave nothing for the import gate to gate. The declarative equivalent of the
commands above is `uv sync --all-extras --group licensed` — both flags, since
`--group` alone re-resolves the environment and drops the test dependencies.

With it installed, startup requires a licence key: that is the gate doing its
job, not a bug. The test suite is written against the default install and a few
tests assert that surface directly, so either uninstall it before running tests,
or mint a key for the shell (`license_keygen.py dev-env`, see above).
`tests/conftest.py` fails fast with both options spelled out if you forget.

### Frontend E2E and the EE-gated admin pages

`admin-sso.spec.ts` and `admin-insights.spec.ts` drive admin pages wrapped in
`<EEGate>`, so the backend they talk to has to report `edition=ee` or those
specs see the "Enterprise feature" card instead of the page. Mint a throwaway
license into the shell that starts the backend:

```bash
cd backend
set -a; eval "$(uv run python scripts/dev/license_keygen.py dev-env)"; set +a
python main.py
```

The `set -a` matters. `dev-env` prints bare `NAME=value` lines (the format
`$GITHUB_ENV` needs), and `eval` on those creates shell variables that child
processes do not inherit — without `set -a` the server starts and quietly reports
`edition: oss`.

`dev-env` generates a keypair, signs a short-lived key, and prints
`CUBEPLEX_LICENSE__KEY` plus `CUBEPLEX_LICENSE__PUBLIC_KEY_HEX`. Nothing is
persisted — there is no committed secret and nothing to expire. CI does the same
in the `frontend-e2e` job, where appending to `$GITHUB_ENV` handles the export.

Confirm it took effect before blaming a spec:

```bash
curl -s localhost:8000/api/v1/system/info | grep -o '"edition":"[a-z]*"'
```

In a fresh worktree, copy them in before the first test run:

```bash
cp /path/to/main/backend/.env backend/.env
cp /path/to/main/backend/config.development.local.yaml \
   backend/config.development.local.yaml
```

Missing files surface as:
- `DynaconfFormatError: Dynaconf can't interpolate variable because
  'CUBEPLEX_E2E_LLM_*'` at config load, or
- Quiet `'error' == 'text_delta'` SSE assertion failures inside agent tests
  (agent crashes mid-stream on lazy interpolation, emits an error event).

## Database

- Alembic migrations: `alembic upgrade head`, `alembic revision -m "..."`.
- After SQLModel schema changes: `alembic revision --autogenerate -m "..."`.
- Pulling this branch into an existing checkout: drop and recreate the local
  DB before running migrations — the alembic baseline is incompatible with
  prior revisions.

### Datetime columns (tz-aware, always)

All `datetime` model fields use `sa_column=Column(DateTime(timezone=True), ...)`
(Postgres `timestamptz`). Application code writes `datetime.now(UTC)`
(tz-aware). Frontend gets ISO 8601 with `+00:00` (via `utc_isoformat()`) or
`Z` (via Pydantic default) — both valid. No naive `datetime` ever crosses the
DB or service-API boundary.

**Migration trap:** when introducing a new datetime column or converting an
existing one, the alembic migration must hand-add
`postgresql_using="<col> AT TIME ZONE 'UTC'"` on each `alter_column` call —
autogen omits it, and the default cast applies the session `TimeZone`
(wrong for our stored UTC values).

### Migration head conflicts after rebase

When rebasing onto main introduces a second alembic head, do NOT use
`alembic merge heads`. Instead, edit the branch's first migration file to
change its `down_revision` to main's new head. This keeps the history linear.

### Short prefixed public IDs

All business tables use short prefixed string PKs (e.g.
`conv-V1StGXR8Z5jdHi`, ≤20 chars). `cubeplex.models.public_id.generate_public_id`
packs a 41-bit ms timestamp + 42-bit random into 14 base62 chars — sortable
at ms granularity, multi-instance safe.

To add a new business table: define a `PREFIX_<NAME>` constant in
`public_id.py`, then on the `id` column use
`default_factory=lambda: generate_public_id(PREFIX_<NAME>)` with
`max_length=20`. Pure association tables (composite PK) skip the prefix.

## Vault Key Rotation

1. Generate a new Fernet key.
2. Deploy `CUBEPLEX_AUTH__VAULT_KEY=<new>,<old>` (both keys decrypt; new
   key encrypts).
3. Run the key rotation command once it lands.
4. Deploy `CUBEPLEX_AUTH__VAULT_KEY=<new>` only after rotation is verified.

## Runtime Flow

`POST /api/v1/ws/{workspace_id}/conversations/{id}/messages`
→ `RunManager._run_cubepi_path` builds a `cubepi.Agent` via
`create_cubeplex_agent`
→ subscribes to the agent's `AgentEvent` listener channel
→ translates each event via
`cubeplex/agents/stream.py::convert_agent_event_to_sse`
→ `run_manager.cubepi_dict_to_agent_event` emits typed SSE events:
`text_delta`, `reasoning`, `tool_call`, `tool_result`, `usage`, `error`,
`done`.

Full architectural detail: [agent-system-design.md](agent-system-design.md).

## Gotchas

- **Async event loop**: tests use `pytest-asyncio` (`asyncio_mode = "auto"`).
  If you manually create event loops, `nest_asyncio.apply()`.
- **`alembic/versions/`**: auto-generated migration files are excluded from
  ruff / mypy.
- **Config precedence**: env vars override YAML.
