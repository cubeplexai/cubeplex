# cubepi JSONL Tracing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attach cubepi 0.4's `Tracer` + `JsonlSpanExporter` to cubeplex's per-run agent so each conversation turn writes an on-disk OTLP/JSON trace, gated behind a new `tracing:` config block.

**Architecture:** A small config-driven factory (`cubeplex/agents/tracing.py`) builds a per-run `Tracer` (or returns `None` when disabled / on any failure). `RunManager._run_cubepi_path` wraps `agent.prompt(...)` with the tracer via an `AsyncExitStack`, so attach/detach/flush happen per run with no global state. JSONL files shard by `run_id` automatically.

**Tech Stack:** Python 3.12, cubepi 0.4 (`cubepi.tracing`), dynaconf config, pytest + pytest-asyncio.

Spec: `docs/dev/specs/2026-05-20-cubepi-tracing-jsonl-design.md`.

---

### Task 1: Config-driven Tracer factory

**Files:**
- Create: `cubeplex/agents/tracing.py`
- Test: `tests/unit/agents/test_tracing.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/agents/test_tracing.py
"""Unit tests for the per-run cubepi Tracer factory."""

from __future__ import annotations

import pytest

from cubeplex.agents import tracing as tracing_mod


def _fake_config(values: dict[str, object]):
    """Return a stub with a dynaconf-like .get(key, default)."""

    class _Stub:
        def get(self, key: str, default: object = None) -> object:
            return values.get(key, default)

    return _Stub()


def test_build_run_tracer_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(
        tracing_mod, "config", _fake_config({"tracing.enabled": False})
    )
    assert tracing_mod.build_run_tracer() is None


def test_build_run_tracer_missing_key_defaults_disabled(monkeypatch):
    # No tracing.enabled key at all -> default False -> None.
    monkeypatch.setattr(tracing_mod, "config", _fake_config({}))
    assert tracing_mod.build_run_tracer() is None


@pytest.mark.asyncio
async def test_build_run_tracer_enabled_returns_tracer(monkeypatch, tmp_path):
    from cubepi.tracing import Tracer

    monkeypatch.setattr(
        tracing_mod,
        "config",
        _fake_config(
            {
                "tracing.enabled": True,
                "tracing.directory": str(tmp_path),
                "tracing.record_content": True,
                "env": "development",
            }
        ),
    )
    tracer = tracing_mod.build_run_tracer()
    assert isinstance(tracer, Tracer)
    # Clean up so the BatchSpanProcessor / atexit hook doesn't leak.
    await tracer.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/agents/test_tracing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cubeplex.agents.tracing'` (or `AttributeError: build_run_tracer`).

- [ ] **Step 3: Write the factory**

```python
# cubeplex/agents/tracing.py
"""Build a per-run cubepi Tracer from cubeplex config.

Tracing is opt-in via the ``tracing:`` config block. When disabled — or when
construction fails for any reason — the run proceeds untraced. A tracing fault
must never break a run, so every failure path returns ``None``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from cubeplex.config import config

if TYPE_CHECKING:
    from cubepi.tracing import Tracer


def build_run_tracer() -> "Tracer | None":
    """Return a configured cubepi Tracer, or ``None`` when tracing is disabled.

    Reads ``tracing.enabled`` / ``tracing.directory`` / ``tracing.record_content``
    from config. Import or construction failures are logged and swallowed.
    """
    if not config.get("tracing.enabled", False):
        return None
    try:
        from cubepi.tracing import JsonlSpanExporter, Tracer

        directory = config.get("tracing.directory", "./cubepi-traces")
        record_content = bool(config.get("tracing.record_content", False))
        return Tracer(
            service_name="cubeplex",
            deployment_environment=str(config.get("env", "development")),
            agent_name="cubeplex-agent",
            exporters=[JsonlSpanExporter(directory=directory)],
            record_content=record_content,
        )
    except Exception as exc:
        logger.warning("Tracing unavailable, continuing untraced: {}", exc)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/agents/test_tracing.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add cubeplex/agents/tracing.py tests/unit/agents/test_tracing.py
git commit -m "feat(tracing): config-driven per-run cubepi Tracer factory"
```

