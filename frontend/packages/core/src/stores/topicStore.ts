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
  addMembers(client: ApiClient, topicId: string, userIds: string[]): Promise<void>
  removeMember(client: ApiClient, topicId: string, userId: string): Promise<void>
  updateParticipantRole(
    client: ApiClient,
    topicId: string,
    userId: string,
    role: 'owner' | 'member',
  ): Promise<void>
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
      set({ topics: items, isLoading: false })
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
    set((s) => ({ topics: [data.topic, ...s.topics] }))
    return {
      topicId: data.topic.id,
      conversationId: data.conversation.id,
    }
  },

  async remove(client, topicId) {
    await deleteTopic(client, topicId)
    set((s) => ({ topics: s.topics.filter((t) => t.id !== topicId) }))
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
}))
