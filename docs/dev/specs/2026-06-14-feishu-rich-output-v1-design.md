# Feishu rich output v1 — design

**Date:** 2026-06-14
**Status:** Design, awaiting implementation
**Related:** [2026-06-13 investigation note](../notes/2026-06-13-feishu-richer-output-borrow-from-openclaw.md)
**Touches:** `backend/cubeplex/im/feishu/`, `backend/cubeplex/im/{outbound,types,artifacts}.py`, `backend/cubeplex/api/routes/v1/im_feishu_events.py`
**Out of repo:** Feishu app permission scope `cardkit:card:write` must be applied for the bot app before release

---

## 1. Background

Today every outbound IM message to Feishu uses `msg_type=text`. The
agent's markdown lands flat, tool calls collapse to a single italic line,
and non-image artifacts become a `📎 link` one-liner. The web client
shows rich widgets (markdown, collapsible tool calls, artifact cards,
ask-user prompts, sandbox-confirm prompts, sub-agent clusters, task
progress) that have no Feishu counterpart.

The 2026-06-13 investigation note compared cubeplex to `~/openclaw-lark`
and concluded:

1. Markdown must be rendered via Feishu's **interactive card** with a
   `markdown` element, not via the `post` message type (which silently
   blanks tables).
2. Widgets must be cards or card sub-elements, not text.
3. `~/openclaw-lark`'s overall pattern is sound; cubeplex should borrow
   the schema and rate-limit handling but keep its own tailer/state seam.

This spec lands v1.

## 2. Locked v1 decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Widget scope** | markdown · tool-use panel · artifact cards · AskUser · SandboxConfirm callbacks · SubAgent (light) · TaskProgress · Thinking-as-reaction · MemoryUpdate-as-tool-call · RunError-as-terminal-variant · Citation-as-inline-link · HTML widget as placeholder card | Full callback close-loop without the heaviest tail (deep sub-agent, server-side HTML rendering) |
| **Transport** | Pure CardKit | Single permission scope (`cardkit:card:write`), no tenant feature flag, no admin gating, independent rate-limit quota from IM, native streaming. JSON 2.0 required |
| **Card lifecycle** | One card per run | Matches openclaw and the web message model; cleaner state, fewer messages, fewer rate-limit hits |
| **Rollout** | Hard cutover | Project hasn't shipped publicly; CLAUDE.md "no backward-compat shim"; keep one emergency-text fallback path only for CardKit `create_entity` failure |
| **Citation popups** | Inline `[N](url)` superlinks | CardKit has no popover/tooltip element. Strip-and-cite is the only path without dropping provenance |
| **Embedded HTML widgets** | Placeholder card with share-URL button | CardKit forbids HTMLBlock and has no iframe element. Server-side HTML→image rendering deferred to v2 |

## 3. Architecture

```
cubepi run events            outbound rendering              feishu wire
─────────────────            ──────────────────              ──────────────
text_delta       ┐
tool_call        │           ┌─ RenderState ─┐              ┌─ CardKit ─┐
tool_result      ├─ tailer ─→│  CardState    │── renderer ─→│  entity   │── stream/patch
artifact         │           │  (typed data) │              │  card_id  │
ask_user         │           └───────────────┘              └───────────┘
sandbox_confirm  ┘                  │                              │
                                    │                              ↓ initial send
                                    │                       im.message.create
                                    │                       msg_type=interactive
                                    │                       body={type:card, data:{card_id}}
                                    │                              │
                                    └─ on AskUser/Confirm ─────────┤
                                                                   ↓
inbound card.action  ←── feishu webhook / long-conn ────────── user clicks button
                              │
                              ↓ resume paused run via cubepi
                         agent_runtime.resume(answer)
```

Four new modules, all under `backend/cubeplex/im/feishu/`:

1. **`card_model.py`** — typed Pydantic `CardState`
   (`header`, `streaming_text_id`, `tool_panel`, `artifacts`, `sources`,
   `pending_input`, `finalized`, `error`). Pure data, no IO.
2. **`card_renderer.py`** — pure `render(state: CardState) → dict`
   serializing to CardKit JSON 2.0. Owns the `TOOL_DISPLAY` map
   (per-tool icon/emoji + `summarize_args` one-liner) and the
   `optimize_markdown_style()` Python port.
