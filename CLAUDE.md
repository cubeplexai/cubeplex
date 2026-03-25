# CLAUDE.md

cubebox is an AI Agent System with a full-stack architecture: Python FastAPI backend + Next.js frontend.

## Project Overview

- **Backend** (`/backend`): FastAPI streaming API for agent execution, LangGraph orchestration, LLM integration
- **Frontend** (`/frontend`): Next.js web app with TypeScript library (monorepo with pnpm)

See `backend/CLAUDE.md` and `frontend/CLAUDE.md` for component-specific guidance.

## Repository Structure

```
cubebox/
├── backend/           # FastAPI backend (see backend/CLAUDE.md)
│   ├── cubebox/       # Source package
│   ├── tests/         # E2E tests
│   ├── main.py
│   ├── Makefile
│   └── CLAUDE.md
├── frontend/          # Next.js frontend (see frontend/CLAUDE.md)
│   ├── packages/
│   │   ├── core/      # Shared TypeScript library
│   │   └── web/       # Next.js app
│   └── CLAUDE.md
├── .kiro/
│   ├── specs/         # Feature specifications
│   └── steering/agent.md
└── CLAUDE.md          # This file
```

## Quick Setup

```bash
# Backend (Python 3.12+)
cd backend
make dev-install
export OPENAI_API_KEY=<your-key>
python main.py

# Frontend (Node 18+)
cd frontend
pnpm install
pnpm dev
```

**Backend**: http://localhost:8000
**Frontend**: http://localhost:3000

## Component-Specific Guides

- **Backend developers**: Read `backend/CLAUDE.md` for FastAPI, agent execution, LLM config
- **Frontend developers**: Read `frontend/CLAUDE.md` for Next.js, TypeScript, pnpm workspace

## Rules (Project-Wide)

- All functions require type annotations
- Line length: 100 chars
- Focus on E2E tests
- Read architecture docs before working on features
- Do not create docs without permission
