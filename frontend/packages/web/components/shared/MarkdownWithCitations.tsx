'use client'

import ReactMarkdown from 'react-markdown'
import { memo, useMemo } from 'react'
import type { ComponentProps, MouseEvent } from 'react'
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
import { resolveSandboxHref } from '@/lib/sandboxLinks'

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

export interface SandboxMarkdownContext {
  /** Absolute sandbox path of the markdown file being rendered. */
  filePath: string
  /** Called when the user clicks a link that resolves to another sandbox file. */
  onNavigate: (path: string) => void
  /** Translates an asset path inside the sandbox into a fetchable URL (used for images). */
  resolveAssetUrl: (path: string) => string
}

interface MarkdownWithCitationsProps {
  children: string
  className?: string
  conversationId?: string
  /** When set, links/images that resolve inside the sandbox become navigable / fetchable. */
  sandbox?: SandboxMarkdownContext
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
  sandbox,
}: MarkdownWithCitationsProps) {
  const activeId = useConversationStore((s) => s.activeId)
  const conversationId = conversationIdProp ?? activeId ?? ''
  const md = fixCjkBoldQuotes(children)
  const hasCitations = CITATION_RE.test(md)
  const sandboxComponents = useMemo(
    () => (sandbox ? buildSandboxComponents(sandbox) : null),
    [sandbox],
  )

  if (!hasCitations) {
    return (
      <div className={className}>
        <ReactMarkdown
          remarkPlugins={REMARK_PLUGINS}
          rehypePlugins={REHYPE_PLUGINS}
          components={sandboxComponents ?? undefined}
        >
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
          ...(sandboxComponents ?? {}),
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

type MarkdownComponents = NonNullable<ComponentProps<typeof ReactMarkdown>['components']>

function buildSandboxComponents(sandbox: SandboxMarkdownContext): MarkdownComponents {
  return {
    a: ({ href, children, ...rest }) => {
      if (!href) return <a {...rest}>{children}</a>
      const resolved = resolveSandboxHref(sandbox.filePath, href)
      if (resolved.kind === 'sandbox') {
        const onClick = (e: MouseEvent<HTMLAnchorElement>) => {
          if (e.defaultPrevented) return
          if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return
          e.preventDefault()
          sandbox.onNavigate(resolved.path)
        }
        return (
          <a
            {...rest}
            href={sandbox.resolveAssetUrl(resolved.path) + (resolved.hash ?? '')}
            onClick={onClick}
            data-sandbox-link={resolved.path}
          >
            {children}
          </a>
        )
      }
      if (resolved.kind === 'anchor') {
        return (
          <a {...rest} href={resolved.hash}>
            {children}
          </a>
        )
      }
      return (
        <a {...rest} href={resolved.href}>
          {children}
        </a>
      )
    },
    img: ({ src, alt, ...rest }) => {
      // sandbox-served images are dynamic, same-origin authed URLs — next/image doesn't apply.
      /* eslint-disable @next/next/no-img-element */
      if (typeof src !== 'string' || !src) return <img src={src} alt={alt} {...rest} />
      const resolved = resolveSandboxHref(sandbox.filePath, src)
      if (resolved.kind === 'sandbox') {
        return <img {...rest} src={sandbox.resolveAssetUrl(resolved.path)} alt={alt} />
      }
      return <img {...rest} src={src} alt={alt} />
      /* eslint-enable @next/next/no-img-element */
    },
  }
}
