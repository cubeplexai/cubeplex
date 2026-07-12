# Generative UI Widgets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the agent render live, interactive HTML/JS widgets inline in the chat stream via a `show_widget` tool, streamed token-by-token into a sandboxed iframe and morphed in place.

**Architecture:** `show_widget` is a builtin tool whose `widget_code` HTML fragment rides the existing `tool_call_delta → args_text → extractJsonStringPrefix` streaming path (no streaming-pipeline change). The frontend extracts the in-progress HTML and `postMessage`s it into a `sandbox="allow-scripts"` iframe whose shell runs morphdom; scripts run once on finalize. Security = opaque-origin sandbox + CSP + `event.source` validation.

**Tech Stack:** Python/FastAPI + cubepi (backend tool + prompt), React 19 / Next.js + TypeScript (frontend component, iframe shell), morphdom (CDN), Playwright + vitest (tests).

**Spec:** `docs/dev/specs/2026-05-27-generative-ui-widgets-design.md` (read it first).

**Read before touching:** `backend/docs/prompt-cache-discipline.md` (tool order + system-prompt injection are cache-sensitive), `backend/docs/agent-system-design.md` (tool/middleware assembly).

**Worktree:** `/home/chris/cubeplex/.worktrees/feat/generative-ui-widgets` (ports 8075/3075). `cat .worktree.env` first. Backend tests: `uv run pytest`. Frontend unit: `pnpm --filter web test`. E2E: `cd frontend && pnpm test:e2e` (the `test:e2e` script lives in `frontend/package.json`; `packages/web/package.json` has no such script).

---

## File Structure

**Backend (create):**
- `backend/cubeplex/tools/builtin/show_widget.py` — `_ShowWidgetArgs` model + `make_show_widget_tool()` factory. The tool's `execute()` returns a light ack; rendering is entirely frontend.
- `backend/cubeplex/prompts/widget.py` — `WIDGET_GUIDELINES` string (self-authored design constraints) + `WIDGET_TOOL_DESCRIPTION`.

**Backend (modify):**
- `backend/cubeplex/streams/run_manager.py` — register `make_show_widget_tool()` in `_builtin_tools` (fixed order) and append `WIDGET_GUIDELINES` to the system prompt when the tool is enabled.

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
- `frontend/packages/web/__tests__/e2e/widget-shell.spec.ts` (shell protocol, Playwright)
- `frontend/packages/web/components/chat/widget/__tests__/WidgetView.test.tsx` (vitest)

---

## Task 1: Backend — `show_widget` tool

**Files:**
- Create: `backend/cubeplex/tools/builtin/show_widget.py`
- Create: `backend/cubeplex/prompts/widget.py`
- Test: `backend/tests/tools/test_show_widget.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/tools/test_show_widget.py
import pytest
from cubeplex.tools.builtin.show_widget import make_show_widget_tool, _ShowWidgetArgs


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
Expected: FAIL — `ModuleNotFoundError: cubeplex.tools.builtin.show_widget`.

- [ ] **Step 3: Write the prompt constants**

```python
# backend/cubeplex/prompts/widget.py
"""Design guidelines + tool description for the show_widget generative-UI tool.

Self-authored (informed by, not copied from, Claude's artifact guidelines).
Keep this string stable: it is appended to the system prompt and is
cache-sensitive (see backend/docs/prompt-cache-discipline.md).
"""

WIDGET_TOOL_DESCRIPTION = (
    "Render an interactive HTML widget inline in the conversation. Use for "
    "visual/explanatory answers: charts, diagrams, sliders, animations. "
    "widget_code is an HTML fragment (no <html>/<body>); it renders in a "
    "sandboxed iframe. It cannot fetch/XHR/WebSocket (no data fetching) and "
    "cannot use localStorage, but it MAY load JS libraries from the allowed CDNs."
)

