import type { Model } from '@cubeplex/core'

export type PriceField = 'cost_input' | 'cost_output' | 'cost_cache_read' | 'cost_cache_write'

export function configuredPrice(model: Model, field: PriceField): number | null {
  const evidence = model.configuration_metadata?.fields?.[field]
  if (
    model[field] > 0 ||
    (evidence?.value === 0 && ['declared', 'confirmed', 'existing'].includes(evidence.state))
  )
    return model[field]
  return null
}
