/**
 * MCP tool names from the backend are namespaced as `{server_slug}__{bare_tool}`
 * (sometimes with an id-disambiguator suffix). For UI classification (icons,
 * panel content type, etc.) the namespace is irrelevant — strip it here so
 * exact-match logic against bare names like "web_search" still works.
 *
 * Tools that don't carry a namespace (built-in agent tools) pass through
 * unchanged.
 */
export function bareToolName(toolName) {
    const idx = toolName.indexOf('__');
    return idx < 0 ? toolName : toolName.slice(idx + 2);
}
//# sourceMappingURL=toolName.js.map