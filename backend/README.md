# CubePlex - Agent System Backend

AI Agent System Backend built on cubepi, a Python-native agent runtime.

## Project Structure

```
backend/
├── cubeplex/                      # Main source package
│   ├── agents/                   # Agent graph factory, schemas, message conversion
│   ├── api/                      # FastAPI app, routes, exceptions
│   ├── llm/                      # LLM factory, config, OpenAI-compatible client
│   ├── memory/                   # Memory manager (short/long-term)
│   ├── mcp/                      # MCP protocol client
│   ├── middleware/                # Agent middleware (sandbox, subagents, skills)
│   ├── prompts/                  # System prompts (base, sandbox, subagents, skills)
│   ├── sandbox/                  # Code execution sandbox (base ABC + implementations)
│   ├── tools/                    # Tool registry + built-in tools
│   ├── utils/                    # Logging
│   └── config.py                 # Dynaconf-based config
├── tests/
│   ├── unit/                     # Unit tests
│   └── e2e/                      # E2E tests
├── config.yaml                   # Base configuration
├── config.development.yaml       # Development overrides
├── config.production.yaml        # Production overrides
├── main.py                       # Application entry point
├── pyproject.toml                # Project metadata and dependencies
├── Makefile                      # Dev commands
└── README.md                     # This file
```

## Setup

### Prerequisites

- Python 3.12+
- uv (Python package manager)

### Installation

```bash
cd backend
make dev-install   # or: uv sync --all-extras
```

Set required environment variables:

```bash
export OPENAI_API_KEY="your-api-key"
export ENV_FOR_DYNACONF="development"
```

## Running the Application

### Development

```bash
python main.py
```

The API will be available at `http://localhost:8000`

### Production

```bash
ENV_FOR_DYNACONF=production python main.py
```

## Configuration

Configuration is managed using dynaconf with YAML files:

- `config.yaml` - Base configuration
- `config.development.yaml` - Development overrides
- `config.production.yaml` - Production overrides
- Environment variables with `CUBEPLEX_` prefix

Example:

```bash
export CUBEPLEX_DEBUG=true
export CUBEPLEX_LLM__PROVIDER=anthropic
```

## Architecture Overview

### Core Components

1. **Agent Graph Factory** - `create_cubeplex_agent()` wires the cubepi Provider, tools, and
   middleware stack into a `cubepi.Agent`
2. **Middleware Stack** - SandboxMiddleware, SubAgentMiddleware, SkillsMiddleware
3. **LLM Integration** - Multi-provider LLM support (OpenAI, OpenAI-compatible)
4. **Tool Registry** - Built-in and MCP tools management
5. **Memory System** - Short-term and long-term memory
6. **Sandbox** - Isolated code execution (OpenSandbox + LocalSandbox for dev)
7. **MCP Client** - Model Context Protocol integration
8. **Message History** - persisted by cubepi's `PostgresCheckpointer` (HASH-partitioned 64 ways
   on `thread_id`); no separate messages table

### Key Features

- Multi-agent coordination with subagent streaming
- Modular prompt system injected via middleware
- Code execution in isolated sandboxes
- SSE streaming API with typed events (text_delta, reasoning, tool_call, tool_result, error, done)
- MCP protocol support for tool integration
- Dependency injection for testability (checkpointer_factory, sandbox_factory)

## Development

```bash
make format        # ruff format + import sort
make lint          # ruff check
make type-check    # mypy cubeplex/
make test          # pytest -s -v
make check         # format + lint + type-check + test
```

## Dependencies

Key dependencies:

- **FastAPI** - Web framework
- **cubepi** - In-house Python-native agent runtime (provider, middleware, checkpointer)
- **Pydantic** - Data validation
- **Dynaconf** - Configuration management
- **Loguru** - Logging

See `pyproject.toml` for complete dependency list.
