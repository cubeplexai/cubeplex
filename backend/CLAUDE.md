# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

cubebox is an AI Agent System Backend built on the DeepAgents framework with LangChain and LangGraph. The backend exposes a streaming SSE API for executing agent tasks.

## Repository Structure

```
cubebox/
├── backend/
│   ├── cubebox/          # Main source package
│   │   ├── agents/       # Agent executor, schemas, config
│   │   ├── api/          # FastAPI app, routes, exceptions
│   │   ├── llm/          # LLM factory, config, OpenAI-compatible client
│   │   ├── memory/       # Memory manager (short/long-term)
│   │   ├── mcp/          # MCP protocol client
│   │   ├── sandbox/      # Code execution sandbox
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

**Request flow:** `POST /api/v1/agents/run` → `DeepAgentExecutor.stream()` → LangGraph agent → SSE stream of typed events (`chain_start`, `llm_start`, `llm_end`, `tool_start`, `tool_end`, `chain_end`, `error`, `done`)

**Key components:**
- `DeepAgentExecutor` (`cubebox/agents/executor.py`) — creates LLM via `LLMFactory`, loads tools from `ToolRegistry`, runs LangGraph agent, yields typed `AgentEvent` subclasses
- `LLMFactory` (`cubebox/llm/factory.py`) — reads `config.yaml` `llm.providers`, supports OpenAI and OpenAI-compatible endpoints
- `ToolRegistry` (`cubebox/tools/registry.py`) — registers `BaseTool` instances (supports built-in `StructuredTool` and MCP tools)
- `MCPManager` (`cubebox/mcp/client.py`) — connects to MCP servers via `langchain-mcp-adapters`, loads tools at startup
- Config via dynaconf: `ENV_FOR_DYNACONF=development|production`, env var prefix `CUBEBOX_`, e.g. `CUBEBOX_LLM__PROVIDER`

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
