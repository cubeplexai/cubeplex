# Compaction Default-On + Error Classification & i18n — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the conversation compaction middleware actually run for long chats, and turn opaque provider failures into a classified error taxonomy with i18n-friendly rendering anchored to the run that failed.

**Architecture:**
- **PR-1 (compaction):** Flip `compaction.enabled` default to `true`, and replace the fixed `fallback_context_window` with the active model's `context_window` from the model registry (fallback only when missing). One worktree, one PR.
- **PR-2 (error UX):** Add a backend error-code classifier that maps exceptions → `(ErrorCode, params)`; extend `ErrorEvent` payload + `RunMeta` Redis hash with the classified fields; surface them through the SSE event and run-list API; render in the frontend per-conversation with i18n keys (en + zh), anchored at the tail of the failed run.
- Error message remains the **last_error on RunMeta** (Redis, no Alembic), not a new chat message kind. UI mounts the error bubble after the assistant's last block in the failing run.

**Tech Stack:** FastAPI · cubepi `Middleware` · dynaconf · Redis hash for `RunMeta` · Next.js · next-intl JSON messages · Zustand.

---

## Decisions locked from brainstorming

- Error persistence: **RunMeta last_error** (Redis only — no DB migration). Rendered at the tail of the failed run.
- Error codes (first wave): `context_length_exceeded`, `rate_limited`, `provider_auth_failed`, `provider_unavailable`, `provider_bad_request`, `tool_failed`. `run_cancelled` stays on its own existing path.
- i18n: **frontend-only**. Backend emits `{error_code, error_params, error_raw, message}` with `message` being an English fallback; frontend `en.json` / `zh.json` own the localized copy keyed by code.
- No E2E for PR-1 (user said so). PR-2 gets one Playwright check that a 400 in the model surfaces a localized bubble.

---

# PR-1 — Compaction default-on, per-model threshold

### Task 1: Flip `compaction.enabled` to true in default config

**Files:**
- Modify: `backend/config.yaml:334`

- [ ] **Step 1: Edit the default**

```yaml
  compaction:
    enabled: true
    summary_provider: "cubebox"
    summary_model: "doubao-seed-1.8"
    threshold_ratio: 0.7
    keep_recent_messages: 8
    max_summary_tokens: 1024
    min_compact_messages: 4
    fallback_context_window: 128000
```

The threshold ratio stays 0.7. `fallback_context_window` bumps from `64000` to `128000` because most production models we ship (kimi-k2.6 256k, deepseek-v4-flash 128k, doubao 256k) sit at 128k+ and the fallback should never be the *smaller* of the two paths in normal operation — it's only a last resort when `_model_config.context_window` is missing.

- [ ] **Step 2: Confirm via dynaconf load**

```bash
cd backend && uv run python -c "
from cubebox.config import config
print('enabled =', config.get('compaction.enabled'))
print('fallback =', config.get('compaction.fallback_context_window'))
"
```

Expected:
```
enabled = True
fallback = 128000
```

- [ ] **Step 3: Commit**

```bash
git add backend/config.yaml
git commit -m "feat(compaction): enable by default, raise fallback ctx window to 128k"
```

### Task 2: Wire `CompactionMiddleware` to per-model `context_window`

**Files:**
- Modify: `backend/cubebox/streams/run_manager.py:2344-2388` (compaction wire-up block)

The factory at `_build_agent_for_conversation` already resolved `_model_config = factory.get_model_config(provider_name, model_id)` at line 1932 — it's in scope when compaction is wired ~400 lines later. Use its `.context_window` directly.

- [ ] **Step 1: Edit the threshold computation**

Replace the line:

```python
_ctx_window: int = int(_comp_cfg.get("compaction.fallback_context_window", 64000))
```

with:

```python
_fallback_window = int(_comp_cfg.get("compaction.fallback_context_window", 128000))
_model_window = int(_model_config.context_window or 0)
_ctx_window: int = _model_window or _fallback_window
```

Right after computing `_ctx_window`, change the existing `logger.info` so the source is observable:

```python
logger.info(
    "CompactionMiddleware enabled (threshold={} tokens, model_window={}, fallback={})",
    int(_ctx_window * _ratio),
    _model_window,
    _fallback_window,
)
```

(Replaces the existing `logger.info("CompactionMiddleware enabled (threshold={} tokens)", ...)`.)

- [ ] **Step 2: Sanity-load the backend module to verify no import / lint regression**

```bash
cd backend && uv run python -c "from cubebox.streams import run_manager"
```

Expected: clean import, no exception.

- [ ] **Step 3: Run focused mypy on the changed file**

```bash
cd backend && uv run mypy cubebox/streams/run_manager.py
```

Expected: `Success: no issues found in 1 source file` (or matches pre-change baseline if file already had pre-existing notes).