---

### Task 2: Wire the tracer into the cubepi run path

**Files:**
- Modify: `cubeplex/streams/run_manager.py` (import line ~6; the `agent.prompt(_user_msg)` block ~1133-1139)

- [ ] **Step 1: Extend the contextlib import**

Find (near the top of the file, currently line 6):

```python
from contextlib import suppress
```

Replace with:

```python
from contextlib import AsyncExitStack, suppress
```

- [ ] **Step 2: Wrap `agent.prompt` with the tracer**

In `_run_cubepi_path`, find this block (currently ~line 1128-1139):

```python
            _user_msg = _UserMessage(
                content=[_TextContent(text=content)],
                timestamp=_time.time(),
                metadata=_user_msg_metadata,
            )
            try:
                await agent.prompt(_user_msg)
            finally:
                # Signal drainer and wait for it to flush remaining events so
                # all SSE dicts are published before citation buffers flush.
                await sse_queue.put(None)
                await drainer
```

Replace with:

```python
            _user_msg = _UserMessage(
                content=[_TextContent(text=content)],
                timestamp=_time.time(),
                metadata=_user_msg_metadata,
            )

            from cubeplex.agents.tracing import build_run_tracer

            tracer = build_run_tracer()
            try:
                async with AsyncExitStack() as _trace_stack:
                    if tracer is not None:
                        # LIFO exit: attached() detaches + awaits its flush
                        # task first, then the tracer's __aexit__ shuts down
                        # (force_flush + close exporters) — so this run's
                        # spans are on disk before _run_cubepi_path returns.
                        # attached().__aenter__ calls Recorder/provider
                        # subscription work that can raise; isolate it so a
                        # tracing fault runs the turn untraced rather than
                        # failing the run (spec §3).
                        try:
                            await _trace_stack.enter_async_context(tracer)
                            await _trace_stack.enter_async_context(tracer.attached(agent))
                        except Exception as _trace_exc:
                            logger.warning(
                                "Tracing attach failed, continuing untraced: {}", _trace_exc
                            )
                            await _trace_stack.aclose()
                    await agent.prompt(_user_msg)
            finally:
                # Signal drainer and wait for it to flush remaining events so
                # all SSE dicts are published before citation buffers flush.
                await sse_queue.put(None)
                await drainer
```

- [ ] **Step 3: Verify it imports and type-checks**

Run: `uv run mypy cubeplex/streams/run_manager.py cubeplex/agents/tracing.py`
Expected: `Success: no issues found`.

- [ ] **Step 4: Run the run-manager unit tests to confirm no regression**

Run: `uv run pytest tests/unit/streams/ -v`
Expected: PASS (no regressions; tracing is disabled by default in base config so the wrapped path behaves exactly as before).

- [ ] **Step 5: Commit**

```bash
git add cubeplex/streams/run_manager.py
git commit -m "feat(tracing): attach per-run cubepi Tracer around agent.prompt"
```

---

### Task 3: Add the `tracing:` config block

**Files:**
- Modify: `config.yaml` (after the `compaction:` block, ~line 296)
- Modify: `config.development.yaml` (alongside other dev overrides)

- [ ] **Step 1: Add the base config block**

In `config.yaml`, after the `compaction:` block (ends ~line 296), add (matching the 2-space `default:` indentation used by `compaction:` / `sandbox:`):

```yaml

  # cubepi agent tracing (OTLP/JSON spans on disk; default off; opt-in per env)
  # When enabled, each run writes <directory>/<YYYY-MM-DD>/<run_id>.jsonl.
  tracing:
    enabled: false
    directory: "./cubepi-traces"
    # record_content=true captures full prompts / responses / tool args+results
    # in the trace — useful for debugging, larger files, may contain sensitive data.
    record_content: false
```

- [ ] **Step 2: Add the development overrides**

In `config.development.yaml`, under the `development:` mapping (alongside the
other dev override blocks such as `sandbox:`), add:

```yaml

  # Tracing on by default in dev so local runs emit trace files.
  tracing:
    enabled: true
    record_content: true
```

