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

/**
 * Static commands first (registry order = usefulness), then skill entries.
 * Skills never displace a static command with the same display name in ranking —
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
  return [...statics, ...skills]
}