WIDGET_GUIDELINES = """\
## Rendering interactive widgets (show_widget)

When a visual or interactive explanation is clearly better than text, call
`show_widget`. Rules for `widget_code`:

- It is an HTML fragment injected into a `<div id="root">`. Do NOT include
  `<!DOCTYPE>`, `<html>`, `<head>`, or `<body>`.
- Order content for streaming: `<style>` (short) first, then visible HTML,
  then `<script>` last. Scripts run only after the widget finishes streaming.
- No data fetching: fetch/XHR/WebSocket are blocked (CSP `connect-src 'none'`).
  Embed all data directly in the code. (Loading JS libraries from the allowed
  CDNs below IS permitted — that is `script-src`, not `connect-src`.)
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
# backend/cubeplex/tools/builtin/show_widget.py
"""show_widget builtin tool.

Declares the schema so the model can stream an HTML widget. execute() does no
real work — rendering happens entirely in the frontend, which reads the
streamed widget_code from tool_call_delta events. The ack just closes the
tool call in message history.
"""

from pydantic import BaseModel, Field

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent

from cubeplex.prompts.widget import WIDGET_TOOL_DESCRIPTION


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

> Verified against `cubeplex/middleware/sandbox.py:21-23`: `AgentTool` /
> `AgentToolResult` come from `cubepi.agent.types`, `TextContent` from
> `cubepi.providers.base`. (Not `cubepi.agent.tools` — that module does not exist.)

- [ ] **Step 5: Run tests, verify pass**

Run: `uv run pytest tests/tools/test_show_widget.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/tools/builtin/show_widget.py backend/cubeplex/prompts/widget.py backend/tests/tools/test_show_widget.py
git commit -m "feat(widget): add show_widget tool + design-guidelines prompt"
```

---

## Task 2: Backend — register tool + inject guidelines

**Files:**
- Modify: `backend/cubeplex/streams/run_manager.py` (builtin-tool assembly block, ~`:977`-`:994`; system-prompt assembly)
- Test: `backend/tests/tools/test_show_widget.py` (add a registration assertion)

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/tools/test_show_widget.py`:

```python
def test_guidelines_mention_tool_and_constraints():
    from cubeplex.prompts.widget import WIDGET_GUIDELINES
    assert "show_widget" in WIDGET_GUIDELINES
    assert "fetch" in WIDGET_GUIDELINES  # network-blocked note
    assert "localStorage" in WIDGET_GUIDELINES
```

- [ ] **Step 2: Run it, verify it passes** (the prompt already exists from Task 1)

Run: `uv run pytest tests/tools/test_show_widget.py::test_guidelines_mention_tool_and_constraints -v`
Expected: PASS. (This locks the guideline content. The registration/prompt
wiring itself is verified by the Step 5 import smoke + the manual model→widget
run in Task 9 — Task 8 is deterministic frontend-only testing and does not
exercise the backend run path.)

- [ ] **Step 3: Register the tool in run_manager**

In `backend/cubeplex/streams/run_manager.py`, after the `view_images` append block (~`:994`) and before `generate_image`, add:

```python
        # show_widget — UI-only tool; no DI. Fixed position in the builtin
        # tool order to keep the prompt-cache prefix stable.
        try:
            from cubeplex.tools.builtin.show_widget import make_show_widget_tool

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
        # backend/cubeplex/streams/run_manager.py, in _execute_run, after the
        # SKILLS_PROMPT_TEMPLATE append (~:1801), before _run_cubepi_path(...)
        from cubeplex.prompts.widget import WIDGET_GUIDELINES

        effective_system_prompt += "\n\n" + WIDGET_GUIDELINES
```

> Read `backend/docs/prompt-cache-discipline.md` first. Append at a fixed,
> deterministic position so the cache prefix stays stable across runs. v1
> appends unconditionally (the tool is always registered), which keeps the
> prompt deterministic.

- [ ] **Step 5: Run the backend tool tests + a quick import smoke**

Run: `uv run pytest tests/tools/test_show_widget.py -v`
Run: `uv run python -c "import cubeplex.streams.run_manager"`
Expected: tests PASS; import succeeds with no error.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/streams/run_manager.py backend/tests/tools/test_show_widget.py
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
  // WidgetView replaces the placeholder below with JSON.stringify(widgetId) at
  // mount (note: NO surrounding quotes here — JSON.stringify supplies them and
  // escapes any special chars, so an id with quotes/backslashes can't break out
  // of the literal). The placeholder appears exactly ONCE in this whole srcDoc
  // string (here), so a single string replace is unambiguous. The shell knows
  // its identity from the start (no learn-from-first-message handshake → no
  // deadlock).
  var WIDGET_ID = %%WIDGET_ID%%;
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
> `%%WIDGET_ID%%` is a literal placeholder appearing exactly once in the string
> (in the `var WIDGET_ID` assignment, not in any comment), so `WidgetView`'s
> single replace is unambiguous and injection-safe: `WidgetView` substitutes
> `JSON.stringify(widgetId)` with every `<` further escaped to the JS sequence
> `<` (so a hypothetical `</script>` can't close this block). `ready` fires
> only after morphdom loads, and the parent sends nothing before `ready`, so no
> pre-ready buffering is needed.

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

  // Inject the real id into the shell (stable per widgetId). widgetId is a
  // public id (alphanumeric + `_`) or `idx-<n>`, so it cannot contain quotes or
  // an angle bracket. We still harden defensively: JSON.stringify supplies
  // quotes + standard escaping, then we replace every "<" with the JS escape
  // "\\u003c" so a hypothetical "</script>" can't close the shell's script
  // block. A function replacement avoids String.replace's "$" special-handling.
  const srcDoc = useMemo(() => {
    const idLiteral = JSON.stringify(widgetId).replace(/</g, '\\u003c')
    return WIDGET_SHELL_HTML.replace('%%WIDGET_ID%%', () => idLiteral)
  }, [widgetId])

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
    const title = extractJsonStringPrefix(block.args_text, 'title') || undefined
    return (
      <WidgetView
        widgetId={block.tool_call_id ?? `idx-${block.index}`}
        widgetCode={extractWidgetCode(block.args_text)}
        status="streaming"
        title={title}
      />
    )
  }
```

> `width`/`height` are integers that may not have streamed yet mid-call; they
> are left `undefined` during streaming. They are only *initial hints*: the
> shell posts a `resize` message (scrollHeight) after every morph and after
> finalize, and that drives the authoritative height via `setHeight` — so the
> rendered height self-corrects regardless of the `height` prop, with no remount
> needed. Only `title` (a leading string field) is cheap to extract live.

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
import { extractWidgetCode, extractJsonStringPrefix } from '@/lib/partialJson'
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

**Context:** `SubAgentMiddleware` is constructed with
`shared_tools=_sandbox_tools + _artifact_tools + _builtin_tools`
(`run_manager.py:1299`), so registering `show_widget` in `_builtin_tools` makes
it callable from inside subagents too. Rendering widgets inside `SubAgentCard.tsx`
(which has its own block-rendering path) is extra surface we don't want in v1.

**v1 decision:** keep widgets a **top-level assistant feature only**. The
cleanest way is to **not pass `show_widget` to subagents** via `shared_tools`, so
subagent output can never contain a widget block to render. (The top-level run
still includes it from `_builtin_tools`.)

**Files:**
- Modify: `backend/cubeplex/streams/run_manager.py` (SubAgentMiddleware construction, `:1292-1300`)

- [ ] **Step 1: Extract a tiny pure helper (so it is unit-testable)**

The filter is currently inline at `SubAgentMiddleware(...)` (`:1299`). Make it a
named module-level pure function in `run_manager.py` (above `_execute_run`) so it
has a unit seam:

```python
# backend/cubeplex/streams/run_manager.py (module level)
# Annotated `list[Any]` to match this file's convention (it does not import
# AgentTool at module level — tools are built via lazy imports inside functions).
def _subagent_shared_tools(tools: list[Any]) -> list[Any]:
    """Tools shared into subagents. show_widget is top-level only (v1)."""
    return [t for t in tools if t.name != "show_widget"]
```

> `Any` avoids adding a new top-level `AgentTool` import to `run_manager.py`,
> which uses lazy imports throughout. The helper only reads `t.name`.

- [ ] **Step 2: Use it at the SubAgentMiddleware construction (`:1299`)**

```python
            subagent_mw = SubAgentMiddleware(
                subagent_map={},
                default_provider=provider,
                default_model_id=model_id,
                default_provider_name=provider_name,
                shared_tools=_subagent_shared_tools(
                    _sandbox_tools + _artifact_tools + _builtin_tools
                ),
                inherited_middleware=_cost_mw_for_inherit,
            )
```

> This is the list actually handed to subagents. (`SubAgentMiddleware` also drops
> self-referential tools internally, but `show_widget` isn't one of those.)

- [ ] **Step 3: Add a concrete backend test**

```python
# backend/tests/tools/test_show_widget.py
from types import SimpleNamespace

from cubeplex.streams.run_manager import _subagent_shared_tools
from cubeplex.tools.builtin.show_widget import make_show_widget_tool


def test_subagent_shared_tools_drops_show_widget():
    sw = make_show_widget_tool()
    keep = SimpleNamespace(name="execute")  # helper only reads .name
    result = _subagent_shared_tools([sw, keep])  # type: ignore[list-item]
    assert sw not in result          # show_widget removed
    assert keep in result            # other tools retained
```

> `_subagent_shared_tools` only reads `t.name`, so a `SimpleNamespace` stand-in
> is enough to prove a non-widget tool is retained without constructing a second
> real `AgentTool`.

- [ ] **Step 4: Run + commit**

Run: `uv run pytest tests/tools/test_show_widget.py -v`

```bash
git add backend/cubeplex/streams/run_manager.py backend/tests/tools/test_show_widget.py
git commit -m "feat(widget): keep show_widget out of subagent tool set (v1 top-level only)"
```

---

## Task 8: Deterministic widget tests (shell + WidgetView)

**Why not a model-driven E2E:** the frontend E2E harness drives a **real model**
(`streaming.spec.ts` registers a user and waits up to 50s for a real haiku) — it
does not use the faux provider. A real model will not reliably emit a
`show_widget` call, so a model-driven assertion would be flaky. Instead we test
the widget machinery directly and deterministically, and leave the true
model→widget path to manual verification (Task 9).

Two layers:
- **8A — shell** (Playwright via `setContent` + a real `<iframe srcdoc>`): drives
  the postMessage protocol against a real browser/morphdom. Covers ready,
  morph, latest-wins, finalize-once, opaque-origin, `connect-src 'none'`.
- **8B — WidgetView** (vitest + jsdom, the repo's component-test setup): the
  parent-side logic — ready-timeout fallback, size-cap fallback, posts-after-ready,
  forged-source rejection.

**Files:**
- Create: `frontend/packages/web/__tests__/e2e/widget-shell.spec.ts`
- Create: `frontend/packages/web/components/chat/widget/__tests__/WidgetView.test.tsx`

- [ ] **Step 1: Write the shell Playwright spec (8A)**

```typescript
// frontend/packages/web/__tests__/e2e/widget-shell.spec.ts
import { test, expect, type Page } from '@playwright/test'
import { WIDGET_SHELL_HTML } from '../../components/chat/widget/widgetShell'

const WIDGET_ID = 'w-test'
// Must match WidgetView's production injection exactly: JSON.stringify supplies
// the quotes (the shell has `var WIDGET_ID = %%WIDGET_ID%%;` with no quotes),
// and `<` is escaped to <.
const ID_LITERAL = JSON.stringify(WIDGET_ID).replace(/</g, '\\u003c')
const SHELL = WIDGET_SHELL_HTML.replace('%%WIDGET_ID%%', () => ID_LITERAL)

async function mountShell(page: Page) {
  await page.setContent('<div id="host"></div>')
  await page.evaluate((srcdoc) => {
    ;(window as unknown as { __ready: boolean }).__ready = false
    window.addEventListener('message', (e) => {
      if ((e.data || {}).type === 'ready') (window as unknown as { __ready: boolean }).__ready = true
    })
    const f = document.createElement('iframe')
    f.id = 'wf'
    f.setAttribute('sandbox', 'allow-scripts')
    f.srcdoc = srcdoc
    document.getElementById('host')!.appendChild(f)
  }, SHELL)
  await page.waitForFunction(() => (window as unknown as { __ready: boolean }).__ready === true, {
    timeout: 15_000,
  })
}

async function send(page: Page, msg: Record<string, unknown>) {
  await page.evaluate((m) => {
    ;(document.getElementById('wf') as HTMLIFrameElement).contentWindow!.postMessage(m, '*')
  }, msg)
}

test('morph applies, then finalize runs scripts exactly once', async ({ page }) => {
  await mountShell(page)
  const frame = page.frameLocator('#wf')
  await send(page, {
    widgetId: WIDGET_ID, seq: 1, type: 'morph',
    html: '<p id="w">hi</p><script>window.__c=(window.__c||0)+1;document.getElementById("w").textContent="done"+window.__c;</script>',
  })
  await expect(frame.locator('#w')).toHaveText('hi')         // script not run yet
  await send(page, { widgetId: WIDGET_ID, seq: 2, type: 'finalize' })
  await expect(frame.locator('#w')).toHaveText('done1')      // ran once
  // a second (higher-seq) finalize must NOT re-run scripts (idempotent)
  await send(page, { widgetId: WIDGET_ID, seq: 3, type: 'finalize' })
  await page.waitForTimeout(200)
  await expect(frame.locator('#w')).toHaveText('done1')      // still 1, not done2
})

test('latest-wins: a stale lower-seq morph is ignored', async ({ page }) => {
  await mountShell(page)
  const frame = page.frameLocator('#wf')
  await send(page, { widgetId: WIDGET_ID, seq: 5, type: 'morph', html: '<p id="w">new</p>' })
  await expect(frame.locator('#w')).toHaveText('new')
  await send(page, { widgetId: WIDGET_ID, seq: 2, type: 'morph', html: '<p id="w">stale</p>' })
  await expect(frame.locator('#w')).toHaveText('new')        // unchanged
})

test('widget cannot reach parent (opaque origin) nor fetch (connect-src none)', async ({ page }) => {
  await mountShell(page)
  const frame = page.frameLocator('#wf')
  await send(page, {
    widgetId: WIDGET_ID, seq: 1, type: 'morph',
    html: `<p id="probe"></p><script>
      var r='';
      try { void parent.document; r+='PARENT_OK'; } catch(e){ r+='PARENT_BLOCKED'; }
      fetch('https://example.com').then(function(){document.getElementById('probe').textContent=r+' FETCH_OK';})
        .catch(function(){document.getElementById('probe').textContent=r+' FETCH_BLOCKED';});
    </script>`,
  })
  await send(page, { widgetId: WIDGET_ID, seq: 2, type: 'finalize' })
  await expect(frame.locator('#probe')).toHaveText('PARENT_BLOCKED FETCH_BLOCKED')
})
```

> 8A loads morphdom from the real CDN (same as production). If CI has no network,
> mark this spec to run only where outbound HTTPS to the CDN allowlist is
> available; the CDN-failure path itself is covered in 8B by a timeout.

- [ ] **Step 2: Write the WidgetView vitest spec (8B)**

```tsx
// frontend/packages/web/components/chat/widget/__tests__/WidgetView.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { WidgetView } from '../WidgetView'

describe('WidgetView', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

  it('falls back to a source block when widget_code exceeds the size cap', () => {
    const big = 'x'.repeat(256 * 1024 + 1)
    render(<WidgetView widgetId="a" widgetCode={big} status="complete" />)
    expect(screen.getByText(/too large/i)).toBeInTheDocument()
  })

  it('falls back when no ready arrives before the timeout', () => {
    render(<WidgetView widgetId="a" widgetCode="<p>x</p>" status="complete" />)
    act(() => { vi.advanceTimersByTime(5001) })
    expect(screen.getByText(/failed to render/i)).toBeInTheDocument()
  })

  it('posts a morph only after a ready from the iframe', () => {
    const { container } = render(<WidgetView widgetId="a" widgetCode="<p>x</p>" status="complete" />)
    const iframe = container.querySelector('iframe') as HTMLIFrameElement
    const post = vi.spyOn(iframe.contentWindow as Window, 'postMessage')
    // forged source (window, not the iframe) → ignored
    act(() => { window.dispatchEvent(new MessageEvent('message', { data: { widgetId: 'a', type: 'ready' }, source: window })) })
    expect(post).not.toHaveBeenCalled()
    // real ready (source === iframe.contentWindow) → morph is sent
    act(() => { window.dispatchEvent(new MessageEvent('message', { data: { widgetId: 'a', type: 'ready' }, source: iframe.contentWindow })) })
    expect(post).toHaveBeenCalledWith(expect.objectContaining({ type: 'morph' }), '*')
  })
})
```

> `@testing-library/react` is already used by the repo's component tests; match
> the existing test setup (jsdom env). jsdom does not execute the iframe's inline
> scripts, which is fine — 8B verifies only WidgetView's parent-side behavior;
> the in-iframe behavior is 8A's job.

- [ ] **Step 3: Run both layers**

Run: `pnpm --filter web test WidgetView`
Run: `cd frontend && pnpm test:e2e widget-shell`  (backend not required — shell is self-contained)
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/__tests__/e2e/widget-shell.spec.ts frontend/packages/web/components/chat/widget/__tests__/WidgetView.test.tsx
git commit -m "test(widget): deterministic shell (playwright) + WidgetView (vitest) tests"
```

---

## Task 9: Manual browser verification + changed-module sweep

- [ ] **Step 1:** Start backend (8075) + frontend (3075) via worktree env. In the
  browser (bind 0.0.0.0; report IP:port — user is remote), trigger a real
  `show_widget` (e.g. "show me how compound interest works"). Confirm: it
  streams in, morphs smoothly, scripts run once, dark theme reads well, resize
  fits. Check both light and dark app themes. **Then reload the page** and
  confirm the completed widget re-renders from message history (one-shot, no
  streaming animation) — this is the reload path (deterministic E2E can't cover
  it because it needs a real model-produced widget in history).
- [ ] **Step 2:** Run changed-module tests:
  - `uv run pytest tests/tools/test_show_widget.py -v`
  - `pnpm --filter web test partialJson writeFilePreview`
  - `pnpm --filter web type-check`
- [ ] **Step 3:** Defer the full suite + `/ci` to the pre-PR / Stage-5 sweep.

---

## Self-Review

- **Spec coverage:** tool (T1) + registration/prompt-injection-in-`_execute_run`
  (T2) + extract reuse (T3) + shell with `%%WIDGET_ID%%` injection (T4) +
  WidgetView with id-injected srcDoc, seq/debounce/ready-timeout/fallback/limits,
  height applied (T5) + mount with `groupBlocks` exclusion + completed branch (T6)
  + subagent exclusion via `shared_tools` (T7) + deterministic tests: shell
  morph/latest-wins/finalize-once/opaque-origin/connect-src (8A) and WidgetView
  size-cap/ready-timeout/forged-source/posts-after-ready (8B) + manual model→widget
  + reload + theme check (T9). Multi-widget isolation is structural (one
  `WidgetView`/iframe per `tool_call_streaming` block, keyed by `index`) — no
  dedicated test needed. Interrupted-widget non-persistence is inherited behavior.
- **Verified against real code:** cubepi imports (`cubepi.agent.types` /
  `cubepi.providers.base`), builtin registration order (`run_manager.py:977-996`),
  prompt site (`_execute_run` `:1760-1807`), `groupBlocks` name-exclusion
  (`AssistantMessage.tsx:361`), E2E dir (`__tests__/e2e/`) + runner
  (`frontend/package.json` `test:e2e`).
- **Handshake:** the deadlock is removed — the shell gets its id via
  `%%WIDGET_ID%%` → `JSON.stringify(widgetId)` replacement at mount (injection-safe)
  and posts `ready` (with the right id) after morphdom loads; the parent sends
  only after `ready`.
- **Placeholders:** none blocking. Task 8 is fully concrete (shell driven via
  `setContent`, WidgetView via vitest); the only deferral is the *manual* Task 9
  model→widget/reload check, which is manual by necessity (the frontend harness
  uses a real model). All code units have complete implementations.
- **Type consistency:** message shape `{widgetId, seq, type, html?}` (parent→child)
  and `{widgetId, type, ...}` (child→parent) is identical in `widgetShell.ts` and
  `WidgetView.tsx`; `extractWidgetCode` signature matches its callers; `WidgetViewProps`
  `width`/`height`/`title` are all passed from the mount branches.
