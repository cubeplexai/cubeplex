'use client'

import { use, useEffect } from 'react'
import { Sparkles } from 'lucide-react'
import { DiscoverPanel } from '@/components/skills/DiscoverPanel'
import { SkillsList } from '@/components/skills/SkillsList'

interface SkillsPageProps {
  params: Promise<{ wsId: string }>
}

export default function WorkspaceSkillsPage({ params }: SkillsPageProps) {
  const { wsId } = use(params)

  useEffect(() => {
    document.title = 'Skills'
  }, [])

  return (
    <div className="flex flex-col gap-6 px-6 py-6 max-w-3xl">
      {/* Page header */}
      <div className="flex items-center gap-3">
        <div className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Sparkles className="size-4.5" />
        </div>
        <div>
          <h1 className="text-xl font-semibold leading-tight">Skills</h1>
          <p className="text-sm text-muted-foreground">
            Discover and install skills for your workspace
          </p>
        </div>
      </div>

      <DiscoverPanel wsId={wsId} />
      <SkillsList wsId={wsId} />
    </div>
  )
}
