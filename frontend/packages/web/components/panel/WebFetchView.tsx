import { useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { proseClasses } from '@/lib/utils'

interface WebFetchViewProps {
  args: Record<string, unknown>
  result: string | null
  highlightText?: string | null
}

export function WebFetchView({
  args,
  result,
  highlightText,
}: WebFetchViewProps) {
  const contentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!highlightText || !contentRef.current) return
    const walker = document.createTreeWalker(
      contentRef.current, NodeFilter.SHOW_TEXT,
    )
    const searchText = highlightText.slice(0, 50)
    while (walker.nextNode()) {
      const node = walker.currentNode
      if (node.textContent?.includes(searchText)) {
        const parent = node.parentElement
        if (parent) {
          parent.classList.add('ring-2', 'ring-yellow-400/50', 'bg-yellow-50/10')
          parent.scrollIntoView({ behavior: 'smooth', block: 'center' })
          setTimeout(() => {
            parent.classList.remove('ring-2', 'ring-yellow-400/50', 'bg-yellow-50/10')
          }, 2000)
        }
        break
      }
    }
  }, [highlightText])

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
