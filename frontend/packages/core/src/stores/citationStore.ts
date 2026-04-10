import { create } from 'zustand'
import type { CitationData } from '../types'

export interface CitationStore {
  /** conversationId → citationId → CitationData */
  citations: Record<string, Record<number, CitationData>>

  addCitation: (conversationId: string, data: CitationData) => void
  loadCitations: (conversationId: string, citations: CitationData[]) => void
  getCitation: (conversationId: string, citationId: number) => CitationData | undefined
  clearConversation: (conversationId: string) => void
}

export const useCitationStore = create<CitationStore>((set, get) => ({
  citations: {},

  addCitation(conversationId, data) {
    set((s) => ({
      citations: {
        ...s.citations,
        [conversationId]: {
          ...s.citations[conversationId],
          [data.citation_id]: data,
        },
      },
    }))
  },

  loadCitations(conversationId, citations) {
    const map: Record<number, CitationData> = {}
    for (const c of citations) {
      map[c.citation_id] = c
    }
    set((s) => ({
      citations: {
        ...s.citations,
        [conversationId]: {
          ...s.citations[conversationId],
          ...map,
        },
      },
    }))
  },

  getCitation(conversationId, citationId) {
    return get().citations[conversationId]?.[citationId]
  },

  clearConversation(conversationId) {
    set((s) => {
      const { [conversationId]: _, ...rest } = s.citations
      return { citations: rest }
    })
  },
}))