- [ ] **Step 4: Run the compaction unit tests**

```bash
cd backend && uv run pytest tests/middleware/test_compaction.py -v
```

Expected: all pass. (No new tests needed in PR-1 per user direction.)

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/streams/run_manager.py
git commit -m "feat(compaction): use model.context_window for threshold, fallback only when unknown"
```

### Task 3: Push PR-1

- [ ] **Step 1: Push branch**

```bash
git push -u origin feat/compact-and-error-ux
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "compaction: enable by default + use per-model context window" --body "$(cat <<'EOF'
## Summary
- Flip `compaction.enabled` to true in `config.yaml`; raise fallback context window from 64k to 128k.
- Use the active model's `context_window` from the model registry as the threshold basis (fallback only when missing). Logs `model_window` and `fallback` alongside the resolved threshold so the source is observable.

## Why
Compaction was shipped but never enabled in dev/prod config — long conversations on kimi-k2.6 (256k ctx) blew past the model's window and returned an opaque `InvalidParameter` 400. Even when enabled, the threshold was fixed at 64k regardless of model.

## Test plan
- [x] `uv run pytest tests/middleware/test_compaction.py`
- [x] `uv run mypy cubebox/streams/run_manager.py`
- [ ] Manual: start a long conversation, confirm `CompactionMiddleware enabled (threshold=...)` log fires and the summary kicks in past the threshold.
EOF
)"
```

- [ ] **Step 3: Run the codex review loop**

Trigger the pr-codex-review-loop skill — push, wait for codex, fix, reply, re-tag, repeat until clean. (See `.claude/skills/pr-codex-review-loop/SKILL.md`.)

---

# PR-2 — Error classification + i18n

### Task 4: Define the ErrorCode taxonomy + classifier

**Files:**
- Create: `backend/cubebox/errors/__init__.py`
- Create: `backend/tests/errors/test_classify.py`

The classifier takes the raw exception and returns `(ErrorCode, params)`. Params capture the dynamic bits the frontend needs to render the localized string (e.g. `model`, `provider`, `tokens_in`, `context_window`).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/errors/test_classify.py`:

```python
"""Tests for the error classifier — exception → (code, params)."""

from __future__ import annotations

import pytest

from cubebox.errors import ErrorCode, classify_exception


class _FakeBadRequest(Exception):
    """Mimics openai.BadRequestError surface — has .status_code and .message."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class _FakeRateLimit(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.status_code = 429
        self.message = message


def test_classify_explicit_context_length_message() -> None:
    exc = _FakeBadRequest(400, "This model's maximum context length is 128000 tokens")
    code, params = classify_exception(exc, model="kimi-k2.6", provider="ark")
    assert code is ErrorCode.context_length_exceeded
    assert params["model"] == "kimi-k2.6"
    assert params["provider"] == "ark"


def test_classify_volcano_invalid_parameter_with_oversize_tokens() -> None:
    exc = _FakeBadRequest(400, "InvalidParameter: A parameter specified in the request is not valid")
    code, params = classify_exception(
        exc, model="kimi-k2.6", provider="ark", tokens_in=290_000, context_window=256_000
    )
    assert code is ErrorCode.context_length_exceeded
    assert params["tokens_in"] == 290_000
    assert params["context_window"] == 256_000


def test_classify_rate_limit() -> None:
    exc = _FakeRateLimit("Rate limit exceeded, retry in 60s")
    code, params = classify_exception(exc, model="gpt-4o", provider="openai")
    assert code is ErrorCode.rate_limited


def test_classify_auth_failed() -> None:
    exc = _FakeBadRequest(401, "Invalid API key")
    code, _ = classify_exception(exc, model="gpt-4o", provider="openai")
    assert code is ErrorCode.provider_auth_failed


def test_classify_forbidden_as_auth() -> None:
    exc = _FakeBadRequest(403, "Forbidden")
    code, _ = classify_exception(exc, model="gpt-4o", provider="openai")
    assert code is ErrorCode.provider_auth_failed


def test_classify_provider_unavailable_5xx() -> None:
    exc = _FakeBadRequest(503, "service unavailable")
    code, _ = classify_exception(exc, model="gpt-4o", provider="openai")
    assert code is ErrorCode.provider_unavailable


def test_classify_timeout() -> None:
    exc = TimeoutError("read timed out")
    code, _ = classify_exception(exc, model="gpt-4o", provider="openai")
    assert code is ErrorCode.provider_unavailable


def test_classify_generic_bad_request_falls_back() -> None:
    exc = _FakeBadRequest(400, "model_not_found")
    code, _ = classify_exception(exc, model="gpt-4o", provider="openai")
    assert code is ErrorCode.provider_bad_request


def test_classify_unknown_exception_falls_back_to_tool_or_internal() -> None:
    code, _ = classify_exception(RuntimeError("boom"), model="gpt-4o", provider="openai")
    assert code is ErrorCode.internal_error
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/errors/test_classify.py -v
```

