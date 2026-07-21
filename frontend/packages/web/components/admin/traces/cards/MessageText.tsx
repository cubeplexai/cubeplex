'use client'

import ReactMarkdown from 'react-markdown'
import type { ComponentProps } from 'react'
import remarkGfm from 'remark-gfm'
import remarkBreaks from 'remark-breaks'
import rehypeHighlight from 'rehype-highlight'
import { proseClasses } from '@/lib/utils'

const REMARK_PLUGINS: ComponentProps<typeof ReactMarkdown>['remarkPlugins'] = [
  remarkGfm,
  remarkBreaks,
]
const REHYPE_PLUGINS: ComponentProps<typeof ReactMarkdown>['rehypePlugins'] = [
  [rehypeHighlight, { detect: true, ignoreMissing: true }],
]

interface Props {
  children: string
}

// Deliberately not MarkdownWithCitations (components/shared) - that component
// needs a conversationId from the live chat's Zustand store plus sandbox-link
// resolution, which don't apply to this read-only historical trace inspector.
export function MessageText({ children }: Props) {
  return (
    <div className={proseClasses}>
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS}>
        {children}
      </ReactMarkdown>
    </div>
  )
}