3. **`cardkit_client.py`** — thin async wrapper around CardKit endpoints:
   `create_entity(json) → card_id`, `stream_text(card_id, element_id,
   delta, seq)`, `patch_card(card_id, json)`, `finalize(card_id, json)`.
   Implements per-channel throttling, `streaming_mode` toggling,
   `update_multi=true`, rate-limit error code handling, exponential
   backoff for finalize.
4. **`card_action_router.py`** — inbound. Pure `dispatch(payload) →
   ResumeAction` plus thin IO wrapper that calls cubepi `resume_run`.

**Reused unchanged:**
- `OutboundRunTailer` event loop (Redis stream consumer, flood-strike
  backoff, reaction lifecycle hooks).
- `FeishuConnector` reaction add/remove (`add_reaction` /
  `remove_reaction`).
- `feishu/signature.py` (inbound signature validation).
- `im/identity.py` (per-sender identity gate).

**Reused but generalized:**
- `OutboundOp` — `kind` widens from `"post" | "edit" | "artifact" |
  "no_op"` to `"card_create" | "stream_text" | "patch_card" |
  "finalize"`.
- `fold_event(event, state, now)` — now mutates `state.card_state`
  in-place and returns the op kind to flush; no longer concatenates
  text strings.
- `IMArtifactDispatcher` — no longer sends standalone messages; instead
  updates `state.card_state.artifacts` and returns a `patch_card` op.

**Removed:**
- `FeishuConnector._build_payload` (text wire format).
- `FeishuConnector.post_placeholder` / `edit` text paths (renamed to
  `_send_emergency_text` and kept only for §7.4 CardKit-create fallback).
- `_MARKDOWN_TABLE_RE` / `_MARKDOWN_HINT_RE` (no longer sniffing).
- `fold_event` `_running 'xxx'…_` italic line synthesis.
- `IMArtifactDispatcher` share-link text format for non-image artifacts.

## 4. Card JSON 2.0 mapping

### 4.1 Skeleton

```json
{
  "schema": "2.0",
  "header": {
    "title": { "tag": "plain_text", "content": "<workspace bot name>" },
    "subtitle": { "tag": "plain_text", "content": "<run-status line>" },
    "template": "blue"
  },
  "config": {
    "streaming_mode": true,
    "update_multi": true,
    "locales": ["zh_cn", "en_us"]
  },
  "body": {
    "elements": [
      { "tag": "markdown",             "element_id": "streaming_content", "content": "" },
      { "tag": "collapsible_panel",    "element_id": "tool_panel",         "elements": [] },
      { "tag": "collapsible_panel",    "element_id": "artifacts",          "elements": [] },
      { "tag": "collapsible_panel",    "element_id": "sources",            "elements": [] },
      { "tag": "interactive_container","element_id": "pending_input",      "elements": [] }
    ]
  }
}
```

The renderer drops elements with empty children, so the live card only
shows the slots that have content. `element_id`s are the targets for
`patch_card` and `streamCardContent`.

### 4.2 Markdown (`streaming_content`)

- First `text_delta` triggers `create_entity` then `streamCardContent`.
- Subsequent deltas push via `streamCardContent(card_id,
  "streaming_content", delta, seq)` with monotonically increasing `seq`.
- Pre-processing pipeline `optimize_markdown_style(text)`:
  1. Demote `#` / `##` → `####` / `#####`. Stops headings from blowing
     up the card layout (cardkit renders large headings full-width).
  2. Wrap tables in `<br>` spacers (cardkit ≥ v2 requirement).
  3. Protect content inside fenced code blocks from rewrite.
  4. Strip image references whose key doesn't match `^img_[A-Za-z0-9]+$`
     (CardKit error 200570).
  5. Replace citation markers (`[N]`, `【N-M】`) with `[N](url)` inline
     links — URL resolved from a `citation_index: dict[str, str]` that
     the tailer maintains from cubepi `citation` events. Markers whose
     URL is not in the index are left as plain text (no link, no
     stripping) — better to show `[1]` than to silently drop
     provenance.

