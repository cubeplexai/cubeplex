# AGENTS.md

Guidance for AI agents working in cubeplex. Auto-loaded on every session, from
any subdir. This file holds the **rules and the index**; specifics live in the
linked docs — when a pointer exists, read the doc before acting, don't guess.

If you only read one section, read **Workflow Discipline** and **Critical
Subsystems**.

---

## What This Repo Is

cubeplex is a full-stack agent platform.

- `backend/` — FastAPI + cubepi streaming agent runtime, SSE API, Postgres
  message history, MCP tool integration.
- `frontend/` — Next.js + React 19 monorepo (`packages/web` + shared
  `@cubeplex/core`).
- `docs/` — cross-cutting documentation, including
  `docs/dev/{specs,plans,notes}` for **all** feature specs, multi-step
  plans, and engineering notes.
- `backend/docs/`, `frontend/docs/` — area-specific reference and
  architecture deep dives. Indexed in "Critical Subsystems" below.
- `scripts/` — worktree provisioning, dev helpers.

---

## Workflow Discipline (non-negotiable)

1. **Worktree before spec/code** for any non-trivial feature. Run
   `./scripts/new-worktree feat/YYYY-MM-DD-<name>` from the **main repo
   root**.
2. **Read `.worktree.env` first** on entering a worktree. Never assume
   ports 8000 / 3000. See [docs/worktrees.md](docs/worktrees.md).
3. **E2E priority over mocks/units.** "E2E" = a business-flow test that
   exercises a real user/system invariant, not a DOM snapshot. Full rules:
   [docs/testing.md](docs/testing.md).
4. **Plan before code** for multi-step work. Use `/feature-workflow` skill.
5. **Brainstorm before designing** features. Use `/feature-workflow` skill.
6. **One concern per PR.** Decide spec/plan/code split (1 PR vs N) **before**
   pushing, based on coupling and review cost.
7. **PR codex review loop** — automated by the
   [`pr-codex-review-loop`](.claude/skills/pr-codex-review-loop/SKILL.md)
   skill: push → poll → fix → reply to every comment → re-tag `@codex` →
   repeat until clean.
8. **Branch discipline.** During multi-task execution, stay on the feature
   branch. Never auto-switch to main or initiate merges mid-execution.
9. **Verify before claiming done.** Run the actual command, paste evidence.
   No "should work now" without output. CI-equivalent `make check-ci` is gated
   automatically by the pre-push hook — don't run it by hand first.
10. **Act on review feedback directly** once you've confirmed it's valid —
    push the fix as a follow-up commit; don't ask permission first.
11. **Incremental testing during dev**: run only changed-module tests per
    task; reserve the full suite for the pre-PR sweep.
12. **Plain language in docs/specs.** Don't invent abstract jargon. Say what
    literally happens + why, in concrete words.
13. **Docs ship with the code.** Any user-facing change (route, header, enum,
    default, config key, UI flow, role, CLI/slash command) updates the matching
    `docs/site/docs/` page in the **same PR**; new subsystems get a new page
    (the one sanctioned new-doc exception). Missing screenshot → placeholder,
    never a silent gap. Format + code-area→page mapping:
    [docs/dev/plans/2026-06-23-docs-overhaul.md](docs/dev/plans/2026-06-23-docs-overhaul.md).
14. **PR titles: a brief description, nothing else.** Never use
    "Codex-generated", "[WIP]", or any other static prefixes.
15. **Branch names follow repo history.** Prefer `feat/YYYY-MM-DD-<slug>` or
    `fix/YYYY-MM-DD-<slug>` for new branches so names stay consistent with the
    existing pattern. Do not introduce `bugfix/`, `codex/`, or other new
    prefixes unless the user explicitly asks for them.
16. **Commit messages use conventional commits.** Prefer
    `type(scope): summary` for commit subjects, with a short imperative
    summary and an optional scope.

---

## Hard Code Rules

- **Type annotations everywhere.** mypy strict in backend, strict TS in
  frontend. Line length: 100 chars.
- **Time columns are tz-aware; no naive `datetime` ever crosses the DB or
  service-API boundary.** Write `datetime.now(UTC)`; serialize DB datetimes
  via `utc_isoformat()`. Column definition + the alembic `postgresql_using`
  migration trap: [backend/docs/quick-reference.md](backend/docs/quick-reference.md)
  → "Datetime columns".
