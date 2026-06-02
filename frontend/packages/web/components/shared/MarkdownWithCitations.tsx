'use client'

import ReactMarkdown from 'react-markdown'
import { memo } from 'react'
import type { ComponentProps } from 'react'
import remarkGfm from 'remark-gfm'
import remarkBreaks from 'remark-breaks'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import rehypeHighlight from 'rehype-highlight'
import rehypeExternalLinks from 'rehype-external-links'
import 'katex/dist/katex.min.css'
import { useConversationStore } from '@cubebox/core'
import { renderWithCitations } from '@/lib/citations'
import { CitationMarker } from '@/components/chat/CitationMarker'

const CITATION_RE = /【\d+-\d+】/

// singleTilde:false → only ~~text~~ is strikethrough, so "28~29℃" ranges render literally.
const REMARK_PLUGINS: ComponentProps<typeof ReactMarkdown>['remarkPlugins'] = [
  [remarkGfm, { singleTilde: false }],
  remarkBreaks,
  remarkMath,
]

const REHYPE_PLUGINS: ComponentProps<typeof ReactMarkdown>['rehypePlugins'] = [
  rehypeKatex,
  [rehypeHighlight, { detect: true, ignoreMissing: true }],
  [rehypeExternalLinks, { target: '_blank', rel: ['noopener', 'noreferrer'] }],
]

/**
 * Move CJK quotes outside bold/italic markers so CommonMark flanking rules
 * recognise them correctly.  e.g. **\u201cfoo\u201d** → \u201c**foo**\u201d
 */
function fixCjkBoldQuotes(text: string): string {
  return text
    .replace(/\*\*\s*(["\u201c\u300c])/g, '$1**')
    .replace(/(["\u201d\u300d])\s*\*\*/g, '**$1')
}

interface MarkdownWithCitationsProps {
  children: string
  className?: string
  conversationId?: string
}

/**
 * ReactMarkdown wrapper that detects 【N-M】 citation markers and renders
 * them as interactive CitationMarker components. Falls back to plain
 * ReactMarkdown when no markers are present.
 */
function MarkdownWithCitationsImpl({
  children,
  className,
  conversationId: conversationIdProp,
}: MarkdownWithCitationsProps) {
  const activeId = useConversationStore((s) => s.activeId)
  const conversationId = conversationIdProp ?? activeId ?? ''
  const md = fixCjkBoldQuotes(children)
  const hasCitations = CITATION_RE.test(md)

  if (!hasCitations) {
    return (
      <div className={className}>
        <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS}>
          {md}
        </ReactMarkdown>
      </div>
    )
  }

  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        components={{
          p: ({ children: c }) => <p>{renderWithCitations(c, conversationId, CitationMarker)}</p>,
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
            <blockquote>{renderWithCitations(c, conversationId, CitationMarker)}</blockquote>
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
        {md}
      </ReactMarkdown>
    </div>
  )
}

export const MarkdownWithCitations = memo(MarkdownWithCitationsImpl)
