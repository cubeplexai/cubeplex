/**
 * Composer skill chips — selected skills ride with the next send/steer as a
 * natural-language prefix so the agent can load_skill by canonical name.
 * No message-metadata API yet (option B from the prompt-bar design).
 */

export type ComposerSkillChip = {
  id: string
  /** Canonical skill name (e.g. deep-research or org:slug). */
  name: string
}

/** Build the text actually sent when skill chips are pinned on the composer. */
export function applySkillChipsToContent(
  content: string,
  skills: readonly ComposerSkillChip[],
): string {
  if (skills.length === 0) return content
  const names = skills.map((s) => s.name)
  const lead =
    names.length === 1
      ? `Use skill \`${names[0]}\`.`
      : `Use skills ${names.map((n) => `\`${n}\``).join(', ')}.`
  const body = content.trim()
  return body.length > 0 ? `${lead}\n\n${body}` : lead
}