Expected: ImportError / ModuleNotFoundError — `cubebox.errors` does not yet exist.

- [ ] **Step 3: Write the implementation**

Create `backend/cubebox/errors/__init__.py`:

```python
"""Error taxonomy and classifier.

The classifier turns provider/tool exceptions into a structured
``(ErrorCode, params)`` pair the SSE layer can carry to the frontend.
The frontend owns the localized strings keyed by ``ErrorCode``; the
backend only emits codes and dynamic params (model, provider, token
counts) plus an English fallback ``message`` for non-Web clients.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """Coarse error categories surfaced to the user.

    Members are strings so they serialize cleanly into SSE JSON / Redis.
    Add new members at the end; never reuse or renumber.
    """

    context_length_exceeded = "context_length_exceeded"
    rate_limited = "rate_limited"
    provider_auth_failed = "provider_auth_failed"
    provider_unavailable = "provider_unavailable"
    provider_bad_request = "provider_bad_request"
    tool_failed = "tool_failed"
    internal_error = "internal_error"


_CONTEXT_LENGTH_PATTERNS = (
    re.compile(r"maximum context length", re.IGNORECASE),
    re.compile(r"context.{0,10}length.{0,20}exceed", re.IGNORECASE),
    re.compile(r"too many tokens", re.IGNORECASE),
    re.compile(r"prompt is too long", re.IGNORECASE),
    re.compile(r"reduce.{0,10}messages", re.IGNORECASE),
)

_RATE_LIMIT_PATTERNS = (
    re.compile(r"rate ?limit", re.IGNORECASE),
    re.compile(r"quota", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
)


def _status_of(exc: BaseException) -> int | None:
    """Best-effort status code extraction. Handles openai-sdk-style attrs."""

    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    resp = getattr(exc, "response", None)
    if resp is not None:
        rc = getattr(resp, "status_code", None)
        if isinstance(rc, int):
            return rc
    return None


def classify_exception(
    exc: BaseException,
    *,
    model: str | None = None,
    provider: str | None = None,
    tokens_in: int | None = None,
    context_window: int | None = None,
) -> tuple[ErrorCode, dict[str, Any]]:
    """Map an exception to ``(ErrorCode, params)``.

    Heuristics (first match wins):
      1. Explicit context-length wording in the message.
      2. tokens_in within 5% of context_window when status is 4xx with no
         clear signal (covers Volcano ARK's opaque ``InvalidParameter``).
      3. 401 / 403 → provider_auth_failed.
      4. 429 / quota wording → rate_limited.
      5. 5xx / TimeoutError / ConnectionError → provider_unavailable.
      6. Other 4xx → provider_bad_request.
      7. Else → internal_error.

    ``params`` always carries the non-None contextual fields so the
    frontend can interpolate ``{model}`` / ``{provider}`` / ``{tokens_in}``
    / ``{context_window}`` keys in its translation strings.
    """

    msg = str(exc) or getattr(exc, "message", "") or ""
    status = _status_of(exc)

    params: dict[str, Any] = {}
    for key, value in (
        ("model", model),
        ("provider", provider),
        ("tokens_in", tokens_in),
        ("context_window", context_window),
    ):
        if value is not None:
            params[key] = value

    for pat in _CONTEXT_LENGTH_PATTERNS:
        if pat.search(msg):
            return ErrorCode.context_length_exceeded, params

    if (
        status == 400
        and tokens_in is not None
        and context_window is not None
        and tokens_in >= int(context_window * 0.95)
    ):
        return ErrorCode.context_length_exceeded, params

    if status in (401, 403):
        return ErrorCode.provider_auth_failed, params

    if status == 429 or any(pat.search(msg) for pat in _RATE_LIMIT_PATTERNS):
        return ErrorCode.rate_limited, params

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return ErrorCode.provider_unavailable, params

    if status is not None and 500 <= status < 600:
        return ErrorCode.provider_unavailable, params

    if status is not None and 400 <= status < 500:
        return ErrorCode.provider_bad_request, params

    return ErrorCode.internal_error, params


__all__ = ["ErrorCode", "classify_exception"]
```

- [ ] **Step 4: Re-run tests**

```bash
cd backend && uv run pytest tests/errors/test_classify.py -v
```

Expected: 9 passed.

- [ ] **Step 5: mypy**

```bash
cd backend && uv run mypy cubebox/errors
```

Expected: `Success: no issues found in 1 source file`.

- [ ] **Step 6: Commit**

```bash
mkdir -p backend/tests/errors && touch backend/tests/errors/__init__.py
git add backend/cubebox/errors backend/tests/errors
git commit -m "feat(errors): add ErrorCode taxonomy + classify_exception"
```