### 4.3 Tool calls (`tool_panel`)

Header: status badge + step count + total elapsed.

| State | Badge color | Badge text |
|---|---|---|
| in progress | turquoise | `运行中 · N step` |
| success | green | `已完成 · N step · Xs` |
| failure | red | `失败 · N step · Xs` |

Per step, three `div` elements:

```json
[
  { "tag": "div",
    "icon": { "tag": "standard_icon", "token": "<TOOL_DISPLAY[name].icon>" },
    "text": { "tag": "lark_md",
              "content": "**read_file** · path=src/foo.py" } },
  { "tag": "div", "margin": "0px 0px 0px 22px",
    "text": { "tag": "plain_text", "content": "312ms" } },
  { "tag": "div", "margin": "0px 0px 0px 22px",
    "text": { "tag": "lark_md",
              "content": "```json\n<truncated result>\n```" } }
]
```

**`TOOL_DISPLAY: dict[str, ToolDisplay]`** in `card_renderer.py`. Each
entry: `(icon: str, summarize_args: Callable[[dict], str])`. Unregistered
tools fall back to `("⚙️", json_oneliner_truncate(80))`. Initial seed
covers `read_file`, `write_file`, `bash`, `web_fetch`, `update_memory`,
`recall_memory`, `mcp_*`. Result rendering uses `formatToolUseCodeBlock`
semantics (```` ```json ```` if parsable JSON, else ```` ```text ````,
truncate to 2000 chars).

**MemoryUpdate**: rendered as a tool call under `update_memory`, no
special widget.

**SubAgent**: one extra line in the panel: `🤖 sub-agent "<name>" · 已调用
N 个工具`. Sub-tool detail is **not** expanded in v1.

**Thinking**: not in the card body. Mapped to reaction lifecycle
(§5.5).

### 4.4 Artifacts (`artifacts`)

Each artifact is one `interactive_container` inside the panel:

- **image** — `img` element with the `image_key` from
  `IMArtifactDispatcher`'s existing upload step.
- **code / document / website / other** — title line (icon + name +
  `<text_tag>` for type), description line, one button: `查看
  → <share_url>` (`button.behaviors=[{"type":"open_url",
  "default_url":"<share_url>"}]`).
- **HTML widget** (in-conversation rich preview) — same as code/document
  but header reads `📊 预览` and the button reads `在浏览器中打开`.

### 4.5 AskUser / SandboxConfirm (`pending_input`)

```json
{
  "tag": "interactive_container",
  "elements": [
    { "tag": "markdown", "content": "<question text>" },
    { "tag": "column_set",
      "columns": [
        { "elements": [
            { "tag": "button",
              "text": { "tag": "plain_text", "content": "是" },
              "type": "primary",
              "behaviors": [{ "type": "callback" }],
              "value": { "action": "ask_user",
                         "run_id": "run_xxx",
                         "choice": "yes" } } ] },
        { "elements": [
            { "tag": "button",
              "text": { "tag": "plain_text", "content": "否" },
              "type": "default",
              "behaviors": [{ "type": "callback" }],
              "value": { "action": "ask_user",
                         "run_id": "run_xxx",
                         "choice": "no" } } ] }
      ] }
  ]
}
```

`button.value` is the round-trip payload — the inbound `card.action`
event carries it back verbatim. SandboxConfirm uses the same shape with
`action="sandbox_confirm"`.

After a button click the `pending_input` slot is replaced (§6.4) by a
non-interactive receipt line.

### 4.6 Terminal / error

- `done`: renderer sets `state.finalized=true`,
  `config.streaming_mode=false`, header `template="green"`, subtitle =
  `"已完成 · {elapsed}"`, final `patch_card`.
- `error`: header `template="red"`, subtitle = `"运行失败"`, error text
  appended (not replacing) to `streaming_content` as a fenced block.

## 5. Streaming lifecycle & rate limits

### 5.1 Run timeline

