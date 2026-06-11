'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { createApiClient, searchConversations, type SearchResult } from '@cubebox/core'

export interface SearchState {
  loading: boolean
  error: string | null
  results: SearchResult[]
}

const DEBOUNCE_MS = 250

export function useConversationSearch(query: string, wsId: string | null): SearchState {
  const [state, setState] = useState<SearchState>({ loading: false, error: null, results: [] })
  // Stale-response counter: the only response we render is the most recent
  // one. ApiClient.get has no signal parameter, so we can't actually abort
  // the fetch — instead we tag each request and ignore replies for older
  // tags.
  const requestIdRef = useRef(0)

  const client = useMemo(() => {
    const c = createApiClient('')
    if (wsId) c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    if (!wsId) {
      setState({ loading: false, error: null, results: [] })
      return
    }
    const q = query.trim()
    if (q.length === 0) {
      setState({ loading: false, error: null, results: [] })
      return
    }
    const handle = window.setTimeout(() => {
      const myId = ++requestIdRef.current
      setState((s) => ({ ...s, loading: true, error: null }))
      searchConversations(client, q, 8)
        .then((resp) => {
          if (myId !== requestIdRef.current) return
          setState({ loading: false, error: null, results: resp.results })
        })
        .catch(() => {
          if (myId !== requestIdRef.current) return
          setState({ loading: false, error: 'search-failed', results: [] })
        })
    }, DEBOUNCE_MS)
    return () => {
      window.clearTimeout(handle)
      // Bump the id so any in-flight reply is discarded.
      requestIdRef.current += 1
    }
  }, [query, wsId, client])

  return state
}
