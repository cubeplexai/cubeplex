# Reasoning Capability Standard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Cubeplex's `thinking` request control with a provider-neutral
`reasoning` control, with official API mappings owned by cubepi and custom
provider mappings validated through preview and lint.

**Architecture:** cubepi owns `ReasoningControl`, the capability DSL v2,
official profile registry, request rendering, preview, and lint. Cubeplex stores
profile/override configuration, passes `ReasoningControl` through runtime, and
surfaces preview/lint to provider admins.

**Tech Stack:** Python 3.13, Pydantic v2, FastAPI, SQLModel, Alembic,
Postgres, Redis, Next.js, React 19, TypeScript, Zustand, vitest, Playwright.

## Global Constraints

- Work in isolated worktrees; read `.worktree.env` first in Cubeplex worktrees.
- Use `docs/dev/plans/` and `docs/dev/specs/` for Cubeplex planning docs.
- Backend line length is 100 characters.
- Type annotations everywhere; backend mypy is strict.
- New migrations use `alembic revision --autogenerate -m "..."`.
- Do not hand-edit `pyproject.toml` or package files for dependency changes;
  use `uv add` or the established project command.
- This plan intentionally makes a clean cutover and does not preserve the
  legacy `thinking` request field.
- Stream output blocks named `thinking` remain unchanged; this plan changes
  request control, not assistant content block names.

---

## File Structure

### cubepi repository: `/home/chris/cubepi`

- Modify: `cubepi/providers/base.py`
  Defines `ReasoningControl`, updates `StreamOptions`, `Provider.generate`, and
  `BoundModel.generate`.
- Modify: `cubepi/providers/capability.py`
  Defines `ReasoningCapability`, profile merge helpers, preview, lint, and
  `apply_reasoning_control`.
- Create: `cubepi/providers/reasoning_profiles.py`
  Built-in profiles for official API shapes.
- Modify: `cubepi/providers/openai.py`
  Uses the OpenAI Chat profile and `apply_reasoning_control`.
- Modify: `cubepi/providers/openai_responses.py`
  Uses the OpenAI Responses profile and `apply_reasoning_control`.
- Modify: `cubepi/providers/anthropic.py`
  Uses Anthropic profiles and keeps `thinking.budget_tokens` max-token guard.
- Modify: `cubepi/providers/fallback.py`
  Renames `thinking` passthrough to `reasoning`.
- Modify: `cubepi/agent/agent.py`
  Replaces agent constructor `thinking` with `reasoning`.
- Modify: `cubepi/providers/__init__.py`, `cubepi/__init__.py`
  Exports new public types and helpers.
- Test: `tests/providers/test_reasoning_capability.py`
  Pure capability rendering, preview, lint, and profile merge tests.
- Test: `tests/providers/test_openai_capability.py`
  OpenAI Chat provider payload tests.
- Test: `tests/providers/test_openai_responses_capability.py`
  OpenAI Responses provider payload tests.
- Test: `tests/providers/test_anthropic_capability.py`
  Anthropic adaptive and legacy-budget payload tests.
- Test: `tests/providers/test_base.py`
  `StreamOptions` and `generate` API tests.

### cubeplex repository: current worktree

- Modify: `backend/pyproject.toml`, `backend/uv.lock`
  Pin the cubepi commit that includes the new reasoning API.
- Modify: `backend/cubeplex/llm/config.py`
  Adds provider `capability_profile` and `capability_overrides`.
- Modify: `backend/cubeplex/llm/catalog/data/capabilities.yaml`
  Converts old capability blocks to DSL v2.
- Modify: `backend/cubeplex/llm/catalog/data/vendors.yaml`
  References profile ids and provider overrides.
- Modify: `backend/cubeplex/llm/catalog/loader.py`,
  `backend/cubeplex/llm/catalog/types.py`
  Loads and resolves profile-plus-overrides.
- Modify: `backend/cubeplex/seeders/provider_seeder.py`,
  `backend/cubeplex/llm/snapshot.py`, `backend/cubeplex/llm/builder.py`
  Persists and builds resolved capability descriptors.
- Modify: `backend/cubeplex/models/conversation.py`
  Replaces `thinking` column with `reasoning` JSON.
- Add: the Alembic-generated migration file for `conversation reasoning` under
  `backend/alembic/versions/`.
- Modify: `backend/cubeplex/api/routes/v1/conversations.py`,
  `backend/cubeplex/repositories/conversation.py`,
  `backend/cubeplex/api/serializers.py`
  Replaces request and persistence flow with `reasoning`.
- Modify: `backend/cubeplex/streams/run_manager.py`,
  `backend/cubeplex/agents/graph.py`,
  `backend/cubeplex/services/conversation_title.py`,
  `backend/cubeplex/services/provider_probe.py`,
  `backend/cubeplex/services/provider_service.py`
  Passes `ReasoningControl`, previews and lints custom capabilities, probes the
  resolved mapping.
- Modify: `backend/cubeplex/api/schemas/provider.py`,
  `backend/cubeplex/api/routes/v1/admin_providers.py`
  Adds preview/lint schemas and route.
- Modify: `frontend/packages/core/src/api/stream.ts`,
  `frontend/packages/core/src/types/events.ts`,
  `frontend/packages/core/src/types/conversation.ts`
  Replaces send-message and conversation types.
- Modify: `frontend/packages/web/lib/types/presets.ts`,
  `frontend/packages/web/lib/stores/preset-selection.ts`,
  `frontend/packages/web/components/chat/EffortSlider.tsx`,
  `frontend/packages/web/components/chat/ModelPicker.tsx`,
  `frontend/packages/web/components/layout/InputBar.tsx`
  Changes composer state from `thinking` to `reasoning`.
- Modify: `frontend/packages/web/components/admin/models/wizard/CapabilityEditor.tsx`,
  `frontend/packages/web/components/admin/models/ProviderConfigForm.tsx`
  Adds profile/override editing and preview/lint display.
- Modify: `docs/site/docs/*`
  Updates provider setup and model reasoning docs.

---

### Task 1: cubepi Reasoning Types and Capability DSL

**Files:**
- Modify: `/home/chris/cubepi/cubepi/providers/base.py`
- Modify: `/home/chris/cubepi/cubepi/providers/capability.py`
- Create: `/home/chris/cubepi/cubepi/providers/reasoning_profiles.py`
- Modify: `/home/chris/cubepi/cubepi/providers/__init__.py`
- Modify: `/home/chris/cubepi/cubepi/__init__.py`
- Test: `/home/chris/cubepi/tests/providers/test_reasoning_capability.py`
- Test: `/home/chris/cubepi/tests/providers/test_base.py`

