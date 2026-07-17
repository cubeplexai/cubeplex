# Memory Reflection Hook Design

**Date:** 2026-06-02
**Status:** Approved

## Problem

Agent does not proactively save memories. The `MEMORY_AUTHORING_BLOCK` in the system
prompt instructs it to call `memory_save`, but in practice the agent rarely does so on
its own. When a user expresses a preference, corrects the agent, or shares an opinion,
that information is lost after the conversation ends.

## Solution

Add an `on_run_end` middleware hook to cubepi that fires once after the entire agent run
completes (all turns and tool calls done, before `AgentEndEvent`). cubeplex implements
a `ReflectionMiddleware` that uses this hook to inject a short reflection prompt, causing
the agent to review the current conversation turn and call `memory_save` / `memory_update`
if appropriate. No separate LLM process is spawned — this is one extra turn appended to
the existing run.

## cubepi Changes

### `cubepi/middleware/base.py`

Add `on_run_end` to the `Middleware` base class:

```python
async def on_run_end(
    self, ctx: AgentContext, *, signal: object = None
) -> list[Message] | None:
    raise NotImplementedError
```

Add composition in `compose_middleware`: collect all middleware that implement
`on_run_end`, call them in sequence, concatenate the returned message lists. The
composed hook is stored in `hooks["on_run_end"]`.

### `cubepi/agent/loop.py`

In `run_agent_loop`, after the `get_follow_up_messages` drain and before the outer
`break`, insert the `on_run_end` call. A local `_reflection_fired: bool = False`
flag ensures it fires exactly once per `prompt()` call, even if the reflection pass
itself triggers another loop iteration.

```python
# after follow_up drain, before break
if on_run_end and not _reflection_fired:
    _reflection_fired = True
    inject = await on_run_end(current_context, signal=opts.signal)
    if inject:
        for msg in inject:
            current_context.messages.append(msg)
            new_messages.append(msg)
        first_turn = False
        continue   # re-enters outer loop for reflection pass

break
```

### `cubepi/agent/agent.py`

Extract `on_run_end` from `_mw_hooks` (same pattern as `after_model_response`) and
thread it through `_run_loop` → `run_agent_loop`.

## cubeplex Changes

### `cubeplex/prompts/reflection.py`

New file. Contains `REFLECTION_PROMPT`:

```
This run is complete. Before we finish: briefly review what happened in this
conversation turn. Did the user express a preference, correction, opinion, or
important fact worth remembering?

If yes: call memory_save or memory_update. Check memory_search first to avoid
duplicating an existing item. Use scope=personal unless the user explicitly asked
to share with their team.

If nothing is worth saving, reply with "done" and stop.
```

### `cubeplex/middleware/reflection.py`

New file. `ReflectionMiddleware(Middleware)` implements `on_run_end`:

```python
async def on_run_end(self, ctx, *, signal=None) -> list[Message] | None:
    return [UserMessage(
        content=[TextContent(text=REFLECTION_PROMPT)],
        metadata={"is_reflection": True},
    )]
```

### `cubeplex/streams/run_manager.py`

Add `ReflectionMiddleware` as the last entry in the middleware stack (after
`TodoListMiddleware`), so the reflection turn has the full memory tool set and
authoring guidance already wired in.

## Event Visibility

The reflection turn's injected `UserMessage` and the agent's response flow through
the normal SSE channel. The `UserMessage` carries `metadata["is_reflection"] = True`.
Frontend can use this tag to render the reflection turn differently (fold, mute, etc.)
in a future iteration. First version: no special frontend handling.

## Out of Scope

- Cross-conversation memory search (separate feature, not yet designed)
- Hiding reflection events from the SSE stream (future frontend work)
- Skipping reflection on very short or errored runs (can add heuristics later)
