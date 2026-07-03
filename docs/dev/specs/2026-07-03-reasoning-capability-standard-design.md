# Reasoning Capability Standard

**Status:** Draft for review
**Author:** xfgong
**Date:** 2026-07-03
**Scope:** Redesign the reasoning-control abstraction shared by cubepi and
cubebox so official APIs work out of the box, while custom OpenAI-compatible
endpoints can still describe their non-standard request fields safely.

## 1. Problem

cubepi and cubebox currently expose `thinking` as the user-facing and runtime
control for model reasoning:

- API requests carry `thinking: "off" | "low" | "medium" | "high" | "xhigh"`.
- Conversations persist `thinking`.
- `RunManager`, `create_cubebox_agent`, and cubepi `StreamOptions` pass that
  value through.
- Provider capabilities translate it through `reasoning_off_payload`,
  `reasoning_on_payload`, and `reasoning_level`.

That shape worked while Anthropic extended thinking was the dominant mental
model, but it is now too narrow. Modern provider APIs split reasoning into
several concerns:

- Whether reasoning is allowed or disabled.
- How much reasoning effort the model should spend.
- Whether a reasoning summary or encrypted reasoning item should be returned.
- Which wire field expresses those controls for a given API.

The current abstraction mixes those concerns. It also lets a Cubebox-level
concept named `thinking` accidentally land in the wrong wire location. The
recent LiteLLM incident was the concrete failure: a custom OpenAI-compatible
provider stored `reasoning_on_payload: {"thinking": {"type": "enabled"}}`.
cubepi merged that payload at the top level of `chat.completions.create(...)`,
and the OpenAI SDK rejected it as an unexpected keyword argument.

The goal is not to hardcode every vendor in cubepi. The goal is to make official
protocol behavior a cubepi concern, then give Cubebox a precise, validated way
to describe non-standard provider mappings.

## 2. Goals

1. Give Cubebox one provider-neutral runtime control for reasoning.
2. Put official API defaults in cubepi, not in Cubebox business logic.
3. Keep vendor-specific and custom-provider behavior data-driven.
4. Make custom capability mappings understandable, previewable, and lintable.
5. Remove `budget_tokens` from the public Cubebox abstraction. Token budgets are
   an implementation detail of older Anthropic-style mappings.
6. Prefer a clean implementation over backwards compatibility with the current
   `thinking` field.

## 3. Non-Goals

- Do not add one provider subclass per commercial vendor.
- Do not make Cubebox know OpenAI or Anthropic wire details at call sites.
- Do not expose raw chain-of-thought. Summary controls are for provider-supported
  summaries only.
- Do not build a full visual capability editor in the first slice. JSON plus
  preview and lint is enough.
- Do not move the Cubebox vendor catalog into cubepi. cubepi owns protocols and
  generic profiles; Cubebox owns product catalog entries.

## 4. Proposed User Model

Replace `thinking` with `reasoning`:

```python
ReasoningMode = Literal["off", "auto", "on"]
ReasoningEffort = Literal["minimal", "low", "medium", "high", "max"]
ReasoningSummary = Literal["none", "auto", "detailed", "summarized"]


class ReasoningControl(BaseModel):
    mode: ReasoningMode = "off"
    effort: ReasoningEffort = "medium"
    summary: ReasoningSummary = "none"
```

Meaning:

- `mode=off`: disable reasoning where the provider supports disabling it. If the
  provider only has an effort knob, map this to the lowest non-reasoning or
  near-non-reasoning value such as `minimal`.
- `mode=auto`: let the model or provider decide whether to reason. If the
  provider has no auto mode, this may map to `on` with the chosen effort.
- `mode=on`: request reasoning explicitly.
- `effort`: the desired speed/cost/depth tradeoff.
- `summary`: request a provider-supported reasoning summary. `none` means do
  not request summaries.

`max` is Cubebox's canonical highest effort. Providers that call this `xhigh`,
`max`, or something else translate it through capability mappings.

## 5. Layer Boundary

### cubepi owns protocol semantics

cubepi should ship built-in profiles for official API shapes:

