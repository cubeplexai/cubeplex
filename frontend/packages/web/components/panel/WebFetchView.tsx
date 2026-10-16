import { useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import { markdownComponents } from '@/components/shared/markdownComponents'
import remarkGfm from 'remark-gfm'

import { proseClasses } from '@/lib/utils'

const BLOCK_SEL = 'p, li, td, th, h1, h2, h3, h4, h5, h6, pre, blockquote, dt, dd'
const HIGHLIGHT_CLS = ['ring-2', 'ring-primary/50', 'bg-primary/10', 'rounded']

/** Extract word/CJK tokens from markdown source, ignoring all syntax chars. */
function extractTokens(text: string): string[] {
  return (text.match(/[\p{L}\p{N}]+/gu) ?? []).filter((w) => w.length > 1)
}

interface TextEntry {
  text: string
  block: HTMLElement | null
  start: number
}

/**
 * Search rendered text (original source for Mermaid diagrams) for a token sequence
 * derived from the chunk, then collect every block-level element whose
 * text overlaps the matched range.
 *
 * This works across element boundaries (table cells, li + p, etc.)
 * because we search one continuous string, not per-block.
 */
function findChunkBlocks(container: HTMLElement, chunkText: string): HTMLElement[] {
  // 1. Diagrams keep their source on a stable wrapper; SVG labels omit syntax tokens.
  const entries: TextEntry[] = []
  const walker = document.createTreeWalker(
    container,
    NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT,
    {
      acceptNode(node) {
        if (node.parentElement?.closest('[data-mermaid-source]')) return NodeFilter.FILTER_REJECT
        if (
          node.nodeType === Node.TEXT_NODE ||
          (node instanceof HTMLElement && node.hasAttribute('data-mermaid-source'))
        ) {
          return NodeFilter.FILTER_ACCEPT
        }
        return NodeFilter.FILTER_SKIP
      },
    },
  )
  let total = 0
  while (walker.nextNode()) {
    const node = walker.currentNode
    const text =
      node instanceof HTMLElement
        ? `mermaid\n${node.dataset.mermaidSource ?? ''}`
        : (node.textContent ?? '')
    const block =
      node instanceof HTMLElement
        ? node
        : (node.parentElement?.closest<HTMLElement>(BLOCK_SEL) ?? null)
    entries.push({ text, block, start: total })
    total += text.length
  }
  const domText = entries.map((e) => e.text).join('')

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
        const nodeEnd = entry.start + entry.text.length
        if (nodeEnd <= firstIdx || entry.start >= endPos) continue
        const block = entry.block
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

export function WebFetchView({ args, result, highlightText, highlightKey }: WebFetchViewProps) {
  const contentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!highlightText || !contentRef.current) return

    let cancelled = false
    let highlighted: HTMLElement[] = []

    // ReactMarkdown renders asynchronously — defer highlight search until the
    // DOM content has been committed.  We retry a few times via rAF to handle
    // the first-open case where the markdown hasn't mounted yet.
    let attempts = 0
    const maxAttempts = 5

    function tryHighlight() {
      if (cancelled || !contentRef.current) return

      const blocks = findChunkBlocks(contentRef.current, highlightText!)
      if (blocks.length === 0 && ++attempts < maxAttempts) {
        requestAnimationFrame(tryHighlight)
        return
      }

      highlighted = blocks
      for (const b of blocks) b.classList.add(...HIGHLIGHT_CLS)
      if (blocks.length > 0) {
        blocks[0].scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    }

    requestAnimationFrame(tryHighlight)

    return () => {
      cancelled = true
      for (const b of highlighted) b.classList.remove(...HIGHLIGHT_CLS)
    }
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
          <ReactMarkdown components={markdownComponents} remarkPlugins={[remarkGfm]}>
            {result}
          </ReactMarkdown>
        </div>
      )}
    </div>
  )
}
