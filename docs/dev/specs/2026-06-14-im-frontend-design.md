# IM connector frontend — design

**Status:** spec (pre-implementation)
**Date:** 2026-06-14
**Worktree:** `feat/im-frontend`
**Owner:** xfgong
**Implementation:** sequel to `docs/dev/plans/2026-06-11-im-connectors-feishu.md`
(backend) — this doc covers the cubebox web UI for that connector.

## Goal

Give workspace admins a self-serve UI to bind an IM bot (Feishu first,
Slack/Teams later) to their workspace, see the bot's live state, and
disable / delete it. Give org admins an org-wide view of all bound
accounts with disable/enable, but no binding flow on the admin side.

The backend (`feat/im-connectors`) already ships:

- `POST   /api/v1/ws/{ws}/im/accounts` — create
- `GET    /api/v1/ws/{ws}/im/accounts` — list (workspace-scoped)
- `DELETE /api/v1/ws/{ws}/im/accounts/{id}` — delete
- `GET    /api/v1/admin/im/accounts` — list (org-wide)
- `POST   /api/v1/admin/im/accounts/{id}/disable`
- `POST   /api/v1/admin/im/accounts/{id}/enable`

…and a per-sender identity gate that resolves Feishu sender → cubebox
user → workspace membership. The frontend exposes binding + observation
of all of the above.

## Non-goals (v1)

- No SSE / WebSocket push for runtime state — 5s polling is enough.
- No persisted activity/stats table — 24h aggregates from existing
  receipts cover MVP signal.
- No admin-side binding flow — admins observe, workspace admins bind.
  Avoids `acting_user_id` confusion and centralizes the credential entry.
- No identity-gate management page (no manual re-map, no kick) — the
  cached identity-link is auto-invalidated when membership disappears.
- No bot persona / canned-response editor — `acting_user_id` controls
  the agent identity at run time; UI for that lives in workspace
  persona, not here.

## Architecture overview

File tree (★ modify, ✚ new):

```
frontend/packages/web/
  app/
    (app)/w/[wsId]/settings/page.tsx              ★ accept ?tab=im
    admin/im/page.tsx                             ✚ org-wide table
  components/
    workspace-settings/
      SettingsTabs.tsx                            ★ add 'im' tab
      ImPanel.tsx                                 ✚ workspace shell
    admin/
      AdminSubNav.tsx                             ★ add 'IM connectors'
    im/                                           ✚ shared modules
      ImAccountListItem.tsx                       ✚ shared row component
      ImAccountDetailPanel.tsx                    ✚ shared, scope-aware
      ImAccountStatusPill.tsx                     ✚ shared
      ImAccountToolbar.tsx                        ✚ shared
      ImConnectWizard/
        index.tsx                                 ✚ shell
        steps/
          StepPlatform.tsx                        ✚ step 0
          StepPrereqs.tsx                         ✚ shared
          StepCredentials.tsx                     ✚ shared, descriptor-driven
          StepVerify.tsx                          ✚ shared
        platforms/
          types.ts                                ✚ PlatformDescriptor
          feishu.ts                               ✚ live
          slack.stub.ts                           ✚ coming-soon stub
        useConnectMutation.ts                     ✚ POST + classify err
  i18n/messages/{en,zh}.json                      ★ add im.* namespace
backend/cubebox/
  api/schemas/im_connector.py                    ★ add ImRuntimeStatus
  api/routes/v1/ws_im.py                          ★ list returns runtime
  api/routes/v1/admin_im.py                       ★ same
  services/im_connector.py                        ★ add compute_runtime()
  repositories/im_connector.py                    ★ add aggregate helper
core/                                             types regenerated only
```

**Reuse boundary** (per AGENTS.md "Scope-isolated pages" rule):

- `ImAccountDetailPanel`, `ImAccountStatusPill`, `ImAccountListItem`,
  `ImAccountToolbar` are **modules** — shared across scopes.
- The page components (`ImPanel.tsx` for workspace,
  `admin/im/page.tsx` for admin) are independent route assemblies that
  compose these modules; they share no `mode` prop.
- The wizard shell is scope-neutral (workspace-only entry point per
  Section 2, but the shell itself has no scope branching).

**Multi-platform extension point** is contained to
`components/im/ImConnectWizard/platforms/*`. Adding Slack means adding
`slack.ts` (replacing the stub) and `OAuthRedirect` step component —
zero changes to the wizard shell, list, detail panel, toolbar, or
backend admin/workspace routes (the backend already accepts
`platform` as a payload field).

## Navigation and routing

### Workspace scope

