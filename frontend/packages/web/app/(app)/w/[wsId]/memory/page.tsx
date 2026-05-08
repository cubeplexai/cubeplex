'use client'

import { use } from 'react'
import { Brain } from 'lucide-react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { MemoryList } from './components/MemoryList'

interface MemoryPageProps {
  params: Promise<{ wsId: string }>
}

export default function MemoryPage({ params }: MemoryPageProps) {
  const { wsId } = use(params)

  return (
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

      {/* Tabs */}
      <Tabs defaultValue="personal">
        <TabsList variant="line" className="w-fit">
          <TabsTrigger value="personal">Personal</TabsTrigger>
          <TabsTrigger value="workspace">Workspace</TabsTrigger>
          <TabsTrigger value="org">Organization</TabsTrigger>
          <TabsTrigger value="archived">Archived</TabsTrigger>
        </TabsList>

        <TabsContent value="personal" className="mt-4">
          <MemoryList wsId={wsId} scope="personal" status="active" />
        </TabsContent>

        <TabsContent value="workspace" className="mt-4">
          <MemoryList wsId={wsId} scope="workspace" status="active" />
        </TabsContent>

        <TabsContent value="org" className="mt-4">
          <MemoryList wsId={wsId} scope="org" status="active" />
        </TabsContent>

        <TabsContent value="archived" className="mt-4">
          <MemoryList wsId={wsId} status="archived" />
        </TabsContent>
      </Tabs>
    </div>
  )
}