- **New business table → public ID prefix** in
  `backend/cubeplex/models/public_id.py`. How-to:
  [backend/docs/quick-reference.md](backend/docs/quick-reference.md)
  → "Short prefixed public IDs".
- **Migrations: `alembic revision --autogenerate -m "..."`.** Do not hand-edit
  migration files; do not skip autogen. Head conflict after rebase →
  re-parent the branch's first migration, never `alembic merge heads`
  (see quick-reference → "Migration head conflicts").
- **Dependencies: `uv add <pkg>` (backend), `pnpm add` (frontend).** Don't
  hand-edit `pyproject.toml` / `package.json`.
- **Do not create new docs without permission.** Update an existing doc when
  possible.
- **Scope-isolated APIs.** Workspace routes (`/api/v1/ws/{ws}/...`) and
  org-admin routes (`/api/v1/admin/...`) are separate handlers, even when the
  logic is identical. Reuse goes one layer down (services, repositories) —
  never at the route layer. The smell: adding `?scope=` or a `role` body field.
- **Scope-isolated pages.** Each user-facing page gets its own Next route and
  page file; modules (`<List>`, `<DetailPanel>`, …) are the reuse boundary.
  The smell: a `mode?: 'admin' | 'workspace'` prop on a page component.

---

## Testing — the non-negotiables

Full discipline: **[docs/testing.md](docs/testing.md)**. TDD loop + when-to-TDD
judgment: **`/cubeplex-tdd`**. What you can't get wrong:

- A test must protect a business invariant or contract. DOM-presence /
  element-count tests get deleted on sight.
- Directory choice: `backend/tests/unit/` (pure, in-process),
  `tests/integration/` (multi-module, no external systems), `tests/e2e/`
  (touches Postgres / Redis / S3 / the FastAPI app). **If it opens an
  `AsyncSession`, runs alembic, or hits the app, it's e2e, full stop** —
  misplacing it breaks `make check-ci`.
- Real services at internal boundaries; mock only the outermost external the
  test isn't about. Real-LLM tests are tagged `@pytest.mark.real_llm`.

---

## Critical Subsystems — Read Before Touching

If you're modifying any of the following, **read the linked doc first**.
These are the spots where wrong changes are expensive (broken cache bills,
broken auth, data corruption).

| If you're modifying… | Read first |
|---|---|
| LLM call path, system prompt, tools, memory, middleware, message replay | [backend/docs/prompt-cache-discipline.md](backend/docs/prompt-cache-discipline.md) |
| Auth, registration, org/workspace bootstrap, RBAC | [backend/docs/auth.md](backend/docs/auth.md) |
| Agent middleware stack, request flow, event types | [backend/docs/agent-system-design.md](backend/docs/agent-system-design.md) |
| MCP catalog / OAuth / connector install | [backend/docs/mcp_catalog_oauth.md](backend/docs/mcp_catalog_oauth.md) |
| Frontend auth, CSRF, SSE proxy, deployment mode | [frontend/docs/auth-and-sse.md](frontend/docs/auth-and-sse.md) |
| Worktree provisioning, rebase / migration drift | [docs/worktrees.md](docs/worktrees.md) |
| Tests — writing, placing, or reviewing them | [docs/testing.md](docs/testing.md) |

Env vars, commands, file layouts (reference, not discipline) live in
[backend/docs/quick-reference.md](backend/docs/quick-reference.md) and
[frontend/docs/quick-reference.md](frontend/docs/quick-reference.md).

---

## Skill Index — When To Trigger What

Skills are loaded on demand; trigger them when the situation matches.

**Process / discipline:**

| Skill | Trigger |
|---|---|
| `/feature-workflow` | **Start** of any feature or non-trivial change — worktree, clarify intent, spec/plan under `docs/dev`, execute with checkpoints. |
| `/cubeplex-tdd` | Feature work, business logic, reusable core behavior, and durable bug-fix contracts. Test-first with cubeplex's test taxonomy. Use judgment for one-off migrations/config/docs; always verify. |
| `/debug-cubeplex` | Any bug or test failure — **before** proposing fixes. Reproduce first, then route to the right cubeplex diagnostic. |
| `/pr-codex-review-loop` | After pushing a PR — the full review loop, including replying to every codex / human review comment. |

