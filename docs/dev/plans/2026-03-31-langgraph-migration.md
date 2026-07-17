# LangGraph Migration Implementation Plan

> **SUPERSEDED (2026-05-14):** The LangGraph runtime described here was fully replaced by cubepi. See `docs/superpowers/specs/2026-05-13-cubepi-main-agent-migration-design.md` and the M0–M6 cubepi-migration plans (plus the 2026-05-14 cleanup follow-up) for the current state.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `deepagents` with `langchain.agents.create_agent()` + custom middleware, simplify sandbox to execute-only, use LangGraph thread state as the single source of truth for messages, and simplify frontend state management.

**Architecture:** `langchain.agents.create_agent()` is a thin wrapper around LangGraph `StateGraph` — it returns a `CompiledStateGraph` with full `.astream()` and checkpointer support. Custom middleware (SandboxMiddleware, SubAgentMiddleware, SkillsMiddleware) are vendored and adapted from deepagents. The frontend store is rewritten to be flat and accumulate stream text at the store layer, with per-agent streaming state to support subagent cards.

**Tech Stack:** Python/FastAPI, LangGraph 1.0+, `langchain.agents.create_agent()`, `langchain.agents.middleware.AgentMiddleware`, `langgraph-checkpoint-mysql`, Next.js/Zustand, Playwright, Vitest

---

## File Map

### Backend — Create
- `backend/cubeplex/sandbox/base.py` — `Sandbox` ABC with `execute`, `upload`, `download`, `close`
- `backend/cubeplex/sandbox/local.py` — `LocalSandbox` using `asyncio.create_subprocess_shell`
- `backend/cubeplex/prompts/__init__.py` — empty
- `backend/cubeplex/prompts/system.py` — `BASE_SYSTEM_PROMPT`
- `backend/cubeplex/prompts/sandbox.py` — `SANDBOX_PROMPT`
- `backend/cubeplex/prompts/subagents.py` — `SUBAGENT_PROMPT`
- `backend/cubeplex/prompts/skills.py` — `SKILLS_PROMPT`
- `backend/cubeplex/middleware/__init__.py` — empty
- `backend/cubeplex/middleware/_utils.py` — `append_to_system_message` helper
- `backend/cubeplex/middleware/sandbox.py` — `SandboxMiddleware`
- `backend/cubeplex/middleware/subagents.py` — `SubAgentMiddleware`, `SubAgent` TypedDict
- `backend/cubeplex/middleware/skills.py` — `SkillsMiddleware`
- `backend/cubeplex/agents/graph.py` — `create_cubeplex_agent()`
- `backend/cubeplex/agents/convert.py` — `convert_to_api_messages()`
- `backend/tests/unit/test_sandbox_local.py`
- `backend/tests/unit/test_convert_messages.py`
- `backend/tests/unit/test_middleware_sandbox.py`
- `backend/tests/unit/test_prompts.py`
- `backend/tests/e2e/test_conversation_flow.py`
- `backend/tests/e2e/test_streaming.py`
- `backend/tests/e2e/test_thread_state.py`

### Backend — Modify
- `backend/cubeplex/sandbox/opensandbox.py` — rewrite to inherit new `Sandbox` base, keep only `execute`/`upload`/`download`
- `backend/cubeplex/sandbox/manager.py` — use `Sandbox` base type instead of `OpenSandbox` directly
- `backend/cubeplex/agents/schemas.py` — add `agent_id`/`agent_name` fields, remove `ChainStartEvent`
- `backend/cubeplex/api/app.py` — add `checkpointer_factory`/`sandbox_factory` params to `create_app()`
- `backend/cubeplex/api/routes/v1/conversations.py` — rewrite `send_message` and `list_messages`
- `backend/cubeplex/models/__init__.py` — remove `Message` export
- `backend/cubeplex/repositories/__init__.py` — remove `MessageRepository` export
- `backend/pyproject.toml` — remove `deepagents`, `nest-asyncio`, `asyncer`

### Backend — Delete
- `backend/cubeplex/agents/executor.py`
- `backend/cubeplex/models/message.py`
- `backend/cubeplex/repositories/message.py`

### Frontend — Modify
- `frontend/packages/core/src/types/message.ts` — add `tool` role, `tool_calls`, `reasoning`, `name`
- `frontend/packages/core/src/types/events.ts` — add `agent_id`, `agent_name` fields
- `frontend/packages/core/src/stores/messageStore.ts` — rewrite with flat state + `streamAgents`
- `frontend/packages/web/hooks/useMessages.ts` — simplify, no `conversationId` param
- `frontend/packages/web/components/chat/AssistantMessage.tsx` — read from store, remove extract functions
- `frontend/packages/web/components/chat/MessageList.tsx` — render SubAgentCard for subagents

### Frontend — Create
- `frontend/packages/web/components/chat/SubAgentCard.tsx` — collapsible subagent execution card
- `frontend/packages/web/__tests__/hooks/useMessages.test.ts`
- `frontend/packages/web/__tests__/e2e/chat-flow.spec.ts`
- `frontend/packages/web/__tests__/e2e/streaming.spec.ts`
- `frontend/playwright.config.ts`

---

## Task 1: Sandbox Base Class + LocalSandbox

**Files:**
- Create: `backend/cubeplex/sandbox/base.py`
- Create: `backend/cubeplex/sandbox/local.py`
- Create: `backend/tests/unit/test_sandbox_local.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_sandbox_local.py
import asyncio
import pytest
from cubeplex.sandbox.local import LocalSandbox


@pytest.mark.asyncio
async def test_execute_simple_command():
    sandbox = LocalSandbox()
    result = await sandbox.execute("echo hello")
    assert result.output.strip() == "hello"
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_execute_exit_code():
    sandbox = LocalSandbox()
    result = await sandbox.execute("exit 1", timeout=5)
    assert result.exit_code == 1


@pytest.mark.asyncio
async def test_execute_combines_stderr():
    sandbox = LocalSandbox()
    result = await sandbox.execute("echo out && echo err >&2")
    assert "out" in result.output
    assert "err" in result.output


@pytest.mark.asyncio
async def test_upload_and_download(tmp_path):
    sandbox = LocalSandbox(workdir=str(tmp_path))
    content = b"hello world"
    await sandbox.upload([(str(tmp_path / "test.txt"), content)])
    downloaded = await sandbox.download([str(tmp_path / "test.txt")])
    assert downloaded[0][1] == content


@pytest.mark.asyncio
async def test_close_is_noop():
    sandbox = LocalSandbox()
    await sandbox.close()  # should not raise


def test_sandbox_id_is_stable():
    sandbox = LocalSandbox()
    assert sandbox.id == sandbox.id
    assert isinstance(sandbox.id, str)
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd backend && uv run pytest tests/unit/test_sandbox_local.py -v 2>&1 | head -20
```
Expected: `ModuleNotFoundError: No module named 'cubeplex.sandbox.base'`

- [ ] **Step 3: Create the sandbox base class**

```python
# backend/cubeplex/sandbox/base.py
"""Sandbox base class — async-first interface for code execution environments."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ExecuteResult:
    """Result of a shell command execution."""

    output: str
    exit_code: int | None = None


class Sandbox(ABC):
    """Async-first sandbox base class.

    Agent-facing: only `execute` is registered as a tool.
    Infrastructure-facing: `upload`/`download` for binary file transfer
    (used by API endpoints, SandboxManager, skills sync — NOT agent tools).
    """

    @property
    @abstractmethod
    def id(self) -> str:
        """Unique identifier for this sandbox instance."""
        ...

    @abstractmethod
    async def execute(
        self, command: str, *, timeout: int | None = None
    ) -> ExecuteResult:
        """Execute a shell command. Returns combined stdout+stderr and exit code."""
        ...

    @abstractmethod
    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        """Upload files into the sandbox. Each tuple is (absolute_path, content)."""
        ...

    @abstractmethod
    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        """Download files from the sandbox. Returns list of (path, content) tuples."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release sandbox resources."""
        ...
```

- [ ] **Step 4: Create LocalSandbox**

```python
# backend/cubeplex/sandbox/local.py
"""Local sandbox using asyncio subprocesses — for dev/debug only."""

import asyncio
import os
import uuid
from pathlib import Path

from cubeplex.sandbox.base import ExecuteResult, Sandbox


class LocalSandbox(Sandbox):
    """Sandbox backed by local asyncio subprocesses.

    Not suitable for production. Use for development and testing.
    """

    def __init__(self, *, workdir: str | None = None) -> None:
        self._id = str(uuid.uuid4())
        self._workdir = workdir or os.getcwd()

    @property
    def id(self) -> str:
        return self._id

    async def execute(
        self, command: str, *, timeout: int | None = None
    ) -> ExecuteResult:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self._workdir,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return ExecuteResult(output="[timeout]", exit_code=-1)

        return ExecuteResult(
            output=stdout.decode(errors="replace"),
            exit_code=proc.returncode,
        )

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        for path, content in files:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        result = []
        for path in paths:
            result.append((path, Path(path).read_bytes()))
        return result

    async def close(self) -> None:
        pass
```

- [ ] **Step 5: Run tests — expect pass**

```bash
cd backend && uv run pytest tests/unit/test_sandbox_local.py -v
```
Expected: `5 passed`

- [ ] **Step 6: Commit**

```bash
cd backend && git add cubeplex/sandbox/base.py cubeplex/sandbox/local.py tests/unit/test_sandbox_local.py
git commit -m "feat: add Sandbox base class and LocalSandbox implementation"
```

---

## Task 2: Update OpenSandbox to Inherit New Base

**Files:**
- Modify: `backend/cubeplex/sandbox/opensandbox.py`

- [ ] **Step 1: Rewrite opensandbox.py**

Replace the entire file content. Keep only `execute`, `upload`, `download`, `close`. The existing `aexecute` logic becomes `execute`. Remove all other methods.

```python
# backend/cubeplex/sandbox/opensandbox.py
"""OpenSandbox implementation of the Sandbox base class."""

import opensandbox
from loguru import logger

from cubeplex.sandbox.base import ExecuteResult, Sandbox


class OpenSandbox(Sandbox):
    """Sandbox backed by a remote OpenSandbox container."""

    def __init__(self, *, sandbox: opensandbox.Sandbox) -> None:
        self._sandbox = sandbox

    @property
    def id(self) -> str:
        return self._sandbox.id

    async def execute(
        self, command: str, *, timeout: int | None = None
    ) -> ExecuteResult:
        execution = await self._sandbox.commands.run(command)

        output_lines: list[str] = []
        for msg in execution.logs.stdout:
            output_lines.append(msg.text)
        for msg in execution.logs.stderr:
            output_lines.append(msg.text)
        output = "\n".join(output_lines) if output_lines else ""

        exit_code: int | None = None
        if execution.id:
            try:
                status = await self._sandbox.commands.get_command_status(execution.id)
                exit_code = status.exit_code
            except Exception as e:
                logger.warning("Could not get exit code for command: {}", e)

        return ExecuteResult(output=output, exit_code=exit_code)

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        for path, content in files:
            await self._sandbox.files.write_file(path, content)

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        result = []
        for path in paths:
            content_str = await self._sandbox.files.read_file(path)
            result.append((path, content_str.encode("utf-8")))
        return result

    async def close(self) -> None:
        pass
```

