"""Design guidelines + tool description for the show_widget generative-UI tool.

Self-authored (informed by, not copied from, Claude's artifact guidelines).
Keep this string stable: it is appended to the system prompt and is
cache-sensitive (see backend/docs/prompt-cache-discipline.md).
"""

WIDGET_TOOL_DESCRIPTION = (
    "Render an interactive HTML widget INLINE in the chat. Default tool for any "
    "answer that wants a chart, diagram, dashboard, sliders, animation, side-by-side "
    "comparison, or other visual / interactive explanation. Stream `widget_code` "
    "(an HTML fragment — no <html>/<body>) directly here; do NOT first write it to "
    "a file or save it as an artifact. It renders in a sandboxed iframe: no "
    "fetch/XHR/WebSocket, no localStorage, but JS libraries may be loaded from the "
    "allowed CDNs."
)

WIDGET_GUIDELINES = """\
## Rendering interactive widgets (show_widget)

### When to use

`show_widget` is your DEFAULT for any answer the user is better off seeing
than reading. Trigger words (Chinese + English) that should make you reach
for it first: 画 / 绘制 / 画一个 / 可视化 / 示意图 / 流程图 / 架构图 / 图表 /
仪表盘 / 数据图 / 对比 / draw / visualize / chart / diagram / dashboard /
flowchart / architecture / mockup / sliders / animation / explainer /
side-by-side / breakdown.

Use it directly. Do NOT route through `write_file` + `save_artifact` first —
the widget IS the answer, not an attachment to it.

Skip it for: pure text, code blocks, short factual replies, error messages.

### Size

This is a WIDGET, not a webpage. Keep it compact:

- Target width ~600 px, max 640 px. The host iframe is already centered and
  capped — do NOT set `width: 100vw` or `min-width:1024px`.
- Target height ~360-480 px. Avoid scrollbars; if content is long, pick a
  more compact form (a table instead of cards, a single chart instead of
  five) rather than letting it grow.
- One focused thing per widget. If a request implies two unrelated visuals,
  ship two widget calls.

### Hard rules for `widget_code`

- It is an HTML fragment injected into `<div id="root">`. Do NOT include
  `<!DOCTYPE>`, `<html>`, `<head>`, or `<body>`.
- Order for streaming: `<style>` (short) first, then visible HTML, then
  `<script>` last. Scripts run only after the widget finishes streaming.
- No data fetching: fetch/XHR/WebSocket are blocked. Embed all data inline.
  (Loading JS libraries from the CDNs below IS permitted — that is
  `script-src`, not `connect-src`.)
- No `localStorage` / `sessionStorage` (they throw). Use in-memory variables.
- Libraries may be loaded only from: cdnjs.cloudflare.com, cdn.jsdelivr.net,
  unpkg.com, esm.sh.
- Total `widget_code` under ~256 KB.

### Visual quality bar

The shell pre-defines these CSS variables on `:root`; values are picked by
the host for the current light or dark theme:

- `--bg`     page background
- `--fg`     foreground text
- `--muted`  card / panel surface
- `--border` separator
- `--accent` link / primary

Always reference them via `var(--bg)` etc. **Do NOT hard-code colors** (no
`#1a1a1a`, no `#fff`, no `rgb()`); the widget must look right in BOTH themes.
Two font weights max (400, 500). Avoid gradients, drop shadows, blur. Use
generous whitespace and small radii (4-8 px). Keep typography to one family
(system-ui is already set).

### Scenario skeletons

Pick the one closest to the request and adapt — keep the overall shape,
swap the data and labels. These are starting points, not templates to
echo verbatim.

**Important — theme reactivity.** Anything that reads CSS variables into JS at
render time (Canvas, WebGL, Chart.js, D3, etc.) freezes those colours. The
host can repaint the widget when the app's theme toggles by updating `:root`'s
inline `style` attribute, so add a `MutationObserver` on the root to re-apply
the new tokens. Inline SVG / HTML that uses `var(--fg)` etc. directly does
NOT need this — the cascade re-evaluates automatically.

#### A. Single chart (Chart.js)

```html
<style>
  .card{background:var(--muted);border:1px solid var(--border);border-radius:8px;padding:16px;}
  .card h3{margin:0 0 8px;font-size:14px;font-weight:500;color:var(--fg);}
  canvas{width:100%!important;height:300px!important;}
</style>
<div class="card">
  <h3>Title that says what is plotted</h3>
  <canvas id="c"></canvas>
</div>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<script>
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  function tokens(){ return { fg: css('--fg'), acc: css('--accent'), bd: css('--border') }; }
  let t = tokens();
  const chart = new Chart(document.getElementById('c'), {
    type: 'line',
    data: { labels: ['A','B','C','D','E'], datasets: [{ label: 'Series', data: [3,8,5,12,9], borderColor: t.acc, backgroundColor: t.acc+'33', tension: .3 }] },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: t.fg } } },
      scales: { x: { ticks: { color: t.fg }, grid: { color: t.bd } },
                y: { ticks: { color: t.fg }, grid: { color: t.bd } } } }
  });
  // Re-apply theme tokens whenever the host updates :root's inline style.
  new MutationObserver(() => {
    t = tokens();
    chart.options.plugins.legend.labels.color = t.fg;
    chart.options.scales.x.ticks.color = t.fg;
    chart.options.scales.x.grid.color = t.bd;
    chart.options.scales.y.ticks.color = t.fg;
    chart.options.scales.y.grid.color = t.bd;
    chart.data.datasets[0].borderColor = t.acc;
    chart.data.datasets[0].backgroundColor = t.acc + '33';
    chart.update('none');
  }).observe(document.documentElement, { attributes: true, attributeFilter: ['style'] });
</script>
```

#### B. Architecture / flow diagram (inline SVG)

```html
<style>
  .diag{display:block;width:100%;height:auto;}
  .node{fill:var(--muted);stroke:var(--border);}
  .label{fill:var(--fg);font:500 12px system-ui;text-anchor:middle;dominant-baseline:central;}
  .edge{stroke:var(--border);stroke-width:1.5;fill:none;}
  .edge.hl{stroke:var(--accent);}  /* emphasized edge — arrowhead follows via context-stroke */
</style>
<svg class="diag" viewBox="0 0 560 220" xmlns="http://www.w3.org/2000/svg">
  <!-- One shared marker; fill:context-stroke makes each arrowhead inherit its
       OWN line's stroke, so a recoloured edge never gets a mismatched head. -->
  <defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6"
        orient="auto" markerUnits="userSpaceOnUse">
    <path d="M0 0 L10 5 L0 10 z" fill="context-stroke"/></marker></defs>
  <rect class="node" x="20"  y="80" width="120" height="60" rx="6"/>
  <text class="label" x="80"  y="110">Client</text>
  <rect class="node" x="220" y="80" width="120" height="60" rx="6"/>
  <text class="label" x="280" y="110">API</text>
  <rect class="node" x="420" y="80" width="120" height="60" rx="6"/>
  <text class="label" x="480" y="110">DB</text>
  <line class="edge"    x1="140" y1="110" x2="220" y2="110" marker-end="url(#a)"/>
  <line class="edge hl" x1="340" y1="110" x2="420" y2="110" marker-end="url(#a)"/>
</svg>
```

**Arrowheads must match their line.** Use one shared marker with
`fill="context-stroke"` so every arrowhead inherits its own edge's `stroke` —
an emphasized edge (`.hl` → `var(--accent)`) then gets a matching coloured head
automatically. **Never give the arrow a hard-coded `fill`** (a fixed token or
hex): a recoloured line would keep a mismatched grey head. Add
`markerUnits="userSpaceOnUse"` so the head stays a constant size regardless of
the line's `stroke-width`.

#### C. Dashboard (metric cards + small chart)

```html
<style>
  .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:12px;}
  .stat{background:var(--muted);border:1px solid var(--border);border-radius:8px;padding:12px;}
  .stat .v{font-size:22px;font-weight:500;color:var(--fg);}
  .stat .l{font-size:11px;color:var(--fg);opacity:.7;margin-top:2px;}
  .panel{background:var(--muted);border:1px solid var(--border);border-radius:8px;padding:16px;}
  canvas{width:100%!important;height:200px!important;}
</style>
<div class="grid">
  <div class="stat"><div class="v">1,284</div><div class="l">Active users</div></div>
  <div class="stat"><div class="v">$42.1k</div><div class="l">Revenue (MTD)</div></div>
  <div class="stat"><div class="v">98.6%</div><div class="l">Uptime</div></div>
</div>
<div class="panel"><canvas id="c"></canvas></div>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<script>/* Chart.js init — see scenario A */</script>
```

#### D. Interactive explainer (slider drives a calculation)

```html
<style>
  .row{display:flex;align-items:center;gap:12px;margin:8px 0;}
  .row label{flex:0 0 110px;color:var(--fg);font-size:13px;}
  .row input{flex:1;accent-color:var(--accent);}
  .row .val{flex:0 0 64px;text-align:right;font-variant-numeric:tabular-nums;color:var(--fg);}
  .out{margin-top:12px;padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--muted);}
  .out .big{font-size:24px;font-weight:500;color:var(--fg);}
  .out .lbl{font-size:11px;color:var(--fg);opacity:.7;}
</style>
<div class="row"><label>Principal</label><input id="p" type="range" min="1000" max="100000" step="100" value="10000"><div class="val" id="pv">$10,000</div></div>
<div class="row"><label>Years</label>    <input id="y" type="range" min="1"    max="40"     step="1"   value="10"><div class="val" id="yv">10</div></div>
<div class="row"><label>Rate (%)</label> <input id="r" type="range" min="0"    max="15"     step="0.1" value="5"><div class="val" id="rv">5%</div></div>
<div class="out"><div class="big" id="ans">$16,288.95</div><div class="lbl">Future value</div></div>
<script>
  const $=(id)=>document.getElementById(id);
  function recalc(){
    const P=+$('p').value, y=+$('y').value, r=+$('r').value/100;
    $('pv').textContent='$'+P.toLocaleString();
    $('yv').textContent=y; $('rv').textContent=r*100+'%';
    $('ans').textContent='$'+(P*Math.pow(1+r,y)).toLocaleString(undefined,{maximumFractionDigits:2});
  }
  ['p','y','r'].forEach(id=>$(id).addEventListener('input',recalc));
</script>
```

#### E. Side-by-side comparison table

```html
<style>
  table{width:100%;border-collapse:collapse;font-size:13px;color:var(--fg);}
  th,td{padding:8px 10px;border-bottom:1px solid var(--border);text-align:left;}
  th{font-weight:500;color:var(--fg);opacity:.85;background:var(--muted);}
  .check{color:var(--accent);font-weight:500;}
  .dash{opacity:.5;}
</style>
<table>
  <thead><tr><th>Feature</th><th>Option A</th><th>Option B</th></tr></thead>
  <tbody>
    <tr><td>Streams</td><td class="check">Yes</td><td class="dash">–</td></tr>
    <tr><td>Offline</td><td class="dash">–</td><td class="check">Yes</td></tr>
    <tr><td>Free tier</td><td>1k req/mo</td><td>500 req/mo</td></tr>
  </tbody>
</table>
```

If none of the above fits, write something simple in the same style — bordered
muted cards, accent color sparingly, small radii, no shadows. Better to ship a
plain card than a sprawling page.
"""
