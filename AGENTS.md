# AGENTS.md

Guidance for AI agents working in cubebox. This file is auto-loaded by Claude
Code (and other AGENTS.md-aware tools) on every session. Walking from any
subdir back up still finds this file — so the discipline below applies
everywhere.

If you only read one section, read **Workflow Discipline** and **Critical
Subsystems → docs to load before touching**.

---

## What This Repo Is

cubebox is a full-stack agent platform.

- `backend/` — FastAPI + cubepi streaming agent runtime, SSE API, Postgres
  message history, MCP tool integration.
- `frontend/` — Next.js + React 19 monorepo (`packages/web` + shared
  `@cubebox/core`).
- `docs/` — cross-cutting documentation, including
  `docs/dev/{specs,plans,notes}` for **all** feature specs, multi-step
  plans, and engineering notes (see below).
- `backend/docs/`, `frontend/docs/` — area-specific reference and
  architecture deep dives. Indexed in the "Critical Subsystems" table.
- `scripts/` — worktree provisioning, dev helpers.

---

## Workflow Discipline (non-negotiable)

1. **Worktree before spec/code** for any non-trivial feature. Run
   `./scripts/new-worktree feat/<name>` from the **main repo root**.
2. **Read `.worktree.env` first** on entering a worktree. Never assume
   ports 8000 / 3000. See [docs/worktrees.md](docs/worktrees.md).
3. **E2E priority over mocks/units.** Skip E2E only when the system genuinely
   cannot be simulated (e.g. third-party SaaS with no test mode); fall back
   to unit tests, not fake-server E2E. **What "E2E" means here**: a
   business-flow test that exercises a real user/system invariant, not a
   DOM snapshot. See the "Testing Principles" section below.
4. **Plan before code** for multi-step work. Use `/writing-plans` skill.
5. **Brainstorm before designing** features. Use `/brainstorming` skill.
6. **One concern per PR.** Decide spec/plan/code split (1 PR vs N) **before**
   pushing, based on coupling and review cost.
7. **PR codex review loop** — automated by the
   [`pr-codex-review-loop`](.claude/skills/pr-codex-review-loop/SKILL.md)
   skill. Push → wait → poll → fix actionable feedback → reply to every
   comment → re-tag `@codex` → repeat until clean.
8. **Branch discipline.** During multi-task execution, stay on the feature
   branch. Never auto-switch to main or initiate merges mid-execution.
9. **Verify before claiming done.** Run the actual command, paste evidence.
   `/verification-before-completion` skill enforces this.
10. **Act on review feedback directly** once you've confirmed it's valid —
    push the fix as a follow-up commit; don't ask permission first.
11. **Incremental testing during dev**: run only changed-module tests per
    task; reserve the full suite for the pre-PR sweep.
12. **Plain language in docs/specs.** Don't invent abstract jargon. Say what
    literally happens + why, in concrete words.
13. **Docs ship with the code.** Any change that adds, removes, or alters a
    user-facing behavior — a route or its path, a header, an enum/option,
    a default limit, a config key, a UI flow, a role/permission, a CLI/slash
    command — MUST update the matching page under `docs/site/docs/` in the
    **same PR**. A PR that changes documented behavior without touching the
    doc is incomplete. New user-facing subsystems get a new doc page (this is
    the one sanctioned exception to "don't create new docs"). When a screenshot
    is needed but not yet captured, leave a placeholder, never a silent gap:
    ```md
    :::info 📸 Screenshot placeholder
    **Capture:** <what to show, incl. the interaction/state>
    **Asset:** `/img/<area>/<name>.png`
    :::
    ```
    The mapping from code area to doc page lives in the docs-overhaul plan
    ([docs/dev/plans/2026-06-23-docs-overhaul.md](docs/dev/plans/2026-06-23-docs-overhaul.md)).

---

## Hard Code Rules

- **Type annotations everywhere.** mypy strict in backend, strict TS in
  frontend.
