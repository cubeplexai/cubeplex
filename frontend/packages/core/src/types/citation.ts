export interface CitationChunk {
  chunk_index: number
  content: string
}

export interface CitationMetadata {
  source_type: string
  url?: string
  title?: string
  domain?: string
  published_at?: string
}

export interface CitationData {
  citation_id: number
  chunks: CitationChunk[]
  metadata: CitationMetadata
  tool_call_id: string
}
