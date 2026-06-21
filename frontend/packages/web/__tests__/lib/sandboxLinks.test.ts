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
})