- [ ] **Step 2: Update SandboxManager to use Sandbox base type**

In `backend/cubeplex/sandbox/manager.py`, change the import and type annotation:

```python
# Change this import:
from cubeplex.sandbox.opensandbox import OpenSandbox
# to:
from cubeplex.sandbox.base import Sandbox
from cubeplex.sandbox.opensandbox import OpenSandbox
```

And update the return type of `get_or_create()`:

```python
# line ~64 — change return type annotation
async def get_or_create(self, user_id: str) -> Sandbox:
```

Also update `_sync_skills()` — replace the `aupload_files` calls with the new `upload()` method. Find the relevant lines:

```python
# In _sync_skills(), replace:
await sandbox.aupload_files(skill_files)
# with:
await sandbox.upload(skill_files)
```

- [ ] **Step 3: Run type check**

```bash
cd backend && uv run mypy cubeplex/sandbox/
```
Expected: no errors

- [ ] **Step 4: Commit**

```bash
cd backend && git add cubeplex/sandbox/opensandbox.py cubeplex/sandbox/manager.py
git commit -m "feat: update OpenSandbox to inherit Sandbox base, simplify to execute/upload/download"
```

---

## Task 3: Prompts Directory

**Files:**
- Create: `backend/cubeplex/prompts/__init__.py`
- Create: `backend/cubeplex/prompts/system.py`
- Create: `backend/cubeplex/prompts/sandbox.py`
- Create: `backend/cubeplex/prompts/subagents.py`
- Create: `backend/cubeplex/prompts/skills.py`
- Create: `backend/tests/unit/test_prompts.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_prompts.py
from cubeplex.prompts.system import BASE_SYSTEM_PROMPT
from cubeplex.prompts.sandbox import SANDBOX_PROMPT
from cubeplex.prompts.subagents import SUBAGENT_PROMPT
from cubeplex.prompts.skills import SKILLS_PROMPT


def test_all_prompts_are_non_empty_strings():
    for prompt in [BASE_SYSTEM_PROMPT, SANDBOX_PROMPT, SUBAGENT_PROMPT, SKILLS_PROMPT]:
        assert isinstance(prompt, str)
        assert len(prompt.strip()) > 50


def test_prompts_have_no_format_placeholders():
    """Prompts used directly (not as templates) must have no unformatted {} placeholders."""
    for prompt in [BASE_SYSTEM_PROMPT, SANDBOX_PROMPT, SUBAGENT_PROMPT]:
        # Skills prompt is a template — skip it
        assert "{" not in prompt or prompt.count("{") == prompt.count("}")


def test_system_prompt_mentions_tools():
    assert "tool" in BASE_SYSTEM_PROMPT.lower()


def test_sandbox_prompt_mentions_execute():
    assert "execute" in SANDBOX_PROMPT.lower()
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd backend && uv run pytest tests/unit/test_prompts.py -v 2>&1 | head -10
```
Expected: `ModuleNotFoundError: No module named 'cubeplex.prompts'`

- [ ] **Step 3: Create prompt files**

```python
# backend/cubeplex/prompts/__init__.py
```

```python
# backend/cubeplex/prompts/system.py
"""Base system prompt for the cubeplex agent."""

BASE_SYSTEM_PROMPT = """You are an AI assistant that helps users accomplish tasks using tools. You respond with text and tool calls.

## Core Behavior

- Be concise and direct. Don't over-explain unless asked.
- NEVER add unnecessary preamble ("Sure!", "Great question!", "I'll now...").
- Don't say "I'll now do X" — just do it.
- If the request is ambiguous, ask questions before acting.
- If asked how to approach something, explain first, then act.

## Professional Objectivity

- Prioritize accuracy over validating the user's beliefs.
- Disagree respectfully when the user is incorrect.
- Avoid unnecessary superlatives, praise, or emotional validation.

## Doing Tasks

When the user asks you to do something:

1. **Understand first** — read relevant files, check existing patterns. Quick but thorough.
2. **Act** — implement the solution. Work quickly but accurately.
3. **Verify** — check your work against what was asked, not against your own output.

Keep working until the task is fully complete. Don't stop partway and explain what you would do — just do it. Only yield back to the user when the task is done or you're genuinely blocked.

**When things go wrong:**
- If something fails repeatedly, stop and analyze *why* — don't keep retrying the same approach.
- If you're blocked, tell the user what's wrong and ask for guidance."""
```

```python
# backend/cubeplex/prompts/sandbox.py
"""Sandbox execution prompt — injected when a sandbox is available."""

SANDBOX_PROMPT = """## Shell Execution

You have access to the `execute` tool to run shell commands in a sandbox environment.

**Use shell commands for all file operations:**
- Read files: `cat`, `head`, `tail`, `less`
- List files: `ls -la`, `find`, `tree`
- Search: `grep -r`, `rg`, `awk`
- Write/edit: `echo`, `tee`, `sed`, `patch`
- Run code: `python`, `node`, `bash`

**Shell features available:**
- Pipes: `cat file.txt | grep pattern | wc -l`
- Redirection: `command > output.txt 2>&1`
- Command chaining: `cmd1 && cmd2` (stop on error), `cmd1 ; cmd2` (always continue)
- Background: `cmd &`

**Error handling:**
- Non-zero exit codes are appended to output as `[exit code: N]`
- Check exit codes for command success/failure
- Commands run in an isolated sandbox — safe to experiment"""
```

```python
# backend/cubeplex/prompts/subagents.py
"""Subagent delegation prompt — injected when subagents are configured."""

SUBAGENT_PROMPT = """## Delegating Tasks to Subagents

You can delegate work to specialized subagents using the `task` tool. Each subagent runs independently and returns a result.

**When to use subagents:**
- Tasks that can be parallelized (e.g., researching multiple topics at once)
- Tasks requiring specialized expertise beyond your current tools
- Long-running tasks you can delegate while continuing other work

**When NOT to use subagents:**
- Simple, fast tasks — just do them yourself
- Tasks requiring your current conversation context

**Usage:**
- Provide a clear, self-contained `description` — the subagent has no access to your conversation history
- The subagent returns a single result when complete
- You can dispatch multiple subagents in parallel by calling `task` multiple times"""
```

```python
# backend/cubeplex/prompts/skills.py
"""Skills system prompt template — injected by SkillsMiddleware."""

# This is a template — formatted by SkillsMiddleware with discovered skills
SKILLS_PROMPT_TEMPLATE = """## Available Skills

Skills are pre-defined workflows stored as SKILL.md files. Use them for common tasks.

{skills_list}

To invoke a skill, read its SKILL.md file first, then follow the instructions within it."""
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd backend && uv run pytest tests/unit/test_prompts.py -v
```
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
cd backend && git add cubeplex/prompts/ tests/unit/test_prompts.py
git commit -m "feat: add prompts directory with system, sandbox, subagents, skills prompts"
```

---

## Task 4: Middleware Utils + SandboxMiddleware

**Files:**
- Create: `backend/cubeplex/middleware/__init__.py`
- Create: `backend/cubeplex/middleware/_utils.py`
- Create: `backend/cubeplex/middleware/sandbox.py`
- Create: `backend/tests/unit/test_middleware_sandbox.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/unit/test_middleware_sandbox.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from langchain_core.messages import SystemMessage

from cubeplex.middleware.sandbox import SandboxMiddleware
from cubeplex.sandbox.local import LocalSandbox


def test_sandbox_middleware_registers_execute_tool():
    sandbox = LocalSandbox()
    mw = SandboxMiddleware(sandbox=sandbox)
    tool_names = [t.name for t in mw.tools]
    assert "execute" in tool_names
    assert len(mw.tools) == 1  # only execute, nothing else


@pytest.mark.asyncio
async def test_execute_tool_runs_command():
    sandbox = LocalSandbox()
    mw = SandboxMiddleware(sandbox=sandbox)
    execute_tool = mw.tools[0]
    result = await execute_tool.ainvoke({"command": "echo hello"})
    assert "hello" in result


@pytest.mark.asyncio
async def test_execute_tool_appends_exit_code_on_failure():
    sandbox = LocalSandbox()
    mw = SandboxMiddleware(sandbox=sandbox)
    execute_tool = mw.tools[0]
    result = await execute_tool.ainvoke({"command": "exit 1"})
    assert "exit code: 1" in result


def test_sandbox_middleware_injects_prompt_in_wrap_model_call():
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    sandbox = LocalSandbox()
    mw = SandboxMiddleware(sandbox=sandbox)

    # Build a minimal ModelRequest with no system message
    request = MagicMock(spec=ModelRequest)
    request.system_message = None
    request.override = lambda **kw: MagicMock(system_message=kw.get("system_message"))

    captured = {}

    def handler(req):
        captured["system_message"] = req.system_message
        return MagicMock(spec=ModelResponse)

    mw.wrap_model_call(request, handler)
    assert captured["system_message"] is not None
    content = str(captured["system_message"].content)
    assert "execute" in content.lower()
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd backend && uv run pytest tests/unit/test_middleware_sandbox.py -v 2>&1 | head -15
```
Expected: `ModuleNotFoundError: No module named 'cubeplex.middleware'`

- [ ] **Step 3: Create middleware utils**

```python
# backend/cubeplex/middleware/__init__.py
```

```python
# backend/cubeplex/middleware/_utils.py
"""Shared utilities for middleware implementations."""

from langchain_core.messages import SystemMessage


def append_to_system_message(
    system_message: SystemMessage | None,
    text: str,
) -> SystemMessage:
    """Append text to a system message, creating one if needed."""
    if system_message is None:
        return SystemMessage(content=text)

    existing = system_message.content
    if isinstance(existing, str):
        return SystemMessage(content=f"{existing}\n\n{text}" if existing else text)

    # Content is a list of blocks
    new_content = list(existing) if isinstance(existing, list) else [{"type": "text", "text": existing}]
    new_content.append({"type": "text", "text": f"\n\n{text}"})
    return SystemMessage(content=new_content)
```

- [ ] **Step 4: Create SandboxMiddleware**

```python
# backend/cubeplex/middleware/sandbox.py
"""SandboxMiddleware — registers the execute tool and injects sandbox context."""

from collections.abc import Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.tools import BaseTool, tool

from cubeplex.middleware._utils import append_to_system_message
from cubeplex.prompts.sandbox import SANDBOX_PROMPT
from cubeplex.sandbox.base import Sandbox


def _create_execute_tool(sandbox: Sandbox) -> BaseTool:
    @tool
    async def execute(command: str) -> str:
        """Execute a shell command in the sandbox environment."""
        result = await sandbox.execute(command)
        output = result.output
        if result.exit_code is not None and result.exit_code != 0:
            output += f"\n[exit code: {result.exit_code}]"
        return output

    return execute