**Interfaces:**
- Produces:
  - `ReasoningControl(mode: ReasoningMode, effort: ReasoningEffort,
    summary: ReasoningSummary)`
  - `ReasoningCapability`
  - `CapabilityDescriptor(reasoning: ReasoningCapability, ...)`
  - `apply_reasoning_control(payload, capability, reasoning, *, model)`
  - `preview_payload(api, model, capability, reasoning, base_payload=None)`
  - `lint_capability(api, capability, model=None)`
  - `get_capability_profile(profile_id)`
  - `merge_capability_profile(profile, override)`
- Consumes: existing `Model`, `StreamOptions`, `CapabilityDescriptor`,
  `merge_capability_payload`, dotted-path write behavior.

- [ ] **Step 1: Create failing tests for the new reasoning primitives**

Add `/home/chris/cubepi/tests/providers/test_reasoning_capability.py`:

```python
from __future__ import annotations

from cubepi.providers.base import Model, ReasoningControl, StreamOptions
from cubepi.providers.capability import (
    CapabilityDescriptor,
    ReasoningCapability,
    apply_reasoning_control,
    lint_capability,
    preview_payload,
)
from cubepi.providers.reasoning_profiles import get_capability_profile


def test_stream_options_default_reasoning_is_off_medium_none() -> None:
    opts = StreamOptions()

    assert opts.reasoning == ReasoningControl(
        mode="off",
        effort="medium",
        summary="none",
    )


def test_apply_reasoning_writes_effort_when_off_for_chat_profile() -> None:
    cap = CapabilityDescriptor(
        reasoning=ReasoningCapability(
            effort_path="reasoning_effort",
            effort_values={"minimal": "minimal", "medium": "medium"},
            apply_effort_when_off=True,
        )
    )
    payload: dict[str, object] = {}

    apply_reasoning_control(
        payload,
        cap,
        ReasoningControl(mode="off", effort="minimal", summary="none"),
        model=Model(id="m", reasoning=True),
    )

    assert payload == {"reasoning_effort": "minimal"}


def test_apply_reasoning_writes_nested_summary_and_include_payload() -> None:
    cap = CapabilityDescriptor(
        reasoning=ReasoningCapability(
            mode_payloads={"on": {"reasoning": {}}},
            effort_path="reasoning.effort",
            effort_values={"high": "high"},
            summary_path="reasoning.summary",
            summary_values={"auto": "auto"},
            include_payloads={"auto": {"include": ["reasoning.encrypted_content"]}},
        )
    )
    payload: dict[str, object] = {}

    apply_reasoning_control(
        payload,
        cap,
        ReasoningControl(mode="on", effort="high", summary="auto"),
        model=Model(id="gpt", reasoning=True),
    )

    assert payload == {
        "reasoning": {"effort": "high", "summary": "auto"},
        "include": ["reasoning.encrypted_content"],
    }


def test_preview_payload_returns_reasoning_diff() -> None:
    cap = get_capability_profile("openai.chat_completions")

    preview = preview_payload(
        api="openai-completions",
        model=Model(id="m", reasoning=True),
        capability=cap,
        reasoning=ReasoningControl(mode="on", effort="high", summary="none"),
    )

    assert preview.payload_diff == {"reasoning_effort": "high"}
    assert preview.warnings == []


def test_lint_warns_for_top_level_thinking_on_openai_chat() -> None:
    cap = CapabilityDescriptor(
        reasoning=ReasoningCapability(mode_payloads={"on": {"thinking": {"type": "enabled"}}})
    )

    warnings = lint_capability("openai-completions", cap, Model(id="m", reasoning=True))

    assert [w.code for w in warnings] == ["openai_top_level_thinking"]
```

Update `/home/chris/cubepi/tests/providers/test_base.py` with:

```python
from cubepi.providers.base import ReasoningControl


def test_generate_updates_reasoning_options() -> None:
    provider = _RecordingProvider()
    model = provider.model("m")

    result = await provider.generate(
        model,
        messages=[],
        reasoning=ReasoningControl(mode="on", effort="high", summary="none"),
    )

    assert result.stop_reason == "stop"
    assert provider.seen_options is not None
    assert provider.seen_options.reasoning.effort == "high"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cd /home/chris/cubepi
uv run pytest tests/providers/test_reasoning_capability.py tests/providers/test_base.py -q
```

Expected: fail because `ReasoningControl`, `ReasoningCapability`,
`preview_payload`, and `StreamOptions.reasoning` do not exist yet.

- [ ] **Step 3: Implement the new public types**

In `/home/chris/cubepi/cubepi/providers/base.py`, replace `ThinkingLevel` and
`ThinkingBudgets` public request-control usage with:

```python
ReasoningMode = Literal["off", "auto", "on"]
ReasoningEffort = Literal["minimal", "low", "medium", "high", "max"]
ReasoningSummary = Literal["none", "auto", "detailed", "summarized"]


class ReasoningControl(BaseModel):
    mode: ReasoningMode = "off"
    effort: ReasoningEffort = "medium"
    summary: ReasoningSummary = "none"
```

Update `StreamOptions`:

```python
class StreamOptions(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    reasoning: ReasoningControl = Field(default_factory=ReasoningControl)
    signal: asyncio.Event | None = None
    on_payload: OnPayloadCallback | None = None
    on_response: OnResponseCallback | None = None
```

Update `BaseProvider.generate` and the provider protocol signature:

```python
async def generate(
    self,
    model: Model,
    messages: list[Message],
    *,
    system_prompt: str = "",
    tools: list[ToolDefinition] | None = None,
    tool_choice: ToolChoice | None = None,
    options: StreamOptions | None = None,
    max_output_tokens: int | None = None,
    temperature: float | None = None,
    reasoning: ReasoningControl | None = None,
) -> AssistantMessage:
    ...
    option_updates: dict[str, ReasoningControl] = {}
    if reasoning is not None:
        option_updates["reasoning"] = reasoning
```

- [ ] **Step 4: Implement capability DSL v2**

In `/home/chris/cubepi/cubepi/providers/capability.py`, add:

