# CLAUDE.md

Frontend monorepo for cubebox — pnpm workspace with Next.js web app and shared core library.

## Project Overview

cubebox-frontend is a Next.js web application with a shared TypeScript library. It uses modern tooling: React 19, Tailwind CSS 4, shadcn/ui components, and Zustand for state management.

## Repository Structure

```
frontend/
├── packages/
│   ├── core/          # Shared library (@cubebox/core)
│   │   ├── src/
│   │   │   ├── api/   # API client abstractions
│   │   │   ├── stores/ # Zustand stores
│   │   │   └── types/  # Shared TypeScript types
│   │   ├── package.json
│   │   └── tsconfig.json
│   └── web/           # Next.js application
│       ├── app/       # App router pages and layouts
│       ├── components/ # React components
│       ├── hooks/     # Custom React hooks
│       ├── lib/       # Utilities and helpers
│       ├── public/    # Static assets
│       ├── package.json
│       └── tsconfig.json
├── docs/              # Shared documentation
├── package.json       # Workspace root
└── pnpm-workspace.yaml
```

## Quick Start

```bash
pnpm install
pnpm dev      # Starts Next.js dev server on http://localhost:3000
```

## Commands

All commands run from root `frontend/` directory:

```bash
pnpm dev           # Start development server (web package)
pnpm build         # Build web app for production
pnpm start         # Start production server
pnpm type-check    # Run TypeScript type checking for all packages
```

Single package commands:

```bash
pnpm -w -r run build  # Build all packages (including core)
pnpm --filter web dev # Run dev for web package only
pnpm --filter @cubebox/core type-check
```

## Architecture

**Tech Stack:**
- **Frontend**: Next.js 16, React 19, TypeScript 5
- **Styling**: Tailwind CSS 4, shadcn/ui components (via components.json)
- **State**: Zustand (web and core)
- **Package Manager**: pnpm with workspace

**Package Structure:**
- `@cubebox/core` — TypeScript library exporting:
  - `./api` — API client code
  - `./stores` — Zustand stores
  - `./types` — Shared TypeScript types
- `web` — Next.js app that consumes `@cubebox/core`

**Data Flow**: Components → Zustand stores (in core) → API client (in core) → Backend

## Development Workflow

1. **Shared code**: Add to `packages/core/src/`
2. **Building core**: `pnpm --filter @cubebox/core build` (compiles TypeScript to `dist/`)
3. **Components**: Use shadcn/ui via `npx shadcn-ui@latest add <component>`
4. **Type safety**: Always use TypeScript; `pnpm type-check` before committing

## Rules

- Keep core package type-safe and framework-agnostic
- Use shadcn/ui for UI components; don't reinvent wheels
- Zustand stores in core; component state in React hooks
- Export from `packages/core/src/index.ts` for public API
- Line length: 100 chars
- Type annotations required (strict TypeScript)

## Common Gotchas

- **pnpm workspace**: Always use `pnpm` not `npm`. Use `pnpm -w` for root, `pnpm --filter <pkg>` for single package.
- **Core build**: Core is a TypeScript lib, not bundled. Must build with `tsc` before web can use changes.
- **shadcn/ui**: Run `npx shadcn-ui@latest` from `packages/web/` directory.
- **Import aliases**: Check `tsconfig.json` in each package for path mappings (likely `@` for `src/`).