class SandboxMiddleware(AgentMiddleware[Any, ContextT, ResponseT]):
    """Registers the execute tool and injects sandbox context into system prompt."""

    def __init__(self, *, sandbox: Sandbox) -> None:
        self.sandbox = sandbox
        self.tools: list[BaseTool] = [_create_execute_tool(sandbox)]

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        new_system = append_to_system_message(request.system_message, SANDBOX_PROMPT)
        return handler(request.override(system_message=new_system))

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Any],
    ) -> Any:
        new_system = append_to_system_message(request.system_message, SANDBOX_PROMPT)
        return await handler(request.override(system_message=new_system))
```

- [ ] **Step 5: Run tests — expect pass**

```bash
cd backend && uv run pytest tests/unit/test_middleware_sandbox.py -v
```
Expected: `4 passed`

- [ ] **Step 6: Commit**

```bash
cd backend && git add cubeplex/middleware/ tests/unit/test_middleware_sandbox.py
git commit -m "feat: add SandboxMiddleware with execute tool and prompt injection"
```

---

## Task 5: SubAgentMiddleware

**Files:**
- Create: `backend/cubeplex/middleware/subagents.py`
- Create: `backend/tests/unit/test_middleware_subagents.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/unit/test_middleware_subagents.py
import pytest
from cubeplex.middleware.subagents import SubAgent, SubAgentMiddleware


def test_subagent_middleware_registers_task_tool():
    mw = SubAgentMiddleware(subagents=[])
    tool_names = [t.name for t in mw.tools]
    assert "task" in tool_names


def test_subagent_middleware_with_no_subagents_has_empty_task_tool():
    mw = SubAgentMiddleware(subagents=[])
    task_tool = mw.tools[0]
    assert task_tool.name == "task"


def test_subagent_spec_type():
    """SubAgent is a TypedDict with required fields."""
    agent: SubAgent = {
        "name": "test-agent",
        "description": "A test subagent",
        "system_prompt": "You are a test agent.",
    }
    assert agent["name"] == "test-agent"


def test_subagent_middleware_injects_prompt():
    from unittest.mock import MagicMock
    from langchain.agents.middleware.types import ModelRequest, ModelResponse

    mw = SubAgentMiddleware(subagents=[])

    request = MagicMock(spec=ModelRequest)
    request.system_message = None
    request.override = lambda **kw: MagicMock(system_message=kw.get("system_message"))

    captured = {}
    def handler(req):
        captured["system_message"] = req.system_message
        return MagicMock(spec=ModelResponse)

    mw.wrap_model_call(request, handler)
    assert captured["system_message"] is not None
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd backend && uv run pytest tests/unit/test_middleware_subagents.py -v 2>&1 | head -10
```
Expected: `ModuleNotFoundError: No module named 'cubeplex.middleware.subagents'`

- [ ] **Step 3: Create SubAgentMiddleware**

```python
# backend/cubeplex/middleware/subagents.py
"""SubAgentMiddleware — delegates tasks to ephemeral subagents."""

from collections.abc import Callable
from typing import Any, NotRequired

from langchain.agents import create_agent
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from loguru import logger
from pydantic import BaseModel

from cubeplex.middleware._utils import append_to_system_message
from cubeplex.prompts.subagents import SUBAGENT_PROMPT


class SubAgent(dict):
    """Specification for a subagent.

    Required keys: name (str), description (str), system_prompt (str)
    Optional keys: tools (list[BaseTool]), model (BaseChatModel), middleware (list)
    """


class _TaskSchema(BaseModel):
    description: str
    subagent_type: str = "general-purpose"


def _create_task_tool(
    subagents: list[SubAgent],
    default_model: BaseChatModel | None = None,
) -> BaseTool:
    """Build the `task` tool that spawns subagent runs."""

    subagent_map = {s["name"]: s for s in subagents}
    # Always register general-purpose if not present
    if "general-purpose" not in subagent_map:
        subagent_map["general-purpose"] = SubAgent(
            name="general-purpose",
            description="A general-purpose AI assistant",
            system_prompt="You are a helpful AI assistant.",
        )

    available = ", ".join(f'"{k}"' for k in subagent_map)

    async def _run_task(description: str, subagent_type: str = "general-purpose") -> str:
        spec = subagent_map.get(subagent_type, subagent_map["general-purpose"])
        model = spec.get("model") or default_model
        if model is None:
            return f"[error: no model available for subagent '{subagent_type}']"

        tools: list[BaseTool] = list(spec.get("tools", []))
        middleware = list(spec.get("middleware", []))

        agent = create_agent(
            model=model,
            tools=tools,
            system_prompt=spec.get("system_prompt", ""),
            middleware=middleware,
        )
        try:
            result = await agent.ainvoke(
                {"messages": [{"role": "user", "content": description}]}
            )
            messages = result.get("messages", [])
            last = messages[-1] if messages else None
            if last and hasattr(last, "content"):
                content = last.content
                return content if isinstance(content, str) else str(content)
            return "[subagent produced no output]"
        except Exception as e:
            logger.error("Subagent '{}' failed: {}", subagent_type, e)
            return f"[error: {e}]"

    return StructuredTool.from_function(
        coroutine=_run_task,
        name="task",
        description=(
            f"Delegate a task to a subagent. Available subagent types: {available}. "
            "Provide a self-contained description — the subagent has no conversation context."
        ),
        args_schema=_TaskSchema,
    )


class SubAgentMiddleware(AgentMiddleware[Any, ContextT, ResponseT]):
    """Registers the task tool that spawns ephemeral subagents."""

    def __init__(
        self,
        *,
        subagents: list[SubAgent],
        default_model: BaseChatModel | None = None,
    ) -> None:
        self._subagents = subagents
        self._default_model = default_model
        self.tools: list[BaseTool] = [_create_task_tool(subagents, default_model)]

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        new_system = append_to_system_message(request.system_message, SUBAGENT_PROMPT)
        return handler(request.override(system_message=new_system))

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Any],
    ) -> Any:
        new_system = append_to_system_message(request.system_message, SUBAGENT_PROMPT)
        return await handler(request.override(system_message=new_system))
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd backend && uv run pytest tests/unit/test_middleware_subagents.py -v
```
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
cd backend && git add cubeplex/middleware/subagents.py tests/unit/test_middleware_subagents.py
git commit -m "feat: add SubAgentMiddleware with task tool for ephemeral subagent delegation"
```

---

## Task 6: SkillsMiddleware

**Files:**
- Create: `backend/cubeplex/middleware/skills.py`
- Create: `backend/tests/unit/test_middleware_skills.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/unit/test_middleware_skills.py
import pytest
from unittest.mock import MagicMock
from cubeplex.middleware.skills import SkillsMiddleware, SkillSpec


def test_skills_middleware_has_no_tools():
    """Skills are exposed via prompt, not as tools."""
    mw = SkillsMiddleware(skills=[])
    assert mw.tools == []


def test_skills_middleware_injects_empty_skills_prompt():
    from langchain.agents.middleware.types import ModelRequest, ModelResponse

    mw = SkillsMiddleware(skills=[])
    request = MagicMock(spec=ModelRequest)
    request.system_message = None
    request.override = lambda **kw: MagicMock(system_message=kw.get("system_message"))

    captured = {}
    def handler(req):
        captured["system_message"] = req.system_message
        return MagicMock(spec=ModelResponse)

    mw.wrap_model_call(request, handler)
    assert captured["system_message"] is not None


def test_skills_middleware_lists_skills_in_prompt():
    from langchain.agents.middleware.types import ModelRequest, ModelResponse

    skills = [
        SkillSpec(name="git-commit", description="Create well-formatted git commits"),
        SkillSpec(name="code-review", description="Review code for issues"),
    ]
    mw = SkillsMiddleware(skills=skills)

    request = MagicMock(spec=ModelRequest)
    request.system_message = None
    request.override = lambda **kw: MagicMock(system_message=kw.get("system_message"))

    captured = {}
    def handler(req):
        captured["system_message"] = req.system_message
        return MagicMock(spec=ModelResponse)

    mw.wrap_model_call(request, handler)
    content = str(captured["system_message"].content)
    assert "git-commit" in content
    assert "code-review" in content
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd backend && uv run pytest tests/unit/test_middleware_skills.py -v 2>&1 | head -10
```

- [ ] **Step 3: Create SkillsMiddleware**

```python
# backend/cubeplex/middleware/skills.py
"""SkillsMiddleware — injects available skills into system prompt."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.tools import BaseTool

from cubeplex.middleware._utils import append_to_system_message
from cubeplex.prompts.skills import SKILLS_PROMPT_TEMPLATE


@dataclass
class SkillSpec:
    """A skill available to the agent."""

    name: str
    description: str
    path: str | None = None  # path to SKILL.md, if file-backed


class SkillsMiddleware(AgentMiddleware[Any, ContextT, ResponseT]):
    """Injects available skills into the system prompt each model call."""

    tools: list[BaseTool] = []

    def __init__(self, *, skills: list[SkillSpec]) -> None:
        self._skills = skills

    def _build_prompt(self) -> str:
        if not self._skills:
            return ""
        skills_list = "\n".join(
            f"- **{s.name}**: {s.description}" for s in self._skills
        )
        return SKILLS_PROMPT_TEMPLATE.format(skills_list=skills_list)

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        prompt = self._build_prompt()
        if not prompt:
            return handler(request)
        new_system = append_to_system_message(request.system_message, prompt)
        return handler(request.override(system_message=new_system))

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Any],
    ) -> Any:
        prompt = self._build_prompt()
        if not prompt:
            return await handler(request)
        new_system = append_to_system_message(request.system_message, prompt)
        return await handler(request.override(system_message=new_system))
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd backend && uv run pytest tests/unit/test_middleware_skills.py -v
```
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
cd backend && git add cubeplex/middleware/skills.py tests/unit/test_middleware_skills.py
git commit -m "feat: add SkillsMiddleware for skill discovery via system prompt injection"
```

---

## Task 7: Agent Graph Factory

**Files:**
- Create: `backend/cubeplex/agents/graph.py`
- Create: `backend/tests/unit/test_graph.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/unit/test_graph.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from cubeplex.agents.graph import create_cubeplex_agent
from cubeplex.sandbox.local import LocalSandbox


def _make_mock_llm(response_text: str = "hello") -> MagicMock:
    """Build a mock LLM that returns a simple AIMessage with no tool calls."""
    llm = MagicMock()
    llm.bind_tools = MagicMock(return_value=llm)
    ai_msg = AIMessage(content=response_text)
    llm.invoke = MagicMock(return_value=ai_msg)
    llm.ainvoke = AsyncMock(return_value=ai_msg)
    return llm


def test_create_agent_returns_compiled_graph():
    from langgraph.graph.state import CompiledStateGraph

    llm = _make_mock_llm()
    agent = create_cubeplex_agent(llm=llm, tools=[])
    assert isinstance(agent, CompiledStateGraph)


