import { describe, it, expect } from 'vitest'
import { resolveSandboxHref } from '@/lib/sandboxLinks'

const BASE = '/workspace/docs/index.md'

describe('resolveSandboxHref', () => {
  it('resolves sibling file', () => {
    expect(resolveSandboxHref(BASE, '06-rollout/README.md')).toEqual({
      kind: 'sandbox',
      path: '/workspace/docs/06-rollout/README.md',
      hash: null,
    })
  })

  it('resolves explicit ./ prefix', () => {
    expect(resolveSandboxHref(BASE, './notes.md')).toEqual({
      kind: 'sandbox',
      path: '/workspace/docs/notes.md',
      hash: null,
    })
  })

  it('resolves parent traversal', () => {
    expect(resolveSandboxHref(BASE, '../other/file.md')).toEqual({
      kind: 'sandbox',
      path: '/workspace/other/file.md',
      hash: null,
    })
  })

  it('treats leading slash as workspace-root', () => {
    expect(resolveSandboxHref(BASE, '/top.md')).toEqual({
      kind: 'sandbox',
      path: '/workspace/top.md',
      hash: null,
    })
  })

  it('preserves fragment in sandbox links', () => {
    expect(resolveSandboxHref(BASE, 'guide.md#install')).toEqual({
      kind: 'sandbox',
      path: '/workspace/docs/guide.md',
      hash: '#install',
    })
  })

  it('flags absolute URLs as external', () => {
    expect(resolveSandboxHref(BASE, 'https://example.com/x')).toEqual({
      kind: 'external',
      href: 'https://example.com/x',
    })
  })

  it('flags mailto/tel as external', () => {
    expect(resolveSandboxHref(BASE, 'mailto:a@b.co')).toEqual({
      kind: 'external',
      href: 'mailto:a@b.co',
    })
  })

  it('flags protocol-relative URLs as external', () => {
    expect(resolveSandboxHref(BASE, '//cdn.example.com/x.png')).toEqual({
      kind: 'external',
      href: '//cdn.example.com/x.png',
    })
  })

  it('returns anchor for in-page fragments', () => {
    expect(resolveSandboxHref(BASE, '#section')).toEqual({
      kind: 'anchor',
      hash: '#section',
    })
  })

  it('decodes percent-escaped filenames', () => {
    expect(resolveSandboxHref(BASE, 'Roadmap%202026.md')).toEqual({
      kind: 'sandbox',
      path: '/workspace/docs/Roadmap 2026.md',
      hash: null,
    })
  })

  it('strips query string but keeps fragment', () => {
    expect(resolveSandboxHref(BASE, 'assets/flow.png?v=1#top')).toEqual({
      kind: 'sandbox',
      path: '/workspace/docs/assets/flow.png',
      hash: '#top',
    })
  })

  it('keeps invalid percent escapes literal instead of throwing', () => {
    expect(resolveSandboxHref(BASE, 'bad%2.md')).toEqual({
      kind: 'sandbox',
      path: '/workspace/docs/bad%2.md',
      hash: null,
    })
  })

  it('clamps parent traversal past workspace root', () => {
    expect(resolveSandboxHref(BASE, '../../../../etc/passwd')).toEqual({
      kind: 'sandbox',
      path: '/workspace',
      hash: null,
    })
  })

  it('clamps encoded parent traversal', () => {
    expect(resolveSandboxHref(BASE, '%2e%2e/%2e%2e/%2e%2e/%2e%2e/etc/passwd')).toEqual({
      kind: 'sandbox',
      path: '/workspace',
      hash: null,
    })
  })

  it('clamps encoded-slash traversal hidden inside one raw segment', () => {
    expect(resolveSandboxHref(BASE, '%2e%2e%2f%2e%2e%2f%2e%2e%2fetc/passwd')).toEqual({
      kind: 'sandbox',
      path: '/workspace',
      hash: null,
    })
  })
})