```
event                       action                          feishu call
─────                       ──────                          ───────────
inbound message arrives     add reaction 🤔                 reactions/create
fold_event → first emit     render → json                   POST cardkit/v1/cards
                                                            → card_id
                            reply (group) / create (DM)     POST im/v1/messages
                            msg_type=interactive            (msg_type=interactive)
                            body={type:card,data:{card_id}}
first text_delta            toggle reaction ✏️              reactions/{add,delete}
                            streamCardContent(seq=0)        PUT cards/{id}/elements/
                                                              streaming_content/content
subsequent text_delta       streamCardContent(seq=k)        PUT … (100ms throttle)
tool_call / artifact /      patch_card(full json)           PATCH cards/{id}
ask_user / etc.             (1.5s throttle + bypasses)
done                        finalize(streaming_mode=false)  PATCH cards/{id}
                            toggle reaction ✅
error                       finalize(template=red)          PATCH cards/{id}
                            toggle reaction ❌
```

### 5.2 Two throttle channels

- **`streamCardContent`** — 100 ms inter-message gap. Reuses
  `RenderState.last_edit_monotonic`; bucket per `element_id`.
- **`patch_card`** — 1.5 s inter-message gap. Coalesces multiple
  state mutations into one full-card PATCH.

**Bypass list** (fire patch immediately regardless of throttle):
`done`, `error`, `ask_user` first appearance, `sandbox_confirm` first
appearance, first non-text_delta tool/artifact event after a long pause.

### 5.3 Sequence numbers

`CardState.next_seq: int` monotonically increases; every `stream_text`
or `patch_card` consumes one. The tailer is a single consumer; no lock
needed.

### 5.4 Finalize discipline

Terminal patch (`done` / `error`) must set
`config.streaming_mode=false`. Otherwise the card stays half-locked and
forwarding/interactions are disabled. Finalize is **idempotent and
retried up to 10 attempts with exponential backoff capped at 30 s**
(200ms→500ms→1s→3s→10s→30s→30s…), giving up to ~2.5 minutes total.
After exhaustion the half-locked state is accepted, the failure is
logged with `card_id`, and the reaction is set to ❌. Finalize is the
single error mode worth the long tail because a half-locked card is
the worst user-facing outcome — and 2.5 minutes is long enough to ride
out routine Feishu blips without being unbounded.

### 5.5 Reaction lifecycle

| State | Reaction | Trigger |
|---|---|---|
| Run started | 🤔 THINKING | `on_processing_start` |
| First text_delta or tool_call | ✏️ TYPING | tailer first emit |
| AskUser / SandboxConfirm pending | ⏳ WAITING | `pending_input` populated |
| `done` | ✅ DONE | `on_processing_complete` |
| `error` / finalize unrecoverable | ❌ FAILED | `on_processing_failed` |

## 6. Inbound action callback path

### 6.1 Event shape

CardKit button click triggers a `card.action.trigger` event delivered
through the same two channels as messages (long-connection + webhook).

```json
{
  "schema": "2.0",
  "header": { "event_type": "card.action.trigger", "tenant_key": "...",
              "token": "<30-min one-time token>" },
  "event": {
    "operator": { "open_id": "ou_..." },
    "action": { "tag": "button",
                "value": { "action": "ask_user",
                           "run_id": "run_xxx",
                           "choice": "yes" } },
    "context": { "open_message_id": "om_...",
                 "open_chat_id": "oc_..." }
  }
}
```

### 6.2 Router

`card_action_router.dispatch(payload) → ResumeAction` is pure (no IO).
Thin IO wrapper invokes cubepi `agent_runtime.resume_with_human_input`.

```
inbound card.action ─┐
                     ↓
        validate signature (feishu/signature.py)
                     ↓
        check token replay (Redis SETNX, 30-min TTL)
                     ↓
        parse action.value → ActionPayload
                     ↓
        identity gate: payload.operator.open_id ==
                       Redis.get(f"run:{run_id}:awaiting_responder")?
                     ↓
        dispatch:
          ask_user        → resume_run(run_id, human_input=choice)
          sandbox_confirm → resume_run(run_id, sandbox_decision=choice)
          open_url        → no-op (handled by button.behavior natively)
          unknown         → log + 200 + toast "未知操作"
                     ↓
        respond 200 immediately
        (optionally toast: {"toast":{"type":"success","content":"已确认"}})
                     ↓
        async: renderer replaces pending_input with receipt + patch_card
```

### 6.3 Security gates

