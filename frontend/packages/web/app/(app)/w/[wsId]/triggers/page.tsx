import { use } from 'react'
import { TriggersPanel } from '@/components/triggers/TriggersPanel'

interface TriggersPageProps {
  params: Promise<{ wsId: string }>
}

export default function TriggersPage({ params }: TriggersPageProps): React.ReactElement {
  const { wsId } = use(params)

  return (
    <div className="flex flex-1 overflow-hidden h-full">
      <TriggersPanel wsId={wsId} />
    </div>
  )
}