| Path | Trigger |
|---|---|
| `/w/{wsId}/settings?tab=im` | `SettingsTabs.tsx` adds an `im` tab alongside `workspace / skills / mcp / members / shares` |
| `/w/{wsId}/settings?tab=im&action=connect` | Wizard opens; Back / refresh land back on step 0 |
| `/w/{wsId}/settings?tab=im&account={id}` | Detail panel shows that account |

Tab visibility: the `im` tab is **always shown** to workspace members.
Member-level users see the list read-only (no Connect / Disable /
Delete buttons); admins see the full action set. Hiding the tab would
prevent members from discovering that their workspace has a bot.

### Admin scope

| Path | Trigger |
|---|---|
| `/admin/im` | `AdminSubNav.tsx` adds "IM connectors" (`MessageSquare` icon) near MCP |
| `/admin/im?account={id}` | Detail panel renders inline (expanded row) |

No `[+ Connect]` button on the admin page. The "⋯" menu offers
"Open workspace settings" instead, which routes to that account's
workspace's `tab=im`.

All state (tab / action / account) is encoded in URL query so refresh
and browser Back behave intuitively. Losing wizard state on refresh
only pops back to the most recent step.

## Wizard component design

### Shell vs steps

`ImConnectWizard/index.tsx` is the platform-neutral shell. It owns:

- Wizard state: `step | platform | formData | error`
- Step indicator (dot count = `descriptor.steps.length`)
- Next / Back / Cancel + `canAdvance` gating
- Final submit (in the conventional last `Verify` step)
- Esc with confirm dialog (prevents lost form data on accidental close)

Each step is a pure props component:

```ts
type WizardStepProps = {
  descriptor: PlatformDescriptor
  form: FormState
  onChange: (patch: Partial<FormState>) => void
  onNext: () => void
}
```

Steps own no fetch. The Verify step's POST goes through
`useConnectMutation`, owned by the shell.

### Variable steps per platform

The 3-step Feishu wizard does NOT fit Slack (OAuth redirect) or Teams
(manifest upload). The descriptor declares its own step sequence:

```ts
export type WizardStepDef = {
  key: string                            // 'prereqs', 'credentials', 'verify', 'oauth_redirect', ...
  label: string                          // i18n key for step indicator
  Component: React.FC<WizardStepProps>   // shared OR platform-specific
  canAdvance?: (form: FormState) => boolean
}

export type PlatformDescriptor = {
  id: 'feishu' | 'slack' | 'teams'
  label: string
  iconName: string
  live: boolean
  steps: WizardStepDef[]                  // <-- the variable part
  buildPayload: (form: FormState) => ConnectAccountIn
}
```

Per-platform step sequences:

- **Feishu** (live): `[Prereqs, Credentials, Verify]` — shared components
- **Slack** (later): `[OAuthRedirect (slack-private), Verify (shared)]`
- **Teams** (later): `[ManifestUpload (teams-private), AzureCreds (teams-private), Verify (shared)]`

Step 0 (platform picker) is **outside** `descriptor.steps`. It is the
gateway, not a numbered step, so the indicator dot count only counts
the platform-specific sequence.

### Field definitions

```ts
export type FieldDef = {
  key: string
  label: string                                       // i18n key
  type: 'text' | 'password' | 'select' | 'help'
  required: boolean
  showIf?: (form: FormState) => boolean               // smart disclosure
  options?: { value: string; label: string }[]
  placeholder?: string
  helpText?: string
}
```

Feishu fields:

| key | type | showIf |
|---|---|---|
| `app_id` | text | always |
| `app_secret` | password | always |
| `delivery_mode` | select (long_connection / webhook) | always |
| `domain` | select (feishu / lark) | always |
| `encrypt_key` | password | `delivery_mode === 'webhook'` |
| `verification_token` | password | `delivery_mode === 'webhook'` |

`buildPayload` builds:

```ts
{ platform: 'feishu', app_id, app_secret, delivery_mode, domain,
  encrypt_key: '', verification_token: '', acting_user_id: 'self' }
```

## List + detail layout (unified)

Both scopes use **list-left / detail-right**. The list density is
"compact row" (~52px each) so it scales from 1 to 50+ accounts without
re-layout.

