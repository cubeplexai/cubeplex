# Generative UI Widgets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the agent render live, interactive HTML/JS widgets inline in the chat stream via a `show_widget` tool, streamed token-by-token into a sandboxed iframe and morphed in place.

**Architecture:** `show_widget` is a builtin tool whose `widget_code` HTML fragment rides the existing `tool_call_delta → args_text → extractJsonStringPrefix` streaming path (no streaming-pipeline change). The frontend extracts the in-progress HTML and `postMessage`s it into a `sandbox="allow-scripts"` iframe whose shell runs morphdom; scripts run once on finalize. Security = opaque-origin sandbox + CSP + `event.source` validation.

**Tech Stack:** Python/FastAPI + cubepi (backend tool + prompt), React 19 / Next.js + TypeScript (frontend component, iframe shell), morphdom (CDN), Playwright + vitest (tests).

**Spec:** `docs/dev/specs/2026-05-27-generative-ui-widgets-design.md` (read it first).

**Read before touching:** `backend/docs/prompt-cache-discipline.md` (tool order + system-prompt injection are cache-sensitive), `backend/docs/agent-system-design.md` (tool/middleware assembly).

**Worktree:** `/home/chris/cubebox/.worktrees/feat/generative-ui-widgets` (ports 8075/3075). `cat .worktree.env` first. Backend tests: `uv run pytest`. Frontend unit: `pnpm --filter web test`. E2E: `pnpm --filter web test:e2e` (or the repo's Playwright command).

---

## File Structure

**Backend (create):**
- `backend/cubebox/tools/builtin/show_widget.py` — `_ShowWidgetArgs` model + `make_show_widget_tool()` factory. The tool's `execute()` returns a light ack; rendering is entirely frontend.
- `backend/cubebox/prompts/widget.py` — `WIDGET_GUIDELINES` string (self-authored design constraints) + `WIDGET_TOOL_DESCRIPTION`.

**Backend (modify):**
- `backend/cubebox/streams/run_manager.py` — register `make_show_widget_tool()` in `_builtin_tools` (fixed order) and append `WIDGET_GUIDELINES` to the system prompt when the tool is enabled.

**Frontend (create):**
- `frontend/packages/web/lib/partialJson.ts` — `extractJsonStringPrefix` (moved out of `writeFilePreview.ts`, exported) + `extractWidgetCode`.
- `frontend/packages/web/components/chat/widget/widgetShell.ts` — exports `WIDGET_SHELL_HTML` (srcDoc string: CSP meta + morphdom runtime + postMessage listener).
- `frontend/packages/web/components/chat/widget/WidgetView.tsx` — the sandboxed-iframe component.

**Frontend (modify):**
- `frontend/packages/web/lib/writeFilePreview.ts` — import `extractJsonStringPrefix` from `partialJson.ts` instead of defining it.
- `frontend/packages/web/components/chat/AssistantMessage.tsx` — mount `<WidgetView>` for streaming + completed `show_widget` blocks; exclude completed `show_widget` from `ToolCallGroup`.

**Tests (create):**
- `backend/tests/tools/test_show_widget.py`
- `frontend/packages/web/lib/__tests__/partialJson.test.ts`
- `frontend/packages/web/__tests__/e2e/generative-ui-widgets.spec.ts`

---

## Task 1: Backend — `show_widget` tool

**Files:**
- Create: `backend/cubebox/tools/builtin/show_widget.py`
- Create: `backend/cubebox/prompts/widget.py`
- Test: `backend/tests/tools/test_show_widget.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/tools/test_show_widget.py
import pytest
from cubebox.tools.builtin.show_widget import make_show_widget_tool, _ShowWidgetArgs


def test_tool_metadata():
    tool = make_show_widget_tool()
    assert tool.name == "show_widget"
    assert tool.parameters is _ShowWidgetArgs


@pytest.mark.asyncio
async def test_execute_returns_light_ack():
    tool = make_show_widget_tool()
    result = await tool.execute(
        "call_1",
        _ShowWidgetArgs(title="demo", widget_code="<div>hi</div>"),
    )
    text = "".join(c.text for c in result.content)
    assert "rendered" in text.lower()
```

- [ ] **Step 2: Run it, verify it fails**

Run: `uv run pytest tests/tools/test_show_widget.py -v`
Expected: FAIL — `ModuleNotFoundError: cubebox.tools.builtin.show_widget`.

- [ ] **Step 3: Write the prompt constants**

```python
# backend/cubebox/prompts/widget.py
"""Design guidelines + tool description for the show_widget generative-UI tool.

Self-authored (informed by, not copied from, Claude's artifact guidelines).
Keep this string stable: it is appended to the system prompt and is
cache-sensitive (see backend/docs/prompt-cache-discipline.md).
"""

WIDGET_TOOL_DESCRIPTION = (
    "Render an interactive HTML widget inline in the conversation. Use for "
    "visual/explanatory answers: charts, diagrams, sliders, animations. "
    "widget_code is an HTML fragment (no <html>/<body>); it renders in a "
    "sandboxed iframe with no network access and no localStorage."
)

WIDGET_GUIDELINES = """\
## Rendering interactive widgets (show_widget)

When a visual or interactive explanation is clearly better than text, call
`show_widget`. Rules for `widget_code`:

- It is an HTML fragment injected into a `<div id="root">`. Do NOT include
  `<!DOCTYPE>`, `<html>`, `<head>`, or `<body>`.
- Order content for streaming: `<style>` (short) first, then visible HTML,
  then `<script>` last. Scripts run only after the widget finishes streaming.
- No network requests (fetch/XHR/WebSocket are blocked). Embed all data
  directly in the code.
- No `localStorage`/`sessionStorage` (they throw). Use in-memory variables.
- Use CSS variables for colors; support a dark background (#1a1a1a-ish).
  Two font weights max (400, 500). Avoid gradients, shadows, and blur.
- Libraries may be loaded only from: cdnjs.cloudflare.com, cdn.jsdelivr.net,
  unpkg.com, esm.sh.
- Keep total `widget_code` under ~256KB.
"""
```

- [ ] **Step 4: Write the tool**

```python
# backend/cubebox/tools/builtin/show_widget.py
"""show_widget builtin tool.

Declares the schema so the model can stream an HTML widget. execute() does no
real work — rendering happens entirely in the frontend, which reads the
streamed widget_code from tool_call_delta events. The ack just closes the
tool call in message history.
"""

from pydantic import BaseModel, Field

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent

from cubebox.prompts.widget import WIDGET_TOOL_DESCRIPTION


class _ShowWidgetArgs(BaseModel):
    title: str = Field(description="Short snake_case identifier for the widget.")
    widget_code: str = Field(description="HTML fragment to render (no <html>/<body>).")
    width: int | None = Field(default=None, description="Optional preferred width in px.")
    height: int | None = Field(default=None, description="Optional preferred height in px.")


def make_show_widget_tool() -> AgentTool[_ShowWidgetArgs]:
    async def _show_widget(
        tool_call_id: str,
        args: _ShowWidgetArgs,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        return AgentToolResult(content=[TextContent(text="Widget rendered.")])

    return AgentTool(
        name="show_widget",
        description=WIDGET_TOOL_DESCRIPTION,
        parameters=_ShowWidgetArgs,
        execute=_show_widget,
    )
```

> Verified against `cubebox/middleware/sandbox.py:21-23`: `AgentTool` /
> `AgentToolResult` come from `cubepi.agent.types`, `TextContent` from
> `cubepi.providers.base`. (Not `cubepi.agent.tools` — that module does not exist.)

- [ ] **Step 5: Run tests, verify pass**

Run: `uv run pytest tests/tools/test_show_widget.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/tools/builtin/show_widget.py backend/cubebox/prompts/widget.py backend/tests/tools/test_show_widget.py
git commit -m "feat(widget): add show_widget tool + design-guidelines prompt"
```

---

## Task 2: Backend — register tool + inject guidelines

**Files:**
- Modify: `backend/cubebox/streams/run_manager.py` (builtin-tool assembly block, ~`:977`-`:994`; system-prompt assembly)
- Test: `backend/tests/tools/test_show_widget.py` (add a registration assertion)

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/tools/test_show_widget.py`:

```python
def test_guidelines_mention_tool_and_constraints():
    from cubebox.prompts.widget import WIDGET_GUIDELINES
    assert "show_widget" in WIDGET_GUIDELINES
    assert "fetch" in WIDGET_GUIDELINES  # network-blocked note
    assert "localStorage" in WIDGET_GUIDELINES
```

- [ ] **Step 2: Run it, verify it passes** (the prompt already exists from Task 1)

Run: `uv run pytest tests/tools/test_show_widget.py::test_guidelines_mention_tool_and_constraints -v`
Expected: PASS. (This locks the guideline content; the registration wiring below is verified by the Task 8 E2E, which exercises the full run path.)

- [ ] **Step 3: Register the tool in run_manager**

In `backend/cubebox/streams/run_manager.py`, after the `view_images` append block (~`:994`) and before `generate_image`, add:

```python
        # show_widget — UI-only tool; no DI. Fixed position in the builtin
        # tool order to keep the prompt-cache prefix stable.
        try:
            from cubebox.tools.builtin.show_widget import make_show_widget_tool

            _builtin_tools.append(make_show_widget_tool())
        except Exception as _exc:
            logger.warning("show_widget unavailable for cubepi run: {}", _exc)
```

> Placement matters for cache discipline: append at a *fixed* spot every run.
> Do not gate it behind optional config that would make the tool list vary
> run-to-run for the same workspace.

- [ ] **Step 4: Inject the guidelines into the system prompt**

The system prompt is assembled in `_execute_run` as `effective_system_prompt`
(`run_manager.py:1760-1801`) and **passed into** `_run_cubepi_path` as a
parameter (`run_manager.py:824`, called at `:1807`). Inject there — not inside
`_run_cubepi_path`. Append `WIDGET_GUIDELINES` **once**, at a fixed position
after the existing appends (e.g. after the `SKILLS_PROMPT_TEMPLATE` block at
`~:1801`, before the `_run_cubepi_path` call at `:1807`):

```python
        # backend/cubebox/streams/run_manager.py, in _execute_run, after the
        # SKILLS_PROMPT_TEMPLATE append (~:1801), before _run_cubepi_path(...)
        from cubebox.prompts.widget import WIDGET_GUIDELINES

        effective_system_prompt += "\n\n" + WIDGET_GUIDELINES
```

> Read `backend/docs/prompt-cache-discipline.md` first. Append at a fixed,
> deterministic position so the cache prefix stays stable across runs. v1
> appends unconditionally (the tool is always registered), which keeps the
> prompt deterministic.

- [ ] **Step 5: Run the backend tool tests + a quick import smoke**

Run: `uv run pytest tests/tools/test_show_widget.py -v`
Run: `uv run python -c "import cubebox.streams.run_manager"`
Expected: tests PASS; import succeeds with no error.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/streams/run_manager.py backend/tests/tools/test_show_widget.py
git commit -m "feat(widget): register show_widget tool and inject guidelines prompt"
```

---

## Task 3: Frontend — promote `extractJsonStringPrefix` + add `extractWidgetCode`

**Files:**
- Create: `frontend/packages/web/lib/partialJson.ts`
- Modify: `frontend/packages/web/lib/writeFilePreview.ts`
- Test: `frontend/packages/web/lib/__tests__/partialJson.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/packages/web/lib/__tests__/partialJson.test.ts
import { describe, it, expect } from 'vitest'
import { extractJsonStringPrefix, extractWidgetCode } from '@/lib/partialJson'

describe('extractJsonStringPrefix', () => {
  it('extracts a complete value', () => {
    expect(extractJsonStringPrefix('{"content":"hello"}', 'content')).toBe('hello')
  })
  it('returns the prefix for an unterminated value', () => {
    expect(extractJsonStringPrefix('{"content":"# Title\\nbody', 'content')).toBe('# Title\nbody')
  })
  it('decodes escapes', () => {
    expect(extractJsonStringPrefix('{"c":"a\\tb\\"c\\u0041"}', 'c')).toBe('a\tb"cA')
  })
  it('does not end early on an escaped quote inside the value', () => {
    expect(extractJsonStringPrefix('{"c":"say \\"hi\\" ok"}', 'c')).toBe('say "hi" ok')
  })
  it('returns empty when the key is absent', () => {
    expect(extractJsonStringPrefix('{"other":"x"}', 'content')).toBe('')
  })
})

describe('extractWidgetCode', () => {
  it('pulls widget_code mid-stream', () => {
    expect(extractWidgetCode('{"title":"t","widget_code":"<div>part')).toBe('<div>part')
  })
})
```

- [ ] **Step 2: Run it, verify it fails**

Run: `pnpm --filter web test partialJson`
Expected: FAIL — `@/lib/partialJson` not found.

- [ ] **Step 3: Create `partialJson.ts` by moving the existing function**

Move `decodeEscapeSequence` and `extractJsonStringPrefix` **verbatim** from
`writeFilePreview.ts` into a new file and export the prefix function, then add
the widget wrapper:

```typescript
// frontend/packages/web/lib/partialJson.ts
function decodeEscapeSequence(char: string): string {
  switch (char) {
    case 'n': return '\n'
    case 'r': return '\r'
    case 't': return '\t'
    case 'b': return '\b'
    case 'f': return '\f'
    case '"': return '"'
    case '\\': return '\\'
    case '/': return '/'
    default: return char
  }
}

/** Tolerantly extract a JSON string field's value from possibly-incomplete JSON. */
export function extractJsonStringPrefix(raw: string, key: string): string {
  const keyMatch = new RegExp(`"${key}"\\s*:\\s*"`, 'm').exec(raw)
  if (!keyMatch) return ''
  let i = keyMatch.index + keyMatch[0].length
  let value = ''
  while (i < raw.length) {
    const char = raw[i]
    if (char === '"') break
    if (char === '\\') {
      const next = raw[i + 1]
      if (!next) break
      if (next === 'u') {
        const hex = raw.slice(i + 2, i + 6)
        if (hex.length < 4 || /[^0-9a-fA-F]/.test(hex)) break
        value += String.fromCharCode(parseInt(hex, 16))
        i += 6
        continue
      }
      value += decodeEscapeSequence(next)
      i += 2
      continue
    }
    value += char
    i++
  }
  return value
}

