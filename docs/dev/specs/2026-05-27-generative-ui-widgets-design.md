# Generative UI Widgets — Design

**Date:** 2026-05-27
**Branch:** `feat/generative-ui-widgets`
**Status:** Design (approved in brainstorming, pending spec review)

## What we're building

Let the agent render live, interactive HTML/JS widgets inline in the chat
stream — charts, sliders, diagrams, animations — that assemble themselves
token-by-token as the model generates them. This is the Claude.ai "artifacts /
generative UI" experience, adapted for cubeplex's web frontend.

The model calls a new `show_widget` tool whose `widget_code` parameter is an
HTML fragment. That fragment streams to the browser and renders inside a
sandboxed iframe, morphed in place (no flicker) as more tokens arrive.

### Why this is cheap to build

The streaming data channel already exists end to end. `show_widget`'s
`widget_code` travels the exact same path `write_file`'s `content` does today:

```
toolcall_delta (cubepi) → SSE tool_call_delta (raw partial_json)
  → messageStore accumulates args_text
  → extractJsonStringPrefix(...) pulls the in-progress string value
```

cubepi's `tool_call_delta` does **not** carry parsed arguments mid-stream — the
`partial.arguments` dict stays `{}` until `toolcall_end`
(`cubepi/providers/anthropic.py:586-637`). It emits the raw `partial_json`
fragment on each delta. The frontend already accumulates those fragments
(`messageStore.ts:430`) and extracts the live string value with a hand-rolled
tolerant parser (`lib/writeFilePreview.ts` `extractJsonStringPrefix`). We reuse
that machinery; we do not touch the streaming pipeline.

## Decisions (locked in brainstorming)

- **v1 scope:** full HTML+JS interactive widgets (not SVG-only).
- **Render surface:** inline in the chat bubble (not the side artifact panel).
- **Streaming:** live morphdom rendering — watch it build, not render-on-complete.
- **Design guidelines prompt:** our own, rewritten in our words, informed by
  (not copied verbatim from) the extracted Claude guidelines.
