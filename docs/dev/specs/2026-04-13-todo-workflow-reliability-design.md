# Todo Workflow Reliability Design

## Overview

This design strengthens the existing session todo workflow so it behaves like a managed execution checklist instead of a best-effort planning hint. The primary goal is to reduce cases where the agent either fails to create a todo list for multi-step work or forgets to update the list as work progresses.

The design keeps the current middleware-centered architecture. We extend the existing `write_todos` tool contract, add runtime validation in `TodoListMiddleware`, and introduce lightweight session-state checks that detect stale todos before the agent can continue or finish a turn incorrectly.

For this document, "model invocation" means a single LLM response generation, while "agent loop iteration" means the broader cycle of model output, tool execution, tool results being incorporated into state, and control returning to the model or user. Staleness checks in this design are defined at the agent loop iteration boundary, not after each individual tool call.

## Goals

- Make todo state structurally valid, not just prompt-guided
- Detect when the agent should update todos but does not
- Prevent final summaries while todos are still stale or incomplete
- Preserve todo state across session resume
- Add focused tests for the failure modes we actually care about

## Non-Goals

- Replacing todos with a full task management system
- Adding task ownership, dependency graphs, or distributed scheduling
- Building a new frontend task UI as part of this change
- Enforcing semantic verification of every task from day one

---

## Problems to Solve

### 1. Todo usage is mostly prompt-driven

The current middleware exposes `write_todos` and injects a system prompt, but runtime enforcement is minimal. The only hard failure today is parallel `write_todos` calls in the same model invocation. This makes correctness depend too heavily on model compliance.

### 2. Todo state is under-specified

A todo item currently has only `content` and `status`. This is enough to store a checklist, but the current design does not clearly separate workflow state from display behavior. We need stricter execution-state rules without pushing cosmetic display concerns into the tool contract.

### 3. There is no stale-todo detection

Once a todo list exists, the agent can make substantial progress without touching it again. The runtime does not detect that a previously active plan is now stale.

### 4. The agent can close out a turn while todos are still incomplete

There is no final-turn guard that blocks a user-facing completion message when the todo list still has `pending` or `in_progress` work that should have been updated first.

### 5. Resume behavior is not explicitly defined

Long or interrupted sessions need a clear source of truth for restoring the most recent todo state.

---

## Design Summary

The design has four parts:

1. Strengthen the todo schema
2. Add middleware-level state validation
3. Add stale-todo and finalization guards
4. Restore the latest todo state on resume

These changes stay within the current architecture:

- `TodoListMiddleware` remains the integration point
- `write_todos` remains the state mutation API
- LangGraph state remains the in-session source of truth
- Transcript-based recovery restores state after interruption

---

## Data Model Changes

### Todo Item Schema

Keep the todo item shape compact:

```python
{
    "content": str,
    "status": "pending" | "in_progress" | "completed",
}
```

### Field Definitions

- `content`: Imperative task description, for example `Run unit tests`
- `status`: Current task status

### Display Text Derivation

If the UI or middleware needs progress-tense text for the current task, it should derive that from `content` and `status` outside the `write_todos` schema.

This keeps the tool contract focused on workflow state rather than presentation. It also avoids requiring the model to supply and maintain two semantically overlapping strings for every todo item.

---

## Validation Rules

Validation should happen at the middleware/tool boundary, not only in prompt text.

### List-Level Rules

For any submitted todo list:

- Empty lists are allowed only when there is no remaining work
- If any item is not `completed`, at least one item must be `in_progress`
- At most one item may be `in_progress`
- All items must have non-empty `content`

### Item-Level Rules

- `content` should be specific and action-oriented
- `completed` items must not be rewritten into a different task on later updates

### Behavioral Rules

- The first submitted todo list for a complex task must include an `in_progress` item
- If work remains after completing the current item, completing that item and starting the next must happen in the same `write_todos` update
- If all work is done, every item may be `completed`

The second rule is enforced by the same list-level invariant that unfinished work requires an active item. No separate diff-based validator is required for v1. If a submitted list marks the current item `completed` while other items remain unfinished, that same submitted list must also mark the next active item `in_progress`; otherwise the list is invalid because it leaves unfinished work with no active item.

### Failure Behavior

Invalid todo submissions should return a structured tool error message that explains exactly which invariant failed. The model should then correct the list on the next pass.

---

## Middleware Responsibilities

`TodoListMiddleware` should grow from a prompt injector into a workflow guard.

### Existing Responsibility

- Register `write_todos`
- Append todo instructions to the system prompt
- Reject parallel `write_todos` calls in a single model invocation

### New Responsibilities

- Validate todo schema and list invariants
- Detect stale active plans
- Detect invalid turn finalization while todos remain incomplete
- Emit precise corrective messages back to the model

This keeps the control loop local to middleware and avoids spreading todo policy across unrelated tools.

