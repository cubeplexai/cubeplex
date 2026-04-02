import {
  Terminal,
  Search,
  Globe,
  Code,
  Bot,
  Wrench,
  type LucideIcon,
} from 'lucide-react'

const iconMap: Record<string, LucideIcon> = {
  execute: Terminal,
  web_search: Search,
  search: Search,
  web_fetch: Globe,
  fetch: Globe,
  code_execute: Code,
  python: Code,
  subagent: Bot,
}

export function getToolIcon(
  toolName: string,
): LucideIcon {
  return iconMap[toolName] ?? Wrench
}

/**
 * Extract a human-readable summary from tool arguments.
 * Returns the most meaningful parameter value, truncated.
 */
export function getParamSummary(
  toolName: string,
  args: Record<string, unknown>,
  maxLen = 60,
): string {
  let value = ''
  if (toolName === 'execute') {
    value = String(args.command ?? args.cmd ?? '')
  } else if (
    toolName === 'web_search' || toolName === 'search'
  ) {
    value = String(args.query ?? args.q ?? '')
  } else if (
    toolName === 'web_fetch' || toolName === 'fetch'
  ) {
    value = String(args.url ?? '')
  } else {
    const firstVal = Object.values(args).find(
      (v) => typeof v === 'string',
    )
    value = firstVal ? String(firstVal) : ''
  }
  if (value.length > maxLen) {
    return value.slice(0, maxLen) + '...'
  }
  return value
}
