import { useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { proseClasses } from '@/lib/utils'

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
    const walker = document.createTreeWalker(
      contentRef.current, NodeFilter.SHOW_TEXT,
    )
    const searchText = highlightText.slice(0, 50)
    let matched: HTMLElement | undefined
    while (walker.nextNode()) {
      const node = walker.currentNode
      if (node.textContent?.includes(searchText)) {
        const parent = node.parentElement
        if (parent) {
          parent.classList.add('ring-2', 'ring-primary/50', 'bg-primary/10')
          parent.scrollIntoView({ behavior: 'smooth', block: 'center' })
          matched = parent
        }
        break
      }
    }
    return () => { matched?.classList.remove('ring-2', 'ring-primary/50', 'bg-primary/10') }
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
