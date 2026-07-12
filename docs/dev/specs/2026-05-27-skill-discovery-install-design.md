# Conversational Skill Discovery & Install (#151)

Status: draft spec (design only — no code)
Date: 2026-05-27
Issue: #151
Related: #143 (progressive disclosure of the skill index)

---

## Problem & Motivation

cubeplex already has a working skills system: an agent can `load_skill(name)` to
pull a skill's `SKILL.md` into its system prompt mid-run. But the agent can only
load skills that are *already enabled in the workspace*. Today a skill gets into
a workspace through admin flows — an admin uploads a `.zip`, installs it org-wide,
and binds it to workspaces. A regular user chatting with the agent has no way to
say "I need to build a slide deck — is there a skill for that?" and get it.

Two gaps:

1. **No discovery surface.** The agent sees only the enabled set. It can't
   search a wider catalog — preinstalled or own-org skills not yet enabled in
   this workspace (the `list_visible_for_org` scope), or remote registries like
   `skills.sh` — for something that *would* help. Local discovery never reads
   other orgs' rows; cross-org reach is only via explicit remote sources.
2. **No in-chat install.** Even when a relevant skill exists in the catalog,
   pulling it into the workspace requires leaving chat and using the admin UI.

We want the natural flow: user describes a need in plain language → agent
searches available skills (built-in catalog + remote sources) → returns
candidates with descriptions → on the user's confirmation, one-click installs
into the right scope → the skill is immediately loadable in the same
conversation.

This also feeds #143: the available-skills list injected into the prompt grows
as more skills exist, so discovery and the prompt index both need an on-demand
("search/expand when asked", not "dump everything") shape.

---

## Goals / Non-goals

### Goals

- A **discovery tool** the agent calls to search skills by a natural-language
  need, returning ranked candidates (name, description, source, trust signal).
- Search spans **multiple sources**: the local catalog (preinstalled + org
  uploaded) and at least one **remote registry** (`skills.sh` via `npx skills`).
- A **preview → confirm → install** flow that runs from chat, scoped correctly
  (workspace-private by default), with the install becoming **immediately
  loadable** in the live run.
- **Source management**: an operator/admin can register, list, and disable
  remote sources; built-in catalog is always present.
- Trust/review surfaced at confirm time (source reputation, who can install
  into which scope).
- Reuse the existing load path (`load_skill` + `SkillsMiddleware`) — installing
  a skill just makes a normal catalog row that the existing machinery picks up.

### Non-goals (this spec)

- No automatic/unattended install (the agent never installs without explicit
  user confirmation in v1).
- No sandboxed *execution* of remote skill scripts at install time — we store
  files; execution still happens only inside the agent sandbox at use time.
- No new ranking ML model. v1 ranking is **keyword + trust signal only**
  (OQ-4 resolved); embedding/semantic search is a future enhancement behind
  the same `SkillSource` interface.
- **No per-user (personal) skill scope in v1.** Deferred to align with #153
  managed agents introducing user-pinned definitions; v1 ships
  workspace-private only (OQ-3 resolved).
- No changes to how skill *files* are stored (object store layout stays).

---

## Current Skills System (what already exists)

### Data model — `backend/cubeplex/models/skill.py`

- `Skill` — global catalog row. `source` is `preinstalled` (owner_org_id=NULL,
  bare slug name) or `uploaded` (owner org, `<org-slug>:<skill-slug>` name).
  Carries `description`, `keywords` (JSON list), `current_version`,
  `deprecated_at`.
- `SkillVersion` — immutable per-version row: `description`, `keywords`,
  `raw_metadata`, `storage_prefix` (object-store path), `entry_file`
  (`SKILL.md`).
- `OrgSkillInstall` — an install of a catalog skill into an org. `workspace_id`
  NULL = org-wide; set = workspace-private. Has `installed_version`,
  `auto_bind`.
- `WorkspaceSkillBinding` — per-workspace enable/disable of an org-wide install
  (composite PK, no public id).
- `OrgPreinstalledTombstone` — records an admin uninstalling a preinstalled
  skill so reseed won't restore it.

### Scopes (already enforced)

