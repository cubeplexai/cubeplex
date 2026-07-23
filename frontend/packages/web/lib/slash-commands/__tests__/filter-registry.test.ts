import { describe, expect, it, vi } from 'vitest'
import { filterCommands } from '../filter'
import { SLASH_COMMANDS } from '../registry'
import type { SlashCommandContext } from '../types'

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
    openSkills: vi.fn(),
    openMcp: vi.fn(),
    compactConversation: vi.fn(),
    showHelp: vi.fn(),
    ...overrides,
  }
}

describe('filterCommands + registry availability', () => {
  it('lists idle P0 commands with conversation + workspace', () => {
    const names = filterCommands(SLASH_COMMANDS, '', baseCtx()).map((c) => c.name)
    expect(names).toContain('help')
    expect(names).toContain('new')
    expect(names).toContain('model')
    expect(names).toContain('skills')
    expect(names).toContain('mcp')
    expect(names).toContain('compact')
    expect(names).not.toContain('stop')
  })

  it('shows stop only while streaming', () => {
    const idle = filterCommands(SLASH_COMMANDS, 'stop', baseCtx()).map((c) => c.name)
    expect(idle).not.toContain('stop')
    const streaming = filterCommands(SLASH_COMMANDS, 'stop', baseCtx({ isStreaming: true })).map(
      (c) => c.name,
    )
    expect(streaming).toContain('stop')
  })

  it('hides compact while streaming', () => {
    const names = filterCommands(SLASH_COMMANDS, 'comp', baseCtx({ isStreaming: true })).map(
      (c) => c.name,
    )
    expect(names).not.toContain('compact')
  })

  it('hides compact when seam missing', () => {
    const names = filterCommands(SLASH_COMMANDS, '', baseCtx({ compactAvailable: false })).map(
      (c) => c.name,
    )
    expect(names).not.toContain('compact')
  })

  it('filters by substring on name and keywords', () => {
    const mod = filterCommands(SLASH_COMMANDS, 'mod', baseCtx()).map((c) => c.name)
    expect(mod).toEqual(['model'])
    const ski = filterCommands(SLASH_COMMANDS, 'ski', baseCtx()).map((c) => c.name)
    expect(ski).toEqual(['skills'])
  })

  it('hides skills/mcp without workspace', () => {
    const names = filterCommands(SLASH_COMMANDS, '', baseCtx({ workspaceId: null })).map(
      (c) => c.name,
    )
    expect(names).not.toContain('skills')
    expect(names).not.toContain('mcp')
  })

  it('hides rename/share/compact without conversation', () => {
    const names = filterCommands(SLASH_COMMANDS, '', baseCtx({ conversationId: undefined })).map(
      (c) => c.name,
    )
    expect(names).not.toContain('rename')
    expect(names).not.toContain('share')
    expect(names).not.toContain('compact')
  })

  it('runs stop via cancelStream without send side-effects', () => {
    const cancelStream = vi.fn()
    const cmd = SLASH_COMMANDS.find((c) => c.id === 'stop')!
    void cmd.run(baseCtx({ isStreaming: true, cancelStream }))
    expect(cancelStream).toHaveBeenCalledWith('conv-1')
  })
})
