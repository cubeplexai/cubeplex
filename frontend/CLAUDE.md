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

首次运行 E2E 测试前需要安装 Playwright 浏览器：
```bash
npx playwright install
```

## Commands

All commands run from root `frontend/` directory:

```bash
pnpm dev           # Start development server (web package)
pnpm build         # Build web app for production
pnpm start         # Start production server
pnpm type-check    # Run TypeScript type checking for all packages
pnpm test:e2e      # Run Playwright E2E tests
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

## Auth & Workspace Model

**Route structure:** `(auth)/{login,register}` for unauthenticated pages; `(app)/{workspaces, w/[wsId]/...}` for authenticated pages. `/` is a server redirect: logged-in → first workspace, else `/login`.

**Proxy (`proxy.ts`):** checks for the `cubebox_auth` cookie. Unauthenticated hits to `/w/*` or `/workspaces` redirect to `/login?next=<path>`. Logged-in hits to `/login` or `/register` redirect to `/`.

**Active workspace:** the URL segment `[wsId]` is the single source of truth. `useWorkspaceContext()` (in `(app)` tree) reads it. The `ApiClient` instance each page creates via `createApiClient('')` calls `client.setWorkspaceId(wsId)`, which automatically rewrites scoped paths — `/api/v1/conversations/...` becomes `/api/v1/ws/{wsId}/conversations/...`. Paths under `/api/v1/auth/` and `/api/v1/workspaces` are workspace-neutral and not rewritten. For browser-direct loads (`<img>`, `<iframe>`, `<a href>`, pdf.js) use the URL builders in `components/panel/artifact/previewUtils.ts` (`buildPreviewUrl`, `buildDownloadUrl`) or call `client.resolvePath(...)`.

**CSRF:** double-submit pattern. `ApiClient` reads `cubebox_csrf` from `document.cookie` and adds `X-CSRF-Token` on every non-GET. The backend seeds the cookie on login.

**Stores:**
- `authStore` — `{id, email}` of the current user, or `null`. Populated by `loadMe` on `(app)` mount.
- `workspaceStore` — list of the user's workspaces + `create(client, name)` (reuses the first workspace's `org_id`, M1 assumption: one user = one org).

**SSE proxy:** the Next.js route handler at `app/api/v1/ws/[wsId]/conversations/[id]/messages/route.ts` forwards `cookie`, `X-CSRF-Token`, and `x-user-id` to the backend. Workspace scoping rides in the URL path, not a header.

**Known one-user-one-org assumption:** `workspaceStore.create` reads `workspaces[0].org_id`. When multi-org-per-user ships (P2), this must take an explicit org id.

## Common Gotchas

- **pnpm workspace**: Always use `pnpm` not `npm`. Use `pnpm -w` for root, `pnpm --filter <pkg>` for single package.
- **Core build**: Core is a TypeScript lib, not bundled. Must build with `tsc` before web can use changes.
- **shadcn/ui**: Run `npx shadcn-ui@latest` from `packages/web/` directory.
- **Import aliases**: Check `tsconfig.json` in each package for path mappings (likely `@` for `src/`).
