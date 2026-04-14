import { useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { proseClasses } from '@/lib/utils'

const BLOCK_SEL = 'p, li, td, th, h1, h2, h3, h4, h5, h6, pre, blockquote, dt, dd'
const HIGHLIGHT_CLS = ['ring-2', 'ring-primary/50', 'bg-primary/10', 'rounded']

/** Extract word/CJK tokens from markdown source, ignoring all syntax chars. */
function extractTokens(text: string): string[] {
  return (text.match(/[\p{L}\p{N}]+/gu) ?? []).filter((w) => w.length > 1)
}

interface TextEntry { node: Text; start: number }

/**
 * Search the container's concatenated visible text for a token sequence
 * derived from the chunk, then collect every block-level element whose
 * text overlaps the matched range.
 *
 * This works across element boundaries (table cells, li + p, etc.)
 * because we search one continuous string, not per-block.
 */
function findChunkBlocks(
  container: HTMLElement,
  chunkText: string,
): HTMLElement[] {
  // 1. Collect text nodes → build concatenated DOM text + offset map
  const entries: TextEntry[] = []
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT)
  let total = 0
  while (walker.nextNode()) {
    const node = walker.currentNode as Text
    entries.push({ node, start: total })
    total += node.textContent?.length ?? 0
  }
  const domText = entries.map((e) => e.node.textContent ?? '').join('')

  // 2. Extract tokens from chunk (markdown-agnostic)
  const tokens = extractTokens(chunkText)
  if (tokens.length < 2) return []
  const head = tokens.slice(0, 8)
  const need = Math.min(head.length, 3)

  // 3. Find first occurrence of the token sequence in concatenated text
  let searchFrom = 0
  while (searchFrom < domText.length) {
    const firstIdx = domText.indexOf(head[0], searchFrom)
    if (firstIdx < 0) break

    let endPos = firstIdx + head[0].length
    let matched = 1
    for (let i = 1; i < head.length; i++) {
      const idx = domText.indexOf(head[i], endPos)
      if (idx < 0 || idx - endPos > 150) break
      endPos = idx + head[i].length
      matched++
    }

    if (matched >= need) {
      // Extend endPos through remaining tokens to cover the full chunk
      for (let i = head.length; i < tokens.length; i++) {
        const idx = domText.indexOf(tokens[i], endPos)
        if (idx < 0 || idx - endPos > 150) break
        endPos = idx + tokens[i].length
      }
      // 4. Map matched range [firstIdx, endPos] back to block elements
      const blocks: HTMLElement[] = []
      for (const entry of entries) {
        const nodeEnd = entry.start + (entry.node.textContent?.length ?? 0)
        if (nodeEnd <= firstIdx || entry.start >= endPos) continue
        const block = entry.node.parentElement?.closest(BLOCK_SEL) as HTMLElement | null
        if (block && !blocks.includes(block)) blocks.push(block)
      }
      if (blocks.length > 0) return blocks
    }

    searchFrom = firstIdx + 1
  }

  return []
}

interface WebFetchViewProps {
  args: Record<string, unknown>
  result: string | null
  highlightText?: string | null
  highlightKey?: number
}

export function WebFetchView({
  args,
  result,
  highlightText,
  highlightKey,
}: WebFetchViewProps) {
  const contentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!highlightText || !contentRef.current) return

    const blocks = findChunkBlocks(contentRef.current, highlightText)
    if (blocks.length === 0) return

    for (const b of blocks) b.classList.add(...HIGHLIGHT_CLS)
    blocks[0].scrollIntoView({ behavior: 'smooth', block: 'center' })

    return () => { for (const b of blocks) b.classList.remove(...HIGHLIGHT_CLS) }
  }, [highlightText, highlightKey])

  const url = String(args.url ?? '')

  return (
    <div className="p-4 space-y-3">
      {url && (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-primary
            hover:underline break-all"
        >
          {url}
        </a>
      )}
      {result && (
        <div ref={contentRef} className={proseClasses}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {result}
          </ReactMarkdown>
        </div>
      )}
    </div>
  )
}