- Catalog visibility: `SkillRepository.list_visible_for_org` = all preinstalled
  + own-org uploaded, minus deprecated (`backend/cubeplex/repositories/skill.py`).
- Effective workspace-enabled set:
  `SkillCatalogService.list_enabled_for_workspace`
  (`backend/cubeplex/skills/service.py`) = org-wide installs that are auto-bound
  or explicitly enabled (and not explicitly disabled) **plus** workspace-private
  installs (always on).

### Index / load mechanism (the existing progressive-disclosure-ish path)

- At run start, `run_manager.py` (~line 1804) calls
  `list_enabled_for_workspace`, formats `- \`name\` — description` lines, and
  appends them via `SKILLS_PROMPT_TEMPLATE`
  (`backend/cubeplex/prompts/skills.py`) as a **stable suffix** to the system
  prompt (cache-prefix discipline).
- The agent calls `load_skill(name)`
  (`backend/cubeplex/tools/builtin/load_skill.py`). It resolves the name via
  `find_enabled_by_name`, fetches `SKILL.md`, returns JSON `LoadSkillOutput`.
- `SkillsMiddleware` (`backend/cubeplex/middleware/skills.py`) watches
  `after_tool_call` for `load_skill`, stores content in
  `extra["loaded_skills"]`, and on each subsequent model call appends it to the
  system prompt via `transform_system_prompt`. State persists for the run.

### Seeding — `backend/cubeplex/seeders/skill_seeder.py`

Walks a `preinstalled/` dir, parses `SKILL.md` frontmatter, upserts `Skill` +
`SkillVersion`, uploads files to the object store. Redis-locked (multi-replica
safe). Deprecates preinstalled skills no longer on disk.

### Routes (scope-isolated today)

- Member: `backend/cubeplex/api/routes/v1/ws_skills.py` —
  `GET /ws/{ws}/skills` (scopes `workspace|org|catalog`, with `q`/`tag`
  filters), preview, file fetch, member publish.
- Admin: `backend/cubeplex/api/routes/v1/admin_skills.py` —
  `/admin/skills` list/detail/version, install/patch/uninstall, upload, plus
  `/admin/workspaces/{ws}/skills` binding management.

**Note:** discovery primitives (catalog list + `q` keyword filter) already
exist on the member route. This feature builds *on top of* them, adding remote
sources, a ranked agent-facing search tool, and an in-chat install.

---

## Research (registries + conversational install patterns)

The clear prior art is the **`vercel-labs/skills` ecosystem** (`npx skills`,
the `skills.sh` directory) and **Claude Code plugin marketplaces**.

### How registries expose discovery metadata

- `npx skills` uses **GitHub as the registry**, not a package server. A skill is
  a directory containing `SKILL.md` (with frontmatter: name, description,
  keywords) plus sibling files. Skills install from a repo or a **repo subpath**
  (`npx skills add owner/repo --skill <name>`, or a `tree/main/skills/<name>`
  URL). [vercel-labs/skills], [Vercel KB].
- `skills.sh` is a searchable directory; `npx skills find` queries it and lets
  you browse/install interactively. The directory ranks/sorts by **install
  count, source reputation, and GitHub stars**. [skills.sh find-skills],
  [Vercel changelog v1.1.1].
- Claude Code **plugin marketplaces** are defined by a
  `.claude-plugin/marketplace.json` listing plugins; each plugin has
  `plugin.json` metadata (name, description, version, author) and a `skills/`
  dir. A "Discover" tab surfaces plugins from connected marketplaces; Anthropic
  ships an official one by default. Community directories index thousands of
  skills daily. [claude-code plugin-marketplaces], [claude-plugins-official].

The shape that matters for us: **frontmatter is the discovery metadata**
(name + description + keywords), and a registry is just *a list of skills with
that metadata plus trust signals*, reachable over HTTP or via a CLI.

### Conversational install patterns

- Vercel's `find-skills` skill is itself a conversational-discovery pattern: it
  triggers on "how do I do X / find a skill for X / is there a skill that…",
  searches the directory, **presents candidates with install commands and links
  for the user to review**, and only then installs. [skills.sh find-skills],
  [find/search DeepWiki].
