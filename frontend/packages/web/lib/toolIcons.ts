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
import { bareToolName } from '@cubeplex/core'

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
  return iconMap[bareToolName(toolName)] ?? Wrench
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
  const bare = bareToolName(toolName)
  let value = ''
  if (bare === 'execute') {
    value = String(args.command ?? args.cmd ?? '')
  } else if (bare === 'write_file' || bare === 'edit_file') {
    value = String(args.file_path ?? args.file_name ?? '')
  } else if (bare === 'web_search' || bare === 'search') {
    value = String(args.query ?? args.q ?? '')
  } else if (bare === 'web_fetch' || bare === 'fetch') {
    value = String(args.url ?? '')
  } else if (bare === 'load_skill') {
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
