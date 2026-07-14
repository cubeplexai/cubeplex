---
name: cubeplex-tdd
description: Use when implementing a cubeplex feature, business rule, reusable core behavior, or a bug fix whose contract will keep evolving — the red→green→refactor loop. This skill is the TDD loop and the when-to-TDD judgment; it defers the test taxonomy / placement / discipline to docs/testing.md rather than restating it. Triggers on phrases like "写测试", "先写个 test", "TDD", "e2e 还是 unit", "红绿重构".
---

# cubeplex TDD

The test-first loop for cubeplex. **This skill is the loop and the judgment — it
does not restate the test rules.** For what earns a test's place, the layer
split, directory placement, real-services / real-LLM discipline, no-sleep /
cleanup rules, and the "would this catch the bug?" check, read the authoritative
source: **[docs/testing.md](../../../docs/testing.md)**. Don't duplicate it here.

## When TDD is the default

Feature work, business logic, reusable core behavior, and bug fixes where the
behavior keeps evolving — the red→green loop is strongest when the test can
express a stable contract future changes must preserve. Use judgment for one-off
migrations / config / docs / mechanical rewrites (validate with the smallest
realistic reproduction instead) — but **always verify** with a real command
regardless.

## The loop

1. **Red.** Write the smallest test expressing a stable business invariant or
   contract (testing.md: "would this catch the bug?"). Watch it fail for the
   *right* reason — an assertion, not an import error.
2. **Green.** Minimal code to pass.
3. **Refactor.** Clean up with the test as the safety net.

## The one placement rule you can't get wrong

The full taxonomy is in testing.md; this is the footgun worth repeating: **if
the test opens an `AsyncSession`, runs alembic, or hits the FastAPI app, it is
an e2e test (`backend/tests/e2e/`) — full stop.** Misplacing it into
`integration/` makes it run on every pre-push and fail the suite. Everything
else → testing.md's "pick the directory by what the test hits".

## Running

```bash
make backend-test-e2e        # backend e2e
make backend-test-contracts  # plugin/EE contract tests
make test-ui-unit            # frontend unit
make frontend-test-e2e       # frontend Playwright (writing/debugging → /playwright-cli)
```

Per task, run only the changed-module tests; the pre-push hook runs the
CI-equivalent `make check-ci` when you push code.
