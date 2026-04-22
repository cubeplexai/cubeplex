import {
  Terminal,
  Search,
  Globe,
  Code,
  Bot,
  BookOpen,
  Package,
  Wrench,
  type LucideIcon,
} from 'lucide-react'

const iconMap: Record<string, LucideIcon> = {
  execute: Terminal,
  write_file: Code,
  edit_file: Code,
  web_search: Search,
  search: Search,
  web_fetch: Globe,
  fetch: Globe,
  code_execute: Code,
  python: Code,
  subagent: Bot,
  load_skill: BookOpen,
  save_artifact: Package,
}

export function getToolIcon(toolName: string): LucideIcon {
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
  } else if (toolName === 'write_file' || toolName === 'edit_file') {
    value = String(args.file_path ?? args.file_name ?? '')
  } else if (toolName === 'web_search' || toolName === 'search') {
    value = String(args.query ?? args.q ?? '')
  } else if (toolName === 'web_fetch' || toolName === 'fetch') {
    value = String(args.url ?? '')
  } else if (toolName === 'load_skill') {
    value = String(args.skill_name ?? '')
  } else {
    const firstVal = Object.values(args).find((v) => typeof v === 'string')
    value = firstVal ? String(firstVal) : ''
  }
  if (value.length > maxLen) {
    return value.slice(0, maxLen) + '...'
  }
  return value
}
