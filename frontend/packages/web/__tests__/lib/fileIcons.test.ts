import { describe, expect, it } from 'vitest'
import { getFileVisual } from '@/lib/fileIcons'

describe('getFileVisual', () => {
  it('resolves PDF by extension', () => {
    const v = getFileVisual({ filename: 'report.pdf' })
    expect(v.family).toBe('pdf')
    expect(v.label).toBe('PDF')
    expect(v.bg).toBe('bg-rose-500')
  })

  it('resolves Word by extension', () => {
    expect(getFileVisual({ filename: 'doc.docx' }).family).toBe('word')
    expect(getFileVisual({ filename: 'doc.doc' }).family).toBe('word')
  })

  it('resolves Excel and CSV', () => {
    expect(getFileVisual({ filename: 'a.xlsx' }).family).toBe('excel')
    expect(getFileVisual({ filename: 'a.csv' }).family).toBe('csv')
  })

  it('resolves Markdown', () => {
    expect(getFileVisual({ filename: 'a.md' }).family).toBe('markdown')
    expect(getFileVisual({ filename: 'a.markdown' }).family).toBe('markdown')
  })

  it('resolves code by extension family', () => {
    expect(getFileVisual({ filename: 'x.ts' }).family).toBe('code')
    expect(getFileVisual({ filename: 'x.py' }).family).toBe('code')
    expect(getFileVisual({ filename: 'x.json' }).family).toBe('json')
  })

  it('falls back to mime when extension is unknown', () => {
    expect(getFileVisual({ filename: 'noext', mime_type: 'application/pdf' }).family).toBe('pdf')
    expect(getFileVisual({ filename: 'noext', mime_type: 'image/jpeg' }).family).toBe('image')
    expect(getFileVisual({ filename: 'noext', mime_type: 'video/mp4' }).family).toBe('video')
    expect(getFileVisual({ filename: 'noext', mime_type: 'audio/mpeg' }).family).toBe('audio')
    expect(getFileVisual({ filename: 'noext', mime_type: 'text/plain' }).family).toBe('text')
  })

  it('returns unknown for fully unknown input', () => {
    const v = getFileVisual({ filename: 'foo.xyz' })
    expect(v.family).toBe('unknown')
    expect(v.bg).toBe('bg-zinc-500')
  })

  it('handles empty input gracefully', () => {
    const v = getFileVisual({})
    expect(v.family).toBe('unknown')
  })
})