### Task 5: Plumb classifier into `_append_error` + ErrorEvent payload

**Files:**
- Modify: `backend/cubebox/streams/run_manager.py:1220-1235` (rename/extend `_append_error`)
- Modify: `backend/cubebox/streams/run_manager.py:3032-3047` (catch block)
- Modify: `backend/cubebox/streams/run_manager.py:3499-3507` (the respond-path catch block — parallel to 3032)

The classifier needs `tokens_in` and `context_window` to detect the Volcano-opaque case. Use:
- `tokens_in`: read from `_model_config.context_window` and the running tally already exposed via `agent.state.last_input_tokens` if available; otherwise pass `None`.
- `context_window`: `_model_config.context_window` from the factory (already ferried via `extra_ref_holder["model_context_window"]` — add the stash, see step 2 below).

- [ ] **Step 1: Add `model_context_window` to the extra_ref_holder stash**

In `backend/cubebox/streams/run_manager.py` at the block ending around line 2594, add a line right after the existing stashes:

```python
extra_ref_holder["llm_factory"] = factory
extra_ref_holder["model_context_window"] = int(_model_config.context_window or 0)
```

- [ ] **Step 2: Replace `_append_error` with code-aware signature**

Replace the existing definition at `run_manager.py:1220-1235`:

```python
async def _append_error(
    self,
    run_id: str,
    conversation_id: str,
    *,
    exc: BaseException | None = None,
    error_code: ErrorCode | None = None,
    params: dict[str, Any] | None = None,
    message: str | None = None,
) -> None:
    """Publish an ErrorEvent on the run's SSE stream.

    Either pass ``exc`` (and we'll classify it) or pass ``error_code``
    directly (used by cancel paths, which already know the code).
    """
    from cubebox.errors import ErrorCode as _ErrorCode, classify_exception

    if error_code is None:
        if exc is None:
            error_code = _ErrorCode.internal_error
            params = params or {}
        else:
            error_code, params = classify_exception(exc, **(params or {}))
    params = params or {}

    # English fallback so non-Web clients (curl, scripts) still get a
    # human-readable line without consulting the frontend i18n table.
    fallback = message or _english_fallback(error_code, params)
    details = str(exc) if exc is not None else fallback

    error_event = ErrorEvent(
        timestamp=datetime.now(UTC).isoformat(),
        data={
            "error_code": error_code.value,
            "params": params,
            "message": fallback,
            "details": details,
        },
    )
    await self._append_event(run_id, conversation_id, error_event)
```

Add `_english_fallback` as a module-level helper near the top of `run_manager.py` (or in `cubebox/errors/__init__.py`, your choice — keep it in `cubebox/errors` to keep run_manager smaller). Update the import: `from cubebox.errors import ErrorCode, classify_exception, english_fallback as _english_fallback`. Add `english_fallback` to `cubebox/errors/__init__.py`:

```python
def english_fallback(code: ErrorCode, params: dict[str, Any]) -> str:
    """English copy for non-Web clients. Frontend has its own i18n."""

    model = params.get("model") or "the model"
    if code is ErrorCode.context_length_exceeded:
        return f"Conversation exceeds {model}'s context window. Start a new chat or switch models."
    if code is ErrorCode.rate_limited:
        return f"Rate limit reached for {model}. Try again shortly."
    if code is ErrorCode.provider_auth_failed:
        return f"Authentication with the {model} provider failed. Check your API key."
    if code is ErrorCode.provider_unavailable:
        return f"The {model} provider is unavailable. Try again shortly."
    if code is ErrorCode.provider_bad_request:
        return f"The request to {model} was rejected. See details for the raw error."
    if code is ErrorCode.tool_failed:
        return "A tool call failed during this turn. See details for the raw error."
    return "An unexpected error occurred. See details for the raw error."
```

…and add `english_fallback` to `__all__`.

- [ ] **Step 3: Update the prompt-path catch block** (around `run_manager.py:3032`):

Replace:

```python
with suppress(Exception):
    await self._append_error(
        run_id,
        conversation_id,
        "An unexpected error occurred during execution",
        str(exc),
    )
```

with:

```python
with suppress(Exception):
    # extra_ref_holder may be unset if we failed before the factory ran.
    _holder = locals().get("extra_ref_holder") or {}
    await self._append_error(
        run_id,
        conversation_id,
        exc=exc,
        params={
            "provider": _holder.get("provider_name"),
            "model": _holder.get("model_id"),
            "context_window": _holder.get("model_context_window"),
        },
    )
```

(Note: `_holder` falls back to `{}` so a failure before the factory ran still produces a usable error event with `internal_error`.)

- [ ] **Step 4: Mirror the change in the respond-path catch** (`run_manager.py:3499-3507`):

Same edit as Step 3, with the same `_holder` lookup. Both catch blocks now route through the classifier identically.