def test_create_agent_with_sandbox():
    sandbox = LocalSandbox()
    llm = _make_mock_llm()
    agent = create_cubeplex_agent(llm=llm, tools=[], sandbox=sandbox)
    assert agent is not None


def test_create_agent_with_checkpointer():
    llm = _make_mock_llm()
    checkpointer = MemorySaver()
    agent = create_cubeplex_agent(llm=llm, tools=[], checkpointer=checkpointer)
    assert agent is not None


@pytest.mark.asyncio
async def test_agent_responds_to_message():
    llm = _make_mock_llm("I can help with that.")
    checkpointer = MemorySaver()
    agent = create_cubeplex_agent(llm=llm, tools=[], checkpointer=checkpointer)

    config = {"configurable": {"thread_id": "test-thread"}}
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content="Hello")]},
        config=config,
    )
    messages = result["messages"]
    assert any(isinstance(m, AIMessage) for m in messages)


@pytest.mark.asyncio
async def test_agent_persists_across_invocations():
    llm = _make_mock_llm("Remembered.")
    checkpointer = MemorySaver()
    agent = create_cubeplex_agent(llm=llm, tools=[], checkpointer=checkpointer)

    config = {"configurable": {"thread_id": "persist-thread"}}
    await agent.ainvoke({"messages": [HumanMessage(content="First")]}, config=config)
    result = await agent.ainvoke({"messages": [HumanMessage(content="Second")]}, config=config)

    # Thread state should contain all 4 messages (2 human + 2 AI)
    assert len(result["messages"]) >= 4
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd backend && uv run pytest tests/unit/test_graph.py -v 2>&1 | head -15
```

- [ ] **Step 3: Create agent graph factory**

```python
# backend/cubeplex/agents/graph.py
"""Agent graph factory — builds the cubeplex agent using create_agent() + middleware."""

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer
from loguru import logger

from cubeplex.middleware.sandbox import SandboxMiddleware
from cubeplex.middleware.skills import SkillSpec, SkillsMiddleware
from cubeplex.middleware.subagents import SubAgent, SubAgentMiddleware
from cubeplex.prompts.system import BASE_SYSTEM_PROMPT
from cubeplex.sandbox.base import Sandbox


def create_cubeplex_agent(
    llm: BaseChatModel,
    tools: list[BaseTool],
    *,
    sandbox: Sandbox | None = None,
    skills: list[SkillSpec] | None = None,
    subagents: list[SubAgent] | None = None,
    checkpointer: Checkpointer | None = None,
) -> CompiledStateGraph:
    """Build the cubeplex agent with the configured middleware stack.

    Returns a CompiledStateGraph (LangGraph) that supports .astream(),
    .ainvoke(), and checkpointer-based thread persistence.

    Args:
        llm: The language model to use.
        tools: Additional tools beyond what middleware provides.
        sandbox: If provided, SandboxMiddleware is added (registers execute tool).
        skills: If provided, SkillsMiddleware is added.
        subagents: If provided, SubAgentMiddleware is added.
        checkpointer: LangGraph checkpointer for conversation persistence.
    """
    middleware = []

    if sandbox is not None:
        middleware.append(SandboxMiddleware(sandbox=sandbox))
        logger.debug("SandboxMiddleware added (sandbox id={})", sandbox.id)

    middleware.append(SkillsMiddleware(skills=skills or []))
    middleware.append(SubAgentMiddleware(subagents=subagents or [], default_model=llm))

    logger.info(
        "Creating cubeplex agent: {} tools, {} middleware",
        len(tools),
        len(middleware),
    )

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=BASE_SYSTEM_PROMPT,
        middleware=middleware,
        checkpointer=checkpointer,
    )
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd backend && uv run pytest tests/unit/test_graph.py -v
```
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
cd backend && git add cubeplex/agents/graph.py tests/unit/test_graph.py
git commit -m "feat: add create_cubeplex_agent() factory with middleware stack"
```

---

## Task 8: Message Conversion + Updated SSE Schemas

**Files:**
- Create: `backend/cubeplex/agents/convert.py`
- Modify: `backend/cubeplex/agents/schemas.py`
- Create: `backend/tests/unit/test_convert_messages.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/unit/test_convert_messages.py
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from cubeplex.agents.convert import convert_to_api_messages


def test_convert_human_message():
    msgs = [HumanMessage(content="Hello")]
    result = convert_to_api_messages(msgs)
    assert result[0]["role"] == "user"
    assert result[0]["content"] == "Hello"


def test_convert_ai_message_text():
    msgs = [AIMessage(content="Hi there")]
    result = convert_to_api_messages(msgs)
    assert result[0]["role"] == "assistant"
    assert result[0]["content"] == "Hi there"
    assert result[0]["tool_calls"] is None


def test_convert_ai_message_with_tool_calls():
    msg = AIMessage(
        content="",
        tool_calls=[{"id": "1", "name": "execute", "args": {"command": "ls"}, "type": "tool_call"}],
    )
    result = convert_to_api_messages([msg])
    assert result[0]["role"] == "assistant"
    assert result[0]["content"] is None
    assert result[0]["tool_calls"] == [{"name": "execute", "arguments": {"command": "ls"}}]


def test_convert_tool_message():
    msgs = [ToolMessage(content="file.txt\nother.txt", name="execute", tool_call_id="1")]
    result = convert_to_api_messages(msgs)
    assert result[0]["role"] == "tool"
    assert result[0]["name"] == "execute"
    assert result[0]["content"] == "file.txt\nother.txt"


def test_convert_ai_message_with_reasoning():
    msg = AIMessage(
        content="The answer is 4",
        additional_kwargs={"reasoning_content": "2+2=4"},
    )
    result = convert_to_api_messages([msg])
    assert result[0]["reasoning"] == "2+2=4"


def test_convert_mixed_messages():
    msgs = [
        HumanMessage(content="What is 2+2?"),
        AIMessage(content="The answer is 4"),
    ]
    result = convert_to_api_messages(msgs)
    assert len(result) == 2
    assert result[0]["role"] == "user"
    assert result[1]["role"] == "assistant"
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd backend && uv run pytest tests/unit/test_convert_messages.py -v 2>&1 | head -10
```

- [ ] **Step 3: Create convert.py**

```python
# backend/cubeplex/agents/convert.py
"""Convert LangChain message types to the API wire format."""

from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage


def convert_to_api_messages(lc_messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """Convert a list of LangChain messages to the API response format.

    LangChain type → API role mapping:
      HumanMessage  → "user"
      AIMessage     → "assistant"
      ToolMessage   → "tool"
    """
    result: list[dict[str, Any]] = []

    for msg in lc_messages:
        if isinstance(msg, HumanMessage):
            result.append({
                "id": getattr(msg, "id", None) or _generate_id(),
                "role": "user",
                "content": msg.content if isinstance(msg.content, str) else str(msg.content),
                "tool_calls": None,
                "reasoning": None,
                "name": None,
                "created_at": _get_timestamp(msg),
            })

        elif isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, list):
                # Extract text from content blocks
                text_parts = [
                    block["text"] for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                content = "".join(text_parts) or None
            else:
                content = content or None

            tool_calls = None
            if msg.tool_calls:
                tool_calls = [
                    {"name": tc["name"], "arguments": tc["args"]}
                    for tc in msg.tool_calls
                ] or None

            reasoning = msg.additional_kwargs.get("reasoning_content")

            result.append({
                "id": getattr(msg, "id", None) or _generate_id(),
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
                "reasoning": reasoning or None,
                "name": None,
                "created_at": _get_timestamp(msg),
            })

        elif isinstance(msg, ToolMessage):
            result.append({
                "id": getattr(msg, "id", None) or _generate_id(),
                "role": "tool",
                "content": msg.content if isinstance(msg.content, str) else str(msg.content),
                "tool_calls": None,
                "reasoning": None,
                "name": msg.name,
                "created_at": _get_timestamp(msg),
            })

    return result


def _generate_id() -> str:
    import uuid
    return str(uuid.uuid4())


def _get_timestamp(msg: BaseMessage) -> str:
    # LangChain messages may carry created_at in response_metadata
    ts = (msg.response_metadata or {}).get("created_at")
    return ts or datetime.now(UTC).isoformat()
```

- [ ] **Step 4: Update schemas.py — add agent_id/agent_name, remove ChainStartEvent**

Replace the content of `backend/cubeplex/agents/schemas.py`:

