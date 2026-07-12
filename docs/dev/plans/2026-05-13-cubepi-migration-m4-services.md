# cubepi Migration M4 — Services & Ancillary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Round out the cubepi runtime path's non-agent surfaces: dispatch the **history read API** based on `config.agents.runtime` (closing Codex review #2), and port `services/conversation_title.py` to use cubepi.Provider directly instead of LangChain BaseChatModel. After M4, the cubepi path is end-to-end consistent for read AND write, and auto-title generation runs through cubepi too.

**Spec:** `docs/superpowers/specs/2026-05-13-cubepi-main-agent-migration-design.md` § M4.

**Baseline:** M3 done; 738 unit tests pass; cubepi runtime with full 11-middleware stack working through M1.6/M2.6 E2Es.

---

## Tasks

### M4.1: history API dispatch (Codex review #2)

When `config.agents.runtime == "cubepi"`, `_get_history_messages` reads via cubepi checkpointer instead of langgraph.

**File:** `backend/cubeplex/api/routes/v1/conversations.py`

**Approach:**
```python
async def _get_history_messages(raw_request: Request, conversation_id: str) -> dict[str, object]:
    runtime = getattr(raw_request.app.state, "agents_runtime", None) or _config.agents.runtime
    if runtime == "cubepi":
        return await _get_history_messages_cubepi(conversation_id)
    return await _get_history_messages_langgraph(raw_request, conversation_id)


async def _get_history_messages_cubepi(conversation_id: str) -> dict[str, object]:
    from cubeplex.agents.checkpointer_pi import init_cubepi_checkpointer
    from cubeplex.agents.convert_pi import cubepi_message_to_wire

    async with init_cubepi_checkpointer() as cp:
        data = await cp.load(conversation_id)
    if data is None:
        return {"messages": [], "total": 0}
    messages = [cubepi_message_to_wire(m) for m in data.messages]
    return {"messages": messages, "total": len(messages)}
```

Move existing body of `_get_history_messages` into `_get_history_messages_langgraph` (or rename the current function).

**Test:** add a new E2E that:
- Sends a message via POST /messages (cubepi path writes)
- Calls GET /messages (cubepi path reads)
- Asserts the user message + assistant message both come back

`backend/tests/e2e/test_cubepi_history_round_trip.py`.

### M4.2: conversation_title.py port to cubepi.Provider direct

`services/conversation_title.py` currently does a one-shot LLM call via `LLMFactory.create_default()` (LangChain). Port to use `LLMFactory.build_cubepi_provider()` + direct provider stream when `config.agents.runtime == "cubepi"`.

**File:** `backend/cubeplex/services/conversation_title.py`

**Approach**: dispatch at the LLM-call site. Read existing flow; identify the place that does `llm.ainvoke(messages)` or similar. Branch:

```python
if config.agents.runtime == "cubepi":
    provider = factory.build_cubepi_provider(provider_config, cache_policy=None)
    # Direct provider.stream call OR build a minimal cubepi.Agent
    # For one-shot title generation, direct provider call is enough — no agent loop needed
    stream = await provider.stream(
        model=cubepi.Model(id=model_id, provider=provider_config.name),
        messages=[cubepi.UserMessage(content=[cubepi.TextContent(text=full_prompt)])],
        system_prompt="",
    )
    # Collect text from stream events until done
    title_text = ""
    async for evt in stream:
        if evt.type == "text_delta":
            title_text += evt.delta
        elif evt.type == "done":
            break
        elif evt.type == "error":
            raise RuntimeError(evt.error_message)
else:
    # existing LangChain path
    ...
```

NOTE: cubepi `MessageStream` (returned by Provider.stream) is iterated via `async for evt in stream`. Confirm exact API by reading `cubepi/providers/base.py:MessageStream`.

**Tests**: add unit tests with FauxProvider for the cubepi branch.

### M4.3: validation + push

- Full unit suite green
- M1.6/M2.6 E2Es still pass
- New M4.1 history E2E passes
- `make check` clean
- push

---

## Out of scope for M4

- `services/provider_service.py` (per spec: "Mostly metadata routing; likely no change") — verify nothing actually needs changing; if a small adjustment is needed, do it as a one-line fix
- `streams/run_manager.py` event queue protocol — already adapted in M1.5 and M3.f; nothing extra
