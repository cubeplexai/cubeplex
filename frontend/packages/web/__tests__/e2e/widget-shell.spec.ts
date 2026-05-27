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
      if ((e.data || {}).type === 'ready')
        (window as unknown as { __ready: boolean }).__ready = true
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

test('morph applies, then finalize runs scripts exactly once (idempotent)', async ({ page }) => {
  await mountShell(page)
  const frame = page.frameLocator('#wf')
  await send(page, {
    widgetId: WIDGET_ID,
    seq: 1,
    type: 'morph',
    html: '<p id="w">hi</p><script>window.__c=(window.__c||0)+1;document.getElementById("w").textContent="done"+window.__c;</script>',
  })
  await expect(frame.locator('#w')).toHaveText('hi') // script not run yet
  await send(page, { widgetId: WIDGET_ID, seq: 2, type: 'finalize' })
  await expect(frame.locator('#w')).toHaveText('done1') // ran once
  // a second (higher-seq) finalize must NOT re-run scripts (idempotent)
  await send(page, { widgetId: WIDGET_ID, seq: 3, type: 'finalize' })
  await page.waitForTimeout(200)
  await expect(frame.locator('#w')).toHaveText('done1') // still 1, not done2
})

test('latest-wins: a stale lower-seq morph is ignored', async ({ page }) => {
  await mountShell(page)
  const frame = page.frameLocator('#wf')
  await send(page, { widgetId: WIDGET_ID, seq: 5, type: 'morph', html: '<p id="w">new</p>' })
  await expect(frame.locator('#w')).toHaveText('new')
  await send(page, { widgetId: WIDGET_ID, seq: 2, type: 'morph', html: '<p id="w">stale</p>' })
  await page.waitForTimeout(200)
  await expect(frame.locator('#w')).toHaveText('new') // unchanged
})

test('widget cannot reach parent (opaque origin) nor fetch (connect-src none)', async ({
  page,
}) => {
  await mountShell(page)
  const frame = page.frameLocator('#wf')
  await send(page, {
    widgetId: WIDGET_ID,
    seq: 1,
    type: 'morph',
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
