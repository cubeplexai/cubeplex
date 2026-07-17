# LLM Vendor Compat — Config-Driven Thinking Toggle + Title Model

**Status:** Superseded by `2026-05-19-llm-provider-platform-design.md` —
this spec was framed narrowly around the title-gen 30s incident. The
follow-up reframes model management at the product level (preset
catalog + capability descriptors + connection test) and absorbs both
pieces below as side-effects of the larger change.
**Author:** xfgong
**Date:** 2026-05-18
**Scope:** Two changes that came out of a single incident. The shared
target is "make it cheap to add a new OpenAI-compatible LLM endpoint
through the cubeplex UI, including vendors that put their own private
fields in `extra_body` for things like reasoning toggle." Two pieces:

1. **cubepi:** turn `StreamOptions.thinking` into something that works on
   the OpenAI-completions path too, via a small data-driven registry of
   per-vendor `extra_body` writers. No new Provider subclasses.
2. **cubeplex:** stop using the default chat model for conversation title
   generation. Wire a separate `title_model` so reasoning models never
   run a 4-Chinese-character labeling task.

Sections are written so each piece can ship as its own PR.

---

## 1. Problem

### 1.1 The incident

Frontend showed a 500 on `POST /api/v1/ws/.../generate-title`. Tracing
through:

| layer | what we saw |
|---|---|
| browser console | `client.ts:78 POST .../generate-title 500` |
| Next.js dev log | `Failed to proxy http://localhost:8000/...generate-title  Error: socket hang up  ECONNRESET` |
| backend uvicorn log | the request was accepted; **no response row was ever written for it** |

Other generate-title calls in the same log returned 200 — but at 27s
and 29s on previous days. Reading
`next/dist/server/lib/router-utils/proxy-request.js` confirms Next.js
dev rewrite has a hard 30 000ms `proxyTimeout`, after which it destroys
the upstream socket, logs `Failed to proxy ... ECONNRESET`, and returns
500 to the browser. Backend logs nothing because the FastAPI task is
cancelled before any response is sent.

A standalone timing script (`/tmp/title_timing.py` in this branch's
investigation) made the same call two ways:

```
raw httpx → alicode /chat/completions:   first token 75.5s, last 75.7s
cubepi OpenAIProvider stream:            loop exit 63.7s
```

Both calls produced 2 output chunks for an 8-character title. The 60–75
seconds is **server-side reasoning time** before the model emits its
first visible token. cubepi adds no measurable overhead.

### 1.2 What this exposed

Two problems sit underneath:

**Problem A — task / model mismatch.**
`alicode/qwen3.6-plus` is a reasoning coder model. Generating a 4-word
sidebar label is not a reasoning task. Using the same default model for
both chat and title is what made the request bump into the 30s wall.
Even if reasoning were free, this is wrong on principle: title gen
should run on the cheapest small model the workspace has.

**Problem B — cubepi has no unified way to disable reasoning on
OpenAI-completions endpoints.**

cubepi exposes `StreamOptions.thinking: ThinkingLevel`. It works on
`AnthropicProvider` (maps to `thinking.budget_tokens`) and on
`OpenAIResponsesProvider` (maps to `reasoning.effort`). On
`OpenAIProvider` it's read into `opts` and then never touched — see
`cubepi/providers/openai.py:72` and the absence of any later reference.

That's because OpenAI's `/chat/completions` spec has no reasoning
toggle. The vendors that ride this wire (Qwen via dashscope, Doubao via
volcengine, MiniMax, vLLM-served OSS reasoning models, etc.) each
invented their own private field:

| vendor | toggle |
|---|---|
| Qwen (dashscope) | `extra_body.enable_thinking: bool` |
| 豆包 (volcengine) | `extra_body.thinking: {type: "enabled"\|"disabled"\|"auto"}` |
| MiniMax | `extra_body.thinking: {budget: int}` |
| OpenAI reasoning-effort (Responses-style on completions wire) | `reasoning_effort: "low"\|"medium"\|"high"` |

