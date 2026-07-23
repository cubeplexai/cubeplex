# AGENTS.md

Rules and index for AI agents working in cubeplex. Auto-loaded from any
subdir. When a pointer exists, **read the linked doc before acting**.

If you only read one section: **Workflow Discipline** + **Critical Subsystems**.

---

## What This Repo Is

Full-stack agent platform.

| Path | Role |
|---|---|
| `backend/` | FastAPI + cubepi streaming runtime, SSE API, Postgres history, MCP |
| `frontend/` | Next.js + React 19 monorepo (`packages/web`, `@cubeplex/core`) |
| `docs/` | Cross-cutting docs; all specs/plans/notes under `docs/dev/{specs,plans,notes}` |
| `backend/docs/`, `frontend/docs/` | Area reference (see Critical Subsystems) |
| `scripts/` | Worktree provisioning, dev helpers |

---

## Workflow Discipline (non-negotiable)

1. **Worktree first** for non-trivial work. From main repo root:
   `./scripts/new-worktree feat/YYYY-MM-DD-<name>`
2. **Read `.worktree.env` on entry.** Never assume ports 8000/3000.
   [docs/worktrees.md](docs/worktrees.md)
3. **E2E over mocks.** "E2E" = business-flow test of a real invariant, not a
   DOM snapshot. [docs/testing.md](docs/testing.md)
4. **Plan before code** for multi-step work → `/feature-workflow`.
5. **Brainstorm before design** for new features → `/feature-workflow`.
6. **One concern per PR.** Choose spec/plan/code split before pushing.
7. **PR codex review loop** after push:
   [`pr-codex-review-loop`](.claude/skills/pr-codex-review-loop/SKILL.md)
   — push → poll → fix → reply every comment → re-tag `@codex` until clean.
8. **Stay on the feature branch** during multi-task work. No auto-switch to
   main or mid-execution merges.
9. **Verify before claiming done.** Run the command; paste evidence. Pre-push
   already runs `make check-ci` — don't run it by hand first.
10. **Act on valid review feedback** — push the fix; don't ask permission.
11. **Incremental tests during dev** — only the changed module; full suite at
    pre-PR.
12. **Plain language in docs/specs.** Say what happens and why. No invented
    jargon.
13. **Docs ship with the code.** User-facing change → update matching
    `docs/site/docs/` page in the **same PR**. New subsystem → new page (only
    sanctioned new-doc exception). Missing screenshot → placeholder, never a
    silent gap. Mapping:
    [docs/dev/plans/2026-06-23-docs-overhaul.md](docs/dev/plans/2026-06-23-docs-overhaul.md)
14. **PR titles** are a brief description only. No "Codex-generated", "[WIP]",
    or other static prefixes.
15. **Branch names**: `feat/YYYY-MM-DD-<slug>` or `fix/YYYY-MM-DD-<slug>`.
    Don't invent prefixes (`bugfix/`, `codex/`, …) unless asked.
16. **Conventional commits**: `type(scope): summary` (imperative summary).

---

## Hard Code Rules

- **Types everywhere.** mypy strict (backend), strict TS (frontend).
  **Code line length: 100 chars** (ruff/formatter). Does **not** apply to
  prose, markdown, or agent chat — only source code.
- **tz-aware time only.** No naive `datetime` across DB or service API.
  `datetime.now(UTC)`; serialize with `utc_isoformat()`. See
  [backend/docs/quick-reference.md](backend/docs/quick-reference.md) →
  "Datetime columns".
- **New business table → public ID prefix** in
  `backend/cubeplex/models/public_id.py`. Same quick-reference → "Short
  prefixed public IDs".
- **Migrations:** `alembic revision --autogenerate -m "..."`. No hand-edits,
  no skip autogen. Head conflict after rebase → re-parent the branch's first
  migration; never `alembic merge heads`.
- **Deps:** `uv add` (backend), `pnpm add` (frontend). Don't hand-edit
  `pyproject.toml` / `package.json`.
- **No new docs without permission.** Prefer updating an existing doc.
- **Scope-isolated APIs.** Workspace (`/api/v1/ws/{ws}/...`) and org-admin
  (`/api/v1/admin/...`) stay separate handlers even if logic matches. Reuse
  one layer down (services/repos), never at the route layer. Smell:
  `?scope=` or a `role` body field.
- **Scope-isolated pages.** One Next route/page file per user-facing page;
  modules (`<List>`, `<DetailPanel>`, …) are the reuse boundary. Smell:
  `mode?: 'admin' | 'workspace'` on a page component.

---

## Testing — non-negotiables

Full rules: **[docs/testing.md](docs/testing.md)**. TDD judgment:
**`/cubeplex-tdd`**.

- Tests protect a business invariant or contract. DOM-presence / element-count
  tests get deleted on sight.
- Placement:
  - `backend/tests/unit/` — pure, in-process
  - `tests/integration/` — multi-module, no external systems
  - `tests/e2e/` — Postgres / Redis / S3 / FastAPI app
  - **If it opens `AsyncSession`, runs alembic, or hits the app → e2e.**
    Misplacement breaks `make check-ci`.
