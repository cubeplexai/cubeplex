import { describe, expect, it } from 'vitest'

import { reasoningLevels } from '@/lib/reasoning-control'
import type { WorkspacePresetSummary } from '@/lib/types/presets'

function preset(overrides: Partial<WorkspacePresetSummary>): WorkspacePresetSummary {
  return {
    key: 'deepseek',
    kind: 'custom',
    primary: 'deepseek/deepseek-v4-flash',
    description: '',
    is_default: false,
    ...overrides,
  }
}

describe('reasoningLevels', () => {
  it('places off before effort names, with default first', () => {
    expect(
      reasoningLevels(
        preset({
          reasoning_modes: ['on', 'off'],
          reasoning_efforts: ['max', 'minimal', 'high', 'low', 'medium'],
        }),
      ),
    ).toEqual(['default', 'off', 'minimal', 'low', 'medium', 'high', 'max'])
  })
})