```python
class ReasoningCapability(BaseModel):
    mode_payloads: dict[ReasoningMode, dict[str, Any]] = Field(default_factory=dict)
    effort_path: str | None = None
    effort_values: dict[ReasoningEffort, Any] = Field(default_factory=dict)
    summary_path: str | None = None
    summary_values: dict[ReasoningSummary, Any] = Field(default_factory=dict)
    include_payloads: dict[ReasoningSummary, dict[str, Any]] = Field(default_factory=dict)
    apply_effort_when_off: bool = False
    unsupported_mode_policy: Literal["omit", "coerce", "error"] = "omit"


class CapabilityWarning(BaseModel):
    code: str
    message: str
    path: str | None = None


class PayloadPreview(BaseModel):
    payload_diff: dict[str, Any]
    warnings: list[CapabilityWarning] = Field(default_factory=list)
```

Replace `CapabilityDescriptor` reasoning fields with:

```python
class CapabilityDescriptor(BaseModel):
    reasoning: ReasoningCapability = Field(default_factory=ReasoningCapability)
    temperature: TemperatureSpec = Field(default_factory=TemperatureSpec)
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    supports_tools: bool = True
    supports_images: bool = False
    supports_streaming: bool = True
```

Add renderer helpers:

```python
def apply_reasoning_control(
    payload: dict[str, Any],
    capability: CapabilityDescriptor,
    reasoning: ReasoningControl,
    *,
    model: Model,
) -> None:
    cap = capability.reasoning
    patch = cap.mode_payloads.get(reasoning.mode)
    if patch:
        merge_capability_payload(payload, patch)
    should_write_effort = reasoning.mode != "off" or cap.apply_effort_when_off
    if should_write_effort and cap.effort_path:
        value = cap.effort_values.get(reasoning.effort)
        if value is not None:
            _write_dotted_path(payload, cap.effort_path, value)
    if reasoning.summary != "none":
        include_patch = cap.include_payloads.get(reasoning.summary)
        if include_patch:
            merge_capability_payload(payload, include_patch)
        if cap.summary_path:
            value = cap.summary_values.get(reasoning.summary)
            if value is not None:
                _write_dotted_path(payload, cap.summary_path, value)
```

Add `preview_payload` and `lint_capability` in the same module.

- [ ] **Step 5: Add built-in profiles**

Create `/home/chris/cubepi/cubepi/providers/reasoning_profiles.py`:

```python
from __future__ import annotations

from copy import deepcopy

from cubepi.providers.capability import CapabilityDescriptor, ReasoningCapability


_PROFILES: dict[str, CapabilityDescriptor] = {
    "openai.chat_completions": CapabilityDescriptor(
        reasoning=ReasoningCapability(
            effort_path="reasoning_effort",
            effort_values={
                "minimal": "minimal",
                "low": "low",
                "medium": "medium",
                "high": "high",
                "max": "high",
            },
            apply_effort_when_off=True,
        ),
        max_tokens_field="max_completion_tokens",
    ),
    "openai.responses": CapabilityDescriptor(
        reasoning=ReasoningCapability(
            mode_payloads={"auto": {"reasoning": {}}, "on": {"reasoning": {}}},
            effort_path="reasoning.effort",
            effort_values={
                "minimal": "minimal",
                "low": "low",
                "medium": "medium",
                "high": "high",
                "max": "xhigh",
            },
            summary_path="reasoning.summary",
            summary_values={"auto": "auto", "detailed": "detailed"},
            include_payloads={
                "auto": {"include": ["reasoning.encrypted_content"]},
                "detailed": {"include": ["reasoning.encrypted_content"]},
            },
        )
    ),
}


def get_capability_profile(profile_id: str) -> CapabilityDescriptor:
    return deepcopy(_PROFILES[profile_id])
```

Add Anthropic profiles in the same `_PROFILES` dict.

- [ ] **Step 6: Export public names**

Update `/home/chris/cubepi/cubepi/providers/__init__.py` and
`/home/chris/cubepi/cubepi/__init__.py` to export:

```python
ReasoningControl
ReasoningEffort
ReasoningMode
ReasoningSummary
ReasoningCapability
CapabilityWarning
PayloadPreview
apply_reasoning_control
lint_capability
preview_payload
get_capability_profile
```

- [ ] **Step 7: Run tests for Task 1**

Run:

```bash
cd /home/chris/cubepi
uv run pytest tests/providers/test_reasoning_capability.py tests/providers/test_base.py -q
```

Expected: pass.

- [ ] **Step 8: Commit Task 1**

```bash
cd /home/chris/cubepi
git add cubepi/providers/base.py cubepi/providers/capability.py \
  cubepi/providers/reasoning_profiles.py cubepi/providers/__init__.py \
  cubepi/__init__.py tests/providers/test_reasoning_capability.py \
  tests/providers/test_base.py
git commit -m "feat: add reasoning capability primitives"
```

---

### Task 2: cubepi Provider Rendering

**Files:**
- Modify: `/home/chris/cubepi/cubepi/providers/openai.py`
- Modify: `/home/chris/cubepi/cubepi/providers/openai_responses.py`
- Modify: `/home/chris/cubepi/cubepi/providers/anthropic.py`
- Modify: `/home/chris/cubepi/cubepi/providers/fallback.py`
- Modify: `/home/chris/cubepi/cubepi/agent/agent.py`
- Test: `/home/chris/cubepi/tests/providers/test_openai_capability.py`
- Test: `/home/chris/cubepi/tests/providers/test_openai_responses_capability.py`
- Test: `/home/chris/cubepi/tests/providers/test_anthropic_capability.py`
- Test: `/home/chris/cubepi/tests/providers/test_bound_model_calls.py`

**Interfaces:**
- Consumes: `ReasoningControl`, `apply_reasoning_control`,
  `get_capability_profile`.
- Produces: provider payloads using official profiles by default.

- [ ] **Step 1: Update OpenAI Chat tests first**

In `/home/chris/cubepi/tests/providers/test_openai_capability.py`, add:

```python
async def test_openai_chat_default_profile_writes_reasoning_effort() -> None:
    payload = await _capture_openai(
        OpenAIProvider(api_key="x", base_url="http://example"),
        model_reasoning=True,
        reasoning=ReasoningControl(mode="on", effort="high", summary="none"),
    )

    assert payload["reasoning_effort"] == "high"
    assert "thinking" not in payload


async def test_openai_chat_off_writes_minimal_effort() -> None:
    payload = await _capture_openai(
        OpenAIProvider(api_key="x", base_url="http://example"),
        model_reasoning=True,
        reasoning=ReasoningControl(mode="off", effort="minimal", summary="none"),
    )

    assert payload["reasoning_effort"] == "minimal"
```

