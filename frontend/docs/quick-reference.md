# Frontend Quick Reference

Reference content for the cubeplex frontend monorepo. For workflow
discipline, hard rules, and skill triggers, see the root
[AGENTS.md](../../AGENTS.md). For auth, CSRF, SSE, and deployment-mode
behavior, see [auth-and-sse.md](auth-and-sse.md).

## Repository Structure

```
frontend/
├── packages/
│   ├── core/             # @cubeplex/core — shared TS library
│   │   ├── src/
│   │   │   ├── api/      # API client abstractions
│   │   │   ├── stores/   # Zustand stores
│   │   │   └── types/    # Shared TypeScript types
│   │   └── tsconfig.json
│   └── web/              # Next.js app
│       ├── app/          # App router pages and layouts
│       ├── components/   # React components
│       ├── hooks/        # Custom React hooks
│       ├── lib/          # Utilities
│       └── public/
├── docs/                 # Frontend docs
├── package.json          # Workspace root
└── pnpm-workspace.yaml
```

## Tech Stack

- Next.js 16, React 19, TypeScript 5 (strict).
- Tailwind CSS 4, shadcn/ui (via `components.json`).
- Zustand for state (in both `web` and `core`).
- pnpm workspace.

## Commands (run from `frontend/`)

```bash
pnpm dev          # Start Next.js dev server (web package)
pnpm build        # Build web app
pnpm start        # Start production server
pnpm type-check   # Type check all packages
pnpm test:e2e     # Run Playwright E2E
```

Single-package commands:

```bash
pnpm -w -r run build               # Build all packages (including core)
pnpm --filter web dev              # Web only
pnpm --filter @cubeplex/core type-check
```

## Package Structure

- `@cubeplex/core` — TypeScript library exporting:
  - `./api` — API client.
  - `./stores` — Zustand stores.
  - `./types` — Shared types.
- `web` — Next.js app, consumes `@cubeplex/core`.

**Data flow:** components → Zustand stores (in core) → API client (in core)
→ backend.

## Development Workflow

1. **Shared code** lives in `packages/core/src/`.
2. **Build core** with `pnpm --filter @cubeplex/core build` — compiles
   TypeScript to `dist/`. **Must build before web sees API/type changes.**
3. **Add components** with `npx shadcn-ui@latest add <component>`, run
   from `packages/web/`.
4. **Type safety**: `pnpm type-check` before committing.
5. **Export** public surface from `packages/core/src/index.ts`.

## Gotchas

- **pnpm not npm.** Use `pnpm -w` for root, `pnpm --filter <pkg>` for a
  single package.
- **Core is a TS lib, not bundled.** Must `tsc` before web can consume
  changes.
- **shadcn/ui** runs from `packages/web/`, not root.
- **Import aliases**: each package's `tsconfig.json` defines path
  mappings (typically `@` for `src/`).
- **First-time E2E**: `npx playwright install` once per machine.
