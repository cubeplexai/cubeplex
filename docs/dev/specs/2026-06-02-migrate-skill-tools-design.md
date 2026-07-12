# Migrate Skill Tools to Agent Platform Actions — Design

**Date:** 2026-06-02
**Status:** Approved (brainstorming)
**Branch:** `feat/migrate-skill-tools`
**Spec parent:** `docs/dev/specs/2026-06-01-agent-platform-actions-design.md`

## Problem

PR #185 established a unified mechanism for agent-operable platform
capabilities (`AgentCapability` registry + generic builder + `ScopeContext`)
and landed scheduled-tasks as the first capability built on it. The existing
skill tools — `find_skills`, `preview_skill`, `install_skill` — predate that
mechanism and are still wired ad-hoc in `streams/run_manager.py:1116-1193`:

- Each tool is its own `create_X_tool(...)` factory in `cubeplex/tools/builtin/`.
- `run_manager.py` constructs a `SkillsAdapterManager`, an
  `OrganizationRepository`, a `SkillCatalogService` reference, a catalog
  session, and threads them through three separate factory calls.
- No structural mutation gate — `install_skill` is exposed to automated runs.

This migration moves the three skill tools onto the same mechanism so that:
- The bespoke wiring in `run_manager.py` collapses to a single capability
  registration.
- `install_skill` is gated by `mutates=True` (automated runs see only `find`
  and `preview`).
- Future "agent-operable" capabilities follow one pattern, not two.

## Goals

- One `skills` capability tool with 3 operations (`find`, `preview`, `install`).
- Same business behavior (no semantic changes to discovery, preview, install).
- The three legacy `create_X_tool` factories and their files are deleted.
- `install` is `mutates=True` so automated runs cannot install skills.

## Non-Goals

- `load_skill` migration. `load_skill` is runtime infrastructure: its result
  is intercepted by `SkillsMiddleware`, which appends the loaded SKILL.md to
  the next system prompt. It is not a "platform action" the user delegates to
  the agent — it is how the agent reads a skill at runtime. Stays wired
  directly in `run_manager.py`.
- Service extraction beyond what is already there. `SkillDiscoveryService`,
  `SkillInstallService`, `SkillCatalogService`, and `SkillsAdapterManager`
  already exist; we reuse them as-is.
- Changes to the skill catalog, registries, or adapters.

## Decisions (from brainstorming)

- **Capability shape:** one tool, three operations (`find`, `preview`,
  `install`). Discriminated union via `operation`, consistent with the
  `scheduled_tasks` capability.
- **`mutates` assignment:** `find=False`, `preview=False`, `install=True`.
  Automated runs see `find` and `preview` only.
