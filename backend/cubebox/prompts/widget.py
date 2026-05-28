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
  CDNs below IS permitted - that is `script-src`, not `connect-src`.)
- No `localStorage`/`sessionStorage` (they throw). Use in-memory variables.
- Colors: the shell pre-defines five CSS variables on `:root` —
  `--bg` (page background), `--fg` (foreground text), `--muted` (card/panel
  surface), `--border` (separator), `--accent` (link/primary). Their values
  are picked by the host based on the current app theme (light or dark) and
  give correct contrast in both. Reference them via `var(--bg)` etc.
  **Do NOT hard-code colors** (no `#1a1a1a`, no `#fff`, no rgb()); the widget
  must look right in both themes. Two font weights max (400, 500). Avoid
  gradients, shadows, and blur.
- Libraries may be loaded only from: cdnjs.cloudflare.com, cdn.jsdelivr.net,
  unpkg.com, esm.sh.
- Keep total `widget_code` under ~256KB.
"""