1. **Feishu signature** (`X-Lark-Signature` + body). Failure → 401.
2. **Token replay** — Redis `SETNX cardkit:token:<token> 1 EX 1800`.
   Replay → 200 empty.
3. **Responder identity** — `event.operator.open_id` must match
   `Redis.get(f"run:{run_id}:awaiting_responder")`. Mismatch → 200 +
   toast `"这不是发给你的"`.
4. **Run state** — the run referenced in `value.run_id` must currently
   be paused awaiting human input. Late clicks against a finished /
   aborted / never-paused run → 200 + toast `"会话已结束"`. The exact
   field/state-machine call to query this is cubepi-side and resolved
   in the implementation plan (`agent_runtime.is_awaiting_input(run_id)`
   or equivalent).

### 6.4 Receipt UI

After click the `pending_input` slot is replaced with one non-interactive
line:

```
✓ 已选择「是」 · 由 @某某 操作 · 14:32
```

The original question markdown stays visible above for context.

### 6.5 Timeout

If `pending_input` stays unanswered for 10 minutes, cubepi's existing
timeout path fires `resume_with_human_input(None, timeout=True)` and the
renderer patches the slot to:

```
⏰ 超时已忽略（10 分钟无响应）
```

## 7. Error handling

### 7.1 Error matrix

| Source | Example | Handling |
|---|---|---|
| CardKit `create_entity` | 5xx / network | 200ms→1s→3s retry; still fails → §7.4 emergency text |
| `streamCardContent` rate-limit | 230020 | Skip, flood strike +1; 3 strikes disable streaming, terminal patch still fires |
| `patch_card` rate-limit | 230020 | Coalesce to next flush; not counted as strike |
| Element validation | 200570 invalid image_key | Pre-filtered in `optimize_markdown_style`; single failure drops element + log, no retry |
| Card 14-day expiry | long run | Out of envelope (runs are seconds, not days); ignored |
| Finalize PATCH | any | Exponential backoff to 30s, 10 attempts (~2.5 min); on exhaustion accept half-locked + log + ❌ reaction |
| Inbound signature | forged request | 401 |
| Inbound token replay | duplicate token | 200 empty (idempotent) |
| Inbound responder mismatch | bystander click | 200 + toast "这不是发给你的" |
| AskUser timeout | 10 min no click | cubepi timeout path + patch slot to "超时已忽略" |
| Renderer exception | bug | log, §7.4 emergency text with cached buffer |

### 7.2 Flood strike

Reuse existing `OutboundRunTailer.note_flood_strike` /
`note_edit_success`. Semantics unchanged: 3 consecutive strikes disable
streaming, terminal patch still fires.

### 7.3 Stale-epoch guard

If the user cancels the run (abort) while a patch is in-flight, the
renderer marks `state.epoch += 1`. Late responses for the previous epoch
are dropped without retry, preventing zombie updates to the now-aborted
card.

### 7.4 Emergency text fallback

Only path that bypasses CardKit: when `create_entity` retries are
exhausted, set `state.card_unavailable = True` and send a single text
message via `_send_emergency_text`:

> `⚠️ 飞书富文本渲染暂时不可用，结果将以文本展示`

Subsequent ops in this run fall back to the legacy `text` path (which
remains in `_send_emergency_text` only, renamed and minimally
maintained). Critical: this is the **only** dual-path code; every other
failure mode is handled by backoff or graceful element omission.

## 8. Migration plan

Hard cutover (CLAUDE.md "no backward-compat shim"; project pre-public).

### 8.1 Delete

- `FeishuConnector._build_payload`
- `FeishuConnector.post_placeholder` / `edit` / `send_text_message`
  (replace with `_send_emergency_text` covering all three behaviors)
- `_MARKDOWN_TABLE_RE` / `_MARKDOWN_HINT_RE` and the post-vs-text branch
- `fold_event`'s `_running 'xxx'…_` synthesis
- `IMArtifactDispatcher` share-link text format

### 8.2 Change

- `OutboundOp.kind`: new union
  `"card_create" | "stream_text" | "patch_card" | "finalize"`.
- `RenderState`: add `card_id`, `card_state`, `next_seq`, `epoch`;
  remove `text_buffer`, `tool_lines`.