- **Static vs dynamic capability:** the `skills` capability needs run-scoped
  dependencies (`SkillCatalogService`, `catalog_session`,
  `SkillsAdapterManager`, `org_id`). Unlike `SCHEDULED_TASKS_CAPABILITY`
  (a module-level constant whose handlers re-resolve everything from the
  generic `ContextFactory`'s `(ScopeContext, session)`), `skills` cannot be
  a constant — its handlers must close over a per-run `SkillsAdapterManager`
  that is itself built async from the catalog session.
- **Mechanism extension:** introduce `build_skills_capability(...)` that
  returns an `AgentCapability` with handlers that have closed over the
  run-scoped deps. The registry's `tools_for_run(...)` accepts an optional
  `skill_deps` payload; when present, the registry calls the factory and
  appends the resulting capability to the list. The `ContextFactory` type
  does NOT change — only the registry's entry point gains the optional
  payload.

## Architecture

### Capability definition (dynamic)

```python
# agents/actions/capabilities/skills.py

@dataclass(frozen=True)
class SkillDeps:
    catalog: SkillCatalogService
    catalog_session: AsyncSession
    registry: SkillsAdapterManager
    org_id: str

def build_skills_capability(deps: SkillDeps) -> AgentCapability:
    """Construct the skills capability with run-scoped deps captured.

    Handlers close over `deps`; the discovery / install / preview services
    are instantiated lazily from the closed-over catalog_session + registry.
    """
    ...
```

The three operations:

| Operation | mutates | Handler delegates to |
|---|---|---|
| `find`    | `False` | `SkillDiscoveryService(registry).discover(query, limit)` |
| `preview` | `False` | Inline logic (mirrors today's `preview_skill.py`, lifted into `skills.py` as a private helper). |
| `install` | `True`  | `SkillInstallService(...).install(candidate_id)` |

`preview` does not have its own service today; the existing tool's logic
(local lookup vs remote fetch, env-var extraction) moves into the capability
module as a private helper. This is a content-move, not a behavior change.

### Registry extension

```python
# agents/actions/registry.py

def tools_for_run(
    context_factory: ContextFactory,
    *,
    allow_mutations: bool,
    skill_deps: SkillDeps | None = None,
) -> list[AgentTool[Any]]:
    tools = [...]  # existing scheduled_tasks
    if skill_deps is not None:
        skills_cap = build_skills_capability(skill_deps)
        tool = build_capability_tool(skills_cap, context_factory,
                                     allow_mutations=allow_mutations)
        if tool is not None:
            tools.append(tool)
    return tools
```

Rationale for the optional payload: `skill_deps` is only available when the
catalog is reachable. If the DB is down, the catalog block in `run_manager`
skips both `load_skill` (already does) and the new `skills` capability,
matching today's behavior.

### Handler signatures and DI

Handlers conform to the existing `(ctx: ScopeContext, session: Any, input)`
contract. The `session` they receive is a **fresh per-call** session from
the `ContextFactory` (used for `install`, which mutates DB state under the
caller's scope). The closed-over `catalog_session` is reused for `find`
and `preview` (read-only catalog/registry reads), matching the current
wiring's lifetime.

### run_manager change

The 80-line block at `run_manager.py:1116-1193` collapses to:

```python
# load_skill — unchanged; this is runtime infrastructure, not a platform action
if skill_catalog is not None:
    ... existing load_skill wiring ...

# Skills, scheduled_tasks, and any future capability — registered via the
# action registry.
skill_deps: SkillDeps | None = None
if skill_catalog is not None and catalog_session is not None:
    _org = await OrganizationRepository(catalog_session).get(ctx.org_id)
    if _org is not None:
        _registry = await SkillsAdapterManager.build(
            session=catalog_session,
            catalog=skill_catalog,
            org_id=ctx.org_id,
            org_slug=_org.slug,
            workspace_id=ctx.workspace_id,
        )
        skill_deps = SkillDeps(
            catalog=skill_catalog,
            catalog_session=catalog_session,
            registry=_registry,
            org_id=ctx.org_id,
        )

_builtin_tools.extend(
    tools_for_run(
        _action_ctx_factory,
        allow_mutations=(trigger == "interactive"),
        skill_deps=skill_deps,
    )
)
```

The `install` handler also needs `org_slug` + `actor_user_id` to construct
`SkillInstallService`. `actor_user_id` is in `ScopeContext.user_id`; we add
`org_slug` to `SkillDeps`.

### Deletions

After migration, delete:
- `backend/cubeplex/tools/builtin/find_skills.py`
- `backend/cubeplex/tools/builtin/preview_skill.py`
- `backend/cubeplex/tools/builtin/install_skill.py`

The corresponding `from cubeplex.tools.builtin.find_skills import ...`
imports in `run_manager.py` are removed.

`load_skill.py` and its registration stay.

## Data Flow

### `find` (read-only)
1. LLM calls `skills(operation="find", query="...", limit=5)`.
2. Builder dispatches to the `find` handler.
3. Handler instantiates `SkillDiscoveryService(deps.registry)` and calls
   `discover(query, limit)`.
4. Returns the same JSON shape today's `find_skills` tool returns.

### `preview` (read-only)
1. LLM calls `skills(operation="preview", candidate_id="...")`.
2. Handler runs the local-vs-remote dispatch (same logic as today's
   `preview_skill.py`), using `deps.catalog_session` for DB reads and
   `deps.registry` for remote fetch.
3. Returns same payload shape (`candidate_id`, `name`, `content`,
   `env_vars`).

### `install` (mutating; interactive only)
1. LLM calls `skills(operation="install", candidate_id="...")`.
2. Builder dispatches; handler instantiates `SkillInstallService` (closes
   over `catalog_session`, `registry`, `catalog.cache`, `org_id`,
   `org_slug`, `workspace_id`, `actor_user_id=ctx.user_id`).
3. Calls `install(candidate_id)`. Returns `{installed, canonical_name,
   version}` (same shape as today).

## Error Handling

Existing error shapes preserved:

| Today | After |
|---|---|
| `BAD_CANDIDATE_ID` text + `is_error=True` | Same |
| `SkillInstallError` text + `is_error=True` | Same — caught in handler, mapped to `ActionInvalidInput` so the builder maps it to `is_error=True` with the same text |
| `SKILL_NOT_FOUND` / `SOURCE_NOT_FOUND` / `REMOTE_FETCH_FAILED` / `SKILL_MD_MISSING` / `INVALID_UTF8` | Same — returned from preview handler via `ActionInvalidInput` |

Mapping `SkillInstallError` → `ActionInvalidInput` (rather than introducing
a new domain exception) is a deliberate simplification: the builder maps
all three domain exceptions to `is_error=True` text, so the wire format
is unchanged.

## Testing

- **Existing tool tests** — `tests/unit/test_*_skill*.py` and any
  test that imports from `tools/builtin/{find,preview,install}_skills.py`
  must be updated to use the new capability path or deleted if redundant
  with capability-level tests.
- **Capability unit tests** — `tests/unit/test_skills_capability.py`:
  - `find` handler returns candidates from a fake `SkillDiscoveryService`.
  - `preview` handler returns content for a local candidate and for a
    remote candidate; errors for bad candidate_id, missing skill,
    fetch failure.
  - `install` handler returns `{installed: True, ...}` on success and
    `is_error=True` on `SkillInstallError`.
- **Mutation gate** — assert `build_skills_capability(...)` builds with
  `find`/`preview`/`install`; assert
  `build_capability_tool(..., allow_mutations=False)` drops `install`.
- **Builder regression** — existing
  `tests/unit/test_agent_action_builder.py` stays green (no builder
  changes).
- **Integration smoke** — at least one test exercises a full run with
  the skills capability registered (mirrors the existing build pattern).

## Risks

- **DI complexity in handler closures.** Closures capture three references
  (catalog, session, registry, plus org_slug). Mitigated by `SkillDeps`
  dataclass — one named bundle, not four loose args.
- **Catalog session lifetime.** `catalog_session` lives for the duration
  of the run (today's behavior). `find` and `preview` reuse it; `install`
  receives a fresh session from `ContextFactory` for its own writes
  (matching today's `_make_install_factory` which used `catalog_session`
  for its own writes too — this is a slight tightening for transactional
  hygiene, identical to scheduled-tasks).
- **Behavior regression.** Mitigated by lifting `preview_skill.py`'s
  logic verbatim into the capability module and by capability-level tests
  that assert the same payload shapes.

### Resolved by design

- **Mutation gate for `install`.** Achieved by `mutates=True` on the
  operation; the builder excludes it from automated runs structurally.

## Out-of-scope follow-ups

- Migrating `load_skill` to the actions mechanism (would require modeling
  middleware coupling, which is out of scope here).
- Migrating other capabilities (conversations, MCP connectors, memory)
  — separate spec each.