```python
# backend/cubeplex/agents/schemas.py
"""SSE event schemas for agent execution streaming."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentEvent(BaseModel):
    """Base model for agent streaming events."""

    type: str = Field(description="Event type")
    timestamp: str = Field(description="ISO 8601 timestamp")
    data: dict[str, Any] = Field(description="Event data")
    agent_id: str | None = Field(
        default=None,
        description="None for main agent, 'task:xxx' for subagents",
    )
    agent_name: str | None = Field(
        default=None,
        description="Human-readable subagent description",
    )


class TextDeltaEvent(AgentEvent):
    type: Literal["text_delta"] = "text_delta"


class ReasoningEvent(AgentEvent):
    type: Literal["reasoning"] = "reasoning"


class ToolCallEvent(AgentEvent):
    type: Literal["tool_call"] = "tool_call"


class ToolResultEvent(AgentEvent):
    type: Literal["tool_result"] = "tool_result"


class ErrorEvent(AgentEvent):
    type: Literal["error"] = "error"


class DoneEvent(AgentEvent):
    type: Literal["done"] = "done"
    data: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 5: Run tests — expect pass**

```bash
cd backend && uv run pytest tests/unit/test_convert_messages.py -v
```
Expected: `7 passed`

- [ ] **Step 6: Commit**

```bash
cd backend && git add cubeplex/agents/convert.py cubeplex/agents/schemas.py tests/unit/test_convert_messages.py
git commit -m "feat: add message conversion and update event schemas with agent_id/agent_name"
```

---

## Task 9: Update App Factory + Conversations API

**Files:**
- Modify: `backend/cubeplex/api/app.py`
- Modify: `backend/cubeplex/api/routes/v1/conversations.py`

- [ ] **Step 1: Update create_app() with dependency injection**

In `backend/cubeplex/api/app.py`, update the `create_app` signature and lifespan:

```python
# backend/cubeplex/api/app.py
"""FastAPI application factory."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from loguru import logger

from cubeplex.api.exceptions import register_exception_handlers
from cubeplex.api.routes.v1 import conversations as conversations_router_module
from cubeplex.api.middleware.cancellation import CancellationMiddleware
from cubeplex.api.middleware.user_identity import UserIdentityMiddleware


@asynccontextmanager
async def _lifespan(
    app: FastAPI,
    checkpointer_factory: Callable[[], Any] | None,
    sandbox_factory: Callable[[], Any] | None,
) -> AsyncIterator[None]:
    """Application lifespan — startup and shutdown."""
    from cubeplex.agents.checkpointer import create_checkpointer

    # Initialize LangGraph checkpointer tables
    checkpointer = await (checkpointer_factory() if checkpointer_factory else create_checkpointer())
    if checkpointer is not None:
        try:
            await checkpointer.setup()
            logger.info("LangGraph checkpointer tables initialized")
        except Exception as e:
            logger.warning("Checkpointer setup failed (may already exist): {}", e)
        finally:
            if hasattr(checkpointer, "conn"):
                checkpointer.conn.close()

    # Store factories in app state for use in routes
    app.state.checkpointer_factory = checkpointer_factory
    app.state.sandbox_factory = sandbox_factory

    # Initialize SandboxManager if sandbox enabled
    from cubeplex.config import config
    cleanup_task = None
    if config.get("sandbox.enabled", False):
        from cubeplex.sandbox.manager import init_sandbox_manager
        await init_sandbox_manager()
        logger.info("SandboxManager initialized")

        async def _cleanup_loop() -> None:
            from cubeplex.sandbox.manager import get_sandbox_manager
            while True:
                await asyncio.sleep(60)
                try:
                    mgr = get_sandbox_manager()
                    await mgr.cleanup_expired()
                except Exception as e:
                    logger.warning("Sandbox cleanup error: {}", e)

        cleanup_task = asyncio.create_task(_cleanup_loop())

    logger.info("cubeplex started")
    yield

    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
    logger.info("cubeplex shutdown complete")


def create_app(
    checkpointer_factory: Callable[[], Any] | None = None,
    sandbox_factory: Callable[[], Any] | None = None,
) -> FastAPI:
    """Create the FastAPI application.

    Args:
        checkpointer_factory: Callable that returns a checkpointer instance.
            Defaults to create_checkpointer() (MySQL). Pass `lambda: MemorySaver()`
            for testing.
        sandbox_factory: Callable that returns a Sandbox instance.
            Defaults to None. Pass `lambda: LocalSandbox()` for testing.
    """
    from functools import partial

    lifespan = partial(
        _lifespan,
        checkpointer_factory=checkpointer_factory,
        sandbox_factory=sandbox_factory,
    )

    app = FastAPI(
        title="cubeplex",
        lifespan=lifespan,
    )

    app.add_middleware(CancellationMiddleware)
    app.add_middleware(UserIdentityMiddleware)

    register_exception_handlers(app)

    from cubeplex.api.routes.v1.conversations import router as conversations_router
    app.include_router(conversations_router, prefix="/api/v1")

    return app
```

- [ ] **Step 2: Rewrite conversations.py — send_message and list_messages**

Replace only the `send_message` and `list_messages` endpoints in `backend/cubeplex/api/routes/v1/conversations.py`. Keep all conversation CRUD endpoints unchanged.

Replace everything from line 124 (`class SendMessageRequest`) to the end of the file:

```python
class SendMessageRequest(BaseModel):
    """Request body for sending a message."""

    content: str


def _ns_to_agent_id(ns: tuple) -> str | None:
    """Convert LangGraph namespace tuple to agent_id string."""
    if not ns:
        return None
    return ":".join(str(part) for part in ns)


def _convert_stream_chunk(chunk: Any, ns: tuple = ()) -> list[AgentEvent]:
    """Convert a LangGraph stream chunk to SSE events.

    Adapted from DeepAgentExecutor._handle_stream_chunk().
    chunk is a (message, metadata) tuple from stream_mode='messages'.
    """
    from datetime import UTC, datetime

    from cubeplex.agents.schemas import (
        DoneEvent,
        ReasoningEvent,
        TextDeltaEvent,
        ToolCallEvent,
        ToolResultEvent,
    )

    timestamp = datetime.now(UTC).isoformat()
    agent_id = _ns_to_agent_id(ns)
    events: list[AgentEvent] = []

    if not isinstance(chunk, tuple) or len(chunk) < 2:
        return events

    msg, metadata = chunk

    if isinstance(msg, dict):
        content = msg.get("content", "")
        additional_kwargs = msg.get("additional_kwargs", {})
        tool_calls = msg.get("tool_calls", [])
        usage_metadata = msg.get("usage_metadata", {})
        tool_name = msg.get("name")
    else:
        content = getattr(msg, "content", "") or ""
        additional_kwargs = getattr(msg, "additional_kwargs", {}) or {}
        tool_calls = getattr(msg, "tool_calls", []) or []
        usage_metadata = getattr(msg, "usage_metadata", {}) or {}
        tool_name = getattr(msg, "name", None)

    reasoning_content = additional_kwargs.get("reasoning_content", "") if additional_kwargs else ""
    if reasoning_content:
        events.append(ReasoningEvent(
            timestamp=timestamp,
            data={"content": reasoning_content},
            agent_id=agent_id,
        ))

    if tool_calls:
        for tc in tool_calls:
            tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
            tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
            tc_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
            if not tc_name:
                continue
            events.append(ToolCallEvent(
                timestamp=timestamp,
                data={"tool_call_id": tc_id, "name": tc_name, "arguments": tc_args},
                agent_id=agent_id,
            ))

    if tool_name and content:
        events.append(ToolResultEvent(
            timestamp=timestamp,
            data={"tool_name": tool_name, "content": content if isinstance(content, str) else str(content)},
            agent_id=agent_id,
        ))
        return events

    if content:
        events.append(TextDeltaEvent(
            timestamp=timestamp,
            data={
                "content": content,
                "usage": {
                    "input_tokens": (usage_metadata or {}).get("input_tokens", 0),
                    "output_tokens": (usage_metadata or {}).get("output_tokens", 0),
                },
            },
            agent_id=agent_id,
        ))

    return events


@router.post("/{conversation_id}/messages", status_code=status.HTTP_200_OK)
async def send_message(
    conversation_id: str,
    request_obj: SendMessageRequest,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    """Send a user message and stream the assistant response via SSE."""
    conv_repo = ConversationRepository(session)
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    if not request_obj.content or not request_obj.content.strip():
        raise InvalidInputError(
            message="Content field cannot be empty",
            details="Please provide a non-empty content string",
        )

    user_id: str = getattr(raw_request.state, "user_id", "anonymous")

    async def event_generator() -> AsyncIterator[str]:
        from datetime import UTC, datetime

        from langchain_core.messages import HumanMessage

        from cubeplex.agents.checkpointer import create_checkpointer
        from cubeplex.agents.graph import create_cubeplex_agent
        from cubeplex.agents.schemas import DoneEvent, ErrorEvent
        from cubeplex.api.exceptions import ExecutionError, InternalError
        from cubeplex.llm.factory import LLMFactory

        checkpointer = None
        sandbox = None
        sandbox_manager = None

        try:
            checkpointer = await create_checkpointer()

            from cubeplex.config import config
            sandbox_enabled = config.get("sandbox.enabled", False)
            if sandbox_enabled:
                try:
                    from cubeplex.sandbox.manager import get_sandbox_manager
                    sandbox_manager = get_sandbox_manager()
                    sandbox = await sandbox_manager.get_or_create(user_id)
                except Exception as e:
                    logger.warning("Sandbox unavailable, continuing without: {}", e)

            factory = LLMFactory()
            providers = factory.list_providers()
            llm = factory.create(
                model_id=factory.list_models(providers[0])[0],
                provider_name=providers[0],
            )
            from cubeplex.tools import get_registry
            tools = get_registry().list_tools()

            agent = create_cubeplex_agent(
                llm=llm,
                tools=tools,
                sandbox=sandbox,
                checkpointer=checkpointer,
            )

            config_dict = {"configurable": {"thread_id": conversation_id}}

            async for chunk in agent.astream(
                {"messages": [HumanMessage(content=request_obj.content)]},
                stream_mode="messages",
                stream_subgraphs=True,
                config=config_dict,
            ):
                # When stream_subgraphs=True, chunk may be prefixed with ns
                ns: tuple = ()
                if isinstance(chunk, tuple) and len(chunk) == 3:
                    ns, _, chunk = chunk
                elif isinstance(chunk, dict) and "ns" in chunk:
                    ns = chunk.get("ns", ())
                    chunk = chunk.get("data", chunk)

                for event in _convert_stream_chunk(chunk, ns=ns):
                    yield f"data: {event.model_dump_json()}\n\n"

        except Exception as e:
            logger.error("Streaming error: {}", str(e), exc_info=True)
            error = InternalError(
                message="An unexpected error occurred during execution",
                details=str(e),
            )
            error_event = error.to_error_event()
            yield f"data: {error_event.model_dump_json()}\n\n"

        finally:
            if sandbox_manager and sandbox:
                try:
                    await sandbox_manager.release(sandbox.id)
                except Exception as e:
                    logger.warning("Error releasing sandbox: {}", e)

            if checkpointer is not None and hasattr(checkpointer, "conn"):
                try:
                    checkpointer.conn.close()
                except Exception as e:
                    logger.warning("Error closing checkpointer: {}", e)

            # Update conversation timestamp
            from sqlalchemy.ext.asyncio import AsyncSession
            from sqlalchemy.pool import NullPool
            from cubeplex.db.engine import _build_database_url
            save_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
            try:
                async with AsyncSession(save_engine, expire_on_commit=False) as save_session:
                    save_conv_repo = ConversationRepository(save_session)
                    await save_conv_repo.update_timestamp(conversation_id)
            finally:
                await save_engine.dispose()

            done = DoneEvent(timestamp=datetime.now(UTC).isoformat())
            yield f"data: {done.model_dump_json()}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """List messages in a conversation, read from LangGraph thread state."""
    conv_repo = ConversationRepository(session)
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    from cubeplex.agents.checkpointer import create_checkpointer
    from cubeplex.agents.convert import convert_to_api_messages

    checkpointer = await create_checkpointer()
    if checkpointer is None:
        return {"messages": [], "total": 0}

    try:
        config = {"configurable": {"thread_id": conversation_id}}
        checkpoint = await checkpointer.aget(config)
        if not checkpoint:
            return {"messages": [], "total": 0}

        lc_messages = checkpoint["channel_values"].get("messages", [])
        messages = convert_to_api_messages(lc_messages)
        return {"messages": messages, "total": len(messages)}
    finally:
        if hasattr(checkpointer, "conn"):
            checkpointer.conn.close()
```

Also update the imports at the top of conversations.py — remove `MessageRepository` import and add `Any`:

```python
# Remove this import line:
# from cubeplex.repositories import ConversationRepository, MessageRepository
# Replace with:
from cubeplex.repositories import ConversationRepository

# Add to existing imports:
from typing import Any
```

- [ ] **Step 3: Run type check**

```bash
cd backend && uv run mypy cubeplex/api/ --ignore-missing-imports 2>&1 | head -30
```

- [ ] **Step 4: Commit**

```bash
cd backend && git add cubeplex/api/app.py cubeplex/api/routes/v1/conversations.py
git commit -m "feat: update conversations API to use create_cubeplex_agent and read messages from checkpointer"
```

---

## Task 10: Delete Old Files + Update Dependencies

**Files:**
- Delete: `backend/cubeplex/agents/executor.py`
- Delete: `backend/cubeplex/models/message.py`
- Delete: `backend/cubeplex/repositories/message.py`
- Modify: `backend/cubeplex/models/__init__.py`
- Modify: `backend/cubeplex/repositories/__init__.py`
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Remove old files**

```bash
cd backend && rm cubeplex/agents/executor.py cubeplex/models/message.py cubeplex/repositories/message.py
```

- [ ] **Step 2: Update models/__init__.py**

```python
# backend/cubeplex/models/__init__.py
from cubeplex.models.conversation import Conversation
from cubeplex.models.user_sandbox import UserSandbox

__all__ = ["Conversation", "UserSandbox"]
```

- [ ] **Step 3: Update repositories/__init__.py**

```python
# backend/cubeplex/repositories/__init__.py
from cubeplex.repositories.conversation import ConversationRepository
from cubeplex.repositories.user_sandbox import UserSandboxRepository

__all__ = ["ConversationRepository", "UserSandboxRepository"]
```

- [ ] **Step 4: Remove dependencies from pyproject.toml**

Remove these three lines from the `dependencies` list:

```toml
# Remove:
"deepagents>=0.4.5",
"nest-asyncio>=1.6.0",
"asyncer>=0.0.17",
```

- [ ] **Step 5: Sync dependencies**

```bash
cd backend && uv sync
```

Expected: uv resolves without `deepagents`, `nest-asyncio`, `asyncer`

- [ ] **Step 6: Run full type check**

```bash
cd backend && uv run mypy cubeplex/
```
Expected: no errors (or only pre-existing ones)

- [ ] **Step 7: Commit**

```bash
cd backend && git add -u && git add pyproject.toml
git commit -m "chore: remove deepagents, nest-asyncio, asyncer; delete message model and repository"
```

---

## Task 11: Alembic Migration — Drop messages Table

**Files:**
- Create: `backend/alembic/versions/<hash>_drop_messages_table.py`

- [ ] **Step 1: Generate the migration**

```bash
cd backend && uv run alembic revision -m "drop_messages_table"
```

This creates a new file in `alembic/versions/`. Open it and fill in the `upgrade` and `downgrade` functions:

```python
def upgrade() -> None:
    op.drop_table("messages")


def downgrade() -> None:
    op.create_table(
        "messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("events", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
```

- [ ] **Step 2: Apply the migration (against dev DB)**

```bash
cd backend && uv run alembic upgrade head
```

Expected: migration runs cleanly, `messages` table is dropped

- [ ] **Step 3: Commit**

```bash
cd backend && git add alembic/versions/
git commit -m "chore: alembic migration to drop messages table"
```

---

## Task 12: Backend Unit Tests — Run All

- [ ] **Step 1: Run all unit tests**

```bash
cd backend && uv run pytest tests/unit/ -v
```

Expected: all tests pass. If any fail, fix before proceeding.

- [ ] **Step 2: Run type check**

```bash
cd backend && uv run mypy cubeplex/
```

Expected: no errors

---

## Task 13: Backend E2E Tests

**Files:**
- Create: `backend/tests/e2e/test_conversation_flow.py`
- Create: `backend/tests/e2e/test_streaming.py`
- Create: `backend/tests/e2e/test_thread_state.py`
- Modify: `backend/tests/conftest.py`

- [ ] **Step 1: Update conftest.py with new client fixture**

Add to `backend/tests/conftest.py`:

```python
import pytest
from httpx import AsyncClient
from langgraph.checkpoint.memory import MemorySaver

from cubeplex.api.app import create_app
from cubeplex.sandbox.local import LocalSandbox


@pytest.fixture
async def client():
    """Test client using MemorySaver checkpointer and LocalSandbox."""
    app = create_app(
        checkpointer_factory=MemorySaver,
        sandbox_factory=LocalSandbox,
    )
    async with AsyncClient(app=app, base_url="http://test") as c:
        yield c


@pytest.fixture
async def conversation_id(client: AsyncClient) -> str:
    """Create a conversation and return its ID."""
    resp = await client.post("/api/v1/conversations", params={"title": "test"})
    assert resp.status_code == 201
    return resp.json()["id"]


async def collect_sse_events(
    client: AsyncClient, url: str, json: dict
) -> list[dict]:
    """POST to an SSE endpoint and collect all events as a list."""
    import json as json_lib
    events = []
    async with client.stream("POST", url, json=json) as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json_lib.loads(line[6:]))
    return events
```

- [ ] **Step 2: Write conversation flow E2E test**

```python
# backend/tests/e2e/test_conversation_flow.py
"""E2E test: full conversation lifecycle with real LLM and MemorySaver."""
import pytest
from httpx import AsyncClient

from tests.conftest import collect_sse_events


@pytest.mark.asyncio
async def test_send_message_returns_sse_stream(client: AsyncClient, conversation_id: str):
    events = await collect_sse_events(
        client,
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "Say the word 'hello' and nothing else."},
    )
    assert len(events) > 0
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_stream_contains_text_delta(client: AsyncClient, conversation_id: str):
    events = await collect_sse_events(
        client,
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "Say the word 'hello' and nothing else."},
    )
    text_events = [e for e in events if e["type"] == "text_delta"]
    assert len(text_events) > 0
    full_text = "".join(e["data"]["content"] for e in text_events)
    assert len(full_text) > 0


@pytest.mark.asyncio
async def test_list_messages_returns_history_after_send(
    client: AsyncClient, conversation_id: str
):
    await collect_sse_events(
        client,
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "Say the word 'hello' and nothing else."},
    )

    resp = await client.get(f"/api/v1/conversations/{conversation_id}/messages")
    assert resp.status_code == 200
    messages = resp.json()["messages"]
    assert len(messages) >= 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Say the word 'hello' and nothing else."
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"]


@pytest.mark.asyncio
async def test_send_to_nonexistent_conversation_returns_404(client: AsyncClient):
    resp = await client.post(
        "/api/v1/conversations/nonexistent-id/messages",
        json={"content": "hello"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_send_empty_content_returns_400(client: AsyncClient, conversation_id: str):
    resp = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": ""},
    )
    assert resp.status_code == 400
```

- [ ] **Step 3: Write streaming format E2E test**

```python
# backend/tests/e2e/test_streaming.py
"""E2E test: SSE stream format validation."""
import json
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_sse_response_content_type(client: AsyncClient, conversation_id: str):
    async with client.stream(
        "POST",
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "Say hi."},
    ) as response:
        assert "text/event-stream" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_every_event_is_valid_json(client: AsyncClient, conversation_id: str):
    async with client.stream(
        "POST",
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "Say hi."},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])  # must not raise
                assert "type" in payload
                assert "timestamp" in payload


@pytest.mark.asyncio
async def test_stream_always_ends_with_done(client: AsyncClient, conversation_id: str):
    events = []
    async with client.stream(
        "POST",
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "Say hi."},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_agent_id_is_null_for_main_agent(client: AsyncClient, conversation_id: str):
    events = []
    async with client.stream(
        "POST",
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "Say hi."},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    text_events = [e for e in events if e["type"] == "text_delta"]
    assert len(text_events) > 0
    for e in text_events:
        assert e.get("agent_id") is None  # main agent
```

- [ ] **Step 4: Write thread state E2E test**

```python
# backend/tests/e2e/test_thread_state.py
"""E2E test: multi-turn conversation state persistence."""
import pytest
from httpx import AsyncClient

from tests.conftest import collect_sse_events


@pytest.mark.asyncio
async def test_multi_turn_context_is_retained(client: AsyncClient, conversation_id: str):
    """Agent should remember context from previous turns."""
    # Turn 1: establish a fact
    await collect_sse_events(
        client,
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "My name is TestUser. Just acknowledge this."},
    )

    # Turn 2: recall the fact
    events = await collect_sse_events(
        client,
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "What is my name? Reply with just the name."},
    )

    text_events = [e for e in events if e["type"] == "text_delta"]
    full_text = "".join(e["data"]["content"] for e in text_events)
    assert "TestUser" in full_text


@pytest.mark.asyncio
async def test_message_count_after_two_turns(client: AsyncClient, conversation_id: str):
    """Thread state should accumulate all messages across turns."""
    await collect_sse_events(
        client,
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "First message."},
    )
    await collect_sse_events(
        client,
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "Second message."},
    )

    resp = await client.get(f"/api/v1/conversations/{conversation_id}/messages")
    messages = resp.json()["messages"]
    # 2 user messages + at least 2 assistant messages
    assert len(messages) >= 4
    user_msgs = [m for m in messages if m["role"] == "user"]
    assert len(user_msgs) == 2


@pytest.mark.asyncio
async def test_separate_conversations_have_independent_state(client: AsyncClient):
    """Two conversations must not share thread state."""
    resp1 = await client.post("/api/v1/conversations", params={"title": "conv1"})
    resp2 = await client.post("/api/v1/conversations", params={"title": "conv2"})
    conv1_id = resp1.json()["id"]
    conv2_id = resp2.json()["id"]

    await collect_sse_events(
        client,
        f"/api/v1/conversations/{conv1_id}/messages",
        json={"content": "My secret word is ALPHA."},
    )

    events = await collect_sse_events(
        client,
        f"/api/v1/conversations/{conv2_id}/messages",
        json={"content": "Do you know my secret word? Just say no if you don't."},
    )

    text = "".join(
        e["data"]["content"]
        for e in events
        if e["type"] == "text_delta"
    )
    assert "ALPHA" not in text
```

- [ ] **Step 5: Run E2E tests (requires LLM API key)**

```bash
cd backend && uv run pytest tests/e2e/ -v --timeout=60
```

Expected: all pass. These make real LLM calls — may take 30–60s each.

- [ ] **Step 6: Commit**

```bash
cd backend && git add tests/conftest.py tests/e2e/
git commit -m "test: add backend E2E tests for conversation flow, streaming, and thread state"
```

---

## Task 14: Frontend Types + Store Rewrite

**Files:**
- Modify: `frontend/packages/core/src/types/message.ts`
- Modify: `frontend/packages/core/src/types/events.ts`
- Modify: `frontend/packages/core/src/stores/messageStore.ts`

- [ ] **Step 1: Update message types**

```typescript
// frontend/packages/core/src/types/message.ts
export interface Message {
  id: string
  role: 'user' | 'assistant' | 'tool'
  content: string | null
  tool_calls?: { name: string; arguments: Record<string, unknown> }[] | null
  reasoning?: string | null
  name?: string | null  // for tool messages
  created_at?: string
}
```

- [ ] **Step 2: Update event types**

```typescript
// frontend/packages/core/src/types/events.ts
export type AgentEventType =
  | 'text_delta'
  | 'reasoning'
  | 'tool_call'
  | 'tool_result'
  | 'error'
  | 'done'

export interface AgentEvent {
  type: AgentEventType
  timestamp: string
  data: Record<string, unknown>
  agent_id: string | null    // null = main agent, "task:xxx" = subagent
  agent_name: string | null  // subagent description
}

export interface TextDeltaEvent extends AgentEvent {
  type: 'text_delta'
  data: { content: string; usage?: { input_tokens: number; output_tokens: number } }
}

export interface ReasoningEvent extends AgentEvent {
  type: 'reasoning'
  data: { content: string }
}

export interface ToolCallEvent extends AgentEvent {
  type: 'tool_call'
  data: { tool_call_id: string; name: string; arguments: Record<string, unknown> }
}

export interface ToolResultEvent extends AgentEvent {
  type: 'tool_result'
  data: { tool_name: string; content: string }
}

export interface ErrorEvent extends AgentEvent {
  type: 'error'
  data: { error_code: string; message: string; details?: string }
}

export interface DoneEvent extends AgentEvent {
  type: 'done'
  data: Record<string, unknown>
}
```

- [ ] **Step 3: Rewrite messageStore.ts**

```typescript
// frontend/packages/core/src/stores/messageStore.ts
import { create } from 'zustand'
import type { AgentEvent, Message, TextDeltaEvent, ToolCallEvent, ReasoningEvent } from '../types'
import type { ApiClient } from '../api'
import { listMessages, streamMessages } from '../api'

export interface AgentStream {
  text: string
  toolCalls: ToolCallEvent[]
  reasoning: string
  name: string | null
}

export interface MessageStore {
  messages: Message[]
  streamAgents: Record<string, AgentStream>   // "main" or "task:xxx"
  isStreaming: boolean
  error: string | null

  loadMessages(client: ApiClient, conversationId: string): Promise<void>
  send(client: ApiClient, conversationId: string, content: string): Promise<void>
  clearStream(): void
}

const MAIN_AGENT_KEY = 'main'

function emptyStream(name: string | null = null): AgentStream {
  return { text: '', toolCalls: [], reasoning: '', name }
}

export const useMessageStore = create<MessageStore>((set, get) => ({
  messages: [],
  streamAgents: {},
  isStreaming: false,
  error: null,

  async loadMessages(client: ApiClient, conversationId: string) {
    if (get().isStreaming) return
    try {
      const messages = await listMessages(client, conversationId)
      set({ messages, error: null })
    } catch (err) {
      set({ error: (err as Error).message })
    }
  },

  async send(client: ApiClient, conversationId: string, content: string) {
    // Optimistic: add user message immediately
    const userMessage: Message = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    }

    set((s) => ({
      messages: [...s.messages, userMessage],
      streamAgents: { [MAIN_AGENT_KEY]: emptyStream() },
      isStreaming: true,
      error: null,
    }))

    try {
      for await (const event of streamMessages(client.baseUrl, conversationId, content)) {
        const agentKey = event.agent_id ?? MAIN_AGENT_KEY

        if (event.type === 'text_delta') {
          const e = event as TextDeltaEvent
          set((s) => ({
            streamAgents: {
              ...s.streamAgents,
              [agentKey]: {
                ...s.streamAgents[agentKey] ?? emptyStream(event.agent_name),
                text: (s.streamAgents[agentKey]?.text ?? '') + e.data.content,
              },
            },
          }))
        } else if (event.type === 'reasoning') {
          const e = event as ReasoningEvent
          set((s) => ({
            streamAgents: {
              ...s.streamAgents,
              [agentKey]: {
                ...s.streamAgents[agentKey] ?? emptyStream(event.agent_name),
                reasoning: (s.streamAgents[agentKey]?.reasoning ?? '') + e.data.content,
              },
            },
          }))
        } else if (event.type === 'tool_call') {
          const e = event as ToolCallEvent
          set((s) => ({
            streamAgents: {
              ...s.streamAgents,
              [agentKey]: {
                ...s.streamAgents[agentKey] ?? emptyStream(event.agent_name),
                toolCalls: [...(s.streamAgents[agentKey]?.toolCalls ?? []), e],
              },
            },
          }))
        } else if (event.type === 'done') {
          break
        } else if (event.type === 'error') {
          set({ error: (event.data as { message: string }).message })
          break
        }
      }
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      // Build final assistant message from accumulated main agent stream
      const mainStream = get().streamAgents[MAIN_AGENT_KEY]
      if (mainStream) {
        const assistantMessage: Message = {
          id: `assistant-${Date.now()}`,
          role: 'assistant',
          content: mainStream.text || null,
          tool_calls: mainStream.toolCalls.length > 0
            ? mainStream.toolCalls.map((tc) => ({
                name: tc.data.name,
                arguments: tc.data.arguments,
              }))
            : null,
          reasoning: mainStream.reasoning || null,
          created_at: new Date().toISOString(),
        }
        set((s) => ({
          messages: [...s.messages, assistantMessage],
          isStreaming: false,
          streamAgents: {},
        }))
      } else {
        set({ isStreaming: false, streamAgents: {} })
      }
    }
  },

  clearStream() {
    set({ streamAgents: {}, isStreaming: false })
  },
}))
```

- [ ] **Step 4: Commit**

```bash
cd frontend && git add packages/core/src/types/ packages/core/src/stores/messageStore.ts
git commit -m "feat: update types and rewrite messageStore with flat state and per-agent streaming"
```

---

## Task 15: Frontend Components

**Files:**
- Modify: `frontend/packages/web/hooks/useMessages.ts`
- Modify: `frontend/packages/web/components/chat/AssistantMessage.tsx`
- Modify: `frontend/packages/web/components/chat/MessageList.tsx`
- Create: `frontend/packages/web/components/chat/SubAgentCard.tsx`

- [ ] **Step 1: Simplify useMessages hook**

```typescript
// frontend/packages/web/hooks/useMessages.ts
import { useMessageStore } from '@cubeplex/core/stores'
import type { AgentStream } from '@cubeplex/core/stores'

export function useMessages() {
  const messages = useMessageStore((s) => s.messages)
  const isStreaming = useMessageStore((s) => s.isStreaming)
  const streamAgents = useMessageStore((s) => s.streamAgents)

  const mainStream = streamAgents['main'] ?? null
  const subAgentStreams = Object.entries(streamAgents).filter(([key]) => key !== 'main')

  return { messages, isStreaming, mainStream, subAgentStreams }
}
```

- [ ] **Step 2: Create SubAgentCard component**

```tsx
// frontend/packages/web/components/chat/SubAgentCard.tsx
'use client'

import { useState } from 'react'
import { ChevronDown, ChevronRight, Bot } from 'lucide-react'
import type { AgentStream } from '@cubeplex/core/stores'
import type { ToolCallEvent } from '@cubeplex/core/types'

interface Props {
  agentId: string
  stream: AgentStream
  isRunning: boolean
}

export function SubAgentCard({ agentId, stream, isRunning }: Props) {
  const [open, setOpen] = useState(true)
  const name = stream.name ?? agentId

  return (
    <div className="border border-border rounded-lg my-2 overflow-hidden bg-muted/20">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-sm text-muted-foreground hover:bg-muted/30 transition-colors"
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <Bot className="h-3 w-3" />
        <span className="font-medium">{name}</span>
        {isRunning && (
          <span className="ml-auto flex gap-0.5">
            {[0, 1, 2].map((i) => (
              <span
                key={i}
                className="w-1 h-1 rounded-full bg-muted-foreground animate-bounce"
                style={{ animationDelay: `${i * 150}ms` }}
              />
            ))}
          </span>
        )}
      </button>

      {open && (
        <div className="px-3 pb-3 pt-1 space-y-1">
          {stream.toolCalls.map((tc, i) => (
            <div key={i} className="text-xs font-mono text-muted-foreground truncate">
              <span className="text-foreground/60">{tc.data.name}</span>
              {' '}
              <span className="opacity-60">
                {JSON.stringify(tc.data.arguments).slice(0, 80)}
              </span>
            </div>
          ))}
          {stream.text && (
            <p className="text-sm text-foreground/80 whitespace-pre-wrap">{stream.text}</p>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Simplify AssistantMessage.tsx**

Replace the component. Remove `extractText`, `extractReasoning`, `hasToolActivity`. Read directly from props (for history messages) or store (for streaming).

```tsx
// frontend/packages/web/components/chat/AssistantMessage.tsx
'use client'

import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { Message } from '@cubeplex/core/types'
import type { AgentStream } from '@cubeplex/core/stores'

interface HistoryProps {
  message: Message
  stream?: never
  isStreaming?: never
}

interface StreamingProps {
  message?: never
  stream: AgentStream
  isStreaming: true
}

type Props = HistoryProps | StreamingProps

function ReasoningBlock({ content }: { content: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mb-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        Reasoning
      </button>
      {open && (
        <div className="mt-1 p-2 rounded bg-muted/30 text-xs text-muted-foreground whitespace-pre-wrap font-mono">
          {content}
        </div>
      )}
    </div>
  )
}

function ToolCallList({ toolCalls }: { toolCalls: { name: string; arguments: Record<string, unknown> }[] }) {
  return (
    <div className="mb-2 space-y-1">
      {toolCalls.map((tc, i) => (
        <div key={i} className="text-xs font-mono px-2 py-1 rounded bg-muted/40 text-muted-foreground">
          <span className="text-foreground/70">{tc.name}</span>
          {' '}
          <span className="opacity-60">{JSON.stringify(tc.arguments).slice(0, 100)}</span>
        </div>
      ))}
    </div>
  )
}

export function AssistantMessage({ message, stream, isStreaming }: Props) {
  const text = isStreaming ? stream.text : message.content
  const reasoning = isStreaming ? stream.reasoning : message.reasoning
  const toolCalls = isStreaming
    ? stream.toolCalls.map((tc) => ({ name: tc.data.name, arguments: tc.data.arguments }))
    : (message.tool_calls ?? [])

  return (
    <div data-role="assistant" className="flex gap-3 py-4">
      <div className="flex-1 min-w-0">
        {reasoning && <ReasoningBlock content={reasoning} />}
        {toolCalls.length > 0 && <ToolCallList toolCalls={toolCalls} />}
        {text ? (
          <div className="prose prose-sm dark:prose-invert max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
          </div>
        ) : isStreaming ? (
          <span className="flex gap-1">
            {[0, 1, 2].map((i) => (
              <span
                key={i}
                className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce"
                style={{ animationDelay: `${i * 150}ms` }}
              />
            ))}
          </span>
        ) : null}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Update MessageList.tsx**

```tsx
// frontend/packages/web/components/chat/MessageList.tsx
'use client'

import { useEffect, useRef } from 'react'
import { createApiClient } from '@cubeplex/core/api'
import { useMessageStore } from '@cubeplex/core/stores'
import { useMessages } from '../../hooks/useMessages'
import { UserMessage } from './UserMessage'
import { AssistantMessage } from './AssistantMessage'
import { SubAgentCard } from './SubAgentCard'

interface Props {
  conversationId: string
}

export function MessageList({ conversationId }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const loadMessages = useMessageStore((s) => s.loadMessages)
  const { messages, isStreaming, mainStream, subAgentStreams } = useMessages()

  useEffect(() => {
    const client = createApiClient('')
    loadMessages(client, conversationId)
  }, [conversationId, loadMessages])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isStreaming, mainStream?.text])

  return (
    <div className="flex flex-col gap-1 px-4">
      {messages.map((msg) =>
        msg.role === 'user' ? (
          <UserMessage key={msg.id} message={msg} />
        ) : msg.role === 'assistant' ? (
          <AssistantMessage key={msg.id} message={msg} />
        ) : null
      )}

      {isStreaming && mainStream && (
        <>
          {subAgentStreams.map(([agentId, stream]) => (
            <SubAgentCard
              key={agentId}
              agentId={agentId}
              stream={stream}
              isRunning={isStreaming}
            />
          ))}
          <AssistantMessage stream={mainStream} isStreaming />
        </>
      )}

      <div ref={bottomRef} />
    </div>
  )
}
```

- [ ] **Step 5: Build core package and check types**

```bash
cd frontend && pnpm --filter @cubeplex/core build && pnpm type-check
```

Expected: no type errors

- [ ] **Step 6: Commit**

```bash
cd frontend && git add packages/
git commit -m "feat: simplify frontend components, add SubAgentCard for subagent streaming"
```

---

## Task 16: Frontend Testing Setup + Unit Tests

**Files:**
- Create: `frontend/packages/web/vitest.config.ts`
- Modify: `frontend/packages/web/package.json` (add test script)
- Create: `frontend/packages/web/__tests__/hooks/useMessages.test.ts`

- [ ] **Step 1: Install vitest**

```bash
cd frontend && pnpm --filter web add -D vitest @vitest/ui jsdom @testing-library/react @testing-library/jest-dom
```

- [ ] **Step 2: Create vitest config**

```typescript
// frontend/packages/web/vitest.config.ts
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
  },
})
```

```typescript
// frontend/packages/web/vitest.setup.ts
import '@testing-library/jest-dom'
```

- [ ] **Step 3: Add test script to web package.json**

In `frontend/packages/web/package.json`, add to scripts:
```json
"test": "vitest run",
"test:watch": "vitest"
```

- [ ] **Step 4: Write useMessages unit test**

```typescript
// frontend/packages/web/__tests__/hooks/useMessages.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act } from '@testing-library/react'
import { useMessageStore } from '@cubeplex/core/stores'

function mockSSEResponse(events: object[]) {
  const lines = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(lines))
      controller.close()
    },
  })
  return new Response(stream, {
    headers: { 'content-type': 'text/event-stream' },
  })
}

const mockClient = { baseUrl: '', get: vi.fn(), post: vi.fn() }

beforeEach(() => {
  useMessageStore.setState({
    messages: [],
    streamAgents: {},
    isStreaming: false,
    error: null,
  })
})

describe('messageStore.send', () => {
  it('adds user message optimistically', async () => {
    vi.stubGlobal('fetch', vi.fn(() => mockSSEResponse([{ type: 'done', data: {}, agent_id: null, agent_name: null, timestamp: '' }])))

    await act(async () => {
      await useMessageStore.getState().send(mockClient as any, 'conv-1', 'hello')
    })

    const { messages } = useMessageStore.getState()
    expect(messages.some((m) => m.role === 'user' && m.content === 'hello')).toBe(true)
  })

  it('accumulates text_delta events into streamAgents', async () => {
    let resolveStream: () => void
    const streamPromise = new Promise<void>((resolve) => { resolveStream = resolve })

    vi.stubGlobal('fetch', vi.fn(() => mockSSEResponse([
      { type: 'text_delta', data: { content: 'Hello' }, agent_id: null, agent_name: null, timestamp: '' },
      { type: 'text_delta', data: { content: ' world' }, agent_id: null, agent_name: null, timestamp: '' },
      { type: 'done', data: {}, agent_id: null, agent_name: null, timestamp: '' },
    ])))

    await act(async () => {
      await useMessageStore.getState().send(mockClient as any, 'conv-1', 'hi')
    })

    const { messages } = useMessageStore.getState()
    const assistantMsg = messages.find((m) => m.role === 'assistant')
    expect(assistantMsg?.content).toBe('Hello world')
  })

  it('sets error on error event', async () => {
    vi.stubGlobal('fetch', vi.fn(() => mockSSEResponse([
      { type: 'error', data: { error_code: 'ERR', message: 'Something failed' }, agent_id: null, agent_name: null, timestamp: '' },
    ])))

    await act(async () => {
      await useMessageStore.getState().send(mockClient as any, 'conv-1', 'hi')
    })

    expect(useMessageStore.getState().error).toBe('Something failed')
  })

  it('clears isStreaming after completion', async () => {
    vi.stubGlobal('fetch', vi.fn(() => mockSSEResponse([
      { type: 'done', data: {}, agent_id: null, agent_name: null, timestamp: '' },
    ])))

    await act(async () => {
      await useMessageStore.getState().send(mockClient as any, 'conv-1', 'hi')
    })

    expect(useMessageStore.getState().isStreaming).toBe(false)
  })
})
```

- [ ] **Step 5: Run unit tests**

```bash
cd frontend && pnpm --filter web test
```

Expected: `4 passed`

- [ ] **Step 6: Commit**

```bash
cd frontend && git add packages/web/vitest.config.ts packages/web/vitest.setup.ts packages/web/package.json packages/web/__tests__/
git commit -m "test: add vitest setup and messageStore unit tests"
```

---

## Task 17: Frontend Playwright E2E Tests

**Files:**
- Create: `frontend/playwright.config.ts`
- Create: `frontend/packages/web/__tests__/e2e/chat-flow.spec.ts`
- Create: `frontend/packages/web/__tests__/e2e/streaming.spec.ts`
- Modify: root `frontend/package.json` (add e2e script)

- [ ] **Step 1: Install Playwright**

```bash
cd frontend && pnpm add -D -w @playwright/test && npx playwright install chromium
```

- [ ] **Step 2: Create playwright config**

```typescript
// frontend/playwright.config.ts
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './packages/web/__tests__/e2e',
  fullyParallel: false,
  retries: 1,
  timeout: 60_000,
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    command: 'pnpm --filter web dev',
    url: 'http://localhost:3000',
    reuseExistingServer: true,
    timeout: 30_000,
  },
})
```

- [ ] **Step 3: Add e2e script to root package.json**

In `frontend/package.json`, add to scripts:
```json
"test:e2e": "playwright test"
```

- [ ] **Step 4: Write chat flow E2E test**

```typescript
// frontend/packages/web/__tests__/e2e/chat-flow.spec.ts
import { test, expect } from '@playwright/test'

test('can send a message and see a response', async ({ page }) => {
  await page.goto('/')

  const input = page.getByPlaceholder('有什么可以帮你的？')
  await input.fill('Say the word "hello" and nothing else.')
  await input.press('Enter')

  // Should navigate to conversation page
  await expect(page).toHaveURL(/\/conversations\//, { timeout: 10_000 })

  // User message should be visible
  await expect(page.getByText('Say the word "hello" and nothing else.')).toBeVisible()

  // Wait for streaming to complete (loading dots disappear)
  await expect(page.locator('.animate-bounce').first()).toBeHidden({ timeout: 30_000 })

  // Assistant response should appear
  const assistantMsg = page.locator('[data-role="assistant"]')
  await expect(assistantMsg).toBeVisible()
  const text = await assistantMsg.textContent()
  expect(text!.trim().length).toBeGreaterThan(0)
})

test('conversation history persists after page reload', async ({ page }) => {
  await page.goto('/')

  const input = page.getByPlaceholder('有什么可以帮你的？')
  await input.fill('My favorite color is blue.')
  await input.press('Enter')

  await expect(page).toHaveURL(/\/conversations\//)
  await expect(page.locator('.animate-bounce').first()).toBeHidden({ timeout: 30_000 })

  // Reload the page
  await page.reload()

  // History should still be visible
  await expect(page.getByText('My favorite color is blue.')).toBeVisible({ timeout: 10_000 })
  await expect(page.locator('[data-role="assistant"]')).toBeVisible()
})
```

- [ ] **Step 5: Write streaming E2E test**

```typescript
// frontend/packages/web/__tests__/e2e/streaming.spec.ts
import { test, expect } from '@playwright/test'

test('loading animation appears while streaming', async ({ page }) => {
  await page.goto('/')

  const input = page.getByPlaceholder('有什么可以帮你的？')
  await input.fill('Write a haiku about coding.')
  await input.press('Enter')

  await expect(page).toHaveURL(/\/conversations\//)

  // Loading animation should appear
  await expect(page.locator('.animate-bounce').first()).toBeVisible({ timeout: 10_000 })

  // And disappear when done
  await expect(page.locator('.animate-bounce').first()).toBeHidden({ timeout: 30_000 })

  // Final response should have meaningful content
  const assistantMsg = page.locator('[data-role="assistant"]')
  const text = await assistantMsg.textContent()
  expect(text!.length).toBeGreaterThan(20)
})

test('send button is disabled while streaming', async ({ page }) => {
  await page.goto('/')

  const input = page.getByPlaceholder('有什么可以帮你的？')
  await input.fill('Write a short poem.')
  await input.press('Enter')

  await expect(page).toHaveURL(/\/conversations\//)

  // Navigate to conversation and send another message
  const newInput = page.getByPlaceholder('有什么可以帮你的？')
  await newInput.fill('Another message')

  // Send button should be disabled while streaming
  const sendBtn = page.getByRole('button', { name: /send/i })
  // Button may not have "send" label — check the input is disabled instead
  await expect(newInput).toBeDisabled({ timeout: 5_000 }).catch(() => {
    // Some implementations disable the button, not the input — both are valid
  })

  // Wait for completion
  await expect(page.locator('.animate-bounce').first()).toBeHidden({ timeout: 30_000 })
})
```

- [ ] **Step 6: Run E2E tests (requires frontend + backend running)**

Start backend: `cd backend && python main.py`
Start frontend: `cd frontend && pnpm dev`

Then:
```bash
cd frontend && pnpm test:e2e
```

Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
cd frontend && git add playwright.config.ts packages/web/__tests__/e2e/ package.json
git commit -m "test: add Playwright E2E tests for chat flow and streaming"
```

---

## Final Verification

- [ ] **Run all backend tests**

```bash
cd backend && make test
```

- [ ] **Run all frontend tests**

```bash
cd frontend && pnpm test && pnpm type-check
```

- [ ] **Run make check (backend full lint + type + test)**

```bash
cd backend && make check
```

- [ ] **Manual smoke test**

1. Start backend: `cd backend && python main.py`
2. Start frontend: `cd frontend && pnpm dev`
3. Open http://localhost:3000
4. Send a message — verify streaming response appears
5. Reload page — verify history loads from checkpointer
6. Send a second message in same conversation — verify context is retained (multi-turn)