Adjust the local `_capture_openai` helper to accept `reasoning:
ReasoningControl`.

- [ ] **Step 2: Update Responses tests first**

In `/home/chris/cubepi/tests/providers/test_openai_responses_capability.py`,
add:

```python
async def test_responses_default_profile_writes_reasoning_object() -> None:
    payload = await _capture_responses(
        OpenAIResponsesProvider(api_key="x"),
        model_reasoning=True,
        reasoning=ReasoningControl(mode="on", effort="high", summary="auto"),
    )

    assert payload["reasoning"] == {"effort": "high", "summary": "auto"}
    assert payload["include"] == ["reasoning.encrypted_content"]
```

- [ ] **Step 3: Update Anthropic tests first**

In `/home/chris/cubepi/tests/providers/test_anthropic_capability.py`, add:

```python
async def test_anthropic_legacy_budget_profile_maps_effort_to_budget() -> None:
    provider = AnthropicProvider(api_key="x")
    payload = await _capture_anthropic(
        provider,
        StreamOptions(reasoning=ReasoningControl(mode="on", effort="medium")),
    )

    assert payload["thinking"]["type"] == "enabled"
    assert payload["thinking"]["budget_tokens"] == 8192
    assert payload["max_tokens"] > payload["thinking"]["budget_tokens"]
```

- [ ] **Step 4: Run provider tests to verify failure**

Run:

```bash
cd /home/chris/cubepi
uv run pytest tests/providers/test_openai_capability.py \
  tests/providers/test_openai_responses_capability.py \
  tests/providers/test_anthropic_capability.py -q
```

Expected: fail until providers use `StreamOptions.reasoning`.

- [ ] **Step 5: Update OpenAIProvider rendering**

In `/home/chris/cubepi/cubepi/providers/openai.py`:

```python
from cubepi.providers.capability import apply_reasoning_control
from cubepi.providers.reasoning_profiles import get_capability_profile
```

Default capability:

```python
self._capability: CapabilityDescriptor = capability or get_capability_profile(
    "openai.chat_completions"
)
```

Replace `opts.thinking` branching with:

```python
if self._cap_active or model.reasoning:
    kwargs.setdefault("temperature", model.temperature)
    if cap.max_tokens_field not in kwargs:
        kwargs.setdefault("max_tokens", model.max_tokens)
    apply_temperature(kwargs, cap.temperature)
    if cap.max_tokens_field != "max_tokens" and "max_tokens" in kwargs:
        kwargs[cap.max_tokens_field] = kwargs.pop("max_tokens")
    if model.reasoning:
        apply_reasoning_control(kwargs, cap, opts.reasoning, model=model)
```

- [ ] **Step 6: Update OpenAIResponsesProvider rendering**

In `/home/chris/cubepi/cubepi/providers/openai_responses.py`, default to
`openai.responses` and replace `_THINKING_TO_EFFORT` with
`apply_reasoning_control`.

```python
if model.reasoning:
    apply_reasoning_control(kwargs, cap, opts.reasoning, model=model)
```

- [ ] **Step 7: Update AnthropicProvider rendering**

In `/home/chris/cubepi/cubepi/providers/anthropic.py`, default to
`anthropic.messages.legacy_budget`. Replace `thinking = clamp_thinking_level(...)`
with:

```python
reasoning = clamp_reasoning_control(model, opts.reasoning)
```

After `apply_reasoning_control`, preserve the existing max-token guard by
reading:

```python
thinking_block = kwargs.get("thinking")
budget = 0
if isinstance(thinking_block, dict):
    budget = thinking_block.get("budget_tokens", 0) or 0
```

Keep the rule that Anthropic temperature is omitted when a thinking block is
enabled.

- [ ] **Step 8: Update Agent and fallback passthrough**

In `/home/chris/cubepi/cubepi/agent/agent.py`, replace constructor field:

```python
reasoning: ReasoningControl = Field(default_factory=ReasoningControl)
```

Update `_build_stream_options`:

```python
return StreamOptions(reasoning=self.config.reasoning, signal=signal)
```

In `/home/chris/cubepi/cubepi/providers/fallback.py`, replace
`thinking` parameters with `reasoning`.

- [ ] **Step 9: Run cubepi provider tests**

Run:

```bash
cd /home/chris/cubepi
uv run pytest tests/providers/test_openai_capability.py \
  tests/providers/test_openai_responses_capability.py \
  tests/providers/test_anthropic_capability.py \
  tests/providers/test_bound_model_calls.py -q
```

Expected: pass.

- [ ] **Step 10: Run cubepi full test suite**

Run:

```bash
cd /home/chris/cubepi
uv run pytest -q
```

Expected: pass.

- [ ] **Step 11: Commit Task 2**

```bash
cd /home/chris/cubepi
git add cubepi tests
git commit -m "feat: render reasoning controls in providers"
```

---

### Task 3: Cubeplex Dependency and Backend Reasoning Types

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Modify: `backend/cubeplex/api/routes/v1/conversations.py`
- Modify: `backend/cubeplex/models/conversation.py`
- Modify: `backend/cubeplex/repositories/conversation.py`
- Modify: `backend/cubeplex/api/serializers.py`
- Add: the Alembic-generated migration file for `conversation reasoning` under
  `backend/alembic/versions/`.
- Test: `backend/tests/unit/test_send_message_schema.py`
- Test: `backend/tests/e2e/test_conversations.py`
- Test: `backend/tests/e2e/test_preset_switching_e2e.py`

**Interfaces:**
- Consumes: cubepi `ReasoningControl`.
- Produces: Cubeplex API request/response field `reasoning`.

- [ ] **Step 1: Update cubepi dependency**

