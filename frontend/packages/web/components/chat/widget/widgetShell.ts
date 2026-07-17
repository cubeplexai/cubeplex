// srcDoc for the widget iframe. The CSP meta MUST be the first element in
// <head>. Parent -> child: {widgetId, seq, type:'morph', html} / {...'finalize'}.
// Child -> parent: {widgetId, type:'ready'|'error'|'resize', ...}.
//
// Sandbox is opaque-origin (no allow-same-origin), so the iframe cannot inherit
// the parent's CSS variables via the cascade. WidgetView injects theme tokens
// AND the widgetId via single-replace placeholders at mount:
//   %%WIDGET_ID%%  — appears once in `var WIDGET_ID = %%WIDGET_ID%%;`
//                     (NO surrounding quotes; JSON.stringify supplies them)
//   %%BG%%, %%FG%%, %%MUTED%%, %%BORDER%%, %%ACCENT%%
//                  — each appears once in the :root CSS variables below
// Widget code should always reference var(--bg)/--fg/--muted/--border/--accent
// instead of hard-coding colors (see WIDGET_GUIDELINES).
export const WIDGET_SHELL_HTML = `<!DOCTYPE html><html><head>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://unpkg.com https://esm.sh; style-src 'unsafe-inline'; img-src data: https:; font-src data: https:; connect-src 'none'; base-uri 'none'; form-action 'none'; worker-src 'none'; frame-src 'none'; object-src 'none';">
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<style>
:root{--bg:%%BG%%;--fg:%%FG%%;--muted:%%MUTED%%;--border:%%BORDER%%;--accent:%%ACCENT%%;}
*{box-sizing:border-box}
body{margin:0;padding:1rem;font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--fg);}
@keyframes _fadeIn{from{opacity:0;transform:translateY(4px);}to{opacity:1;transform:none;}}
@keyframes _pulse{0%,100%{opacity:.35}50%{opacity:.7}}
/* Skeleton lives in #root and is replaced by morphdom on the first morph.
   It reserves stable vertical space so the parent message layout doesn't
   shift when the iframe transitions from empty -> rendered. */
._skel{display:flex;flex-direction:column;gap:.55rem;padding:.25rem 0 1rem;}
._skel-bar{height:.85rem;border-radius:.25rem;background:var(--muted);border:1px solid var(--border);animation:_pulse 1.6s ease-in-out infinite;}
._skel-bar._lg{height:1.6rem;width:35%;}
._skel-card{margin-top:.4rem;padding:1rem;border:1px solid var(--border);border-radius:.5rem;background:var(--muted);height:8.5rem;display:flex;align-items:center;justify-content:center;color:transparent;animation:_pulse 1.6s ease-in-out infinite;}
/* Guard against Chart.js responsive + auto-height container feedback loop:
   when maintainAspectRatio:false and no CSS height is set, Chart.js reads the
   parent clientHeight (which includes the canvas itself), computes a larger
   value, resizes, and loops. max-height caps the CSS-rendered size, which is
   what ResizeObserver sees, so the loop converges. #root prefix wins over a
   plain canvas{} rule in widget code without needing !important. */
#root canvas{display:block;max-height:1200px;}
</style></head>
<body><div id="root"><div class="_skel" aria-hidden="true"><div class="_skel-bar _lg"></div><div class="_skel-bar" style="width:80%"></div><div class="_skel-bar" style="width:65%"></div><div class="_skel-card">·</div></div></div>
<script>
(function(){
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

  // Run #root scripts in document order. External (src) scripts are awaited
  // before the next script runs, so an inline initializer (e.g. new Chart(...))
  // never executes before its CDN library has loaded. Attributes are preserved.
  function runScripts(done){
    var scripts = Array.prototype.slice.call(document.querySelectorAll('#root script'));
    var i = 0;
    function next(){
      if (i >= scripts.length) { if (done) done(); return; }
      var old = scripts[i++];
      var s = document.createElement('script');
      for (var a = 0; a < old.attributes.length; a++) {
        s.setAttribute(old.attributes[a].name, old.attributes[a].value);
      }
      if (old.src) {
        s.onload = next;
        s.onerror = next; // proceed even if a CDN script fails
        old.parentNode.replaceChild(s, old);
      } else {
        s.textContent = old.textContent;
        old.parentNode.replaceChild(s, old); // inline runs synchronously
        next();
      }
    }
    next();
  }

  // Update :root CSS variables in place from a theme message. Theme tokens are
  // colour strings; only those known keys are honoured (defence against random
  // extra payload fields).
  function applyTheme(d){
    var s = document.documentElement.style;
    if (typeof d.bg === 'string')     s.setProperty('--bg', d.bg);
    if (typeof d.fg === 'string')     s.setProperty('--fg', d.fg);
    if (typeof d.muted === 'string')  s.setProperty('--muted', d.muted);
    if (typeof d.border === 'string') s.setProperty('--border', d.border);
    if (typeof d.accent === 'string') s.setProperty('--accent', d.accent);
  }

  window.addEventListener('message', function(e){
    if (e.source !== parent) return;
    var d = e.data;
    if (!d || typeof d !== 'object') return;
    if (d.widgetId !== WIDGET_ID) return;
    // Theme messages are stateless (no ordering needed); apply and return.
    if (d.type === 'theme') { try { applyTheme(d); } catch(_){} return; }
    if (typeof d.seq !== 'number' || d.seq <= lastSeq) return; // latest-wins
    lastSeq = d.seq;
    try {
      if (d.type === 'morph') {
        if (finalized) return;
        applyMorph(d.html);
      } else if (d.type === 'finalize') {
        if (finalized) return;
        finalized = true;
        runScripts(function(){ post({type:'resize', height: document.body.scrollHeight}); });
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
