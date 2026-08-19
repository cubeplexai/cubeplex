import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  AUTO_OPEN_ARTIFACTS_STORAGE_KEY,
  DEFAULT_AUTO_OPEN_ARTIFACTS,
  canAutoOpenBrowserPanel,
  canAutoOpenReplacePanel,
  isAutoOpenArtifactsEnabled,
  isBrowserToolCall,
  parseAutoOpenArtifacts,
  setAutoOpenArtifactsEnabled,
  shouldAutoOpenArtifactPreview,
  shouldAutoOpenBrowserPreview,
} from './autoOpenPreview'

describe('parseAutoOpenArtifacts', () => {
  it('defaults on when unset', () => {
    expect(parseAutoOpenArtifacts(null)).toBe(DEFAULT_AUTO_OPEN_ARTIFACTS)
    expect(parseAutoOpenArtifacts(undefined)).toBe(DEFAULT_AUTO_OPEN_ARTIFACTS)
  })

  it('accepts true/false and 0/1', () => {
    expect(parseAutoOpenArtifacts('true')).toBe(true)
    expect(parseAutoOpenArtifacts('1')).toBe(true)
    expect(parseAutoOpenArtifacts('false')).toBe(false)
    expect(parseAutoOpenArtifacts('0')).toBe(false)
  })

  it('falls back to default for garbage', () => {
    expect(parseAutoOpenArtifacts('maybe')).toBe(DEFAULT_AUTO_OPEN_ARTIFACTS)
  })
})

describe('isAutoOpenArtifactsEnabled / setAutoOpenArtifactsEnabled', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    localStorage.clear()
  })

  it('reads default when key missing', () => {
    expect(isAutoOpenArtifactsEnabled()).toBe(DEFAULT_AUTO_OPEN_ARTIFACTS)
  })

  it('round-trips disable', () => {
    setAutoOpenArtifactsEnabled(false)
    expect(localStorage.getItem(AUTO_OPEN_ARTIFACTS_STORAGE_KEY)).toBe('false')
    expect(isAutoOpenArtifactsEnabled()).toBe(false)
  })

  it('round-trips enable', () => {
    setAutoOpenArtifactsEnabled(false)
    setAutoOpenArtifactsEnabled(true)
    expect(localStorage.getItem(AUTO_OPEN_ARTIFACTS_STORAGE_KEY)).toBe('true')
    expect(isAutoOpenArtifactsEnabled()).toBe(true)
  })
})

describe('shouldAutoOpenArtifactPreview', () => {
  it('opens when enabled and chat surface is viewing the conversation', () => {
    expect(shouldAutoOpenArtifactPreview('conv-1', 'conv-1', true)).toBe(true)
  })

  it('does not open when preference is off', () => {
    expect(shouldAutoOpenArtifactPreview('conv-1', 'conv-1', false)).toBe(false)
  })

  it('does not open when chat surface is not that conversation', () => {
    expect(shouldAutoOpenArtifactPreview('conv-1', 'conv-other', true)).toBe(false)
    expect(shouldAutoOpenArtifactPreview('conv-1', null, true)).toBe(false)
  })
})

describe('canAutoOpenReplacePanel', () => {
  it('allows closed panel', () => {
    expect(canAutoOpenReplacePanel({ type: 'closed' }, 'conv-1', 'art-1')).toBe(true)
  })

  it('allows same artifact id (caller may no-op open to keep user source)', () => {
    expect(
      canAutoOpenReplacePanel(
        { type: 'artifact', conversationId: 'conv-1', artifactId: 'art-1', source: 'user' },
        'conv-1',
        'art-1',
      ),
    ).toBe(true)
  })

  it('allows switching artifacts only when current view was auto-opened', () => {
    expect(
      canAutoOpenReplacePanel(
        { type: 'artifact', conversationId: 'conv-1', artifactId: 'art-1', source: 'auto' },
        'conv-1',
        'art-2',
      ),
    ).toBe(true)
    expect(
      canAutoOpenReplacePanel(
        { type: 'artifact', conversationId: 'conv-1', artifactId: 'art-1', source: 'user' },
        'conv-1',
        'art-2',
      ),
    ).toBe(false)
  })

  it('blocks other conversation artifact and non-artifact surfaces', () => {
    expect(
      canAutoOpenReplacePanel(
        { type: 'artifact', conversationId: 'conv-2', artifactId: 'art-x', source: 'auto' },
        'conv-1',
        'art-1',
      ),
    ).toBe(false)
    expect(canAutoOpenReplacePanel({ type: 'tool' }, 'conv-1', 'art-1')).toBe(false)
    expect(canAutoOpenReplacePanel({ type: 'sandbox' }, 'conv-1', 'art-1')).toBe(false)
    expect(canAutoOpenReplacePanel({ type: 'attachment' }, 'conv-1', 'art-1')).toBe(false)
  })
})

describe('isBrowserToolCall', () => {
  it('detects loading the preinstalled browser skill', () => {
    expect(isBrowserToolCall('load_skill', { skill_name: 'browser' })).toBe(true)
    expect(isBrowserToolCall('load_skill', { skill_name: 'acme:browser' })).toBe(true)
    expect(isBrowserToolCall('load_skill', { skill_name: 'pdf' })).toBe(false)
  })

  it('detects execute commands that drive the sandbox browser', () => {
    expect(isBrowserToolCall('execute', { cmd: 'agent-browser goto https://x' })).toBe(true)
    expect(isBrowserToolCall('execute', { command: '/usr/local/bin/start-browser.sh' })).toBe(true)
    expect(isBrowserToolCall('execute', { cmd: 'echo hello' })).toBe(false)
  })

  it('ignores unrelated tools', () => {
    expect(isBrowserToolCall('web_search', { query: 'browser' })).toBe(false)
    expect(isBrowserToolCall('write_file', { path: 'browser.md' })).toBe(false)
  })
})

describe('shouldAutoOpenBrowserPreview', () => {
  it('opens only while the chat surface is viewing that conversation', () => {
    expect(shouldAutoOpenBrowserPreview('conv-1', 'conv-1')).toBe(true)
    expect(shouldAutoOpenBrowserPreview('conv-1', 'conv-other')).toBe(false)
    expect(shouldAutoOpenBrowserPreview('conv-1', null)).toBe(false)
  })
})

describe('canAutoOpenBrowserPanel', () => {
  it('opens a closed panel and retargets an already-open sandbox', () => {
    expect(canAutoOpenBrowserPanel({ type: 'closed' })).toBe(true)
    expect(canAutoOpenBrowserPanel({ type: 'sandbox' })).toBe(true)
  })

  it('may replace an auto-opened artifact but not a user-picked surface', () => {
    expect(canAutoOpenBrowserPanel({ type: 'artifact', source: 'auto' })).toBe(true)
    expect(canAutoOpenBrowserPanel({ type: 'artifact', source: 'user' })).toBe(false)
    expect(canAutoOpenBrowserPanel({ type: 'tool' })).toBe(false)
    expect(canAutoOpenBrowserPanel({ type: 'attachment' })).toBe(false)
  })
})
