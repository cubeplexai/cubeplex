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
          h1: ({ children: c }) => (
            <h1>{renderWithCitations(c, conversationId, CitationMarker)}</h1>
          ),
          h2: ({ children: c }) => (
            <h2>{renderWithCitations(c, conversationId, CitationMarker)}</h2>
          ),
          h3: ({ children: c }) => (
            <h3>{renderWithCitations(c, conversationId, CitationMarker)}</h3>
          ),
          h4: ({ children: c }) => (
            <h4>{renderWithCitations(c, conversationId, CitationMarker)}</h4>
          ),
          h5: ({ children: c }) => (
            <h5>{renderWithCitations(c, conversationId, CitationMarker)}</h5>
          ),
          h6: ({ children: c }) => (
            <h6>{renderWithCitations(c, conversationId, CitationMarker)}</h6>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
