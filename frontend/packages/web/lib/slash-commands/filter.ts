import type { SlashCommand, SlashCommandContext } from './types'

function matchesQuery(cmd: SlashCommand, query: string): boolean {
  if (!query) return true
  const q = query.toLowerCase()
  if (cmd.name.toLowerCase().includes(q)) return true
  if (cmd.aliases?.some((a) => a.toLowerCase().includes(q))) return true
  if (cmd.keywords?.some((k) => k.toLowerCase().includes(q))) return true
  if (cmd.description?.toLowerCase().includes(q)) return true
  return false
}

/** Available commands matching the query (MVP: hide unavailable). */
export function filterCommands(
  commands: SlashCommand[],
  query: string,
  ctx: SlashCommandContext,
): SlashCommand[] {
  return commands.filter((cmd) => cmd.isAvailable(ctx) && matchesQuery(cmd, query))
}

function isPrefixMatch(cmd: SlashCommand, query: string): boolean {
  const q = query.toLowerCase()
  if (cmd.name.toLowerCase().startsWith(q)) return true
  return Boolean(cmd.aliases?.some((alias) => alias.toLowerCase().startsWith(q)))
}

/**
 * Static commands first (registry order = usefulness), then skill entries.
 * A non-empty query re-ranks prefix matches on name/alias ahead of substring
 * hits (keywords, description, mid-name). Same-tier order is preserved.
 * Skills never displace a static command with the same display name —
 * collision is handled when building skill commands (canonical name fallback).
 */
export function filterSlashPalette(
  staticCommands: SlashCommand[],
  skillCommands: SlashCommand[],
  query: string,
  ctx: SlashCommandContext,
): SlashCommand[] {
  const statics = filterCommands(staticCommands, query, ctx)
  const skills = filterCommands(skillCommands, query, ctx)
  const combined = [...statics, ...skills]
  if (!query) return combined
  return [...combined].sort((a, b) => {
    const aPrefix = isPrefixMatch(a, query) ? 0 : 1
    const bPrefix = isPrefixMatch(b, query) ? 0 : 1
    return aPrefix - bPrefix
  })
}