- **Line length: 100 chars.**
- **Datetimes from DB → `utc_isoformat()`** so frontend sees the UTC offset.
- **Time columns are tz-aware.** All `datetime` model fields use
  `sa_column=Column(DateTime(timezone=True), ...)` (Postgres `timestamptz`).
  Application code writes `datetime.now(UTC)` (tz-aware). Frontend gets
  ISO 8601 with `+00:00` (via `utc_isoformat()`) or `Z` (via Pydantic
  default) — both valid. No naive `datetime` ever crosses the DB or
  service-API boundary. When introducing a new datetime column or
  converting an existing one, the alembic migration must hand-add
  `postgresql_using="<col> AT TIME ZONE 'UTC'"` on each `alter_column`
  call — autogen omits it, and the default cast applies the session
  `TimeZone` (wrong for our stored UTC values).
- **New business table → public ID prefix** in
  `backend/cubebox/models/public_id.py`; `default_factory` uses
  `generate_public_id(PREFIX_X)`.
- **Migrations: `alembic revision --autogenerate -m "..."`.** Do not hand-edit
  migration files; do not skip autogen.
- **Migration head conflicts after rebase:** When rebasing onto main
  introduces a second alembic head, do NOT use `alembic merge heads`.
  Instead, edit the branch's first migration file to change its
  `down_revision` to main's new head. This keeps the history linear.
- **Dependencies: `uv add <pkg>` (backend), `pnpm add` (frontend).** Don't
  hand-edit `pyproject.toml` / `package.json`.
- **Do not create new docs without permission.** Update an existing doc when
  possible.
- **Scope-isolated APIs.** Workspace routes (`/api/v1/ws/{ws}/...`) and
  org-admin routes (`/api/v1/admin/...`) must be separate handlers, even
  when the logic is identical. Don't parameterize one route to serve
  both audiences. Reuse goes one layer down — services, repositories,
  pure functions — never at the route layer. The smell: catching yourself
  adding `?scope=` or a `role` body field.
- **Scope-isolated pages.** Each user-facing page gets its own Next route
  and its own page file, even when the layout looks identical to another
  page. Pages are assemblies of modules; modules (`<List>`, `<DetailPanel>`,
  `<Toolbar>`, …) are the reuse boundary. The smell: a `mode?: 'admin' | 'workspace'`
  prop on a page component.

---

## Testing Principles

**A test must protect a business invariant or a contract a future change could
quietly break.** If a test only confirms "this DOM element renders," it costs
maintenance every time the UI shifts and catches nothing the next user wouldn't
see in 5 seconds. Delete it.

### Layer split — who owns what

| Layer | Owns | Examples |
|---|---|---|
| **backend e2e** (`backend/tests/e2e/`) | API contracts, RBAC/scope isolation, DB invariants, SSE event shape, agent run state machine, billing/cache math, MCP/skill catalog, IM ingress | "cross-org request returns 404 not 403", "drained run replays correctly", "preinstalled skill seeds on bootstrap" |
| **frontend e2e** (Playwright) | Only what the backend can't observe: SSR auth redirects, CSRF cookie flow, SSE-over-Next-rewrite (no compress buffer), client state machines (streaming/steering/widget-morph), iframe + CSP, i18n cookie persistence, real upload UX | "register lands on /w/{ws}", "loading animation persists during stream", "language switcher cookie survives reload", "widget can't fetch parent origin" |
| **unit tests** | Pure functions, parsers, hooks, pricing math, signature verification | `compute_runtime`, `verify_feishu_signature`, `computeAuthBandState` |

### Frontend e2e — what's allowed

Allowed:
- **Full user flows.** Register → land in workspace → send message → see streaming response.
- **SSR / middleware behavior.** Cookie-driven redirects, locale negotiation, CSP headers, opaque-origin iframe isolation.
- **Client state machines.** Steering's pending chip, attachment upload cancel, widget morph idempotence, search popover keyboard nav.
- **Cross-page contracts.** Skill artifact preview → publish dialog → catalog visibility.

Forbidden (delete these on sight):
- **Element-count assertions.** "`expect(nav).toHaveCount(11)`" — every UI tweak breaks it, catches nothing real.
- **Pure presence checks.** "`heading 'X' is visible`" or "`tab 'Y' exists`" *as the entire test body*. If the page didn't render, every richer test in the file would also fail.
- **Nav-route smoke tests.** "click link → URL changes". Next/React Router does this; covered by chat-flow already mounting the page.
- **Section header sweeps.** "`headers 'A', 'B', 'C', 'D' visible`" — same as above with more brittleness.

A presence check is fine *as a step inside a real flow* (you click X, expect a confirmation toast). It's not fine as the whole test.