- [ ] **Step 3: Verify config loads and resolves**

Run:
```bash
ENV_FOR_DYNACONF=development uv run python -c "from cubeplex.config import config; print('enabled=', config.get('tracing.enabled'), 'dir=', config.get('tracing.directory'), 'content=', config.get('tracing.record_content'))"
```
Expected: `enabled= True dir= ./cubepi-traces content= True`

Run (base):
```bash
ENV_FOR_DYNACONF=production uv run python -c "from cubeplex.config import config; print('enabled=', config.get('tracing.enabled', False))"
```
Expected: `enabled= False`

- [ ] **Step 4: Commit**

```bash
git add config.yaml config.development.yaml
git commit -m "feat(tracing): add tracing config block (off base, on in dev)"
```

---

### Task 4: Manual E2E — confirm trace files are emitted

**Files:** none (verification only). Ports from `.worktree.env`: backend `:8038`, frontend `:3038`.

This feature has no deterministic automated assertion surface beyond "a
well-formed trace file appears," so verification is manual inspection of the
emitted JSONL (per spec §6).

- [ ] **Step 1: Start the backend (worktree env)**

Run (from the worktree `backend/`):
```bash
ENV_FOR_DYNACONF=development uv run python main.py
```
Expected: server up on `127.0.0.1:8038`; logs show no tracing errors.

- [ ] **Step 2: Start the frontend (worktree env)**

Run (from the worktree `frontend/`):
```bash
pnpm dev
```
Expected: Next dev on `:3038` (PORT comes from `.worktree.env` via the wrapper — do not hardcode 3000).

- [ ] **Step 3: Send a chat turn that uses a tool**

Open `http://localhost:3038`, log in, send a prompt that triggers at least one
tool call (e.g. ask it to run a calculation or read/write a file) so
`execute_tool` spans are produced.

- [ ] **Step 4: Inspect the emitted trace file**

Run (from the worktree `backend/`):
```bash
ls -R cubepi-traces/
# pick the newest file:
f=$(ls -t cubepi-traces/*/*.jsonl | head -1); echo "$f"; wc -l "$f"
```
Expected: a file at `cubepi-traces/<YYYY-MM-DD>/<run_id>.jsonl` with one JSON
object per line.

- [ ] **Step 5: Verify span shape and content**

Run:
```bash
f=$(ls -t cubepi-traces/*/*.jsonl | head -1)
uv run python -c "import json,sys; [print(json.loads(l)['name']) for l in open(sys.argv[1])]" "$f" | sort | uniq -c
```
Expected: span names include `invoke_agent`, `cubepi.turn`, one or more
`chat ...` (CLIENT) spans, and `execute_tool ...` for the tool call(s).

Then confirm content recording is on (dev `record_content=true`):
```bash
f=$(ls -t cubepi-traces/*/*.jsonl | head -1)
grep -c "gen_ai.input.messages\|cubepi.llm.raw_request" "$f"
```
Expected: a count > 0 (the chat span carries the recorded prompt/request).

- [ ] **Step 6: Note the result in the PR description**

No commit. Capture the `uniq -c` span-name output and paste it into the PR body
as the verification evidence.

---

## Self-Review

- **Spec coverage:**
  - §2 integration point → Task 2 (wraps `agent.prompt` in `_run_cubepi_path`). ✓
  - §3 per-run lifecycle + failure isolation → Task 1 (factory swallows failures) + Task 2 (`AsyncExitStack`, LIFO flush order). ✓
  - §4 config (`enabled`/`directory`/`record_content`, base off / dev on) → Task 3. ✓
  - §5 out-of-scope (OTLP/metrics) → not implemented; factory builds only `JsonlSpanExporter`. ✓
  - §6 testing (manual JSONL inspection) → Task 4. ✓
- **Placeholder scan:** no TBD/TODO; every code/command step is concrete. ✓
- **Type consistency:** factory name `build_run_tracer` is used identically in Task 1 (definition) and Task 2 (import + call). `Tracer` / `JsonlSpanExporter` match cubepi's public `cubepi.tracing` exports. ✓