```
┌────────────────────────────────────────────────────────────┐
│ Toolbar: [+ Connect]?  [Filter ▾]  [Search]                │
├────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────┐  ┌──────────────────────┐ │
│ │ ImAccountListItem #1         │  │ ImAccountDetailPanel │ │
│ │  ● Feishu  @moltbot   2m     │  │  Identity            │ │
│ │  ws-design · long_conn       │  │  Runtime             │ │
│ ├──────────────────────────────┤  │  Identity gate       │ │
│ │  ● Feishu  @cb-stg    5h     │  │  Actions             │ │
│ │  ws-eng    · webhook         │  └──────────────────────┘ │
│ ├──────────────────────────────┤                            │
│ │  ○ Slack   @cb-dev    3d     │                            │
│ └──────────────────────────────┘                            │
└────────────────────────────────────────────────────────────┘
```

### Differences collapse to 3 toolbar props

| prop | workspace | admin |
|---|---|---|
| `showConnect` | true | false |
| `showWorkspaceColumn` | false | true |
| `defaultFilter.scope` | this workspace | all workspaces |

### Shared modules

**`ImAccountListItem`** — one row, three segments:

- left: status dot + platform badge + bot name
- middle (admin only): workspace name · delivery_mode
- right: last_inbound relative time + `⋯` menu

Keyboard ↑↓ to move selection; selection drives URL `?account=` so
detail panel updates without an extra fetch.

**`ImAccountDetailPanel`** receives:

```ts
type Props = {
  account: ImAccountOut
  scope: 'workspace' | 'admin'    // selects action set
  onMutate: () => void
}
```

Sections:

1. **Identity** — acting_user_id (linked to user profile), app_id, bot_open_id
2. **Runtime** — status pill, last_inbound_at, delivery_mode, pending_queue
3. **Identity gate** — `matched_24h`, `rejected_24h`; admin scope adds a
   link to drill into the rejected list (future, not in v1)
4. **Actions**:
   - workspace: `Disable` toggle, `Delete` (red, double-confirm with
     bot name typed)
   - admin: `Disable / Enable` toggle, link to "Manage in workspace
     settings" (no delete)

## Runtime status (L2)

Embedded in `IMAccountOut.runtime`:

```python
class ImRuntimeStatus(BaseModel):
    connection_state: Literal["connected", "disconnected", "never_connected"]
    last_inbound_at: str | None        # utc_isoformat()
    bot_open_id: str | None
    pending_queue: int                 # status IN ('pending','started')
    matched_24h: int
    rejected_24h: int
```

### `connection_state` semantics

| value | triggered when |
|---|---|
| `connected` | (long_connection: `app.state.im_long_connections[account.id]` exists + ws task not done) OR (webhook: receipt within last 60 min) |
| `disconnected` | `enabled=true` but neither condition above holds |
| `never_connected` | `bot_open_id is None` OR no receipt has ever been seen |

`enabled=false` overrides everything → "Disabled" pill (gray).

### Polling

- Initial fetch on tab open
- Re-fetch every **5s** while tab is visible
  (`document.visibilityState === 'visible'`)
- Pause on hidden tab; resume on visible
- Write actions (disable/enable/delete/connect) invalidate + immediate
  re-fetch

No SSE / WebSocket. Multi-process backend deployments would otherwise
need a broadcast layer for `connection_state`, which is out of scope.

### Status pill visual

- ● `connected` — success-solid (green)
- ◐ `disconnected` — warning-solid (yellow)
- ⚠ `never_connected` — danger-solid (red)
- ○ `disabled` — muted-foreground (gray)

Color tokens reuse existing theme variables. Color is not the only
signal: shape (●/◐/⚠/○) + text label + `role="status"` together meet
WCAG color-independence requirements.

## Error display strategy

Backend error codes encountered during binding/management map to 3 UI
shapes via a single `useConnectMutation` classifier:

```ts
type ConnectError = {
  shape: 'field' | 'banner' | 'toast'
  field?: string                  // when shape === 'field'
  message: string                 // resolved i18n key
  retry?: () => void              // when shape === 'banner'
}
```

