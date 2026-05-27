# Generative UI Widgets — Design

**Date:** 2026-05-27
**Branch:** `feat/generative-ui-widgets`
**Status:** Design (approved in brainstorming, pending spec review)

## What we're building

Let the agent render live, interactive HTML/JS widgets inline in the chat
stream — charts, sliders, diagrams, animations — that assemble themselves
token-by-token as the model generates them. This is the Claude.ai "artifacts /
generative UI" experience, adapted for cubebox's web frontend.

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
   │      parent → child: postMessage({type:'morph', html})  per frame
   │      parent → child: postMessage({type:'finalize'})      on toolcall end
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

### Persistence is free

The `show_widget` tool call (including the full `widget_code`) is already
persisted in the Postgres message history. On reload / history view, the
frontend re-renders from the stored `tool_call.arguments.widget_code` with a
one-shot `morph` + `finalize` — no separate artifact table or storage logic.

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
- **Depends on:** existing `extractJsonStringPrefix(raw, 'widget_code')` — already
  proven to handle unterminated/escaped input.
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
- **Interface (postMessage protocol):**
  - parent → child: `{type:'morph', html}` / `{type:'finalize'}`
  - child → parent: `{type:'ready'}` / `{type:'error', message}` /
    `{type:'resize', height}` (height auto-fit)
- **Depends on:** morphdom, loaded from the CDN allowlist.
- **Boundary:** runs inside the sandbox; cannot reach parent DOM/data; only
  responds to postMessage.

**Mount point** (`AssistantMessage.tsx:314`, extending the `tool_call_streaming` branch)
- **Does:** when `block.name === 'show_widget'`, `extractWidgetCode(block.args_text)`
  → render `<WidgetView status="streaming">`; when matched by a completed
  `tool_call` block, `status="complete"`.
- **Boundary:** sits alongside the existing `write_file` preview branch; they
  don't interfere.

**Key interface rule:** parent↔child exchange **structured data only**
(`{type, html}`). The shell does its own morphdom. We never concatenate HTML
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
  → debounce ~120ms → postMessage({type:'morph', html})
  → shell: morphdom diff #root, fade-in new nodes

toolcall_end → SSE tool_call (full arguments)
  → store: tool_call_streaming converges to a tool_call block
  → WidgetView status='complete', widgetCode=final
  → postMessage({type:'morph', html=final}) then postMessage({type:'finalize'})
  → shell: run <script> (Chart.js etc. execute only now)
```

**Script timing:** `<script>` runs **once, on finalize**. Mid-stream morphs
place script nodes in the DOM but do not execute them (inert scripts, as in the
pi version), so Chart.js never initializes on half-complete data.

**'ready' race:** the shell must finish loading morphdom (CDN) before it can
morph. The shell caches with `_pending`: HTML arriving before `ready` is stored
and flushed once `ready` fires (same logic as the pi version's `window._pending`).

### Debounce ownership

Debounce lives in **WidgetView** (the component), not the store. The store
accumulates `args_text` faithfully every frame (other consumers, e.g. a debug
view, may want it real-time); only the "push to iframe" step is batched at
~120ms for smooth visuals.

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
| User stops mid-stream | toolcall never ends → frozen on the last morphed frame; no finalize, scripts don't run. Acceptable (show "stopped"). |
| `widget_code` ends unterminated | `extractWidgetCode` returns the prefix; finalize still fires; partial HTML renders (browser auto-closes tags). |
| Shell runtime throws | child → parent `{type:'error'}` → WidgetView shows a fallback: a collapsible source block (reuses existing code highlighting). |
| morphdom CDN fails to load | shell `onerror` → fall back to source block. v1 accepts the CDN dependency (same risk class as the existing artifact preview). |

### Multiple widgets

One message may contain several `show_widget` calls (different `content_index`).
The store already keys `tool_call_streaming` blocks by `index`
(`messageStore.ts:445`), so each block → its own `<WidgetView>` → its own
iframe. Naturally isolated, no extra handling. (This avoids Streamdown issue #51
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

```
default-src 'none';
script-src 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://unpkg.com https://esm.sh;
style-src 'unsafe-inline';
img-src data: https:;
font-src data: https:;
connect-src 'none';
```

- `default-src 'none'`: deny by default, open per directive.
- `script-src` allowlist = the same four CDNs as the pi version.
  `'unsafe-inline'` is required (the widget's scripts are inline) — the opaque
  origin sandbox is the backstop for that.
- **`connect-src 'none'`** is the key data-exfiltration defense: the widget
  cannot fetch/XHR/WebSocket anywhere, so even a prompt-injected widget cannot
  POST conversation context out. Cost: widgets can't fetch data; data must be
  written directly into `widget_code` by the model. v1 accepts this.
- `img-src`/`font-src` allow `https:` + `data:` so charts/illustrations can use
  images.

### 3. postMessage validation (both directions)

- **parent → child:** WidgetView only `iframe.contentWindow.postMessage(msg, '*')`.
  Because the iframe is opaque-origin, `'*'` targetOrigin is unavoidable but
  acceptable — we put **no sensitive data** in the message (only the widget HTML,
  which is itself a model artifact).
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
  mitigated by opaque origin + `connect-src 'none'`: a widget **can** misbehave
  inside its iframe but **cannot** escape, read user data, or make network
  requests.
- CDN supply chain: theoretical poisoning of an allowlisted CDN, same risk
  level as the existing artifact preview. v1 accepts.
- This is the standard artifact security posture — trust boundary = inside the
  iframe.

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
- the script runs **once** (`__count++`, assert === 1).

**Reload path:** send a widget → refresh → assert the iframe renders the
finished result (no streaming) and the script has executed.

**Fallback path:** faux emits input that makes the shell error (or mock a CDN
failure) → assert fallback to a collapsible source block, no blank frame.

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
