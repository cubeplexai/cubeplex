'use client'

import { useEffect, useRef, useState } from 'react'

// Render the HTML at a fixed desktop width, then scale the whole iframe down to
// the card's media box so the thumbnail reads like a real page miniature rather
// than a cramped mobile viewport. 1280×720 keeps the logical canvas at 16:9,
// matching the box, so the scaled result fills it exactly.
const BASE_WIDTH = 1280
const BASE_HEIGHT = BASE_WIDTH * (9 / 16)

interface ArtifactHtmlThumbProps {
  src: string
  title: string
}

export function ArtifactHtmlThumb({ src, title }: ArtifactHtmlThumbProps): React.ReactElement {
  const containerRef = useRef<HTMLDivElement>(null)
  const [mounted, setMounted] = useState(false)
  const [scale, setScale] = useState(BASE_WIDTH > 0 ? 320 / BASE_WIDTH : 0.25)

  // Lazy-mount the iframe only once the card scrolls near the viewport, so a
  // grid of HTML artifacts doesn't spin up dozens of live iframes on first paint.
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setMounted(true)
          io.disconnect()
        }
      },
      { rootMargin: '200px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [])

  // Track the box width so the scale factor stays correct across breakpoints.
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? el.clientWidth
      if (w > 0) setScale(w / BASE_WIDTH)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  return (
    <div ref={containerRef} className="absolute inset-0 overflow-hidden">
      {mounted && (
        <iframe
          src={src}
          title={title}
          // No allow-same-origin: agent-authored HTML must not run with
          // same-origin access to the session cookie / authenticated requests.
          // The initial document GET still carries cookies, so the thumbnail
          // renders; only the embedded scripts are confined to an opaque origin.
          sandbox="allow-scripts"
          tabIndex={-1}
          aria-hidden
          loading="lazy"
          className="pointer-events-none origin-top-left border-0 bg-white"
          style={{
            width: BASE_WIDTH,
            height: BASE_HEIGHT,
            transform: `scale(${scale})`,
          }}
        />
      )}
    </div>
  )
}
