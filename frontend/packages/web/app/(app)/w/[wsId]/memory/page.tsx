'use client'

import { use } from 'react'
import { useSearchParams, useRouter, usePathname } from 'next/navigation'
import { Brain, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { MemoryList } from './components/MemoryList'

interface MemoryPageProps {
  params: Promise<{ wsId: string }>
}

export default function MemoryPage({ params }: MemoryPageProps) {
  const { wsId } = use(params)
  const searchParams = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const conversationFilter = searchParams.get('conversation') ?? undefined

  const clearFilter = () => {
    router.replace(pathname)
  }

  return (
    // overflow-y-auto: parent (app shell) clips with overflow-hidden; this page
    // owns its scroll, matching the pattern used by skills/page.tsx.
    <div className="flex-1 overflow-y-auto">
      <div className="flex flex-col gap-6 px-6 py-6 max-w-3xl">
        {/* Page header */}
        <div className="flex items-center gap-3">
          <div className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Brain className="size-4.5" />
          </div>
          <div>
            <h1 className="text-xl font-semibold leading-tight">Memory</h1>
            <p className="text-sm text-muted-foreground">
              What the agent remembers about you and your workspace
            </p>
          </div>
        </div>

        {conversationFilter && (
          <div
            className="flex items-center gap-2 rounded-lg border border-border bg-muted/40
              px-3 py-2 text-sm text-muted-foreground"
          >
            <span>Filtered to memories from a single conversation</span>
            <Button variant="ghost" size="sm" className="h-7 ml-auto gap-1" onClick={clearFilter}>
              <X className="size-3.5" />
              Clear
            </Button>
          </div>
        )}

        {/* Tabs */}
        <Tabs defaultValue="personal">
          <TabsList variant="line" className="w-fit">
            <TabsTrigger value="personal">Personal</TabsTrigger>
            <TabsTrigger value="workspace">Workspace</TabsTrigger>
            <TabsTrigger value="org">Organization</TabsTrigger>
            <TabsTrigger value="archived">Archived</TabsTrigger>
          </TabsList>

          <TabsContent value="personal" className="mt-4">
            <MemoryList
              wsId={wsId}
              scope="personal"
              status="active"
              sourceConversationId={conversationFilter}
            />
          </TabsContent>

          <TabsContent value="workspace" className="mt-4">
            <MemoryList
              wsId={wsId}
              scope="workspace"
              status="active"
              sourceConversationId={conversationFilter}
            />
          </TabsContent>

          <TabsContent value="org" className="mt-4">
            <MemoryList
              wsId={wsId}
              scope="org"
              status="active"
              sourceConversationId={conversationFilter}
            />
          </TabsContent>

          <TabsContent value="archived" className="mt-4">
            <MemoryList wsId={wsId} status="archived" sourceConversationId={conversationFilter} />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
