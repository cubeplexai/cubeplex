# IM connector frontend — smoke validation

**Date:** 2026-06-14
**Branch:** `feat/im-frontend`
**Worktree:** slot 69 (backend `:8069`, frontend `:3069`)
**Bot:** `@moltbot` from `~/.feishurc`

## Setup

Backend + frontend started in the worktree:

```bash
cd .worktrees/feat/im-frontend/backend
nohup env CUBEPLEX_API__RELOAD=false uv run python main.py > /tmp/im-fe-test/backend.log 2>&1 < /dev/null & disown
cd ../frontend
HOSTNAME=0.0.0.0 nohup pnpm dev > /tmp/im-fe-test/frontend.log 2>&1 < /dev/null & disown
```

Playwright was driven from `frontend/packages/web/smoke.mjs` against
`http://127.0.0.1:3069`.

## Captured flow

Screenshots in `/tmp/im-fe-shots/`:

| # | Frame | What it shows |
|---|---|---|
| 00 | `00-register.png` | Registration form |
| 01 | `01-workspace.png` | Newly-created workspace landing |
| 02 | `02-im-empty.png` | `/w/{ws}/settings?tab=im` empty CTA — headline, "Connect a Feishu bot" button, "Slack · Teams · DingTalk — coming later" |
| 03 | `03-wizard-platform.png` | Step 0 platform picker — Feishu enabled, Slack disabled ("Coming soon") |
| 04 | `04-wizard-prereqs.png` | Step 1 prerequisites — 4 unchecked items + 2 jump-to-Feishu-console link icons (app + scopes) |
| 05 | `05-prereqs-checked.png` | Same view with all 4 checkboxes filled |
| 06 | `06-credentials-empty.png` | Step 2 credentials form — App ID, App Secret, Delivery mode select, Domain select |
| 07 | `07-credentials-filled.png` | Same with real `cli_a9f0a4c078a11bd3` + masked secret |
| 08 | `08-verify-ready.png` | Step 3 verify — "Ready to connect Feishu bot cli_…" summary BEFORE click |
| 09 | `09-verify-busy.png` | Same step with spinner + "Verifying credentials…" AFTER Connect |
| 10 | `10-connected-list.png` | Wizard closed, list shows 1 account |
| 11 | `11-pill-connected.png` | Pill flipped to ● Connected after the 5s polling tick |

## What the connected state shows

`11-pill-connected.png` confirms:

- Toolbar: `1 account` + `+ Connect` button
- ListItem: ● Connected pill (success-fg/green) · Feishu badge · `cli_a9f0a4c078a11bd3` · `long_connection`
- Detail panel right side:
  - Title `cli_a9f0a4c078a11bd3` + ● Connected pill
  - **Identity** — Acting as `usr-1hPgBG6D2ON8YH` (the smoke user) · Bot open_id `ou_b6aef8a8a515e3…` (truncated; full id matches `@moltbot`) · Mode `long_connection`
  - **Identity gate (24h)** — `0 matched · 0 rejected`
  - **Disable** button (workspace scope)
  - **Delete** button (red, destructive)

That hydration succeeded against the real Feishu API: `bot_open_id` came back populated, the backend's long-connection `is_open()` flipped to `true` within 5s of the connect, and the next `wsListImAccounts` poll surfaced `connection_state: "connected"`.

## Backend observations

`/tmp/im-fe-test/backend.log` confirmed:

- `POST /api/v1/ws/{ws}/im/accounts` → 201
- `_hydrate_bot_open_id` resolved the bot identity via `/open-apis/bot/v3/info`
- Long-connection startup `connected to wss://msg-frontier.feishu.cn/...`
- Subsequent `GET /api/v1/ws/{ws}/im/accounts` returned the new `runtime` block with `connection_state: "connected"`, `bot_open_id` populated, `pending_queue: 0`, `matched_24h: 0`, `rejected_24h: 0`

## Known cosmetic gaps (fixed)

- ~~StepCredentials Select showed raw enum values~~ — fixed: removed
  pre-set default values from form state; Select now shows a "Select…"
  placeholder until the user explicitly picks an option, at which point
  Radix captures the translated label correctly.
- ~~StepPlatform had 3 columns but only 2 cards~~ — fixed: added
  `teams.stub.ts` (`live: false`) and included it in `ALL_PLATFORMS`.
- No Playwright e2e committed yet — the smoke script was untracked.

## Status

Backend B1–B5 + frontend F1–F9 are in. Real-Feishu happy path
(`register → connect → connected pill`) verified end-to-end on the
worktree with the credentials from `~/.feishurc`.