- It explicitly folds **trust into the recommendation**: official sources
  (vercel-labs, anthropics, microsoft) rank higher; a repo with <100 stars is
  treated with skepticism. [skills.sh find-skills].
- Install **scope** is explicit: project-local `skills/` vs global/user
  (`-g`), with a confirm step (`-y` to skip). [Vercel KB].

Takeaway for cubeplex: model discovery as **one agent tool that fans out over
sources, normalizes to one candidate shape, ranks with a trust signal, and
returns a short list**; model install as **an explicit confirm step that copies
the chosen skill into the local catalog and installs it into a scope**, then let
the existing load path light it up.

### Semantic vs keyword search

The directory tooling is primarily keyword/metadata + popularity ranking, not
embeddings. For cubeplex v1 we follow suit: keyword match over
name/description/keywords, ordered by a trust/recency signal. Semantic
(embedding) search is a later enhancement (Open Questions) — descriptions are
short and curated, so keyword recall is usually adequate, and embeddings add a
model dependency + index to maintain.

---

## Proposed Design

### 1. Source / registry abstraction

Introduce a `SkillSource` interface with two responsibilities: **search** (given
a query, return candidate skills with metadata + trust signal) and **fetch**
(given a candidate id, return its files for import into the catalog). Two
implementations in v1:

- **`LocalCatalogSource`** — wraps `SkillRepository.list_visible_for_org` +
  `SkillVersionRepository`. Candidates are catalog rows not yet enabled in the
  asking workspace. "Fetch" is a no-op (files already in our object store);
  install just creates the `OrgSkillInstall` row.
- **`RemoteRegistrySource`** — talks to a registry (the `skills.sh` directory /
  a configured GitHub-backed registry, the same shape `npx skills` consumes).
  Search hits the directory's query endpoint; fetch downloads the skill
  directory (repo subpath) so we can import it into our catalog as an
  `uploaded`-style row owned by the installing org. The imported row carries
  the upstream `source_ref` + a `last_imported_at` timestamp so the
  "Check for update" button (OQ-6 resolved) can re-pull on demand.

A `SkillSourceRegistry` holds the configured sources. The local source is always
present. Remote sources are **config/DB-driven**, never hardcoded — register a
source by `{kind, base_url/repo, trust_tier, enabled}`. This mirrors the
pluggable-backend pattern used elsewhere (unified interface + per-kind subclass
+ factory + config-driven selection).

Each candidate normalizes to: `candidate_id, name, description, keywords,
source_kind, source_ref, version, trust` where `trust` carries the signals
research surfaced (official-source flag, stars/install-count if the registry
exposes them, "already in your org catalog" boolean).

`candidate_id` is an **opaque, URL-safe, HMAC-signed** token (OQ-7 resolved):
`base64url(<hmac_signed>(source_kind + ":" + source_ref))` over the existing
token-signing keyring. Stateless — no DB row, no expiry, no GC; rotation is
done by rotating the HMAC signing key. The token is the only handle clients
pass back to preview/install. We do **not** route on `source_ref`: a remote
`source_ref` is a GitHub repo/subpath like `owner/repo/tree/main/skills/foo`,
full of slashes that won't fit one FastAPI path segment (the route would 404 or
truncate the ref). The signed token sidesteps that entirely — it carries no
slashes, is safe in a query string or JSON body, and the signature also pins
the candidate to this server's keyring so a client can't forge a
`(source_kind, source_ref)` pair on the install endpoint. `source_ref` stays
in the candidate payload for display and for the eventual import, but never
appears in a URL path.

`name` here is the human-facing display name (for remote candidates the upstream
skill slug). It is **not** necessarily the name `load_skill` resolves against. The
catalog stores each skill under a canonical `Skill.name`, and for an imported
remote skill that canonical name is `<org-slug>:<skill-slug>` (the namespaced form
the import path mints), not the bare upstream slug. So the candidate also carries a
`canonical_name` field: for local candidates it's the existing catalog name; for
remote candidates that aren't imported yet it's the name the import **will** produce
(`<org-slug>:<skill-slug>`), computed up front from the installing org's slug. Every
later step (install response, the "load it now" hint) uses `canonical_name`, never
the display `name`, because `load_skill` resolves by exact canonical name via
`find_enabled_by_name`.

