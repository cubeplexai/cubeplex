# Backend Quick Reference

Reference content (env vars, commands, file layout). For workflow discipline,
hard rules, and skill triggers, see the root [AGENTS.md](../../AGENTS.md).
For prompt cache discipline, auth, and architecture deep dives, see the
focused docs alongside this one.

## Repository Structure

```
backend/
├── cubebox/
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
make type-check        # mypy cubebox/
make test              # pytest -s -v
make test-cov          # pytest with HTML coverage
make check             # format + lint + type-check + test (pre-commit sweep)
make pre-commit-install
```

Single test file: `uv run pytest tests/e2e/test_agents.py`.

## Environment Variables

The actual entry points loaded by `config.py`:

- `ENV_FOR_DYNACONF` (optional, default `development`)
- `CUBEBOX_LLM__PROVIDERS__<PROVIDER>__BASE_URL`
- `CUBEBOX_LLM__PROVIDERS__<PROVIDER>__API_KEY`
- `CUBEBOX_LLM__DEFAULT_MODEL` (overrides `default_model`; format
  `provider/model-id`)
- `CUBEBOX_AUTH__JWT_SECRET`
- `CUBEBOX_AUTH__CSRF_SECRET`
- `CUBEBOX_AUTH__VAULT_KEY` — comma-separated Fernet keys; first encrypts,
  all decrypt. Required once Credential Vault is enabled.
- `CUBEBOX_REDIS__URL`
- `CUBEBOX_DATABASE__HOST|PORT|USER|PASSWORD|NAME`
- `CUBEBOX_SANDBOX__DOMAIN`
- `CUBEBOX_SANDBOX__IMAGE`
- `CUBEBOX_SANDBOX__API_KEY`
- `CUBEBOX_LOG_LEVEL` (default `INFO`)

Test env (`config.test.yaml`):

- `CUBEBOX_E2E_LLM_BASE_URL`
- `CUBEBOX_E2E_LLM_API_KEY`
- `CUBEBOX_E2E_LLM_MODEL_ID`

`backend/.env.example` shows ready-to-use names. `config.py` also loads
`backend/.env` and `backend/config.<env>.local.yaml` as local overrides.

Env vars override YAML config (use the `CUBEBOX_` prefix).

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
  `CUBEBOX_E2E_LLM_*`).
- `backend/config.development.local.yaml` — machine-specific overrides
  (LLM endpoint URL, sandbox domain).

**Copy both from a working machine; do not recreate from scratch.** With
them in place, `uv run pytest tests/e2e/` runs cleanly with no
command-line env vars.

In a fresh worktree, copy them in before the first test run:

```bash
cp /path/to/main/backend/.env backend/.env
cp /path/to/main/backend/config.development.local.yaml \
   backend/config.development.local.yaml
```

Missing files surface as:
- `DynaconfFormatError: Dynaconf can't interpolate variable because
  'CUBEBOX_E2E_LLM_*'` at config load, or
- Quiet `'error' == 'text_delta'` SSE assertion failures inside agent tests
  (agent crashes mid-stream on lazy interpolation, emits an error event).

## Database

- Alembic migrations: `alembic upgrade head`, `alembic revision -m "..."`.
- After SQLModel schema changes: `alembic revision --autogenerate -m "..."`.
- Pulling this branch into an existing checkout: drop and recreate the local
  DB before running migrations — the alembic baseline is incompatible with
  prior revisions.

### Short prefixed public IDs

All business tables use short prefixed string PKs (e.g.
`conv-V1StGXR8Z5jdHi`, ≤20 chars). `cubebox.models.public_id.generate_public_id`
packs a 41-bit ms timestamp + 42-bit random into 14 base62 chars — sortable
at ms granularity, multi-instance safe.

To add a new business table: define a `PREFIX_<NAME>` constant in
`public_id.py`, then on the `id` column use
`default_factory=lambda: generate_public_id(PREFIX_<NAME>)` with
`max_length=20`. Pure association tables (composite PK) skip the prefix.

## Vault Key Rotation

1. Generate a new Fernet key.
2. Deploy `CUBEBOX_AUTH__VAULT_KEY=<new>,<old>` (both keys decrypt; new
   key encrypts).
3. Run the key rotation command once it lands.
4. Deploy `CUBEBOX_AUTH__VAULT_KEY=<new>` only after rotation is verified.

## Runtime Flow

`POST /api/v1/ws/{workspace_id}/conversations/{id}/messages`
→ `RunManager._run_cubepi_path` builds a `cubepi.Agent` via
`create_cubebox_agent`
→ subscribes to the agent's `AgentEvent` listener channel
→ translates each event via
`cubebox/agents/stream.py::convert_agent_event_to_sse`
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
