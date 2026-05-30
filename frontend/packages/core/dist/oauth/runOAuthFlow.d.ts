/**
 * Browser-side OAuth pop-up controller for MCP four-layer authentication.
 * Spec: docs/superpowers/specs/2026-05-16-mcp-install-auth-handoff-spec.md §5.5.
 *
 * Must be invoked synchronously from the user-activation click handler:
 * `window.open` is gated on the activation token, which is consumed by
 * any preceding `await`. The popup is opened to about:blank first, then
 * navigated after the start POST returns.
 */
export interface OAuthStartResponse {
    authorize_url: string;
    state: string;
    expires_at: string;
}
export interface OAuthFlowResult {
    status: 'ok' | 'cancelled' | 'error';
    reason?: string;
}
export interface RunOAuthFlowDeps {
    /** Performs the start POST. Caller composes the path per scope. */
    startPost: () => Promise<OAuthStartResponse>;
}
export declare function runOAuthFlow(deps: RunOAuthFlowDeps): Promise<OAuthFlowResult>;
//# sourceMappingURL=runOAuthFlow.d.ts.map