### 2. Discovery — an agent tool

Add a builtin tool `find_skills(query, [limit])` (sits next to `load_skill` in
`run_manager.py`'s tool list). It:

1. Fans out the query across enabled sources via `SkillSourceRegistry`.
2. Merges + de-dupes candidates (same name across sources → prefer local /
   higher trust).
3. Ranks: exact/keyword match first, then trust tier, then install-count/stars.
   (v1 keyword; semantic is a later swap behind the same interface.)
4. Returns a short ranked list (default ~5) of `{candidate_id, name,
   canonical_name, description, source, trust, install_state}` — the
   `candidate_id` is the opaque handle later passed to preview/install;
   **descriptions only, not full
   SKILL.md** (keeps it cheap; the model previews on demand). This is the
   discovery counterpart to #143's on-demand index.

The tool is **read-only**: it never installs. It returns candidates and, for
already-enabled skills, tells the agent it can `load_skill(canonical_name)`
directly — using `canonical_name`, not the display `name`, so the call resolves.

### 3. Preview → confirm → install flow

- **Preview.** Agent (or the user via UI) previews a candidate: for local
  candidates reuse the existing preview route; for remote candidates fetch the
  `SKILL.md` (without importing yet) so the user sees what they'd get.
- **Confirm.** Install is **never silent and never agent-initiated.** The
  user starts every install via one of two surfaces (OQ-5 resolved), both
  authenticated and both calling the same `POST …/skills/install` endpoint:
  - **UI button** on the candidate card in the workspace skills page —
    primary path. A click is the confirm.
  - **Chat fallback** — the user types `install <canonical_name>` into the
    conversation. The conversation route parses that user message
    server-side and calls the install endpoint before the agent loop sees
    the message. The agent itself never autonomously calls `install_skill`;
    `find_skills` is strictly read-only.

  This does **not** depend on cubepi HITL — install is always user-initiated,
  there is no agent-pause-for-approval moment. Autonomous agent-initiated
  install (agent says "I need this" → human approves → install runs) is
  explicitly out of v1 scope and listed under Future Work.
- **Install.** On confirm, an install service:
  - **Local candidate:** create the workspace-private `OrgSkillInstall`
    (reusing `create_for_workspace`, `auto_bind=True`).
  - **Remote candidate:** import the fetched files into the object store as a
    new `uploaded` catalog skill owned by the installing org (reusing
    `SkillPublishService` / `publish_from_zip`-style path), then create the
    workspace-private install. The import mints the canonical `Skill.name` as
    `<org-slug>:<skill-slug>`.
- **Install response returns the canonical name.** The install service returns the
  actual installed `Skill.name` it created — the canonical `<org-slug>:<skill-slug>`
  for remote imports, the existing catalog name for local. The agent must use this
  returned name (which equals the candidate's pre-computed `canonical_name`) for the
  follow-up `load_skill`, not the bare display name.
- **Immediately loadable.** Because install produces a normal catalog +
  workspace-private install row, the next `load_skill(canonical_name)` resolves it
  via `find_enabled_by_name`. For the *current* run, the agent re-queries the enabled
  set after a successful install so the freshly installed skill is visible without a
  new conversation (the available-skills suffix is recomputed; loaded content still
  flows through `SkillsMiddleware` as today).

### 4. Install scope & isolation + trust/review

- **Default scope = workspace-private** (`OrgSkillInstall.workspace_id` set).
  This is the least-blast-radius scope and the one a member can self-serve. It's
  visible only to that workspace and always-enabled there.
- **Org-wide install** (workspace_id NULL, possibly auto-bind) stays an
  **admin** action — same trust posture as today's admin marketplace. The
  conversational flow for a member targets workspace-private only; promoting to
  org-wide remains an admin decision via the existing admin route.
- **Who can install what:** a member can install into their own workspace; only
  org admins can install org-wide or register remote sources. This matches the
  existing member-vs-admin route split.
- **Trust/review (signal only, no enforcement in v1; OQ-1 resolved).** Remote
  candidates carry a trust tier from their source config + registry signals.
  The confirm card shows: source name, author/repo, stars/installs, and a
  clear **"unvetted" banner** for any remote source that isn't `official`.
  Admins can pin a remote source to a trust tier or disable it entirely. v1
  does **not** machine-enforce a source allowlist, run a content scan, or
  require admin approval for remote imports — those land later in a dedicated
  security/gating module (see Future Work). What v1 ships is the user-visible
  trust signal so users see what they're pulling in. We **store** remote
  skill files but never execute them at install time — execution only happens
  inside the agent sandbox at use time, subject to the same #144
  `command_rules` as any other skill (OQ-2 resolved: no isolation tier).

### 5. Synergy with #143 (progressive disclosure)

The available-skills list in the system prompt (`SKILLS_PROMPT_TEMPLATE`) grows
with the enabled set. #143 wants that index expandable on demand rather than
fully inlined. This feature aligns:

- `find_skills` is exactly the "expand the index on demand" tool for the
  *catalog/remote* space — the agent doesn't carry the whole catalog in-prompt;
  it searches when a need arises.
- For the *enabled* set, the same principle applies: if the enabled list gets
  large, #143 can fold it behind the same search affordance (search enabled
  skills first, remote second). The two features should share one candidate
  shape and one ranking path so #143 doesn't reinvent discovery.

### 6. Scope-isolated routes

Following the repo's scope-isolation rule (separate handlers per audience, reuse
one layer down in services):

