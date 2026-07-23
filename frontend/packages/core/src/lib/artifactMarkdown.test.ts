import { describe, expect, it } from 'vitest'

import { isMarkdownArtifact, isMarkdownEditable, markdownFilename } from './artifactMarkdown'

const base = {
  artifact_type: 'document' as const,
  path: '/workspace/docs/guide.md',
  entry_file: null as string | null,
  mime_type: null as string | null,
}

describe('isMarkdownArtifact', () => {
  it('matches document + .md path', () => {
    expect(isMarkdownArtifact(base)).toBe(true)
  })

  it('matches .markdown and .mdx', () => {
    expect(isMarkdownArtifact({ ...base, path: '/a/b.markdown' })).toBe(true)
    expect(isMarkdownArtifact({ ...base, path: '/a/b.mdx' })).toBe(true)
  })

  it('matches mime even without md extension', () => {
    expect(
      isMarkdownArtifact({
        ...base,
        path: '/workspace/notes',
        mime_type: 'text/markdown',
      }),
    ).toBe(true)
    expect(
      isMarkdownArtifact({
        artifact_type: 'file',
        path: '/x',
        entry_file: null,
        mime_type: 'text/x-markdown; charset=utf-8',
      }),
    ).toBe(true)
  })

  it('rejects non-md document', () => {
    expect(isMarkdownArtifact({ ...base, path: '/workspace/a.pdf' })).toBe(false)
  })

  it('rejects image type even with md name', () => {
    expect(
      isMarkdownArtifact({
        ...base,
        artifact_type: 'image',
        path: '/workspace/x.md',
      }),
    ).toBe(false)
  })

  it('uses entry_file over path basename', () => {
    expect(
      isMarkdownArtifact({
        ...base,
        path: '/workspace/docs',
        entry_file: 'README.md',
      }),
    ).toBe(true)
  })
})

describe('markdownFilename', () => {
  it('prefers entry_file basename', () => {
    expect(markdownFilename({ path: '/workspace/docs', entry_file: 'nested/README.md' })).toBe(
      'README.md',
    )
  })

  it('rejects absolute entry_file', () => {
    expect(markdownFilename({ path: '/w', entry_file: '/etc/passwd' })).toBe(null)
  })

  it('rejects parent traversal in entry_file', () => {
    expect(markdownFilename({ path: '/w', entry_file: '../x.md' })).toBe(null)
  })
})

describe('isMarkdownEditable', () => {
  it('requires md filename target', () => {
    expect(isMarkdownEditable(base)).toBe(true)
    expect(
      isMarkdownEditable({
        artifact_type: 'document',
        path: '/workspace/notes',
        entry_file: null,
        mime_type: 'text/markdown',
      }),
    ).toBe(false)
  })
})