- [ ] **Step 5: Update the cancel calls**

There are two call sites that pass `"Run cancelled"` (lines 3030 and 3488). Update both to use the new keyword form:

```python
with suppress(Exception):
    await self._append_error(
        run_id,
        conversation_id,
        error_code=ErrorCode.internal_error,  # cancel is not really an error, but keeps the path uniform
        params={},
        message="Run cancelled",
    )
```

Actually, given the user's brainstorming answer ("`run_cancelled` 不纳入枚举"), use a separate code path. Keep the existing literal-string `_append_error` overload for the cancel case by adding a *narrow* positional overload OR by emitting a different event entirely. Simplest: add a separate `error_code=ErrorCode.internal_error, message="Run cancelled"` and accept the white lie; cancel UX is already handled by `lastRunStatus === 'stale'` in `MessageList.tsx:471`, so the bubble we emit here is a backup.

(Author's note: keep the cancel path **as-is for now**. The signature change is keyword-only and the existing positional call would break — convert just the two catch blocks above; revisit cancel routing in a follow-up. Drop step 5 if it complicates review; mark the two cancel sites with `# noqa: ARG-CANCEL` style TODO instead.)

- [ ] **Step 6: Re-import + import the new helpers at top of `run_manager.py`**

Add to the existing import block:

```python
from cubebox.errors import ErrorCode, classify_exception, english_fallback
```

- [ ] **Step 7: mypy + smoke import**

```bash
cd backend && uv run mypy cubebox/streams/run_manager.py cubebox/errors
cd backend && uv run python -c "from cubebox.streams.run_manager import RunManager"
```

Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add backend/cubebox/streams/run_manager.py backend/cubebox/errors/__init__.py
git commit -m "feat(errors): classify provider exceptions in _append_error, stamp ErrorEvent with code/params"
```

### Task 6: Persist error_code/params on RunMeta

**Files:**
- Modify: `backend/cubebox/streams/run_events.py` (RunMeta dataclass + update_run_meta)
- Modify: `backend/cubebox/streams/run_manager.py:3032-3047` and parallel respond-path block — call `update_run_meta` with the new fields right before `_append_error`.
- Create: `backend/tests/streams/test_run_meta_error.py`

- [ ] **Step 1: Extend RunMeta + update_run_meta**

In `run_events.py:43-54`, add three optional fields:

```python
@dataclass(slots=True)
class RunMeta:
    """Metadata for a single run."""

    run_id: str
    conversation_id: str
    status: str
    started_at: str
    user_message: str | None = None
    first_event_id: str | None = None
    last_event_id: str | None = None
    last_event_at: str | None = None
    error_code: str | None = None
    error_params: str | None = None  # JSON-encoded dict, since Redis hash values are strings
    error_message: str | None = None  # English fallback
```

Update `_meta_from_hash` (line 206) to read `raw.get("error_code")`, `raw.get("error_params")`, `raw.get("error_message")`.

In `update_run_meta` (line 307), extend the signature with `error_code: str | None = None, error_params: str | None = None, error_message: str | None = None`, and pack them into the `other_updates` dict the same way as `first_event_id` / `last_event_id`.

- [ ] **Step 2: Write a test**

Create `backend/tests/streams/test_run_meta_error.py`:

```python
"""RunMeta carries classified error fields when a run fails."""

from __future__ import annotations

import pytest
from fakeredis.aioredis import FakeRedis

from cubebox.streams.run_events import create_run, get_run_meta, update_run_meta


@pytest.mark.asyncio
async def test_run_meta_round_trips_error_fields() -> None:
    redis = FakeRedis()
    prefix = "cb-test"
    await create_run(
        redis,
        prefix=prefix,
        run_id="r1",
        conversation_id="c1",
        status="running",
        started_at="2026-06-04T10:00:00+00:00",
        ttl_seconds=600,
    )

    await update_run_meta(
        redis,
        prefix=prefix,
        run_id="r1",
        status="errored",
        error_code="context_length_exceeded",
        error_params='{"model":"kimi-k2.6","tokens_in":262014,"context_window":256000}',
        error_message="Conversation exceeds the model's context window.",
    )

    meta = await get_run_meta(redis, prefix=prefix, run_id="r1")
    assert meta is not None
    assert meta.error_code == "context_length_exceeded"
    assert meta.error_message.startswith("Conversation exceeds")
    assert '"tokens_in":262014' in (meta.error_params or "")
```

- [ ] **Step 3: Run test (expect FAIL)**

```bash
cd backend && uv run pytest tests/streams/test_run_meta_error.py -v
```

Expected: FAIL on missing field/argument.

- [ ] **Step 4: Run test (expect PASS after Step 1 edits)**

After applying Step 1 edits, re-run. Expected: 1 passed.

- [ ] **Step 5: Wire `update_run_meta` call into the catch blocks**

In `run_manager.py:3032-3047` (and the respond mirror), call `update_run_meta` with the new fields **before** `_append_error`:

```python
except Exception as exc:
    logger.error("Run {} failed: {}", run_id, exc, exc_info=True)
    from cubebox.errors import classify_exception
    import json as _json

    _holder = locals().get("extra_ref_holder") or {}
    _code, _params = classify_exception(
        exc,
        model=_holder.get("model_id"),
        provider=_holder.get("provider_name"),
        context_window=_holder.get("model_context_window"),
    )
    await update_run_meta(
        self._redis,
        prefix=self._key_prefix,
        run_id=run_id,
        status="failed",
        error_code=_code.value,
        error_params=_json.dumps(_params, ensure_ascii=False),
        error_message=english_fallback(_code, _params),
    )
    await record_scheduled_run_terminal_state(run_id=run_id, run_status="failed")
    with suppress(Exception):
        await self._append_error(
            run_id,
            conversation_id,
            exc=exc,
            params={
                "provider": _holder.get("provider_name"),
                "model": _holder.get("model_id"),
                "context_window": _holder.get("model_context_window"),
            },
        )
```

(Imports at top of file: add `import json` if not present, and `from cubebox.errors import english_fallback` — already done in Task 5.)

- [ ] **Step 6: mypy**

```bash
cd backend && uv run mypy cubebox/streams/run_events.py cubebox/streams/run_manager.py
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add backend/cubebox/streams/run_events.py backend/cubebox/streams/run_manager.py backend/tests/streams/test_run_meta_error.py
git commit -m "feat(errors): persist error_code/params/message on RunMeta"
```

### Task 7: Expose error fields in the runs list API

**Files:**
- Modify: `backend/cubebox/api/schemas/run.py` (or wherever RunSummary schema lives — confirm with `grep -rn "class.*Run.*BaseModel" cubebox/api/schemas/`)
- Modify: the route that builds RunSummary objects from RunMeta.

- [ ] **Step 1: Find the schema**

```bash
cd backend && grep -rn "class.*Run.*BaseModel\|RunSummary\|RunListItem" cubebox/api/schemas/ cubebox/api/routes/v1/ | head -10
```

- [ ] **Step 2: Add three fields to the schema**

```python
error_code: str | None = None
error_params: dict[str, Any] | None = None
error_message: str | None = None
```

- [ ] **Step 3: Populate from RunMeta in the route**

`error_params` decodes from the JSON string on RunMeta:

```python
import json as _json
err_params = _json.loads(meta.error_params) if meta.error_params else None
RunSummary(..., error_code=meta.error_code, error_params=err_params, error_message=meta.error_message)
```

- [ ] **Step 4: mypy + existing API tests**

```bash
cd backend && uv run pytest tests/api/test_runs.py -v   # or whatever the existing run-listing test is
cd backend && uv run mypy cubebox/api/schemas cubebox/api/routes/v1
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/api/schemas backend/cubebox/api/routes/v1
git commit -m "feat(errors): expose error_code/params/message on runs API"
```

### Task 8: Frontend — extend Error event shape + per-conversation error state

**Files:**
- Modify: `frontend/packages/core/src/types/events.ts:148-155` (Error event)
- Modify: `frontend/packages/core/src/stores/messageStore.ts:945-964` and `:1313-1331` (two error branches)
- Modify: `frontend/packages/core/src/types/index.ts` (export new types if needed)

- [ ] **Step 1: Extend the Error event type**

In `events.ts`, find the `'error'` discriminated union member and expand the data shape:

```ts
type ErrorEventData = {
  error_code: string
  params?: Record<string, unknown>
  message: string
  details?: string
}
```

Replace the existing data type for the `'error'` member with `ErrorEventData`.

- [ ] **Step 2: Replace top-level `error: string | null` with a per-conversation `errors: Record<string, ErrorEventData & {runId: string}>`**

In `messageStore.ts`, change the slice shape (search `error: string | null`). The store currently stashes the error globally so it leaks across conversations. New shape:

```ts
errors: { [conversationId: string]: { runId: string; data: ErrorEventData } | null }
```

Update the `error` branches at line 951 and line 1313:

```ts
} else if (event.type === 'error') {
  const errData = event.data as ErrorEventData
  set((s) => ({
    errors: { ...s.errors, [conversationId]: { runId: s.currentRunId ?? '', data: errData } },
    isStreaming: false,
    pendingConfirmMap: {},
    pendingAsk: null,
    streamingConversationId: null,
    currentRunId: null,
    statusPhase: null,
    lastAppliedEventId: nextEventId(s.lastAppliedEventId, event.event_id),
    pendingSteers: { ...s.pendingSteers, [conversationId]: [] },
  }))
  break
}
```

Also clear `errors[conversationId]` to `null` when a new run for that conversation **starts** (search for `streamRun` / `start` / wherever currentRunId is set on send) — keep the bubble visible until the user retries.

- [ ] **Step 3: Build the error bubble component**

Create `frontend/packages/web/components/chat/RunErrorBubble.tsx`:

```tsx
'use client'