After Task 2 is merged or committed in `/home/chris/cubepi`, update Cubeplex:

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-07-03-reasoning-capability-standard/backend
NEW_CUBEPI_COMMIT="$(git -C /home/chris/cubepi rev-parse HEAD)"
CUBEPI_SPEC="cubepi[mcp,postgres,trace-cli,tracing,tracing-otlp]"
CUBEPI_URL="git+https://github.com/cubeplexai/cubepi.git@${NEW_CUBEPI_COMMIT}"
uv add "${CUBEPI_SPEC} @ ${CUBEPI_URL}"
```

Expected: `backend/pyproject.toml` and `backend/uv.lock` point at the new
cubepi commit.

- [ ] **Step 2: Write schema tests first**

Replace `backend/tests/unit/test_send_message_schema.py` with:

```python
import pytest
from pydantic import ValidationError

from cubeplex.api.routes.v1.conversations import SendMessageRequest


def test_request_accepts_model_key_and_reasoning() -> None:
    body = SendMessageRequest.model_validate(
        {
            "content": "hi",
            "model_key": "pro",
            "reasoning": {"mode": "on", "effort": "high", "summary": "none"},
        }
    )

    assert body.model_key == "pro"
    assert body.reasoning.mode == "on"
    assert body.reasoning.effort == "high"


def test_reasoning_defaults_to_off_medium_none() -> None:
    body = SendMessageRequest.model_validate({"content": "hi"})

    assert body.reasoning.model_dump() == {
        "mode": "off",
        "effort": "medium",
        "summary": "none",
    }


def test_request_rejects_legacy_thinking() -> None:
    with pytest.raises(ValidationError):
        SendMessageRequest.model_validate({"content": "hi", "thinking": "high"})
```

- [ ] **Step 3: Run schema test to verify failure**

Run:

```bash
cd backend
uv run pytest tests/unit/test_send_message_schema.py --no-cov -q
```

Expected: fail until `SendMessageRequest` uses `ReasoningControl` and forbids
extra fields.

- [ ] **Step 4: Update API request model**

In `backend/cubeplex/api/routes/v1/conversations.py`:

```python
from cubepi.providers.base import ReasoningControl
```

Replace `thinking`:

```python
class SendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = ""
    attachments: list[str] = []
    model_key: str | None = None
    reasoning: ReasoningControl = Field(default_factory=ReasoningControl)
```

Pass `request_obj.reasoning` into repository and run manager.

- [ ] **Step 5: Change conversation model**

In `backend/cubeplex/models/conversation.py`, replace `thinking` with:

```python
reasoning: dict[str, Any] = Field(
    default_factory=lambda: {"mode": "off", "effort": "medium", "summary": "none"},
    sa_column=Column(JSON),
)
```

- [ ] **Step 6: Update repository and serializers**

In `backend/cubeplex/repositories/conversation.py`, change
`model_setting: tuple[str | None, str] | None` to:

```python
model_setting: tuple[str | None, dict[str, Any]] | None
```

Assign:

```python
conv.model_key, conv.reasoning = model_setting
```

In `backend/cubeplex/api/serializers.py`, return:

```python
"reasoning": c.reasoning,
```

- [ ] **Step 7: Generate migration**

Run:

```bash
cd backend
uv run alembic revision --autogenerate -m "conversation reasoning"
```

Edit only if autogenerate omits a safe server default. The migration should add
`conversations.reasoning` JSON with the default object and drop
`conversations.thinking`.

- [ ] **Step 8: Run backend focused tests**

Run:

```bash
cd backend
uv run pytest tests/unit/test_send_message_schema.py \
  tests/e2e/test_conversations.py \
  tests/e2e/test_preset_switching_e2e.py --no-cov -q
```

Expected: pass.

- [ ] **Step 9: Commit Task 3**

```bash
git add backend/pyproject.toml backend/uv.lock backend/cubeplex \
  backend/alembic/versions backend/tests/unit/test_send_message_schema.py \
  backend/tests/e2e/test_conversations.py \
  backend/tests/e2e/test_preset_switching_e2e.py
git commit -m "feat: replace conversation thinking with reasoning"
```

---

### Task 4: Cubeplex Runtime and Provider Probe Cutover

**Files:**
- Modify: `backend/cubeplex/llm/builder.py`
- Modify: `backend/cubeplex/agents/graph.py`
- Modify: `backend/cubeplex/streams/run_manager.py`
- Modify: `backend/cubeplex/services/conversation_title.py`
- Modify: `backend/cubeplex/services/provider_probe.py`
- Modify: `backend/cubeplex/services/provider_service.py`
- Test: `backend/tests/unit/test_provider_probe.py`
- Test: `backend/tests/unit/test_run_manager_build_agent.py`
- Test: `backend/tests/unit/test_conversation_title_pi.py`

**Interfaces:**
- Consumes: `ReasoningControl`, `StreamOptions(reasoning=...)`.
- Produces: runtime calls no longer pass `thinking`.

- [ ] **Step 1: Update run-manager tests first**

In `backend/tests/unit/test_run_manager_build_agent.py`, assert captured agent
kwargs include:

```python
"reasoning": ReasoningControl(mode="on", effort="high", summary="none")
```

Do not assert `thinking`.

- [ ] **Step 2: Update provider probe tests first**

In `backend/tests/unit/test_provider_probe.py`, replace fake capture:

```python
self.calls.append({"reasoning": options.reasoning.model_dump()})
```

Assert:

```python
assert provider.calls[0]["reasoning"]["mode"] == "off"
assert provider.calls[1]["reasoning"]["mode"] == "on"
assert provider.calls[1]["reasoning"]["effort"] == "medium"
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
cd backend
uv run pytest tests/unit/test_provider_probe.py \
  tests/unit/test_run_manager_build_agent.py \
  tests/unit/test_conversation_title_pi.py --no-cov -q
```

Expected: fail until runtime signatures are updated.

- [ ] **Step 4: Update builder signatures**

In `backend/cubeplex/llm/builder.py`, replace `thinking` parameters with:

```python
reasoning: ReasoningControl | None = None
```

The builder does not need to bind reasoning to `BoundModel`; reasoning belongs
in per-call `StreamOptions`.

- [ ] **Step 5: Update agent factory**

In `backend/cubeplex/agents/graph.py`:

```python
def create_cubeplex_agent(..., reasoning: ReasoningControl | None = None, ...) -> Agent[Any]:
    return Agent(..., reasoning=reasoning or ReasoningControl(), ...)