- **Rendering architecture:** Approach A — shell iframe + structured
  postMessage (chosen over per-frame `srcDoc` reset, which reintroduces the
  flicker we're trying to avoid).

## Architecture

```
model decides to visualize
   │
   ▼
[backend] show_widget tool (cubepi AgentTool)
   │  widget_code streams as a tool-call parameter
   ▼
[existing] agents/stream.py:109  toolcall_delta → SSE tool_call_delta (raw partial_json)
   │                              toolcall_end   → SSE tool_call (full arguments)
   ▼
[existing] messageStore.ts:430   accumulate args_text on a tool_call_streaming block
   │
   ▼
[new] extractWidgetCode(args_text)   ← reuses extractJsonStringPrefix
   │
   ▼
[new] <WidgetView>  sandboxed iframe (srcDoc = shell runtime)
   │      parent → child: postMessage({widgetId, seq, type:'morph', html})  per frame
   │      parent → child: postMessage({widgetId, seq, type:'finalize'})      on toolcall end
   ▼
[new] iframe shell runtime: morphdom diff + fade-in + run <script> on finalize
```

### Change map

| Layer | File | Change |
|---|---|---|
| Backend tool | new `middleware/widget.py` (or `tools/`) | define `show_widget` AgentTool; `execute()` returns a light ack |
| Backend prompt | new `prompts/widget.py` | self-authored design guidelines, injected when the tool is available |
| Backend stream | `agents/stream.py` | **no change** — widget_code already streams via tool_call_delta |
| Frontend store | `messageStore.ts` | **essentially no change** — tool_call_streaming block is already generic |
| Frontend extract | next to `lib/writeFilePreview.ts` | `extractWidgetCode` reusing `extractJsonStringPrefix` |
| Frontend component | new `components/.../WidgetView.tsx` | sandboxed iframe + postMessage |
| Frontend shell | new `widgetShell.ts` (srcDoc string) | morphdom runtime + message listener |
| Frontend mount | `AssistantMessage.tsx:314` | render `<WidgetView>` when `block.name === 'show_widget'` |

### Persistence (completed widgets only)

A **completed** `show_widget` tool call (full `widget_code`) is already persisted
in the Postgres message history. On reload / history view, the frontend
re-renders from the stored `tool_call.arguments.widget_code` with a one-shot
`morph` + `finalize` — no separate artifact table or storage logic.

**Interrupted widgets are not persisted in v1.** `buildTurnMessages()`
(`messageStore.ts:541`) filters out `tool_call_streaming` blocks when it builds
the assistant message, so a widget cancelled mid-stream shows its frozen partial
frame during the live session but **disappears on the next turn finalize /
reload**. This matches the current behavior for every streaming block; v1
accepts it. Persisting a partial widget would require changing
`buildTurnMessages` to convert an interrupted `show_widget` streaming block into
a `tool_call` block carrying the partial `widget_code` — explicitly out of scope
for v1.

## Components and interfaces

Each unit is bounded so it can be understood and tested independently.

### Backend

**`show_widget` tool** (new `middleware/widget.py`)
- **Does:** declares the tool schema so the model can stream a widget. The
  `execute()` does no real work — it returns a light ack (`{"status":"rendered"}`)
  because rendering happens entirely in the frontend.
- **Interface:** params `{ title: str, widget_code: str, width?: int, height?: int }`.
  `widget_code` is an HTML fragment (no `<html>`/`<body>`).
- **Depends on:** `cubepi.AgentTool`. Registered in the same place the agent
  assembles its tool set (alongside `_make_write_file_tool`).
- **Boundary:** does not touch the sandbox or the streaming pipeline.

**Design-guidelines prompt** (new `prompts/widget.py`)
- **Does:** exports a self-authored design-constraint string (CSS variables for
  color, two font weights, streaming-safe structure `style → content → script`,
  no gradients/shadows, libraries only from the CDN allowlist) and an
  injection switch.
- **Interface:** `WIDGET_GUIDELINES: str` + a "when to inject" hook.
- **Boundary:** v1 injects the whole block (no lazy per-module `read_me`
  loading — YAGNI). Plain string asset, no dependencies.

### Frontend

**`extractWidgetCode(argsText)`** (next to `lib/writeFilePreview.ts`)
- **Does:** pulls the current `widget_code` string value out of the accumulated
  partial JSON.
- **Interface:** `(raw: string) => string`.
- **Depends on:** `extractJsonStringPrefix(raw, 'widget_code')` — already proven
  to handle unterminated/escaped input. **It is currently a private function in
  `writeFilePreview.ts`.** First refactor: promote it to a shared module (e.g.
  `lib/partialJson.ts`) and export it, since it's generic partial-JSON
  string-prefix extraction, not write-file-specific. `writeFilePreview.ts` and
  `extractWidgetCode` both import the shared version — no duplicated parser.
- **Boundary:** pure function, unit-testable.

**`<WidgetView>`** (new component)
- **Does:** renders the sandboxed iframe; pushes incremental/complete
  `widget_code` in via postMessage; manages finalize timing.
- **Interface:** props `{ widgetCode: string, status: 'streaming' | 'complete',
  title, width?, height? }`.
- **Depends on:** the shell HTML string; `window.postMessage`.
- **Boundary:** only feeds HTML into the isolated container. Does not parse or
  fetch data (parent passes it in).

**iframe shell runtime** (new `widgetShell.ts`, exports the srcDoc string)
- **Does:** inside the iframe, listens for parent messages — `morph` → morphdom
  diff `#root` + fade-in new nodes; `finalize` → clone-and-run `<script>` tags.
- **Interface (postMessage protocol):** every message carries `widgetId`.
  **Parent → child** messages additionally carry a monotonically increasing
  `seq` (the ordering token); **child → parent** messages do not need `seq`
  (they are status notifications, not an ordered stream). The canonical shapes:
  - parent → child: `{widgetId, seq, type:'morph', html}` / `{widgetId, seq, type:'finalize'}`
  - child → parent: `{widgetId, type:'ready'}` / `{widgetId, type:'error', message}` /
    `{widgetId, type:'resize', height}` (height auto-fit)
  - **Latest-wins (parent → child only):** the shell tracks the highest `seq` it
    has applied. A `morph` or `finalize` with `seq` ≤ the last applied is
    **ignored** — this defuses the debounce/finalize race (a delayed `morph`
    landing after `finalize` cannot regress the DOM). `finalize` runs `<script>`
    exactly once; later `morph`s below the finalized `seq` are dropped.
  - **Note on shorthand:** prose and the lifecycle/diagram examples elsewhere in
    this doc abbreviate messages by `type` (e.g. "a `morph`", "`{type:'ready'}`")
    for readability; the shapes above are authoritative.
- **Depends on:** morphdom, loaded from the CDN allowlist.
- **Boundary:** runs inside the sandbox; cannot reach parent DOM/data; only
  responds to postMessage. `error.message` from the child is length-clamped and
  string-checked by the parent before it is rendered or logged.

**Mount point** (`AssistantMessage.tsx`)
- **Does (streaming):** in the `tool_call_streaming` branch (~`:314`), when
  `block.name === 'show_widget'`, `extractWidgetCode(block.args_text)` →
  render `<WidgetView status="streaming">`.
- **Does (completed):** `AssistantMessage.tsx` already special-cases completed
  `tool_call` blocks by name **before** the generic `ToolCallGroup` fallback
  (`subagent` ~`:230`, `save_artifact` ~`:256`, then `ToolCallGroup` ~`:303`).
  Add a `block.type === 'tool_call' && block.name === 'show_widget'` branch in
  that same spot so a completed widget renders `<WidgetView status="complete">`
  and is **excluded from `ToolCallGroup`** (otherwise it would collapse into the
  generic tool-call list instead of rendering).
- **Boundary:** sits alongside the existing `write_file` / `subagent` /
  `save_artifact` branches; they don't interfere.

**Key interface rule:** parent↔child exchange **structured data only**, in the
shapes defined by the postMessage protocol above (parent→child carry `seq`,
child→parent do not). The shell does its own morphdom. We never concatenate HTML
into a JS string for injection — this is the security improvement over
pi-generative-ui's `escapeJS(...)` `win.send(jsString)` approach.

## Data flow and lifecycle

### Streaming (main path)

```
toolcall_start (name=show_widget)
  → store creates tool_call_streaming block (args_text="")
  → AssistantMessage renders <WidgetView status="streaming">
  → WidgetView mounts iframe (srcDoc=shell), waits for child 'ready'

toolcall_delta ×N
  → store: args_text += partial_json fragment
  → extractWidgetCode(args_text) → current HTML prefix
  → WidgetView receives new widgetCode prop
  → debounce ~120ms → postMessage({widgetId, seq, type:'morph', html})
  → shell: morphdom diff #root, fade-in new nodes

toolcall_end → SSE tool_call (full arguments)
  → store: tool_call_streaming converges to a tool_call block
  → WidgetView status='complete', widgetCode=final
  → postMessage({widgetId, seq, type:'morph', html}) with the final html, then postMessage({widgetId, seq, type:'finalize'})
  → shell: run <script> (Chart.js etc. execute only now)
```

**Script timing:** `<script>` runs **once, on finalize**. Mid-stream morphs
place script nodes in the DOM but do not execute them (inert scripts, as in the
pi version), so Chart.js never initializes on half-complete data.

**'ready' race:** the shell must finish loading morphdom (CDN) **and install its
message listener** before it can accept a morph. Two-sided guard:
- **Parent side (authoritative):** `WidgetView` does not send any `morph` until
  it has received `{type:'ready'}` from the child. It holds the latest
  `widgetCode` and sends it the moment `ready` arrives. This is the real fix —
  messages sent before the listener exists are simply never sent.
- **Child side (belt-and-suspenders):** the shell also caches with `_pending`
  (as in the pi version's `window._pending`) in case morphdom finishes loading
  after the listener is installed but the first morph arrives in between.

### Debounce ownership

Debounce lives in **WidgetView** (the component), not the store. The store
accumulates `args_text` faithfully every frame (other consumers, e.g. a debug
view, may want it real-time); only the "push to iframe" step is batched at
~120ms for smooth visuals. On `finalize`, any pending debounced `morph` timer is
**cancelled** before sending the final `morph` + `finalize`; combined with the
`seq` latest-wins guard in the shell, a stale morph cannot land after finalize.

### Reload / history view

The message history stores the full `tool_call.arguments.widget_code`.
Reopening a conversation:
- the block is a `tool_call` (not streaming), `WidgetView status='complete'`;
- one-shot `morph(final html)` + `finalize`;
- no streaming animation — just the finished widget. No special persistence
  logic.

### Interruption / errors

| Case | Handling |
|---|---|
| User stops mid-stream | toolcall never ends → frozen on the last morphed frame for the live session; no finalize, scripts don't run. On turn finalize/reload the partial block is dropped by `buildTurnMessages` (see "Persistence"), so the widget is not retained. Acceptable for v1. |
| `widget_code` ends unterminated | `extractWidgetCode` returns the prefix; finalize still fires; partial HTML renders (browser auto-closes tags). |
| Shell runtime throws | child → parent `{type:'error'}` → WidgetView shows a fallback: a collapsible source block (reuses existing code highlighting). |
| morphdom CDN fails to load | shell `onerror` → fall back to source block. v1 accepts the CDN dependency (same risk class as the existing artifact preview). |
| morphdom CDN **stalls** (no error, no load) | `WidgetView` arms a **shell-readiness timeout** (e.g. 5s after iframe mount): if no `{type:'ready'}` arrives, fall back to the source block rather than sitting blank. |

### Multiple widgets

One message may contain several `show_widget` calls (different `content_index`).
The store keys `tool_call_streaming` blocks by `index` (`messageStore.ts:445`) —
**this depends on cubepi emitting a stable `content_index` per concurrent tool
call**, which it does today (the index originates from the provider's content
block index). Each block → its own `<WidgetView>` → its own iframe. Naturally
isolated, no extra handling. (This avoids Streamdown issue #51
— multiple mermaid blocks showing only the last — because each widget is an
independent iframe and store block, not a shared render target.)

## Security model

Inline full HTML+JS is the highest-risk part: model-generated arbitrary scripts
run in the user's browser. Isolation rests on three walls.

### 1. iframe sandbox attribute

```
sandbox="allow-scripts"
```

- **Include** `allow-scripts`: widgets need JS.
- **Never include** `allow-same-origin`. With both `allow-scripts` and
  `allow-same-origin`, the frame gets its own origin and scripts can remove the
  frame's own sandbox attribute and escape. Keeping them mutually exclusive
  makes the widget iframe an **opaque origin** (`origin = "null"`): it cannot
  read parent cookies/localStorage/DOM.
- The cost: `localStorage`/`sessionStorage` throw inside the iframe. This
  matches the Claude artifact constraint (use in-memory variables, not storage)
  — encode it in the prompt guidelines.
- No `allow-top-navigation`, no `allow-popups`, no `allow-forms` in v1.
- Use `srcDoc` (not a remote `src`) to load our own shell string.

### 2. CSP (a `<meta http-equiv="Content-Security-Policy">` in the shell)

There is **no HTTP response header** for a `srcDoc` document — the policy must
ride as a `<meta http-equiv>` tag, and it must be the **first element in
`<head>`, before any `<script>`, `<style>`, or resource reference**, or the
directives won't apply to content parsed before the meta tag.

```
default-src 'none';
script-src 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://unpkg.com https://esm.sh;
style-src 'unsafe-inline';
img-src data: https:;
font-src data: https:;
connect-src 'none';
base-uri 'none';
form-action 'none';
worker-src 'none';
frame-src 'none';
object-src 'none';
```

- `default-src 'none'`: deny by default, open per directive.
- `script-src` allowlist = the same four CDNs as the pi version.
  `'unsafe-inline'` is required (the widget's scripts are inline) — the opaque
  origin sandbox is the backstop for that.
- **`connect-src 'none'`** blocks fetch/XHR/WebSocket. Note it is **not** a
  complete exfiltration seal (see residual risk below): with `img-src`/`font-src`
  allowing `https:`, widget JS can still encode data into an outbound image/font
  URL. `connect-src 'none'` blocks the easy/bulk channels (POST a payload), not
  every covert channel.
- `base-uri 'none'`, `form-action 'none'`, `worker-src 'none'`, `frame-src 'none'`,
  `object-src 'none'`: harden against base-tag hijack, form posts, worker-based
  network access, and nested frame/plugin loads.
- `img-src`/`font-src` allow `https:` + `data:` so charts/illustrations can use
  images. **Decision:** v1 keeps broad `https:` here because widgets legitimately
  pull images from arbitrary sources; we accept the residual image/font-URL
  exfil channel rather than maintain an image-CDN allowlist. If exfil resistance
  later becomes a hard requirement, tighten `img-src`/`font-src` to `data:` only
  (+ an allowlist) — called out as a future tightening, not v1.

### 3. postMessage validation (both directions)

- **parent → child:** WidgetView only `iframe.contentWindow.postMessage(msg, '*')`.
  Because the iframe is opaque-origin, `'*'` targetOrigin is unavoidable. The
  message body is the widget HTML — which **may contain conversation/user data**
  the model embedded (since data must live in `widget_code`, per the
  `connect-src 'none'` decision). This is acceptable because the **only** frame
  that can receive it is our own sandboxed widget iframe (a child we created with
  a known `srcDoc`); `'*'` does not broadcast to unrelated frames. We rely on the
  sandbox keeping that data inside the opaque-origin frame, not on `'*'` secrecy.
- **child → parent:** the parent listener **must** verify
  `event.source === iframe.contentWindow` (object identity — `event.origin` is
  `"null"` for opaque origins and unreliable) and that `event.data.type` is in
  the `{ready, error, resize}` allowlist. Everything else is dropped.
- When handling child messages, the parent reads **only the agreed fields**
  (e.g. `resize.height` via `Number()` + clamped to an upper bound). No eval,
  no injection.

### Not copied from pi

| pi-generative-ui | us | reason |
|---|---|---|
| `win.send(jsString)` eval | structured postMessage | no JS-injection seam beyond the widget itself |
| native WKWebView full capabilities | sandbox + CSP opaque iframe | web requires isolation; native didn't |

### Residual risk (stated explicitly)

- `'unsafe-inline'` script can't be avoided (inline scripts are the point);
  mitigated by opaque origin + tightened CSP: a widget **can** misbehave inside
  its iframe but cannot escape the sandbox or read parent/user data.
- **Network exfiltration is reduced, not eliminated.** `connect-src 'none'`
  blocks fetch/XHR/WebSocket, but `img-src`/`font-src https:` leaves a covert
  channel (encode data in an image URL). v1 accepts this; full sealing would
  require `data:`-only image/font sources.
- **Resource exhaustion is not prevented.** Sandbox + CSP isolate *data access*,
  not CPU/memory. A finalized widget can run an infinite loop or allocate until
  the tab hangs. v1 mitigation: the widget runs in its own iframe (so it degrades
  that frame first, not the whole app) + a `widget_code` size cap (see Limits).
  Hard CPU/timeout enforcement is out of scope for v1.
- CDN supply chain: theoretical poisoning of an allowlisted CDN, same risk
  level as the existing artifact preview. v1 accepts.
- This is the standard artifact security posture — trust boundary = inside the
  iframe.

### Limits (production guards)

- **Max `widget_code` size:** cap the accumulated `args_text` / final
  `widget_code` (e.g. ~256KB); beyond it, stop morphing and show the source-block
  fallback. Bounds memory and the resource-exhaustion surface.
- **Max rendered height:** clamp the `resize` height to an upper bound so a
  runaway widget can't grow unbounded in the chat.
- **Shell readiness timeout:** 5s after mount with no `{type:'ready'}` → source
  fallback (see Interruption table).
- **`error.message` bound:** length-clamp + type-check before render/log.

## Testing strategy

Per repo discipline (E2E over mocks): E2E what can be exercised end to end; unit
only pure functions.

### Unit (vitest)

**`extractWidgetCode` / `extractJsonStringPrefix`** — core of streaming
correctness, cover the edges:
- complete JSON extracts the value;
- unterminated `{"widget_code":"<div>` → returns the prefix so far;
- escape sequences `\n` `\"` `\\` `\uXXXX` decode correctly;
- a `"` inside the HTML (escaped) is not mistaken for the field end;
- empty / missing key → empty string.

Cheapest layer, highest regression value (streaming bugs almost always live here).

### E2E (Playwright, `tests/e2e/`, marker auto-applied)

**Main path — streaming render:** use the faux provider
(`cubepi/providers/faux.py`) to script a `show_widget` tool call whose
`widget_code` is emitted across several frames, then assert:
- iframe appears with `sandbox="allow-scripts"` and **without** `allow-same-origin`;
- mid-stream, `#root` already has partial nodes (morph works — not waiting for end);
- after end, the script executes (e.g. a script writing `window.__ran=true` or
  known text into the DOM, assert it appears) — verifies finalize timing;
- the script runs **once** (`__count++`, assert === 1);
- **latest-wins:** after `finalize`, a late/duplicate `morph` (lower `seq`) does
  not regress the DOM — assert the finalized content stays put.

**Reload path:** send a widget → refresh → assert the iframe renders the
finished result (no streaming) and the script has executed.

**Fallback paths:**
- shell error: faux emits input that makes the shell error (or mock a CDN
  failure) → assert fallback to a collapsible source block, no blank frame;
- readiness timeout: block the morphdom CDN so `ready` never fires → assert the
  5s timeout fallback to the source block, not a permanently blank frame.

**Multiple widgets:** one message with two `show_widget` calls → assert two
independent iframes each render (guards against the Streamdown-#51 cross-talk
class).

### Security assertions (folded into the E2E above)

- opaque origin: a widget trying to read `parent.document` → assert it throws /
  is blocked;
- `fetch('https://...')` inside a widget → assert blocked by CSP `connect-src 'none'`;
- the parent listener ignores a forged-`event.source` message (inject a message
  from a non-iframe source, assert no side effect).

### Not tested

- no test for morphdom itself (third-party, trusted);
- no pixel screenshot comparison (brittle, and widget content is
  model-defined — no stable baseline);
- visual feel (fade-in animation) is confirmed manually in-browser during dev +
  user review, not automated.

**Toolchain note:** this runs on the worktree ports (8075/3075); the faux
provider is selected via config switch to avoid real model calls. The exact
fixture wiring is settled in the implementation plan.

## Out of scope (v1)

- Lazy per-module guideline loading (pi's `read_me` pattern) — inject one block.
- Side artifact-panel rendering — inline only.
- Widgets fetching live data (blocked by `connect-src 'none'`) — model writes
  data into `widget_code`.
- A saved/standalone widget artifact type — persistence rides on message history.
- Persisting interrupted/partial widgets (would need a `buildTurnMessages` change).
- Hard CPU/memory/timeout enforcement on widget scripts (only iframe isolation +
  size cap in v1).
- Image/font-CDN allowlisting to fully seal exfiltration (broad `https:` in v1).