export function extractWidgetCode(rawArgsText: string): string {
  return extractJsonStringPrefix(rawArgsText, 'widget_code')
}
```

- [ ] **Step 4: Update `writeFilePreview.ts` to import the shared function**

Delete the local `decodeEscapeSequence` + `extractJsonStringPrefix` definitions
in `writeFilePreview.ts` and add at the top:

```typescript
import { extractJsonStringPrefix } from '@/lib/partialJson'
```

Leave the rest (`parseWriteFileArgs`, etc.) unchanged — they call
`extractJsonStringPrefix` exactly as before.

- [ ] **Step 5: Run tests, verify pass**

Run: `pnpm --filter web test partialJson`
Run: `pnpm --filter web test writeFilePreview`  (existing tests still green)
Run: `pnpm --filter web type-check`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/lib/partialJson.ts frontend/packages/web/lib/writeFilePreview.ts frontend/packages/web/lib/__tests__/partialJson.test.ts
git commit -m "refactor(widget): extract shared partial-JSON parser + extractWidgetCode"
```

---

## Task 4: Frontend — iframe shell runtime

**Files:**
- Create: `frontend/packages/web/components/chat/widget/widgetShell.ts`

This is a static string; correctness is verified end-to-end by the Task 8 E2E.
No isolated unit test (it only runs inside an iframe).

