'use client'

import { use } from 'react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { MemoryList } from './components/MemoryList'

interface MemoryPageProps {
  params: Promise<{ wsId: string }>
}

export default function MemoryPage({ params }: MemoryPageProps) {
  const { wsId } = use(params)

  return (
    <div className="op-page op-page--narrow">
      <div className="op-page__head">
        <div className="flex flex-col gap-1">
          <p className="op-eyebrow">workspace · memory</p>
          <h1>Memory</h1>
        </div>
        <span className="op-meta">scoped by personal / workspace / org</span>
      </div>
      <p className="op-page__lede">
        What the agent remembers between conversations. Items are scoped — personal stays with you
        across workspaces, workspace items follow the project, org items are shared with every
        member.
      </p>

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
