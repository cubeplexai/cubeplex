import { use } from 'react'
import { TriggerDetailPanel } from '@/components/triggers/TriggerDetailPanel'

interface TriggerDetailPageProps {
  params: Promise<{ wsId: string; id: string }>
}

export default function TriggerDetailPage({ params }: TriggerDetailPageProps): React.ReactElement {
  const { wsId, id } = use(params)

  return (
    <div className="flex flex-1 overflow-hidden h-full">
      <TriggerDetailPanel wsId={wsId} triggerId={id} />
    </div>
  )
}
