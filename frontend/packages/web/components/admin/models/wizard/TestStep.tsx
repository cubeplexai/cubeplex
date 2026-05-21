'use client'

import type { ApiClient } from '@cubebox/core'

interface TestStepProps {
  client: ApiClient
  providerId: string
  modelDbIds: string[]
  onFinish: () => void
}

export function TestStep(_props: TestStepProps) {
  return null
}
