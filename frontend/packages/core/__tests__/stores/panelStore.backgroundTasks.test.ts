import { beforeEach, describe, expect, it } from 'vitest'
import { usePanelStore } from '../../src/stores/panelStore'

describe('background task panel', () => {
  beforeEach(() => usePanelStore.getState().close())

  it('shares the right-panel slot and keeps the conversation scope', () => {
    usePanelStore.getState().openBackgroundTasks('conv-1')
    expect(usePanelStore.getState().view).toEqual({
      type: 'background-tasks',
      conversationId: 'conv-1',
    })

    usePanelStore.getState().openTool('execute', {}, null)
    expect(usePanelStore.getState().view.type).toBe('tool')

    usePanelStore.getState().openBackgroundTasks('conv-2')
    expect(usePanelStore.getState().view).toEqual({
      type: 'background-tasks',
      conversationId: 'conv-2',
    })
  })
})
