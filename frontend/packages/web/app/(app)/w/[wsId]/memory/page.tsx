'use client'

import { use } from 'react'
import { useTranslations } from 'next-intl'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { MemoryList } from './components/MemoryList'

interface MemoryPageProps {
  params: Promise<{ wsId: string }>
}

export default function MemoryPage({ params }: MemoryPageProps) {
  const { wsId } = use(params)
  const t = useTranslations('memoryPage')

  return (
    <div className="op-page op-page--narrow">
      <div className="op-page__head">
        <div className="flex flex-col gap-1">
          <p className="op-eyebrow">{t('eyebrow')}</p>
          <h1>{t('title')}</h1>
        </div>
        <span className="op-meta">{t('scopedBy')}</span>
      </div>
      <p className="op-page__lede">{t('lede')}</p>

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
  )
}
