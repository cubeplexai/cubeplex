import { describe, expect, it, vi } from 'vitest'
import type { SkillSummary } from '@cubeplex/core'
import { skillCommandsFromSummaries } from '../skillCommands'
import { filterSlashPalette } from '../filter'
import { SLASH_COMMANDS } from '../registry'
import type { SlashCommandContext } from '../types'

function skill(partial: Partial<SkillSummary> & Pick<SkillSummary, 'id' | 'name'>): SkillSummary {
  return {
    source: 'preinstalled',
    description: partial.description ?? 'A skill',
    current_version: '1.0.0',
    keywords: partial.keywords ?? [],
    install_state: 'installed',
    installed_version: '1.0.0',
    workspace_bindings_count: 1,
    imported_from_registry_id: null,
    imported_from_registry_name: null,
    ...partial,
  }
}

function baseCtx(overrides: Partial<SlashCommandContext> = {}): SlashCommandContext {
  return {
    conversationId: 'conv-1',
    workspaceId: 'ws-1',
    isStreaming: false,
    effortAvailable: true,
    modelPickerAvailable: true,
    compactAvailable: true,
    cancelStream: vi.fn(),
    openModelPicker: vi.fn(),
    openEffortControl: vi.fn(),
    startRename: vi.fn(),
    openAttach: vi.fn(),
    createNewChat: vi.fn(),
    openShare: vi.fn(),
    openSkillsPicker: vi.fn(),
    openMcpPicker: vi.fn(),
    compactConversation: vi.fn(),
    pinSkill: vi.fn(),
    ...overrides,
  }
}

describe('skillCommandsFromSummaries', () => {
  it('uses primary slug as slash name', () => {
    const cmds = skillCommandsFromSummaries([skill({ id: 's1', name: 'deep-research' })])
    expect(cmds).toHaveLength(1)
    expect(cmds[0]!.name).toBe('deep-research')
    expect(cmds[0]!.id).toBe('skill:s1')
    expect(cmds[0]!.category).toBe('skill')
  })

  it('avoids colliding with static command names', () => {
    const cmds = skillCommandsFromSummaries([skill({ id: 's1', name: 'model' })])
    expect(cmds[0]!.name).toBe('model')
    // bare "model" collides — should fall back to canonical (same string here).
    // Namespaced collision:
    const namespaced = skillCommandsFromSummaries([
      skill({ id: 's2', name: 'acme:new', description: 'collision with /new' }),
    ])
    // primary is "new" which collides with static /new
    expect(namespaced[0]!.name).toBe('acme:new')
  })

  it('pins canonical name on run', () => {
    const pinSkill = vi.fn()
    const cmds = skillCommandsFromSummaries([skill({ id: 's1', name: 'acme:deep-research' })])
    void cmds[0]!.run(baseCtx({ pinSkill }))
    expect(pinSkill).toHaveBeenCalledWith({ id: 's1', name: 'acme:deep-research' })
  })
})

describe('filterSlashPalette order', () => {
  it('lists static usefulness order before skills', () => {
    const skillCmds = skillCommandsFromSummaries([
      skill({ id: 's1', name: 'zebra-skill' }),
      skill({ id: 's2', name: 'alpha-skill' }),
    ])
    const names = filterSlashPalette(SLASH_COMMANDS, skillCmds, '', baseCtx()).map((c) => c.name)
    expect(names[0]).toBe('new')
    expect(names).toContain('model')
    expect(names).toContain('skills')
    // skills alphabetically after static block
    const alpha = names.indexOf('alpha-skill')
    const zebra = names.indexOf('zebra-skill')
    const lastStatic = names.indexOf('rename')
    expect(alpha).toBeGreaterThan(lastStatic)
    expect(zebra).toBeGreaterThan(alpha)
  })

  it('filters skills by query', () => {
    const skillCmds = skillCommandsFromSummaries([
      skill({ id: 's1', name: 'deep-research', description: 'web research' }),
      skill({ id: 's2', name: 'code-review' }),
    ])
    const names = filterSlashPalette(SLASH_COMMANDS, skillCmds, 'deep', baseCtx()).map(
      (c) => c.name,
    )
    expect(names).toEqual(['deep-research'])
  })

  it('ranks name-prefix matches ahead of keyword-only matches for /p', () => {
    const skillCmds = skillCommandsFromSummaries([
      skill({ id: 's1', name: 'pdf', description: 'Generate a PDF' }),
    ])
    const names = filterSlashPalette(SLASH_COMMANDS, skillCmds, 'p', baseCtx()).map((c) => c.name)
    expect(names).toContain('pdf')
    expect(names).toContain('model')
    expect(names.indexOf('pdf')).toBeLessThan(names.indexOf('model'))
  })

  it('ranks alias-prefix matches ahead of keyword-only matches', () => {
    const always = (): boolean => true
    const noop = (): void => undefined
    const statics = [
      {
        id: 'model',
        name: 'model',
        category: 'composer' as const,
        keywords: ['clearance'],
        isAvailable: always,
        run: noop,
      },
      {
        id: 'new',
        name: 'new',
        aliases: ['clear'],
        category: 'conversation' as const,
        isAvailable: always,
        run: noop,
      },
    ]
    const names = filterSlashPalette(statics, [], 'cle', baseCtx()).map((c) => c.name)
    expect(names).toEqual(['new', 'model'])
  })
})