### Middleware Check Flow

The `after_model`-side workflow should evaluate checks in a deterministic order so one correction path is active at a time:

```text
1. Inspect latest AI action set and current todo state
2. Reject parallel write_todos calls in the same model invocation
3. Validate any submitted todo payload
4. If no todo was submitted, evaluate stale-todo guard at the iteration boundary
5. If the iteration is about to return a user-facing completion response, evaluate finalization guard
6. Return the first blocking corrective message, or allow progression if no guard fires
```

This ordering keeps malformed todo submissions, stale active plans, and invalid completion attempts from competing with each other in one pass.

Retry accounting is independent per guard type. A stale-todo correction failure increments only the stale-todo retry counter; a finalization-guard correction failure increments only the finalization retry counter.

---

## Stale-Todo Detection

### Definition

A todo list is stale when:

- The session already has unfinished todos
- The agent has progressed the task in a meaningful way
- The current agent loop iteration finished without synchronizing the todo list

### Signals for Meaningful Progress

The first version should use simple, robust signals evaluated at the end of an agent loop iteration:

- The iteration included one or more tool calls
- The todo list contains unfinished work
- No `write_todos` call occurred during that iteration

This is intentionally coarse. The goal is to catch obvious misses without needing fragile semantic classification, while avoiding false positives during normal multi-tool execution inside a single active task.

### Middleware Response

When a stale-todo condition is detected, the middleware should inject a blocking tool-style error or system reminder that says, in effect:

- You made progress on an active plan
- The todo list was not updated
- Update the todo list before continuing

This check should run only after the tool phase for that iteration has completed and control is about to advance. It should not fire after each individual tool call. In middleware terms, the implementation should hook into the boundary where the latest AI action set and all resulting tool messages are available for inspection together.

---

## Finalization Guard

### Problem

The easiest place to miss a todo update is right before the final response. The agent completes work, writes a summary, and exits without synchronizing the checklist.

### Guard Rule

If the current agent loop iteration appears to be concluding work while the session todo state still contains unfinished items, the middleware should block that conclusion and instruct the model to update the todo list first.

### First-Version Heuristic

Use a simple guard:

- There are unfinished todos in state
- The latest AI message contains no tool calls at all
- The iteration is about to return a pure-text assistant response to the user

If the runtime exposes a reliable graph-level completion signal such as an `END` transition, that signal should be preferred over text-pattern heuristics. Pure-text completion is only the fallback heuristic when that stronger signal is not available.

This is enough to stop the most obvious false-complete cases.

### Expected Result

Instead of sending an incomplete final answer to the user, the model is forced into one more correction pass:

1. Update the todo list
2. Mark the correct item `completed` or keep it `in_progress`
3. Only then proceed to the final summary

### Failure Escalation Policy

Correction loops must not continue indefinitely.

If the same guard condition blocks progress repeatedly, the runtime should escalate after a bounded number of retries.

Recommended first-version policy:

- Track consecutive correction failures in middleware state, keyed by guard type
- Allow up to 2 automatic self-correction retries for the same guard
- On the 3rd failure, stop re-prompting the model and surface a user-visible failure explaining that todo synchronization could not be repaired automatically

The escalation response should:

- preserve the current todo state
- include the last guard error
- avoid claiming task completion
- make it clear that the run ended in a workflow-control failure rather than a successful finish

---

## Completion and Verification Nudges

This design does not make verification a hard requirement for every task, but it should add a lightweight structural nudge when the checklist closes out suspiciously.

### Nudge Condition

When all todos are marked `completed` and the list had 3 or more items, the tool result should append a reminder to verify outcomes before finalizing.

This is a purely structural rule. It does not inspect todo semantics, domain vocabulary, or item ordering.

### Why This Stays Lightweight

The first goal is todo reliability, not full execution proof. A nudge is enough to improve behavior without introducing a larger verification framework into this change.

The design intentionally accepts a higher false-positive rate here because the cost of the nudge is low. For a general-purpose agent, a generic verification reminder on a completed 3+ item list is safer than trying to infer domain-specific verification intent from task wording.

---

## Resume Strategy

### Source of Truth

The latest successful `write_todos` tool call in the transcript should be treated as the resumable representation of the todo list.

### Resume Flow

On resume:

1. Scan the conversation transcript from newest to oldest
2. Find the most recent `write_todos` tool use or tool result
3. Parse the todo payload
4. Rehydrate session state with that list

### Why Transcript Recovery

This avoids introducing new persistence infrastructure for the first version and keeps recovery aligned with the rest of the conversation state.

### Known Limitation

Transcript scanning is acceptable for the first version, but it is not the ideal long-term source of truth for very long or compacted sessions.

Two failure modes are expected over time:

- resume cost grows with transcript size
- transcript compaction or truncation can make the latest todo state harder to recover reliably

### Future Improvement

