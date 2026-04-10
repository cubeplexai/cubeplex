import React, { type ReactNode } from 'react'

/** Regex matching 【N-M】 citation markers in text */
const CITATION_RE = /【(\d+)-(\d+)】/g

export interface CitationRef {
  citationId: number
  chunkIndex: number
}

/**
 * Parse a string for 【N-M】 markers and return an array of alternating
 * text strings and CitationRef objects.
 */
export function parseCitationMarkers(
  text: string,
): (string | CitationRef)[] {
  const parts: (string | CitationRef)[] = []
  let lastIndex = 0

  for (const match of text.matchAll(CITATION_RE)) {
    const before = text.slice(lastIndex, match.index)
    if (before) parts.push(before)
    parts.push({
      citationId: parseInt(match[1], 10),
      chunkIndex: parseInt(match[2], 10),
    })
    lastIndex = match.index + match[0].length
  }

  const tail = text.slice(lastIndex)
  if (tail) parts.push(tail)
  return parts
}

/**
 * Walk React children, find string nodes containing 【N-M】, and replace
 * markers with CitationMarker components. Non-string children pass through.
 *
 * @param children - React children from a markdown element (p, li, etc.)
 * @param conversationId - current conversation ID for store lookups
 * @param MarkerComponent - the CitationMarker component to render
 */
export function renderWithCitations(
  children: ReactNode,
  conversationId: string,
  MarkerComponent: React.ComponentType<{
    citationId: number
    chunkIndex: number
    conversationId: string
  }>,
): ReactNode {
  return React.Children.map(children, (child) => {
    if (typeof child === 'string') {
      const parts = parseCitationMarkers(child)
      if (parts.length === 1 && typeof parts[0] === 'string') {
        return child // no markers found, return original string
      }
      return parts.map((part, i) => {
        if (typeof part === 'string') return part
        return (
          <MarkerComponent
            key={`cite-${part.citationId}-${part.chunkIndex}-${i}`}
            citationId={part.citationId}
            chunkIndex={part.chunkIndex}
            conversationId={conversationId}
          />
        )
      })
    }
    // Recursively handle nested elements (e.g., <strong>, <em> inside <p>)
    if (React.isValidElement<{ children?: ReactNode }>(child) && child.props.children) {
      return React.cloneElement(
        child,
        undefined,
        renderWithCitations(child.props.children, conversationId, MarkerComponent),
      )
    }
    return child
  })
}
