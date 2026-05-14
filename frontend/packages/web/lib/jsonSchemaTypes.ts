export type SchemaNode = Record<string, unknown>

export interface ResolvedType {
  label: string
  kind: 'string' | 'number' | 'integer' | 'boolean' | 'object' | 'array' | 'any'
}

export function resolveType(node: SchemaNode): ResolvedType {
  const t = node.type
  if (t === 'string') return { label: 'string', kind: 'string' }
  if (t === 'integer') return { label: 'integer', kind: 'integer' }
  if (t === 'number') return { label: 'number', kind: 'number' }
  if (t === 'boolean') return { label: 'boolean', kind: 'boolean' }
  if (t === 'object') return { label: 'object', kind: 'object' }
  if (t === 'array') {
    const items = (node.items ?? {}) as SchemaNode
    const inner = resolveType(items)
    return { label: `array<${inner.label}>`, kind: 'array' }
  }
  if (Array.isArray(t) && t.length > 0) {
    const first = resolveType({ type: t[0] } as SchemaNode)
    return { label: t.join(' | '), kind: first.kind }
  }
  return { label: 'any', kind: 'any' }
}

const KIND_TO_CHIP: Record<ResolvedType['kind'], string> = {
  string: 'bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300',
  number: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300',
  integer: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300',
  boolean: 'bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300',
  object: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-300',
  array: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300',
  any: 'bg-muted text-muted-foreground',
}

export function typeChipClasses(kind: ResolvedType['kind']): string {
  return KIND_TO_CHIP[kind]
}

export function resolveRef(root: SchemaNode, ref: string): SchemaNode | null {
  // Supports "#/definitions/Foo" and "#/$defs/Foo"
  if (!ref.startsWith('#/')) return null
  const parts = ref.slice(2).split('/')
  let node: unknown = root
  for (const p of parts) {
    if (node && typeof node === 'object' && p in (node as Record<string, unknown>)) {
      node = (node as Record<string, unknown>)[p]
    } else {
      return null
    }
  }
  return (node as SchemaNode) ?? null
}

export function isObjectSchema(node: SchemaNode): boolean {
  return node.type === 'object' || typeof node.properties === 'object'
}

export function getProperties(node: SchemaNode): Record<string, SchemaNode> {
  const props = node.properties
  return (props && typeof props === 'object' ? props : {}) as Record<string, SchemaNode>
}

export function getRequired(node: SchemaNode): string[] {
  return Array.isArray(node.required) ? (node.required as string[]) : []
}
