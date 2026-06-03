import type { MemoryItem, MemoryScope, MemoryStatus, MemoryType } from '../types/memory'
import { toApiError, type ApiClient } from './client'

export interface ListMemoryOptions {
  scope?: MemoryScope
  type?: MemoryType
  status?: MemoryStatus
  q?: string
  source_conversation_id?: string
}

export interface CountMemoryOptions {
  scope?: MemoryScope
  status?: MemoryStatus
  source_conversation_id?: string
}

export interface CreateMemoryBody {
  scope: MemoryScope
  type: MemoryType
  content: string
  confidence?: number
}

export interface UpdateMemoryBody {
  content?: string
  type?: MemoryType
  confidence?: number
  status?: MemoryStatus
}

export async function listMemory(
  client: ApiClient,
  opts: ListMemoryOptions = {},
): Promise<MemoryItem[]> {
  const params = new URLSearchParams()
  if (opts.scope) params.set('scope', opts.scope)
  if (opts.type) params.set('type', opts.type)
  if (opts.status) params.set('status', opts.status)
  if (opts.q) params.set('q', opts.q)
  if (opts.source_conversation_id) params.set('source_conversation_id', opts.source_conversation_id)
  const qs = params.toString()
  const url = qs ? `/api/v1/memory?${qs}` : '/api/v1/memory'
  const res = await client.get(url)
  if (!res.ok) throw await toApiError(res)
  const data = (await res.json()) as { items: MemoryItem[] }
  return data.items
}

export async function getMemoryCount(
  client: ApiClient,
  opts: CountMemoryOptions = {},
): Promise<number> {
  const params = new URLSearchParams()
  if (opts.scope) params.set('scope', opts.scope)
  if (opts.status) params.set('status', opts.status)
  if (opts.source_conversation_id) params.set('source_conversation_id', opts.source_conversation_id)
  const qs = params.toString()
  const url = qs ? `/api/v1/memory/count?${qs}` : '/api/v1/memory/count'
  const res = await client.get(url)
  if (!res.ok) throw await toApiError(res)
  const data = (await res.json()) as { count: number }
  return data.count
}

export async function createMemory(client: ApiClient, body: CreateMemoryBody): Promise<MemoryItem> {
  const res = await client.post('/api/v1/memory', body)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<MemoryItem>
}

export async function updateMemory(
  client: ApiClient,
  id: string,
  body: UpdateMemoryBody,
): Promise<MemoryItem> {
  const res = await client.patch(`/api/v1/memory/${id}`, body)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<MemoryItem>
}

export async function archiveMemory(client: ApiClient, id: string): Promise<void> {
  const res = await client.del(`/api/v1/memory/${id}`)
  if (!res.ok) throw await toApiError(res)
}
