/**
 * Browser-side OAuth pop-up controller for MCP four-layer authentication.
 * Spec: docs/superpowers/specs/2026-05-16-mcp-install-auth-handoff-spec.md §5.5.
 *
 * Must be invoked synchronously from the user-activation click handler:
 * `window.open` is gated on the activation token, which is consumed by
 * any preceding `await`. The popup is opened to about:blank first, then
 * navigated after the start POST returns.
 */
const CHANNEL_NAME = 'cubebox-mcp-oauth';
const TIMEOUT_MS = 90000;
const POLL_INTERVAL_MS = 1000;
export async function runOAuthFlow(deps) {
    // 1. Open popup synchronously BEFORE any await.
    //
    // Per-flow unique window target name. A fixed name like
    // `mcp-oauth` would make window.open reuse the existing popup
    // when a second flow starts before the first finishes (codex
    // round-7 catch). crypto.randomUUID is cleanest but is missing on
    // browsers older than ~Chrome 92 / FF 95 / Safari 15.4 and on
    // non-secure contexts other than localhost (real bug surfaced
    // in local testing). Fall back to timestamp + Math.random — this
    // string only needs to be locally unique within the page, not
    // cryptographic.
    const uniqueSuffix = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const target = `mcp-oauth-${uniqueSuffix}`;
    const child = window.open('about:blank', target, 'width=620,height=760');
    if (child === null) {
        return { status: 'error', reason: 'popup_blocked' };
    }
    // 2. Open BroadcastChannel.
    const channel = new BroadcastChannel(CHANNEL_NAME);
    try {
        // 3. Fetch start.
        let start;
        try {
            start = await deps.startPost();
        }
        catch (err) {
            child.close();
            return { status: 'error', reason: `start_failed:${err.message}` };
        }
        // 4. Set up the message listener and timers BEFORE navigating the
        //    popup. If the AS is already authorized (silent re-consent) or
        //    the network is fast, /oauth/mcp/return can broadcast within
        //    milliseconds — attaching the listener after navigation can
        //    miss the message and leave the parent waiting for the
        //    timeout / closed-popup poll, both of which would resolve as
        //    a wrong status.
        return await new Promise((resolve) => {
            let done = false;
            const finish = (r) => {
                if (done)
                    return;
                done = true;
                clearTimeout(timer);
                clearInterval(poll);
                channel.removeEventListener('message', onMessage);
                resolve(r);
            };
            const onMessage = (ev) => {
                const m = ev.data;
                if (!m || m.kind !== 'mcp.oauth.return')
                    return;
                if (m.state !== start.state)
                    return; // strict — see spec §5.5/5.6
                if (m.status === 'ok')
                    return finish({ status: 'ok' });
                if (m.status === 'cancelled')
                    return finish({ status: 'cancelled' });
                finish({ status: 'error', reason: m.reason ?? 'callback_error' });
            };
            const timer = setTimeout(() => {
                try {
                    child.close();
                }
                catch {
                    /* ignore */
                }
                finish({ status: 'error', reason: 'timeout' });
            }, TIMEOUT_MS);
            const poll = setInterval(() => {
                if (child.closed) {
                    finish({ status: 'cancelled' });
                }
            }, POLL_INTERVAL_MS);
            channel.addEventListener('message', onMessage);
            // Now safe to navigate — listener is live.
            try {
                child.location.href = start.authorize_url;
            }
            catch {
                child.close();
                finish({ status: 'error', reason: 'popup_navigate_failed' });
            }
        });
    }
    finally {
        channel.close();
    }
}
//# sourceMappingURL=runOAuthFlow.js.map