'use client'

import type { ApiClient, ProviderPreset } from '@cubebox/core'

interface PresetPickerProps {
  client: ApiClient
  selectedSlug: string | null
  onPick: (preset: ProviderPreset) => void
}

export function PresetPicker(_props: PresetPickerProps) {
  return null
}
