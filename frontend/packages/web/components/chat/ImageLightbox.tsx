'use client'

import { useEffect } from 'react'
import { X } from 'lucide-react'

interface Props {
  src: string
  alt: string
  onClose: () => void
}

export function ImageLightbox({ src, alt, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/80 p-6" onClick={onClose}>
      <button
        type="button"
        aria-label="Close"
        className="absolute top-4 right-4 grid size-9 place-items-center rounded-full bg-background/30 text-white hover:bg-background/50"
        onClick={onClose}
      >
        <X className="size-5" />
      </button>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt={alt} className="max-h-full max-w-full rounded-lg shadow-2xl" />
    </div>
  )
}