- **Member (workspace) routes** under `/api/v1/ws/{ws}/skills/`:
  - `GET …/discover?q=` — ranked search across sources (powers both the
    `find_skills` tool and the chat UI); each result carries its opaque
    `candidate_id`.
  - `GET …/discover/preview?candidate_id=` — preview a remote candidate
    without importing. The opaque `candidate_id` rides in the query string,
    so the slash-laden remote `source_ref` never has to fit a path segment.
  - `POST …/install` — install a chosen candidate into **this workspace**
    (workspace-private). Body carries `{candidate_id}` (the opaque handle),
    not the raw ref. Authenticated user action = the "confirm".
  - `POST …/{skill_id}/refresh` — on-demand re-import of a remote-imported
    skill from its stored `source_ref` (OQ-6 manual re-import). Powers the
    "Check for update" button on the workspace skills page.
- **Admin routes** under `/api/v1/admin/skills/` and
  `/api/v1/admin/skill-sources/`:
  - source management (register/list/enable/disable remote sources, set trust
    tier).
  - org-wide install of a discovered/imported skill.

Shared logic (`SkillSourceRegistry`, ranking, import-from-remote) lives in
services/repositories, never parameterized at the route layer. No `?scope=` or
`role` body field.

### 7. v1 scope

- `SkillSource` interface + `LocalCatalogSource` + one `RemoteRegistrySource`
  (skills.sh / configured GitHub-backed registry).
- `find_skills` agent tool (keyword ranking + trust signal, descriptions only).
- Member `discover` + workspace-private `install` + `refresh` (re-import on
  demand) routes; remote-candidate import via existing publish path.
- Admin source-management routes; admin org-wide install reuses existing route.
- In-run "recompute enabled set after install" so the skill is loadable in the
  same conversation.
- **Workspace skills page** at `/w/[wsId]/skills` — list of skills visible to
  this workspace (preinstalled + own-org uploads + remote-imported), a
  "Discover skills" panel with the search bar + ranked candidate cards, an
  **Install** button per card backed by the install endpoint, an **"unvetted"
  badge** on remote sources, and a **"Check for update"** button on the detail
  of each remote-imported skill that re-pulls its `source_ref`.
- **Chat fallback** — the conversation route detects a user message matching
  `install <canonical_name>` and calls the same install endpoint server-side
  before the agent loop sees the message. The agent never autonomously calls
  install.
- **Local-wins on bare slug** with same-name remote variant coexisting under
  `<source>:<slug>` (OQ-8 resolved).

Deferred (see Future Work): semantic/embedding search, personal (per-user)
scope, automated update polling, source allowlist + content-scan + admin
approval queue, agent-initiated install via HITL, multiple remote registries
with cross-source dedupe heuristics beyond name match.

---

## Testing Strategy (E2E-first)