Today cubeplex has no way to express "this model lives behind the
OpenAI-completions wire but toggles reasoning with `enable_thinking`."
Anywhere we'd want `StreamOptions(thinking="off")` to actually disable
reasoning, we'd have to write call-site code that mutates
`extra_body`, per vendor. That doesn't scale — every new vendor costs a
code change in cubeplex.

### 1.3 What we ruled out

- **One Provider subclass per vendor (litellm style).** litellm has 60+
  vendor directories and ships a separate transformation class per
  vendor. We considered the same — `QwenProvider`, `DoubaoProvider`,
  etc. inheriting from `OpenAIProvider`. Rejected: every new vendor a
  user wants to add through the cubeplex UI would require shipping a
  cubepi release. UX for "add a self-hosted Qwen endpoint" should not
  require a backend deploy.

- **Hardcoding `enable_thinking: false` at the title-gen call site.**
  Solves the immediate symptom; doesn't generalize to other vendors or
  other reasoning-toggle scenarios; reintroduces the same hack the next
  time we add a vendor.

- **Routing title gen through a frozen baked-in model**, e.g. always
  Haiku. Doesn't work — many cubeplex installs have no Anthropic
  credential. The choice must be per-org/per-workspace.

---

## 2. Mental model

Three independent axes — keep them separate in the schema and the
code.

```
 ┌─────────────────────────────────────────────────────────────────┐
 │ wire protocol   "what HTTP request shape does the endpoint     │
 │                  speak?"                                        │
 │                 anthropic-messages | openai-completions |       │
 │                 openai-responses                                │
 ├─────────────────────────────────────────────────────────────────┤
 │ vendor quirks   "given a unified intent (thinking=off, ...),   │
 │                  how does THIS endpoint expect it expressed in  │
 │                  the request body?"                             │
 │                 qwen.enable_thinking | doubao.thinking_type |   │
 │                 openai.reasoning_effort | null                  │
 ├─────────────────────────────────────────────────────────────────┤
 │ task ↔ model    "for THIS task (chat / title / summarize), what│
 │   matching       model should we actually call?"                │
 │                 default_model | title_model | ...               │
 └─────────────────────────────────────────────────────────────────┘
```

