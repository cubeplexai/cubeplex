import type { CommandToken } from './types'

/**
 * Open the palette only when the entire draft is a single leading command token:
 * optional leading whitespace + `/` + optional non-space query. Spec §4.1.
 */
const LEADING_COMMAND = /^\s*\/(\S*)$/

export function parseLeadingCommandToken(draft: string): CommandToken | null {
  const match = LEADING_COMMAND.exec(draft)
  if (!match) return null
  return {
    kind: 'command',
    raw: draft,
    query: match[1] ?? '',
  }
}
