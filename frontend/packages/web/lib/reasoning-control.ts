import type { ReasoningControl } from '@cubebox/core'

import type { ThinkingLevel } from '@/lib/types/presets'

export const DEFAULT_REASONING: ReasoningControl = {
  mode: 'off',
  effort: 'medium',
  summary: 'none',
}

export function reasoningFromThinking(level: ThinkingLevel): ReasoningControl {
  if (level === 'off') return { mode: 'off', effort: 'minimal', summary: 'none' }
  return { mode: 'on', effort: level, summary: 'none' }
}

export function thinkingFromReasoning(
  reasoning: ReasoningControl | null | undefined,
): ThinkingLevel {
  if (!reasoning || reasoning.mode === 'off') return 'off'
  if (reasoning.effort === 'minimal') return 'low'
  return reasoning.effort
}
