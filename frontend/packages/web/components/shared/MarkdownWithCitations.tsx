'use client'

import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import { useConversationStore } from '@cubebox/core'
import { renderWithCitations } from '@/lib/citations'
import { CitationMarker } from '@/components/chat/CitationMarker'

const CITATION_RE = /【\d+-\d+】/

interface MarkdownWithCitationsProps {
  children: string
  className?: string
}

/**
 * ReactMarkdown wrapper that detects 【N-M】 citation markers and renders
 * them as interactive CitationMarker components. Falls back to plain
 * ReactMarkdown when no markers are present.
 */
export function MarkdownWithCitations({ children, className }: MarkdownWithCitationsProps) {
  const conversationId = useConversationStore((s) => s.activeId) ?? ''
  const hasCitations = CITATION_RE.test(children)

  if (!hasCitations) {
    return (
      <div className={className}>
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>{children}</ReactMarkdown>
      </div>
    )
  }

  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}
        components={{
          p: ({ children: c }) => (
            <p>{renderWithCitations(c, conversationId, CitationMarker)}</p>
          ),
          li: ({ children: c }) => (
            <li>{renderWithCitations(c, conversationId, CitationMarker)}</li>
          ),
          td: ({ children: c }) => (
            <td>{renderWithCitations(c, conversationId, CitationMarker)}</td>
          ),
          th: ({ children: c }) => (
            <th>{renderWithCitations(c, conversationId, CitationMarker)}</th>
          ),
          blockquote: ({ children: c }) => (
            <blockquote>
              {renderWithCitations(c, conversationId, CitationMarker)}
            </blockquote>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
