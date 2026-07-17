# Provider `slug` — design

**Status:** approved (brainstorm) · **Date:** 2026-05-20 · **Branch:** `feat/model-mgmt-followup`

## Problem

A model is referenced as `default_model` / `fallback_models` / `task_models`
in `OrgSettings`, stored as the string `"<provider>/<model-id>"`. Today the
`<provider>` part is the provider's **display name** (`hooks/useAllModels.ts`
builds `` `${provider.name}/${model_id}` ``; the resolver
`LLMFactory._parse_model_ref` splits on `/` and looks the provider up by name
in the merged config).

This is fragile:

- Renaming a provider silently breaks every saved ref that pointed at the old
  name — model routing falls back to the default with no error.
- Display names carry spaces / parentheses (`"DeepSeek (Anthropic shape)"`),
  which is ugly and brittle inside a `/`-delimited ref.

`preset_slug` cannot serve as the identifier: it is the cubepi catalog preset,
not unique per provider (two providers can share a preset) and `null` for
custom providers.

## Goal

Give every provider a unique, stable `slug` and key model refs on it:
`"<provider-slug>/<model-id>"`. Renames no longer break routing. Surface the
slug in the UI.

## Decisions (locked in brainstorm)

1. **Slug source:** auto-derived from `name` at create via `slugify(name)`; the
   create form pre-fills it and the user may override it. Not derived after
   create.
2. **Editability:** immutable after create. `name` may change freely; `slug`
   never does. This is what keeps refs valid forever.
3. **Existing refs:** clean cutover. A one-shot data migration rewrites the
   stored refs from `name/model-id` to `slug/model-id`; the resolver only
   understands slug afterward (no name fallback).
4. **Uniqueness scope:** unique per `(org_id, slug)`, mirroring the existing
   `(org_id, name)` constraint. System providers live in the `org_id = NULL`
   bucket. Config/seeded providers' slug is their yaml key (already their
   `name`, e.g. `deepseek`).

## Design

### Schema + migration

Add `slug: str` to the `providers` table (`Provider` SQLModel), with a unique
constraint `(org_id, slug)` (alongside the existing `(org_id, name)` one).

Migration (autogenerate the column, hand-write the backfill data step):

1. Add `slug` as nullable.
2. Backfill every existing row: `slug = slugify(name)`, de-duplicated **within
   each `org_id` bucket** by appending `-2`, `-3`, … on collision (ordered by
   `created_at` so the result is deterministic).
3. Alter `slug` to `NOT NULL` and add the `(org_id, slug)` unique constraint.

Config/seeded providers need no special case: their `name` is the yaml key, and
`slugify("deepseek") == "deepseek"`.

### `slugify` helper

A pure function (e.g. `cubeplex/utils/slug.py`): lowercase, replace any run of
non-`[a-z0-9]` with a single `-`, trim leading/trailing `-`. Empty result (name
was all punctuation) falls back to `"provider"`. Used by both the create path
and the migration backfill. Collision suffixing (`-2`, `-3`) lives in the
caller (create service + migration), not in `slugify` itself.

### API + immutability

- `ProviderCreate` gains optional `slug: str | None`. When omitted, the create
  service derives it via `slugify(name)` and applies collision suffixing within
  the org. When provided, it is validated for format (matches
  `^[a-z0-9]+(-[a-z0-9]+)*$`) and `(org_id, slug)` uniqueness → `409
  provider_slug_conflict` on clash (same error shape as the existing name
  conflict).
- `ProviderUpdate` does **not** include `slug`. Editing a provider never
  changes its slug.
- `ProviderOut` exposes `slug`.

### Resolver + merged-config keying (core change)

- `_parse_model_ref(ref)` keeps splitting on the first `/`, but the first part
  is now treated as a **slug**, returning `(slug, model_id)`.
- The merged `LLMConfig.providers` mapping is keyed by **slug** instead of
  name. For DB providers the key is the `slug` column; for config (yaml)
  providers the key is `slugify(yaml_key)`. (Today's yaml keys — `deepseek`,
  `minimax`, … — are already slug-form, so this is identity in practice; the
  seeder also stores `slug = slugify(name)` on the rows it seeds, so the two
  paths agree.) So `merged.providers[slug]` resolves in both
  `LLMFactory.resolve_default_provider_and_config` and `task_model_resolver`.
- No name fallback. This feature changes the *identifier* (name → slug), not the
  resolution fallback semantics: a ref whose slug is unknown behaves exactly as
  the pre-existing unknown-ref path did for each consumer (`default_model`
  already falls back to the global default; the task-model resolver surfaces the
  error as it does today). We do **not** add any name-matching fallback.

### One-shot ref migration (OrgSettings)

A data migration step (in the same Alembic revision, after the slug backfill)
rewrites the three ref-bearing `OrgSettings` rows **per org**:

- `default_model` → `{ "model_ref": "<slug>/<model-id>" }`
- `fallback_models` → `{ "models": ["<slug>/<model-id>", …] }`
- `task_models` → `{ "<task>": "<slug>/<model-id>", … }`

For each org it builds a `name → slug` map from that org's providers (plus the
system `org_id = NULL` providers, since refs can point at system providers) and
rewrites the provider part of each ref. Refs whose provider name no longer
resolves are left unchanged (they were already dangling).

### UI

- `ProviderOut.slug` flows into the core `Provider` type.
- The provider **detail** panel shows the slug (read-only) near the name; the
  **list** card may show it as secondary text.
- `ProviderConfigForm` (create mode only) adds an editable **Slug** field that
  auto-fills from the name as the user types (until they edit the slug field
  themselves), with the format hint. In edit mode the slug is shown read-only
  (immutable).
- `hooks/useAllModels.ts` builds `ref = ` `` `${provider.slug}/${model_id}` ``.
  The model pickers are otherwise unchanged — they already store `opt.ref`.

## Testing

- `slugify` unit tests: spaces/case/punctuation/unicode-ish, all-punctuation
  fallback.
- Create service: create without slug derives + suffixes on collision; create
  with explicit slug; duplicate slug in same org → 409; same slug across
  different orgs is allowed.
- `ProviderUpdate` ignores/omits slug (slug unchanged after an update).
- Resolver: `slug/model-id` resolves to the right provider/model; unknown slug
  falls back to default.
- Migration: a test exercising the backfill + ref rewrite on seeded + org data
  (names with spaces → slugs; an `OrgSettings.default_model` ref rewritten
  name→slug).
- E2E: create a provider via the API, confirm `slug` round-trips in
  `ProviderOut`, set it as `default_model` by its slug ref, and confirm
  resolution.

## Out of scope

- Editable slug after create (deliberately excluded — immutability is the
  point).
- Task-model routing UI, per-workspace providers, OAuth auth — separate slices.
