'use client'

import ReactMarkdown from 'react-markdown'
import type { ComponentProps } from 'react'
import remarkGfm from 'remark-gfm'
import remarkBreaks from 'remark-breaks'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import rehypeHighlight from 'rehype-highlight'
import rehypeExternalLinks from 'rehype-external-links'
import 'katex/dist/katex.min.css'

const REMARK_PLUGINS_WITH_MATH: ComponentProps<typeof ReactMarkdown>['remarkPlugins'] = [
  [remarkGfm, { singleTilde: false }],
  remarkBreaks,
  [remarkMath, { singleDollarTextMath: false }],
]

const REHYPE_PLUGINS_WITH_MATH: ComponentProps<typeof ReactMarkdown>['rehypePlugins'] = [
  rehypeKatex,
  [rehypeHighlight, { detect: true, ignoreMissing: true }],
  [rehypeExternalLinks, { target: '_blank', rel: ['noopener', 'noreferrer'] }],
]

interface MarkdownMathRendererProps {
  md: string
  components: ComponentProps<typeof ReactMarkdown>['components']
}

export function MarkdownMathRenderer({ md, components }: MarkdownMathRendererProps) {
  return (
    <ReactMarkdown
      remarkPlugins={REMARK_PLUGINS_WITH_MATH}
      rehypePlugins={REHYPE_PLUGINS_WITH_MATH}
      components={components}
    >
      {md}
    </ReactMarkdown>
  )
}
