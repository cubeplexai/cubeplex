import type {
  DiscoveredModel,
  FieldEvidence,
  ImportModelSelection,
  ModelField,
} from '@cubeplex/core'

const accepted = new Set(['declared', 'confirmed', 'existing'])

function catalogSource(source: string): boolean {
  return source.startsWith('catalog:') || source.startsWith('models.dev:')
}

export function listingCatalogDefault(evidence: FieldEvidence | undefined) {
  if (!evidence || evidence.state !== 'conflict') return undefined
  const listing = evidence.alternatives.filter(
    (item) => item.source === 'listing' && item.value != null,
  )
  const others = evidence.alternatives.filter((item) => item.source !== 'listing')
  const catalogOnly = others.length > 0 && others.every((item) => catalogSource(item.source))
  if (listing.length !== 1 || !catalogOnly) return undefined
  return listing[0]
}

export function canSelectDiscoveredModel(model: DiscoveredModel): boolean {
  const purpose = model.fields.purpose
  const declarations = purpose ? [purpose, ...purpose.alternatives] : []
  return (
    !model.existing_model_db_id &&
    model.model_id.length > 0 &&
    model.model_id.length <= 128 &&
    model.display_name.length > 0 &&
    model.display_name.length <= 128 &&
    !['embedding', 'image', 'audio', 'video'].includes(model.purpose) &&
    !declarations.some(
      (item) =>
        accepted.has(item.state) &&
        ['embedding', 'image', 'audio', 'video'].includes(String(item.value)),
    )
  )
}

const reportedFields = new Set<string>([
  'purpose',
  'context_window',
  'max_tokens',
  'input_modalities',
  'output_modalities',
  'reasoning',
  'reasoning_levels',
  'capability',
  'cost_input',
  'cost_output',
  'cost_cache_read',
  'cost_cache_write',
])

export function submittedDiscoverySelection(
  model: DiscoveredModel,
  selection?: ImportModelSelection,
): ImportModelSelection {
  const resolutions: Partial<Record<ModelField, string>> = {}
  for (const [field, source] of Object.entries(selection?.resolutions ?? {}) as [
    ModelField,
    string,
  ][]) {
    if (source) resolutions[field] = source
  }
  for (const [field, evidence] of Object.entries(model.fields) as [
    ModelField,
    (typeof model.fields)[ModelField],
  ][]) {
    const source = listingCatalogDefault(evidence)?.source
    if (source && !resolutions[field]) resolutions[field] = source
  }
  return { ...selection, model_id: model.model_id, resolutions }
}

export function incompleteImportFields(error: { message: string; detail: unknown }): {
  modelId?: string
  fields: ModelField[]
} {
  const detail = error.detail
  if (detail && typeof detail === 'object') {
    const record = detail as { model_id?: unknown; missing_fields?: unknown }
    const fields = knownFields(record.missing_fields)
    if (fields.length > 0) {
      return {
        modelId: typeof record.model_id === 'string' ? record.model_id : undefined,
        fields,
      }
    }
  }
  const match = /Complete required fields for ([^:]+): ([^.]+)\./.exec(error.message)
  if (!match) return { fields: [] }
  return {
    modelId: match[1].trim(),
    fields: knownFields(match[2].split(',').map((item) => item.trim())),
  }
}

function knownFields(value: unknown): ModelField[] {
  if (!Array.isArray(value)) return []
  return value.filter(
    (item): item is ModelField => typeof item === 'string' && reportedFields.has(item),
  )
}

export function reviewDiscoverySelection(model: DiscoveredModel, selection?: ImportModelSelection) {
  const submitted = submittedDiscoverySelection(model, selection)
  const value = (field: ModelField): unknown => {
    const override =
      submitted.overrides?.[field as keyof NonNullable<ImportModelSelection['overrides']>]
    if (override != null) return override
    const evidence = model.fields[field]
    if (!evidence) return undefined
    const source = submitted.resolutions?.[field]
    if (evidence.state === 'conflict') {
      if (!source) return listingCatalogDefault(evidence)?.value
      const matches = evidence.alternatives.filter((item) => item.source === source)
      return matches.length === 1 ? matches[0].value : undefined
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
