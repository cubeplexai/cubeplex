import { create } from 'zustand'
import type { Topic, TopicParticipant } from '../types'
import type { ApiClient } from '../api'
import {
  listTopics,
  getTopic,
  createTopic,
  deleteTopic,
  addTopicParticipants,
  removeTopicParticipant,
  updateParticipantRole as apiUpdateParticipantRole,
  upgradeToTopic,
  createTopicConversation,
  setTopicPin,
} from '../api'

export interface TopicWithParticipants {
  topic: Topic
  participants: TopicParticipant[]
}

export interface TopicStore {
  topics: Topic[]
  topicParticipants: Record<string, TopicParticipant[]>
  isLoading: boolean
  error: string | null
  fetchList(client: ApiClient): Promise<void>
  fetchDetail(client: ApiClient, topicId: string): Promise<TopicWithParticipants | null>
  create(
    client: ApiClient,
    body: { title: string; sandbox_mode?: string; member_user_ids?: string[] },
  ): Promise<{ topicId: string; conversationId: string }>
  remove(client: ApiClient, topicId: string): Promise<void>
  setPin(client: ApiClient, topicId: string, isPinned: boolean): Promise<void>
  addMembers(client: ApiClient, topicId: string, userIds: string[]): Promise<void>
  removeMember(client: ApiClient, topicId: string, userId: string): Promise<void>
  updateParticipantRole(
    client: ApiClient,
    topicId: string,
    userId: string,
    role: 'owner' | 'member',
  ): Promise<void>
  upgradeConversationToTopic(
    client: ApiClient,
    conversationId: string,
    body: { title: string; sandbox_mode?: string; member_user_ids?: string[] },
  ): Promise<{ topicId: string; conversationId: string }>
  createConversation(
    client: ApiClient,
    topicId: string,
    title?: string,
  ): Promise<{ conversationId: string }>
}

export const useTopicStore = create<TopicStore>((set) => ({
  topics: [],
  topicParticipants: {},
  isLoading: false,
  error: null,

  async fetchList(client: ApiClient) {
    set({ isLoading: true, error: null })
    try {
      const { items } = await listTopics(client)
      // The list endpoint embeds participants per topic so the sidebar can
      // render avatars on first paint without an N+1 detail fetch.
      const nextParticipants: Record<string, TopicParticipant[]> = {}
      for (const t of items) {
        const ps = (t as Topic & { participants?: TopicParticipant[] }).participants
        if (Array.isArray(ps)) nextParticipants[t.id] = ps
      }
      set((s) => ({
        topics: items,
        isLoading: false,
        topicParticipants: { ...s.topicParticipants, ...nextParticipants },
      }))
    } catch (e) {
      set({ error: String(e), isLoading: false })
    }
  },

  async fetchDetail(client: ApiClient, topicId: string) {
    try {
      const data = await getTopic(client, topicId)
      set((s) => ({
        topicParticipants: {
          ...s.topicParticipants,
          [topicId]: data.participants,
        },
      }))
      return { topic: data.topic, participants: data.participants }
    } catch {
      return null
    }
  },

  async create(client, body) {
    const data = await createTopic(client, body)
    set((s) => ({
      topics: [data.topic, ...s.topics],
      // Seed participants so the sidebar shows the creator's avatar
      // immediately (response includes the owner row). Without this the
      // topic row reads "0 members" until something triggers fetchDetail.
      topicParticipants: {
        ...s.topicParticipants,
        [data.topic.id]: data.participants,
      },
    }))
    return {
      topicId: data.topic.id,
      conversationId: data.conversation.id,
    }
  },

  async upgradeConversationToTopic(client, conversationId, body) {
    const data = await upgradeToTopic(client, conversationId, body)
    set((s) => ({
      topics: [data.topic, ...s.topics.filter((t) => t.id !== data.topic.id)],
      topicParticipants: {
        ...s.topicParticipants,
        [data.topic.id]: data.participants,
      },
    }))
    return {
      topicId: data.topic.id,
      conversationId: data.conversation.id,
    }
  },

  async remove(client, topicId) {
    await deleteTopic(client, topicId)
    set((s) => {
      // Drop the topic's participants too so a future panel mount for
      // the same id doesn't render stale state, and let the
      // conversationStore drop child conversations whose backend rows
      // just became inaccessible (_scoped_select joins is_archived).
      const nextParticipants = { ...s.topicParticipants }
      delete nextParticipants[topicId]
      return {
        topics: s.topics.filter((t) => t.id !== topicId),
        topicParticipants: nextParticipants,
      }
    })
  },

  async setPin(client, topicId, isPinned) {
    // Optimistic flip; revert on failure.
    set((s) => ({
      topics: s.topics.map((t) => (t.id === topicId ? { ...t, is_pinned: isPinned } : t)),
    }))
    try {
      const { topic } = await setTopicPin(client, topicId, isPinned)
      set((s) => ({
        topics: s.topics.map((t) => (t.id === topicId ? topic : t)),
      }))
    } catch (e) {
      // Roll back.
      set((s) => ({
        topics: s.topics.map((t) => (t.id === topicId ? { ...t, is_pinned: !isPinned } : t)),
      }))
      throw e
    }
  },

  async addMembers(client, topicId, userIds) {
    const { participants } = await addTopicParticipants(client, topicId, userIds)
    set((s) => ({
      topicParticipants: {
        ...s.topicParticipants,
        [topicId]: [...(s.topicParticipants[topicId] ?? []), ...participants],
      },
    }))
  },

  async removeMember(client, topicId, userId) {
    await removeTopicParticipant(client, topicId, userId)
    set((s) => ({
      topicParticipants: {
        ...s.topicParticipants,
        [topicId]: (s.topicParticipants[topicId] ?? []).filter((p) => p.user_id !== userId),
      },
    }))
  },

  async updateParticipantRole(client, topicId, userId, role) {
    const { participant } = await apiUpdateParticipantRole(client, topicId, userId, role)
    set((s) => {
      const current = s.topicParticipants[topicId] ?? []
      // When promoting another member to owner, the backend demotes the caller
      // to member. Refetch to keep all roles in sync rather than guessing.
      const next = current.map((p) => (p.user_id === userId ? participant : p))
      return {
        topicParticipants: {
          ...s.topicParticipants,
          [topicId]: next,
        },
      }
    })
    // Authoritative refresh — caller demotion + any cascading changes.
    try {
      const detail = await getTopic(client, topicId)
      set((s) => ({
        topicParticipants: {
          ...s.topicParticipants,
          [topicId]: detail.participants,
        },
      }))
    } catch {
      /* best-effort */
    }
  },

  async createConversation(client, topicId, title) {
    const { conversation } = await createTopicConversation(client, topicId, title)
    // Bump the topic to the top of the sidebar (client-side) immediately.
    set((s) => ({
      topics: s.topics.map((t) =>
        t.id === topicId ? { ...t, last_activity_at: new Date().toISOString() } : t,
      ),
    }))
    return { conversationId: conversation.id }
  },
}))
