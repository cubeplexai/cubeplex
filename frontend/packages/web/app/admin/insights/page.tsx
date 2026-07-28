'use client'

import { EEGate } from '@/components/admin/EEGate'
import { InsightsShell } from '@/components/admin/insights/InsightsShell'

export default function InsightsPage() {
  return (
    <EEGate>
      <InsightsShell />
    </EEGate>
  )
}
