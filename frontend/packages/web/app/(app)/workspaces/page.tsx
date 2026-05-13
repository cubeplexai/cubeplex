'use client'

import { useTranslations } from 'next-intl'

import { WorkspaceList } from '@/components/workspace/WorkspaceList'
import { WorkspaceCreateForm } from '@/components/workspace/WorkspaceCreateForm'

export default function WorkspacesPage() {
  const t = useTranslations('workspacesPage')
  return (
    <div className="op-page op-page--narrow">
      <div className="op-page__head">
        <div className="flex flex-col gap-1">
          <p className="op-eyebrow">{t('eyebrow')}</p>
          <h1>{t('title')}</h1>
        </div>
      </div>
      <p className="op-page__lede">{t('lede')}</p>
      <WorkspaceList />
      <WorkspaceCreateForm />
    </div>
  )
}
