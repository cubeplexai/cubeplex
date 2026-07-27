import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  AUTO_OPEN_ARTIFACTS_STORAGE_KEY,
  DEFAULT_AUTO_OPEN_ARTIFACTS,
  isAutoOpenArtifactsEnabled,
  parseAutoOpenArtifacts,
  setAutoOpenArtifactsEnabled,
  shouldAutoOpenArtifactPreview,
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
  it('opens when enabled and conversation is focused', () => {
    expect(shouldAutoOpenArtifactPreview('conv-1', 'conv-1', true)).toBe(true)
  })

  it('does not open when preference is off', () => {
    expect(shouldAutoOpenArtifactPreview('conv-1', 'conv-1', false)).toBe(false)
  })

  it('does not open for a non-focused conversation', () => {
    expect(shouldAutoOpenArtifactPreview('conv-1', 'conv-other', true)).toBe(false)
    expect(shouldAutoOpenArtifactPreview('conv-1', null, true)).toBe(false)
  })
})