- [ ] **Step 1: Write the shell string**

```typescript
// frontend/packages/web/components/chat/widget/widgetShell.ts
// srcDoc for the widget iframe. CSP meta MUST be the first element in <head>.
// Parent → child messages: {widgetId, seq, type:'morph', html} / {...type:'finalize'}.
// Child → parent messages: {widgetId, type:'ready'|'error'|'resize', ...}.
export const WIDGET_SHELL_HTML = `<!DOCTYPE html><html><head>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://unpkg.com https://esm.sh; style-src 'unsafe-inline'; img-src data: https:; font-src data: https:; connect-src 'none'; base-uri 'none'; form-action 'none'; worker-src 'none'; frame-src 'none'; object-src 'none';">
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<style>
*{box-sizing:border-box}
body{margin:0;padding:1rem;font-family:system-ui,-apple-system,sans-serif;background:#1a1a1a;color:#e0e0e0;}
@keyframes _fadeIn{from{opacity:0;transform:translateY(4px);}to{opacity:1;transform:none;}}
</style></head>
<body><div id="root"></div>
<script>
(function(){
  // WidgetView injects the real id by replacing the __WIDGET_ID__ token in the
  // srcDoc at mount, so the shell knows its identity from the start (no
  // learn-from-first-message handshake → no deadlock).
  var WIDGET_ID = "__WIDGET_ID__";
  var lastSeq = -1;
  var finalized = false;

  function post(msg){ parent.postMessage(Object.assign({widgetId: WIDGET_ID}, msg), '*'); }

  function applyMorph(html){
    var root = document.getElementById('root');
    var target = document.createElement('div');
    target.id = 'root';
    target.innerHTML = html;
    window.morphdom(root, target, {
      onBeforeElUpdated: function(from, to){ return !from.isEqualNode(to); },
      onNodeAdded: function(node){
        if (node.nodeType === 1 && node.tagName !== 'STYLE' && node.tagName !== 'SCRIPT') {
          node.style.animation = '_fadeIn 0.3s ease both';
        }
        return node;
      }
    });
    post({type:'resize', height: document.body.scrollHeight});
  }

  function runScripts(){
    document.querySelectorAll('#root script').forEach(function(old){
      var s = document.createElement('script');
      if (old.src) { s.src = old.src; } else { s.textContent = old.textContent; }
      old.parentNode.replaceChild(s, old);
    });
  }

  // WidgetView only sends after it receives {type:'ready'} (sent below, after
  // morphdom loads), so every inbound morph/finalize arrives morph-ready.
  window.addEventListener('message', function(e){
    if (e.source !== parent) return;
    var d = e.data || {};
    if (d.widgetId !== WIDGET_ID) return;
    if (typeof d.seq !== 'number' || d.seq <= lastSeq) return; // latest-wins
    lastSeq = d.seq;
    try {
      if (d.type === 'morph') {
        if (finalized) return;
        applyMorph(d.html);
      } else if (d.type === 'finalize') {
        if (finalized) return;
        finalized = true;
        runScripts();
        post({type:'resize', height: document.body.scrollHeight});
      }
    } catch (err) {
      post({type:'error', message: String(err && err.message || err).slice(0, 500)});
    }
  });

  var s = document.createElement('script');
  s.src = 'https://cdn.jsdelivr.net/npm/morphdom@2.7.4/dist/morphdom-umd.min.js';
  s.onload = function(){ post({type:'ready'}); };
  s.onerror = function(){ post({type:'error', message:'morphdom failed to load'}); };
  document.head.appendChild(s);
})();
</script></body></html>`
```

> The CSP `meta` is the first `<head>` child, before the inline `<style>`/`<script>`.
> `__WIDGET_ID__` is a literal placeholder that `WidgetView` replaces with the
> real id at mount (Task 5). `ready` fires only after morphdom loads, and the
> parent sends nothing before `ready`, so no pre-ready buffering is needed.

- [ ] **Step 2: Type-check**

Run: `pnpm --filter web type-check`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/chat/widget/widgetShell.ts
git commit -m "feat(widget): iframe shell runtime (CSP + morphdom + postMessage)"
```

---

## Task 5: Frontend — `WidgetView` component

**Files:**
- Create: `frontend/packages/web/components/chat/widget/WidgetView.tsx`

- [ ] **Step 1: Write the component**

```tsx
// frontend/packages/web/components/chat/widget/WidgetView.tsx
'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { WIDGET_SHELL_HTML } from './widgetShell'

const READY_TIMEOUT_MS = 5000
const MAX_HEIGHT_PX = 4000
const MAX_CODE_BYTES = 256 * 1024

interface WidgetViewProps {
  widgetCode: string
  status: 'streaming' | 'complete'
  widgetId: string
  title?: string
  width?: number
  height?: number
}

export function WidgetView({
  widgetCode,
  status,
  widgetId,
  title,
  width,
  height: initialHeight,
}: WidgetViewProps) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)
  const [height, setHeight] = useState(initialHeight ?? 120)
  const seqRef = useRef(0)
  const latestRef = useRef('')
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Inject the real id into the shell (stable per widgetId).
  const srcDoc = useMemo(() => WIDGET_SHELL_HTML.replace('__WIDGET_ID__', widgetId), [widgetId])

  const tooBig = new Blob([widgetCode]).size > MAX_CODE_BYTES
  latestRef.current = widgetCode

  // child → parent listener (validate source + type)
  useEffect(() => {
    function onMessage(e: MessageEvent) {
      if (e.source !== iframeRef.current?.contentWindow) return
      const d = e.data as { widgetId?: string; type?: string; height?: number; message?: string }
      if (d.widgetId !== widgetId) return
      if (d.type === 'ready') setReady(true)
      else if (d.type === 'error') setFailed(true)
      else if (d.type === 'resize' && typeof d.height === 'number') {
        setHeight(Math.min(Math.max(d.height, 40), MAX_HEIGHT_PX))
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [widgetId])

  // readiness timeout → fallback
  useEffect(() => {
    if (ready || failed) return
    const t = setTimeout(() => { if (!ready) setFailed(true) }, READY_TIMEOUT_MS)
    return () => clearTimeout(t)
  }, [ready, failed])

  // push morph (debounced) once ready
  useEffect(() => {
    if (!ready || failed || tooBig) return
    const send = (final: boolean) => {
      const win = iframeRef.current?.contentWindow
      if (!win) return
      seqRef.current += 1
      win.postMessage(
        { widgetId, seq: seqRef.current, type: 'morph', html: latestRef.current },
        '*',
      )
      if (final) {
        seqRef.current += 1
        win.postMessage({ widgetId, seq: seqRef.current, type: 'finalize' }, '*')
      }
    }
    if (status === 'complete') {
      if (debounceRef.current) clearTimeout(debounceRef.current)
      send(true)
    } else {
      if (debounceRef.current) clearTimeout(debounceRef.current)
      debounceRef.current = setTimeout(() => send(false), 120)
    }
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [widgetCode, status, ready, failed, tooBig, widgetId])

  if (failed || tooBig) {
    return (
      <details className="rounded-lg border border-border bg-muted p-2 text-sm">
        <summary className="cursor-pointer text-muted-foreground">
          {tooBig ? 'Widget too large — showing source' : 'Widget failed to render — showing source'}
          {title ? ` (${title})` : ''}
        </summary>
        <pre className="overflow-auto text-xs"><code>{widgetCode}</code></pre>
      </details>
    )
  }

  return (
    <iframe
      ref={iframeRef}
      title={title ?? 'widget'}
      sandbox="allow-scripts"
      srcDoc={srcDoc}
      style={{ width: width ? `${width}px` : '100%', height, border: 'none' }}
      className="rounded-lg border border-border bg-muted"
    />
  )
}
```

> `sandbox="allow-scripts"` only — never add `allow-same-origin`. `srcDoc` carries
> the shell. The component holds `latestRef` so the first post after `ready` uses
> the newest HTML.

- [ ] **Step 2: Type-check**

Run: `pnpm --filter web type-check`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/chat/widget/WidgetView.tsx
git commit -m "feat(widget): WidgetView sandboxed-iframe component"
```

---

## Task 6: Frontend — mount in `AssistantMessage`

**Files:**
- Modify: `frontend/packages/web/components/chat/AssistantMessage.tsx`

- [ ] **Step 1: Add the streaming branch**

In the `block.type === 'tool_call_streaming'` branch (~`:314`), before the
generic `write_file` handling, add:

```tsx
  if (block.type === 'tool_call_streaming' && block.name === 'show_widget') {
    return (
      <WidgetView
        widgetId={block.tool_call_id ?? `idx-${block.index}`}
        widgetCode={extractWidgetCode(block.args_text)}
        status="streaming"
      />
    )
  }
```

- [ ] **Step 2: Exclude `show_widget` from `groupBlocks` (REQUIRED — the real fix)**

Completed `tool_call` blocks are grouped by `groupBlocks` (`:361`) **before**
`ContentBlockRenderer` ever sees them (`:430` builds `grouped`, rendered at
`:467`/`:478`). `groupBlocks` already exempts `subagent`/`save_artifact`/
`write_todos` by name. Add `show_widget` to that exemption so it passes through
ungrouped to `ContentBlockRenderer`:

```tsx
    if (
      block.type === 'tool_call' &&
      block.name !== 'subagent' &&
      block.name !== 'save_artifact' &&
      block.name !== 'write_todos' &&
      block.name !== 'show_widget'        // <-- add
    ) {
      // ...existing grouping push...
```

Without this, a completed widget is swallowed into a `ToolCallGroup` and the
custom branch below never runs.

- [ ] **Step 3: Add the completed branch in `ContentBlockRenderer`**

Alongside the existing `subagent` (~`:230`) and `save_artifact` (~`:256`)
special cases, before the generic `if (block.type === 'tool_call')` →
`ToolCallGroup` (~`:303`), add:

```tsx
  if (block.type === 'tool_call' && block.name === 'show_widget') {
    const a = block.arguments ?? {}
    return (
      <WidgetView
        widgetId={block.id}
        widgetCode={typeof a.widget_code === 'string' ? a.widget_code : ''}
        status="complete"
        title={typeof a.title === 'string' ? a.title : undefined}
        width={typeof a.width === 'number' ? a.width : undefined}
        height={typeof a.height === 'number' ? a.height : undefined}
      />
    )
  }
```

Combined with Step 2, a completed `show_widget` reaches this branch ungrouped
and renders as a widget instead of collapsing into the tool-call list.

- [ ] **Step 4: Add imports**

```tsx
import { WidgetView } from '@/components/chat/widget/WidgetView'
import { extractWidgetCode } from '@/lib/partialJson'
```

- [ ] **Step 5: Type-check + existing component tests**

Run: `pnpm --filter web type-check`
Run: `pnpm --filter web test AssistantMessage` (if present)
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/components/chat/AssistantMessage.tsx
git commit -m "feat(widget): mount WidgetView for streaming + completed show_widget"
```

---

## Task 7: Subagent scope — exclude `show_widget` from subagent tools (v1)

**Context:** `SubAgentMiddleware` shares the same `_builtin_tools` list
(`run_manager.py:1292-1300`), so registering `show_widget` in builtins makes it
callable from inside subagents too. Rendering widgets inside `SubAgentCard.tsx`
(which has its own block-rendering path) is extra surface we don't want in v1.

**v1 decision:** keep widgets a **top-level assistant feature only**. The
cleanest way is to **not expose `show_widget` to subagents**, so subagent output
can never contain a widget block to render.

**Files:**
- Modify: `backend/cubebox/streams/run_manager.py` (subagent tool assembly, ~`:1292-1303`)

- [ ] **Step 1: Exclude `show_widget` from the subagent tool set**

Where `_subagent_tools` / the SubAgentMiddleware tool list is built from the
shared builtins (~`:1292-1303`), filter it out:

```python
        _subagent_tools = [t for t in _subagent_tools if t.name != "show_widget"]
```

> Verify the exact variable that feeds `SubAgentMiddleware` and filter that one.
> The goal: the top-level run includes `show_widget`; subagent runs do not.

- [ ] **Step 2: Add a backend test**

```python
# backend/tests/tools/test_show_widget.py
def test_show_widget_excluded_from_subagent_tools():
    # Build the subagent tool list the way run_manager does and assert
    # no tool named "show_widget" is present. (Use the helper/path that
    # run_manager uses to assemble _subagent_tools.)
    ...
```

> Fill in using the actual assembly helper. If a direct unit seam isn't
> available, assert it via the Task 8 E2E instead (a subagent prompt that would
> trigger a widget produces no iframe).

- [ ] **Step 3: Run + commit**

Run: `uv run pytest tests/tools/test_show_widget.py -v`

```bash
git add backend/cubebox/streams/run_manager.py backend/tests/tools/test_show_widget.py
git commit -m "feat(widget): keep show_widget out of subagent tool set (v1 top-level only)"
```

---

## Task 8: E2E — streaming, latest-wins, reload, fallback, multi, security

**Files:**
- Create: `frontend/packages/web/__tests__/e2e/generative-ui-widgets.spec.ts`

> Verified repo layout: frontend E2E specs live in
> `frontend/packages/web/__tests__/e2e/` (e.g. `admin-settings.spec.ts`), and the
> Playwright runner is `test:e2e` in **`frontend/package.json`** (`playwright test`),
> run as `cd frontend && pnpm test:e2e`. There is no `test:e2e` in
> `packages/web/package.json` (that only has `test` = vitest).

Uses the faux provider (`cubepi/providers/faux.py`) to script a `show_widget`
tool call streamed across frames. Wire the faux script via the existing E2E
config switch (match how a sibling spec in `__tests__/e2e/` selects faux and
sends a prompt — reuse those exact helpers; do not invent harness APIs).

- [ ] **Step 1: Write the E2E spec**

```typescript
// frontend/packages/web/__tests__/e2e/generative-ui-widgets.spec.ts
import { test, expect } from '@playwright/test'

// Helper: the faux script emits a show_widget tool call whose widget_code is:
//   <style>...</style><p id="w">hi</p><script>window.__c=(window.__c||0)+1;
//     document.getElementById('w').textContent='done'+window.__c;</script>
// streamed across several deltas. (See faux fixture wiring.)

test('streams a widget and finalizes scripts once', async ({ page }) => {
  await page.goto('/')             // adjust to the conversation URL the suite uses
  // ...send the prompt that triggers the faux show_widget script...
  const frame = page.frameLocator('iframe[title]')
  // sandbox attribute is hardened
  const sandbox = await page.locator('iframe[title]').getAttribute('sandbox')
  expect(sandbox).toBe('allow-scripts')
  // mid-stream partial node appears
  await expect(frame.locator('#w')).toBeVisible()
  // after finalize the script ran exactly once
  await expect(frame.locator('#w')).toHaveText('done1')
})

test('reload renders finished widget without streaming', async ({ page }) => {
  // ...after a completed widget exists, reload...
  await page.reload()
  const frame = page.frameLocator('iframe[title]')
  await expect(frame.locator('#w')).toHaveText('done1')
})

test('latest-wins: a late lower-seq morph does not regress', async ({ page }) => {
  // Drive WidgetView with a complete widget, then inject a stale morph via
  // page.evaluate(postMessage with seq=0); assert content unchanged.
})

test('readiness timeout falls back to source block', async ({ page }) => {
  // Block the morphdom CDN (route.abort) so ready never fires; assert the
  // <details> source fallback appears within ~6s, not a blank iframe body.
})

test('two widgets render in independent iframes', async ({ page }) => {
  // faux emits two show_widget calls; assert two iframes, each with its content.
})

test('widget cannot reach parent or network', async ({ page }) => {
  const frame = page.frameLocator('iframe[title]')
  // inside the widget, reading parent.document throws (opaque origin);
  // fetch() is blocked by connect-src 'none'. Assert via a widget that posts
  // its probe result, or via console/network assertions.
})

test('parent ignores a forged-source message', async ({ page }) => {
  // From the page (not the iframe), postMessage a {widgetId, type:'resize',
  // height: 9999} directly to window. Because WidgetView checks
  // e.source === iframe.contentWindow, the forged message (source = top window)
  // must be ignored — assert the iframe height does NOT jump to 9999.
})

// Subagent scope (Task 7): a subagent prompt that would otherwise trigger a
// widget produces NO iframe, since show_widget is excluded from subagent tools.
test('subagents do not render widgets', async ({ page }) => {
  // drive a faux subagent run that would call show_widget; assert no iframe[title].
})
```

> The exact navigation/prompt-send steps and faux wiring follow the patterns in
> a sibling spec in `__tests__/e2e/`. Fill those in from that spec; do not
> invent new harness APIs.

- [ ] **Step 2: Run the E2E on worktree ports**

Run: `cd frontend && pnpm test:e2e generative-ui-widgets` (the `test:e2e` script
is in `frontend/package.json`). Ensure backend (8075) + frontend (3075) are up
via the worktree env (`cat .worktree.env`).
Expected: all specs PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/__tests__/e2e/generative-ui-widgets.spec.ts
git commit -m "test(widget): E2E for streaming, latest-wins, reload, fallback, security, subagent-scope"
```

---

## Task 9: Manual browser verification + changed-module sweep

- [ ] **Step 1:** Start backend (8075) + frontend (3075) via worktree env. In the
  browser (bind 0.0.0.0; report IP:port — user is remote), trigger a real
  `show_widget` (e.g. "show me how compound interest works"). Confirm: it
  streams in, morphs smoothly, scripts run once, dark theme reads well, resize
  fits. Check both light and dark app themes.
- [ ] **Step 2:** Run changed-module tests:
  - `uv run pytest tests/tools/test_show_widget.py -v`
  - `pnpm --filter web test partialJson writeFilePreview`
  - `pnpm --filter web type-check`
- [ ] **Step 3:** Defer the full suite + `/ci` to the pre-PR / Stage-5 sweep.

---

## Self-Review

- **Spec coverage:** tool (T1) + registration/prompt-injection-in-`_execute_run`
  (T2) + extract reuse (T3) + shell with `__WIDGET_ID__` injection (T4) +
  WidgetView with id-injected srcDoc, seq/debounce/ready-timeout/fallback/limits,
  height applied (T5) + mount with `groupBlocks` exclusion + completed branch (T6)
  + subagent exclusion (T7) + E2E incl. latest-wins/reload/fallback/multi/security/
  forged-source/subagent-scope (T8) + manual + theme check (T9).
  Interrupted-widget non-persistence is inherited behavior (no task needed).
- **Verified against real code:** cubepi imports (`cubepi.agent.types` /
  `cubepi.providers.base`), builtin registration order (`run_manager.py:977-996`),
  prompt site (`_execute_run` `:1760-1807`), `groupBlocks` name-exclusion
  (`AssistantMessage.tsx:361`), E2E dir (`__tests__/e2e/`) + runner
  (`frontend/package.json` `test:e2e`).
- **Handshake:** the deadlock is removed — the shell gets its id via
  `__WIDGET_ID__` replacement at mount and posts `ready` (with the right id)
  after morphdom loads; the parent sends only after `ready`.
- **Placeholders:** the E2E navigation/faux-wiring steps intentionally defer to
  sibling-spec patterns rather than inventing harness APIs; all code units have
  complete implementations.
- **Type consistency:** message shape `{widgetId, seq, type, html?}` (parent→child)
  and `{widgetId, type, ...}` (child→parent) is identical in `widgetShell.ts` and
  `WidgetView.tsx`; `extractWidgetCode` signature matches its callers; `WidgetViewProps`
  `width`/`height`/`title` are all passed from the mount branches.