```

- [ ] **Step 6: Update run manager**

In `backend/cubeplex/streams/run_manager.py`, replace every run-control
signature:

```python
reasoning: ReasoningControl | None = None
```

When passing to cubepi:

```python
reasoning=reasoning or ReasoningControl()
```

- [ ] **Step 7: Update title generation**

In `backend/cubeplex/services/conversation_title.py`, replace title calls with:

```python
reasoning=ReasoningControl(mode="off", effort="minimal", summary="none")
```

and:

```python
options=StreamOptions(
    reasoning=ReasoningControl(mode="off", effort="minimal", summary="none")
)
```

- [ ] **Step 8: Update provider probe matrix**

In `backend/cubeplex/services/provider_probe.py`, replace `_drain_stream`
parameter `thinking` with `reasoning`. Use:

```python
REASONING_OFF = ReasoningControl(mode="off", effort="minimal", summary="none")
REASONING_MEDIUM = ReasoningControl(mode="on", effort="medium", summary="none")
REASONING_MAX = ReasoningControl(mode="on", effort="max", summary="none")
```

Probe `off` and `medium` as blocking for reasoning-capable models. Treat `max`
as advisory.

- [ ] **Step 9: Run runtime tests**

Run:

```bash
cd backend
uv run pytest tests/unit/test_provider_probe.py \
  tests/unit/test_run_manager_build_agent.py \
  tests/unit/test_conversation_title_pi.py --no-cov -q
```

Expected: pass.

- [ ] **Step 10: Commit Task 4**

```bash
git add backend/cubeplex/llm/builder.py backend/cubeplex/agents/graph.py \
  backend/cubeplex/streams/run_manager.py \
  backend/cubeplex/services/conversation_title.py \
  backend/cubeplex/services/provider_probe.py \
  backend/cubeplex/services/provider_service.py backend/tests/unit
git commit -m "feat: pass reasoning through cubeplex runtime"
```

---

### Task 5: Provider Capability Profiles in Cubeplex

**Files:**
- Modify: `backend/cubeplex/llm/config.py`
- Modify: `backend/cubeplex/models/provider.py`
- Modify: `backend/cubeplex/api/schemas/provider.py`
- Modify: `backend/cubeplex/llm/catalog/types.py`
- Modify: `backend/cubeplex/llm/catalog/loader.py`
- Modify: `backend/cubeplex/llm/catalog/data/capabilities.yaml`
- Modify: `backend/cubeplex/llm/catalog/data/vendors.yaml`
- Modify: `backend/cubeplex/seeders/provider_seeder.py`
- Modify: `backend/cubeplex/llm/snapshot.py`
- Modify: `backend/cubeplex/llm/readiness.py`
- Test: `backend/tests/test_provider_capability_factory.py`
- Test: `backend/tests/unit/llm/catalog/test_loader.py`
- Test: `backend/tests/e2e/test_admin_providers_crud.py`

**Interfaces:**
- Consumes: cubepi `CapabilityDescriptor` v2 and profile registry.
- Produces: provider config supports `capability_profile` and
  `capability_overrides`.

- [ ] **Step 1: Write provider capability tests first**

Update `backend/tests/test_provider_capability_factory.py`:

```python
def test_provider_profile_and_overrides_resolve_to_descriptor() -> None:
    cfg = _bare_provider_config(
        "openai-completions",
        capability_profile="openai.chat_completions",
        capability_overrides={
            "reasoning": {
                "effort_path": "reasoning_effort",
                "effort_values": {"minimal": "minimal", "medium": "medium"},
                "apply_effort_when_off": True,
            }
        },
    )

    provider = _build(cfg)

    assert provider._capability.reasoning.effort_path == "reasoning_effort"
    assert provider._capability.reasoning.apply_effort_when_off is True
```

- [ ] **Step 2: Run provider capability test to verify failure**

Run:

```bash
cd backend
uv run pytest tests/test_provider_capability_factory.py --no-cov -q
```

Expected: fail until config fields and resolver exist.

- [ ] **Step 3: Add provider config fields**

In `backend/cubeplex/llm/config.py`:

```python
capability_profile: str | None = None
capability_overrides: dict[str, Any] = Field(default_factory=dict)
```

Keep `capability` for resolved snapshots until subsequent tasks remove old catalog
fixtures.

- [ ] **Step 4: Add provider model fields**

In `backend/cubeplex/models/provider.py`:

```python
capability_profile: str | None = Field(default=None, max_length=128)
capability_overrides: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
```

Generate migration with:

```bash
cd backend
uv run alembic revision --autogenerate -m "provider capability profile"
```

- [ ] **Step 5: Resolve profile plus overrides**

In `backend/cubeplex/llm/builder.py`, before constructing provider:

```python
capability = resolve_provider_capability(cfg)
```

Implement `resolve_provider_capability` in `backend/cubeplex/llm/config.py`:

```python
def resolve_provider_capability(cfg: ProviderConfig) -> CapabilityDescriptor | None:
    if cfg.capability:
        return CapabilityDescriptor.model_validate(cfg.capability)
    if cfg.capability_profile:
        base = get_capability_profile(cfg.capability_profile)
        return merge_capability_profile(base, cfg.capability_overrides)
    return None
```

- [ ] **Step 6: Convert catalog data**

In `backend/cubeplex/llm/catalog/data/capabilities.yaml`, replace old
`reasoning_off_payload` entries with DSL v2 fields under `reasoning`.

In `backend/cubeplex/llm/catalog/data/vendors.yaml`, store:

```yaml
capability_profile: openai.chat_completions
capability_overrides:
  reasoning:
    effort_path: extra_body.enable_thinking
```

Use provider-specific override values for Qwen, Volcengine, Zhipu, LiteLLM,
and vLLM examples.

- [ ] **Step 7: Update schemas and seeders**

In `backend/cubeplex/api/schemas/provider.py`, add profile fields to
`ProviderCreate`, `ProviderUpdate`, `ProviderLivenessRequest`, and
`ProviderOut`.

In `backend/cubeplex/seeders/provider_seeder.py`, persist `capability_profile`
and `capability_overrides`.

- [ ] **Step 8: Run provider tests**

Run:

```bash
cd backend
uv run pytest tests/test_provider_capability_factory.py \
  tests/e2e/test_admin_providers_crud.py --no-cov -q
```

Expected: pass.

- [ ] **Step 9: Commit Task 5**

```bash
git add backend/cubeplex/llm backend/cubeplex/models/provider.py \
  backend/cubeplex/api/schemas/provider.py backend/cubeplex/seeders \
  backend/alembic/versions backend/tests/test_provider_capability_factory.py \
  backend/tests/e2e/test_admin_providers_crud.py