**Implementation domains:**

| Skill | Trigger |
|---|---|
| `cubepi` | Building/extending/debugging agents on the CubePi framework — Agent API, providers, tools, middleware, checkpointing, MCP, HITL. |
| `cubepi-trace` | Debugging a cubepi/cubebox agent run — span tree, tool I/O, token/cache numbers, "why did the agent do that?". |
| `web-design-guidelines` | UI accessibility / design review pass. |
| `playwright-cli` | Writing or debugging Playwright tests. |

**Escape hatch:** `/codex:rescue` — stuck; want a second opinion or alternate
implementation pass.

---

## Quick Start

```bash
# Backend
cd backend && make dev-install && python main.py   # → http://localhost:8000

# Frontend
cd frontend && pnpm install && pnpm dev            # → http://localhost:3000

# First-time frontend E2E
npx playwright install
```

Local E2E needs `backend/.env` + `backend/config.development.local.yaml`
(both gitignored — copy from a working machine; don't recreate from scratch).
Worktrees need both copied in before first test run. Details:
[backend/docs/quick-reference.md](backend/docs/quick-reference.md).

---

## Auth & Scoping Mental Model

`Organization → Workspace → Membership → User`. All business routes are
workspace-scoped via the path: `/api/v1/ws/{workspace_id}/...`. Repository
layer enforces `(org_id, workspace_id)` structurally via `OrgScopedMixin` +
`ScopedRepository[T]` — not via ACL bolted on.

Two deployment modes: `single_tenant` (OSS, one shared org) vs
`multi_tenant` (cloud, per-user org). Bootstrap diverges at registration.
Full details, role tables, operator CLI, system endpoints:
[backend/docs/auth.md](backend/docs/auth.md).

---

## Common Gotchas (cross-cutting)

- **Subagent CWD**: subagents don't inherit cwd. When dispatching into a
  worktree, pin the absolute path AND tell them to `cat .worktree.env` first.
- **pnpm not npm** in frontend, always.
- **`@cubeplex/core` must build** before web sees API/type changes.
- **shadcn/ui**: run `npx shadcn-ui@latest` from `packages/web/`.
- **SSE compress**: Next.js rewrite buffers SSE if compress is on. Keep
  `compress: false`.
- **Worktree test DB**: plain `uv run pytest` is safe — conftest auto-routes
  to the per-slot `cubeplex_test_<slug>` DB. S3 tests need rustfs on `:9000`.
  See [docs/worktrees.md](docs/worktrees.md) → "Running tests in a worktree".

---

## Capturing Test / Build Output (agent workflow)

Anything noisy — pytest, `pnpm lint`, `pnpm build`, `mypy`, alembic — pipe
through `tee` into `tmp/<task>.log` (gitignored), then tail the summary:

```bash
uv run pytest tests/unit/test_foo.py --no-cov 2>&1 | tee tmp/foo.log | tail -3
```

Green tail → done, don't grep "just to be sure". Failed tail → the full
traceback is already in the log; grep it instead of re-running the suite.

---

## Authoring Conventions for Agents

- Temporary / one-shot scripts → `backend/scripts/dev/`.
- **All specs, plans, and engineering notes live under `docs/dev/`**:
  `specs/YYYY-MM-DD-<slug>-design.md` (feature designs),
  `plans/YYYY-MM-DD-<slug>.md` (implementation plans),
  `notes/YYYY-MM-DD-<slug>.md` (investigations, decisions, post-mortems).
  A spec/plan is a frozen snapshot; rebase the content, don't rewrite history.
- Only add a code comment when the *why* is non-obvious; never multi-paragraph.
- **Surgical changes.** Every changed line traces to the request. Match the
  file's existing style; don't refactor, reformat, or "improve" unrelated code.
  Remove only the imports/vars your change orphaned — flag other dead code,
  don't delete it.
- No backwards-compat shims unless explicitly asked — the project hasn't
  shipped publicly; cut over cleanly.
