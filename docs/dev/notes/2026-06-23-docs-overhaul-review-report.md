# CubeBox Docs Overhaul — Review & Correction Report

**Date:** 2026-06-23
**Branch:** `feat/2026-06-23-docs-overhaul`
**Method:** A `Workflow` fan-out (9 per-module reviewers + 1 synthesizer) verified
every page of `docs/site/docs/` against the backend/frontend code and corrected
drift in place. This note is the synthesizer's report plus the human-decision
list. See the plan: [2026-06-23-docs-overhaul.md](../plans/2026-06-23-docs-overhaul.md).

**Verification:** `pnpm build` in `docs/site` passes for both `en` and `zh-Hans`
locales (config sets `onBrokenLinks: throw` / `onBrokenAnchors: throw`), so all
internal links and anchors resolve. One MDX bug introduced during the pass
(`{enabled}` brace-expression in a placeholder) was caught by the build and
fixed.

---

## Overview

This pass corrected factual drift across nine documentation modules and authored
a brand-new IM Connectors surface. The dominant theme was **navigation and
naming drift**: leaked Next.js route-group paths (`/(app)/w/{id}/...`) were
replaced with real user-facing URLs, fabricated nav vocabulary
(`Settings > Models`, `Workspace Settings > MCP`, `Organization Settings >`) was
repointed to the actual Admin panel and top-level workspace sidebar pages, and
UI labels were aligned to the real i18n strings (`Extra High`, `New task`,
`Add to workspace`, `Re-authenticate`). Several **fabricated or invented
mechanisms** were removed entirely — an email-invite member flow, a
transfer-ownership action, a configurable missed-run policy, an org-level skill
enable/disable toggle, workspace-level model restrictions, and CPU/memory
resource limits — and replaced with what the code actually does. The IM module
went from zero coverage to an overview plus a full Feishu setup guide, wired into
the sidebar. Event Triggers (hand-fixed in step 1) needed no further corrections.

## Highest-impact factual corrections

| Module | Was wrong | Fix | Evidence |
|---|---|---|---|
| Getting Started | First self-hosted user "instantly creates org & becomes owner"; others wait for admin approval | One-time setup screen (name org + slug) → owner; later users join as members with personal workspace | `auth/users.py:86,230`; `auth.md:69-86`; `RegisterForm.tsx:31-34` |
| Getting Started | Model setup at `Settings > Models` | `Admin > Models > Model Providers` (via avatar menu) | `app/admin/models`; `AdminSubNav.tsx:132-136` |
| Conversations | Failover banner used hard-coded dated model IDs | Generic `<provider>/<model>` refs; exact text `Failover exhausted on …` | `FailoverBanner.tsx:26-28` |
| Conversations | Artifacts created only by `save_artifact` | Both `save_artifact` and `generate_image` produce artifacts | `agents/stream.py:87` |
| Skills | Trust tier "Unvetted"; "remote registry" as a stored source type | "Untrusted" (enum); source is `preinstalled\|uploaded`, remote stored as uploaded w/ registry ref | `skills/sources/base.py:26-29`; `models/skill.py:27,31-34` |
| Skills (admin) | Org-level enable/disable toggle removing a skill from all workspaces | Org install/uninstall + auto-bind, plus per-workspace enable/disable bindings | `admin_skills.py:381-472,577-613` |
| Memory | Workspace/org edits restricted to admins or item creator | Any member who can view an item can archive/update it | `repositories/memory.py:53-60`; `services/memory.py:48-54` |
| Memory | Proactive saves default to workspace scope | Agent always saves **personal**; workspace/org only on explicit request | `prompts/memory.py:28-40` |
| Memory | "Delete" permanently removes an item | Memory Center only archives; API delete is a soft-delete | `api/routes/v1/memory.py:139-153` |
| MCP (admin) | Fabricated Auth tab with client ID/secret + redirect URI fields | DCR auto-registers; static clients need deploy-time env-var/seeder; tabs are Overview/Tools/Citations/Workspaces | `MCPAdminDetailPanel.tsx:312-382`; `template_seed.py:8-11` |
| MCP | DCR-auto OAuth list omitted Atlassian | Added Atlassian | `template_seed.py:174` |
| Scheduled Tasks | Configurable "missed-run policy" (Skip / Run latest) | No such field; only latest due occurrence catches up, older → Skipped (missed) | `schedules/compute.py:17-19`; `poller.py:53,160-211` |
| Scheduled Tasks | "Three schedule kinds" incl. raw cron the user types | Visual builder: Daily/Weekly/Monthly/Every…/Once; cron is storage only | `ScheduleEditor.tsx:19-27` |
| Admin (members) | Email-invite flow with sign-up link | "Add members" adds an existing CubeBox user by email; fails if no account | `admin_members.py:90-99` |
| Admin (members) | "Transfer Ownership" action | No such endpoint; owner role unchangeable, owner unremovable | `admin_members.py:120,142` |
| Admin (sandbox) | Fixed runtime language list | Languages depend on configured `default_image`; agent runs via shell `execute` | `prompts/artifacts.py:13`; `models/sandbox_policy.py:60` |
| Admin (sandbox) | Resource limits control CPU/memory/duration | Real feature is Command rules (allow/deny/confirm HITL) + per-run timeout | `models/sandbox_policy.py:78-80` |
| IM (new) | Zero IM coverage | New overview + Feishu setup guide | `im/feishu/signature.py:87-105`; `im_ingress.py:117` |

