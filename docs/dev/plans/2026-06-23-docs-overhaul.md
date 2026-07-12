# Docs Site Overhaul — Review & Correction Plan

**Date:** 2026-06-23
**Branch:** `feat/2026-06-23-docs-overhaul`
**Scope:** `docs/site/docs/**` (the user-facing product documentation site)

---

## Goal

Bring the user-facing docs site into factual agreement with the shipped
product, close the major content gaps, fix rendering bugs, and establish a
process so docs never silently drift from code again.

This plan is a frozen snapshot. Rebase the content; don't rewrite history.

---

## How we got here

The docs site (`docs/site/`, Docusaurus) was written as one batch and is
broadly good — clear structure, consistent voice, useful per-page "Tips".
Spot-checking against the codebase confirmed most product facts are accurate
(`save_artifact` / `load_skill` tool names, the five thinking levels
`off/low/medium/high/xhigh`, the 50 MB / 500 MB attachment limits all match).

But a verification pass surfaced **factual drift** (docs describe behavior the
code does not implement), **whole missing subsystems** (IM connectors), and a
class of **rendering bugs** (`--` not rendering as an em-dash). This plan
records every finding and drives a per-module correction workflow.

---

## Findings

### Tier 1 — Factual errors (a user following the doc will fail)

All in `guides/automation/event-triggers.md`. Verified against
`backend/cubeplex/triggers/ingest.py`, `triggers/signature.py`, and
`api/routes/v1/trigger_ingest.py`.

| # | Doc claims | Reality (code) |
|---|---|---|
| T1 | Signature header is `X-Webhook-Signature` | Default is `X-Signature` (`ingest.py:129`, configurable via `signature_header`) |
| T2 | Webhook URL is `…/api/v1/webhooks/trg_abc123` | Route is `POST /api/v1/ws/{workspace_id}/triggers/{trigger_id}/ingest` (`trigger_ingest.py:17,20`) |
| T3 | "compute the HMAC-SHA256 of the raw request body" | Signed message is `"{timestamp}." + raw_body` — timestamp is part of the signature (`signature.py:sign`) |
| T4 | Only a signature header is needed | `X-Timestamp` is **also required** (missing → 404). `X-Event-Id` is optional (dedup). All configurable. |
| T5 | Invalid signature → `401` | Every failure path returns **404** `{"error":"not_found"}` — deliberately opaque (`_flat_404()`) |
| T6 | Event-log "Rejected" status = signature failed | Signature failures return 404 **before** any `trigger_events` row is inserted, so they never appear in the event log at all |
| T7 | Implied 5xx for stale requests not mentioned | Timestamp freshness window is **300 s** (`timestamp_fresh`, `signature.py`); stale → 404 |
| T8 | "GitHub, Stripe… paste the HMAC secret and CubePlex validates `X-Hub-Signature-256` automatically" | **False.** The generic webhook expects CubePlex's own scheme (`X-Signature` over `timestamp.body` + required `X-Timestamp`). GitHub sends no `X-Timestamp` and signs body-only with a different header → always 404. The sender must implement CubePlex's scheme (or sit behind a relay). |

> **T8 is the most damaging** — both worked examples ("GitHub issue triage",
> "Slack alert escalation") are written as drop-in integrations that cannot
> work as described. The honest framing: the generic webhook is a
> *bring-your-own-signer* endpoint; the `curl` recipe is the reference.

### Tier 2 — Missing subsystems / content gaps

| # | Gap |
|---|---|
| G1 | **IM connectors have zero docs.** Code ships Feishu, DingTalk, Discord, Slack, Teams (`backend/cubeplex/im/`). This is a flagship surface (recent commits are IM-heavy) with no "connect a bot" guide. |
| G2 | **i18n is a shell.** `docusaurus.config.ts` declares the `zh-Hans` locale + a language dropdown, but there is no `i18n/` directory — Chinese users get the switcher and untranslated/fallback pages. Either translate or drop the locale until ready. |
| G3 | **No API-keys doc** (programmatic access exists in flight). |
| G4 | **No group-chat / conversation-participants doc** (recent plans). |
| G5 | **No "Limits & Quotas" reference page.** Limits are scattered; e.g. `max_per_message = 10` attachments/message (`conversations.py:1329`) is undocumented. |
| G6 | **No Troubleshooting/FAQ landing page and no glossary** (preset, grant, template, install are unglossed jargon on first use). |

