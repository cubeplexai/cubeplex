'use client'

import { useMemo } from 'react'
import { createAvatar } from '@dicebear/core'
import { bottts } from '@dicebear/collection'

interface AgentAvatarProps {
  seed: string
  size?: number
  className?: string
}

export function AgentAvatar({ seed, size = 32, className }: AgentAvatarProps) {
  const svgDataUri = useMemo(() => {
    const avatar = createAvatar(bottts, {
      seed,
      size,
    })
    return avatar.toDataUri()
  }, [seed, size])

  return (
    <img
      src={svgDataUri}
      alt=""
      width={size}
      height={size}
      className={className}
    />
  )
}