---

## Residual gaps — human decisions required

### Cross-module: ratify the navigation vocabulary
A single nav vocabulary was adopted and should be confirmed:
- **Org-scoped** → `Admin > <Section>` via avatar menu → "Admin panel" → `/admin/...`
- **Workspace features** → top-level sidebar pages by name (Skills, MCP, Scheduled Tasks, Triggers, Memory, Artifacts)
- **Workspace Settings sub-views** → `Settings > <Tab>` (General / IM / Memory / Sandbox env / Members / Shares)
- Banned: `Organization Settings >`, `Workspace Settings > Models/MCP/Skills/Automation`, bare `Settings > Models`.

### Cross-module: i18n decision
`zh-Hans` builds but has **no translations** (no `i18n/` dir) — Chinese users get
the language switcher and English content. Also `SettingsTabs.tsx` has
`navMemory` / `navModel` label keys whose tabs are **not rendered**. Decide:
translate vs. drop the locale; and whether those keys are dead (remove) or
pending features.

### Per-module unverified claims

**Resolved (owner confirmed 2026-06-24):**
- **Skills — "Deprecate a skill"** → ✅ removed. No such feature exists; section deleted from `admin/skills-management.md`.
- **Admin (sandbox) — "secrets are not logged"** → ✅ rewritten to the real mechanism. Secrets are injected as opaque `cbxref_…` **placeholders**, never the real value; the **egress proxy** substitutes the real secret at the network boundary, only for allowed hosts/headers (`sandbox_env/placeholder.py`, `services/egress_exchange.py`, `sandbox/manager.py`). So the secret never enters the sandbox or its logs. Section retitled "How secrets stay out of the sandbox" and now recommends secret-env entries for all credentials.
- **Memory — confidence "self-rated by the agent"** → ✅ confirmed correct; wording stands (default 0.8).

**Still open (left in place per honesty rule):**
- **Getting Started** — could not confirm any workspace-level model allowlist UI; the restriction claim was removed. Verify in the model-presets service/repo.
- **Skills** — version rollback documented as "install the version you want" (no dedicated rollback endpoint).
- **Memory** — "personal memory wins on conflict" could not be verified; rewritten to verifiable behavior (org→workspace→personal render order + intra-scope `correction` priority).
- **MCP** — the four catalog status labels are a simplification of `install_scope`/`auth_status`/`discovery_status`/`install_state`/`credential_availability`; exact on-screen strings need a frontend render check.
- **Scheduled Tasks** — interval **resume** first-fire wording is approximate; misfire grace window left unstated (code default 300s but constructor-overridable).
- **Event Triggers** — two overview/Tips wording issues outside the webhook facts: the "Rate limited" row omits the 429-vs-`202_drop` distinction; "excess events are queued and processed in order" is wrong (token-bucket rejects, no queue). Reword after a product decision.

### IM Connectors — all five setup guides now authored
Feishu, Slack, DingTalk, Teams, and Discord each have a dedicated page, wired into
the sidebar; `overview.md` links them and its platform/command/identity claims were
corrected against code. Authoring surfaced two **code-confirmed** facts that also
fixed the overview:
- `/new` / `/reset` only exist on **Feishu** (text) and **Discord** (native slash) —
  **not** Slack, DingTalk, or Teams (`feishu/reset_command.py`, `discord/commands.py`;
  absent elsewhere).
- Email auto-resolution is wired on **Feishu, Slack, DingTalk** (`resolve_email` →
  `identity_resolver` in each gateway); **Discord/Teams** use `/link`. The `绑定`
  alias is Feishu-only; DingTalk has no Chinese alias.

Remaining (screenshots + external-console strings, not code):
- All per-platform screenshot placeholders are unfilled (Slack/Discord/Azure/DingTalk consoles).
- Exact console strings — Slack scope names, Discord intents/OAuth scopes, DingTalk permission names, Azure blade/manifest fields, Feishu scope/event keys — are described by capability (they live in each vendor's console, not in code). Confirm during screenshot capture.
- Channel-binding "shared" mode `sandbox_mode` values still not enumerated — verify in `api/schemas/im_channel_binding.py`.

---

## Screenshots to capture (28 placeholders added)

Capture against a seeded demo workspace; store under `docs/site/static/img/`
matching each page path. Grouped:

- **Getting Started** — one-time org setup screen; `Admin > Models > Model Providers`.
- **Conversations** — Effort slider (Off→Extra High); failover banner mid-stream.
- **Skills** — Workspace Skills page with Source dropdown; skill artifact card "Add to workspace"; Add-registry form + `.zip` upload modal.
- **Memory** — Memory Center four tabs; a memory card (badge/confidence/archive).
- **MCP** — workspace MCP page "Connect with <provider>" state; admin detail Overview/Tools/Citations/Workspaces tabs; "+ Add custom connector" dialog.
- **Scheduled Tasks** — schedule builder frequency pills; conversation-target section; run-history with six state badges.
- **Admin** — Members "Add members" dialog + owner badge; Sandbox Environment + Command rules editor; cost dashboard (granularity/dimension/export).
- **IM** — overview flow diagram + maturity table; Feishu app-create/bot-publish, binding form, event subscription.