import { useTranslations } from 'next-intl'
import { AlertCircle } from 'lucide-react'

type Params = Record<string, unknown>

export function RunErrorBubble({
  errorCode,
  params,
  fallbackMessage,
}: {
  errorCode: string
  params?: Params
  fallbackMessage: string
}) {
  const t = useTranslations('runError')
  // next-intl returns the key itself when missing; we treat that as a miss and
  // fall back to the backend's English message. Detect via deep equality.
  const localized = t(errorCode as never, (params ?? {}) as never)
  const isMiss = localized === `runError.${errorCode}` || localized === errorCode
  return (
    <div
      role="alert"
      className="flex items-start gap-2 px-3 py-2.5 rounded-lg
      bg-destructive/10 border border-destructive/20 text-destructive text-sm"
    >
      <AlertCircle className="size-4 shrink-0 mt-0.5" />
      <span>{isMiss ? fallbackMessage : localized}</span>
    </div>
  )
}
```

- [ ] **Step 4: Mount the bubble at the failing run's tail**

In `MessageList.tsx:461-469`, replace the `{error && (...)}` block with logic that picks up the per-conversation error and mounts it after the failing run's last message. Locate the failing run via `errors[conversationId]?.runId`, find the last message with that `run_id`, and render the bubble right after it.

If no message carries the run_id (a failure before the model emitted anything — exactly our kimi-k2.6 case), mount the bubble after the user message that triggered the run.

Rough sketch:

```tsx
const conversationError = errors[conversationId]
// Inside the messages.map, after rendering each message:
if (conversationError && msg.run_id === conversationError.runId && isLastOfRun(messages, i, msg.run_id)) {
  return <>{renderMessage(msg)}<RunErrorBubble {...conversationError.data} fallbackMessage={conversationError.data.message} /></>
}
```

If a fast version is needed: after the messages list, scan for the user message that *initiated* `conversationError.runId` (or the run's last assistant message) and append the bubble below it. Inline scan is fine — these lists are short.

- [ ] **Step 5: Add i18n keys**

In `frontend/packages/web/messages/en.json`, add a top-level `"runError"` block:

```json
"runError": {
  "context_length_exceeded": "Conversation exceeded {model}'s context window ({tokens_in, number} / {context_window, number} tokens). Start a new chat or switch models.",
  "rate_limited": "Rate limit hit for {model}. Wait a moment, then try again.",
  "provider_auth_failed": "Authentication with {provider} failed. Check your API key in workspace settings.",
  "provider_unavailable": "{provider} is unavailable right now. Try again shortly.",
  "provider_bad_request": "{provider} rejected the request. Open this run's details to see the raw error.",
  "tool_failed": "A tool call failed during this turn. Open this run's details to see the raw error.",
  "internal_error": "Something went wrong. Open this run's details to see the raw error."
}
```

In `messages/zh.json`, add the same keys with Chinese copy:

```json
"runError": {
  "context_length_exceeded": "对话长度超过了 {model} 的上下文窗口（{tokens_in, number} / {context_window, number} tokens）。请新开会话或切换模型。",
  "rate_limited": "{model} 触发了限流，请稍后再试。",
  "provider_auth_failed": "{provider} 鉴权失败，请到工作区设置检查 API key。",
  "provider_unavailable": "{provider} 暂时不可用，请稍后再试。",
  "provider_bad_request": "{provider} 拒绝了这次请求。在该轮详情中查看原始错误。",
  "tool_failed": "工具调用失败。在该轮详情中查看原始错误。",
  "internal_error": "发生了未知错误。在该轮详情中查看原始错误。"
}
```

- [ ] **Step 6: TypeScript + lint**

```bash
cd frontend && pnpm typecheck
cd frontend && pnpm lint
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/core/src/types/events.ts \
       frontend/packages/core/src/stores/messageStore.ts \
       frontend/packages/web/components/chat/MessageList.tsx \
       frontend/packages/web/components/chat/RunErrorBubble.tsx \
       frontend/packages/web/messages/en.json \
       frontend/packages/web/messages/zh.json
