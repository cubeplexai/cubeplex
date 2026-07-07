# Testing Principles & Layout

Cross-cutting testing rules for cubebox — backend (pytest) and frontend
(Playwright). The one-paragraph summary lives in [AGENTS.md](../AGENTS.md);
this is the full discipline.

**A test must protect a business invariant or a contract a future change could
quietly break.** If a test only confirms "this DOM element renders," it costs
maintenance every time the UI shifts and catches nothing the next user wouldn't
see in 5 seconds. Delete it.

## Layer split — who owns what

| Layer | Owns | Examples |
|---|---|---|
| **backend e2e** (`backend/tests/e2e/`) | API contracts, RBAC/scope isolation, DB invariants, SSE event shape, agent run state machine, billing/cache math, MCP/skill catalog, IM ingress | "cross-org request returns 404 not 403", "drained run replays correctly", "preinstalled skill seeds on bootstrap" |
| **frontend e2e** (Playwright) | Only what the backend can't observe: SSR auth redirects, CSRF cookie flow, SSE-over-Next-rewrite (no compress buffer), client state machines (streaming/steering/widget-morph), iframe + CSP, i18n cookie persistence, real upload UX | "register lands on /w/{ws}", "loading animation persists during stream", "language switcher cookie survives reload", "widget can't fetch parent origin" |
| **unit tests** | Pure functions, parsers, hooks, pricing math, signature verification | `compute_runtime`, `verify_feishu_signature`, `computeAuthBandState` |

## Frontend e2e — what's allowed

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

## Backend e2e — disciplines

- **Real services, not mocks, at internal boundaries.** Postgres, Redis, rustfs, the running FastAPI app are all real. Mock only at the OUTERMOST external boundary the test isn't about — opensandbox SDK, lark_oapi token endpoint, Tempo HTTP client, cubepi LLM provider when not testing the LLM path itself.
- **No fake-server E2E.** If the system genuinely can't be simulated (third-party SaaS, no test mode), drop down to a **unit test of the seam**, don't build a fake server.
- **Skip honestly.** When external infra (e.g. opensandbox lacking pause API on a given backend) can't satisfy the test, `pytest.skip(reason="...")` with a *named* reason — never `xfail`, never silent. See `tests/e2e/test_sandbox_pause_resume.py` "G11" pattern.
- **Real-LLM tests are tagged.** `@pytest.mark.real_llm`. CI fast lane runs `-m "not real_llm"`; the real-LLM suite runs separately. Don't drop real LLM calls into a test that doesn't need to assert about model behavior.
- **No fire-and-forget sleeps.** Replace `await asyncio.sleep(0.5)` with a bounded poll loop waiting on the observable state — `test_steer_endpoint.py:46` is the model.
- **Cleanup per test.** Shared `DEFAULT_ORG/WS` is fine for read-only checks; tests that create rows must delete them (or use a per-test fresh workspace). Aggregate queries (`select count(*)`) across runs are a flake-bomb.

## When in doubt: would this catch the bug?

Before writing a test, write the one-line bug it would have caught.
- "If the cache prefix drifted, this test fails." ✓ Write it.
- "If the seed silently broke, this test fails." ✓ Write it.
- "If the heading text changed from 'Memory' to 'Memories', this test fails." ✗ Delete it. The user notices in 5 seconds and the change was probably intentional.

## Test layout — pick the directory by what the test hits

The `e2e` marker is auto-applied by `backend/tests/e2e/conftest.py`, and the
Makefile filters know which suite each command targets.

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

(Other top-level dirs under `backend/tests/` — `api/`, `services/`,
`streams/`, … — are legacy layout. New tests go in the three dirs above.)

**If your test opens an `AsyncSession`, runs alembic, or hits the
FastAPI app, it is an e2e test, full stop.** The directory choice
drives both which conftest fixtures the file inherits AND whether
`make check-ci` ignores it — putting a DB-touching test in
`tests/integration/` will run it during every pre-push and fail the
moment a new migration lands without manually re-`upgrade head`ing the
per-slot test DB.

Running tests in a worktree (per-slot test DB routing, rustfs creds):
[docs/worktrees.md](worktrees.md) → "Running tests in a worktree".
