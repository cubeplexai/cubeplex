# Managed Agents — Design Spec

Issue: #153
Status: Draft
Date: 2026-05-27

---

## Problem & motivation

Today a cubebox agent run has no first-class "agent" object. Everything that shapes a
run — system prompt, model, enabled MCP connectors, enabled skills, sandbox policy — is
read implicitly from **workspace-wide singletons** at run start (see *Current state*).
There is exactly one agent persona per workspace (`AgentConfig`, 1:1 with `Workspace`),
and it applies to every conversation in that workspace.

That model breaks down as soon as users want more than one named, purpose-built agent:

- A user wants a "Support Triage" agent (narrow prompt, ticket MCP, no code sandbox) and a
  "Data Analyst" agent (code sandbox, SQL MCP, charting skills) living side by side and
  invoked by name.
- Scheduled tasks (#150), triggers (#152), and IM integrations (#149) all need a **target**
  to invoke. "Run conversation in workspace W" is not a stable, shareable target; "run
  Managed Agent A" is.
- Teams want to define an agent once, review it, publish a version, and share it across the
  workspace or org — with the confidence that the config that was reviewed is the config
  that runs.

Managed Agents introduce a named, reusable, versioned **AgentDefinition**: a saved bundle of
agent configuration that can be instantiated into a run from any invocation surface.

---

## Goals / Non-goals

### Goals

- A named `AgentDefinition` that bundles: instructions, model selection, tool/MCP refs,
  skill refs, sandbox policy, and a permission scope.
- Instantiate a definition into a run by **reusing the existing run path** — a definition
  resolves to the same inputs `_run_cubepi_path` already takes, instead of falling back to
  workspace singletons.
- Versioning + publish, so a run pins an immutable version (reproducibility, prompt-cache
  stability).
- Sharing & scope: workspace-private and org-shared definitions, with scope-isolated routes
  per `CLAUDE.md`.
- Be the single invocation target for conversations and (later) #150 / #152 / #149.

### Non-goals (this spec)

- Multi-agent orchestration / handoffs between managed agents (subagents already exist for
  in-run delegation; cross-definition handoff is future work).
- A public, cross-org agent marketplace (mirror the skills-marketplace path later if wanted).
- Per-agent long-term knowledge bases / RAG corpora (tracked as an open question; v1 leans on
  the existing memory + skills systems).
- Replacing the per-workspace `AgentConfig`. The workspace default persona stays as the
  "no managed agent selected" fallback during the transition.
- Building the #150 / #152 / #149 surfaces themselves — this spec only defines the target
  contract they consume.

---

## Current state — how a run is configured & started today

Entry point: `RunManager._execute_run` → `RunManager._run_cubepi_path`
(`backend/cubebox/streams/run_manager.py`). A run is **assembled fresh per turn** from
workspace/org-scoped state. There is no persisted "agent" the run loads; the run *is* the
agent assembly. The inputs:

| Input | Where it comes from today | File / ref |
|---|---|---|
| System prompt | `BASE_SYSTEM_PROMPT` + the singleton `AgentConfig.system_prompt` for `(org, ws)`, then a stable skills-list suffix | `run_manager.py` ~L1772–1820; `models/agent_config.py`; `prompts/system.py` |
| Model + provider | `LLMFactory.resolve_default_provider_and_config()` — workspace/org **default** provider/model | `run_manager.py` ~L859–897; `llm/factory.py` |
| max_tokens / reasoning / thinking | `LLMFactory.get_model_config(...)` on the resolved model | `run_manager.py` ~L881–897, L1393–1398 |
| Builtin tools | `list_builtin_tools()` + per-request memory / load_skill / view_images / generate_image | `tools/registry.py`; `run_manager.py` ~L918–1032 |
| MCP tools | `load_workspace_mcp_tools_for_cubepi(...)` over the four-layer connector tables for the workspace | `run_manager.py` ~L1034–1111; `mcp/effective.py`, `mcp/cubepi_runtime.py` |
| Skills | `SkillCatalogService.list_enabled_for_workspace(ws, org)`; `load_skill` tool + skills-list prompt suffix | `run_manager.py` ~L963–975, L1805–1818; `skills/service.py`; `models/skill.py` |
| Sandbox | config-driven `LazySandbox` via `get_sandbox_manager()`; gated on `sandbox.enabled` | `run_manager.py` ~L1721–1744; `sandbox/lazy.py` |
| Middleware stack | 11 cubepi middleware composed in fixed order; tool order is **cache-prefix-stable** | `run_manager.py` ~L1123–1367; `agents/graph.py`; `middleware/_compose.py` |
| Agent construction | `create_cubebox_agent(provider, model_id, system_prompt, tools, middleware, …)` | `agents/graph.py` |

Two cross-cutting constraints the design must respect:

- **Scope isolation** (`CLAUDE.md`): workspace vs org-admin routes are separate handlers;
  reuse only at the service/repository layer. Tables carry `(org_id, workspace_id)` via
  `OrgScopedMixin` + `ScopedRepository`.
- **Prompt-cache discipline** (`backend/docs/prompt-cache-discipline.md`): the system prompt,
  tool definitions, and their order form the cache-eligible stable prefix and must be
  byte-identical across turns of the same conversation. A managed agent changes *which*
  prompt/tools are bound, but once a run starts it must keep them frozen for the
  conversation's lifetime.

**Key takeaway:** Managed Agents do not need a new runtime. They need a layer that, at run
start, **resolves an `AgentDefinition` version into exactly the inputs
`_run_cubepi_path` already accepts** — overriding the workspace-singleton lookups.

---

## Research — assistant / agent definition models

Consistent finding across platforms: an *agent definition* holds **intrinsic config**
(identity, instructions, model, tool/knowledge interfaces, approval policy) that is constant
across uses, while **runtime state** (the acting user, conversation thread, request context)
is passed per execution and kept out of the stored definition.

- **OpenAI Agents SDK** — an agent is "an LLM configured with instructions, tools, and
  optional runtime behavior." Definition fields: `name`, `instructions` (used as the system
  prompt), `model`, `tools`, `outputType`, `handoffs`, `handoffDescription`. Runtime
  state (authenticated user, db clients) is passed separately via a context parameter, kept
  out of model context. Guidance: "define the smallest agent that can own a clear task."
  ([define-agents](https://developers.openai.com/api/docs/guides/agents/define-agents),
  [agents](https://openai.github.io/openai-agents-python/agents/))
- **OpenAI Assistants API** (deprecating 2026-08-26 → Responses API) — an Assistant bundles
  `model`, `instructions`, `tools`, and attached files/knowledge; runs are separate objects.
  Prompts/agents are versioned in the dashboard with **snapshot, review, diff, roll back**.
  ([migration](https://platform.openai.com/docs/assistants/migration),
  [deep-dive](https://platform.openai.com/docs/assistants/deep-dive),
  [new tools for building agents](https://openai.com/index/new-tools-for-building-agents/))
- **Anthropic Claude Agent SDK — subagents** — each subagent has a custom system prompt, a
  specific tool allowlist, and independent permissions; the `description` drives when it's
  invoked. `allowed_tools` is a permission allowlist (auto-approve) layered over tool
  availability — a useful split between "can call" and "can call without approval."
  ([subagents](https://platform.claude.com/docs/en/agent-sdk/subagents),
  [permissions](https://platform.claude.com/docs/en/agent-sdk/permissions))

Design implications adopted below:
- Store intrinsic config on the definition; pass `(user, org, workspace, conversation)` per
  run via the existing `RunContext`.
- Version definitions immutably; pin a version per run (reproducibility + cache stability).
- Separate "tool/skill availability" from "permission/approval" so a definition can both
  narrow the tool surface and set a permission posture.

---

## Proposed design

### The AgentDefinition object

An `AgentDefinition` is the named, mutable head; each save publishes an immutable
`AgentDefinitionVersion`. This mirrors the existing `Skill` / `SkillVersion` split
(`models/skill.py`) so we reuse a proven catalog/version pattern.

A **version** carries the resolvable config. Most fields are *references* to underlying
objects (a renamed MCP connector is followed by ref), **except** skill refs, which pin a
concrete immutable `SkillVersion` so an admin's skill upgrade can't change a published agent's
skill content (see `skill_refs` below):

- `name` / `description` — identity; description doubles as the "when to use this agent"
  hint for invocation surfaces.
- `instructions` — the agent-specific system prompt. Composed at run start as
  `BASE_SYSTEM_PROMPT + instructions + skills-list suffix` (same shape as today's
  `AgentConfig.system_prompt`, so the cache prefix rules are unchanged).
- `model_ref` — `provider_slug` + `model_id`, OR a sentinel meaning "workspace default."
  Optional `thinking` / `max_tokens` overrides bounded by the model's config.
- `tool_policy` — which builtin tool groups are on (sandbox, artifacts, todo, memory,
  view/generate image, subagents). Tools the agent doesn't get are simply not bound;
  **tool order remains the fixed cache-stable order** for whatever subset is enabled.
- `mcp_refs` — list of MCP connector install ids (scoped to the workspace/org). At run start
  these filter the four-layer effective-connector resolution to just the referenced set.
- `skill_refs` — the skills the agent may load (the skills-list prompt suffix and
  `load_skill` allowlist are built from this set instead of "all enabled in workspace").
  Each ref pins a **concrete `SkillVersion`** (store `skill_version_id` + the resolved
  `version` string), not just the `OrgSkillInstall` id. This matters because the live skills
  loader resolves content by joining `SkillVersion.version == OrgSkillInstall.installed_version`
  (`skills/service.py` L74–81, L107–113) — the install row's `installed_version` is mutable, so
  pinning only the install id would let an admin's skill upgrade silently change a pinned
  agent's skill instructions/tools mid-conversation. Pinning the `SkillVersion` makes the
  snapshot's skill content immutable, matching the reproducibility + cache-stability goals. We
  still keep the `OrgSkillInstall` id alongside, so the resolver can verify the skill is still
  installed/visible in the workspace before binding the pinned version (see resolution note
  below).
- `sandbox_policy` — `none` | `default` | (future) named profile. Gates whether a
  `LazySandbox` is created and which workdir/limits apply.
- `permission_scope` — execution posture: which tool calls auto-run vs require approval
  (maps to the Anthropic allowlist split). v1 keeps this coarse (see Open Questions).

A version is immutable once published; editing creates the next version. The head row tracks
`current_version` and `published_version` (the default a run uses when none is pinned).

### How a definition instantiates into a run

No new runtime. We insert one resolution step before `_run_cubepi_path` and thread the
resolved values into its existing parameters:

1. `start_run` gains an optional `agent_definition_id` (+ optional pinned `version_id`).
   Absent → today's workspace-singleton behavior (back-compat during transition).
2. A new `AgentResolver` service loads the version and produces a `ResolvedAgentConfig`:
   - `effective_system_prompt` ← `BASE_SYSTEM_PROMPT + version.instructions` (+ skills suffix
     built from `skill_refs`). Replaces the `AgentConfig` lookup at `run_manager.py` ~L1772.
   - `provider_name / model_id / model_config` ← `model_ref` resolved through `LLMFactory`,
     or `resolve_default_provider_and_config()` when "workspace default."
   - MCP filter set ← `mcp_refs`, applied inside the existing
     `load_workspace_mcp_tools_for_cubepi` call.
   - Skill set ← `skill_refs`, applied to `load_skill` + skills-list suffix. The resolver loads
     each ref by its pinned `skill_version_id` directly (not via the
     `installed_version` join the workspace path uses), so the bound skill content is the exact
     version captured at publish time and cannot drift on a later install upgrade. It first
     checks the paired `OrgSkillInstall` is still present/visible in the (org, workspace); a
     missing/uninstalled ref is surfaced (hard-fail vs skip is an Open Question).
   - `sandbox_policy` → whether/how to build the sandbox.
   - `tool_policy` → which builtin/middleware tool groups to include (still in fixed order).
3. `_run_cubepi_path` consumes `ResolvedAgentConfig` instead of reading workspace singletons.
   Middleware composition, cache markers, and tool ordering are untouched.

Because the resolved config is frozen for the run and the conversation pins the version,
the stable prefix stays byte-identical across turns — prompt-cache discipline holds. Pinning
the version onto the conversation also means *editing the definition mid-conversation does
not mutate an in-flight thread* (treated like a new conversation if switched — see Open Q).

### Versioning & publish

- Save = create a new immutable `AgentDefinitionVersion` (append-only, like `SkillVersion`).
- `published_version` is the default target for invocations that don't pin a version.
- A conversation/run records the exact `version_id` it ran, for reproducibility and to keep
  history byte-stable.
- Diff/rollback is a UI concern over the immutable version rows (rollback = re-point
  `published_version`); no migration needed.

### Sharing & scope (scope-isolated)

Two visibility levels mirror the skills model (`OrgSkillInstall`):

- **workspace-private** — `workspace_id` set; visible only to that workspace.
- **org-shared** — `workspace_id` NULL; visible to all workspaces in the org.

Per `CLAUDE.md` scope-isolation, routes are **separate handlers**, never a `?scope=` param:

- Workspace authoring/listing: `/api/v1/ws/{ws}/agents/...`
- Org-admin shared management: `/api/v1/admin/agents/...`

Reuse lives one layer down: a shared `AgentDefinitionService` backs both handler sets. The
service uses a dedicated `AgentDefinitionRepository` — **not** `ScopedRepository`. Because
`workspace_id` is nullable here (NULL ⇒ org-shared), `ScopedRepository` is the wrong base:
it always filters `workspace_id == <current workspace>` and force-sets that workspace on
`add()`, which would hide org-shared rows from listings and save them as workspace-private.
Instead `AgentDefinitionRepository` follows the MCP-connector precedent
(`MCPConnectorInstallRepository`): the constructor takes `org_id` and enforces it on every
query, with separate methods for the two scopes — `list_org_shared()` (`workspace_id IS NULL`)
and `list_workspace(workspace_id)` (`workspace_id == <ws>`), plus a `list_visible(workspace_id)`
that unions both for the workspace-authoring view. `add()` persists the `workspace_id` the
caller passes (NULL for org-shared) rather than overriding it. Frontend gets separate Next
routes/pages per scope; shared `<List>` / `<DetailPanel>` / `<Editor>` modules are the reuse
boundary (no `mode` prop on pages).

### Invocation surfaces

- **Conversation** (v1): user picks a managed agent for a conversation; `start_run` passes
  `agent_definition_id`. The conversation pins the resolved `version_id` on first turn.
- **Scheduled tasks (#150)** / **triggers (#152)** / **IM (#149)** (future, contract here):
  each stores `(agent_definition_id, optional version_id)` as its target. At fire time it
  creates a conversation/run via the same `start_run(agent_definition_id=…)` path. The target
  contract is intentionally just "a definition id + optional pinned version + input payload,"
  so the three sibling features converge on one entry point and never re-implement run
  assembly.
- **Skill discovery (#151)** / **progressive disclosure (#143)** compose *inside* a run:
  `skill_refs` narrows the candidate set those mechanisms range over; they are unchanged
  otherwise.

---

## Data model

New tables (public-id prefixes via the `_PREFIX` ClassVar pattern in `models/public_id.py`).
All carry `(org_id, workspace_id)`. `agent_definitions` keeps `org_id` NOT NULL but
`workspace_id` **nullable** (NULL ⇒ org-shared), so it does not use `OrgScopedMixin` /
`ScopedRepository` — it mirrors `MCPConnectorInstall`'s nullable-workspace shape and its
dedicated repository (see Sharing & scope above).

- **`agent_definitions`** (`_PREFIX = "agtd"`) — the mutable head.
  - `org_id`, `workspace_id` (NULL ⇒ org-shared, mirroring `OrgSkillInstall`).
  - `name`, `description`, `current_version`, `published_version`, `created_by_user_id`,
    `archived_at` (soft-delete, like `Conversation.deleted_at`).
  - Partial unique index on `(org_id, name)` where `workspace_id IS NULL`; unique
    `(org_id, workspace_id, name)` otherwise — same shape as `OrgSkillInstall`.
- **`agent_definition_versions`** (`_PREFIX = "agtv"`) — immutable, append-only.
  - `agent_definition_id`, `version` (string), `created_by_user_id`.
  - `instructions` (Text), `model_ref` (JSON: provider/model or default sentinel +
    optional thinking/max_tokens), `tool_policy` (JSON), `sandbox_policy` (JSON/string),
    `permission_scope` (JSON/string).
  - Unique `(agent_definition_id, version)` (like `uq_skill_version`).
- **`agent_definition_mcp_refs`** (association; composite PK, no public_id — like
  `WorkspaceSkillBinding`): `(agent_definition_version_id, mcp_connector_install_id)`.
- **`agent_definition_skill_refs`** (association; composite PK):
  `(agent_definition_version_id, skill_version_id)`. Carries the pinned concrete skill version
  so the snapshot is immutable. Also stores `org_skill_install_id` (the install the version was
  selected through) so the resolver can confirm the skill is still installed/visible before
  binding; the install row's mutable `installed_version` is **not** used for resolution here.
- **Conversation pin** — add `agent_definition_id` + `agent_definition_version_id` (nullable)
  to `conversations`, recording the pinned target for the thread. Nullable ⇒ default
  workspace-persona behavior, so existing rows are unaffected.

Migration: `alembic revision --autogenerate` (no hand-edits), per `CLAUDE.md`.

---

## v1 slice (be ruthless)

Ship the smallest thing that makes a managed agent real and gives #150/#152/#149 a target:

1. **`AgentDefinition` + `AgentDefinitionVersion` (workspace-private only)** with
   `name`, `description`, `instructions`, `model_ref`, `skill_refs` (each pinning a concrete
   `skill_version_id`), `mcp_refs`, `sandbox_policy` (`none` | `default`). **Defer**
   `tool_policy` granularity and `permission_scope` to a fast-follow — v1 enables the standard
   tool set and current approval behavior.
2. **`AgentResolver` + run-path wiring**: `start_run(agent_definition_id, version_id?)`
   resolves to `ResolvedAgentConfig` and threads it through `_run_cubepi_path`, replacing the
   workspace-singleton lookups for prompt/model/MCP/skills/sandbox. Absent id ⇒ today's path.
3. **Versioning**: save = new immutable version; conversation pins `version_id` on first turn.
4. **Conversation invocation only**: select a managed agent for a conversation. No schedule /
   trigger / IM wiring yet — but the `start_run(agent_definition_id=…)` entry point is the
   contract those features will call.
5. **Workspace-scoped routes + pages** (`/api/v1/ws/{ws}/agents/...`). **Defer** org-shared
   sharing and `/api/v1/admin/agents/...` to v2.

Explicitly deferred from v1: org sharing, fine-grained `tool_policy`, `permission_scope`,
per-agent knowledge bases, marketplace, schedule/trigger/IM wiring, handoffs.

---

## Testing strategy (E2E-first)

Per `CLAUDE.md`, E2E over mocks. Primary gates:

- **E2E: definition → run reproducibility.** Create a definition with a non-default
  instructions + a known skill + an MCP ref; start a conversation pinned to it; assert the
  run actually uses that prompt/model/skill/MCP (not the workspace singleton). This is the
  load-bearing test — it proves resolution overrides the singleton path.
- **E2E: prompt-cache stability with a managed agent.** Extend the discipline gate
  (`tests/e2e/memory/test_prompt_cache.py`) to a managed-agent conversation: multi-turn,
  assert the stable prefix is byte-identical and the pinned version doesn't drift even after
  the definition is edited mid-conversation.
- **E2E: version pinning.** Run turn 1; publish a new version that changes the prompt; assert
  turn 2 in the same conversation still uses the pinned version.
- **E2E: scope isolation.** A workspace-private definition is invisible to another workspace;
  org-shared (v2) is visible org-wide. Workspace routes reject org-admin operations.
- **Unit:** `AgentResolver` mapping (each field → correct `_run_cubepi_path` input);
  ref-resolution when a referenced MCP connector / skill is missing or disabled.

---

## Open Questions

- **Knowledge / memory attachment.** Should a definition own a knowledge corpus (RAG), or
  only reference skills + lean on the existing memory system? Memory is currently
  user+workspace-scoped — is per-agent memory a thing, and does it break the snapshot/cache
  model in `prompt-cache-discipline.md`?
- **Permission model granularity.** How coarse is `permission_scope` in v1.x? Per-tool
  auto-approve allowlist (Anthropic-style) vs a single posture? Where is approval enforced —
  middleware, or a new gate? Does it interact with sandbox egress / credential grants?
- **Versioning semantics.** Version string scheme (semver vs monotonic int)? Is *every* save
  a published version, or draft-then-publish? What does "rollback" do to conversations pinned
  to the rolled-back version? Auto-bump vs explicit publish?
- **Mid-conversation agent switch.** Can a user change the managed agent on an existing
  conversation? If yes, is it a new cache epoch (new conversation semantics), or forbidden?
- **`model_ref` resolution when the referenced model is unavailable** (org override removed,
  provider down). Fall back to workspace default, or hard-fail the run?
- **Pinned skill version no longer installed.** A `skill_ref` pins a `skill_version_id`, but the
  admin may later uninstall that skill or remove the version's stored artifact. Does the
  resolver hard-fail the run, skip the missing skill (and warn), or fall back to the install's
  current `installed_version` (which reintroduces drift)? Also: should publishing a new
  agent version "re-pin" to each skill's latest installed version, or always require an
  explicit version choice?
- **Relationship to the workspace `AgentConfig` singleton.** Does the per-workspace persona
  become "the default managed agent," or stay a separate fallback? Long-term, do we migrate
  `AgentConfig` into an `AgentDefinition` row?
- **`tool_policy` shape.** Coarse tool *groups* (sandbox/artifacts/memory/…) vs individual
  tools? Granular toggles risk breaking the cache-stable tool order — what's the safe unit?
- **Subagents & handoffs.** A managed agent can already spawn subagents in-run. Should a
  definition reference *other definitions* as named subagents/handoff targets? (Out of v1.)
- **Org-shared editing & ownership.** Who can edit an org-shared definition — only admins?
  Fork-on-edit for workspaces? How do workspace overrides of a shared definition work?
- **Sharing beyond the org / marketplace.** Mirror the skills-marketplace publish path
  (`2026-04-26-skills-marketplace-design.md`) for cross-org agent sharing — in scope ever?
- **Cost attribution.** Should `CostMiddleware` stamp the `agent_definition_id` so usage can
  be sliced per managed agent? (Likely yes; confirm it doesn't touch the cache prefix.)
- **Input payload contract for #150/#152/#149.** What shape is the per-invocation input
  (free-text vs structured) that schedule/trigger/IM pass alongside `agent_definition_id`?

---

## References

- `backend/cubebox/streams/run_manager.py` — run assembly (`_execute_run`,
  `_run_cubepi_path`); the singleton lookups this spec overrides.
- `backend/cubebox/agents/graph.py` — `create_cubebox_agent` (agent construction).
- `backend/cubebox/models/agent_config.py` — current per-workspace persona singleton.
- `backend/cubebox/models/skill.py` — `Skill` / `SkillVersion` / `OrgSkillInstall` /
  `WorkspaceSkillBinding`; the catalog/version/install/scope pattern reused here.
- `backend/cubebox/models/mixins.py`, `models/public_id.py` — `CubeboxBase`,
  `OrgScopedMixin`, `_PREFIX` public-id pattern.
- `backend/cubebox/repositories/mcp.py` — `MCPConnectorInstallRepository`; the
  nullable-`workspace_id` (org-shared + workspace-private) repository shape reused here.
- `backend/docs/prompt-cache-discipline.md` — stable-prefix rules the resolver must honor.
- `backend/docs/agent-system-design.md` — (note: stale DeepAgents-era doc; the live runtime
  is cubepi as wired in `run_manager.py` / `agents/graph.py`).
- `CLAUDE.md` — scope-isolation rules; new-table public-id prefix rule; auth/scoping model.
- `docs/dev/specs/2026-04-26-skills-marketplace-design.md` — analogous publish/version model.
- Issues: #150 (scheduled tasks), #152 (triggers), #149 (IM), #151 (skill discovery),
  #143 (progressive disclosure).
- OpenAI Agents SDK — agent definition fields:
  https://developers.openai.com/api/docs/guides/agents/define-agents
- OpenAI Assistants migration / versioning:
  https://platform.openai.com/docs/assistants/migration
- Anthropic Claude Agent SDK — subagents & permissions:
  https://platform.claude.com/docs/en/agent-sdk/subagents ,
  https://platform.claude.com/docs/en/agent-sdk/permissions
