// --- Helpers (frontend ergonomics over the block-list shape) ---
export function getTextContent(msg) {
    return msg.content
        .filter((b) => b.type === 'text')
        .map((b) => b.text)
        .join('');
}
/**
 * Content to feed tool-result previews (SearchResultView / WebFetchView, citation
 * popovers). CitationMiddleware rewrites a tool result's `.content` to 【N-M】-marked
 * chunk text for the LLM and stashes the raw, parseable output in
 * `details.original_content` (see backend cubebox/middleware/citation.py). Previews
 * need that raw output — falling back to `.content` would feed them the citation
 * markup, which they can't parse. The live SSE path already prefers original_content
 * (cubebox/agents/stream.py `_stringify_tool_result`); this keeps reload consistent.
 */
export function getToolResultPreviewContent(msg) {
    const details = msg.details;
    if (typeof details?.original_content === 'string')
        return details.original_content;
    return getTextContent(msg);
}
export function getThinking(msg) {
    return msg.content
        .filter((b) => b.type === 'thinking')
        .map((b) => b.thinking)
        .join('');
}
export function getToolCalls(msg) {
    return msg.content.filter((b) => b.type === 'tool_call');
}
/**
 * Extract a SubagentSummary for a tool result message, handling both shapes:
 *
 *   - in-memory after live finalization: `metadata.subagent_events` already
 *     holds a normalized `SubagentSummary`
 *   - reloaded from cubepi: `details.subagent_events` holds the raw SSE event
 *     list collected by `SubAgentMiddleware` — we replay it into a summary
 *
 * Returns null when neither shape is present.
 */
export function getSubagentSummary(msg) {
    const fromMeta = msg.metadata?.subagent_events;
    if (fromMeta && !Array.isArray(fromMeta))
        return fromMeta;
    const details = msg.details;
    const events = details?.subagent_events;
    if (!Array.isArray(events))
        return null;
    const summary = {
        text: '',
        tool_calls: [],
        tool_results: [],
        thinking: '',
    };
    for (const evt of events) {
        if (!evt || typeof evt !== 'object')
            continue;
        const e = evt;
        switch (e.type) {
            case 'text_delta':
                summary.text += typeof e.delta === 'string' ? e.delta : '';
                break;
            case 'reasoning':
                summary.thinking += typeof e.delta === 'string' ? e.delta : '';
                break;
            case 'tool_call':
                summary.tool_calls.push({
                    id: typeof e.id === 'string' ? e.id : undefined,
                    name: typeof e.name === 'string' ? e.name : '',
                    arguments: e.arguments ?? {},
                });
                break;
            case 'tool_result':
                summary.tool_results.push({
                    tool_name: typeof e.name === 'string' ? e.name : '',
                    tool_call_id: typeof e.tool_call_id === 'string' ? e.tool_call_id : '',
                    content: typeof e.result === 'string' ? e.result : String(e.result ?? ''),
                });
                break;
        }
    }
    return summary;
}
//# sourceMappingURL=message.js.map