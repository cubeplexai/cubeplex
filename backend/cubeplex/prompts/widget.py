"""show_widget tool description + short always-on system stub.

Full design rules and scenario skeletons live in the preinstalled
``show-widget`` skill (``backend/skills/preinstalled/show-widget/``).
The always-on system fragment is intentionally small so non-widget turns
do not pay ~11k chars every request (trace finding F1 / #391).

Keep these strings stable: they sit in the system-prompt cache prefix
(see backend/docs/prompt-cache-discipline.md).
"""

WIDGET_TOOL_DESCRIPTION = (
    "Render an interactive HTML widget INLINE in the chat. Default tool for any "
    "answer that wants a chart, diagram, dashboard, sliders, animation, side-by-side "
    "comparison, or other visual / interactive explanation. Stream `widget_code` "
    "(an HTML fragment — no <html>/<body>) directly here; do NOT first write it to "
    "a file or save it as an artifact. It renders in a sandboxed iframe: no "
    "fetch/XHR/WebSocket, no localStorage, but JS libraries may be loaded from the "
    "allowed CDNs. For non-trivial widgets, load the `show-widget` skill first."
)

# Short always-on stub. Hard safety limits stay here so a model that skips
# load_skill still cannot invent full-page docs / network calls / storage.
# Skeletons and visual playbooks are on-demand via the show-widget skill.
WIDGET_GUIDELINES = """\
## Interactive widgets (show_widget)

Use `show_widget` when the answer is better seen than read (charts, diagrams,
dashboards, sliders, comparisons). Stream an HTML fragment — do not
`write_file` or `save_artifact` first.

Hard limits (always):

- Fragment only: no `<!DOCTYPE>` / `<html>` / `<head>` / `<body>`.
- Order: short `<style>` → HTML → `<script>` last.
- No fetch/XHR/WebSocket; no `localStorage`/`sessionStorage`; embed data.
- CDNs only: cdnjs.cloudflare.com, cdn.jsdelivr.net, unpkg.com, esm.sh.
- Target ~600×360–480 px; one focus per call; total under ~256 KB.
- Theme via CSS vars only: `var(--bg)`, `var(--fg)`, `var(--muted)`,
  `var(--border)`, `var(--accent)` — never hard-code colors.
- Chart.js: pin canvas CSS height with `!important` (e.g.
  `height:300px!important`) or the chart grows unbounded.

For scenario skeletons (Chart.js, SVG flow, dashboard, sliders, comparison
table), theme-repaint patterns, and the visual quality bar — load the
`show-widget` skill before building non-trivial widgets.
"""
