/**
 * MCP tool names from the backend are namespaced as `{server_slug}__{bare_tool}`
 * (sometimes with an id-disambiguator suffix). For UI classification (icons,
 * panel content type, etc.) the namespace is irrelevant — strip it here so
 * exact-match logic against bare names like "web_search" still works.
 *
 * Tools that don't carry a namespace (built-in agent tools) pass through
 * unchanged.
 */
export function bareToolName(toolName: string): string {
  const idx = toolName.indexOf('__')
  return idx < 0 ? toolName : toolName.slice(idx + 2)
}

export interface ToolNameParts {
  server: string | null
  tool: string
}

/** Split the namespaced name used on the wire into its UI-friendly parts. */
export function splitToolName(toolName: string): ToolNameParts {
  const idx = toolName.indexOf('__')
  return {
    server: idx < 0 ? null : toolName.slice(0, idx),
    tool: bareToolName(toolName),
  }
}

/** Turn snake_case / kebab-case tool identifiers into compact UI labels. */
export function humanizeToolName(toolName: string): string {
  const label = toolName.replace(/[_-]+/g, ' ').trim()
  return label ? label.charAt(0).toUpperCase() + label.slice(1) : toolName
}