- `openai.chat_completions`
- `openai.responses`
- `anthropic.messages.adaptive`
- `anthropic.messages.legacy_budget`

Provider classes use those profiles by default when no explicit capability is
supplied. Cubebox should not need to know that OpenAI Responses uses
`reasoning.effort`, that Chat Completions may use a chat-specific effort field,
or that Anthropic adaptive thinking uses `thinking` plus `output_config.effort`.

### Cubebox owns catalog and overrides

Cubebox stores provider rows, model rows, catalog presets, custom provider
config, and organization settings. It can reference cubepi profile ids and store
overrides, but it should not implement protocol-specific payload rewriting at
runtime.

Example Cubebox catalog entry:

```yaml
api: openai-completions
capability_profile: openai.chat_completions
capability_overrides:
  reasoning:
    effort_path: reasoning_effort
    effort_values:
      minimal: minimal
      low: low
      medium: medium
      high: high
      max: max
```

The runtime merges the profile and overrides, then cubepi renders the request.

## 6. Capability DSL v2

Replace the current `reasoning_off_payload` / `reasoning_on_payload` /
`reasoning_level` trio with a clearer reasoning capability block.

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
```

Rules:

1. Start from the provider's normal request payload.
2. Merge `mode_payloads[reasoning.mode]` if present.
3. Write effort if `effort_path` is configured and either `mode != "off"` or
   `apply_effort_when_off` is true.
4. Write summary if `summary != "none"` and the provider has `summary_path` or
   an `include_payloads` entry for that summary mode.
5. If a requested mode or summary is unsupported, apply `unsupported_mode_policy`.
6. Capability writes use dotted paths, creating nested dicts as needed.
7. Capability writes are provider-controlled and win over scalar collisions.

This keeps the mapping explicit: mode, effort, and summary are separate
concerns. It also makes custom provider setup easier to explain.

## 7. Built-In Profiles

### 7.1 OpenAI Responses

Official OpenAI reasoning workloads are best represented through the Responses
API. The built-in profile should express:

```yaml
reasoning:
  mode_payloads:
    off: {}
    auto: { reasoning: {} }
    on: { reasoning: {} }
  effort_path: reasoning.effort
  effort_values:
    minimal: minimal
    low: low
    medium: medium
    high: high
    max: xhigh
  summary_path: reasoning.summary
  summary_values:
    auto: auto
    detailed: detailed
  include_payloads:
    auto: { include: [reasoning.encrypted_content] }
    detailed: { include: [reasoning.encrypted_content] }
```

If the target model is not marked `reasoning=true`, cubepi should omit reasoning
fields by default and produce a lint warning during preview/probe.

### 7.2 OpenAI Chat Completions

The official Chat Completions profile should be conservative because the chat
wire has historically had less reasoning state support than Responses. The
profile should support the current official chat reasoning effort field, but not
reasoning summaries or encrypted reasoning items.

```yaml
reasoning:
  mode_payloads:
    off: {}
    auto: {}
    on: {}
  effort_path: reasoning_effort
  effort_values:
    minimal: minimal
    low: low
    medium: medium
    high: high
    max: high
  apply_effort_when_off: true
```

Vendor-compatible chat endpoints can override `effort_path` or
`mode_payloads`. For example, Volcengine Chat API can use `reasoning_effort`
for effort and `thinking.type` only if that field is expected by the endpoint.

### 7.3 Anthropic Messages, Adaptive

Newer Anthropic models use adaptive thinking plus an effort field. cubepi should
support this as a first-class profile:

```yaml
reasoning:
  mode_payloads:
    off: { thinking: { type: disabled } }
    auto: { thinking: { type: adaptive } }
    on: { thinking: { type: adaptive } }
  effort_path: output_config.effort
  effort_values:
    minimal: low
    low: low
    medium: medium
    high: high
    max: xhigh
  summary_path: thinking.display
  summary_values:
    summarized: summarized
