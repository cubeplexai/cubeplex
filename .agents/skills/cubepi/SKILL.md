---
name: cubepi
description: Use when building, extending, or debugging agents with the CubePi framework. Covers the Agent API, providers, tools, middleware, checkpointing, MCP integration, and HITL. References the cubepi-trace skill for run debugging.
---

# Building agents with CubePi

CubePi is a Pythonic, async-native agent framework. The agent loop is a plain
`while` loop — no graph nodes, no state machines. You can read the full runtime
in a few minutes.

**Docs:** https://cubepi.ai/docs  
**Source:** https://github.com/cubeplexai/cubepi  
**Issues / PRs:** https://github.com/cubeplexai/cubepi/issues

If anything in the docs is unclear or missing, read the source directly — the
code is the authoritative reference. If you hit a bug or find a gap, open an
issue or PR on the repo.

## Install

```bash
pip install cubepi                              # core
pip install cubepi[mcp]                        # MCP tool loaders
pip install cubepi[tracing,trace-cli]          # OpenTelemetry + cubepi trace CLI
pip install cubepi[sqlite]                     # SQLite checkpointer
pip install cubepi[postgres]                   # Postgres checkpointer
```

## Core concepts

### Agent + Provider + Model

```python
from cubepi import Agent, AgentTool, Model
from cubepi.providers.anthropic import AnthropicProvider
from cubepi.providers.openai import OpenAIProvider

provider = AnthropicProvider(provider_id="anthropic")              # or OpenAIProvider(provider_id="openai")
model = Model(id="claude-opus-4-5-20251001")
agent = Agent(model=model, system_prompt="You are helpful.")

await agent.prompt("Hello")
await agent.wait_for_idle()
```

### Tools

```python
from pydantic import BaseModel
from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent

class SearchInput(BaseModel):
    query: str

async def search(tool_call_id: str, params: SearchInput, *, signal=None, on_update=None):
    result = await do_search(params.query)
    return AgentToolResult(content=[TextContent(text=result)])

tool = AgentTool(name="search", description="Search the web", parameters=SearchInput, execute=search)
agent = Agent(..., tools=[tool])
```

Full tool docs: https://cubepi.ai/docs/guides/tools

### Streaming responses

```python
async for event in agent.stream("What is 2+2?"):
    if event.type == "text_delta":
        print(event.delta, end="", flush=True)
```

Event types: `text_delta`, `thinking_delta`, `toolcall_start`, `toolcall_delta`,
`toolcall_end`, `message_start`, `message_end`. Full reference:
https://cubepi.ai/docs/guides/streaming

### Middleware

Eight typed hooks. Subclass `Middleware`, override the hooks you need, pass instances
to `Agent(middleware=[...])`:

```python
from cubepi import Middleware

class MyMiddleware(Middleware):
    async def transform_context(self, messages, *, ctx, signal=None):
        return messages          # filter / inject messages before each LLM call

    async def transform_system_prompt(self, sp, *, ctx, signal=None):
        return sp + "\nExtra."  # append to system prompt

    async def before_tool_call(self, ctx, *, signal=None):
        return None              # BeforeToolCallResult(block=True) to deny

    async def after_tool_call(self, ctx, *, signal=None):
        return None              # AfterToolCallResult(...) to override result

    async def after_model_response(self, response, ctx, *, signal=None):
        return None              # TurnAction(decision="stop"|"loop_to_model")

    async def should_stop_after_turn(self, ctx) -> bool:
        return False             # True to end run after this turn

    async def on_run_end(self, ctx, *, signal=None):
        return None              # list[Message] to inject + run one extra turn

agent = Agent(..., middleware=[MyMiddleware()])
```

| Hook | Fires | Composition |
|---|---|---|
| `transform_context` | Before each model call | Chained |
| `convert_to_llm` | Right before serialisation | Last wins |
| `transform_system_prompt` | Before each model call | Chained |
| `before_tool_call` | Per tool call | First block stops |
| `after_tool_call` | Per tool call | Later overrides earlier |
| `after_model_response` | After assistant message | Chain; last decision wins |
| `should_stop_after_turn` | Each turn boundary | Any True stops |
| `on_run_end` | Once after all turns, before agent_end | Messages concatenate |

Full docs: https://cubepi.ai/docs/guides/middleware

### Checkpointing

```python
from cubepi.checkpointer.sqlite import SqliteCheckpointer

checkpointer = await SqliteCheckpointer.create("./state.db")
agent = Agent(..., checkpointer=checkpointer)

# Resume a conversation
await agent.resume(conversation_id="conv_123")
await agent.prompt("Continue from where we left off")
```

Full docs: https://cubepi.ai/docs/guides/checkpointing

### MCP tools

```python
from cubepi.mcp import MCPToolLoader

loader = MCPToolLoader(server_url="http://localhost:8080")
tools = await loader.load()
agent = Agent(..., tools=tools)
```

Full docs: https://cubepi.ai/docs/guides/mcp

### Human-in-the-loop (HITL)

Use `before_tool_call` middleware to intercept and approve tool calls, or use
the built-in `ApprovalPolicyMiddleware` for policy-driven approval:

Full docs: https://cubepi.ai/docs/guides/hitl

## Debugging runs

Install the `cubepi-trace` skill — it gives you the full protocol for
inspecting recorded OTel spans without re-running the agent:

```bash
npx skills add cubeplexai/cubepi@cubepi-trace -a claude-code
```

The skill covers `cubepi trace ls / view / follow / stats / convert`, token
and cache-rate conventions, and streaming failure debugging.

## Getting help

- **Docs:** https://cubepi.ai/docs  
- **Source:** https://github.com/cubeplexai/cubepi (read it — it's short)  
- **Issues:** https://github.com/cubeplexai/cubepi/issues  
- **PRs welcome** for bugs, missing docs, or new provider support
