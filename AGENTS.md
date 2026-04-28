# AGENTS.md

Guidance for AI agent work in this repository.

## Project Overview
- `cubebox` is a full-stack system with:
  - FastAPI + LangGraph backend (streaming agent execution, SSE events)
  - Next.js frontend monorepo with shared TypeScript core package
- Backend and frontend each have additional detailed instructions:
  - `backend/CLAUDE.md`
  - `frontend/CLAUDE.md`

## Repository Layout (high level)
```
cubebox/
├── backend/        # Python API, agents, auth, E2E tests
├── frontend/       # Next.js web app + @cubebox/core
└── .kiro/          # Specs and steering docs
```

## Global Rules
- All functions require type annotations.
- Line length: 100 chars.
- Prefer E2E tests over unit tests.
- Read architecture docs before implementing features.
- Do not create docs without permission.

## Quick Start
- Backend: `cd backend && make dev-install && python main.py` (listens `http://localhost:8000`)
- Frontend: `cd frontend && pnpm install && pnpm dev` (listens `http://localhost:3000`)
- First frontend E2E run: `npx playwright install`

## Backend essentials
- Environment (实际有效的入口):
  - `ENV_FOR_DYNACONF` (optional, default `development`)
  - `CUBEBOX_LLM__PROVIDERS__<PROVIDER>__BASE_URL`
  - `CUBEBOX_LLM__PROVIDERS__<PROVIDER>__API_KEY`
  - `CUBEBOX_LLM__DEFAULT_MODEL`（覆盖 `default_model`，值如 `provider/model-id`）
  - `CUBEBOX_AUTH__JWT_SECRET`
  - `CUBEBOX_AUTH__CSRF_SECRET`
  - `CUBEBOX_REDIS__URL`
  - `CUBEBOX_DATABASE__HOST|PORT|USER|PASSWORD|NAME`
  - `CUBEBOX_SANDBOX__DOMAIN`
  - `CUBEBOX_SANDBOX__IMAGE`
  - `CUBEBOX_SANDBOX__API_KEY`
  - `CUBEBOX_LANGSMITH__KEY`
- 测试环境（当前项目）: `config.test.yaml` 使用
  - `CUBEBOX_E2E_LLM_BASE_URL`
  - `CUBEBOX_E2E_LLM_API_KEY`
  - `CUBEBOX_E2E_LLM_MODEL_ID`
- `backend/.env.example` 示例中给了可直接用的环境变量名；`config.py` 会加载 `backend/.env`、`backend/config.<env>.local.yaml` 作为本地覆盖
- Common commands (`backend/`):
  - `make dev-install`, `make format`, `make lint`, `make lint-fix`
  - `make type-check`, `make test`, `make test-cov`, `make check`
- Core runtime flow:
  - API route posts stream to `create_cubebox_agent()` and LangGraph `astream(...)`
  - SSE event stream includes `text_delta`, `reasoning`, `tool_call`, `tool_result`, `error`, `done`
- Architecture to remember:
  - Agent factory: `cubebox/agents/graph.py`
  - Middleware stack: sandbox / subagents / skills
  - Message history stored in LangGraph checkpointer thread state (no separate messages table)
  - Identity model: Organization → Workspace → Membership → User, with `OrgScopedMixin` enforcement
  - All business routes are workspace-scoped via `/api/v1/ws/{workspace_id}/...`
- E2E caveat:
  - Local E2E requires `backend/.env` and `backend/config.development.local.yaml` (gitignored); copy from main worktree when in worktree environments.
- Database:
  - Alembic for migrations (`alembic upgrade head`, `alembic revision ...`)

## Frontend essentials
- Tech stack: Next.js app router + React + TypeScript + Tailwind + shadcn/ui + Zustand + pnpm workspace.
- Commands (`frontend/`):
  - `pnpm dev`, `pnpm build`, `pnpm start`, `pnpm type-check`, `pnpm test:e2e`
  - Use `pnpm -w` for root/workspace and `pnpm --filter <pkg> ...` for package-level tasks
- Shared package pattern:
  - `@cubebox/core` contains API clients, types, and Zustand stores
  - Core should stay framework-agnostic and type-safe
- Routing/workspace behavior:
  - Auth pages: `(auth)/login`, `(auth)/register`
  - App pages: `(app)/w/[wsId]/...`
  - Active workspace is URL segment `[wsId]`
  - `ApiClient.setWorkspaceId(wsId)` rewrites scoped paths to `/api/v1/ws/{wsId}/...`
  - Auth routes and workspace list routes remain unscoped
- CSRF:
  - `ApiClient` adds `X-CSRF-Token` from `cubebox_csrf` cookie for non-GET requests
- SSE proxy:
  - Route `app/api/v1/ws/[wsId]/conversations/[id]/messages/route.ts` forwards credentials and headers to backend
- Common gotcha:
  - Core package must be built before web can consume API/type changes
  - Use `npx shadcn-ui@latest` from `packages/web/` when adding components