- Real services at internal boundaries; mock only the outermost external the
  test isn't about. Real-LLM: `@pytest.mark.real_llm`.

---

## Critical Subsystems — Read Before Touching

Wrong changes here are expensive (cache bills, auth, data corruption).

| If you're modifying… | Read first |
|---|---|
| LLM path, system prompt, tools, memory, middleware, message replay | [backend/docs/prompt-cache-discipline.md](backend/docs/prompt-cache-discipline.md) |
| Auth, registration, org/workspace bootstrap, RBAC | [backend/docs/auth.md](backend/docs/auth.md) |
| Agent middleware, request flow, event types | [backend/docs/agent-system-design.md](backend/docs/agent-system-design.md) |
| MCP catalog / OAuth / connector install | [backend/docs/mcp_catalog_oauth.md](backend/docs/mcp_catalog_oauth.md) |
| Frontend auth, CSRF, SSE proxy, deploy mode | [frontend/docs/auth-and-sse.md](frontend/docs/auth-and-sse.md) |
| Worktree provisioning, rebase / migration drift | [docs/worktrees.md](docs/worktrees.md) |
| Tests — writing, placing, reviewing | [docs/testing.md](docs/testing.md) |

Env vars, commands, layouts (reference only):
[backend/docs/quick-reference.md](backend/docs/quick-reference.md),
[frontend/docs/quick-reference.md](frontend/docs/quick-reference.md).

---

## Skill Index

Load on demand when the situation matches.

**Process**

| Skill | Trigger |
|---|---|
| `/feature-workflow` | Start of a feature / non-trivial change |
| `/cubeplex-tdd` | Feature, business logic, durable bug-fix contracts |
| `/debug-cubeplex` | Bug or test failure — **before** proposing fixes |
| `/pr-codex-review-loop` | After pushing a PR |

**Domains**

| Skill | Trigger |
|---|---|
| `cubepi` | Agents on CubePi — API, providers, tools, middleware, MCP, HITL |
| `cubepi-trace` | Debug a cubepi run (spans, tool I/O, tokens, cache) |
| `web-design-guidelines` | UI a11y / design review |
| `playwright-cli` | Playwright tests |

Escape hatch: `/codex:rescue` — stuck; want a second opinion.

---

## Quick Start

```bash
# Backend
cd backend && make dev-install && python main.py   # → :8000

# Frontend
cd frontend && pnpm install && pnpm dev            # → :3000

# First-time frontend E2E
npx playwright install
```

Local E2E needs `backend/.env` + `backend/config.development.local.yaml`
(gitignored — copy from a working machine). Worktrees need both before the
first test run. Details: [backend/docs/quick-reference.md](backend/docs/quick-reference.md).

---

## Auth & Scoping

`Organization → Workspace → Membership → User`.

Business routes are workspace-scoped: `/api/v1/ws/{workspace_id}/...`.
Repos enforce `(org_id, workspace_id)` via `OrgScopedMixin` +
`ScopedRepository[T]` — not bolted-on ACL.

| Mode | Behavior |
|---|---|
| `single_tenant` | OSS; one shared org |
| `multi_tenant` | Cloud; per-user org |

Bootstrap diverges at registration. Roles, operator CLI, system endpoints:
[backend/docs/auth.md](backend/docs/auth.md).

---

## Common Gotchas

- **Subagent CWD**: not inherited. Pin absolute path; tell them to
  `cat .worktree.env` first.
- **pnpm**, never npm, in frontend.
- **`@cubeplex/core` must build** before web sees API/type changes.
- **shadcn/ui**: `npx shadcn-ui@latest` from `packages/web/`.
- **SSE compress**: Next rewrites buffer SSE if compress is on → keep
  `compress: false`.
- **Worktree test DB**: plain `uv run pytest` is safe (conftest routes to
  `cubeplex_test_<slug>`). S3 tests need rustfs on `:9000`.
  [docs/worktrees.md](docs/worktrees.md) → "Running tests in a worktree".

---

## Capturing Noisy Command Output

Pipe pytest / lint / build / mypy / alembic through `tee` into `tmp/<task>.log`
(gitignored), then tail the summary:

```bash
uv run pytest tests/unit/test_foo.py --no-cov 2>&1 | tee tmp/foo.log | tail -3
```

Green tail → done. Red tail → full traceback is in the log; grep it instead of
re-running.

---

## Authoring Conventions

- One-shot scripts → `backend/scripts/dev/`.
- Specs / plans / notes under `docs/dev/`:
  - `specs/YYYY-MM-DD-<slug>-design.md`
  - `plans/YYYY-MM-DD-<slug>.md`
  - `notes/YYYY-MM-DD-<slug>.md`
  Frozen snapshots — rebase content, don't rewrite history.
- Comment only when the *why* is non-obvious; never multi-paragraph.
- **Surgical changes.** Every line traces to the request. Match file style;
  no drive-by refactors. Remove only imports/vars your change orphaned; flag
  other dead code, don't delete it.
- No backwards-compat shims unless asked — project not publicly shipped; cut
  over cleanly.