### Backend e2e — disciplines

- **Real services, not mocks, at internal boundaries.** Postgres, Redis, rustfs, the running FastAPI app are all real. Mock only at the OUTERMOST external boundary the test isn't about — opensandbox SDK, lark_oapi token endpoint, Tempo HTTP client, cubepi LLM provider when not testing the LLM path itself.
- **No fake-server E2E.** If the system genuinely can't be simulated (third-party SaaS, no test mode), drop down to a **unit test of the seam**, don't build a fake server.
- **Skip honestly.** When external infra (e.g. opensandbox lacking pause API on a given backend) can't satisfy the test, `pytest.skip(reason="...")` with a *named* reason — never `xfail`, never silent. See `tests/e2e/test_sandbox_pause_resume.py` "G11" pattern.
- **Real-LLM tests are tagged.** `@pytest.mark.real_llm`. CI fast lane runs `-m "not real_llm"`; the real-LLM suite runs separately. Don't drop real LLM calls into a test that doesn't need to assert about model behavior.
- **No fire-and-forget sleeps.** Replace `await asyncio.sleep(0.5)` with a bounded poll loop waiting on the observable state — `test_steer_endpoint.py:46` is the model.
- **Cleanup per test.** Shared `DEFAULT_ORG/WS` is fine for read-only checks; tests that create rows must delete them (or use a per-test fresh workspace). Aggregate queries (`select count(*)`) across runs are a flake-bomb.

### When in doubt: would this catch the bug?

Before writing a test, write the one-line bug it would have caught.
- "If the cache prefix drifted, this test fails." ✓ Write it.
- "If the seed silently broke, this test fails." ✓ Write it.
- "If the heading text changed from 'Memory' to 'Memories', this test fails." ✗ Delete it. The user notices in 5 seconds and the change was probably intentional.

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

Env vars, commands, file layouts (reference, not discipline) live in
[backend/docs/quick-reference.md](backend/docs/quick-reference.md) and
[frontend/docs/quick-reference.md](frontend/docs/quick-reference.md).

---

## Skill Index — When To Trigger What

Skills are loaded on demand; trigger them when the situation matches.

**Process / discipline:**

| Skill | Trigger |
|---|---|
| `/brainstorming` | **Before** designing any feature or non-trivial change — explores intent before code. |
| `/writing-plans` | Multi-step work that needs a written plan before touching code. |
| `/executing-plans` | When you have a written plan and need checkpointed execution. |
| `/systematic-debugging` | Any bug, test failure, or unexpected behavior — **before** proposing fixes. |
| `/test-driven-development` | Implementing a feature or bug fix — tests before code. |
| `/verification-before-completion` | Before claiming "done" / committing / opening a PR. |
| `/receiving-code-review` | When responding to codex / human review comments. |
| `/pr-codex-review-loop` | After pushing a PR — automates push → poll → fix → reply → re-tag until clean. Bundled poller at `.claude/skills/pr-codex-review-loop/scripts/codex-poll.sh`. |
| `/finishing-a-development-branch` | When implementation is complete and you're choosing merge / PR / cleanup. |
| `/using-git-worktrees` | Before starting feature work that needs isolation. |

**Implementation domains:**

| Skill | Trigger |
|---|---|
| `sqlmodel-expert` | Creating / modifying SQLModel + Alembic migrations, query optimization. |
| `frontend-design` / `huashu-design` | Building UI components or pages with high design quality. |
| `shadcn` | Adding shadcn/ui components. |
| `web-design-guidelines` | UI accessibility / design review pass. |
| `playwright-cli` | Writing or debugging Playwright tests. |
| `claude-api` | Working with the Anthropic SDK / claude-api integration code. |
| `karpathy-guidelines` | LLM coding pitfalls and disciplines from Andrej Karpathy's observations — heuristics around context, evals, complexity, etc. |

**Escape hatch:**

| Skill | Trigger |
|---|---|
| `/codex:rescue` | Stuck; want a second opinion or alternate implementation pass. |

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

## Worktrees in Brief

Always create from main repo root, with a date prefix:

```bash
./scripts/new-worktree feat/YYYY-MM-DD-<name>
```

Inside a worktree, **first command**:

```bash
cat .worktree.env             # allocated ports + DBs
./scripts/worktree-env doctor # health check
```