A later version should persist the latest todo state directly into LangGraph checkpointer-backed state. That would make resume both faster and more reliable than transcript scanning while keeping the middleware-centered design intact.

---

## Implementation Plan by Phase

### Phase 1: Schema and Validation

Files in scope:

- `cubeplex/middleware/todo.py`
- `tests/unit/test_middleware_todo.py`

Changes:

- Add helper validation for todo list invariants
- Return clear tool errors for invalid lists
- Keep display-oriented active-task wording out of the schema

### Phase 2: Stale-Todo and Finalization Guards

Files in scope:

- `cubeplex/middleware/todo.py`
- `tests/unit/test_middleware_todo.py`

Changes:

- Add stale-todo detection in `after_model`
- Add incomplete-finalization guard in `after_model`
- Add bounded retry and escalation tracking for repeated correction failures
- Ensure these checks compose cleanly with the existing parallel-call check

### Phase 3: Resume and Completion Nudge

Files in scope:

- Resume/state restoration module for agent sessions
- `cubeplex/middleware/todo.py`
- Related tests

Changes:

- Restore latest todo state from transcript
- Add checklist-closeout verification nudge for any completed 3+ item list
- Cover resumed-session behavior in tests

---

## Error Handling

Error messaging should be optimized for correction, not for user display.

Examples:

- `Error: todo list must include exactly one in_progress item while work remains.`
- `Error: work progressed on an active plan but the todo list was not updated. Call write_todos before continuing.`
- `Error: cannot finalize response while todo list still contains unfinished items. Update the list first.`

These messages should be attached as structured tool/system feedback so the model can self-correct in the next turn.

---

## Testing Strategy

Add tests for the behavior the middleware is responsible for, not just the happy path.

### Validation Tests

- accepts a valid todo list with one `in_progress` item
- rejects a list with no `in_progress` item while unfinished work remains
- rejects a list with multiple `in_progress` items
- rejects empty `content`

### Middleware Guard Tests

- still rejects parallel `write_todos` calls
- emits stale-todo error when tools were used but todos were not updated
- emits finalization guard when unfinished todos remain and the turn tries to conclude
- does not emit stale-todo error when the turn correctly updates todos
- does not emit stale-todo error for intermediate multi-tool progress inside one active task
- accepts an atomic transition where item A becomes `completed` and item B becomes `in_progress` in one `write_todos` update
- escalates to a user-visible workflow failure after repeated invalid self-correction attempts

### Resume Tests

- restores the latest todo list from transcript
- ignores malformed historical todo payloads safely

### Nudge Tests

- emits a verification reminder when any 3+ item list is fully completed
- does not emit the reminder for lists with fewer than 3 items

---

## Risks and Mitigations

### Risk: Over-eager stale detection

If the stale-todo heuristic is too broad, the model may be forced into unnecessary update loops, especially during normal multi-tool work within a single task.

Mitigation:

- Evaluate at the agent loop iteration boundary, not per tool call
- Start with the narrow tool-call-based heuristic
- Keep error messages corrective and specific
- Add tests for benign turns that should not trigger the guard

### Risk: Todo rules are too rigid for valid parallel work

A strict single-`in_progress` rule may block future workflows that intentionally run tasks in parallel.

Mitigation:

- Optimize for the common single-threaded case now
- If genuine parallel execution becomes common, relax this later behind an explicit rule change rather than starting loose

### Risk: Resume recovery reads stale history

Transcript recovery could restore an older list if message parsing is too permissive.

Mitigation:

- Scan from newest to oldest
- Parse only the canonical todo payload format
- Ignore invalid payloads rather than partially accepting them
- Plan a future migration to checkpointer-backed todo persistence

### Risk: Correction guards can dead-loop

If the model repeatedly fails to repair the same workflow violation, the guard itself can become the failure mode.

Mitigation:

- Track retries per guard type
- Cap automatic retries
- Escalate to a user-visible workflow failure instead of looping indefinitely

---

## Open Decisions

These should be resolved before implementation begins:

- Should invalid todo submissions fail at pydantic schema validation, middleware validation, or both?
- Should the correction retry counter live only in in-memory state for the current run, or also persist through resume?

My recommendation:

- Use schema validation for shape, middleware validation for workflow invariants
- Make finalization guard blocking in the first version
- Trigger the verification nudge structurally for any completed 3+ item list
- Keep retry counters scoped to the current run in v1; do not persist them across resume
- Keep retry counters keyed independently by guard type rather than sharing one global correction counter

---

## Recommended Direction

Implement Phase 1 and Phase 2 first. Those two phases directly address the main failure mode: the agent forgetting to keep an active checklist synchronized with the work it is doing.

Phase 3 should follow immediately after if session resume is already part of normal usage. If resume support is not yet heavily used, the completion nudge can still ship early while transcript restoration lands in a second pass.
