'use client'

import { useTranslations } from 'next-intl'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { MemoryList } from '@/app/(app)/w/[wsId]/memory/components/MemoryList'

interface MemoryPanelProps {
  wsId: string
}

export function MemoryPanel({ wsId }: MemoryPanelProps): React.ReactElement {
  const t = useTranslations('wsSettings.memory')
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="flex flex-col gap-6 px-6 py-6 max-w-3xl">
        <Tabs defaultValue="personal">
          <TabsList variant="line" className="w-fit">
            <TabsTrigger value="personal">{t('tabPersonal')}</TabsTrigger>
            <TabsTrigger value="workspace">{t('tabWorkspace')}</TabsTrigger>
            <TabsTrigger value="org">{t('tabOrg')}</TabsTrigger>
            <TabsTrigger value="archived">{t('tabArchived')}</TabsTrigger>
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
    </div>
  )
}
