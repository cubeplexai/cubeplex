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
import { extractJsonStringPrefix } from '@/lib/partialJson'

const iconMap: Record<string, LucideIcon> = {
  execute: Terminal,
  write: Code,
  edit: Code,
  read: Code,
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
 * Prefer model-supplied `description` (short intent line) when present —
 * same pattern as Grok Build's bash/task tools. Otherwise pick the most
 * meaningful parameter for the tool kind.
 */
export function getParamSummary(
  toolName: string,
  args: Record<string, unknown>,
  maxLen = 60,
): string {
  const desc = args.description
  if (typeof desc === 'string' && desc.trim()) {
    const d = desc.trim().replace(/\s+/g, ' ')
    return d.length > maxLen ? d.slice(0, maxLen) + '...' : d
  }

  const bare = bareToolName(toolName)
  let value = ''
  if (bare === 'execute') {
    value = String(args.command ?? args.cmd ?? '')
  } else if (bare === 'write' || bare === 'edit' || bare === 'read') {
    value = String(args.file_path ?? args.file_name ?? args.path ?? '')
  } else if (bare === 'web_search' || bare === 'search') {
    value = String(args.query ?? args.q ?? '')
  } else if (bare === 'web_fetch' || bare === 'fetch') {
    value = String(args.url ?? '')
  } else if (bare === 'load_skill') {
    value = String(args.skill_name ?? '')
  } else {
    // Skip description-like keys already handled; prefer a stable string arg.
    const firstVal = Object.entries(args).find(
      ([k, v]) => k !== 'description' && typeof v === 'string' && v.trim(),
    )
    value = firstVal ? String(firstVal[1]) : ''
  }
  if (value.length > maxLen) {
    return value.slice(0, maxLen) + '...'
  }
  return value
}

/**
 * Summary for a still-streaming tool call, whose args are incomplete JSON.
 * Prefer a completed-or-partial `description` so execute can render intent
 * before a long `command` finishes. Execute does not fall back to raw JSON
 * — that would dump the command as it streams.
 */
export function getStreamingParamSummary(toolName: string, argsText: string, maxLen = 60): string {
  const desc = extractJsonStringPrefix(argsText, 'description')
  if (desc.trim()) {
    return getParamSummary(toolName, { description: desc }, maxLen)
  }
  if (bareToolName(toolName) === 'execute') return ''
  return argsText.trim()
}