Full reference (subcommands, rebase migration drift, agent caveats):
[docs/worktrees.md](docs/worktrees.md).

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
  worktree, pin the absolute path AND tell them to `cat .worktree.env`
  first.
- **pnpm not npm** in frontend, always.
- **`@cubebox/core` must build** before web sees API/type changes.
- **shadcn/ui**: run `npx shadcn-ui@latest` from `packages/web/`.
- **SSE compress**: Next.js rewrite buffers SSE if compress is on. Keep
  `compress: false`.
- **Worktree ports**: `8000` / `3000` are wrong inside worktrees — port
  collisions silently test the wrong code.
- **Worktree test DB**: `tests/conftest.py` auto-routes worktree tests to
  the per-slot `cubebox_test_<slug>` DB (and pins the rustfs object-store
  creds), so plain `uv run pytest` is safe and never touches your dev DB.
  S3 tests need rustfs on `:9000` (`~/infra/rustfs`). See
  [docs/worktrees.md](docs/worktrees.md) → "Running tests in a worktree".

---

## Test Layout

Pick the directory by what the test actually hits. The marker is
auto-applied by `backend/tests/conftest.py`, and the Makefile filters
know which suite each command targets.

- **`backend/tests/unit/`** — pure functions, in-process collaborators
  only. No Postgres / Redis / OpenSandbox / S3 / network. Mock at the
  function or class boundary. Runs in seconds; runs on every commit
  via pre-commit / CI.
- **`backend/tests/integration/`** — in-process integration of multiple
  cubebox modules through their real public APIs, but still no external
  systems. Compose services together with fakes between them.
- **`backend/tests/e2e/`** — anything that touches a real backing store
  (Postgres, Redis, rustfs S3, OpenSandbox, or the FastAPI app via
  httpx `AsyncClient`). Slow; run on demand or pre-PR.

**If your test opens an `AsyncSession`, runs alembic, or hits the
FastAPI app, it is an e2e test, full stop.** The directory choice
drives both which conftest fixtures the file inherits AND whether
`make check-ci` ignores it — putting a DB-touching test in
`tests/integration/` will run it during every pre-push and fail the
moment a new migration lands without manually re-`upgrade head`ing the
per-slot test DB.

---

## Capturing Test / Build Output (agent workflow)

Anything noisy and likely to need a second look — pytest, `pnpm lint`,
`pnpm build`, `mypy`, alembic — pipe through `tee` into `tmp/<task>.log`
in the worktree, then tail the summary. Lets you eyeball the result
immediately AND go back into the full log later without re-running the
command. `tmp/` is gitignored at the repo root.

```bash
mkdir -p tmp
uv run pytest tests/unit/test_foo.py --no-cov 2>&1 | tee tmp/foo.log | tail -3
pnpm lint 2>&1 | tee tmp/lint.log | tail -5
```

If `tail` shows green, you're done — don't grep the log "just to be
sure." If `tail` shows a failure, the log already has the full
traceback; `grep -nE "FAILED|Error|line [0-9]+" tmp/foo.log` finds the
exact site without re-running.

This is a process gotcha specifically for AI agents: re-running a
3-minute test suite because the first `tail -3` cut off the relevant
error costs more than every `tee` you'll ever write.

---

## Authoring Conventions for Agents

- Temporary / one-shot scripts → `backend/scripts/dev/`. They are not
  long-term commitments.
- **All specs, plans, and engineering notes live under `docs/dev/`** at
  the repo root — never `backend/docs/superpowers/` or
  `frontend/docs/superpowers/` (those locations are gone). Use the
  following subdirs and naming pattern:
  - `docs/dev/specs/YYYY-MM-DD-<slug>-design.md` — feature designs
    (the "what and why" before implementation).
  - `docs/dev/plans/YYYY-MM-DD-<slug>.md` — multi-step implementation
    plans broken into reviewable chunks.
  - `docs/dev/notes/YYYY-MM-DD-<slug>.md` — investigation notes,
    decision records, post-mortems.
  A spec/plan is a frozen snapshot; rebase the content, don't rewrite
  history.
- Don't write multi-paragraph code comments. Only add a comment when the
  *why* is non-obvious.
- Don't add backwards-compat shims unless explicitly asked — the project
  hasn't shipped publicly yet; cut over cleanly.
