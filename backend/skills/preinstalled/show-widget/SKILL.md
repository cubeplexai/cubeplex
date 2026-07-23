---
name: show-widget
description: >
  Build inline interactive HTML widgets with show_widget — charts, diagrams,
  dashboards, sliders, side-by-side comparisons, or any visual explainer.
  Load before non-trivial widget work for design rules and scenario skeletons.
version: 1.0.0
keywords:
  - widget
  - show_widget
  - chart
  - diagram
  - dashboard
  - 图表
  - 可视化
  - 流程图
  - 架构图
  - 仪表盘
---

# show-widget — inline interactive HTML

Render with the **`show_widget`** tool. Stream `widget_code` as an HTML
fragment; do **not** `write_file` + `save_artifact` first — the widget is the
answer.

## When to use

Prefer `show_widget` when the user is better off *seeing* than reading:
画 / 绘制 / 可视化 / 示意图 / 流程图 / 架构图 / 图表 / 仪表盘 / 对比 /
draw / visualize / chart / diagram / dashboard / flowchart / architecture /
mockup / sliders / animation / explainer / side-by-side / breakdown.

Skip for pure text, code blocks, short facts, or errors.

## Size

This is a widget, not a webpage:

- Target ~600 px wide (max 640). Host iframe is centered/capped — no
  `width:100vw` or `min-width:1024px`.
- Target height ~360–480 px. Prefer a denser form over scrollbars.
- One focus per call; unrelated visuals → multiple `show_widget` calls.

## Hard rules for `widget_code`

1. **HTML fragment** only — inject into `<div id="root">`. No `<!DOCTYPE>`,
   `<html>`, `<head>`, or `<body>`.
2. **Stream order:** short `<style>` → visible HTML → `<script>` last.
   Scripts run only after streaming finishes.
3. **No network from the widget:** fetch / XHR / WebSocket blocked. Embed all
   data. Loading libraries from allowed CDNs is fine (`script-src`, not
   `connect-src`).
4. **No `localStorage` / `sessionStorage`** (they throw). Use in-memory vars.
5. **CDN allowlist only:** `cdnjs.cloudflare.com`, `cdn.jsdelivr.net`,
   `unpkg.com`, `esm.sh`.
6. Total `widget_code` under ~256 KB.

## Visual quality

Host defines these CSS variables on `:root` (light/dark):

| Token | Use |
| --- | --- |
| `--bg` | page background |
| `--fg` | text |
| `--muted` | card / panel surface |
| `--border` | separators |
| `--accent` | primary / links |

Always use `var(--bg)` etc. **Never hard-code colors** (`#…`, `rgb()`, named
colors) — widgets must work in both themes. Two font weights max (400, 500).
No gradients, drop shadows, or blur. Radii 4–8 px. system-ui is already set.

### Theme reactivity (Canvas / Chart.js / D3)

Anything that **reads** CSS vars into JS at render time freezes those colors.
Observe `:root` `style` changes and re-apply tokens:

```js
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
function tokens() {
  return { fg: css('--fg'), acc: css('--accent'), bd: css('--border') };
}
new MutationObserver(() => { /* re-read tokens + chart.update('none') */ })
  .observe(document.documentElement, { attributes: true, attributeFilter: ['style'] });
```

Inline SVG/HTML using `var(--fg)` directly does **not** need this — cascade
re-evaluates automatically.

## Chart.js height (mandatory)

With `responsive:true` and `maintainAspectRatio:false`, Chart.js reads the
canvas **CSS** height — not the HTML `height` attribute. Without an explicit
CSS height pinned with `!important`, the canvas grows unbounded and freezes
the tab.

- One chart: `canvas{width:100%!important;height:300px!important;}`
- Multiple heights: per-id CSS or inline `style="width:100%;height:180px;display:block;"`

## Scenario skeletons

Pick the closest and adapt — swap data/labels; do not echo verbatim.

### A. Single chart (Chart.js)

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
    data: {
      labels: ['A','B','C','D','E'],
      datasets: [{
        label: 'Series', data: [3,8,5,12,9],
        borderColor: t.acc, backgroundColor: t.acc+'33', tension: .3
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: t.fg } } },
      scales: {
        x: { ticks: { color: t.fg }, grid: { color: t.bd } },
        y: { ticks: { color: t.fg }, grid: { color: t.bd } }
      }
    }
  });
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

### B. Architecture / flow (inline SVG)

```html
<style>
  .diag{display:block;width:100%;height:auto;}
  .node{fill:var(--muted);stroke:var(--border);}
  .label{fill:var(--fg);font:500 12px system-ui;text-anchor:middle;dominant-baseline:central;}
  .edge{stroke:var(--border);stroke-width:1.5;fill:none;}
  .edge.hl{stroke:var(--accent);}
</style>
<svg class="diag" viewBox="0 0 560 220" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6"
      orient="auto" markerUnits="userSpaceOnUse">
      <path d="M0 0 L10 5 L0 10 z" fill="context-stroke"/>
    </marker>
  </defs>
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

One shared marker with `fill="context-stroke"` so each arrowhead inherits its
edge stroke. Never hard-code marker fill. Use `markerUnits="userSpaceOnUse"`
for constant head size.

### C. Dashboard (metrics + small chart)

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
<script>/* Chart.js init — same token + MutationObserver pattern as A */</script>
```

### D. Interactive explainer (sliders)

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
<div class="row"><label>Years</label><input id="y" type="range" min="1" max="40" step="1" value="10"><div class="val" id="yv">10</div></div>
<div class="row"><label>Rate (%)</label><input id="r" type="range" min="0" max="15" step="0.1" value="5"><div class="val" id="rv">5%</div></div>
<div class="out"><div class="big" id="ans">$16,288.95</div><div class="lbl">Future value</div></div>
<script>
  const $ = (id) => document.getElementById(id);
  function recalc() {
    const P = +$('p').value, y = +$('y').value, r = +$('r').value / 100;
    $('pv').textContent = '$' + P.toLocaleString();
    $('yv').textContent = y;
    $('rv').textContent = (r * 100) + '%';
    $('ans').textContent = '$' + (P * Math.pow(1 + r, y)).toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  ['p', 'y', 'r'].forEach((id) => $(id).addEventListener('input', recalc));
</script>
```

### E. Side-by-side comparison

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

If nothing fits: bordered muted cards, accent sparingly, small radii, no
shadows. A plain card beats a sprawling page.