| Backend response | Shape | Where |
|---|---|---|
| 422 pydantic validation | field | inline on the named field |
| 400 "could not hydrate bot_open_id" (preflight bad creds) | field | on `app_secret` |
| 409 duplicate `app_id` | banner | wizard top, with `Go to existing →` |
| 502 bot_open_id hydration failed (server hit Feishu, Feishu unhappy) | banner | wizard top, with `[Retry]` |
| 403 acting_user_id impersonation | banner | wizard top (shouldn't trigger from UI; defensive) |
| network err | toast | global; form state preserved |
| any other 5xx | banner | wizard top + log_id displayed for ops |

Success of disable / enable / delete / connect → `role="status"` toast,
auto-dismiss 2s.

Wizard step doesn't reset on error. Field-level errors clear on next
keystroke; banner errors clear when the user advances or retries.

## Empty state and first-run guidance

### Workspace, no accounts

Full-bleed CTA replaces the list+detail two-pane layout:

```
              [MessageSquare icon]
         Connect your team's IM to cubebox
   Bot replies in your chat, runs agents on @mentions,
   auto-routes to the right cubebox user by email.
              [+ Connect a Feishu bot]
      Slack · Teams · DingTalk — coming later
      📖 Setup guide → /docs/im-feishu-setup.md
```

- Headline sells outcome, not feature
- Setup guide link routes to the backend-served markdown
  (`/api/v1/docs/im-feishu-setup.md`, opens new tab)
- Coming-soon line manages expectations re multi-platform

### Admin, no accounts in org

Compact admin-flavored placeholder:

```
       No IM connectors yet
Workspace admins connect bots from their workspace
settings. You'll see all org-wide accounts here.
       [Open my workspaces →]
```

### Wizard Step 0

Platform picker shows all known platforms with `live: true | false`.
Coming-soon cards are `aria-disabled` + tooltip + greyed; clicking does
nothing.

### Selection edge cases

- URL has no `account=` and list non-empty → select first
- Selected account gets deleted → auto-select next
- URL has `account=imac-xxx` not in current list → toast "Account not
  found" + select first

## Backend changes required

Five touch points, no DB migration:

### 1. `api/schemas/im_connector.py`

Add `ImRuntimeStatus` (see Runtime status section) and embed as
`runtime: ImRuntimeStatus` on `IMAccountOut`.

### 2. `services/im_connector.py`

```python
def compute_runtime(
    account: IMConnectorAccount,
    long_conns: dict[str, FeishuLongConnection],
    aggregates: dict[str, _RuntimeAgg],
) -> ImRuntimeStatus
```

Sources:

- `connection_state` ← `long_conns[account.id]` + ws task state OR
  webhook receipt window
- `last_inbound_at` ← `aggregates[account.id].last_receipt_at`
- `bot_open_id` ← already decoded from credentials elsewhere; passed in
- `pending_queue` ← `aggregates[account.id].pending_count`
- `matched_24h` / `rejected_24h` ← `aggregates[account.id].matched24h` /
  `.rejected24h`

### 3. `repositories/im_connector.py`

```python
async def collect_runtime_aggregates(
    session: AsyncSession, *, account_ids: list[str]
) -> dict[str, _RuntimeAgg]
```

3 batch queries (max-receipt-at per account, pending-queue counts per
account, 24h receipt status counts per account) — one `IN (...)` per
query, O(1) per account at assembly time. Returns a dict keyed by
account_id.

### 4. `api/routes/v1/ws_im.py` and `admin_im.py`

Both list endpoints call `collect_runtime_aggregates` once for all
returned accounts, then `compute_runtime` per row. No new endpoint
created (avoids 2x RPS from the polling client).

### 5. `core` SDK type regeneration

`frontend/packages/core/src/api/im.ts` regenerates from the FastAPI
OpenAPI schema. New `ImRuntimeStatus` becomes available to web.

### Performance budget

50 accounts × 5s polling worst-case:

- 3 aggregate SQL queries with `IN (50 ids)` ≤ ~30ms each
- in-memory map join ~negligible
- total per poll ~100ms, well within budget

If observed > 200ms in production, add a 30s Redis cache keyed by
account_id. Not in v1.

## i18n and a11y

### i18n namespace

All keys under `im.*`. Sample slice:

```
im.nav.workspaceTab               "IM"
im.nav.adminItem                  "IM connectors"
im.empty.workspace.headline       "Connect your team's IM to cubebox"
im.empty.workspace.description    "Bot replies in your chat..."
im.platform.feishu.label          "Feishu"
im.platform.slack.coming          "Coming soon"
im.status.connected               "Connected"
im.status.disconnected            "Disconnected"
im.status.never                   "Never connected"
im.status.disabled                "Disabled"
im.wizard.step.prereqs            "Prerequisites"
im.wizard.feishu.prereq.scopes    "Scopes granted: im:message, contact:user.email..."
im.wizard.feishu.field.appId      "App ID"
im.error.field.appIdFormat        "App ID must start with cli_"
im.error.banner.duplicateApp      "This Feishu app is already connected"
im.error.banner.hydrationFailed   "Could not verify the bot identity..."
im.runtime.lastInbound            "Last inbound {when}"
im.runtime.gate.matched           "{count} matched"
im.runtime.gate.rejected          "{count} rejected"
```

Chinese is parity-required: pre-commit hook `i18n-key-parity` enforces.

Descriptors carry only i18n **keys**, not strings. Components resolve
via `useTranslations('im')`. Adding Slack uses `im.platform.slack.*`
keys parallel to Feishu.

### a11y requirements

| Element | Requirement |
|---|---|
| Settings tabs | `aria-current="page"` (existing pattern, reused) |
| Wizard container | `role="dialog"` + `aria-labelledby` + `aria-describedby` |
| Step indicator | `role="list"`, current step `aria-current="step"` |
| Step content swap | Focus moves to current step heading via `tabIndex={-1}` ref |
| Password fields | `type="password"`; eye toggle `aria-pressed` + label |
| Status pill | `role="status"`; dot shape distinguishes by form not only color |
| List item | `role="option"` inside `role="listbox"`; ↑↓ navigation |
| Delete button | `aria-describedby` pointing to "This action cannot be undone" |
| Toast | `role="alert"` for errors, `role="status"` for success |
| External link buttons | `aria-describedby` notes "Opens external site in new tab" |

Color-blind safety: ●/◐/⚠/○ shapes are distinguishable independently
of color. Text labels always render alongside.

Keyboard flow:

- Tab → enter wizard, focus first input of current step
- Enter on Next → advance (gated by `canAdvance`)
- Esc → cancel; confirm dialog if form non-empty
- ↑↓ in list → move selection
- Enter on list item → expand detail panel
- Delete key on list item → toast "Use the ⋯ menu to delete" (no
  direct destructive shortcut)

RTL: all spacing uses CSS logical properties (`margin-inline-start`,
`padding-block`) so direction flips cleanly.

## Testing

### Playwright E2E

Location: `frontend/packages/web/e2e/im/`. Backend calls are
intercepted (the front-end e2e validates UI interactions; real-Feishu
e2e is owned by `backend/tests/e2e/test_im_*`).

| Spec | Asserts |
|---|---|
| `connect-wizard-feishu.spec.ts` | empty CTA → platform select → 4 prereqs checked → fill creds → POST intercepted 201 → list updates |
| `connect-duplicate.spec.ts` | same flow, 409 intercept → banner shows, form preserved |
| `disable-enable.spec.ts` | seeded connected account → Disable → pill ○ → Enable → pill ● |
| `delete-confirm.spec.ts` | Delete → dialog → confirm gated by typed bot name → DELETE intercepted → list shrinks |
| `admin-cross-workspace.spec.ts` | 3 seeded accounts across 3 workspaces → admin page lists all + workspace column shown + no Connect button |

Shared fixture: `e2e/fixtures/im.ts` provides
`seedFeishuAccount({ workspace, status, runtime })`.

### Unit (Vitest)

Location: `frontend/packages/web/__tests__/im/`.

| File | Covers |
|---|---|
| `ImAccountStatusPill.test.tsx` | 4 states × disabled override |
| `ImAccountListItem.test.tsx` | `showWorkspaceColumn` toggle; `onSelect` |
| `ImAccountDetailPanel.test.tsx` | `scope` selects action set |
| `useConnectMutation.test.ts` | `classify(409, ...)` → banner; `classify(422, ...)` → field |
| `platforms/feishu.test.ts` | `buildPayload` matches backend schema; `scopeConsoleUrl` correct |
| `ImConnectWizard/index.test.tsx` | step count = `descriptor.steps.length`; Next gated by `canAdvance` |

### Backend additions

| File | Covers |
|---|---|
| `tests/e2e/test_im_runtime_endpoint.py` | list returns `runtime` block with valid enum |
| `tests/unit/test_im_compute_runtime.py` | 4 connection_state branches |

### Manual smoke

Append to `backend/docs/im-feishu-setup.md`:

```
Frontend smoke:
- [ ] /w/{ws}/settings?tab=im empty state shows CTA + setup-guide link
- [ ] Wizard step indicator dot count matches descriptor.steps.length
- [ ] Status pill transitions never → connected within 5s of bind
- [ ] Disable triggers long-conn disconnect in backend log
- [ ] Delete removes the im_connector_accounts row
```

## Out of scope (explicitly deferred)

- Identity-gate management UI (manual re-map, kick, list rejected
  senders) — observation only in v1, drill-down link is a stub
- Activity time series / charts (7d trend, hour-of-day) — wait for
  signal that users want it
- Multi-bot search / global filter across orgs
- Bot rename / icon upload in cubebox (Feishu owns these)
- SSE / WebSocket push for runtime updates
- HITL card-button flows on the Feishu side (separate spec; not UI-side)

## Open questions

None left after the design walkthrough. If implementation surfaces
unforeseen issues, this doc is the frozen baseline — amendments go in
a follow-up note under `docs/dev/notes/`, not in-line edits.
