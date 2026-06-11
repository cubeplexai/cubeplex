import { toApiError, type ApiClient } from './client'

export interface SearchResult {
  conversation_id: string
  title: string
  snippet: string
  match_offsets: [number, number][]
  matched_message_seq: number | null
  matched_at: string | null
  score: number
}

export interface SearchResponse {
  results: SearchResult[]
  lexical_count: number
  vector_count: number
  fused_count: number
}

export async function searchConversations(
  client: ApiClient,
  q: string,
  limit = 8,
): Promise<SearchResponse> {
  // ApiClient.get returns Promise<Response>; it auto-injects the `/ws/{id}/`
  // segment via injectWorkspace, so we pass the scoped suffix only.
  const path = `/api/v1/conversations/search?q=${encodeURIComponent(q)}&limit=${limit}`
  const res = await client.get(path)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SearchResponse
}
