import type { ReasoningControl } from '@cubeplex/core'
import type { ThinkingLevel, WorkspacePresetSummary } from '@/lib/types/presets'

export const DEFAULT_REASONING: null = null

export function reasoningLevels(preset: WorkspacePresetSummary | null): ThinkingLevel[] {
  const levels: ThinkingLevel[] = ['default']
  const modes = preset?.reasoning_modes ?? []
  if (modes.includes('off')) levels.push('off')
  if (modes.includes('on')) {
    const efforts = preset?.reasoning_efforts ?? []
    if (!efforts.length) levels.push('on')
    for (const effort of ['minimal', 'low', 'medium', 'high', 'max'] as const) {
      if (efforts.includes(effort)) levels.push(effort)
    }
  }
  if (
    modes.includes('auto') &&
    (!preset?.reasoning_efforts?.length || preset.reasoning_efforts.includes('medium'))
  )
    levels.push('auto')
  return levels
}
export function reasoningFromThinking(level: ThinkingLevel): ReasoningControl | null {
  if (level === 'default') return null
  if (level === 'off' || level === 'on' || level === 'auto') {
    return { mode: level, summary: 'none' }
  }
  return { mode: 'on', effort: level, summary: 'none' }
}
export function thinkingFromReasoning(
  reasoning: ReasoningControl | null | undefined,
): ThinkingLevel {
  if (!reasoning) return 'default'
  if (reasoning.mode !== 'on') return reasoning.mode
  return reasoning.effort ?? 'on'
}
