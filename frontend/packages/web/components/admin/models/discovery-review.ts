import type { DiscoveredModel, ImportModelSelection, ModelField } from '@cubeplex/core'

const accepted = new Set(['declared', 'confirmed', 'existing'])

export function canSelectDiscoveredModel(model: DiscoveredModel): boolean {
  const purpose = model.fields.purpose
  const declarations = purpose ? [purpose, ...purpose.alternatives] : []
  return (
    !model.existing_model_db_id &&
    model.model_id.length > 0 &&
    model.model_id.length <= 128 &&
    model.display_name.length > 0 &&
    model.display_name.length <= 128 &&
    !['embedding', 'image', 'audio'].includes(model.purpose) &&
    !declarations.some(
      (item) =>
        accepted.has(item.state) && ['embedding', 'image', 'audio'].includes(String(item.value)),
    )
  )
}

export function submittedDiscoverySelection(
  modelId: string,
  selection?: ImportModelSelection,
): ImportModelSelection {
  const resolutions = Object.fromEntries(
    Object.entries(selection?.resolutions ?? {}).filter((entry): entry is [ModelField, string] =>
      Boolean(entry[1]),
    ),
  )
  return { ...selection, model_id: modelId, resolutions }
}

export function reviewDiscoverySelection(model: DiscoveredModel, selection?: ImportModelSelection) {
  const submitted = submittedDiscoverySelection(model.model_id, selection)
  const value = (field: ModelField): unknown => {
    const override =
      submitted.overrides?.[field as keyof NonNullable<ImportModelSelection['overrides']>]
    if (override != null) return override
    const evidence = model.fields[field]
    if (!evidence) return undefined
    const source = submitted.resolutions?.[field]
    if (evidence.state === 'conflict') {
      const matches = evidence.alternatives.filter((item) => item.source === source)
      return source && matches.length === 1 ? matches[0].value : undefined
    }
    if (accepted.has(evidence.state) || submitted.confirm_fields?.includes(field))
      return evidence.value
    return undefined
  }
  const missing: ModelField[] = []
  const reject = (field: ModelField) => {
    if (!missing.includes(field)) missing.push(field)
  }
  if (!canSelectDiscoveredModel(model)) reject('purpose')
  for (const [field, source] of Object.entries(submitted.resolutions ?? {}) as [
    ModelField,
    string,
  ][]) {
    const evidence = model.fields[field]
    const matches =
      evidence?.state === 'conflict'
        ? evidence.alternatives.filter((item) => item.source === source && item.value != null)
        : []
    if (matches.length !== 1) reject(field)
  }
  for (const field of submitted.confirm_fields ?? []) {
    const evidence = model.fields[field]
    if (!evidence || evidence.value == null || evidence.state === 'conflict') reject(field)
  }
  if (value('purpose') !== 'chat') reject('purpose')
  const context = value('context_window')
  const output = value('max_tokens')
  const positiveInteger = (v: unknown): v is number =>
    typeof v === 'number' && Number.isSafeInteger(v) && v > 0
  if (!positiveInteger(context)) reject('context_window')
  if (!positiveInteger(output) || (positiveInteger(context) && output > context))
    reject('max_tokens')
  const inputs = value('input_modalities')
  if (!Array.isArray(inputs) || !inputs.includes('text')) reject('input_modalities')
  for (const field of [
    'cost_input',
    'cost_output',
    'cost_cache_read',
    'cost_cache_write',
  ] as const) {
    const price = submitted.overrides?.[field]
    if (price != null && (!Number.isFinite(price) || price < 0)) reject(field)
  }
  return { missing, ready: missing.length === 0, value }
}