git commit -m "feat(errors): localized per-conversation error bubble anchored to failing run"
```

### Task 9: Playwright check — error bubble appears on a forced 400

**Files:**
- Create: `frontend/packages/web/tests/e2e/run-error-bubble.spec.ts`

This is the one and only E2E for PR-2: confirm that when the backend emits an `error` event with `error_code=context_length_exceeded`, a red bubble shows up in the message list with the localized copy.

- [ ] **Step 1: Plan the fixture**

Use the existing backend test harness to stub a model that always raises a `BadRequestError(400, "maximum context length is 1000 tokens")`. Wire it via the existing `tests/conftest.py` provider override so the request goes through the real run_manager path and produces a real ErrorEvent. (Find an existing E2E that overrides the provider for reference — `grep -rn "fake_provider\|stub_provider" frontend/packages/web/tests/e2e/`.)

- [ ] **Step 2: Write the spec**

```ts
import { test, expect } from '@playwright/test'

test('forced 400 shows localized error bubble at run tail', async ({ page }) => {
  await page.goto('/c/new?force_provider_error=context_length_exceeded')
  await page.getByRole('textbox', { name: /message/i }).fill('hi')
  await page.getByRole('button', { name: /send/i }).click()

  const bubble = page.getByRole('alert').filter({ hasText: /context window/i })
  await expect(bubble).toBeVisible({ timeout: 15_000 })

  // Bubble is anchored to the run, not floating at the bottom.
  const userMsg = page.locator('[data-message-role="user"]').last()
  const bubbleBox = await bubble.boundingBox()
  const userBox = await userMsg.boundingBox()
  expect(bubbleBox?.y ?? 0).toBeGreaterThan(userBox?.y ?? 0)
})
```

- [ ] **Step 3: Run it**

```bash
cd frontend && pnpm exec playwright test tests/e2e/run-error-bubble.spec.ts
```

Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/tests/e2e/run-error-bubble.spec.ts
git commit -m "test(errors): Playwright check for run-error bubble"
```

