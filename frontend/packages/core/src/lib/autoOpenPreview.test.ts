import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  AUTO_OPEN_ARTIFACTS_STORAGE_KEY,
  DEFAULT_AUTO_OPEN_ARTIFACTS,
  canAutoOpenBrowserPanel,
  canAutoOpenFilePreview,
  isAutoOpenArtifactsEnabled,
  isBrowserToolCall,
  isWriteOrEditTool,
  parseAutoOpenArtifacts,
  setAutoOpenArtifactsEnabled,
  shouldAutoOpenArtifactPreview,
  shouldAutoOpenBrowserPreview,
  shouldAutoOpenFilePreview,
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
    expect(isBrowserToolCall('write', { path: 'browser.md' })).toBe(false)
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

describe('isWriteOrEditTool', () => {
  it('matches write and edit, including MCP-namespaced forms', () => {
    expect(isWriteOrEditTool('write')).toBe(true)
    expect(isWriteOrEditTool('edit')).toBe(true)
    expect(isWriteOrEditTool('sandbox__write')).toBe(true)
    expect(isWriteOrEditTool('read')).toBe(false)
    expect(isWriteOrEditTool('execute')).toBe(false)
  })
})

describe('shouldAutoOpenFilePreview', () => {
  it('opens only while the chat surface is viewing that conversation', () => {
    expect(shouldAutoOpenFilePreview('conv-1', 'conv-1')).toBe(true)
    expect(shouldAutoOpenFilePreview('conv-1', 'conv-other')).toBe(false)
    expect(shouldAutoOpenFilePreview('conv-1', null)).toBe(false)
  })
})

describe('canAutoOpenFilePreview', () => {
  it('opens a closed panel and follows an existing write/edit preview', () => {
    expect(canAutoOpenFilePreview({ type: 'closed' })).toBe(true)
    expect(canAutoOpenFilePreview({ type: 'tool', contentType: 'write' })).toBe(true)
    expect(canAutoOpenFilePreview({ type: 'tool', contentType: 'edit', source: 'user' })).toBe(true)
  })

  it('may replace an auto-opened artifact but not a user-picked surface', () => {
    expect(canAutoOpenFilePreview({ type: 'artifact', source: 'auto' })).toBe(true)
    expect(canAutoOpenFilePreview({ type: 'artifact', source: 'user' })).toBe(false)
    expect(canAutoOpenFilePreview({ type: 'sandbox' })).toBe(false)
    expect(canAutoOpenFilePreview({ type: 'attachment' })).toBe(false)
    expect(canAutoOpenFilePreview({ type: 'tool', contentType: 'search' })).toBe(false)
  })
})