- `fold_event(event, state, now) → OutboundOp | None`: mutates
  `state.card_state` and returns op kind.
- `IMArtifactDispatcher.handle(artifact)`: updates `card_state.artifacts`
  and returns a `patch_card` op.

### 8.3 Add

- `backend/cubeplex/im/feishu/card_model.py`
- `backend/cubeplex/im/feishu/card_renderer.py`
- `backend/cubeplex/im/feishu/cardkit_client.py`
- `backend/cubeplex/im/feishu/card_action_router.py`
- `card.action.trigger` branch in
  `backend/cubeplex/api/routes/v1/im_feishu_events.py`
- Out-of-repo: `cardkit:card:write` scope on the Feishu app (operator
  step, before release)

## 9. Testing

Three layers:

| Layer | Tooling | Coverage |
|---|---|---|
| **Unit** | pytest | `card_renderer.render(state)` JSON snapshot per state shape; `fold_event` state mutations; `card_action_router.dispatch(payload)` per action; `optimize_markdown_style` edge cases |
| **Integration** | pytest + httpx mocked CardKit | Full tailer run with recorded cubepi event sequence → expected CardKit call sequence (create + stream + patch + finalize), including throttle behavior, flood strike, finalize retry |
| **E2E** | playwright + user-hosted Feishu test tenant | Real run → assert card JSON via Feishu SDK fetch; AskUser click loop; emergency fallback mock |

Per CLAUDE.md, run unit + integration on every change, full E2E once in
the pre-PR sweep. The worktree DB router already handles the worktree
slot.

### 9.1 New E2E cases

1. Markdown rendering: send a prompt that produces headings + tables +
   code blocks; assert card markdown element content.
2. Tool call panel: prompt invokes 2 tools; assert two collapsible rows
   with icons + result code blocks.
3. Artifact card: trigger code artifact; assert artifact slot has a
   container with two buttons.
4. AskUser closed loop: agent emits ask_user → click button → assert
   run resumes with the choice → assert receipt UI.
5. Emergency fallback: mock `create_entity` 500 → assert text path
   takes over.

### 9.2 Migrating existing IM E2E

All `bubble.text contains X` assertions become `card has element with
text X`. Shared helper `assert_card_contains(text)` to be added in
`tests/e2e/im_feishu_helpers.py`.

## 10. Out of scope (deferred to v2)

- Server-side HTML widget rendering to image
- Deep SubAgent expansion (sub-tool list rendered)
- Citation hover popovers (CardKit limitation; v1 uses inline links)
- VChart chart elements (agent doesn't emit chart specs)
- Token usage / failover banner / share panel meta widgets
- Multi-locale card content switching (single locale per workspace)
- Group `@mention` parsing enhancements
- Edit-after-finalize for retroactive citations

## 11. Risks / follow-ups

1. **CardKit edit quota under streaming.** openclaw's separate throttles
   suggest separate quotas vs IM patch, but unverified at our scale.
   Plan: instrument `streamCardContent` and `patch_card` error rates
   from day one. If 230020 fires more than a few percent of the time,
   tune the throttles.
2. **Out-of-repo permission step.** Adding `cardkit:card:write` to the
   Feishu app is a manual action in the Feishu admin console. Pre-PR
   checklist must include "scope applied". Doc lives in
   `backend/docs/im-feishu-rich-output.md` (new).
3. **cubepi event payload completeness.** Tailer today only consumes a
   subset of event fields (`tool_call` uses only `name`). v1 needs
   `args`, `result`, `elapsed_ms`, `error` on tool events, plus a
   `citation` event carrying `(index, url, title)` for §4.2 step 5.
   Verify cubepi is already emitting these; missing fields become
   `cubepi-upstream-first` changes ahead of the cubeplex PR.
4. **Sub-agent event shape.** Today's `sub_agent_*` events should
   already carry `name` + tool count; if not, gate v1 SubAgent line on
   the cubepi event payload landing first.
5. **E2E flakiness.** Real Feishu test tenant introduces external
   dependency. Mitigate by keeping E2E gates only in the local sweep,
   not CI (matches existing IM Feishu E2E posture).
