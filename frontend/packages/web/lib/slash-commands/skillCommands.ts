import { formatSkillLabel, type SkillSummary } from '@cubeplex/core'
import type { SlashCommand } from './types'
import { SLASH_COMMANDS } from './registry'

const STATIC_NAMES = new Set(SLASH_COMMANDS.map((c) => c.name.toLowerCase()))

/**
 * Map enabled workspace skills to slash entries so users can type
 * `/deep-research` (or a namespaced primary) like other harnesses.
 *
 * Slash `name` prefers the short primary slug; if that collides with a static
 * command, fall back to the full canonical identity (may include `:`).
 */
export function skillCommandsFromSummaries(skills: readonly SkillSummary[]): SlashCommand[] {
  // Prefer stable alpha order so the palette does not jump between opens.
  const sorted = [...skills].sort((a, b) => a.name.localeCompare(b.name))
  return sorted.map((skill) => {
    const label = formatSkillLabel(skill.name)
    const primary = label.primary || skill.name
    const name = STATIC_NAMES.has(primary.toLowerCase()) ? label.canonical : primary
    const keywords = [
      skill.name,
      label.canonical,
      label.primary,
      ...(label.namespace ? [label.namespace] : []),
      ...skill.keywords,
    ].filter(Boolean)

    return {
      id: `skill:${skill.id}`,
      name,
      description: skill.description || undefined,
      category: 'skill' as const,
      keywords,
      isAvailable: () => true,
      run: (ctx) => {
        // Always pin by canonical name so load_skill sees the real identity.
        ctx.pinSkill({ id: skill.id, name: skill.name })
      },
    }
  })
}
