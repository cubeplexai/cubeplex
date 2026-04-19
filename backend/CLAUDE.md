# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

cubebox is an AI Agent System Backend built on native LangGraph with LangChain. The backend exposes a streaming SSE API for executing agent tasks, using LangGraph checkpointer thread state as the single source of truth for message history.

## Repository Structure

```
cubebox/
├── backend/
│   ├── cubebox/          # Main source package
│   │   ├── agents/       # Agent graph factory, schemas, message conversion
│   │   ├── api/          # FastAPI app, routes, exceptions
│   │   ├── llm/          # LLM factory, config, OpenAI-compatible client
│   │   ├── memory/       # Memory manager (short/long-term)
│   │   ├── mcp/          # MCP protocol client
│   │   ├── middleware/    # Agent middleware (sandbox, subagents, skills)
│   │   ├── prompts/      # System prompts (base, sandbox, subagents, skills)
│   │   ├── sandbox/      # Code execution sandbox (base ABC + implementations)
│   │   ├── tools/        # Tool registry + built-in tools
│   │   ├── utils/        # Logging
│   │   └── config.py     # Dynaconf-based config
│   ├── tests/
│   │   ├── e2e/          # E2E tests (primary focus)
│   │   └── conftest.py
│   ├── docs/             # Architecture docs — read before working on features
│   ├── scripts/dev/      # Temporary dev scripts only
│   ├── config.yaml       # Base config
│   ├── config.development.yaml
│   ├── config.production.yaml
│   ├── main.py           # Entry point
│   └── Makefile
└── .kiro/
    ├── specs/            # Feature specs
    └── steering/agent.md # Project rules
```

## Quick Start

```bash
cd backend
make dev-install
export OPENAI_API_KEY=<your-key>
python main.py  # Starts dev server on http://localhost:8000
```

## Commands (run from `backend/`)

```bash
make dev-install       # Install all deps (uv sync --all-extras)
make format            # ruff format + import sort
make lint              # ruff check
make lint-fix          # ruff check --fix
make type-check        # mypy cubebox/
make test              # pytest -s -v
make test-cov          # pytest with HTML coverage
make check             # format + lint + type-check + test (run before committing)
make pre-commit-install
```

Single test file: `uv run pytest tests/e2e/test_agents.py`

## Architecture

**Request flow:** `POST /api/v1/conversations/{id}/messages` → `create_cubebox_agent()` → LangGraph `astream(stream_mode="messages", stream_subgraphs=True)` → SSE stream of typed events (`text_delta`, `reasoning`, `tool_call`, `tool_result`, `error`, `done`)

**Key components:**
- `create_cubebox_agent` (`cubebox/agents/graph.py`) — factory that wires LLM, tools, and middleware (sandbox, subagents, skills) into a LangGraph CompiledStateGraph via `langchain.agents.create_agent()`
- Middleware stack (`cubebox/middleware/`) — `SandboxMiddleware`, `SubAgentMiddleware`, `SkillsMiddleware` each implement `AgentMiddleware` with `tools` and `awrap_model_call()`
- Prompts (`cubebox/prompts/`) — modular system prompts injected by middleware
- `LLMFactory` (`cubebox/llm/factory.py`) — reads `config.yaml` `llm.providers`, supports OpenAI and OpenAI-compatible endpoints
- `ToolRegistry` (`cubebox/tools/registry.py`) — registers `BaseTool` instances (supports built-in `StructuredTool` and MCP tools)
- `MCPManager` (`cubebox/mcp/client.py`) — connects to MCP servers via `langchain-mcp-adapters`, loads tools at startup
- Message history: stored in LangGraph checkpointer thread state (no messages table)
- Config via dynaconf: `ENV_FOR_DYNACONF=development|production`, env var prefix `CUBEBOX_`, e.g. `CUBEBOX_LLM__PROVIDER`

## Auth & RBAC

Identity model: `Organization` → `Workspace` → `Membership` → `User`. One user can belong to many workspaces via memberships; each membership carries a `Role` (`admin` | `member`). All business tables carry `(org_id, workspace_id)` via `OrgScopedMixin`.

**Auth:** fastapi-users with JWT cookie strategy. Auth cookie name is `cubebox_auth`. Register/login endpoints are rate-limited via slowapi.

**CSRF:** double-submit cookie pattern. A `cubebox_csrf` cookie is set on login; mutating requests (POST/PUT/PATCH/DELETE) must echo it in the `X-CSRF-Token` header whenever the `cubebox_auth` cookie is present.

**Workspace scoping:** every business request requires an `X-Workspace-Id` header. The `request_context` dependency resolves it into a `RequestContext` (user + org_id + workspace_id + role). Missing header → 400; workspace not found → 404; not a member → 403.

**Repository layer:** `OrgScopedMixin` + `ScopedRepository[T]` (`cubebox/repositories/base.py`) automatically filter every query by `(org_id, workspace_id)` — structural isolation, not an ACL check bolted on top. New business repositories should subclass `ScopedRepository`.

**Endpoints:**
- `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`
- `GET/POST /api/v1/workspaces`
- `POST /api/v1/workspaces/{ws}/invites` (admin only), `POST /api/v1/workspaces/invites/accept`

**Register bootstrap:** `UserManager.on_after_register` auto-creates a personal Organization (`"<email-local-part>'s Org"`), a Workspace (`"Personal"`), and an Admin Membership for the new user in the same session. If any of these fails, the User row is best-effort deleted before the exception propagates so registration appears atomic to the client. The register response returns `{id, email, default_workspace_id}`.

**Known P1 gaps (flagged `TODO(P2-auth)`):**
- `create_workspace` accepts a client-supplied `org_id` with no org-membership check (P1 has no org-level membership concept yet).
- `request_context` returns 404 before the role check, so an unauthorized workspace id returns 404 rather than 403. Intentional (avoids enumeration of workspace ids) but worth knowing.

## Environment Variables

Required:
- `OPENAI_API_KEY` — OpenAI API key for LLM
- `CUBEBOX_LLM__PROVIDER` — LLM provider (default: openai)

Optional:
- `ENV_FOR_DYNACONF` — Config environment (default: development)
- `CUBEBOX_LOG_LEVEL` — Logging level (default: INFO)

## Database

Migrations are managed with Alembic:
```bash
alembic upgrade head  # Apply pending migrations
alembic revision -m "description"  # Create new migration
```

After modifying SQLModel schemas, use auto-generation: `alembic revision --autogenerate -m "description"`

## Gotchas

- **Async event loop issues**: Tests use `pytest-asyncio` with `asyncio_mode = "auto"`. If you manually create event loops, use `nest_asyncio.apply()`.
- **alembic/versions/**: Auto-generated migration files are excluded from ruff/mypy checks to avoid false lints.
- **Config precedence**: ENV vars override YAML config. Use `CUBEBOX_*` prefix for env overrides.

## Rules

- Read `backend/docs/` before working on any feature
- Temporary scripts go in `backend/scripts/dev/`
- Do not create docs without permission
- All functions require type annotations (mypy strict)
- Line length: 100 chars
- Focus on E2E tests; avoid testing trivial logic