### Tier 3 — Rendering & consistency

| # | Issue |
|---|---|
| R1 | **`--` used as an em-dash** in `basics.md`, `memory/*`, `automation/*`. Docusaurus renders it as two literal hyphens. Other files correctly use `—`. Normalize all to `—`. |
| R2 | **Internal route groups leak into user docs.** `managing-memory.md` shows `/(app)/w/{workspaceId}/memory` — `(app)` is a Next route group, never in a real URL. `discover-and-install.md` shows `/<workspace>/skills`. Replace with the real user-facing path or describe navigation, not URLs. |
| R3 | **Inconsistent navigation nomenclature** — "Settings > Models" vs "Organization Settings > Models" vs "Admin > Models" for the same destination. Pick one vocabulary and apply globally. |
| R4 | **Hard-coded dated model IDs** in `model-selection.md` failover banner (`claude-sonnet-4-20250514`, …) will age and clash with the "friendly preset names" model used elsewhere. |

### Tier 4 — Structural

| # | Item |
|---|---|
| S1 | **Zero screenshots.** Acceptable pre-launch, but the artifact panel, preset picker, and Memory Center can't be explained in prose alone. This plan introduces screenshot placeholders everywhere a visual is needed. |
| S2 | Consider a top-level **"Concepts" vs "How-to" vs "Reference"** split (Diátaxis) as the doc set grows; out of scope for this pass, noted for later. |

---

## Screenshot placeholder convention

Until we capture real interaction screenshots, every spot that needs a visual
gets a **visible** placeholder so reviewers see the gap and the future
photographer knows exactly what to capture and where the asset lands:

```md
:::info 📸 Screenshot placeholder
**Capture:** <what to show, including the interaction/state to demonstrate>
**Asset:** `/img/<area>/<name>.png`
:::
```

- Placeholders render as a visible admonition (obvious during review).
- `**Asset:**` reserves the final path under `docs/site/static/img/<area>/`.
- Replace each block with `![alt](/img/<area>/<name>.png)` once the real
  screenshot (with interaction visible) is added. Real screenshots land in a
  follow-up pass.

---

## Execution

### Step 1 (done in this PR) — fix the Tier-1 webhook errors

Rewrite the webhook sections of `event-triggers.md` with the verified facts
(T1–T8), correct both worked examples, and add screenshot placeholders. This
is the demonstrated first revision; the rest is driven by the workflow below.

### Step 2 — per-module review-and-correct workflow

A workflow (`Workflow` tool) fans out one agent per subsystem. Each agent:

1. Reads its doc file(s).
2. Greps the corresponding backend/frontend code for the facts the doc asserts
   (routes, headers, limits, tool names, state machines, enums).
3. Applies corrections **in place** (Edit/Write).
4. Inserts screenshot placeholders per the convention above.
5. Returns a structured change report (what was wrong, what changed, residual
   gaps it could not resolve).

A final synthesis agent compiles a master report and the open-gaps list (IM
docs to author, i18n decision, etc.).

**Module → code map:**

| Module (docs) | Code to verify against |
|---|---|
| getting-started/* | cross-cutting: auth, bootstrap, workspaces |
| conversations/* | `agents/`, `attachments.py`, frontend `components/chat/` |
| skills/* + admin/skills-management | `skills/`, frontend skills pages |
| memory/* | `memory/` |
| mcp/* + admin/mcp-connectors | `mcp/`, `triggers` (citations) |
| automation/scheduled-tasks | `schedules/` |
| automation/event-triggers | `triggers/` (already fixed in Step 1; agent verifies only) |
| admin/{models,members,sandbox,cost} | `api/routes/v1/admin*`, sandbox, cost |
| **NEW** im-connectors/* | `backend/cubeplex/im/{feishu,dingtalk,slack,teams,discord}` |

### Step 3 — process guardrail

Add a non-negotiable rule to `CLAUDE.md`: any feature add/change must update
`docs/site/docs` in the **same PR**, and code touching a documented
route/limit/enum must update the doc that asserts it. Include the
screenshot-placeholder convention so new visuals are stubbed, not skipped.

### Step 4 (follow-up, not this PR) — real screenshots + i18n decision

Capture the interaction screenshots that replace the placeholders, and decide
whether to translate `zh-Hans` or remove the locale.

---

## Out of scope

- Real screenshot capture (placeholders only this pass).
- Actually translating the docs.
- Diátaxis restructure (S2).
