import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import {
  listTriggers,
  createTrigger,
  updateTrigger,
  deleteTrigger,
  rotateSecret as apiRotateSecret,
  listTriggerEvents,
  replayEvent as apiReplayEvent,
  type Trigger,
  type TriggerEvent,
  type CreateTriggerBody,
  type UpdateTriggerBody,
  type RotateSecretBody,
  type RotateSecretResult,
  type ListTriggerEventsQuery,
  type TriggerListFilters,
} from '../api/triggers'

export interface TriggerStore {
  triggers: Trigger[]
  loading: boolean
  selectedId: string | null
  eventsByTrigger: Record<string, TriggerEvent[]>
  eventsLoading: boolean

  load(client: ApiClient, wsId: string, filters?: TriggerListFilters): Promise<void>
  create(client: ApiClient, wsId: string, body: CreateTriggerBody): Promise<Trigger>
  update(client: ApiClient, wsId: string, id: string, patch: UpdateTriggerBody): Promise<Trigger>
  remove(client: ApiClient, wsId: string, id: string): Promise<void>
  rotate(
    client: ApiClient,
    wsId: string,
    id: string,
    body: RotateSecretBody,
  ): Promise<RotateSecretResult>
  loadEvents(
    client: ApiClient,
    wsId: string,
    id: string,
    query?: ListTriggerEventsQuery,
  ): Promise<void>
  replay(client: ApiClient, wsId: string, id: string, eventId: string): Promise<void>
  reset(): void
}

export const useTriggerStore = create<TriggerStore>((set, get) => ({
  triggers: [],
  loading: false,
  selectedId: null,
  eventsByTrigger: {},
  eventsLoading: false,

  async load(client, wsId, filters) {
    set({ loading: true })
    try {
      const triggers = await listTriggers(client, wsId, filters)
      set({ triggers })
    } finally {
      set({ loading: false })
    }
  },

  async create(client, wsId, body) {
    const trigger = await createTrigger(client, wsId, body)
    set((s) => ({ triggers: [trigger, ...s.triggers] }))
    return trigger
  },

  async update(client, wsId, id, patch) {
    const trigger = await updateTrigger(client, wsId, id, patch)
    set((s) => ({
      triggers: s.triggers.map((t) => (t.id === id ? trigger : t)),
    }))
    return trigger
  },

  async remove(client, wsId, id) {
    await deleteTrigger(client, wsId, id)
    set((s) => ({
      triggers: s.triggers.filter((t) => t.id !== id),
      eventsByTrigger: Object.fromEntries(
        Object.entries(s.eventsByTrigger).filter(([k]) => k !== id),
      ),
    }))
  },

  async rotate(client, wsId, id, body) {
    const result = await apiRotateSecret(client, wsId, id, body)
    // Refresh the trigger row so rotation fields are up-to-date
    await get().load(client, wsId)
    return result
  },

  async loadEvents(client, wsId, id, query) {
    set({ eventsLoading: true })
    try {
      const events = await listTriggerEvents(client, wsId, id, query)
      set((s) => ({ eventsByTrigger: { ...s.eventsByTrigger, [id]: events } }))
    } finally {
      set({ eventsLoading: false })
    }
  },

  async replay(client, wsId, id, eventId) {
    await apiReplayEvent(client, wsId, id, eventId)
    await get().loadEvents(client, wsId, id)
  },

  reset() {
    set({
      triggers: [],
      loading: false,
      selectedId: null,
      eventsByTrigger: {},
      eventsLoading: false,
    })
  },
}))
