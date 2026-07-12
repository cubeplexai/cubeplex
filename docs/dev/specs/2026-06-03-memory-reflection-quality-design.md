# Memory Reflection Quality Improvements

**Date:** 2026-06-03  
**Branch:** feat/memory-reflection-quality  
**Status:** Draft

## Background

The per-turn reflection agent extracts memories after each agent run and saves
them via `memory_save` / `memory_update`. Analysis of a real conversation
(`conv-1fwZQ8u3ZDukx3`) revealed three structural problems that cause low-quality
memory output:

1. The reflection agent never sees what tools were called or what they returned.
2. It has no conservative "store less" bias — so kimi-k2.6 (and other verbose
   models) over-save, storing the same preference 6+ times.
3. It has no view of what's already in memory when it starts, forcing it to call
   `memory_search` unprompted — a step it frequently skips.

The consolidation layer (Layer 2) is the designed safety net, but it runs at
most once per 5 runs and its `max_tokens=1500` budget makes it hard to merge 6
nearly-identical preference entries in one pass. Fixing Layer 1 (reflection) is
the right lever.

## Problem Details

### P1: tool_summaries always empty

`run_manager.py` constructs `ReflectionTurn` with `tool_summaries=[]` hardcoded:

```python
turn=ReflectionTurn(
    user_message=user_msg_text,
    assistant_message=last_assistant,
    tool_summaries=[],   # never populated
)
```

The reflection agent's seed prompt therefore looks like:

```
Last turn for review:

USER: 时间过期了重新生成一个 code

ASSISTANT: 好的，重新生成了一个设备码：6EFB-EB07
```

It can infer facts from the assistant's text, but it cannot see:
- Which tools ran
- What they returned (e.g. a twitter-cli auth test returning HTTP 403)
- Whether a tool errored

This causes two failure modes:
- Facts that only appear in tool results (not in the assistant summary) are
  missed entirely.
- The assistant sometimes summarises tool output incorrectly; the reflection
  agent has no ground truth to check against.

### P2: No conservative extraction bias

The current `REFLECTION_SYSTEM_PROMPT` lists what to save and what to skip, but
gives no default stance. A verbose model that is uncertain will save rather than
skip. Supermemory's prompt (from hermes-agent) explicitly states:

> "Only extract things useful in future conversations. Most messages are not
> worth remembering. … When in doubt, store less."

This single line changes the default decision from "save if unsure" to "skip if
unsure." The cubeplex prompt has no equivalent.

### P3: No existing memory in reflection seed

The consolidation pass correctly injects existing memory items before the LLM
runs:

```python
existing_text = "\n".join(f"- [{m.id}] ({m.type.value}) {m.content}" for m in existing)
prompt = f"Existing personal memory items:\n{existing_text}\n\nConversation transcript:..."
```

The reflection runner does not. The reflection agent must call `memory_search`
itself, but the system prompt only encourages this ("search first") without
enforcing it. Models frequently skip the search and call `memory_save` directly,
producing duplicates.

## Proposed Changes

### C1: Populate tool_summaries in run_manager

After the main agent becomes idle, iterate over the turn's tool executions and
build a compact summary for each: tool name, a short args digest, and the
outcome (ok / error / truncated result excerpt).

The summary format mirrors what `ReflectionTurn.tool_summaries` already
supports:

```python
{"name": "execute", "args_summary": "pip install twitter-cli", "outcome": "ok"}
{"name": "execute", "args_summary": "twitter whoami", "outcome": "error: HTTP 403"}
```

The reflection seed prompt already renders these:

```
Tools called in this turn:
- execute(pip install twitter-cli) -> ok
- execute(twitter whoami) -> error: HTTP 403
```

**Scope:** `run_manager.py` — the `_run_reflection` closure that builds
`ReflectionTurn`. The agent's `state.messages` already contains the tool
execution results; extract them there.

**Limits:** Cap at 10 tool summaries per turn; truncate args and result text to
150 chars each. This keeps the reflection prompt bounded.

### C2: Add conservative bias to REFLECTION_SYSTEM_PROMPT

Add two sentences that shift the default from "save if unsure" to "skip if unsure":

```
Most turns contain nothing worth saving. When in doubt, do not save.
```

Also make the "search first" instruction more concrete — require calling
`memory_search` before any `memory_save`:

```
Before calling memory_save, always call memory_search to check whether
a closely related item already exists. If one does, call memory_update
instead, or skip if the existing item already covers it.
```

**Scope:** `cubeplex/prompts/reflection_system.py` only.

### C3: Inject top personal memory items into reflection seed

Load the user's active personal memory items and prepend them to the seed
prompt, the same way the consolidation pass does. The reflection agent then has
the full picture without needing to search.

Seed prompt becomes:

```
Your current memory for this user (personal, active):
- [mem-abc] (preference) 用户偏好中文交流
- [mem-def] (project_fact) CubePi 项目，开发者是 xfgong ...
...

Last turn for review:

USER: ...
ASSISTANT: ...
Tools called in this turn:
- ...
```

**Scope:**
- `ReflectionTurn` gets an optional `existing_memory_items` field
  (list of `(id, type, content)` tuples).
- `ReflectionRunner._build_seed_prompt` renders them when present.
- `run_manager.py` loads personal memory via `MemoryRepository` before spawning
  the reflection task and passes them through.

**Limits:** Cap at 40 items, 200 chars per item content. If the user has more
than 40 active personal items, take the 40 most recently used
(`last_used_at DESC, created_at DESC`). This is consistent with the relevance
tier's snapshot budget.

## What This Does Not Change

- The consolidation layer (Layer 2) is unchanged. It remains the long-period
  cleanup pass that can merge/archive across the full history.
- The exact-content deduplication in `MemoryService.create` is unchanged. It
  catches mechanical exact-duplicate writes regardless of the reflection prompt.
- The memory middleware (how memories are injected into the main agent's context)
  is unchanged.
- The reflection model selection is unchanged (stays coupled to the main agent's
  model). A separate cheap-model config is a future concern.

## File Inventory

| File | Change |
|---|---|
| `cubeplex/prompts/reflection_system.py` | C2: rewrite prompt |
| `cubeplex/services/reflection_runner.py` | C1+C3: add `existing_memory_items` to `ReflectionTurn`; update `_build_seed_prompt` |
| `cubeplex/streams/run_manager.py` | C1: extract tool summaries; C3: load and pass memory items |

No new tables, no migrations, no API changes.

## Success Criteria

- A re-run of a conversation similar to `conv-1fwZQ8u3ZDukx3` should not produce
  more than one memory item expressing the same preference (e.g. "prefers
  Chinese") over 20 turns.
- Tool-result facts (e.g. "twitter-cli returned HTTP 403") should appear in
  saved memories when the assistant's text alone does not contain that
  information.
- A turn where the user expresses no new information should result in zero
  `memory_save` calls from the reflection agent.
