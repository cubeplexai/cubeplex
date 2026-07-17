'use client'

import { Avatar } from '@/components/ui/avatar-resolved'

interface AgentAvatarProps {
  seed: string
  size?: number
  className?: string
}

function nearestPreset(size: number): 'sm' | 'default' | 'lg' {
  if (size <= 24) return 'sm'
  if (size <= 32) return 'default'
  return 'lg'
}

export function AgentAvatar({ seed, size = 32, className }: AgentAvatarProps) {
  return (
    <Avatar
      style="bottts"
      seed={seed}
      size={size ? nearestPreset(size) : undefined}
      className={className}
    />
  )
}