Per repo discipline, lead with E2E and fall back to unit only where a real
system can't be simulated.

- **E2E (primary):**
  - User asks the agent in chat for a capability that maps to a
    not-yet-enabled **local catalog** skill → agent calls `find_skills` →
    candidate surfaced → user confirms install → `load_skill` succeeds in the
    same conversation and the skill content shows up in the run.
  - Admin registers a **remote source** (pointed at a local fake registry that
    serves real `SKILL.md` + files over HTTP — a faithful stand-in, not a mock
    of our own code) → member discovers a remote candidate → previews →
    confirms → it imports into the org catalog and installs workspace-private →
    becomes loadable.
  - Scope isolation: a workspace-private install in workspace A is **not**
    visible in workspace B; a member cannot install org-wide.
  - Trust: an untrusted remote candidate shows the warning banner; a disabled
    source returns no candidates.
- **Unit (where E2E can't reach cleanly):**
  - Ranking/merge/dedupe logic (exact > keyword > trust > popularity;
    same-name-across-sources collapse).
  - `RemoteRegistrySource` parsing of registry metadata + subpath fetch (the
    `npx skills` subpath quirk — issue #1015 — is a known footgun; pin the
    subpath explicitly so we fetch one skill, not a whole repo).
  - Candidate normalization across source kinds.
- Reuse the existing skill-seeder + object-store test fixtures; the local fake
  registry is the only new test harness piece.

---

## Open Questions

1. **Trust/security of remote skills.** A `SKILL.md` is instructions the model
   will follow, and sibling files may include scripts the sandbox runs on use.
   What's the minimum vetting before an org member can pull a remote skill into
   their workspace — allowlist of sources only? admin approval queue for remote
   imports? content scan of `SKILL.md` for prompt-injection patterns?
   **Resolved 2026-05-28: deferred to a future dedicated security/gating
   module.** v1 ships **no machine-enforced gate** — no source allowlist, no
   content scan, no admin approval queue. The only trust signal at the user
   layer is the visible **"unvetted" badge** on remote-imported skill cards and
   detail pages, so a user sees what they're pulling in. A separate
   security-module PR will later add the actual enforcement (allowlist,
   approval queue, injection scan). See **Future Work** below.
2. **Sandboxing skill content at use time.** Remote skill scripts run in the
   agent sandbox like any other skill. Do we need an extra isolation tier for
   skills sourced from untrusted registries vs preinstalled ones?
   **Resolved 2026-05-28: no isolation tier in v1.** All skills share one
   sandbox posture; remote skill scripts run subject to the same #144
   `command_rules` as any other skill. The assumption is explicit: a remote
   skill's executable surface is gated by the existing sandbox + command-rules
   discipline, not by a remote-only extra tier.
3. **Personal vs workspace scope.** Issue #151 mentions "workspace/personal
   scope," but the current model has no per-user skill scope.
   **Resolved 2026-05-28: workspace-private only in v1.** Personal scope is
   deferred until #153 (managed agents) lands the user-pinned definition
   concept, so personal-skill semantics line up with that model rather than
   being invented twice.
4. **Keyword vs semantic search.**
   **Resolved 2026-05-28: keyword + trust ranking only in v1.** No embedding
   index; an embedding swap stays a future enhancement behind the same
   `SkillSource` interface.
5. **Confirmation surface.**
   **Resolved 2026-05-28: two user-initiated surfaces, both backed by the same
   install endpoint.** (a) Primary UI path — an **Install** button on the
   candidate card on the workspace skills page (authenticated user click =
   the confirm). (b) Chat fallback — the user types `install <canonical_name>`
   into the conversation; the conversation route parses that user message
   server-side and calls the same install endpoint before the agent loop sees
   it. The agent itself **never autonomously calls** `install_skill`; the
   `find_skills` tool is strictly read-only.

   **Why this does NOT depend on cubepi HITL.** Install is always
   user-initiated — either a click or a literal user-typed command. There is
   no agent-driven moment where the system pauses, the user approves, and the
   agent then proceeds with install. So no human-in-the-loop pause primitive
   is needed; the two surfaces are just two ways the user *starts* the
   install. **Autonomous agent-initiated install** ("the agent decides it
   needs this skill, asks the human to approve, then installs") **would**
   require HITL and is **explicitly out of v1 scope** — it's listed in Future
   Work, gated on cubepi shipping HITL first.
6. **Remote-import freshness.**
   **Resolved 2026-05-28: manual re-import only in v1.** We store `source_ref`
   plus a `last_imported_at` on the imported skill row, and the skill detail
   page exposes a **"Check for update"** button that, on click, re-pulls the
   same `source_ref` from the same registered source and creates a new
   version row. No automatic polling, no background freshness job.
7. **Candidate-id encoding + lifetime.**
   **Resolved 2026-05-28: stateless HMAC-signed token.** `candidate_id` is
   `base64url(<hmac_signed>(source_kind + ":" + source_ref))` — no DB row, no
   GC, no expiry beyond key rotation. Rotation is done by rotating the HMAC
   signing key in the existing token-signing infra (same key-ring used for
   other server-signed tokens). A signed token also pins the candidate to
   this server's keyring, so a client can't forge `(source_kind, source_ref)`
   on the install endpoint.
8. **Duplicate/name collisions.**
   **Resolved 2026-05-28: local-wins on bare-slug, remote variant coexists
   under a namespaced canonical.** Bare-slug `load_skill <name>` always
   resolves to the local skill when one exists. A user MAY install a same-name
   remote variant; it's stored under canonical name `<source>:<slug>` and
   shows up alongside the local in the skills list. Explicit
   `load_skill <source>:<slug>` selects the remote variant; bare-slug never
   resolves to the remote when the local exists.

---

## Future Work

These are deliberately out of v1 scope and tracked here so reviewers don't
re-litigate them at PR time.

- **Source allowlist + content-scan + admin approval queue** — a dedicated
  security/gating module that decides which remote sources an org is allowed
  to import from, scans `SKILL.md` for known prompt-injection patterns, and
  optionally queues remote imports for admin approval. Replaces the v1
  "unvetted" badge with actual enforcement. (Resolves OQ-1.)
- **Personal-scope skills** — user-pinned skill installs that follow the
  user across workspaces. Lands together with #153 (managed agents)
  introducing user-pinned definitions, so personal-skill semantics match.
  (Resolves OQ-3.)
- **Embedding-based semantic search** — a vector index over skill name +
  description + keywords, swapped in behind the existing `SkillSource`
  interface. (Resolves OQ-4.)
- **Automated update polling** — background freshness checks against
  `source_ref` with admin-controlled cadence and a notification surface
  when an imported skill has a new upstream version. Replaces the v1 manual
  "Check for update" button. (Resolves OQ-6.)
- **Agent-initiated install via HITL** — the agent says "I need skill X to
  do this," the user approves once in chat (cubepi human-in-the-loop pause),
  and the install runs against the same endpoint. Strictly gated on cubepi
  shipping HITL. (Extends OQ-5.)

---

## References

- vercel-labs/skills (`npx skills`, open agent skills tool):
  https://github.com/vercel-labs/skills
- Subpath fetch / update bug (issue #1015):
  https://github.com/vercel-labs/skills/issues/1015
- `find-skills` skill (conversational discovery + trust ranking):
  https://github.com/vercel-labs/skills/blob/main/skills/find-skills/SKILL.md
  and https://www.skills.sh/vercel-labs/skills/find-skills
- find / search internals:
  https://deepwiki.com/vercel-labs/skills/4.4-find-search
- Vercel KB — creating/installing/sharing agent skills (scopes, subpaths):
  https://vercel.com/kb/guide/agent-skills-creating-installing-and-sharing-reusable-agent-context
- Skills v1.1.1 — interactive discovery:
  https://vercel.com/changelog/skills-v1-1-1-interactive-discovery-open-source-release-and-agent-support
- Claude Code plugin marketplaces (marketplace.json / plugin.json / Discover):
  https://code.claude.com/docs/en/plugin-marketplaces
- Plugin marketplace & discovery internals:
  https://deepwiki.com/anthropics/claude-code/4.1-plugin-marketplace-and-discovery
- Official Anthropic plugin directory:
  https://github.com/anthropics/claude-plugins-official