- Wire protocol lives on the **provider** row.
- Vendor quirks live on the **model** row (because two models behind
  the same provider can have different reasoning conventions; e.g.
  Qwen-Plus has `enable_thinking`, Qwen-Turbo doesn't reason at all).
- Task-model matching lives on the **org settings / config** layer
  (top-level `llm.title_model`, future `llm.summarize_model`, etc.).

inspirations:

- **litellm** owns the per-vendor knowledge in code (subclasses) but
  doesn't normalize reasoning toggle across them — Volcengine's
  subclass translates `thinking={type: ...}` to extra_body, dashscope's
  subclass doesn't translate at all. The conceptual axis ("vendor
  quirks for thinking") is real and litellm names it; the cost is N
  classes.
- **craft-agents-oss** takes the opposite extreme — only two `api`
  values (`openai-completions` / `anthropic-messages`), no per-vendor
  classes; capability hints (`supportsThinking`, `supportsImages`)
  are declared on the model and the underlying SDK is expected to
  translate. They also have an explicit
  `getMiniModel()` / `getSummarizationModel()` pattern — the task ↔
  model axis is a first-class product concept, not implicit.

We adopt craft's structure plus litellm's "thinking can be translated
on the completions wire" idea, implemented as a registry of
`extra_body` writers in cubepi.

---

## 3. Decision (B'' — config-driven vendor compat + separate title model)

### 3.1 cubepi: thinking-protocol registry

Add a small data table that maps a string identifier to a function
that mutates the OpenAI-completions request payload. Plumb a single new
field through `Model` so cubeplex can opt into a particular protocol
per model row.

```python
# cubepi/providers/thinking_protocols.py  (new file)

from typing import Callable
from cubepi.providers.base import ThinkingLevel

ThinkingApplier = Callable[[dict, ThinkingLevel], None]

def _qwen_enable_thinking(kwargs: dict, level: ThinkingLevel) -> None:
    eb = kwargs.setdefault("extra_body", {})
    eb["enable_thinking"] = level != "off"

def _doubao_thinking_type(kwargs: dict, level: ThinkingLevel) -> None:
    eb = kwargs.setdefault("extra_body", {})
    eb["thinking"] = {
        "type": "disabled" if level == "off" else "enabled",
    }

def _openai_reasoning_effort(kwargs: dict, level: ThinkingLevel) -> None:
    if level == "off":
        kwargs.pop("reasoning_effort", None)
        return
    kwargs["reasoning_effort"] = {
        "minimal": "minimal", "low": "low", "medium": "medium",
        "high": "high", "xhigh": "high",
    }[level]

THINKING_PROTOCOLS: dict[str, ThinkingApplier] = {
    "qwen.enable_thinking":      _qwen_enable_thinking,
    "doubao.thinking_type":      _doubao_thinking_type,
    "openai.reasoning_effort":   _openai_reasoning_effort,
}
```

Plumbing:

- `Model` (in `cubepi/providers/base.py` — the pydantic dataclass cubeplex
  already passes to `provider.stream()`) gets one new optional field:

  ```python
  class Model(BaseModel):
      id: str
      provider: str
      reasoning: bool = False
      # NEW:
      thinking_protocol: str | None = None
  ```

- `OpenAIProvider.stream()` reads it after `opts = options or StreamOptions()`:

  ```python
  if model.thinking_protocol and opts.thinking is not None:
      applier = THINKING_PROTOCOLS.get(model.thinking_protocol)
      if applier is not None:
          applier(kwargs, opts.thinking)
      else:
          logger.warning(
              "Unknown thinking_protocol %r on model %s; ignoring.",
              model.thinking_protocol, model.id,
          )
  ```

- `AnthropicProvider` and `OpenAIResponsesProvider` ignore
  `thinking_protocol` (their existing `opts.thinking` path is already
  correct). The field is documented as "OpenAI-completions wire only."

- `cubepi.list_thinking_protocols() -> list[str]` exposes the registry
  keys so cubeplex can render the picker dropdown without hardcoding
  the list.

### 3.2 cubeplex: schema + factory

```
-- alembic autogenerate
ALTER TABLE models ADD COLUMN thinking_protocol VARCHAR(64) NULL;
```

Existing `models.reasoning: bool` stays — it answers "does this model
reason at all?" which is orthogonal to "what's the toggle field name."
`thinking_protocol` is meaningful only when `reasoning = true`.

In `LLMFactory._load_db_provider_configs`, the model dict picks up the
new field and forwards it through `ProviderConfig` / `ModelConfig`:

```python
{
    "id": m.model_id,
    "reasoning": m.reasoning,
    "thinking_protocol": m.thinking_protocol,   # NEW
    ...
}
```

`LLMFactory.build_cubepi_provider()` is unchanged. The model_id and
thinking_protocol travel through to `cubepi.Model` at call site:

```python
Model(
    id=model_id,
    provider=provider_name,
    reasoning=model_cfg.reasoning,
    thinking_protocol=model_cfg.thinking_protocol,
)
```

### 3.3 cubeplex: title model

Top-level config gains an optional `title_model: str | None`. Resolution
order, all done in `LLMFactory`:

1. `OrgSettings(key="title_model").value.model_ref` (per-org override)
2. `config.llm.title_model` (yaml)
3. fall back to `default_model`

`generate_and_apply_title` switches from
`factory.resolve_default_provider_and_config()` to a new
`factory.resolve_task_model("title")`. The method takes a task name and
walks the same merge chain that `resolve_default_provider_and_config`
walks, but reads `title_model` / `summarize_model` / etc. as the
top-of-chain reference.

When the org has not set a title model, this falls back to the default
model — current behavior preserved.

Independent of (3.1): when a small non-reasoning model is wired up as
`title_model`, the title call no longer trips the 30s rewrite limit even
without any thinking toggle.

### 3.4 cubeplex: UI (Provider/Model admin)

Two changes in `components/admin/models/`.

**ModelFormDialog.tsx** — add a Reasoning-protocol picker, visible only
when `reasoning` is true. Options come from
`GET /api/v1/admin/llm/thinking-protocols` (new endpoint, wraps
`cubepi.list_thinking_protocols()` + a static description map):

```
☑ Reasoning model
  Reasoning toggle (how this model turns thinking on/off):
  ○ None / always-on (server controls)
  ● Qwen      — extra_body.enable_thinking: bool
  ○ Doubao    — extra_body.thinking.type
  ○ OpenAI    — reasoning_effort
```

**SettingsPage** (new section under admin/settings or model defaults) —
add a "Task models" block:

```
Chat (default model)         [ alicode / qwen3.6-plus    ▾ ]
Conversation titles          [ alicode / qwen3.6-flash   ▾ ]
                              ☐ Use chat model
```

Empty / "Use chat model" → leaves `OrgSettings.title_model` unset →
falls back to default. No magic.

### 3.5 Default DB seed updates

`backend/cubeplex/db/seed/system_providers.py` (or equivalent — the
seed module that creates the system Provider/Model rows) gets
`thinking_protocol` populated for the providers we ship presets for:

| provider | model | thinking_protocol |
|---|---|---|
| alicode (dashscope) | qwen3.6-plus | `qwen.enable_thinking` |
| sensedeal | doubao-seed-2.0-pro | `doubao.thinking_type` |
| openrouter | openai/gpt-* | `openai.reasoning_effort` |
| anthropic / openai-responses paths | * | `null` (handled by their own provider) |

Each cubeplex install on first migration gets the right defaults; the
admin can change them in the UI later.

---

## 4. Non-goals (explicit)

- **No automatic vendor detection.** We do not sniff `base_url` to guess
  "this looks like dashscope, set Qwen protocol." When a user adds a
  custom endpoint, they pick the protocol from the dropdown. Magic is
  worse than the typing.
- **No per-call thinking-protocol override.** The protocol is a property
  of the model, not the call. Callers say "thinking=off"; the
  registered protocol decides what bytes go on the wire.
- **No coverage for non-reasoning-toggle vendor quirks** in this
  iteration. Vendor differences in `usage` parsing, error envelopes,
  tool-call shape, etc. are not in scope. When a real case appears, we
  evaluate whether it fits another registry or whether it warrants
  finally introducing a Provider subclass.
- **No new general "extra_body composer" UI.** The model row already has
  an `extra_body: JSON` column the admin can edit raw. The dropdown is
  for the common case (thinking toggle); raw `extra_body` remains the
  escape hatch.
- **No retry / fallback on title-gen failure** beyond what exists. If
  the picked title model is misconfigured, the existing best-effort
  catch in `conversation_title.py` still swallows the error.

---

## 5. Migration / backward compat

- New column `models.thinking_protocol` is nullable with default null.
  Existing rows = no protocol = same behavior as today (i.e.
  `StreamOptions.thinking` is ignored on the completions wire). Nothing
  breaks.
- The yaml `config.development.local.yaml` does **not** need to declare
  `thinking_protocol` to keep working. When the field is absent, models
  load with `thinking_protocol = None`.
- The seed migration that updates ship-in defaults (3.5) runs once and
  is idempotent — it only writes to rows whose `thinking_protocol` is
  still null. Admin overrides are preserved.
- cubepi exposing `thinking_protocol` on `Model` is additive — existing
  cubepi callers in other repos that build `Model(...)` without the
  field continue to work (field default = None).

---

## 6. Rollout — three small PRs

### PR 1 — cubepi: thinking-protocol registry

Repo: `/home/chris/cubepi`.

1. Add `cubepi/providers/thinking_protocols.py` with the three initial
   appliers (qwen.enable_thinking, doubao.thinking_type,
   openai.reasoning_effort) and `THINKING_PROTOCOLS` map.
2. Add `thinking_protocol: str | None = None` to `Model`.
3. In `OpenAIProvider.stream()`, after `opts = options or
   StreamOptions()` and after building the base `kwargs`, look up and
   apply the registered protocol. Log a warning on unknown key.
4. Export `cubepi.list_thinking_protocols() -> list[str]`.
5. Tests: one unit test per protocol asserting `kwargs` mutation for
   `off` / `low` / `high`; one integration test on a faux provider
   asserting `Model(thinking_protocol="qwen.enable_thinking")` +
   `StreamOptions(thinking="off")` ends with `extra_body.enable_thinking
   = False` in the captured payload.

No behavior change for callers that don't set `thinking_protocol`.

### PR 2 — cubeplex: schema + factory + title model

Repo: `/home/chris/cubeplex`. Depends on PR 1 being released.

1. Alembic migration (autogenerate): add
   `models.thinking_protocol VARCHAR(64) NULL`.
2. SQLModel `Model.thinking_protocol`. `LLMFactory._load_db_provider_configs`
   plumbs it into the model dict; `ProviderConfig` / `ModelConfig` types
   carry it; `cubepi.Model` constructor receives it.
3. `LLMFactory.resolve_task_model(task: Literal["title"])` walks
   OrgSettings → yaml → default fallback.
4. `conversation_title.generate_and_apply_title` calls
   `resolve_task_model("title")` instead of
   `resolve_default_provider_and_config()`. Same `StreamOptions(thinking="off")`
   passed — for a small non-reasoning model this is a no-op; for a
   reasoning model with a wired protocol it disables thinking.
5. New endpoint: `GET /api/v1/admin/llm/thinking-protocols` returning
   `[{id, label, description}, ...]` for the UI dropdown.
6. Seed migration: write `thinking_protocol` defaults onto known system
   Provider/Model rows (alicode/qwen, sensedeal/doubao,
   openrouter/openai-effort).
7. E2E: title gen with a wired non-reasoning title model returns <5s;
   chat with a reasoning default model still gets reasoning.

### PR 3 — cubeplex: admin UI

1. `ModelFormDialog`: reasoning-protocol dropdown, gated by `reasoning`
   checkbox. Populated from `/admin/llm/thinking-protocols`.
2. Admin settings: "Task models" section with two pickers (chat,
   titles). Writes to `OrgSettings`.
3. Update i18n strings.

---

## 7. Open questions

1. **Where does the protocol description text live?** The dropdown
   needs human-readable labels per protocol. Option (a) static map in
   cubeplex frontend, (b) cubepi ships labels alongside appliers, (c)
   admin endpoint synthesizes from `THINKING_PROTOCOLS` keys + an
   override table in cubeplex. Leaning (a) — i18n already in cubeplex,
   no reason to span repos for translation strings.

2. **Should `thinking_protocol` live on the Provider row instead of the
   Model row?** Argument for Provider: all models from one vendor share
   the toggle convention. Argument for Model: Qwen ships both
   reasoning models and non-reasoning models behind the same provider;
   non-reasoning models shouldn't carry the field. Decision: Model
   row. Cost is one extra dropdown when adding multiple models, but
   the semantics are clean.

3. **Naming.** `thinking_protocol` vs `reasoning_protocol` vs
   `reasoning_toggle_protocol`. cubepi already uses both "thinking"
   (StreamOptions field, ThinkingLevel type) and "reasoning" (Model
   field). Sticking with `thinking_protocol` aligns with the
   StreamOptions name the caller already touches.

4. **Future task-model slots.** This spec wires only `title_model`. A
   follow-up will add `summarize_model` (response summarization) and
   `mini_model` (tool-result distillation). Schema and resolver are
   designed for it (`resolve_task_model(task: str)`) but seed and UI
   only ship `title`.
