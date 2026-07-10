# Task 1 report: history formatter

## Scope

Implemented only the pure conversation-history formatter task. The formatter
groups persisted messages by user turn, summarizes assistant text and tool
calls, redacts sensitive argument fields, resolves tool-call status from tool
results without exposing result bodies, and bounds normal and targeted output.

## TDD evidence

### RED

Command:

```bash
cd backend && mkdir -p tmp && uv run pytest tests/unit/services/conversation_search/test_history.py --no-cov 2>&1 | tee tmp/history-format-red.log | tail -3
```

Result: failed during collection with the expected
`ModuleNotFoundError: No module named 'cubebox.services.conversation_search.history'`.
The first invocation also revealed that the worktree did not yet have a
`backend/tmp` directory for the requested log; creating the gitignored
directory allowed the prescribed RED command to run normally.

### GREEN

Command:

```bash
cd backend && uv run pytest tests/unit/services/conversation_search/test_history.py --no-cov 2>&1 | tee tmp/history-format-green.log | tail -3
```

Result: `4 passed in 0.06s`.

Final focused verification also ran:

```bash
cd backend && uv run ruff check cubebox/services/conversation_search/history.py tests/unit/services/conversation_search/test_history.py
cd backend && uv run mypy cubebox/services/conversation_search/history.py
cd backend && git diff --check
```

Results: Ruff reported `All checks passed!`; mypy reported `Success: no issues
found in 1 source file`; `git diff --check` produced no output.

## Final P1 follow-up: many normal-sized tool calls

Normal history pages now budget the complete serialized page envelope, including
`estimated_tokens`, using the same stable-estimate approach as targeted results.
When one selected turn has too many otherwise normal tool calls for the budget,
the formatter keeps a prefix of complete call summaries and records the rest in
the turn-level `tool_calls_omitted` count. Kept call IDs remain valid targeted
result references. The design and plan document that omitted calls require a
re-read with a larger `max_tokens` budget before targeted lookup.

The regression creates 400 normal-sized calls at `max_tokens=256`, reconstructs
the full returned page payload, asserts it and `estimated_tokens` are bounded,
checks the omitted count reconciles with the original call count, and confirms
every retained reference resolves through `format_tool_result`.

## Final review follow-up: full payload accounting and structured arguments

Addressed the final two P1 findings.

- Targeted tool-result estimates now include every returned field, including
  `estimated_tokens`. The formatter iterates the cheap JSON estimate until it
  reaches a stable value, and uses that full payload during truncation.
- When a normal page is still oversized after string compaction, tool-call
  arguments now compact regardless of value type. Large numeric lists and
  nested boolean objects are dropped while direct sensitive keys remain
  `[REDACTED]`; call ID, name, and resolved status remain intact.
- Regression tests reconstruct the complete targeted result payload and cover
  a normal turn with both a 1,000-item numeric list and 500 nested booleans.
  They also assert ordinary reads do not expose tool-result bodies.

### Final follow-up TDD and verification evidence

The new regressions were run before the implementation:

```bash
cd backend && uv run pytest tests/unit/services/conversation_search/test_history.py --no-cov 2>&1 | tee tmp/history-format-final-budget-red.log | tail -30
```

Result: `2 failed, 4 passed`. The failures were the expected full targeted
payload estimate (`41 <= 35` was false) and an oversized normal-page result
from the numeric/nested-boolean arguments.

After the minimal formatter changes:

```bash
cd backend && uv run pytest tests/unit/services/conversation_search/test_history.py --no-cov 2>&1 | tee tmp/history-format-final-budget-green.log | tail -12
cd backend && uv run ruff check cubebox/services/conversation_search/history.py tests/unit/services/conversation_search/test_history.py
cd backend && uv run mypy cubebox/services/conversation_search/history.py
git diff --check
```

Results: `6 passed in 0.06s`; Ruff reported `All checks passed!`; mypy
reported `Success: no issues found in 1 source file`; `git diff --check`
produced no output.

## Changed files

- `backend/cubebox/services/conversation_search/history.py`
- `backend/tests/unit/services/conversation_search/test_history.py`

## Review follow-up: minimum formatter budget

The capability input models require `max_tokens >= 256`; the pure formatter now
enforces the same lower bound with a clear `ValueError`. Both formatter
docstrings reference the shared `MIN_HISTORY_MAX_TOKENS` constant. Regression
coverage proves that `255` is rejected by both functions and that a targeted
tool-result response at `256` remains bounded when its full serialized
metadata is included in the estimate.

### Minimum-budget verification evidence

```bash
cd backend && uv run pytest tests/unit/services/conversation_search/test_history.py --no-cov
cd backend && uv run ruff check cubebox/services/conversation_search/history.py tests/unit/services/conversation_search/test_history.py
cd backend && uv run ruff format --check cubebox/services/conversation_search/history.py tests/unit/services/conversation_search/test_history.py
cd backend && uv run mypy cubebox/services/conversation_search/history.py
git diff --check
```

Results: `7 passed`; Ruff checks and format check passed; mypy reported
`Success: no issues found in 1 source file`; `git diff --check` produced no
output.

## Review notes and concerns

No unresolved concerns. The token calculation deliberately uses the approved
cheap JSON-character estimate. A formatted turn has unavoidable structural
metadata, so an extremely tiny budget can still estimate above the requested
number after all textual fields have been shortened; this is why the response
also explicitly reports `truncated=True`.

## Review follow-up: bounded arguments and whole tool-result responses

Addressed both Important review findings in the history formatter.

- Oversized normal turns now clear user text, assistant text, and non-sensitive
  string values in tool-call arguments before restoring the largest prefixes
  that fit the turn's token budget. Sensitive values remain `[REDACTED]` and
  tool call identity/name/status are retained. Nested argument dictionaries
  and lists are handled as well.
- Targeted tool results now estimate the serialized response payload
  (`tool_call_id`, `tool_name`, `content`, `is_error`, and `truncated`) rather
  than the result content alone. The content is shortened by a bounded search
  until that full payload fits the requested budget whenever the metadata
itself fits.
- Added regression coverage for a 1,000-character non-sensitive call argument
  (while preserving API-key redaction) and for a tool result whose content fits
  alone but whose complete response needs truncation.

## Final P1 follow-up: oversized persisted tool metadata

Long persisted tool-call IDs and tool names can no longer consume the entire
formatter budget. IDs longer than 64 characters are represented as a stable
`tool_ref_` plus SHA-256 digest; `format_tool_result` accepts either that
returned reference or the original persisted ID. Long names are bounded to a
64-character display value. This retains an agent-usable path to the exact
persisted result while keeping both normal history and targeted-result payloads
within the 256-token minimum.

The regression uses 10,000-character IDs and names at `max_tokens=256`, checks
both bounded payload estimates, and fetches the exact result with the returned
reference.

### Follow-up test evidence

```bash
cd backend && uv run pytest tests/unit/services/conversation_search/test_history.py --no-cov
```

Result: `5 passed in 0.06s`.

```bash
cd backend && uv run ruff check cubebox/services/conversation_search/history.py tests/unit/services/conversation_search/test_history.py
cd backend && uv run mypy cubebox/services/conversation_search/history.py
git diff --check
```

Results: Ruff reported `All checks passed!`; mypy reported `Success: no issues
found in 1 source file`; `git diff --check` produced no output.