git commit -m "feat: add provider capability profiles"
```

---

### Task 6: Preview and Lint API

**Files:**
- Modify: `backend/cubeplex/api/schemas/provider.py`
- Modify: `backend/cubeplex/services/provider_service.py`
- Modify: `backend/cubeplex/api/routes/v1/admin_providers.py`
- Modify: `backend/cubeplex/services/provider_probe.py`
- Test: `backend/tests/unit/test_provider_probe.py`
- Test: `backend/tests/e2e/test_admin_providers_crud.py`

**Interfaces:**
- Consumes: cubepi `preview_payload` and `lint_capability`.
- Produces: admin endpoint for capability preview/lint.

- [ ] **Step 1: Write API e2e test first**

Add to `backend/tests/e2e/test_admin_providers_crud.py`:

```python
async def test_provider_capability_preview_warns_on_top_level_thinking(admin_client):
    resp = await admin_client.post(
        "/api/v1/admin/providers/capability-preview",
        json={
            "api": "openai-completions",
            "model_id": "glm-5.2",
            "reasoning": {"mode": "on", "effort": "medium", "summary": "none"},
            "capability": {
                "reasoning": {
                    "mode_payloads": {
                        "on": {"thinking": {"type": "enabled"}}
                    }
                }
            },
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["warnings"][0]["code"] == "openai_top_level_thinking"
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
cd backend
TEST_NAME="test_provider_capability_preview_warns_on_top_level_thinking"
uv run pytest \
  "tests/e2e/test_admin_providers_crud.py::${TEST_NAME}" \
  --no-cov -q
```

Expected: fail with 404 until the route exists.

- [ ] **Step 3: Add schemas**

In `backend/cubeplex/api/schemas/provider.py`:

```python
class CapabilityPreviewRequest(BaseModel):
    api: WireApi = "openai-completions"
    model_id: str
    reasoning: ReasoningControl = Field(default_factory=ReasoningControl)
    capability: dict[str, Any] = Field(default_factory=dict)


class CapabilityPreviewOut(BaseModel):
    payload_diff: dict[str, Any]
    warnings: list[dict[str, Any]] = Field(default_factory=list)
```

- [ ] **Step 4: Add service method**

In `backend/cubeplex/services/provider_service.py`:

```python
def preview_capability(self, req: CapabilityPreviewRequest) -> CapabilityPreviewOut:
    cap = CapabilityDescriptor.model_validate(req.capability)
    preview = preview_payload(
        api=req.api,
        model=Model(id=req.model_id, reasoning=True),
        capability=cap,
        reasoning=req.reasoning,
    )
    return CapabilityPreviewOut.model_validate(preview.model_dump())
```

- [ ] **Step 5: Add route**

In `backend/cubeplex/api/routes/v1/admin_providers.py`:

```python
@router.post("/providers/capability-preview", response_model=CapabilityPreviewOut)
async def preview_provider_capability(
    body: CapabilityPreviewRequest,
    svc: ProviderService = Depends(get_provider_service),
) -> CapabilityPreviewOut:
    return svc.preview_capability(body)
```

- [ ] **Step 6: Surface lint warnings in probe summaries**

In `backend/cubeplex/services/provider_probe.py`, call `lint_capability` before
stream tests and include warning dicts in `ProbeResult` summary metadata.

- [ ] **Step 7: Run preview/probe tests**

Run:

```bash
cd backend
uv run pytest tests/e2e/test_admin_providers_crud.py \
  tests/unit/test_provider_probe.py --no-cov -q
```

Expected: pass.

- [ ] **Step 8: Commit Task 6**

```bash
git add backend/cubeplex/api backend/cubeplex/services backend/tests
git commit -m "feat: add capability preview and lint"
```

---

### Task 7: Frontend Reasoning Control and Capability Editor

**Files:**
- Modify: `frontend/packages/core/src/api/stream.ts`
- Modify: `frontend/packages/core/src/types/events.ts`
- Modify: `frontend/packages/core/src/types/conversation.ts`
- Modify: `frontend/packages/web/lib/types/presets.ts`
- Modify: `frontend/packages/web/lib/stores/preset-selection.ts`
- Modify: `frontend/packages/web/components/chat/EffortSlider.tsx`
- Modify: `frontend/packages/web/components/chat/ModelPicker.tsx`
- Modify: `frontend/packages/web/components/layout/InputBar.tsx`
- Modify: `frontend/packages/web/app/(app)/w/[wsId]/page.tsx`
- Modify: `frontend/packages/web/app/(app)/w/[wsId]/conversations/[id]/page.tsx`
- Modify: `frontend/packages/web/components/admin/models/wizard/CapabilityEditor.tsx`
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`
- Test: `frontend/packages/web/__tests__/components/InputBar.test.tsx`
- Test: `frontend/packages/web/__tests__/components/ModelPicker.test.tsx`
- Test: `frontend/packages/web/__tests__/stores/preset-selection.test.ts`

**Interfaces:**
- Consumes: backend `reasoning` request/response shape.
- Produces: composer and provider admin UI no longer send `thinking`.

- [ ] **Step 1: Update TypeScript types first**

In `frontend/packages/core/src/api/stream.ts`:

```ts
export type ReasoningMode = 'off' | 'auto' | 'on'
export type ReasoningEffort = 'minimal' | 'low' | 'medium' | 'high' | 'max'
export type ReasoningSummary = 'none' | 'auto' | 'detailed' | 'summarized'

export interface ReasoningControl {
  mode: ReasoningMode
  effort: ReasoningEffort
  summary: ReasoningSummary
}

export interface SendMessageRequest {
  content: string
  attachments?: string[]
  model_key?: string | null
  reasoning?: ReasoningControl
}
```

- [ ] **Step 2: Update failing UI tests**

In `frontend/packages/web/__tests__/components/InputBar.test.tsx`, replace the
send assertion:

```ts
expect(callArgs[5]).toEqual({
  model_key: 'reasoning',
  reasoning: { mode: 'on', effort: 'medium', summary: 'none' },
})
```

In `frontend/packages/web/__tests__/components/ModelPicker.test.tsx`, assert the
default reasoning:

```ts
expect(getPresetSelectionStore('ws_default').getState().reasoning).toEqual({
  mode: 'on',
  effort: 'medium',
  summary: 'none',
})
```

- [ ] **Step 3: Run frontend tests to verify failure**

Run:

```bash
cd frontend
pnpm vitest run packages/web/__tests__/components/InputBar.test.tsx \
  packages/web/__tests__/components/ModelPicker.test.tsx \
  packages/web/__tests__/stores/preset-selection.test.ts
```

Expected: fail until store and components use `reasoning`.

- [ ] **Step 4: Update preset-selection store**

In `frontend/packages/web/lib/stores/preset-selection.ts`, replace `thinking`
state with:

```ts
reasoning: { mode: 'on', effort: 'medium', summary: 'none' } satisfies ReasoningControl
setReasoning: (reasoning: ReasoningControl) => set({ reasoning })
reset: () =>
  set({
    modelKey: null,
    reasoning: { mode: 'on', effort: 'medium', summary: 'none' },
  })
```

Bump persisted store version and drop old `thinking` during migration.

- [ ] **Step 5: Update composer components**

Rename `EffortSlider` values to `ReasoningEffort`:

```ts
const EFFORT_LEVELS = [
  { value: 'minimal', labelKey: 'reasoningEffortMinimal' },
  { value: 'low', labelKey: 'reasoningEffortLow' },
  { value: 'medium', labelKey: 'reasoningEffortMedium' },
  { value: 'high', labelKey: 'reasoningEffortHigh' },
  { value: 'max', labelKey: 'reasoningEffortMax' },
] as const
```

Use a mode toggle in `ModelPicker`: `off`, `auto`, `on`. Keep `summary` hidden
and send `summary: 'none'`.

- [ ] **Step 6: Update send-message call sites**

In `InputBar`, workspace home, and conversation page, pass:

```ts
{
  model_key: validatedModelKey(selection),
  reasoning: selection.reasoning,
}
```

- [ ] **Step 7: Update CapabilityEditor**

Change the starter template from old fields to:

```ts
const CHAT_REASONING_TEMPLATE = {
  reasoning: {
    effort_path: 'reasoning_effort',
    effort_values: {
      minimal: 'minimal',
      low: 'low',
      medium: 'medium',
      high: 'high',
      max: 'max',
    },
    apply_effort_when_off: true,
  },
}
```

Call `/api/v1/admin/providers/capability-preview` after JSON edits debounce and
render warning codes inline.

- [ ] **Step 8: Run frontend tests**

Run:

```bash
cd frontend
pnpm vitest run packages/web/__tests__/components/InputBar.test.tsx \
  packages/web/__tests__/components/ModelPicker.test.tsx \
  packages/web/__tests__/stores/preset-selection.test.ts
pnpm lint
```

Expected: pass.

- [ ] **Step 9: Commit Task 7**

```bash
git add frontend/packages/core frontend/packages/web
git commit -m "feat: update frontend reasoning controls"
```

---

### Task 8: Documentation and Final Verification

**Files:**
- Modify: `docs/site/docs/*`
- Modify: `docs/dev/specs/2026-07-03-reasoning-capability-standard-design.md`
  only if implementation decisions changed during execution.
- Test: full backend and frontend verification commands.

**Interfaces:**
- Consumes: completed code from Tasks 1-7.
- Produces: user-facing docs and release-ready verification evidence.

- [ ] **Step 1: Identify docs pages to update**

Run:

```bash
rg -n "provider|model|thinking|reasoning|capability" docs/site/docs
```

Update the provider setup and model-selection pages that describe custom
providers, capability JSON, or reasoning controls.

- [ ] **Step 2: Document the new reasoning contract**

Add wording equivalent to:

```md
Cubeplex exposes reasoning as three fields: `mode`, `effort`, and `summary`.
Official OpenAI Chat, OpenAI Responses, and Anthropic Messages endpoints do not
need manual reasoning mappings. Custom endpoints can add capability overrides;
the preview tool shows the provider payload diff before saving.
```

If a screenshot is needed but not captured, add the required screenshot marker
block from `AGENTS.md`.

- [ ] **Step 3: Run backend verification**

Run:

```bash
cd backend
mkdir -p tmp
uv run pytest tests/unit tests/e2e/test_conversations.py \
  tests/e2e/test_admin_providers_crud.py --no-cov 2>&1 | tee tmp/reasoning-backend.log | tail -5
uv run mypy cubeplex 2>&1 | tee tmp/reasoning-mypy.log | tail -5
```

Expected: pytest and mypy exit 0.

- [ ] **Step 4: Run frontend verification**

Run:

```bash
cd frontend
pnpm lint 2>&1 | tee ../tmp/reasoning-frontend-lint.log | tail -5
pnpm typecheck 2>&1 | tee ../tmp/reasoning-frontend-typecheck.log | tail -5
```

Expected: lint and typecheck exit 0.

- [ ] **Step 5: Run catalog/provider focused verification**

Run:

```bash
cd backend
uv run pytest tests/test_provider_capability_factory.py \
  tests/unit/test_provider_probe.py \
  tests/e2e/test_admin_providers_crud.py --no-cov -q
```

Expected: pass.

- [ ] **Step 6: Confirm no legacy request-control references remain**

Run:

```bash
LEGACY_REASONING_RE="ThinkingLevel|thinking: ThinkingLevel|thinking\\?: ThinkingLevel"
LEGACY_REASONING_RE="${LEGACY_REASONING_RE}|request_obj.thinking|StreamOptions\\(thinking"
LEGACY_REASONING_RE="${LEGACY_REASONING_RE}|reasoning_off_payload"
LEGACY_REASONING_RE="${LEGACY_REASONING_RE}|reasoning_on_payload|reasoning_level"
rg -n "${LEGACY_REASONING_RE}" backend frontend
```

Expected: no matches for request-control paths. Matches for assistant output
blocks named `thinking` are acceptable only in message rendering, stream event
translation, and existing content schemas.

- [ ] **Step 7: Commit Task 8**

```bash
git add docs/site/docs docs/dev/specs
git commit -m "docs: update reasoning capability documentation"
```

- [ ] **Step 8: Prepare PR summary**

Write the PR summary with:

```md
## Summary
- replaced request-time `thinking` with provider-neutral `reasoning`
- moved official API reasoning mappings into cubepi profiles
- added custom capability preview/lint for provider setup

## Tests
- `uv run pytest ...`
- `uv run mypy cubeplex`
- `pnpm lint`
- `pnpm typecheck`
```