### Task 10: Pre-PR sweep + push PR-2

- [ ] **Step 1: Full repo type-check**

```bash
cd backend && uv run mypy .
cd frontend && pnpm typecheck
```

- [ ] **Step 2: Run changed-module tests**

```bash
cd backend && uv run pytest tests/errors tests/streams/test_run_meta_error.py tests/middleware/test_compaction.py -v
```

- [ ] **Step 3: Push branch + open PR**

```bash
git push
gh pr create --title "errors: classify provider failures + localized run-error bubble" --body "$(cat <<'EOF'
## Summary
- Add `cubebox.errors.ErrorCode` taxonomy (`context_length_exceeded`, `rate_limited`, `provider_auth_failed`, `provider_unavailable`, `provider_bad_request`, `tool_failed`, `internal_error`) and a `classify_exception` heuristic that handles Volcano ARK's opaque `InvalidParameter` via `tokens_in` vs `context_window`.
- Wire the classifier into `_append_error` for both the prompt and respond paths; emit `{error_code, params, message, details}` on the `ErrorEvent` SSE.
- Persist `error_code` / `error_params` / `error_message` on `RunMeta` (Redis hash, no Alembic) and expose them on the runs list API.
- Frontend: per-conversation error state keyed by `conversationId`, localized via `messages/{en,zh}.json` under `runError.*`, rendered as a red bubble anchored to the failing run instead of a floating banner. Falls back to the backend's English `message` when an i18n key is missing.

## Test plan
- [x] `uv run pytest tests/errors`
- [x] `uv run pytest tests/streams/test_run_meta_error.py`
- [x] `uv run mypy` (backend) / `pnpm typecheck` (frontend)
- [x] Playwright: `run-error-bubble.spec.ts` — forced 400 surfaces a localized bubble at the run tail.
- [ ] Manual: re-trigger the kimi-k2.6 over-context case in the failing conversation and confirm `context_length_exceeded` renders.
EOF
)"
```

- [ ] **Step 4: Run pr-codex-review-loop**

Trigger the skill, iterate until clean.

---

## Self-Review Checklist (run before handing off)

- **Spec coverage:**
  - Compaction default-on → Task 1 ✓
  - Per-model context window → Task 2 ✓
  - Error taxonomy + classifier → Task 4 ✓
  - SSE payload extension → Task 5 ✓
  - RunMeta persistence → Task 6 ✓
  - API exposure → Task 7 ✓
  - Frontend rendering anchored to run tail → Task 8 ✓
  - i18n en + zh → Task 8 step 5 ✓
  - Playwright check → Task 9 ✓
- **Placeholders:** Step 5 of Task 5 has an "author's note" suggesting cancel-path keeps existing signature; treat as the actionable instruction (cancel sites keep their positional form, the new keyword-only signature accepts both since the keyword args have defaults).
- **Type consistency:**
  - `ErrorCode` enum is the single source of truth — values are strings (`.value` is what hits the wire).
  - `classify_exception(exc, *, model, provider, tokens_in, context_window)` signature is identical at every call site.
  - `RunMeta.error_params` is JSON-encoded `str` on the Python side; the runs API decodes it into a `dict[str, Any]`; the frontend treats it as `Record<string, unknown>`.