```

If a model rejects `thinking.type=disabled`, the model-specific override should
set the off mode to `{}` and document that the model has adaptive thinking
always on.

### 7.4 Anthropic Messages, Legacy Budget

Older Anthropic extended-thinking models use `thinking.budget_tokens`. That
should remain supported through capability mapping, not public API fields.

```yaml
reasoning:
  mode_payloads:
    off: {}
    auto: { thinking: { type: enabled } }
    on: { thinking: { type: enabled } }
  effort_path: thinking.budget_tokens
  effort_values:
    minimal: 0
    low: 2048
    medium: 8192
    high: 16384
    max: 32768
```

Anthropic provider code still needs to inspect the final payload. If
`thinking.budget_tokens` is present, it must adjust `max_tokens` so the budget is
below the output cap and enough visible-output room remains. This is a provider
wire constraint, not a Cubebox API concept.

## 8. Custom Provider Workflow

Custom providers need to know how to fill capability mappings without reading
cubepi source. Provide three tools.

### 8.1 Profile picker

The Cubebox Add Provider flow asks for the wire API:

- OpenAI Chat-compatible
- OpenAI Responses-compatible
- Anthropic Messages-compatible

This chooses the base `capability_profile`. Users only edit overrides when their
endpoint differs from the profile.

### 8.2 Payload preview

cubepi exposes a pure preview helper:

```python
preview_payload(
    api: WireApi,
    model: Model,
    capability: CapabilityDescriptor,
    reasoning: ReasoningControl,
    sample_messages: list[Message] | None = None,
) -> PayloadPreview
```

The preview returns:

- the reasoning-related payload diff,
- warnings,
- unsupported fields,
- the resolved profile id and overrides used.

Example:

```json
{
  "payload_diff": {
    "reasoning_effort": "high"
  },
  "warnings": []
}
```

For a bad OpenAI-compatible config that writes top-level `thinking`, preview
should warn before the provider is saved.

### 8.3 Capability lint

cubepi exposes a lint helper used by Cubebox create/update and probe flows:

```python
lint_capability(
    api: WireApi,
    capability: CapabilityDescriptor,
    model: Model | None = None,
) -> list[CapabilityWarning]
```

Initial lint rules:

- `openai-completions`: warn on top-level `thinking` unless the selected profile
  explicitly allows it.
- `openai-completions`: warn when `reasoning.summary` is configured.
- `openai-responses`: warn when effort is not under `reasoning.effort`.
- `anthropic-messages`: allow `thinking` and `output_config.effort`.
- Any API: warn when `effort_path` is set but no `effort_values` exist.
- Any API: warn when `summary_path` is set but `summary_values` are empty.
- Any API: warn when `mode_payloads` has unknown keys.

Warnings should not block save by default. A later UI can let operators choose
strict mode.

## 9. Data Model Changes

### cubepi

- Add `ReasoningControl`, `ReasoningMode`, `ReasoningEffort`, and
  `ReasoningSummary`.
- Replace `StreamOptions.thinking` with `StreamOptions.reasoning`.
- Replace `Provider.generate(... thinking=...)` with
  `Provider.generate(... reasoning=...)`.
- Keep `ThinkingContent` as a stream content block name for now. It describes an
  output block, not the request-control API.
- Replace `CapabilityDescriptor.reasoning_off_payload`,
  `reasoning_on_payload`, and `reasoning_level` with `reasoning:
  ReasoningCapability`.
- Add built-in profile registry and profile merge logic.

### cubebox backend

- Replace request body `thinking` with `reasoning`.
- Replace conversation column `thinking` with `reasoning JSON`.
- Replace run-manager and agent-factory arguments from `thinking` to
  `reasoning`.
- Update provider config, catalog loader, seeder, probes, and snapshots to store
  `capability_profile` plus `capability_overrides` or a resolved capability.
- Update serializers so `GET /conversations/{id}` returns `reasoning`.

Clean migration:

- Add `conversations.reasoning JSON NOT NULL` with default:
  `{"mode": "off", "effort": "medium", "summary": "none"}`.
- Drop `conversations.thinking`.
- Existing local conversations do not need exact preservation because this is a
  clean cutover.

### cubebox frontend

- Replace model picker payload field `thinking` with `reasoning`.
- Rename UI copy from "thinking" to "reasoning" or "reasoning effort".
- Add a compact control for mode and effort. Summary can stay hidden until a
  provider supports it in the active model.
- Add a JSON capability editor section that calls preview and lint.

## 10. Probe and Runtime Behavior

Provider probe should test the resolved capability, not just endpoint liveness.

Recommended probe matrix:

- `mode=off`, `effort=minimal`: endpoint accepts the lowest-cost setting.
- `mode=on`, `effort=medium`: endpoint accepts ordinary reasoning.
- `mode=on`, `effort=max`: advisory check only, because some models support max
  and some ignore or reject it.

Blocking failures:

- liveness failure,
- streaming failure,
- tools failure for models used by the agent,
- `mode=off` or `mode=on` rejected on a model marked `reasoning=true` when the
  selected profile says it should work.

Advisory warnings:

- summary unsupported,
- `max` unsupported,
- usage block missing,
- capability lint warnings.

Runtime should not mutate Cubebox-specific request payloads. It should pass
`ReasoningControl` into cubepi, and cubepi should produce the final provider
payload.

## 11. Implementation Slices

### Slice 1: cubepi reasoning core

- Add `ReasoningControl`.
- Add `ReasoningCapability`.
- Add profile registry.
- Add `apply_reasoning_control`.
- Add payload preview and lint helpers.
- Update OpenAI Chat, OpenAI Responses, and Anthropic providers.
- Unit-test payload diffs for all built-in profiles.

### Slice 2: cubebox backend cutover

- Update request/response schemas.
- Add migration for `conversations.reasoning`.
- Update run manager, agent factory, title generation, provider probe, and tests.
- Update catalog data to profile references and overrides.
- Add backend endpoints for preview/lint if the UI needs them.

### Slice 3: frontend and admin UX

- Update send-message payloads.
- Update conversation display and persisted settings.
- Update Add Provider advanced capability editor.
- Show preview/lint output before saving custom providers.

### Slice 4: catalog cleanup

- Convert current built-in provider presets to profile-plus-overrides.
- Remove stale `thinking` payloads from OpenAI-compatible profiles.
- Add focused custom examples for Qwen, Volcengine, Zhipu, LiteLLM, and vLLM.

## 12. Testing

### cubepi unit tests

- Built-in profile renders expected payload for:
  - OpenAI Chat Completions,
  - OpenAI Responses,
  - Anthropic adaptive,
  - Anthropic legacy budget.
- `mode=off` can still write effort when `apply_effort_when_off=true`.
- Summary writes only when configured.
- Lint catches top-level `thinking` on OpenAI Chat-compatible providers.
- Preview returns payload diff without making network calls.
- Anthropic legacy budget still adjusts `max_tokens`.

### cubebox backend tests

- `SendMessageRequest` accepts `reasoning` and rejects `thinking`.
- Conversation create/update/fork persists `reasoning`.
- `RunManager.start_run` passes `ReasoningControl` to cubepi.
- Provider create/update persists profile and overrides.
- Probe surfaces lint warnings in `last_test_summary`.

### frontend tests

- Chat input sends `reasoning`.
- Mode/effort UI state maps to the expected request body.
- Capability editor renders preview and lint warnings.
- Custom provider cannot silently save a malformed mapping without showing the
  warning returned by the backend.

## 13. Documentation

Update user-facing docs under `docs/site/docs/` in the implementation PR because
this changes provider setup behavior and chat request semantics:

- provider setup docs,
- custom model/provider docs,
- model picker or reasoning-control docs if present.

The docs should explain:

- Cubebox uses `mode`, `effort`, and `summary`.
- Official APIs need no manual mapping.
- Custom APIs use capability overrides.
- Preview shows the final provider payload diff.

## 14. Open Questions

1. Should `summary="summarized"` be Anthropic-only, or should Cubebox collapse it
   into `summary="auto"` for non-Anthropic APIs?
2. Should capability lint ever block save, or only provider test?
3. Should Cubebox persist the selected profile id plus overrides, or persist only
   the fully resolved capability snapshot? The current provider platform favors
   snapshots for runtime stability, but profile ids make future profile upgrades
   easier to explain.
4. Should `mode=auto` be the default for reasoning-capable models, or should
   Cubebox keep the current default behavior of no reasoning unless requested?
