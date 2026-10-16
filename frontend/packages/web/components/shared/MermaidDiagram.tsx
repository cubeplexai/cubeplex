'use client'

import { useEffect, useId, useState } from 'react'
import { useTheme } from 'next-themes'

// Mermaid configuration is global, so initialize and render each diagram together.
let renderQueue = Promise.resolve()

export function MermaidDiagram({ source }: { source: string }) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const { resolvedTheme } = useTheme()
  const theme = resolvedTheme?.endsWith('dark') ? 'dark' : 'default'
  const [result, setResult] = useState<{ source: string; theme: string; svg: string } | null>(null)

  useEffect(() => {
    let cancelled = false
    renderQueue = renderQueue.then(async () => {
      if (cancelled) return
      let container: HTMLDivElement | undefined
      try {
        const { default: mermaid } = await import('mermaid')
        if (cancelled) return
        container = document.createElement('div')
        container.style.position = 'absolute'
        container.style.left = '-100000px'
        document.body.appendChild(container)
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          suppressErrorRendering: true,
          theme,
          fontFamily: 'var(--font-geist-sans), sans-serif',
        })
        const { svg } = await mermaid.render(`mermaid-${id}`, source, container)
        if (!cancelled) setResult({ source, theme, svg })
      } catch {
        // Incomplete streaming output and invalid syntax remain readable as source.
        if (!cancelled) setResult(null)
      } finally {
        container?.remove()
      }
    })
    return () => {
      cancelled = true
    }
  }, [id, source, theme])

  return (
    <div data-mermaid-source={source} className="my-4 max-w-full overflow-x-auto">
      {result?.source === source && result.theme === theme ? (
        <div
          className="not-prose [&_svg]:mx-auto"
          dangerouslySetInnerHTML={{ __html: result.svg }}
        />
      ) : (
        <pre>
          <code className="language-mermaid">{source}</code>
        </pre>
      )}
    </div>
  )
}
